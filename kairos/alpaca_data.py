"""One execution-only Alpaca US book stream with timestamp-preserving REST recovery."""

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal

import aiohttp

from kairos import diagnostics
from kairos.alpaca_transport import PendingAlpacaData
from kairos.domain import Book, SafetyError, dec
from kairos.market_data import timestamp, validate_levels

STREAM_URL = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"


@dataclass
class AlpacaBook(Book):
    arrived_at: float
    observed_mono: float
    source: str = "REST"
    stream: object = None
    generation: int = 0

    def fresh(self, seconds):
        now = time.time()
        if (
            now < self.arrived_at - 2
            or self.received > now + 2
            or time.monotonic() < self.observed_mono
        ):
            raise SafetyError("Alpaca market clock moved backwards or source is in the future")
        if self.stream and (not self.stream.connected or self.generation != self.stream.generation):
            raise PendingAlpacaData("Alpaca book stream interrupted; fresh snapshot required")
        if now - self.received > seconds or time.monotonic() - self.observed_mono > seconds:
            raise PendingAlpacaData(
                f"Alpaca market data is stale ({max(0, now - self.received):.1f}s; limit {seconds}s)"
            )
        validate_levels(self.bids, self.asks)


class AlpacaData:
    def __init__(self, client):
        self.client = client
        self.pairs, self.candle_spec, self.book_age = {}, None, 10
        self.task, self.connected, self.blocked = None, False, False
        self.generation, self.authenticated = 0, False
        self.levels, self.books, self.rest_books, self.attempts = {}, {}, {}, {}
        self.rest_lock = asyncio.Lock()
        self.status, self.error = "inactive", None
        self.last_message = None

    def invalidate(self):
        self.generation += 1
        self.connected = self.authenticated = False
        self.levels.clear()
        self.books.clear()

    async def configure(self, pairs, candle=None, *, max_age=10):
        wanted = {p.id: p for p in pairs}
        changed = set(wanted) != set(self.pairs)
        if changed:
            await self.close()
        self.pairs, self.candle_spec, self.book_age = wanted, candle, max_age
        if (
            self.symbols()
            and self.client.session is not None
            and not self.blocked
            and not self.task
        ):
            self.task = asyncio.create_task(self.run())

    def symbols(self):
        return {
            self.client.symbol(p): p for p in self.pairs.values() if not self.client.is_equity(p)
        }

    async def close(self):
        task, self.task = self.task, None
        self.invalidate()
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.pairs.clear()
        self.rest_books.clear()
        self.attempts.clear()
        self.blocked = False
        self.status = "inactive"

    def message(self, row):
        kind = row.get("T")
        if kind == "error":
            code = row.get("code")
            self.blocked = code in {400, 401, 402, 403, 405, 406, 409, 410}
            raise SafetyError(
                f"Alpaca data stream error {code}; REST fallback remains freshness-gated"
            )
        if kind == "success":
            if row.get("msg") == "authenticated":
                self.authenticated = True
                return True
            return False
        if kind == "subscription":
            if not self.authenticated or set(row.get("orderbooks", [])) != set(self.symbols()):
                raise SafetyError("Unexpected Alpaca book subscription")
            self.connected, self.status, self.error = True, "streaming", None
            return False
        if kind != "o" or not self.connected or row.get("S") not in self.symbols():
            raise SafetyError("Unexpected Alpaca stream data")
        pair = self.symbols()[row["S"]]
        source = timestamp(row["t"])
        if source > time.time() + 2:
            raise SafetyError("Alpaca stream book is from the future")
        reset = row.get("r", False)
        if type(reset) is not bool:
            raise SafetyError("Invalid Alpaca book reset flag")
        previous = self.books.get(pair.id)
        if previous and source < previous.received:
            raise SafetyError("Alpaca book timestamp regressed")
        if not reset and pair.id not in self.levels:
            raise SafetyError("Alpaca book delta arrived before full reset")
        levels = [{}, {}] if reset else [dict(side) for side in self.levels[pair.id]]
        for side, field in zip(levels, ("b", "a"), strict=True):
            seen = set()
            for level in row[field]:
                price, size = dec(level["p"]), dec(level["s"])
                if price <= 0 or size < 0 or price in seen:
                    raise SafetyError("Invalid Alpaca book level")
                seen.add(price)
                if size:
                    side[price] = size
                else:
                    side.pop(price, None)
            if len(side) > 10000:
                raise SafetyError("Alpaca execution book exceeds memory bound")
        bids, asks = [
            sorted(side.items(), reverse=index == 0)[:100] for index, side in enumerate(levels)
        ]
        validate_levels(bids, asks)
        now = time.time()
        self.levels[pair.id] = levels
        self.books[pair.id] = AlpacaBook(
            pair, bids, asks, source, now, time.monotonic(), "WebSocket", self, self.generation
        )
        self.last_message = now
        return False

    async def run(self):
        backoff = 1
        while True:
            self.invalidate()
            self.status = "connecting"
            try:
                async with contextlib.AsyncExitStack() as stack:
                    async with asyncio.timeout(10):
                        ws = await stack.enter_async_context(
                            self.client.session.ws_connect(
                                STREAM_URL, heartbeat=10, max_msg_size=2**20
                            )
                        )
                    # aiohttp follows handshake redirects. Authenticate only after
                    # checking the final response URL; never send keys to a redirect.
                    if str(ws._response.url) not in {
                        STREAM_URL,
                        STREAM_URL.replace("wss:", "https:"),
                    }:
                        self.blocked = True
                        raise SafetyError("Alpaca stream redirected outside the pinned endpoint")
                    await ws.send_json(
                        {"action": "auth", "key": self.client.key, "secret": self.client.secret}
                    )
                    while True:
                        msg = await ws.receive(timeout=30 if self.authenticated else 10)
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            raise SafetyError(
                                "Alpaca stream closed or returned an unsupported frame"
                            )
                        rows = json.loads(msg.data, parse_float=Decimal)
                        if not isinstance(rows, list) or not rows:
                            raise SafetyError("Invalid Alpaca stream envelope")
                        for row in rows:
                            if self.message(row):
                                await ws.send_json(
                                    {"action": "subscribe", "orderbooks": sorted(self.symbols())}
                                )
                        if self.books:
                            backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                diagnostics.capture(exc, "alpaca-market-stream")
                self.error = (
                    str(exc)
                    if isinstance(exc, SafetyError)
                    else "Alpaca stream unavailable; bounded REST recovery"
                )
                if isinstance(exc, aiohttp.WSServerHandshakeError) and exc.status in {401, 403}:
                    self.blocked = True
            finally:
                self.invalidate()
            self.status = "blocked" if self.blocked else "reconnecting"
            if self.blocked:
                return
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)

    def fresh_book(self, pair):
        candidates = [
            book for book in (self.books.get(pair.id), self.rest_books.get(pair.id)) if book
        ]
        for book in sorted(candidates, key=lambda b: b.received, reverse=True):
            try:
                book.fresh(self.book_age)
                return book
            except PendingAlpacaData:
                pass
        return None

    def snapshot(self):
        details = []
        for pair in self.pairs.values():
            try:
                book = self.fresh_book(pair)
            except SafetyError:
                book = None  # Keep read-only diagnostics available for a bad clock.
            known = book or self.books.get(pair.id) or self.rest_books.get(pair.id)
            details.append(
                {
                    "pair": pair.id,
                    "fresh": book is not None,
                    "source": known.source if known else None,
                    "market_at": known.received if known else None,
                    "market_age_seconds": max(0, time.time() - known.received) if known else None,
                    "received_at": known.arrived_at if known else None,
                }
            )
        return {
            "source": "Alpaca US execution books: WebSocket with bounded REST fallback; equities: IEX REST",
            "status": self.status,
            "books_total": len(self.pairs),
            "books_ready": sum(row["fresh"] for row in details),
            "books": details,
            "error": self.error,
            "last_message_at": self.last_message,
            "candle_minutes": None,
            "requests": self.client.requests.snapshot(),
        }
