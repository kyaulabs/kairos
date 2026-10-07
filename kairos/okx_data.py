"""Environment-pinned OKX books5 snapshots; no live/demo or venue fallback."""

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal

import aiohttp

from kairos.domain import Book, SafetyError, dec
from kairos.market_data import validate_levels
from kairos.okx import PUBLIC_WS, PendingOKX, code


@dataclass
class OKXBook(Book):
    environment: str
    arrived_at: float
    observed_mono: float
    feed: object
    generation: int

    def fresh(self, seconds):
        now = time.time()
        if self.environment != self.feed.client.environment or self.pair.id not in self.feed.pairs:
            raise SafetyError(
                "OKX book environment/instrument mismatch; nothing submitted; select the correct environment"
            )
        if not self.feed.connected or self.generation != self.feed.generation:
            raise PendingOKX(
                "OKX book connection changed; nothing submitted; await a new same-environment snapshot"
            )
        if (
            self.received > now + 2
            or now < self.arrived_at - 2
            or time.monotonic() < self.observed_mono
        ):
            raise SafetyError(
                "OKX market clock is invalid; nothing submitted; synchronize clocks and inspect source timestamps"
            )
        source_age, residence = now - self.received, time.monotonic() - self.observed_mono
        if max(source_age, residence) > seconds:
            raise PendingOKX(
                f"OKX book stale: source age {source_age:.3f}s, local age {residence:.3f}s, limit {seconds}s; nothing submitted; await a new snapshot"
            )
        validate_levels(self.bids, self.asks)


