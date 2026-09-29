import time
import unittest
from unittest.mock import AsyncMock, patch

from kairos import scalping
from kairos.domain import SafetyError, dec
from kairos.engine import Engine
from kairos.settings import DEFAULTS, validate_settings
from kairos.store import Store
from tests.helpers import BTC, book, fake_jev, fake_kraken
from tests.test_futures import PAIR, fake_futures


def candles(short=False, now=None):
    cutoff = int(now or time.time()) // 60 * 60
    closes = [dec("100.4") if i % 2 else dec("99.6") for i in range(29)] + [dec(96), dec("98.5")]
    if short:
        closes = [200 - p for p in closes]
    return [
        [
            cutoff - (31 - i) * 60,
            str(p),
            str(p + dec(".1")),
            str(p - dec(".1")),
            str(p),
            "0",
            "10",
            1,
        ]
        for i, p in enumerate(closes)
    ]


class BandTests(unittest.TestCase):
    def test_local_window_ignores_old_history_and_confirms_reentry(self):
        rows = candles()
        result = scalping.signal(rows, DEFAULTS)
        self.assertEqual(result["signal"], "buy")
        self.assertTrue(result["range_eligible"])
        self.assertEqual(result, scalping.signal([[0, "bad"]] * 500 + rows, DEFAULTS))
        self.assertEqual(scalping.signal(candles(True), DEFAULTS)["signal"], "sell")

    def test_monotonic_and_flat_markets_are_not_ranges(self):
        for values in ([dec(100)] * 31, [dec(100) + i for i in range(31)]):
            rows = candles()
            for row, value in zip(rows, values, strict=True):
                row[1:5] = [str(value)] * 4
            self.assertFalse(scalping.signal(rows, DEFAULTS)["range_eligible"])

    def test_incomplete_stale_forming_and_malformed_candles_fail_closed(self):
        for kind in ("short", "gap", "forming", "ohlc", "nan"):
            rows = candles()
            if kind == "short":
                rows.pop(0)
            elif kind == "gap":
                rows[-2][0] -= 60
            elif kind == "forming":
                rows[-1][0] += 60
            elif kind == "ohlc":
                rows[-1][2] = "1"
            else:
                rows[-1][4] = "NaN"
            with self.subTest(kind=kind), self.assertRaises(SafetyError):
                scalping.signal(rows, DEFAULTS)
        with self.assertRaises(SafetyError):
            scalping.signal(candles(now=3600), DEFAULTS, now=3660)

    def test_cost_filter_uses_distinct_notionals_and_worst_tick_rounding(self):
        snapshot = book(BTC, "99.99", "100.01")
        settings = {**DEFAULTS, "slippage_bps": "0", "scalp_margin_bps": "1"}
        room, costs = scalping.cost_room(snapshot, "buy", dec("120.2"), settings, dec(1000))
        self.assertGreater(room, dec(2000))  # Naive 2 × 10% fee would incorrectly pass.
        self.assertLess(room, costs)
        room, costs = scalping.cost_room(snapshot, "sell", dec("79.8"), settings, dec(1000))
        self.assertGreater(room, costs)  # Exit fee is charged on the lower closing notional.

    def test_spread_and_slippage_compound_before_adverse_tick_rounding(self):
        settings = {**DEFAULTS, "slippage_bps": "100", "scalp_margin_bps": "1"}
        for side in ("buy", "sell"):
            room, costs = scalping.cost_room(
                book(BTC, "95", "105"), side, dec(100), settings, dec(0)
            )
            self.assertEqual(room, 0)
            self.assertEqual(costs, dec(1211))

    def test_settings_block_margin_slow_checks_and_recovery(self):
        for update in ({"product": "margin"}, {"interval_seconds": 60}, {"recover_initial": True}):
            with self.subTest(update=update), self.assertRaises(SafetyError):
                validate_settings({**DEFAULTS, "strategy": "scalp", **update})


class ScalpingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Keep funding tests within one hour and away from UTC day boundaries.
        self.clock = patch("time.time", return_value=int(time.time()) // 3600 * 3600 + 600).start()
        self.addCleanup(patch.stopall)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.spot, self.future, self.jev = fake_kraken(), fake_futures(), fake_jev()
        self.jev.key = ""
        self.bid, self.ask, self.depth = "98.4", "98.5", "100"
        self.spot.book = AsyncMock(side_effect=lambda p: book(p, self.bid, self.ask, self.depth))
        self.future.book = AsyncMock(side_effect=lambda p: book(p, self.bid, self.ask, self.depth))
        self.spot.marks = AsyncMock(side_effect=lambda pairs: {p.id: dec(self.bid) for p in pairs})
        self.future.market = AsyncMock(
            side_effect=lambda p: {"markPrice": self.bid, "fundingRate": "0", "postOnly": False}
        )
        self.spot.candles = AsyncMock(side_effect=lambda *args: candles())
        self.future.completed_candles = AsyncMock(side_effect=lambda *args: candles())
        self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None, futures=self.future)
        await self.engine.initialize()
        await self.engine.configure(
            {
                **self.engine.settings,
                "strategy": "scalp",
                "order_size": "200",
                "max_exposure": "500",
            }
        )

    async def enter(self, futures=False, short=False):
        if futures:
            await self.engine.configure(
                {**self.engine.settings, "product": "futures", "pair": PAIR.id}
            )
        if short:
            self.bid, self.ask = "101.5", "101.6"
            self.future.completed_candles.side_effect = lambda *args: candles(True)
            self.spot.candles.side_effect = lambda *args: candles(True)
        await self.engine.start()
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        return self.snapshot()

    def snapshot(self):
        return scalping.snapshot(self.engine)

    async def test_spot_entry_target_exit_no_reentry_and_no_model(self):
        await self.enter()
        position = self.snapshot()["position"]
        self.assertIsNotNone(position)
        self.assertGreater(self.engine.balance(BTC.base), 0)
        self.assertEqual(self.store.orders()[0]["scalp_id"], position["id"])
        await self.engine.tick()
        self.assertEqual(len(self.store.orders()), 1)
        self.bid, self.ask = "100", "100.1"
        await self.engine.tick()
        # A profitable exit can exceed the per-order cap and need two IOCs.
        await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertIsNone(self.snapshot()["position"])
        await self.engine.tick()
        self.assertEqual(len(self.store.orders()), 3)
        self.jev.decide.assert_not_awaited()
        self.spot.add.assert_not_awaited()

    async def test_archived_entry_retains_ownership_and_protective_exit(self):
        await self.enter()
        entry = self.store.orders()[0]
        await self.engine.paper_order_history(entry["id"], "archive")
        self.assertEqual(self.engine.snapshot()["orders"], [])
        self.assertEqual(self.engine.snapshot()["archived_orders"][0]["id"], entry["id"])
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertGreater(self.engine.balance(BTC.base), 0)
        self.bid, self.ask = "97", "97.1"
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertIsNone(self.snapshot()["position"])
        self.assertEqual(len(self.store.orders()), 2)
        self.spot.add.assert_not_awaited()
        self.jev.decide.assert_not_awaited()

    async def test_near_expiry_fees_refresh_before_slow_entry_planning(self):
        await self.engine.start()
        self.clock.return_value += 59
        self.spot.fees.return_value = ({BTC.id: dec(10)}, {BTC.id: dec(20)})

        async def delayed_candles(*args):
            self.clock.return_value += 2
            return candles()

        self.spot.candles.side_effect = delayed_candles
        before = self.spot.fees.await_count
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(self.spot.fees.await_count, before + 1)
        self.assertGreater(self.engine.balance(BTC.base), 0)
        self.assertEqual(self.store.orders()[0]["fee_bps"], "20")
        self.assertEqual(self.snapshot()["position"]["signal"]["entry_fee_bps"], "20")
        self.spot.add.assert_not_awaited()
        self.jev.decide.assert_not_awaited()

    async def test_stop_exits_even_when_fees_make_exit_unprofitable(self):
        await self.enter()
        self.bid, self.ask = "97", "97.1"
        await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertIn("Stop reached", self.engine.latest_decision["reason"])
        self.assertLess(dec(self.engine.equity), 1000)

    async def test_partial_exit_is_latched_and_does_not_reverse_or_reenter(self):
        await self.enter(futures=True)
        self.bid, self.ask, self.depth = "97", "97.1", ".5"
        await self.engine.tick()
        self.assertGreater(self.engine.futures.position(PAIR), 0)
        self.assertEqual(self.snapshot()["position"]["exit_reason"], "Stop reached")
        self.bid, self.ask, self.depth = "98.4", "98.5", "100"
        await self.engine.tick()
        self.assertEqual(self.engine.futures.position(PAIR), 0)
        self.assertTrue(all(o["reduce_only"] for o in self.store.orders()[1:]))
        self.future.request.assert_not_awaited()

    async def test_futures_short_is_reduced_at_target(self):
        await self.enter(futures=True, short=True)
        self.assertLess(self.engine.futures.position(PAIR), 0)
        self.bid, self.ask = "99.9", "100"
        await self.engine.tick()
        self.assertEqual(self.engine.futures.position(PAIR), 0)
        self.assertEqual(self.store.orders()[1]["side"], "buy")
        self.assertTrue(self.store.orders()[1]["reduce_only"])

    async def test_spot_never_shorts_and_costs_veto_entry(self):
        await self.enter(short=True)
        self.assertEqual(self.store.orders(), [])
        await self.engine.stop()
        self.bid, self.ask = "98.4", "98.5"
        self.spot.candles.side_effect = lambda *args: candles()
        self.clock.return_value += 60
        self.spot.fees.return_value = ({BTC.id: dec(40)}, {BTC.id: dec(80)})
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.store.orders(), [])
        self.assertFalse(self.engine.latest_decision["state"]["cost_eligible"])

    async def test_restart_keeps_protection_and_deadline_exit_needs_no_candles(self):
        await self.enter()
        original = self.snapshot()["position"]
        await self.engine.stop()
        self.clock.return_value += 360
        self.spot.candles.side_effect = SafetyError("candle outage")
        self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None, futures=self.future)
        await self.engine.initialize()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.snapshot()["position"], original)
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertIn("Holding deadline", self.engine.latest_decision["reason"])

    async def test_loss_limit_cannot_prevent_tracked_reduction(self):
        await self.enter()
        self.engine.settings["daily_loss"] = ".01"
        await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertIn("Risk limit", self.engine.latest_decision["reason"])
        await self.engine.tick()
        self.assertFalse(self.engine.running)

    async def test_futures_loss_limit_still_allows_paper_reduce_only_exit(self):
        await self.enter(futures=True)
        self.engine.settings["daily_loss"] = ".01"
        await self.engine.tick()
        self.assertEqual(self.engine.futures.position(PAIR), 0)
        self.assertTrue(self.store.orders()[-1]["reduce_only"])
        self.assertIn("Risk limit", self.engine.latest_decision["reason"])

    async def test_no_fill_never_repeats_a_claimed_candle(self):
        self.bid, self.ask = "98.3", "98.6"
        self.spot.fees.return_value = ({BTC.id: dec(0)}, {BTC.id: dec(0)})
        await self.engine.configure(
            {**self.engine.settings, "max_spread_bps": "50", "scalp_cooldown_seconds": 0}
        )
        await self.engine.start()
        with patch("kairos.domain.Book.fill", return_value=(dec(0), dec(0))):
            await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(len(self.store.orders()), 1)
        self.assertEqual(dec(self.store.orders()[0]["filled"]), 0)
        await self.engine.tick()
        self.assertEqual(len(self.store.orders()), 1)
        self.assertIsNone(self.snapshot()["position"])

    async def test_stop_during_candle_read_prevents_entry(self):
        async def stop_during_read(*args):
            self.engine.running = False
            self.engine.stop_generation += 1
            return candles()

        self.spot.candles.side_effect = stop_during_read
        await self.engine.start()
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.store.orders(), [])
        self.spot.add.assert_not_awaited()

    async def test_failure_after_fill_preserves_protection_for_restart(self):
        place = self.engine.place

        async def crash_after_fill(*args, **kwargs):
            await place(*args, **kwargs)
            raise SafetyError("simulated crash after fill")

        self.engine.place = crash_after_fill
        await self.engine.start()
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertGreater(self.engine.balance(BTC.base), 0)
        protection = self.snapshot()["position"]
        self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None, futures=self.future)
        await self.engine.initialize()
        self.assertEqual(self.snapshot()["position"], protection)
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(len(self.store.orders()), 1)

    async def test_malformed_depth_cannot_create_a_simulated_fill(self):
        self.depth = "-1"
        await self.engine.start()
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertIn("order-book depth", self.engine.last_error)
        self.assertEqual(self.store.orders(), [])

    async def test_live_paths_refuse_scalping_even_with_both_gates_enabled(self):
        self.spot.allow_live = True
        with self.assertRaisesRegex(SafetyError, "paper-only"):
            await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        self.engine.mode = "trading"
        self.engine.running = True
        with self.assertRaisesRegex(SafetyError, "paper-only"):
            await self.engine.place(BTC, "buy", dec(1), dec(100), book())
        with self.assertRaisesRegex(SafetyError, "paper-only"):
            await self.engine.futures.place(PAIR, "buy", dec(1), dec(100), book(PAIR))
        self.spot.add.assert_not_awaited()
        self.future.request.assert_not_awaited()

    async def test_position_blocks_strategy_change_and_paper_reset_clears_only_paper(self):
        await self.enter()
        await self.engine.stop()
        with self.assertRaisesRegex(SafetyError, "scalp position"):
            await self.engine.configure({**self.engine.settings, "strategy": "htf"})
        self.store.put("ledger:trading", {"balances": {"ZUSD": "333"}, "initial": "333"})
        await self.engine.reset_paper()
        self.assertIsNone(self.snapshot()["position"])
        self.assertEqual(self.store.get("ledger:trading")["balances"]["ZUSD"], "333")

    async def test_unrelated_holdings_are_not_adopted_and_exit_flag_cannot_open(self):
        ledger = self.engine.ledger()
        ledger["balances"][BTC.base] = "1"
        self.store.put("ledger:dry-run", ledger)
        with self.assertRaisesRegex(SafetyError, "adopt"):
            await self.engine.start()
        self.engine.running = True
        with self.assertRaises(SafetyError):
            await self.engine.place(BTC, "buy", dec(1), dec(100), book(), exit_only=True)
        self.assertEqual(self.store.orders(), [])
