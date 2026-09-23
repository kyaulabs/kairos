"""Execution-only Spot public streams. REST remains the bootstrap/recovery source."""

import asyncio
import contextlib
import json
import time
import zlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import aiohttp

from kairos.domain import Book, SafetyError, dec


def ws_symbol(pair):
    return "/".join("DOGE" if asset == "XDG" else asset for asset in pair.symbol.split("/"))


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SafetyError("Public stream timestamp has no timezone")
    return parsed.timestamp()


def checksum(bids, asks):
    # Keep wire precision, including trailing zeroes; never round through float.
    text = "".join(
        format(value, "f").replace(".", "").lstrip("0")
        for side in (asks[:10], bids[:10])
        for level in side
        for value in level
    )
    return zlib.crc32(text.encode())


def validate_levels(bids, asks):
    for levels, reverse in ((bids, True), (asks, False)):
        prices = [p for p, _ in levels]
        if (
            not levels
            or len(levels) > 100
            or any(p <= 0 or q <= 0 for p, q in levels)
            or prices != sorted(set(prices), reverse=reverse)
        ):
            raise SafetyError("Invalid execution depth")
    if bids[0][0] >= asks[0][0]:
        raise SafetyError("Invalid or crossed order book")


@dataclass
class StreamBook(Book):
    stream: object
    generation: int

    def fresh(self, seconds):
        if self.generation != self.stream.generation or not self.stream.healthy():
            raise SafetyError("Execution stream interrupted; replan with fresh market data")
        if time.time() < self.received:
            raise SafetyError("Market snapshot clock moved backwards")
        super().fresh(min(seconds, self.stream.book_age))

    def fill(self, side, volume, limit):
        # Includes paper maintenance reductions, which can await order cleanup first.
        self.fresh(self.stream.book_age)
        return super().fill(side, volume, limit)


class PendingCandle(SafetyError):
    """Only the newest closed bucket is missing from an otherwise usable window."""

    def __init__(self, cutoff):
        super().__init__("Execution candles are stale or incomplete")
        self.cutoff = cutoff


class CandleHistory:
    def __init__(self, pair, minutes, required):
        self.pair, self.minutes, self.required = pair, minutes, required
        self.rows, self.seed = {}, {}
        self.ready = False

    def validate(self, row):
        ts = dec(row[0])
        opening, high, low, close, vwap, volume = map(dec, row[1:7])
        trades = dec(row[7])
        if (
            ts % (self.minutes * 60)
            or ts < 0
            or not 0 < low <= min(opening, close) <= max(opening, close) <= high
            or vwap < 0
            or (volume > 0 and not low <= vwap <= high)
            or volume < 0
            or trades < 0
            or trades != int(trades)
        ):
            raise SafetyError("Invalid execution candle")
        return [int(ts), *map(str, (opening, high, low, close, vwap, volume)), int(trades)]

    def accept(self, data, kind):
        if not isinstance(data, list) or not data:
            raise SafetyError("Empty execution candle message")
        if kind == "snapshot":
            if self.ready:
                raise SafetyError("Unexpected candle snapshot")
        elif not self.ready:
            raise SafetyError("Candle update arrived before snapshot")
        cutoff = int(time.time()) // (self.minutes * 60) * self.minutes * 60
        for item in data:
            if item["symbol"] != ws_symbol(self.pair) or item["interval"] != self.minutes:
                raise SafetyError("Wrong execution candle subscription")
            row = self.validate(
                [
                    timestamp(item["interval_begin"]),
                    *[
                        item[k]
                        for k in ("open", "high", "low", "close", "vwap", "volume", "trades")
                    ],
                ]
            )
            ts = row[0]
            if ts > cutoff or (self.rows and ts < max(self.rows)):
                raise SafetyError("Out-of-order or future execution candle")
            old = self.rows.get(ts)
            if old and (
                dec(row[1]) != dec(old[1])
                or dec(row[2]) < dec(old[2])
                or dec(row[3]) > dec(old[3])
                or dec(row[6]) < dec(old[6])
                or row[7] < old[7]
            ):
                raise SafetyError("Execution candle regressed")
            self.rows[ts] = row
        self.rows = dict(sorted(self.rows.items())[-720:])
        self.ready = True

    def rest_rows(self, rows, started):
        # REST's last row is uncommitted, even if the wall clock crossed a boundary in flight.
        cutoff = int(started) // (self.minutes * 60) * self.minutes * 60
        result = [self.validate(row) for row in rows[:-1]]
        times = [row[0] for row in result]
        if times != sorted(set(times)):
            raise SafetyError("Unordered execution candle history")
        completed = [row for row in result if row[0] < cutoff]
        return {row[0]: row for row in completed[-720:]}

    def completed(self, *, streamed=True):
        rows = dict(self.seed)
        if streamed and self.ready and self.rows:
            # No clock-only promotion: a later trade bucket must confirm completion.
            last = max(self.rows)
            for ts, row in self.rows.items():
                if ts < last and (ts not in rows or row[7] > rows[ts][7]):
                    rows[ts] = row
        step = self.minutes * 60
        cutoff = int(time.time()) // step * step
        result = [row for ts, row in sorted(rows.items()) if ts < cutoff][-720:]
        tail = result[-self.required :]
        if len(tail) != self.required or any(
            row[0] != cutoff - (len(tail) - i) * step for i, row in enumerate(tail)
        ):
            previous = result[-(self.required - 1) :]
            if len(previous) == self.required - 1 and all(
                row[0] == cutoff - (len(previous) - i + 1) * step for i, row in enumerate(previous)
            ):
                raise PendingCandle(cutoff)
            raise SafetyError("Execution candles are stale or incomplete")
        return [list(row) for row in result]