class OKXData:
    def __init__(self, client):
        self.client = client
        self.pairs, self.books = {}, {}
        self.task = None
        self.generation = 0
        self.connected, self.blocked = False, False
        self.status, self.error = "inactive", None
        self.book_age, self.last_message = 10, None
        self.changed = asyncio.Event()

    def invalidate(self):
        self.generation += 1
        self.connected = False
        self.books.clear()
        self.changed.set()

    async def configure(self, pairs, candle=None, *, max_age=10):
        pairs = {p.id: p for p in pairs}
        if len(pairs) > 1:
            raise SafetyError(
                "OKX initially supports one explicitly selected execution instrument/currency"
            )
        if any(p.id not in self.client.pairs for p in pairs.values()):
            raise SafetyError(
                "OKX execution instrument was not discovered for this account/environment"
            )
        if set(pairs) != set(self.pairs):
            await self.close()
        self.pairs, self.book_age = pairs, max_age
        if pairs and self.client.session is not None and self.task is None and not self.blocked:
            self.task = asyncio.create_task(self.run())

    async def close(self):
        task, self.task = self.task, None
        self.invalidate()
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.pairs.clear()
        self.status, self.error, self.blocked = "inactive", None, False

    def argument(self):
        pair = next(iter(self.pairs.values()))
        return {"channel": "books5", "instId": self.client.instruments[pair.id]["instId"]}

    def message(self, row):
        if not isinstance(row, dict):
            raise SafetyError("Malformed OKX book message")
        if row.get("event") == "error":
            self.blocked = True
            raise SafetyError(
                f"OKX public subscription rejected ({code(row.get('code'))}); inspect instrument availability"
            )
        if row.get("event") == "notice":
            raise PendingOKX("OKX public service reconnect notice; new snapshot required")
        if row.get("event") == "subscribe":
            if row.get("arg") != self.argument():
                raise SafetyError("OKX subscribed to an unexpected book channel/instrument")
            self.connected, self.status, self.error = True, "streaming", None
            return
        if (
            not self.connected
            or row.get("arg") != self.argument()
            or row.get("action") != "snapshot"
        ):
            raise SafetyError("OKX requires a same-environment books5 full snapshot")
        if not isinstance(row.get("data"), list) or len(row["data"]) != 1:
            raise SafetyError("Incomplete OKX book snapshot")
        raw = row["data"][0]
        pair = next(iter(self.pairs.values()))
        stamp = dec(raw["ts"]) / 1000
        if stamp > dec(time.time()) + 2:
            raise SafetyError("OKX source timestamp is in the future")
        previous = self.books.get(pair.id)
        if previous and stamp < dec(previous.received):
            raise SafetyError("OKX source timestamp regressed; snapshot not usable")
        sides = []
        for name in ("bids", "asks"):
            if not isinstance(raw.get(name), list) or not 1 <= len(raw[name]) <= 5:
                raise SafetyError("OKX books5 levels are missing or exceed the declared depth")
            sides.append([[dec(r[0]), dec(r[1])] for r in raw[name]])
        validate_levels(*sides)
        value = OKXBook(
            pair,
            *sides,
            float(stamp),
            self.client.environment,
            time.time(),
            time.monotonic(),
            self,
            self.generation,
        )
        self.books[pair.id] = value
        self.last_message = value.arrived_at
        self.changed.set()

    async def run(self):
        attempts = 0
        while attempts < 5:
            self.invalidate()
            self.status = "connecting"
            try:
                url = PUBLIC_WS[self.client.environment]
                async with asyncio.timeout(10):
                    ws = await self.client.session.ws_connect(url, max_msg_size=2**20)
                async with ws:
                    if str(ws._response.url) not in {url, url.replace("wss:", "https:")}:
                        self.blocked = True
                        raise SafetyError("OKX public stream redirected outside its environment")
                    await ws.send_json({"op": "subscribe", "args": [self.argument()]})
                    while True:
                        try:
                            msg = await ws.receive(timeout=15)
                        except TimeoutError:
                            await ws.send_str("ping")
                            msg = await ws.receive(timeout=10)
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            raise PendingOKX(
                                "OKX public stream disconnected; old books invalidated"
                            )
                        if msg.data == "pong":
                            continue  # Heartbeat does not refresh a market timestamp.
                        self.message(json.loads(msg.data, parse_float=Decimal))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = (
                    str(exc)
                    if isinstance(exc, SafetyError)
                    else "OKX public stream unavailable or malformed; inspect connection and regional endpoint"
                )
                if isinstance(exc, SafetyError) and not isinstance(exc, PendingOKX):
                    self.blocked = True
            finally:
                self.invalidate()
            attempts += 1
            if self.blocked:
                break
            self.status = "reconnecting"
            await asyncio.sleep(min(2 ** (attempts - 1), 16))
        self.blocked, self.status = True, "blocked"
        self.error = self.error or "OKX reconnect limit reached; explicitly restart the feed"
        self.changed.set()

    async def book(self, pair):
        if set(self.pairs) != {pair.id}:
            raise SafetyError(
                "OKX book does not match the selected execution instrument/currency; chart browsing cannot change it"
            )
        deadline = time.monotonic() + 10
        reason = "no full snapshot received"
        while time.monotonic() < deadline:
            if self.blocked:
                raise SafetyError(self.error or "OKX data subscription blocked")
            value = self.books.get(pair.id)
            if value:
                try:
                    value.fresh(self.book_age)
                    return value
                except PendingOKX as exc:
                    reason = str(exc)
            self.changed.clear()
            try:
                await asyncio.wait_for(
                    self.changed.wait(), min(0.25, max(0.001, deadline - time.monotonic()))
                )
            except TimeoutError:
                pass
        raise PendingOKX(
            f"OKX {self.client.environment} book startup/read wait exceeded 10s: {reason}; no order submitted by this data read; inspect the regional books5 feed"
        )

    def snapshot(self):
        books = []
        for pair in self.pairs.values():
            book = self.books.get(pair.id)
            fresh = False
            if book:
                try:
                    book.fresh(self.book_age)
                    fresh = True
                except SafetyError:
                    pass
            books.append(
                {
                    "pair": pair.id,
                    "fresh": fresh,
                    "source": f"OKX U.S. {self.client.environment} books5",
                    "market_at": book.received if book else None,
                    "received_at": book.arrived_at if book else None,
                    "market_age_seconds": max(0, time.time() - book.received) if book else None,
                }
            )
        return {
            "status": self.status,
            "environment": self.client.environment,
            "books_total": len(books),
            "books_ready": sum(b["fresh"] for b in books),
            "books": books,
            "error": self.error,
            "last_message_at": self.last_message,
            "candle_minutes": None,
            "requests": {"last": self.client.last_request},
        }
