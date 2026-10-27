import time
import unittest
from unittest.mock import AsyncMock, patch

from kairos import meta_strategy as meta
from kairos.domain import SafetyError, dec
from kairos.engine import Engine
from kairos.meta_signals import MetaSignals
from kairos.research_signals import Setup
from kairos.settings import DEFAULTS, validate_settings
from kairos.store import Store
from tests.helpers import BTC, book, fake_jev, fake_kraken
from tests.test_meta_signals import rows


class MetaExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = patch("time.time", return_value=3000 * 900 + 3).start()
        self.addCleanup(patch.stopall)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.client, self.jev = fake_kraken(), fake_jev()
        self.jev.key = ""
        self.bid, self.ask, self.depth = "9999", "10000", "10"
        self.client.book = AsyncMock(side_effect=lambda p: book(p, self.bid, self.ask, self.depth))
        self.client.marks = AsyncMock(side_effect=lambda ps: {p.id: dec(self.bid) for p in ps})
        self.client.candles = AsyncMock(side_effect=self.candles)
        self.engine = Engine(self.store, self.client, self.jev, lambda *_: None)
        self.addAsyncCleanup(self.engine.htf_review.close)
        await self.engine.initialize()
        await self.engine.configure({**self.engine.settings, "strategy": "vwap-meta"})
        original = MetaSignals.push

        def push(signals, row, now):
            original(signals, row, now)
            return Setup(
                signals.policy,
                row[0] + 900,
                dec(10000),
                dec(9900),
                dec(10300),
                {
                    "selected_strategy": "vwap-pullback",
                    "candidate_id": str(row[0]),
                    "armed_at": row[0],
                    "underlying_candidate_id": str(row[0]),
                },
            )

        self.push = patch.object(MetaSignals, "push", push).start()

    def candles(self, *_):
        cutoff = int(time.time()) // 900 * 900
        return [[cutoff - 300 * 900 + r[0], *r[1:]] for r in rows(300)]

    async def enter(self):
        await self.engine.start()
        await self.engine.tick()  # Bootstrap/baseline cannot enter.
        self.assertEqual(self.engine.orders(), [])
        self.clock.return_value += 900
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(len(self.engine.orders()), 1)
        return meta.snapshot(self.engine)["position"]

    async def test_entry_once_shared_risk_identity_and_no_model_or_native_write(self):
        position = await self.enter()
        order = self.engine.orders()[0]
        self.assertEqual(order["strategy"], "vwap-meta")
        self.assertEqual(order["meta_id"], position["id"])
        self.assertEqual(order["protection"]["deadline"], position["deadline"])
        self.assertEqual(position["deadline"], time.time() + meta.MAX_HOLD)
        self.assertLessEqual(dec(order["risk_plan"]["requested_stop_risk"]), 10)
        self.assertEqual(
            meta.owned(self.engine, meta.snapshot(self.engine)), self.engine.balance(BTC.base)
        )
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        self.jev.decide.assert_not_awaited()
        self.client.add.assert_not_awaited()
        self.assertIsNone(self.engine.latest_decision["confidence"])

    async def test_protection_precedes_failed_history_and_entry_halt(self):
        await self.enter()
        state = meta.snapshot(self.engine)
        state["entries_halted"] = True
        self.store.put(meta.key(self.engine), state)
        self.clock.return_value += 900
        self.bid, self.ask = "9899", "9900"
        self.client.candles.side_effect = SafetyError("fixture missing history")
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        sell = self.engine.orders()[-1]
        self.assertEqual(sell["side"], "sell")
        self.assertTrue(sell["exit_only"])
        self.assertEqual(meta.snapshot(self.engine)["position"]["exit_reason"], "Stop")
        self.assertTrue(meta.snapshot(self.engine)["history_unavailable"])

    async def test_partial_first_target_remains_latched_after_stop_cross_restart_and_settings(self):
        position = await self.enter()
        deadline = position["deadline"]
        self.depth = "0.001"
        self.bid, self.ask = "10301", "10302"
        await self.engine.tick()
        self.assertEqual(meta.snapshot(self.engine)["position"]["exit_reason"], "Target")
        await self.engine.stop()
        with self.assertRaises(SafetyError):
            await self.engine.configure({**self.engine.settings, "meta_structure_minutes": 360})
        with self.assertRaises(SafetyError):
            await self.engine.configure({**self.engine.settings, "strategy": "fib-meta"})
        await self.engine.start()
        self.bid, self.ask = "9800", "9801"
        await self.engine.tick()
        saved = meta.snapshot(self.engine)["position"]
        self.assertEqual(saved["exit_reason"], "Target")
        self.assertEqual(saved["deadline"], deadline)
        self.assertEqual(len([o for o in self.engine.orders() if o["side"] == "buy"]), 1)

    async def test_deadline_needs_no_candle_or_model_and_does_not_force_fill(self):
        position = await self.enter()
        self.clock.return_value = position["deadline"] + 1
        self.depth = "0.00001"
        self.client.candles.side_effect = SafetyError("unavailable")
        await self.engine.tick()
        self.assertEqual(meta.snapshot(self.engine)["position"]["exit_reason"], "Deadline")
        self.assertGreater(self.engine.balance(BTC.base), 0)
        self.jev.decide.assert_not_awaited()

    async def test_loss_latch_survives_new_UTC_day_and_signals_still_advance(self):
        await self.engine.start()
        await self.engine.tick()
        day = self.store.get("day:dry-run")
        day["equity"] = "1100"
        self.store.put("day:dry-run", day)
        self.clock.return_value += 900
        await self.engine.tick()
        state = meta.snapshot(self.engine)
        self.assertTrue(state["entries_halted"])
        self.assertEqual(self.engine.orders(), [])
        count = meta.view(self.engine)["confirmation_observed_bars"]
        self.clock.return_value += 86400
        await self.engine.tick()
        self.assertTrue(meta.snapshot(self.engine)["entries_halted"])
        self.assertGreater(meta.view(self.engine)["confirmation_observed_bars"], count)
        self.assertEqual(self.engine.orders(), [])

    async def test_no_late_or_restart_catchup_entry(self):
        await self.engine.start()
        await self.engine.tick()
        self.clock.return_value += 958  # 61 seconds after the new confirmation.
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.clock.return_value += 3600
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])

    async def test_unrelated_inventory_live_nonspot_and_deleting_ownership_are_blocked(self):
        await self.enter()
        order = self.engine.orders()[0]
        self.assertIsNone(self.engine.paper_history_restriction(order, "archive"))
        self.assertIsNotNone(self.engine.paper_history_restriction(order, "delete"))
        await self.engine.stop()
        with self.assertRaises(SafetyError):
            await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        self.engine.mode = "trading"
        with self.assertRaises(SafetyError):
            meta.prepare(self.engine)
        self.engine.mode = "dry-run"
        state = meta.snapshot(self.engine)
        self.store.put(meta.key(self.engine), {**state, "epoch": "unrelated"})
        with self.assertRaises(SafetyError):
            meta.prepare(self.engine)

    async def test_final_intent_guard_rejects_a_second_buy_for_consumed_position(self):
        await self.enter()
        original = self.engine.orders()[0]
        count = len(self.engine.orders())
        with self.assertRaisesRegex(SafetyError, "already consumed"):
            await self.engine.place(
                BTC, "buy", dec(original["volume"]), dec(original["price"]), book(BTC)
            )
        self.assertEqual(len(self.engine.orders()), count)
        self.client.add.assert_not_awaited()

    async def test_paper_reset_clears_all_meta_epochs_without_adopting_old_history(self):
        await self.enter()
        await self.engine.stop()
        self.store.put(meta.key(self.engine, pair="old-market"), {"epoch": "old"})
        await self.engine.reset_paper()
        self.assertIsNone(self.store.get(meta.key(self.engine, pair="old-market")))
        self.assertIsNone(meta.snapshot(self.engine)["epoch"])
        await self.engine.start()
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertEqual(meta.owned(self.engine, meta.snapshot(self.engine)), 0)

    async def test_resetting_other_portfolio_does_not_rearm_spot_meta_latch(self):
        await self.engine.start()
        await self.engine.tick()
        await self.engine.stop()
        state_key = meta.key(self.engine)
        state = meta.snapshot(self.engine)
        state["entries_halted"] = True
        self.store.put(state_key, state)
        await self.engine.configure(
            {**self.engine.settings, "strategy": "htf", "product": "margin"}
        )
        await self.engine.reset_paper()
        self.assertEqual(self.store.get(state_key)["epoch"], state["epoch"])
        self.assertTrue(self.store.get(state_key)["entries_halted"])

    async def test_gap_overrun_is_visible_not_clipped_to_planned_budget(self):
        await self.enter()
        self.bid, self.ask, self.depth = "9500", "9501", "0.001"
        await self.engine.tick()
        state = meta.snapshot(self.engine)
        self.assertGreater(
            dec(state["position"]["maximum_observed_loss"]),
            dec(state["position"]["risk_plan"]["capital_budget"]),
        )
        self.assertTrue(state["position"]["risk_overrun_reported"])
        self.assertFalse(state["position"]["risk_plan"]["guaranteed_loss_cap"])

    async def test_risk_budget_reduces_with_equity_and_caps_at_initial(self):
        await self.engine.start()
        setup = Setup("vwap-meta", 0, dec(10000), dec(9900), dec(10300), {})
        for equity, budget in ((500, 5), (1000, 10), (2000, 10)):
            self.engine.equity, self.engine.exposure = str(equity), "0"
            qty, price, plan = meta.risk_plan(self.engine, BTC, book(BTC), setup)
            self.assertEqual(dec(plan["capital_budget"]), budget)
            self.assertLessEqual(
                qty * dec(plan["loss_per_gross_unit"])
                + dec(plan["dust_risk_reserved"])
                + dec(plan["potential_unprotected_dust_cost"]),
                budget,
            )
            self.assertLessEqual(qty * price, 1000)
        with self.assertRaises(SafetyError):
            meta.risk_plan(
                self.engine,
                BTC,
                book(BTC),
                Setup("vwap-meta", 0, dec(10000), dec(9999), dec(10001), {}),
            )

    def test_settings_preserve_defaults_and_block_leverage_recovery_and_slow_cycles(self):
        self.assertEqual(DEFAULTS["strategy"], "htf")
        self.assertEqual(DEFAULTS["meta_structure_minutes"], 60)
        for update in (
            {"product": "margin"},
            {"product": "futures"},
            {"recover_initial": True},
            {"interval_seconds": 60},
        ):
            with self.subTest(update=update), self.assertRaises(SafetyError):
                validate_settings({**DEFAULTS, "strategy": "fib-meta", **update})