class PublicMarketData:
    MAX_AGE = 10

    def __init__(self, client):
        self.client = client
        self.pairs = {}
        self.candle_spec = None
        self.task = None
        self.generation = 0
        self.last_message = None
        self.error = None
        self.last_candle_source = None
        self.books = {}
        self.series = None
        self.candle_lock = asyncio.Lock()
        self.book_age = self.MAX_AGE

    def invalidate(self):
        self.generation += 1
        self.books = {}
        self.last_message = None
        self.series = CandleHistory(*self.candle_spec) if self.candle_spec else None
        self.last_candle_source = None

    def healthy(self):
        return (
            self.last_message is not None
            and 0 <= time.monotonic() - self.last_message <= self.MAX_AGE
        )

    async def configure(self, pairs, candle=None, *, max_age=10):
        pairs = {ws_symbol(p): p for p in pairs}
        max_age = min(self.MAX_AGE, max_age)
        if pairs == self.pairs and candle == self.candle_spec and max_age == self.book_age:
            return
        await self.close()
        self.pairs, self.candle_spec, self.book_age = pairs, candle, max_age
        self.invalidate()
        self.error = None
        if pairs:
            self.task = asyncio.create_task(self.run())

    async def close(self):
        self.invalidate()  # Revoke already-issued books before any cancellation awaits.
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        self.pairs, self.candle_spec = {}, None
        self.series = None

    def book(self, pair):
        book = self.books.get(ws_symbol(pair))
        if not book or book.pair != pair:
            return None
        try:
            book.fresh(self.book_age)
        except SafetyError:
            return None
        # Callers must not mutate the maintained book or revive it with a new timestamp.
        return StreamBook(
            pair,
            [r[:] for r in book.bids],
            [r[:] for r in book.asks],
            book.received,
            self,
            self.generation,
        )

    async def candles(self, pair, minutes):
        async with self.candle_lock:
            deadline, pending = None, None
            for attempt in range(4):
                try:
                    if deadline is None:
                        return await self.read_candles(pair, minutes)
                    # Bound the whole retry, including REST pacing/network time. Read-only:
                    # do not retry order writes, refresh fees or extend data validity here.
                    async with asyncio.timeout_at(deadline):
                        await asyncio.sleep(0.5)
                        return await self.read_candles(pair, minutes)
                except PendingCandle as exc:
                    elapsed = time.time() - exc.cutoff
                    if attempt == 3 or not 0 <= elapsed < 5:
                        raise
                    pending = exc
                    if deadline is None:
                        deadline = asyncio.get_running_loop().time() + min(3, 5 - elapsed)
                except TimeoutError:
                    if pending is None:
                        raise
                    raise pending from None

    async def read_candles(self, pair, minutes):
        series = self.series
        if not series or series.pair != pair or series.minutes != minutes:
            return None
        if self.healthy() and series.ready and series.seed:
            try:
                rows = series.completed()
                self.last_candle_source = "WebSocket"
                return rows
            except SafetyError:
                pass  # A missing boundary needs REST confirmation, not a fabricated bar.
        generation, started = self.generation, time.time()
        rows = await self.client.ohlc(pair, minutes)
        seed = series.rest_rows(rows, started)
        if generation == self.generation and self.healthy():
            series.seed = seed
            result = series.completed()
        else:
            # The REST read is still usable; never install it into a different connection.
            recovered = CandleHistory(pair, minutes, series.required)
            recovered.seed = seed
            result = recovered.completed(streamed=False)
        if generation == self.generation:
            self.last_candle_source = "REST"
        return result

    def accept(self, message):
        try:
            if self.last_message is not None and not self.healthy():
                raise SafetyError("Public stream stalled; a new snapshot is required")
            if not isinstance(message, dict) or message.get("success") is False:
                raise SafetyError("Public market subscription rejected")
            channel = message.get("channel")
            if channel == "book":
                kind = message["type"]
                if (
                    kind not in {"snapshot", "update"}
                    or not isinstance(message["data"], list)
                    or not message["data"]
                ):
                    raise SafetyError("Invalid book message type or data")
                for item in message["data"]:
                    pair = self.pairs[item["symbol"]]
                    previous = self.books.get(item["symbol"])
                    if (kind == "update" and previous is None) or (kind == "snapshot" and previous):
                        raise SafetyError("Book snapshot/update order is invalid")
                    levels = []
                    for name in ("bids", "asks"):
                        side = dict(getattr(previous, name)) if previous else {}
                        seen = set()
                        for change in item.get(name, []):
                            price, qty = dec(change["price"]), dec(change["qty"])
                            if (
                                price <= 0
                                or qty < 0
                                or (kind == "snapshot" and (not qty or price in seen))
                            ):
                                raise SafetyError("Invalid book level")
                            seen.add(price)
                            side.pop(price, None)
                            if qty:
                                side[price] = qty
                        levels.append(
                            [[p, q] for p, q in sorted(side.items(), reverse=name == "bids")[:100]]
                        )
                    bids, asks = levels
                    validate_levels(bids, asks)
                    if (
                        type(item["checksum"]) is not int
                        or checksum(bids, asks) != item["checksum"]
                    ):
                        raise SafetyError("Execution book checksum mismatch")
                    now, remote = time.time(), timestamp(item["timestamp"])
                    if not -2 <= now - remote <= self.MAX_AGE or (
                        previous and remote < previous.received
                    ):
                        raise SafetyError("Execution book timestamp is stale or out of order")
                    self.books[item["symbol"]] = StreamBook(
                        pair, bids, asks, min(now, remote), self, self.generation
                    )
            elif channel == "ohlc":
                if not self.series or message["type"] not in {"snapshot", "update"}:
                    raise SafetyError("Unexpected execution candle stream")
                self.series.accept(message["data"], message["type"])
            elif channel not in {"heartbeat", "status", None}:
                raise SafetyError("Unexpected public execution channel")
            self.last_message = time.monotonic()
        except SafetyError:
            self.invalidate()
            raise
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            self.invalidate()
            raise SafetyError("Public stream invalid; fresh REST recovery required") from None

    async def run(self):
        backoff = 5
        while True:
            try:
                async with self.client.session.ws_connect(
                    "wss://ws.kraken.com/v2",
                    heartbeat=20,
                    receive_timeout=self.MAX_AGE,
                    max_msg_size=2**20,
                ) as ws:
                    try:
                        names = sorted(self.pairs)
                        for start in range(0, len(names), 100):
                            await ws.send_json(
                                {
                                    "method": "subscribe",
                                    "params": {
                                        "channel": "book",
                                        "symbol": names[start : start + 100],
                                        "depth": 100,
                                        "snapshot": True,
                                    },
                                }
                            )
                        if self.series:
                            await ws.send_json(
                                {
                                    "method": "subscribe",
                                    "params": {
                                        "channel": "ohlc",
                                        "symbol": [ws_symbol(self.series.pair)],
                                        "interval": self.series.minutes,
                                        "snapshot": True,
                                    },
                                }
                            )
                        async for message in ws:
                            if message.type != aiohttp.WSMsgType.TEXT:
                                raise SafetyError("Public stream disconnected")
                            self.accept(json.loads(message.data, parse_float=Decimal))
                            if self.books:
                                self.error = None
                        raise SafetyError("Public stream disconnected")
                    finally:
                        self.invalidate()
            except (aiohttp.ClientError, TimeoutError, SafetyError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, SafetyError) else "Public stream unavailable"
                self.error = reason + "; using fresh REST reads while reconnecting."
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def snapshot(self):
        return {
            "status": "connected"
            if self.healthy()
            else "reconnecting"
            if self.task
            else "inactive",
            "books_ready": sum(self.book(pair) is not None for pair in self.pairs.values()),
            "books_total": len(self.pairs),
            "candle_minutes": self.candle_spec[1] if self.candle_spec else None,
            "last_candle_source": self.last_candle_source,
            "error": self.error,
        }
