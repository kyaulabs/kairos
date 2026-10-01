"""Alpaca Trading API: hosted paper only, USD crypto and listed US equities.

Reference: https://docs.alpaca.markets/us/reference
The live trading host and funding endpoints are deliberately not configurable.
"""

import time
from datetime import UTC, datetime
from urllib.parse import quote

import aiohttp

from kairos import diagnostics
from kairos.alpaca_data import AlpacaBook, AlpacaData
from kairos.alpaca_transport import AlpacaRequests, PendingAlpacaData, RejectedAlpacaData
from kairos.clients import ExchangeRejected
from kairos.domain import Pair, SafetyError, dec
from kairos.market_data import CandleHistory, validate_levels

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
STATES = {
    "new": "open",
    "accepted": "open",
    "pending_new": "open",
    "partially_filled": "open",
    "pending_cancel": "open",
    "accepted_for_bidding": "open",
    "filled": "closed",
    "canceled": "canceled",
    "expired": "expired",
    "rejected": "rejected",
    "done_for_day": "open",
    "suspended": "open",
    "stopped": "open",
    "calculated": "open",
}


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise SafetyError("Alpaca timestamp has no timezone")
    return result.timestamp()


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat()


class Alpaca:
    name = "Alpaca"
    hosted_paper = True
    allow_live = False
    fee_source = "Published Alpaca planning assumptions, NOT authenticated account fee rates"
    fee_note = (
        "Crypto: 25 bps on both sides (published entry-tier taker rate); passive limits do not "
        "guarantee maker treatment. Equities: zero commission; hosted paper omits regulatory "
        "fees. Actual posted fees are reconciled separately from account activities."
    )

    def __init__(self, session, key="", secret="", *, allow_paper=False):
        self.session, self.key, self.secret = session, key, secret
        self.allow_paper = allow_paper
        self.pairs, self.assets = {}, {}
        self.requests = AlpacaRequests()
        self.market_data = AlpacaData(self)
        self.order_guard = None
        diagnostics.register_secrets(key, secret)

    async def request(
        self,
        method,
        path,
        *,
        params=None,
        payload=None,
        data=False,
        deadline=None,
        background=False,
        before_send=None,
    ):
        if method not in {"GET", "POST", "DELETE"} or not path.startswith("/"):
            raise SafetyError("Unsupported Alpaca request")
        if method != "GET" and (
            data
            or not self.allow_paper
            or not (path == "/v2/orders" or path.startswith("/v2/orders/"))
        ):
            raise SafetyError("Alpaca paper order writes are not authorized")
        if not self.key or not self.secret:
            raise SafetyError("Configure ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY")
        bucket = "data" if data else "trading"
        priority = (
            3
            if background
            else 0
            if method != "GET"
            or path.startswith("/v2/orders/")
            or path == "/v2/orders:by_client_order_id"
            else 2
            if data
            else 1
        )
        async with self.requests.slot(priority, bucket, deadline):
            if method == "POST" and self.order_guard and not self.order_guard():
                raise ExchangeRejected("Alpaca submission canceled by Stop")
            if before_send:
                before_send()
            started = time.monotonic()
            try:
                async with self.session.request(
                    method,
                    (DATA_URL if data else PAPER_URL) + path,
                    params=params,
                    json=payload,
                    headers={"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret},
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as response:
                    self.requests.response(bucket, method, path, response, started)
                    if response.status == 204:
                        return None
                    if not 200 <= response.status < 300:
                        if (
                            method == "GET"
                            and data
                            and response.status in {429, 500, 502, 503, 504}
                        ):
                            raise PendingAlpacaData(
                                f"Alpaca market data unavailable (HTTP {response.status}); reads deferred"
                            )
                        error = (
                            ExchangeRejected
                            if method != "GET" and response.status in {400, 401, 403, 404, 422}
                            else SafetyError
                        )
                        exc = error(
                            f"Alpaca {method} request failed (HTTP {response.status}); reconcile uncertain orders"
                        )
                        exc.diagnostic_details = {"endpoint": path, "http_status": response.status}
                        raise exc
                    return await response.json()
            except aiohttp.ContentTypeError as exc:
                raise SafetyError("Malformed Alpaca response; no automatic retry") from exc
            except (aiohttp.ClientError, TimeoutError) as exc:
                if method == "GET" and data:
                    raise PendingAlpacaData(
                        "Alpaca market-data transport unavailable; read deferred"
                    ) from exc
                raise SafetyError(
                    "Alpaca transport failure; request outcome may be unknown"
                ) from exc
            except ValueError as exc:
                raise SafetyError("Malformed Alpaca response; no automatic retry") from exc

    async def catalog(self):
        pairs, assets = {}, {}
        for kind in ("crypto", "us_equity"):
            rows = await self.request(
                "GET", "/v2/assets", params={"status": "active", "asset_class": kind}
            )
            for row in rows:
                if row["class"] != kind or row["status"] != "active" or row["tradable"] is not True:
                    continue
                symbol = row["symbol"]
                if kind == "crypto":
                    if not symbol.endswith("/USD"):
                        continue
                    base = symbol.split("/")[0]
                    lot, tick, minimum = (
                        dec(row[k])
                        for k in ("min_trade_increment", "price_increment", "min_order_size")
                    )
                else:
                    if row["exchange"] not in {
                        "NYSE",
                        "NASDAQ",
                        "ARCA",
                        "AMEX",
                        "BATS",
                        "NYSEARCA",
                    }:
                        continue
                    base, lot, tick, minimum = symbol, dec(1), dec(".01"), dec(1)
                if min(lot, tick, minimum) <= 0:
                    raise SafetyError("Invalid Alpaca asset precision/minimum")
                identifier = "alpaca:" + symbol
                pairs[identifier] = Pair(
                    identifier,
                    symbol if kind == "crypto" else symbol + "/USD",
                    base,
                    "USD",
                    tick,
                    lot,
                    minimum,
                    dec(0),
                )
                assets[identifier] = dict(row)
        if not pairs:
            raise SafetyError("No supported Alpaca assets available")
        self.pairs, self.assets = pairs, assets
        return pairs

    def is_equity(self, pair):
        return self.assets[pair.id]["class"] == "us_equity"

    def symbol(self, pair):
        return self.assets[pair.id]["symbol"]

    async def account(self, *, background=False):
        row = await self.request("GET", "/v2/account", background=background)
        if (
            not isinstance(row.get("id"), str)
            or not row["id"]
            or row["status"] != "ACTIVE"
            or row["currency"] != "USD"
            or any(
                row.get(k, False)
                for k in ("trading_blocked", "account_blocked", "trade_suspended_by_user")
            )
        ):
            raise SafetyError("Alpaca paper account is not active and unrestricted in USD")
        if dec(row["cash"]) < 0:
            raise SafetyError("Alpaca account is borrowing cash; leveraged trading is unsupported")
        return row

    async def positions(self):
        rows = await self.request("GET", "/v2/positions")
        result = {}
        self.position_prices = {}
        symbols = {a["symbol"]: self.pairs[k] for k, a in self.assets.items()}
        # Position crypto symbols may use the legacy compact spelling.
        symbols.update(
            {a["symbol"].replace("/", ""): self.pairs[k] for k, a in self.assets.items()}
        )
        for row in rows:
            pair = symbols.get(row["symbol"])
            if pair is None or row["side"] != "long" or dec(row["qty"]) < 0:
                raise SafetyError(
                    "Unsupported or short Alpaca position; manual reconciliation required"
                )
            if pair.base in result:
                raise SafetyError("Duplicate Alpaca position")
            result[pair.base] = dec(row["qty"])
            self.position_prices[pair.base] = dec(row["current_price"])
            if self.position_prices[pair.base] <= 0:
                raise SafetyError("Alpaca position mark is unavailable")
        return result

    async def balances(self):
        account = await self.account()
        return {
            **await self.positions(),
            "USD": min(dec(account["cash"]), dec(account["non_marginable_buying_power"])),
        }

    async def clock(self):
        row = await self.request("GET", "/v2/clock")
        if type(row["is_open"]) is not bool or abs(time.time() - timestamp(row["timestamp"])) > 30:
            raise SafetyError("Alpaca market clock is invalid or stale")
        return row

    async def open_orders(self):
        rows = await self.request(
            "GET", "/v2/orders", params={"status": "open", "limit": 500, "nested": "false"}
        )
        if len(rows) >= 500:
            raise SafetyError(
                "Alpaca open-order limit reached; cannot prove complete reconciliation"
            )
        return rows

    async def activities(self, after):
        result, token, seen = [], None, set()
        for _ in range(100):
            params = {"after": after, "direction": "asc", "page_size": 100}
            if token:
                params["page_token"] = token
            rows = await self.request("GET", "/v2/account/activities", params=params)
            for row in rows:
                if row["id"] in seen:
                    raise SafetyError("Alpaca activity pagination repeated a record")
                seen.add(row["id"])
                result.append(row)
            if len(rows) < 100:
                return result
            token = rows[-1]["id"]
        raise SafetyError("Alpaca activity pagination incomplete")

    async def fees(self, pairs):
        # Risk-screen estimate only. Never promise the maker discount without post-only support.
        rates = {p.id: dec(0 if self.is_equity(p) else 25) for p in pairs}
        return rates, dict(rates)

    async def book(self, pair):
        feed = self.market_data
        async with feed.rest_lock:
            book = feed.fresh_book(pair)
            if book:
                return book
            if time.monotonic() - feed.attempts.get(pair.id, -5) < 5:
                raise PendingAlpacaData(
                    "Waiting for a fresh Alpaca book; REST retry limited to once per five seconds"
                )
            feed.attempts[pair.id] = time.monotonic()
            book = await self.rest_book(pair)
            feed.rest_books[pair.id] = book
            book.fresh(feed.book_age)
            return book

    async def rest_book(self, pair):
        symbol = self.symbol(pair)
        if self.is_equity(pair):
            data = await self.request(
                "GET",
                "/v2/stocks/quotes/latest",
                params={"symbols": symbol, "feed": "iex"},
                data=True,
            )
            row = data["quotes"][symbol]
            # Retain native reported sizes; never invent consolidated market depth.
            bids, asks = [[row["bp"], row["bs"]]], [[row["ap"], row["as"]]]
        else:
            data = await self.request(
                "GET", "/v1beta3/crypto/us/latest/orderbooks", params={"symbols": symbol}, data=True
            )
            row = data["orderbooks"][symbol]
            bids, asks = [[r["p"], r["s"]] for r in row["b"]], [[r["p"], r["s"]] for r in row["a"]]
        bids = [[dec(p), dec(q)] for p, q in bids]
        asks = [[dec(p), dec(q)] for p, q in asks]
        validate_levels(bids, asks)
        received = timestamp(row["t"])
        if received > time.time() + 2:
            raise SafetyError("Alpaca quote is from the future")
        return AlpacaBook(pair, bids, asks, received, time.time(), time.monotonic())

    async def marks(self, pairs):
        result = {}
        for pair in pairs:
            book = await self.book(pair)
            book.fresh(self.market_data.book_age)
            result[pair.id] = book.mid
        return result

    async def bars(self, pair, minutes, count=720, *, background=False):
        equity = self.is_equity(pair)
        path = "/v2/stocks/bars" if equity else "/v1beta3/crypto/us/bars"
        cutoff = int(time.time()) // (minutes * 60) * minutes * 60
        params = {
            "symbols": self.symbol(pair),
            "timeframe": f"{minutes}Min"
            if minutes < 60
            else f"{minutes // 60}Hour"
            if minutes < 1440
            else "1Day",
            "start": iso(cutoff - count * minutes * 60 * (4 if equity else 1)),
            "end": iso(cutoff - 1),
            "limit": 10000,
            "sort": "asc",
        }
        if equity:
            params.update(feed="iex", adjustment="raw")
        rows, tokens = [], set()
        for _ in range(20):
            data = await self.request("GET", path, params=params, data=True, background=background)
            rows.extend(data["bars"].get(self.symbol(pair), []))
            token = data.get("next_page_token")
            if not token:
                break
            if token in tokens:
                raise SafetyError("Alpaca bar pagination repeated a token")
            tokens.add(token)
            params["page_token"] = token
        else:
            raise SafetyError("Alpaca bar history incomplete")
        normalized = [
            [
                int(timestamp(r["t"])),
                *[str(dec(r[k])) for k in ("o", "h", "l", "c", "vw", "v")],
                int(r["n"]),
            ]
            for r in rows
        ]
        if any(r[0] >= cutoff for r in normalized) or [r[0] for r in normalized] != sorted(
            {r[0] for r in normalized}
        ):
            raise SafetyError("Alpaca bars are forming, duplicated or unordered")
        return normalized[-count:]

    async def candles(self, pair, minutes):
        rows = await self.bars(pair, minutes)
        history = CandleHistory(pair, minutes, 31)
        history.seed = {r[0]: history.validate(r) for r in rows}
        return history.completed(streamed=False)

    async def completed_minutes(self, pair, count):
        if self.is_equity(pair):
            raise SafetyError(
                "Equity indicators need session-aware history; use scheduled strategies"
            )
        return await self.bars(pair, 1, count=count)

    def normalize_order(self, row):
        if row["status"] not in STATES or row.get("replaced_by") or row.get("legs"):
            raise SafetyError("Unsupported Alpaca order state; reconciliation required")
        filled = dec(row["filled_qty"])
        price = dec(row["filled_avg_price"]) if filled else dec(0)
        if filled < 0 or filled > dec(row["qty"]) or (filled and price <= 0):
            raise SafetyError("Invalid Alpaca cumulative fill")
        return {
            "vol_exec": str(filled),
            "cost": str(filled * price),
            "fee": "0",
            "status": STATES[row["status"]],
            "raw": row,
        }

    async def query(self, txid):
        row = await self.request("GET", "/v2/orders/" + quote(txid, safe=""))
        return self.normalize_order(row)

    async def find_order(self, client_id, since):
        # A 404 remains uncertain, never an excuse to resubmit the durable intent.
        row = await self.request(
            "GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_id}
        )
        if row["client_order_id"] != client_id:
            raise SafetyError("Alpaca client order ID mismatch")
        return row["id"], self.normalize_order(row)

    async def add(self, params):
        try:
            pair = self.pairs[params["pair"]]
            pair.validate(dec(params["volume"]), dec(params["price"]))
            if params["type"] not in {"buy", "sell"} or params["ordertype"] != "limit":
                raise SafetyError("Only bounded Alpaca paper limit orders are supported")
            if time.time() + 1 >= timestamp(params["deadline"]):
                raise SafetyError("Alpaca intent expired before submission")
            passive = "post" in params.get("oflags", "").split(",")
            if self.is_equity(pair):
                if passive:
                    raise SafetyError("Equities support scheduled strategies only")
                if not (await self.clock())["is_open"]:
                    raise SafetyError("Alpaca regular equity session is closed")
        except SafetyError as exc:
            # No POST has occurred; retain a rejected intent, not an ambiguous submission.
            raise ExchangeRejected(str(exc)) from exc
        payload = {
            "symbol": self.symbol(pair),
            "qty": params["volume"],
            "side": params["type"],
            "type": "limit",
            "limit_price": params["price"],
            "client_order_id": params["cl_ord_id"],
            "time_in_force": "gtc" if passive else "ioc",
            "extended_hours": False,
        }

        def fresh_before_send():
            # Recheck after queue/quota waits, before the HTTP write, including a
            # stream disconnect or a passive price that now crosses the market.
            try:
                book = self.market_data.fresh_book(pair)
            except SafetyError as exc:
                raise ExchangeRejected(str(exc)) from exc
            if book is None:
                raise RejectedAlpacaData("No fresh Alpaca execution book before submission")
            if passive and (
                (params["type"] == "buy" and dec(params["price"]) >= book.asks[0][0])
                or (params["type"] == "sell" and dec(params["price"]) <= book.bids[0][0])
            ):
                raise ExchangeRejected("Passive Alpaca limit now crosses; no order sent")

        row = await self.request(
            "POST",
            "/v2/orders",
            payload=payload,
            deadline=timestamp(params["deadline"]),
            before_send=fresh_before_send,
        )
        if row["client_order_id"] != params["cl_ord_id"] or not row.get("id"):
            raise SafetyError("Alpaca submission identity mismatch")
        return {"txid": [row["id"]]}

    async def cancel(self, txid):
        await self.request("DELETE", "/v2/orders/" + quote(txid, safe=""))
