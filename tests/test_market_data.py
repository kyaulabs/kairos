import asyncio
import copy
import json
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from kairos.clients import Kraken
from kairos.domain import SafetyError, dec
from kairos.engine import Engine
from kairos.market_data import checksum, ws_symbol
from kairos.settings import DEFAULTS
from kairos.store import Store
from kairos.web import feeds
from tests.helpers import BTC, ETH, candle_rows, fake_jev, fake_kraken
from tests.test_scalping import candles


def stamp(value):
    return datetime.fromtimestamp(value, UTC).isoformat()


def book_message(*, kind="snapshot", bids=None, asks=None, at=None):
    bids = [[dec("99.0"), dec("1.00000000")]] if bids is None else bids
    asks = [[dec("101.0"), dec("2.00000000")]] if asks is None else asks
    return {
        "channel": "book",
        "type": kind,
        "data": [
            {
                "symbol": "BTC/USD",
                "timestamp": stamp(time.time() if at is None else at),
                "bids": [{"price": p, "qty": q} for p, q in bids],
                "asks": [{"price": p, "qty": q} for p, q in asks],
                "checksum": checksum(bids, asks),
            }
        ],
    }


def candle_message(rows, *, kind="snapshot", minutes=1):
    return {
        "channel": "ohlc",
        "type": kind,
        "data": [
            dict(
                zip(
                    ("open", "high", "low", "close", "vwap", "volume", "trades"),
                    row[1:8],
                    strict=True,
                ),
                symbol="BTC/USD",
                interval=minutes,
                interval_begin=stamp(row[0]),
            )
            for row in rows
        ],
    }


class MarketDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = Kraken(Mock(), None)
        self.client.request = AsyncMock()
        self.feed = self.client.market_data
        self.feed.pairs = {"BTC/USD": BTC}
        self.feed.candle_spec = (BTC, 1, 31)
        self.feed.invalidate()
        self.now = int(time.time()) // 60 * 60 + 30
        self.clock = patch("kairos.market_data.time.time", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.addAsyncCleanup(self.feed.close)

    def seed(self):
        self.feed.accept(book_message())
        rows = candle_rows(count=40)
        self.feed.accept(candle_message(rows[-10:]))
        self.client.request.return_value = {BTC.id: rows, "last": rows[-2][0]}
        return rows

    async def test_book_uses_public_stream_without_rest_and_copies_levels(self):
        message = book_message()
        untouched = copy.deepcopy(message)
        self.feed.accept(message)
        first = await self.client.book(BTC)
        self.assertEqual(first.received, self.now)
        first.bids[0][1] = dec("999")
        second = await self.client.book(BTC)
        self.assertEqual(second.bids[0][1], dec(1))
        self.assertEqual(message, untouched)
        self.client.request.assert_not_awaited()
        self.assertEqual(self.feed.snapshot()["books_ready"], 1)

    async def test_wire_decimal_precision_and_known_crc(self):
        # Independently concatenated asks + bids, preserving significant trailing zeroes.
        import zlib

        raw = '{"channel":"book","type":"snapshot","data":[{"symbol":"BTC/USD","bids":[{"price":99.0,"qty":1.00000000}],"asks":[{"price":101.0,"qty":2.00000000}]}]}'
        message = json.loads(raw, parse_float=Decimal)
        row = message["data"][0]
        row.update(timestamp=stamp(self.now), checksum=zlib.crc32(b"1010200000000990100000000"))
        self.feed.accept(message)
        self.assertEqual(str(self.feed.book(BTC).bids[0][1]), "1.00000000")
        # Float decoding loses the zeroes and must not pass this same checksum.
        bad = json.loads(raw)
        bad["data"][0].update(timestamp=row["timestamp"], checksum=row["checksum"])
        self.feed.invalidate()
        with self.assertRaises(SafetyError):
            self.feed.accept(bad)

    async def test_deltas_delete_repeat_prices_and_trim_depth_before_checksum(self):
        bids = [[dec(100 - i), dec(1)] for i in range(100)]
        asks = [[dec(101 + i), dec(1)] for i in range(100)]
        self.feed.accept(book_message(bids=bids, asks=asks))
        new_bids = [[dec("100.5"), dec(3)], *bids[:99]]
        new_asks = [[dec("100.8"), dec(2)], *asks[:99]]
        message = book_message(kind="update", bids=new_bids, asks=new_asks)
        message["data"][0]["bids"] = [
            {"price": dec("100.5"), "qty": dec(1)},
            {"price": dec("100.5"), "qty": dec(0)},
            {"price": dec("100.5"), "qty": dec(3)},
        ]
        message["data"][0]["asks"] = [{"price": dec("100.8"), "qty": dec(2)}]
        self.feed.accept(message)
        book = self.feed.book(BTC)
        self.assertEqual(book.bids, new_bids)
        self.assertEqual(book.asks, new_asks)
        self.assertEqual(len(book.bids), 100)

    async def test_bad_data_revokes_already_issued_books(self):
        for change in (
            lambda r: r.update(checksum=0),
            lambda r: r.update(checksum=True),
            lambda r: r.update(symbol="ETH/USD"),
            lambda r: r.update(timestamp=stamp(self.now - 20)),
            lambda r: r.update(timestamp=stamp(self.now + 20)),
            lambda r: r["bids"][0].update(qty="-1"),
            lambda r: r["bids"][0].update(price="NaN"),
        ):
            self.feed.invalidate()
            self.feed.accept(book_message())
            held = self.feed.book(BTC)
            bad = book_message(kind="update")
            change(bad["data"][0])
            with self.assertRaises(SafetyError):
                self.feed.accept(bad)
            self.assertIsNone(self.feed.book(BTC))
            with self.assertRaises(SafetyError):
                held.fresh(30)
            with self.assertRaises(SafetyError):
                held.fill("buy", dec(1), dec(102))

    async def test_crossed_duplicate_and_update_before_snapshot_are_rejected(self):
        for message in (
            book_message(kind="update"),
            book_message(bids=[[dec(102), dec(1)]]),
            book_message(bids=[[dec(99), dec(1)], [dec(99), dec(1)]]),
            book_message(bids=[]),
        ):
            with self.assertRaises(SafetyError):
                self.feed.accept(message)
            self.assertIsNone(self.feed.book(BTC))

    async def test_stale_book_and_dead_connection_are_not_revived_by_reads(self):
        self.feed.accept(book_message())
        held = self.feed.book(BTC)
        with patch("kairos.market_data.time.time", return_value=self.now + 11):
            self.feed.accept({"channel": "heartbeat"})
            self.assertIsNone(self.feed.book(BTC))
        with patch("kairos.market_data.time.monotonic", return_value=self.feed.last_message + 11):
            self.assertIsNone(self.feed.book(BTC))
            with self.assertRaises(SafetyError):
                held.fresh(30)
        with patch("kairos.market_data.time.time", return_value=self.now - 1):
            self.assertIsNone(self.feed.book(BTC))
        with patch("kairos.market_data.time.monotonic", return_value=self.feed.last_message + 11):
            with self.assertRaisesRegex(SafetyError, "stalled"):
                self.feed.accept({"channel": "heartbeat"})
        self.feed.accept({"channel": "heartbeat"})
        with self.assertRaises(SafetyError):
            held.fresh(30)

    async def test_rest_recovery_is_fresh_validated_and_does_not_seed_a_delta_book(self):
        self.client.request.return_value = {
            BTC.id: {"bids": [["99", "1", 0]], "asks": [["101", "2", 0]]}
        }
        book = await self.client.book(BTC)
        self.assertEqual(book.received, self.now)
        self.client.request.assert_awaited_once_with("Depth", {"pair": BTC.id, "count": 100})
        self.assertIsNone(self.feed.book(BTC))
        with self.assertRaises(SafetyError):
            self.feed.accept(book_message(kind="update"))
        for bids in ([["99", "-1", 0]], [["99", "1", 0], ["100", "1", 0]]):
            self.client.request.return_value[BTC.id]["bids"] = bids
            with self.assertRaises(SafetyError):
                await self.client.book(BTC)

    async def test_slow_rest_book_is_not_timestamped_fresh_on_arrival(self):
        self.client.request.return_value = {BTC.id: {"bids": [["99", "1"]], "asks": [["101", "2"]]}}
        with patch(
            "kairos.clients.time.time", side_effect=[self.now, self.now + 11, self.now + 11]
        ):
            with self.assertRaisesRegex(SafetyError, "stale"):
                await self.client.book(BTC)

    async def test_candles_bootstrap_once_then_use_streamed_completed_buckets(self):
        rows = self.seed()
        self.assertEqual(await self.client.candles(BTC, 1), rows[:-1])
        self.assertEqual(self.feed.last_candle_source, "REST")
        self.assertEqual(await self.client.candles(BTC, 1), rows[:-1])
        self.client.request.assert_awaited_once()
        next_row = [rows[-1][0] + 60, *rows[-1][1:]]
        with patch("kairos.market_data.time.time", return_value=self.now + 60):
            self.feed.accept(candle_message([next_row], kind="update"))
            completed = await self.client.candles(BTC, 1)
        self.assertEqual(completed[-1], rows[-1])
        self.assertEqual(len(completed), len(rows))
        self.assertEqual(self.feed.last_candle_source, "WebSocket")
        self.client.request.assert_awaited_once()
        completed[-1][4] = "0"
        self.assertNotEqual(self.feed.series.rows[rows[-1][0]][4], "0")

    async def test_clock_alone_never_finalizes_a_forming_candle(self):
        self.seed()
        await self.client.candles(BTC, 1)
        with patch("kairos.market_data.time.time", return_value=self.now + 60):
            with self.assertRaisesRegex(SafetyError, "stale or incomplete"):
                await self.client.candles(BTC, 1)
        self.assertEqual(self.client.request.await_count, 2)
        self.client.request.side_effect = SafetyError("REST unavailable")
        with patch("kairos.market_data.time.time", return_value=self.now + 60):
            with self.assertRaisesRegex(SafetyError, "REST unavailable"):
                await self.client.candles(BTC, 1)

    async def test_rest_boundary_crossing_does_not_promote_uncommitted_last_row(self):
        rows = self.seed()
        series = self.feed.series
        with patch("kairos.market_data.time.time", return_value=self.now + 60):
            series.seed = series.rest_rows(rows, self.now)
            with self.assertRaises(SafetyError):
                series.completed()

    async def test_gaps_and_malformed_candles_block_instead_of_forward_filling(self):
        rows = self.seed()
        for malformed in (
            rows[:-3] + rows[-2:],
            [*rows[:-2], [rows[-2][0], "0", "0", "0", "0", "0", "0", 0], rows[-1]],
        ):
            self.feed.series.seed = {}
            self.feed.series.rows = {}
            self.client.request.return_value = {BTC.id: malformed}
            with self.assertRaises(SafetyError):
                await self.client.candles(BTC, 1)

    async def test_candle_regressions_wrong_interval_and_future_revoke_connection(self):
        for mutate in (
            lambda r: r.update(interval=15),
            lambda r: r.update(symbol="ETH/USD"),
            lambda r: r.update(volume="-1"),
            lambda r: r.update(interval_begin=stamp(self.now + 90)),
            lambda r: r.update(trades=1),
            lambda r: r.update(high="0"),
        ):
            self.feed.invalidate()
            rows = self.seed()
            held = self.feed.book(BTC)
            message = candle_message([rows[-1]], kind="update")
            mutate(message["data"][0])
            with self.assertRaises(SafetyError):
                self.feed.accept(message)
            with self.assertRaises(SafetyError):
                held.fresh(30)

    async def test_reconnect_during_bootstrap_never_installs_old_history(self):
        rows = self.seed()
        old = self.feed.series

        async def response(*_):
            self.feed.invalidate()
            self.feed.accept(book_message())
            return {BTC.id: rows}

        self.client.request.side_effect = response
        self.assertEqual(await self.client.candles(BTC, 1), rows[:-1])
        self.assertIsNot(self.feed.series, old)
        self.assertEqual(self.feed.series.seed, {})

    async def test_rest_bootstrap_cannot_overwrite_newer_streamed_closed_bar(self):
        rows = self.seed()
        latest = list(rows[-1])
        latest[6], latest[7] = "20", 40

        async def response(*_):
            self.feed.accept(candle_message([latest], kind="update"))
            self.feed.accept(candle_message([[latest[0] + 60, *latest[1:]]], kind="update"))
            return {BTC.id: rows}

        self.client.request.side_effect = response
        with patch("kairos.market_data.time.time", return_value=self.now + 60):
            result = await self.client.candles(BTC, 1)
        self.assertEqual(result[-1], latest)

    async def test_chart_and_other_interval_reads_cannot_change_execution_scope(self):
        rows = self.seed()
        before = self.feed.generation
        await self.client.ohlc(ETH, 15)
        await self.client.candles(BTC, 15)
        self.assertEqual(self.feed.generation, before)
        self.assertEqual(self.feed.candle_spec, (BTC, 1, 31))
        self.assertEqual(self.feed.pairs, {"BTC/USD": BTC})
        self.assertEqual(self.feed.series.seed, {})
        self.assertEqual(self.feed.series.rows[rows[-1][0]], rows[-1])
        self.assertEqual(ws_symbol(replace(BTC, symbol="XDG/USD")), "DOGE/USD")

    async def test_configuration_reuses_same_scope_and_cancels_old_tasks(self):
        await self.feed.close()

        async def idle():
            await asyncio.sleep(100)

        self.feed.run = idle
        await self.feed.configure([BTC], (BTC, 1, 31))
        first = self.feed.task
        await self.feed.configure([BTC], (BTC, 1, 31))
        self.assertIs(self.feed.task, first)
        await self.feed.configure([ETH])
        self.assertTrue(first.cancelled())
        self.assertEqual(self.feed.pairs, {"ETH/USD": ETH})
        self.assertIsNone(self.feed.series)
        second = self.feed.task
        await self.feed.configure([])
        self.assertTrue(second.cancelled())
        self.assertEqual(self.feed.snapshot()["status"], "inactive")

    async def test_operator_freshness_limit_uses_rest_instead_of_older_stream_book(self):
        self.feed.book_age = 2
        self.feed.accept(book_message(at=self.now - 3))
        self.client.request.return_value = {BTC.id: {"bids": [["99", "1"]], "asks": [["101", "1"]]}}
        book = await self.client.book(BTC)
        self.assertEqual(book.received, self.now)
        self.client.request.assert_awaited_once()
        self.assertEqual(self.feed.snapshot()["books_ready"], 0)

    async def test_empty_data_and_failed_subscriptions_invalidate_all_streams(self):
        for message in (
            {"channel": "book", "type": "snapshot", "data": []},
            {"channel": "ohlc", "type": "snapshot", "data": []},
            {"success": False, "error": "DO NOT DISPLAY"},
        ):
            self.seed()
            with self.assertRaises(SafetyError) as raised:
                self.feed.accept(message)
            self.assertNotIn("DO NOT DISPLAY", str(raised.exception))
            self.assertIsNone(self.feed.book(BTC))

    async def test_supplied_zero_volume_candles_are_valid_but_not_invented(self):
        rows = self.seed()
        quiet = list(rows[-2])
        quiet[5:8] = ["0", "0", 0]
        rows[-2] = quiet
        self.feed.series.rows = {}
        self.assertEqual((await self.client.candles(BTC, 1))[-1], quiet)

    async def test_disconnect_during_live_prechecks_blocks_submission(self):
        self.seed()
        held = self.feed.book(BTC)
        store = Store(":memory:")
        self.addCleanup(store.close)
        spot, jev = fake_kraken(), fake_jev()
        engine = Engine(store, spot, jev, lambda *_: None)
        await engine.initialize()
        spot.allow_live = True
        engine.mode = "trading"
        engine.reset_ledger("trading", "1000")
        engine.running = True

        async def disconnect():
            self.feed.invalidate()
            return {BTC.quote: dec(1000)}

        spot.balances.side_effect = disconnect
        with self.assertRaisesRegex(SafetyError, "stream interrupted"):
            await engine.place(BTC, "buy", dec("0.01"), dec(101), held)
        self.assertEqual(store.orders(), [])
        spot.add.assert_not_awaited()
        jev.decide.assert_not_awaited()

    async def test_real_stream_adapter_drives_paper_scalp_entry_and_protective_exit(self):
        rows = candles()
        for row in rows:
            row[5] = row[4]
        rows.append([rows[-1][0] + 60, *rows[-1][1:]])
        self.client.request.return_value = {BTC.id: rows}
        self.feed.accept(candle_message(rows[-10:]))
        self.feed.accept(
            book_message(bids=[[dec("98.4"), dec(100)]], asks=[[dec("98.5"), dec(100)]])
        )
        store = Store(":memory:")
        self.addCleanup(store.close)
        spot, jev = fake_kraken(), fake_jev()
        spot.market_data = self.feed
        spot.book, spot.candles = self.client.book, self.client.candles
        spot.marks.side_effect = lambda pairs: {p.id: dec("98.4") for p in pairs}
        engine = Engine(store, spot, jev, lambda *_: None)
        await engine.initialize()
        await engine.configure(
            {**engine.settings, "strategy": "scalp", "order_size": "200", "max_exposure": "500"}
        )
        await engine.start()
        await engine.tick()
        self.assertTrue(engine.running, engine.last_error)
        self.assertGreater(engine.balance(BTC.base), 0)
        message = book_message(
            kind="update", bids=[[dec(97), dec(100)]], asks=[[dec("97.1"), dec(100)]]
        )
        message["data"][0]["bids"].append({"price": dec("98.4"), "qty": 0})
        message["data"][0]["asks"].append({"price": dec("98.5"), "qty": 0})
        self.feed.accept(message)
        spot.marks.side_effect = lambda pairs: {p.id: dec(97) for p in pairs}
        await engine.tick()
        self.assertTrue(engine.running, engine.last_error)
        self.assertEqual(engine.balance(BTC.base), 0)
        self.assertEqual([o["side"] for o in store.orders()], ["buy", "sell"])
        self.assertEqual(self.client.request.await_count, 1)
        self.assertEqual(self.client.request.call_args.args[0], "OHLC")
        spot.add.assert_not_awaited()
        spot.cancel.assert_not_awaited()
        jev.decide.assert_not_awaited()

    async def test_application_feed_scope_uses_saved_execution_markets_and_intervals(self):
        market_data = SimpleNamespace(configure=AsyncMock())
        engine = SimpleNamespace(
            settings=dict(DEFAULTS),
            kraken=SimpleNamespace(market_data=market_data),
            fee_scope=[BTC, ETH],
            resolve=Mock(return_value=BTC),
        )
        app = {
            "engine": engine,
            "session": Mock(),
            "hub": SimpleNamespace(publish=Mock()),
            "feed_restart": asyncio.Event(),
        }

        async def display(*_):
            await asyncio.sleep(100)

        async def wait_calls(count):
            for _ in range(100):
                if market_data.configure.await_count >= count:
                    return
                await asyncio.sleep(0)
            self.fail("Feed configuration did not update")

        with patch("kairos.web.ticker_feed", side_effect=display):
            task = asyncio.create_task(feeds(app))
            try:
                await wait_calls(1)
                market_data.configure.assert_awaited_with(
                    [BTC, ETH], (BTC, engine.settings["candle_minutes"], 30), max_age=10
                )
                engine.settings = {
                    **engine.settings,
                    "strategy": "scalp",
                    "scalp_window": 120,
                    "stale_seconds": 2,
                }
                app["feed_restart"].set()
                await wait_calls(2)
                market_data.configure.assert_awaited_with([BTC, ETH], (BTC, 1, 121), max_age=2)
                engine.settings = {**engine.settings, "product": "futures"}
                app["feed_restart"].set()
                await wait_calls(3)
                market_data.configure.assert_awaited_with([], None, max_age=2)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_transport_resubscribes_public_channels_and_revokes_each_connection(self):
        socket = AsyncMock()
        socket.__aenter__.return_value = socket
        self.client.session.ws_connect.return_value = socket
        messages = [book_message(), {"channel": "heartbeat"}]
        socket.__aiter__.return_value = [
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(m, default=str))
            for m in messages
        ]

        issued, waits = [], []
        accept = self.feed.accept

        def observed(message):
            accept(message)
            if message.get("channel") == "book":
                issued.append(self.feed.book(BTC))

        self.feed.accept = observed

        async def stop(delay):
            waits.append(delay)
            self.assertIsNone(self.feed.book(BTC))
            self.assertIsNotNone(self.feed.error)
            for book in issued:
                with self.assertRaises(SafetyError):
                    book.fresh(30)
            if len(waits) == 2:
                raise asyncio.CancelledError

        with patch("kairos.market_data.asyncio.sleep", side_effect=stop):
            with self.assertRaises(asyncio.CancelledError):
                await self.feed.run()
        self.assertEqual(self.client.session.ws_connect.call_args.args[0], "wss://ws.kraken.com/v2")
        subscriptions = [c.args[0]["params"] for c in socket.send_json.call_args_list]
        self.assertEqual([s["channel"] for s in subscriptions], ["book", "ohlc"] * 2)
        self.assertEqual(waits, [5, 10])
        self.assertNotEqual(issued[0].generation, issued[1].generation)
        self.assertEqual(subscriptions[0]["depth"], 100)
        self.assertTrue(all("token" not in s for s in subscriptions))
        self.client.request.assert_not_awaited()
        self.assertEqual(socket.__aexit__.await_count, 2)
