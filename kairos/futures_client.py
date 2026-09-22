"""Native linear-perpetual transport. No retries, transfers, or implicit conversions."""

import asyncio
import time
from datetime import datetime
from urllib.parse import urlencode

import aiohttp
from yarl import URL

from kairos.domain import ZERO, Book, Pair, SafetyError, dec
from kairos.retail import Futures, futures_signature, linear_perpetual


class UnsettledFutures(SafetyError):
    """Valid history reads have not yet established a consistent order outcome."""


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class FuturesTrading(Futures):
    PUBLIC = Futures.PUBLIC | {"orderbook", "historical-funding-rates"}
    PRIVATE = Futures.PRIVATE | {"pnlpreferences", "leveragepreferences"}
    WRITES = {"sendorder", "cancelorder", "cancelallordersafter"}

    def __init__(self, session, key="", secret="", allow_live=False):
        super().__init__(session, key, secret)
        self.allow_live = allow_live
        self.pairs, self.metadata = {}, {}

    async def get(self, method, params=None):
        if method not in self.PUBLIC | self.PRIVATE:
            raise SafetyError("Futures endpoint is not an authorized read")
        return await self.request(method, params)

    async def request(self, method, params=None, *, history=False):
        if history:
            if method not in {"orders", "executions", "account-log"}:
                raise SafetyError("Unsupported Futures history endpoint")
            path, verb = "/api/history/v3/" + method, "GET"
        else:
            if method not in self.PUBLIC | self.PRIVATE | self.WRITES:
                raise SafetyError("Unsupported Futures endpoint")
            path = "/api/v3/" + method
            verb = "POST" if method in self.WRITES else "GET"
        private = history or method not in self.PUBLIC
        if method in self.WRITES and not self.allow_live:
            raise SafetyError("Futures writes disabled")
        post = urlencode(params or {})
        headers = {}
        if private:
            if not self.key or not self.secret:
                raise SafetyError("Separate Futures credentials are required")
            try:
                headers = {
                    "APIKey": self.key,
                    "Authent": futures_signature(path, self.secret, post),
                }
            except ValueError:
                raise SafetyError("Invalid Futures credential encoding") from None
        url = "https://futures.kraken.com" + ("" if history else "/derivatives") + path
        # Sign exactly the URL-encoded bytes sent, including history query parameters.
        if verb == "GET" and post:
            url += "?" + post
        if verb == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        async with self.lock:
            await asyncio.sleep(max(0, 0.2 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                async with self.session.request(
                    verb,
                    URL(url, encoded=True),
                    data=post if verb == "POST" else None,
                    headers=headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as response:
                    if response.status != 200:
                        raise SafetyError(
                            "Futures request failed; any write outcome may be unknown"
                        )
                    data = await response.json()
                    continuation = response.headers.get("Next-Continuation-Token")
            except (aiohttp.ClientError, TimeoutError, ValueError):
                raise SafetyError(
                    "Futures transport failed; any write outcome may be unknown"
                ) from None
        if not isinstance(data, dict) or data.get("result", "success") != "success":
            raise SafetyError("Futures request rejected; inspect permissions and reconcile writes")
        if history:
            data["continuationToken"] = continuation or data.get("continuationToken")
        return data

    async def catalog(self):
        data = await self.get("instruments")
        pairs, metadata = {}, {}
        for row in data["instruments"]:
            if not linear_perpetual(row):
                continue
            precision = row["contractValueTradePrecision"]
            if type(precision) is not int or not 0 <= precision <= 12:
                continue
            lot = dec(10) ** -precision
            pair = Pair(
                "futures:" + row["symbol"],
                row["symbol"],
                row["base"],
                "USD",
                dec(row["tickSize"]),
                lot,
                lot,
                dec(0),
            )
            if pair.tick <= 0:
                continue
            pairs[pair.id], metadata[pair.id] = pair, row
        self.pairs, self.metadata = pairs, metadata
        return pairs

    async def book(self, pair):
        started = time.time()
        data = await self.get("orderbook", {"symbol": pair.symbol})
        row = data["orderBook"]
        bids = sorted([[dec(p), dec(q)] for p, q in row["bids"]], reverse=True)
        asks = sorted([[dec(p), dec(q)] for p, q in row["asks"]])
        if not bids or not asks or any(p <= 0 or q <= 0 for p, q in bids + asks):
            raise SafetyError("Invalid Futures depth")
        book = Book(pair, bids, asks, min(started, timestamp(data["serverTime"])))
        book.fresh(10)
        return book

    async def market(self, pair):
        started = time.time()
        data = await self.get("tickers")
        row = next((r for r in data["tickers"] if r["symbol"] == pair.symbol), None)
        if not row or row.get("suspended") is not False or row.get("tag") != "perpetual":
            raise SafetyError("Futures contract is not an active perpetual")
        if time.time() - min(started, timestamp(data["serverTime"])) > 10:
            raise SafetyError("Stale Futures mark")
        if dec(row["markPrice"]) <= 0:
            raise SafetyError("Invalid Futures mark")
        return row

    async def completed_candles(self, pair, minutes):
        data = await self.candles(pair.symbol, minutes)
        cutoff = int(time.time()) // (minutes * 60) * minutes * 60
        completed = [r for r in data if r["time"] < cutoff]
        if not completed or completed[-1]["time"] + minutes * 60 != cutoff:
            raise SafetyError("Latest completed Futures candle is unavailable")
        previous = -1
        for row in completed:
            opening, high, low, close = (dec(row[k]) for k in ("open", "high", "low", "close"))
            if (
                row["time"] <= previous
                or row["time"] % (minutes * 60)
                or not ZERO < low <= min(opening, close) <= max(opening, close) <= high
                or dec(row["volume"]) < 0
            ):
                raise SafetyError("Invalid Futures candle history")
            previous = row["time"]
        return [
            [r["time"], r["open"], r["high"], r["low"], r["close"], "0", r["volume"], 0]
            for r in completed
        ]

    async def history(self, method, since):
        params = {"since": int(since * 1000), "sort": "asc", "count": 1000}
        rows, seen = [], set()
        for _ in range(50):
            data = await self.request(method, params, history=True)
            batch = data["logs" if method == "account-log" else "elements"]
            rows.extend(batch)
            if method == "account-log":
                if len(batch) < 1000:
                    return rows
                cursor = max(int(r["id"]) for r in batch) + 1
                if cursor in seen:
                    break
                params["from"] = cursor
            else:
                cursor = data.get("continuationToken")
                if not cursor:
                    if len(batch) >= 1000:
                        raise SafetyError("Futures history may be truncated; remain stopped")
                    return rows
                if cursor in seen:
                    break
                params["continuation_token"] = cursor
            seen.add(cursor)
        raise SafetyError("Futures history exceeds reconciliation budget; remain stopped")

    async def order_state(self, order):
        """Recover by durable client ID using full history, not the five-second status cache."""
        events = await self.history("orders", order["created"] - 1)
        matched = []
        for element in events:
            for kind, event in element["event"].items():
                row = event.get("order") or event.get("newOrder")
                if row and row.get("clientId") == order["id"]:
                    matched.append((element["timestamp"], kind, row))
        if not matched:
            raise UnsettledFutures("Futures order outcome unconfirmed; no resubmission permitted")
        _, kind, row = max(matched, key=lambda item: item[0])
        if (
            row["tradeable"] != order["symbol"]
            or row["direction"].lower() != order["side"]
            or dec(row["limitPrice"]) != dec(order["price"])
            or dec(row["quantity"]) != dec(order["volume"])
        ):
            raise SafetyError("Futures order identity mismatch")
        fills = {}
        for element in await self.history("executions", order["created"] - 1):
            execution = element["event"]["execution"]["execution"]
            if execution["order"].get("clientId") == order["id"]:
                if execution["order"]["uid"] != row["uid"]:
                    raise SafetyError("Duplicate Futures client ID")
                if dec(execution["quantity"]) <= 0 or dec(execution["price"]) <= 0:
                    raise SafetyError("Invalid Futures execution history")
                if execution["uid"] in fills and fills[execution["uid"]] != execution:
                    raise SafetyError("Conflicting Futures execution history")
                fills[execution["uid"]] = execution
        filled = sum((dec(r["quantity"]) for r in fills.values()), dec(0))
        if filled > dec(order["volume"]):
            raise SafetyError("Futures executions exceed order intent")
        cost = sum((dec(r["quantity"]) * dec(r["price"]) for r in fills.values()), dec(0))
        fee = sum((dec(r["orderData"]["fee"]) for r in fills.values()), dec(0))
        if filled == dec(order["volume"]):
            status = "closed"
        elif kind in {"OrderCancelled", "OrderRejected"}:
            if filled != dec(row["filled"]):
                raise UnsettledFutures(
                    "Futures fill history is not yet consistent; reconcile again"
                )
            status = "canceled" if kind == "OrderCancelled" else "rejected"
        else:
            opened = (await self.get("openorders"))["openOrders"]
            current = next((r for r in opened if r.get("cliOrdId") == order["id"]), None)
            if not current or dec(current["filledSize"]) != filled:
                raise UnsettledFutures("Futures order/fill histories are not yet consistent")
            status = "open"
        return row["uid"], filled, cost, fee, status

    async def account(self):
        data = await self.get("accounts")
        flex = data["accounts"]["flex"]
        if flex["type"] != "multiCollateralMarginAccount":
            raise SafetyError("USD multi-collateral Futures wallet required")
        if any(dec(r["quantity"]) != 0 for a, r in flex["currencies"].items() if a != "USD"):
            raise SafetyError("Futures execution requires a dedicated USD-only collateral wallet")
        return flex

    async def fees(self, spot, pair):
        # Futures fee schedules were deprecated in June 2026. Use the central fee service.
        data = await spot.request(
            "TradeVolume",
            {
                "pair": [{"asset": pair.symbol, "aclass": "derivatives"}],
            },
            private=True,
        )
        taker = dec(data["fees"][pair.symbol]["fee"]) * 100
        maker = dec(data.get("fees_maker", data["fees"])[pair.symbol]["fee"]) * 100
        return maker, taker
