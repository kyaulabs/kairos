"""Native HTF windows: data compatibility, not strategy profitability evidence."""

import asyncio
import copy
import time
import unittest
from unittest.mock import AsyncMock, patch

from kairos import htf
from kairos.alpaca import Alpaca, iso
from kairos.alpaca_engine import AlpacaEngine
from kairos.domain import Book, SafetyError, dec
from kairos.htf_review import NATIVE_POLICY, native_rows
from kairos.store import Store
from tests.helpers import BTC, fake_jev
from tests.test_alpaca import PaperBroker


def bars(end, closes=None):
    closes = closes or [dec(90) + dec(".4") * i for i in range(27)] + [
        dec(103),
        dec(104),
        dec("97.5"),
    ]
    return [
        [end - (30 - i) * 3600, str(p), str(p + 3), str(p - 3), str(p), str(p), "10", 1]
        for i, p in enumerate(closes)
    ]


class NativeHistoryTests(unittest.TestCase):
    def test_requires_thirty_adjacent_closed_bars_without_inventing_minutes(self):
        end = 180000
        rows = bars(end)
        rows[0][5:8] = ["0", "0", 0]  # A real provider zero is retained.
        validated, count, needed = native_rows(BTC, rows, end, 60)
        self.assertEqual((validated, count, needed), (rows, 30, 30))
        self.assertEqual(native_rows(BTC, rows[:-1], end, 60), (None, 0, 30))
        self.assertEqual(native_rows(BTC, rows[:10] + rows[11:], end, 60), (None, 19, 30))
        for invalid in (rows + [bars(end + 3600)[-1]], rows[::-1], rows + [rows[-1]]):
            with self.assertRaises(SafetyError):
                native_rows(BTC, invalid, end, 60)
        for index, value in ((0, rows[-1][0] + 60), (2, "1"), (6, "-1"), (7, "1.5")):
            invalid = copy.deepcopy(rows)
            invalid[-1][index] = value
            with self.assertRaises(SafetyError):
                native_rows(BTC, invalid, end, 60)


class AlpacaNativeHTFTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = int(time.time()) // 3600 * 3600 + 5
        self.wall = patch("time.time", side_effect=lambda: self.now)
        self.wall.start()
        self.store = Store(":memory:", clock=lambda: self.now)
        self.broker, self.jev = PaperBroker(), fake_jev()
        self.engine = AlpacaEngine(
            self.store, self.broker.client, self.jev, lambda *_: None, clock=lambda: self.now
        )
        await self.engine.initialize()
        await self.engine.configure(
            {
                **self.engine.settings,
                "strategy": "htf",
                "candle_minutes": 60,
                "order_size": "25",
                "interval_seconds": 15,
            }
        )
        self.pair = self.engine.resolve(self.engine.settings["pair"])
        self.end = int(self.now) // 3600 * 3600
        self.rows = bars(self.end)
        self.broker.client.bars = AsyncMock(
            side_effect=lambda *_args, **_kwargs: copy.deepcopy(self.rows)
        )
        self.bid = dec("100")
        self.broker.client.book = AsyncMock(
            side_effect=lambda _: Book(
                self.pair, [[self.bid, dec(100)]], [[self.bid + dec(".1"), dec(100)]], self.now
            )
        )
        # An old minute archive must neither block native bars nor be repurposed/pruned.
        self.store.db.execute(
            "INSERT INTO htf_minutes VALUES (?, ?, ?)",
            (self.pair.id, 60, '[60,"1","1","1","1","1","0",0]'),
        )
        self.store.db.commit()
        self.minute_archive = list(self.store.db.execute("SELECT * FROM htf_minutes"))
        await self.engine.start(confirmation="START ALPACA PAPER")

    async def asyncTearDown(self):
        await self.engine.close()
        self.store.close()
        self.wall.stop()

    async def cycle(self):
        await self.engine.tick()
        task = self.engine.htf_review.task
        if task:
            await task
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)

    async def next_hour(self):
        self.now = self.end + 3600 + 5
        self.end += 3600
        self.rows = self.rows[1:] + [
            [self.end - 3600, "99.95", "102.95", "96.95", "99.95", "99.95", "10", 1]
        ]
        await self.cycle()

    async def test_native_transport_requests_only_completed_us_hourly_bars(self):
        wire = [
            dict(zip(("t", "o", "h", "l", "c", "vw", "v", "n"), [iso(r[0]), *r[1:]], strict=True))
            for r in self.rows
        ]
        self.broker.client.request = AsyncMock(return_value={"bars": {"BTC/USD": wire}})
        result = await Alpaca.bars(self.broker.client, self.pair, 60, count=30)
        self.assertEqual(result, self.rows)
        call = self.broker.client.request.call_args
        self.assertEqual(call.args, ("GET", "/v1beta3/crypto/us/bars"))
        self.assertTrue(call.kwargs["data"])
        self.assertEqual(call.kwargs["params"]["timeframe"], "1Hour")
        self.assertEqual(call.kwargs["params"]["start"], iso(self.end - 30 * 3600))
        self.assertEqual(call.kwargs["params"]["end"], iso(self.end - 1))
        wire[-1]["t"] = iso(self.end - 3600 + 0.5)
        with self.assertRaisesRegex(SafetyError, "Unaligned"):
            await Alpaca.bars(self.broker.client, self.pair, 60, count=30)

    async def test_native_ready_mid_hour_with_old_minute_gaps_and_provenance(self):
        await self.cycle()
        self.broker.client.bars.assert_awaited_once_with(self.pair, 60, count=30)
        report = self.engine.snapshot()["review"]["data"]
        self.assertEqual(
            (report["consecutive_native_bars"], report["required_native_bars"]), (30, 30)
        )
        self.assertIsNone(report["required_minutes"])
        self.assertEqual(self.engine.active_run()["history_policy"], NATIVE_POLICY)
        observation = next(
            e["data"] for e in self.store.history() if e["kind"] == "htf-observation"
        )
        self.assertEqual(observation["native_history"], self.rows)
        self.assertEqual(observation["history_revision"], report["history_revision"])
        self.now += 1200
        await self.cycle()
        self.assertEqual(self.broker.client.bars.await_count, 1)
        self.assertEqual(
            list(self.store.db.execute("SELECT * FROM htf_minutes")), self.minute_archive
        )
        self.assertFalse(self.engine.orders())
        self.jev.decide.assert_not_awaited()
        self.engine.settings["candle_minutes"] = 30
        self.assertFalse(self.engine.snapshot()["review"]["data"]["window_current"])

    async def test_new_hour_setup_can_enter_and_hard_stop_needs_no_bars_or_model(self):
        await self.cycle()  # Establish a HOLD baseline; never buy on Start.
        self.jev.decide.return_value["action"] = "buy"
        await self.next_hour()
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertGreater(self.engine.balance("BTC"), 0)
        self.assertEqual(self.jev.decide.call_args.args[0]["history_policy"], NATIVE_POLICY)
        self.assertIn("native_window", self.jev.decide.call_args.args[0])
        self.assertNotIn("rolling_window", self.jev.decide.call_args.args[0])
        position = copy.deepcopy(htf.snapshot(self.engine)["position"])
        self.assertTrue(position)
        self.broker.client.bars.reset_mock()
        self.broker.client.bars.side_effect = SafetyError("Historical service unavailable")
        self.jev.decide.reset_mock()
        self.bid = dec("95")
        self.now += 16
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(len(self.engine.orders()), 2)
        self.assertIn("stop", self.engine.orders()[-1]["reason"].lower())
        self.broker.client.bars.assert_not_awaited()
        self.jev.decide.assert_not_awaited()
        self.assertEqual(
            list(self.store.db.execute("SELECT * FROM htf_minutes")), self.minute_archive
        )

    async def test_intrabar_quote_changes_cannot_create_a_new_setup(self):
        await self.cycle()
        state = htf.snapshot(self.engine)
        state.update(entry_signal="hold", pending_signal=None, last_candle=f"60:{self.end}")
        self.store.put(htf.key(self.engine), state)
        row = {
            "entry_eligible": True,
            "short_entry_eligible": False,
            "entry_execution": "passive-limit",
            "candle_close_time": self.end,
        }
        book = await self.broker.client.book(self.pair)
        context = htf.entry_context(self.engine, self.pair, row, book)
        self.assertFalse(context["entry_ready"])
        self.assertIn(
            "native HTF setup transitions require a new completed bar", context["entry_blockers"]
        )
        row["candle_close_time"] += 3600
        self.assertTrue(htf.entry_context(self.engine, self.pair, row, book)["entry_ready"])

    async def test_missing_newest_bar_retries_but_never_reuses_old_window(self):
        await self.cycle()
        self.now = self.end + 3600 + 5
        await self.cycle()  # Provider has not published the newly completed hour.
        self.assertIsNone(self.engine.htf_review.rows)
        self.assertIn("0/30", self.engine.htf_review.status)
        self.assertFalse(self.engine.orders())
        self.jev.decide.assert_not_awaited()
        self.end += 3600
        self.rows = bars(self.end)  # Delayed complete provider response, no fabricated bar.
        self.now += 31
        await self.cycle()
        self.assertEqual(self.engine.htf_review.available, 30)
        self.assertIsNotNone(self.engine.htf_review.rows)

    async def test_hour_boundary_invalidates_in_flight_read_and_model_answer(self):
        async def late(*_args, **_kwargs):
            self.now = self.end + 3600 + 1
            return copy.deepcopy(self.rows)

        self.broker.client.bars.side_effect = late
        await self.cycle()
        self.assertFalse(self.engine.orders())
        self.jev.decide.assert_not_awaited()
        self.assertIn("window changed", self.engine.htf_review.status)
        self.engine.htf_review.answer = (
            self.engine.htf_review.signature(),
            self.end,
            self.now,
            {"action": "buy", "confidence": 0.9},
        )
        self.assertIsNone(self.engine.htf_review.take())
        self.now = self.end + 120
        self.engine.htf_review.answer = (
            self.engine.htf_review.signature(),
            self.end,
            self.now,
            {"action": "buy", "confidence": 0.9},
        )
        answer = self.engine.htf_review.take()
        self.assertEqual(answer["expires_at"], self.now + 30)  # Never an hour-long approval.

    async def test_stop_during_native_history_read_discards_result(self):
        started = asyncio.Event()

        async def stopped(*_args, **_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return copy.deepcopy(self.rows)  # A transport that ignores cancellation.

        self.broker.client.bars.side_effect = stopped
        await self.engine.tick()
        task = self.engine.htf_review.task
        await started.wait()
        await self.engine.stop()
        await task
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.engine.htf_review.rows)
        self.assertFalse(self.engine.orders())
        self.jev.decide.assert_not_awaited()
