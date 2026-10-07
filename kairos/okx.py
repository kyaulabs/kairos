"""OKX U.S. spot transport. Environment and credential scope are immutable."""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode

import aiohttp
from yarl import URL

from kairos import diagnostics
from kairos.domain import BPS, ZERO, Pair, SafetyError, dec

REST = "https://us.okx.com"
PUBLIC_WS = {
    "live": "wss://wsus.okx.com:8443/ws/v5/public",
    "demo": "wss://wsuspap.okx.com:8443/ws/v5/public",
}
PRIVATE_WS = {k: v.replace("/public", "/private") for k, v in PUBLIC_WS.items()}
READS = {
    "/api/v5/public/time": set(),
    "/api/v5/market/candles": {"instId", "bar", "limit"},
    "/api/v5/market/index-tickers": {"instId"},
    "/api/v5/public/price-limit": {"instId"},
    "/api/v5/account/config": set(),
    "/api/v5/account/balance": {"ccy"},
    "/api/v5/account/instruments": {"instType", "instId"},
    "/api/v5/account/trade-fee": {"instType", "instId"},
    "/api/v5/trade/order": {"instId", "ordId", "clOrdId"},
    "/api/v5/trade/orders-pending": {"instType", "instId", "after", "limit"},
    "/api/v5/trade/orders-history": {"instType", "instId", "after", "begin", "limit"},
    "/api/v5/trade/orders-history-archive": {"instType", "instId", "after", "begin", "limit"},
    "/api/v5/trade/fills": {"instType", "instId", "ordId", "after", "begin", "limit"},
    "/api/v5/trade/fills-history": {"instType", "instId", "ordId", "after", "begin", "limit"},
    "/api/v5/account/bills": {"after", "begin", "limit"},
    "/api/v5/account/bills-archive": {"after", "begin", "limit"},
}
ORDER_FIELDS = {
    "instId",
    "tdMode",
    "clOrdId",
    "side",
    "ordType",
    "sz",
    "px",
    "tradeQuoteCcy",
    "stpMode",
    "pxAmendType",
}


class PendingOKX(SafetyError):
    """Transient read or delayed evidence; never permission to repeat a write."""


class OKXBeforeSend(SafetyError):
    """The transport did not attempt the HTTP write; its intent is still consumed."""


class OKXRejected(SafetyError):
    """A documented application/per-order rejection, not a fill."""

    def __init__(self, code, operation):
        self.code = code
        reasons = {
            "50101": "credential/environment mismatch; install the key for this environment",
            "50102": "timestamp outside venue tolerance; synchronize the server clock",
            "51008": "insufficient available funds; inspect the selected currency and holds",
            "51603": "order not visible; retain the original intent and reconcile, do not resubmit",
            "50011": "rate limit; wait for backoff without replaying a write",
        }
        reason = reasons.get(
            code, "request rejected; inspect the venue code and approved order parameters"
        )
        super().__init__(f"OKX {operation} code {code}: {reason}")


def sign(secret, timestamp, method, path, body=""):
    message = (timestamp + method + path + body).encode()
    return base64.b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()).decode()


def code(value):
    value = str(value)
    return value if re.fullmatch(r"\d{1,8}", value) else "invalid"


