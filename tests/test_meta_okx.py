import time
import unittest
from unittest.mock import AsyncMock, patch

from kairos import meta_strategy as meta
from kairos.domain import SafetyError, dec, floor
from kairos.okx_cycle import authorize, preview
from kairos.research_signals import Setup
from tests import test_okx_strategies as fixtures


class MetaDemoTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.DemoStrategyTests.asyncSetUp
    configure = fixtures.DemoStrategyTests.configure
    book = fixtures.DemoStrategyTests.book
    posts = fixtures.DemoStrategyTests.posts

    async def authorize_meta(self, strategy):
        self.engine.settings.update(strategy=strategy, interval_seconds=15)
        proposal = await preview(
            self.engine,
            {
                "kind": "strategy",
                "pair": self.pair.id,
                "allocation": "500",
                "budget": "25",
                "duration_seconds": 3600,
                "max_orders": 4,
            },
        )
        await authorize(self.engine, proposal["id"], proposal["confirmation"])
        return proposal

    async def test_both_metas_dispatch_only_under_existing_finite_demo_permission(self):
        for strategy in meta.FAMILIES:
            with self.subTest(strategy=strategy):
                await self.authorize_meta(strategy)
                with patch.object(meta, "run", new_callable=AsyncMock) as run:
                    await self.engine.tick()
                    run.assert_awaited_once_with(self.engine)
                self.assertTrue(self.engine.running, self.engine.last_error)
                self.assertEqual(self.posts(), [])
                await self.engine.stop()

    async def test_native_meta_scheduler_places_one_bound_entry_without_Jev(self):
        now = int(time.time()) // 900 * 900 + 903
        with patch("time.time", return_value=now):
            await self.authorize_meta("fib-meta")
            state = meta.snapshot(self.engine)
            state["baseline_after"] = now - 903
            self.store.put(meta.key(self.engine), state)
            setup = Setup(
                "fib-meta",
                now - 3,
                dec(50000),
                dec(49000),
                dec(54000),
                {"selected_strategy": "fib-ema", "candidate_id": "fixture-new-bar"},
            )
            with patch.object(meta, "history", AsyncMock(return_value=setup)):
                await self.engine.tick()
            self.assertTrue(self.engine.running, self.engine.last_error)
            self.assertEqual(len(self.engine.orders()), 1)
            self.assertEqual(self.engine.orders()[0]["strategy"], "fib-meta")
            self.assertEqual(
                self.engine.orders()[0]["signal_facts"]["selected_strategy"], "fib-ema"
            )
            self.engine.jev.decide.assert_not_awaited()

    async def test_native_confirmed_history_bootstrap_never_places_old_setups(self):
        await self.authorize_meta("vwap-meta")
        cutoff = int(self.engine.clock()) // 900 * 900
        bars = [
            [cutoff - (299 - i) * 900, "50000", "50010", "49990", "50000", "0", "10", 0]
            for i in range(299)
        ]
        with patch.object(self.client, "bars", AsyncMock(return_value=bars)) as read:
            await self.engine.tick()
            read.assert_awaited_once_with(self.pair, 15, count=299)
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertGreaterEqual(meta.view(self.engine)["structure_observed_bars"], 73)
        self.assertEqual(meta.view(self.engine)["confirmation_observed_bars"], 299)
        self.assertEqual(self.posts(), [])

    async def test_real_native_intent_tagging_fee_ownership_and_protective_exit(self):
        await self.authorize_meta("fib-meta")
        state = meta.snapshot(self.engine)
        setup = Setup(
            "fib-meta",
            int(time.time()) // 900 * 900,
            dec(50000),
            dec(49000),
            dec(54000),
            {"selected_strategy": "fib-ema", "candidate_id": "fixture"},
        )
        await self.engine.valuation(False)
        qty, price, risk = meta.risk_plan(self.engine, self.pair, self.book(), setup)
        state["position"] = {
            "id": "fixture-meta",
            "stop": str(setup.stop),
            "target": str(setup.target),
            "deadline": self.engine.clock() + meta.MAX_HOLD,
            "signal": setup.facts,
            "risk_plan": risk,
        }
        self.store.put(meta.key(self.engine), state)
        order = await self.engine.place(self.pair, "buy", qty, price, self.book())
        self.assertEqual(order["meta_id"], "fixture-meta")
        self.assertEqual(order["payload"]["ordType"], "ioc")
        self.assertNotIn("attachAlgoOrds", order["payload"])
        self.assertEqual(order["authorization_id"], self.engine.authorization["id"])
        self.assertLessEqual(dec(order["risk_plan"]["requested_stop_risk"]), 5)
        self.assertEqual(
            meta.owned(self.engine, meta.snapshot(self.engine)), self.engine.balance(self.pair.base)
        )
        held = meta.owned(self.engine, state, state["position"])
        quantity = floor(held, self.pair.lot)
        exit_order = await self.engine.place(
            self.pair, "sell", quantity, self.book().bids[0][0], self.book(), exit_only=True
        )
        self.assertEqual(exit_order["meta_id"], "fixture-meta")
        self.assertTrue(exit_order["exit_only"])
        self.assertEqual(
            meta.owned(self.engine, meta.snapshot(self.engine)), self.engine.balance(self.pair.base)
        )
        await self.engine.stop()
        self.engine.settings["strategy"] = "vwap-meta"
        self.engine.exchange = "okx"
        self.engine.mode = "trading"
        with self.assertRaises(SafetyError):
            self.engine.validate_capabilities(self.engine.settings)
