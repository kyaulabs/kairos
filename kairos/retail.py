"""Retail discovery; qualified Futures use a separate derivatives execution catalog."""

import asyncio
import base64
import hashlib
import hmac
import re
import time
from urllib.parse import quote

import aiohttp

from kairos.domain import CANDLE_INTERVALS, SafetyError, dec

FIAT = {"USD", "EUR", "GBP", "CAD", "JPY", "AUD", "CHF", "AED", "BRL", "ARS", "MXN"}


def linear_perpetual(row):
    return (
        row.get("type") == "flexible_futures"
        and row.get("symbol", "").startswith("PF_")
        and row.get("quote") == "USD"
        and type(row.get("contractSize")) in (int, float)
        and row["contractSize"] == 1
        and row.get("tradeable") is True
        and row.get("isExpired") is False
        and row.get("tradfi", False) is False
        and "contractValueTradePrecision" in row
        and bool(row.get("marginLevels"))
    )


def futures_signature(path, secret, post_data=""):
    # Futures permits omission of Nonce. Hash the encoded bytes actually sent.
    digest = hashlib.sha256((post_data + path).encode()).digest()
    return base64.b64encode(
        hmac.new(base64.b64decode(secret), digest, hashlib.sha512).digest()
    ).decode()


class Futures:
    PUBLIC = {"instruments", "tickers"}
    PRIVATE = {"accounts", "openpositions", "openorders", "fills"}

    def __init__(self, session, key="", secret=""):
        self.session, self.key, self.secret = session, key, secret
        self.lock = asyncio.Lock()
        self.last_request = 0

    async def get(self, method):
        if method not in self.PUBLIC | self.PRIVATE:
            raise SafetyError("Futures endpoint is not an authorized read")
        headers = {}
        path = "/api/v3/" + method
        if method in self.PRIVATE:
            if not self.key or not self.secret:
                raise SafetyError("Futures read-only credentials are not configured")
            try:
                headers = {"APIKey": self.key, "Authent": futures_signature(path, self.secret)}
            except ValueError:
                raise SafetyError("Invalid Futures credential encoding") from None
        return await self.fetch("https://futures.kraken.com/derivatives" + path, headers=headers)

    async def fetch(self, url, *, headers=None, params=None):
        async with self.lock:
            await asyncio.sleep(max(0, 0.2 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                async with self.session.request(
                    "GET",
                    url,
                    headers=headers or {},
                    params=params,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as response:
                    if response.status != 200:
                        raise SafetyError("Futures data request failed")
                    data = await response.json()
            except (aiohttp.ClientError, TimeoutError, ValueError):
                raise SafetyError("Futures data unavailable") from None
            if not isinstance(data, dict) or data.get("result", "success") != "success":
                raise SafetyError("Futures request rejected; check API access and permissions")
            return data

    async def candles(self, symbol, minutes):
        if minutes not in CANDLE_INTERVALS or not re.fullmatch(r"[A-Za-z0-9_]+", symbol):
            raise SafetyError("Unsupported Futures chart request")
        resolution = {60: "1h", 240: "4h", 1440: "1d"}.get(minutes, f"{minutes}m")
        end = int(time.time())
        data = await self.fetch(
            f"https://futures.kraken.com/api/charts/v1/trade/{quote(symbol, safe='')}/{resolution}",
            params={"from": end - 720 * minutes * 60, "to": end},
        )
        return [
            {
                "time": int(row["time"]) // 1000,
                **{key: str(dec(row[key])) for key in ("open", "high", "low", "close", "volume")},
            }
            for row in data["candles"][-720:]
        ]


def spot_instrument(pair):
    base, quote_currency = pair.symbol.split("/")
    return {
        **pair.public(),
        "kind": "fx" if base in FIAT and quote_currency in FIAT else "spot",
        "venue": "spot",
        "margin": bool(pair.leverage_buy and pair.leverage_sell),
        "volume_unit": base,
        "chart_volume_unit": "base asset",
        "execution_reason": "",
    }


class RetailMarkets:
    def __init__(self, spot, futures=None):
        self.spot, self.futures = spot, futures
        self.extra = {"xstocks": {}, "futures": {}}
        self.catalog_errors = {}
        self.expires = 0
        self.lock = asyncio.Lock()
        self.quotes = {}

    def instruments(self):
        return {
            **{p.id: spot_instrument(p) for p in self.spot.pairs.values()},
            **self.extra["xstocks"],
            **self.extra["futures"],
        }

    async def catalog(self):
        async with self.lock:
            if time.monotonic() < self.expires:
                return self.instruments()

            async def load(kind):
                try:
                    if kind == "xstocks":
                        data = await self.spot.request(
                            "AssetPairs", {"aclass": "tokenized_asset", "assetVersion": 1}
                        )
                        records = {}
                        for row in data.values():
                            if (
                                not isinstance(row, dict)
                                or row.get("status") != "online"
                                or row.get("aclass_base") != "tokenized_asset"
                            ):
                                continue
                            # Canonical altname avoids duplicate legacy SPV aliases/rebased prices.
                            identifier = "xstocks:" + row["altname"]
                            records[identifier] = {
                                "id": identifier,
                                "symbol": row["wsname"],
                                "raw_id": row["altname"],
                                "base": row["base"],
                                "quote": row["quote"],
                                "kind": kind,
                                "venue": "spot",
                                "margin": bool(
                                    row.get("leverage_buy") and row.get("leverage_sell")
                                ),
                                "volume_unit": row["base"],
                                "chart_volume_unit": "token units",
                                "execution_reason": "xStocks are browse-only; tokenized-asset execution is not integrated.",
                            }
                    else:
                        if self.futures is None:
                            raise SafetyError("Futures adapter unavailable")
                        data = await self.futures.get("instruments")
                        records = {}
                        for row in data["instruments"]:
                            if not row.get("tradeable") or row.get("isExpired"):
                                continue
                            symbol = row["symbol"]
                            identifier = "futures:" + symbol
                            records[identifier] = {
                                "id": identifier,
                                "symbol": symbol,
                                "raw_id": symbol,
                                "base": row.get("base", ""),
                                "quote": row.get("quote", ""),
                                "underlying": row.get("pair", ""),
                                "kind": kind,
                                "venue": "futures",
                                "margin": False,
                                "contract_type": row["type"],
                                "contract_size": str(dec(row["contractSize"])),
                                "volume_unit": "contracts",
                                "chart_volume_unit": "contract units",
                                "linear_perpetual": linear_perpetual(row),
                                "execution_reason": ""
                                if linear_perpetual(row)
                                else "Only qualified USD linear crypto perpetuals support execution; inverse, dated and other contracts are browse-only.",
                            }
                    self.extra[kind] = records
                    self.catalog_errors.pop(kind, None)
                except (SafetyError, KeyError, TypeError, ValueError, AttributeError):
                    self.catalog_errors[kind] = (
                        f"{kind} catalog unavailable; any prior catalog is retained"
                    )

            await asyncio.gather(load("xstocks"), load("futures"))
            self.expires = time.monotonic() + (30 if self.catalog_errors else 300)
            return self.instruments()

    async def extra_quotes(self):
        errors = {}

        async def load(kind):
            try:
                if not self.extra[kind]:
                    return
                values = {}
                if kind == "xstocks":
                    data = await self.spot.request("Ticker", {"asset_class": "tokenized_asset"})
                    for identifier, market in self.extra[kind].items():
                        row = data.get(market["raw_id"])
                        if row:
                            values[identifier] = {
                                target: str(dec(row[source][index]))
                                for target, source, index in (
                                    ("bid", "b", 0),
                                    ("ask", "a", 0),
                                    ("last", "c", 0),
                                    ("volume", "v", 1),
                                )
                            }
                else:
                    data = await self.futures.get("tickers")
                    for row in data["tickers"]:
                        identifier = "futures:" + row["symbol"]
                        if identifier not in self.extra[kind]:
                            continue
                        values[identifier] = {
                            target: str(dec(row[source])) if row.get(source) is not None else None
                            for target, source in (
                                ("bid", "bid"),
                                ("ask", "ask"),
                                ("last", "last"),
                                ("volume", "vol24h"),
                                ("change_pct", "change24h"),
                                ("mark", "markPrice"),
                                ("funding_rate", "fundingRate"),
                            )
                        }
                received = time.time()
                self.quotes[kind] = {
                    key: {**value, "received": received} for key, value in values.items()
                }
            except (SafetyError, KeyError, TypeError, ValueError, AttributeError):
                errors[kind] = (
                    f"{kind} quotes unavailable; prior quotes retain their original timestamps"
                )

        await asyncio.gather(load("xstocks"), load("futures"))
        return {**self.quotes.get("xstocks", {}), **self.quotes.get("futures", {})}, errors

    async def candles(self, market, minutes):
        if market["kind"] == "futures":
            return await self.futures.candles(market["raw_id"], minutes)
        if market["kind"] == "xstocks":
            result = await self.spot.request(
                "OHLC",
                {"pair": market["raw_id"], "interval": minutes, "asset_class": "tokenized_asset"},
            )
            rows = next(v for k, v in result.items() if k != "last")
        else:
            rows = await self.spot.ohlc(self.spot.pairs[market["id"]], minutes)
        return [
            {"time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[6]}
            for r in rows[-720:]
        ]