class OKX:
    fee_name = "OKX account"
    fee_estimates = True
    fee_source = "OKX U.S. authenticated account/trade-fee, instrument feeGroup"
    fee_note = "Planning rates are estimates; signed per-execution fees/rebates and their actual currencies are authoritative. Demo tiers do not establish live fees."

    def __init__(
        self, session, key="", secret="", passphrase="", *, environment="live", allow_writes=False
    ):
        if environment not in PUBLIC_WS:
            raise SafetyError("OKX environment must be live or demo; no host fallback")
        self.session = session
        self._environment = environment
        self.key, self.secret, self.passphrase = key, secret, passphrase
        diagnostics.register_secrets(key, secret, passphrase)
        self.allow_writes = bool(allow_writes)
        self.allow_live = environment == "live" and self.allow_writes
        self.hosted_paper = environment == "demo"
        self.pairs, self.instruments, self.fee_rows = {}, {}, {}
        self.write_guard = None
        self.last_request = None
        self.blocked_until, self.next_request = 0, 0
        self.request_lock = asyncio.Lock()
        from kairos.okx_data import OKXData

        self.market_data = OKXData(self)

    @property
    def environment(self):
        return self._environment

    @property
    def exchange(self):
        return "okx-demo" if self.environment == "demo" else "okx"

    @property
    def configured(self):
        return bool(self.key and self.secret and self.passphrase)

    def check_limit_reference(self, evidence, max_age):
        if evidence.get("environment") != self.environment:
            raise SafetyError(
                "OKX limit evidence belongs to another environment; nothing submitted"
            )
        reference = evidence.get("usd_reference")
        if reference is not None:
            age = dec(time.time()) - dec(reference["source_time"])
            if not -2 <= age <= max_age:
                raise PendingOKX(
                    f"OKX USD limit reference age {age}s exceeds the freshness bound; nothing submitted"
                )

    async def limit_order_check(self, pair, volume, price, max_age):
        """Value only the venue's USD cap; never convert funds or price execution."""
        metadata = self.instruments[pair.id]
        maximum = dec(metadata.get("maxLmtSz", 0))
        if maximum <= 0 or volume > maximum:
            raise SafetyError("OKX quantity exceeds/unavailable native limit-order size cap")
        evidence = {
            "environment": self.environment,
            "instrument": metadata["instId"],
            "price_currency": metadata["quoteCcy"],
            "quantity": str(volume),
            "price": str(price),
            "maximum_quantity": str(maximum),
            "maximum_usd_notional": None,
            "usd_notional": None,
            "usd_reference": None,
        }
        if metadata.get("maxLmtAmt") in (None, "", "0"):
            return evidence
        cap, rate = dec(metadata["maxLmtAmt"]), dec(1)
        if metadata["quoteCcy"] != "USD":
            index = metadata["quoteCcy"] + "-USD"
            rows = await self.request(
                "GET", "/api/v5/market/index-tickers", params={"instId": index}
            )
            if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("instId") != index:
                raise SafetyError(
                    "OKX native USD limit reference unavailable or mismatched; nothing submitted"
                )
            rate = dec(rows[0].get("idxPx", ""))
            if rate <= 0:
                raise SafetyError(
                    "OKX native USD limit reference must be positive; nothing submitted"
                )
            evidence["usd_reference"] = {
                "instrument": index,
                "rate": str(rate),
                "source_time": str(dec(rows[0].get("ts", "")) / 1000),
                "source": "OKX U.S. same-environment index; venue-cap valuation only, not execution or fund conversion",
            }
            self.check_limit_reference(evidence, max_age)
        notional = volume * price * rate
        if cap <= 0 or notional > cap:
            raise SafetyError(
                f"OKX order USD notional {notional} exceeds native cap {cap}; nothing submitted"
            )
        evidence.update(maximum_usd_notional=str(cap), usd_notional=str(notional))
        return evidence

    def validate_write(self, path, payload):
        try:
            self._validate_write(path, payload)
        except SafetyError as exc:
            raise OKXBeforeSend(str(exc)) from None

    def _validate_write(self, path, payload):
        if not self.allow_writes or not self.configured:
            raise SafetyError(
                f"OKX {self.environment} write gate or credentials unavailable; nothing submitted"
            )
        if path == "/api/v5/trade/order":
            if set(payload) != ORDER_FIELDS or (
                payload["tdMode"] != "cash"
                or payload["ordType"] != "ioc"
                or payload["side"] not in {"buy", "sell"}
                or payload["stpMode"] != "cancel_taker"
                or payload["pxAmendType"] != "0"
                or not re.fullmatch(r"[A-Za-z0-9]{1,32}", payload["clOrdId"])
            ):
                raise SafetyError(
                    "OKX writes permit only explicit cash spot IOC limits; nothing submitted"
                )
            pair = next(
                (
                    p
                    for p in self.pairs.values()
                    if self.instruments[p.id]["instId"] == payload["instId"]
                    and p.quote == payload["tradeQuoteCcy"]
                ),
                None,
            )
            if pair is None:
                raise SafetyError(
                    "OKX instrument/spending currency was not discovered for this account"
                )
            pair.validate(dec(payload["sz"]), dec(payload["px"]))
        elif path == "/api/v5/trade/cancel-order":
            if set(payload) != {"instId", "ordId"} or not re.fullmatch(r"\d+", payload["ordId"]):
                raise SafetyError("OKX cancellation requires the exact owned order ID")
        else:
            raise SafetyError("OKX write endpoint prohibited")
        if self.write_guard is None or not self.write_guard(path, payload):
            raise SafetyError(
                "OKX write lacks current permission and matching durable owned intent; nothing submitted"
            )

    async def request(self, method, path, *, params=None, payload=None, deadline=None):
        params = dict(params or {})
        writing = method == "POST"
        if method == "GET":
            if path not in READS or set(params) - READS[path] or payload is not None:
                raise SafetyError("OKX read endpoint/parameters prohibited")
            if "instType" in params and params["instType"] != "SPOT":
                raise SafetyError("OKX supports unleveraged SPOT only")
        elif writing:
            if params or not isinstance(payload, dict):
                raise SafetyError("Invalid OKX write envelope")
            self.validate_write(path, payload)
        else:
            raise SafetyError("OKX HTTP method prohibited")
        private = not (
            path.startswith("/api/v5/public/")
            or path in {"/api/v5/market/candles", "/api/v5/market/index-tickers"}
        )
        if private and not self.configured:
            raise SafetyError(
                f"OKX {self.environment} credentials missing; configure this environment privately"
            )
        query = urlencode(sorted(params.items()))
        target = path + ("?" + query if query else "")
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False) if writing else ""
        async with self.request_lock:
            delay = max(self.blocked_until, self.next_request) - time.monotonic()
            if delay > 10:
                error = OKXBeforeSend if writing else PendingOKX
                raise error(
                    "OKX read/admission backoff exceeds 10s; no request sent; retry reads later"
                )
            if delay > 0:
                await asyncio.sleep(delay)
            if deadline is not None and time.time() >= deadline:
                raise OKXBeforeSend("OKX authorized deadline elapsed before network submission")
            if writing:
                self.validate_write(path, payload)
            stamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            headers = {
                "Content-Type": "application/json",
                "x-simulated-trading": "1" if self.environment == "demo" else "0",
            }
            if private:
                headers.update(
                    {
                        "OK-ACCESS-KEY": self.key,
                        "OK-ACCESS-PASSPHRASE": self.passphrase,
                        "OK-ACCESS-TIMESTAMP": stamp,
                        "OK-ACCESS-SIGN": sign(self.secret, stamp, method, target, body),
                    }
                )
            if writing and path.endswith("/order"):
                headers["expTime"] = str(
                    int(min(deadline or time.time() + 5, time.time() + 5) * 1000)
                )
            started = time.monotonic()
            self.next_request = started + 0.4  # Below the narrowest used endpoint's 5/2s limit.
            try:
                async with self.session.request(
                    method,
                    URL(REST + target, encoded=True),
                    headers=headers,
                    data=body.encode() if writing else None,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    self.last_request = {
                        "method": method,
                        "endpoint": path,
                        "status": response.status,
                        "received_at": time.time(),
                        "latency_seconds": time.monotonic() - started,
                    }
                    if response.status == 429:
                        retry = response.headers.get("Retry-After", "2")
                        wait = float(retry) if re.fullmatch(r"\d+(\.\d+)?", retry) else 2
                        self.blocked_until = max(
                            self.blocked_until, time.monotonic() + max(2, wait)
                        )
                    if response.status != 200:
                        error = (
                            SafetyError
                            if writing or response.status < 500 and response.status != 429
                            else PendingOKX
                        )
                        raise error(
                            f"OKX {method} {path} HTTP {response.status}; no automatic write retry"
                        )
                    raw = await response.read()
                    if len(raw) > 8 * 1024 * 1024:
                        raise SafetyError("OKX response exceeds bounded size; evidence incomplete")
                    result = json.loads(raw, parse_float=Decimal)
            except (aiohttp.ClientConnectionError, TimeoutError):
                error = SafetyError if writing else PendingOKX
                raise error(
                    f"OKX {method} {path} transport outcome unavailable; reconcile original intent before another write"
                ) from None
            except (ValueError, TypeError):
                raise SafetyError("Malformed OKX response; no success inferred") from None
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise SafetyError("Malformed OKX envelope; evidence incomplete")
        status = code(result.get("code"))
        if status != "0":
            if status in {"50011", "50040", "50061"}:
                self.blocked_until = max(self.blocked_until, time.monotonic() + 2)
                if not writing:
                    raise PendingOKX(f"OKX rate limit {status}; wait for admission backoff")
            if status == "50004":
                raise SafetyError(
                    "OKX timeout 50004 has unknown outcome; reconcile original intent, never resubmit"
                )
            raise OKXRejected(status, path)
        rows = result["data"]
        if writing:
            if len(rows) != 1 or not isinstance(rows[0], dict):
                raise SafetyError("OKX write acknowledgement incomplete; reconcile original intent")
            if code(rows[0].get("sCode")) == "50004":
                raise SafetyError(
                    "OKX per-order timeout has unknown outcome; reconcile original intent, never resubmit"
                )
            if code(rows[0].get("sCode")) != "0":
                raise OKXRejected(code(rows[0].get("sCode")), path)
            if not re.fullmatch(r"\d+", str(rows[0].get("ordId", ""))):
                raise SafetyError(
                    "OKX acknowledgement lacks order ID; execution is not established"
                )
            if path.endswith("/cancel-order") and rows[0]["ordId"] != payload["ordId"]:
                raise SafetyError(
                    "OKX cancellation acknowledgement identity mismatch; outcome unknown"
                )
            if path.endswith("/order") and rows[0].get("clOrdId") != payload["clOrdId"]:
                raise SafetyError(
                    "OKX acknowledgement client identity mismatch; reconcile original intent"
                )
        return rows

    async def pages(self, path, *, cursor_key, **params):
        result, seen, cursor = [], set(), None
        for _ in range(100):
            page = await self.request(
                "GET",
                path,
                params={**params, "limit": "100", **({"after": cursor} if cursor else {})},
            )
            for row in page:
                identifier = str(row[cursor_key])
                if identifier in seen:
                    raise SafetyError("OKX pagination repeated evidence; complete history unproven")
                seen.add(identifier)
                result.append(row)
            if len(page) < 100:
                return result
            cursor = str(page[-1][cursor_key])
        raise SafetyError("OKX pagination limit reached; complete history unproven")

    async def catalog(self):
        rows = await self.request("GET", "/api/v5/account/instruments", params={"instType": "SPOT"})
        pairs, instruments = {}, {}
        for row in rows:
            if row.get("instType") != "SPOT" or row.get("state") != "live":
                continue
            if row.get("instCategory") not in (None, "", "1"):
                continue
            quotes = row.get("tradeQuoteCcyList")
            if not isinstance(quotes, list) or not quotes:
                raise SafetyError("OKX account instrument lacks explicit tradable quote currencies")
            for quote in quotes:
                if not re.fullmatch(r"[A-Z0-9]{1,20}", quote) or quote == row["baseCcy"]:
                    raise SafetyError("Invalid OKX instrument currency")
                identifier = f"{self.exchange}:{row['instId']}:{quote}"
                pair = Pair(
                    identifier,
                    f"{row['baseCcy']}/{quote}",
                    row["baseCcy"],
                    quote,
                    dec(row["tickSz"]),
                    dec(row["lotSz"]),
                    dec(row["minSz"]),
                    ZERO,
                )
                if min(pair.tick, pair.lot, pair.minimum) <= 0 or identifier in pairs:
                    raise SafetyError("Invalid/duplicate OKX instrument precision")
                pairs[identifier], instruments[identifier] = pair, dict(row)
        self.pairs, self.instruments = pairs, instruments
        return pairs

    def resolve(self, identifier):
        if identifier not in self.pairs:
            raise SafetyError(
                "Select an account-enabled OKX instrument and actual spending currency; no substitute chosen"
            )
        return self.pairs[identifier]

    async def fees(self, pairs):
        maker, taker = {}, {}
        for pair in pairs:
            instrument = self.instruments[pair.id]
            rows = await self.request(
                "GET",
                "/api/v5/account/trade-fee",
                params={"instType": "SPOT", "instId": instrument["instId"]},
            )
            if len(rows) != 1 or rows[0].get("instType") != "SPOT":
                raise SafetyError(
                    "OKX fee discovery unavailable for the selected spot instrument; do not assume zero"
                )
            groups = [
                g
                for g in rows[0].get("feeGroup", [])
                if g.get("groupId") == instrument.get("groupId")
            ]
            if len(groups) != 1 or not instrument.get("groupId"):
                raise SafetyError("OKX fee group cannot be matched to the account instrument")
            rate = groups[0]
            maker[pair.id], taker[pair.id] = (
                -dec(rate["maker"]) * BPS,
                max(ZERO, -dec(rate["taker"]) * BPS),
            )
            self.fee_rows[pair.id] = dict(rows[0])
        return maker, taker

    async def book(self, pair):
        return await self.market_data.book(pair)

    async def marks(self, pairs):
        return {p.id: (await self.book(p)).mid for p in pairs}
