import unittest
from unittest.mock import patch

from kairos import htf
from kairos.domain import dec
from kairos.store import Store
from tests.helpers import RuleEngine as Engine
from tests.helpers import fake_jev, fake_kraken, htf_baseline


class AuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.kraken, self.jev = fake_kraken(), fake_jev()
        self.engine = Engine(self.store, self.kraken, self.jev, lambda *_: None)
        await self.engine.initialize()
        await self.engine.configure(
            {
                **self.engine.settings,
                "order_size": "25",
                "max_exposure": "100",
                "reinvest_profits": False,
            }
        )
        await self.engine.start()
        await htf_baseline(self.engine, self.kraken.candles)
        await self.engine.tick()

    async def asyncTearDown(self):
        await self.kraken.market_data.close()
        self.store.close()

    async def test_run_and_protection_survive_pause_and_process_recreation(self):
        run = self.engine.active_run()
        position = htf.snapshot(self.engine)["position"]
        order = self.engine.orders()[0]
        self.assertEqual(order["run_id"], run["id"])
        self.assertEqual(order["execution_revision"], run["execution_revision"])
        self.assertEqual(order["risk_at_submission"]["exposure_target"], "80.0")
        self.assertIn("momentum screen", order["reason"])
        summary = self.engine.run_summary()
        self.assertEqual(summary["filled_orders"], 1)
        self.assertEqual(dec(summary["paid_fees"][order["quote"]]), dec(order["fee"]))
        await self.engine.stop()
        self.engine = Engine(self.store, self.kraken, self.jev, lambda *_: None)
        await self.engine.initialize()
        await self.engine.start()
        self.assertEqual(self.engine.active_run(), run)
        self.assertEqual(htf.snapshot(self.engine)["position"], position)

    async def test_config_change_splits_run_not_holdings_and_counters_do_not_mix(self):
        old = self.engine.active_run()
        position = htf.snapshot(self.engine)["position"]
        ledger = self.engine.ledger()
        await self.engine.stop()
        await self.engine.configure({**self.engine.settings, "daily_loss": "12.50"})
        self.assertIsNone(self.engine.active_run())
        await self.engine.start()
        run = self.engine.active_run()
        self.assertNotEqual(run["id"], old["id"])
        self.assertEqual(run["previous_run_id"], old["id"])
        self.assertEqual(self.engine.decision_summary()["assessments"], 0)
        self.assertEqual(self.engine.run_summary()["filled_orders"], 0)
        self.assertEqual(dec(self.engine.run_summary()["equity_change"]), 0)
        self.assertEqual(self.engine.ledger(), ledger)
        self.assertEqual(htf.snapshot(self.engine)["position"], position)
        self.assertEqual(self.engine.orders()[0]["run_id"], old["id"])

    async def test_explicit_program_rearm_starts_a_new_run_without_resetting_cash_or_holdings(self):
        await self.engine.stop()
        await self.engine.reset_paper()
        await self.engine.configure(
            {**self.engine.settings, "strategy": "dca", "dca_amount": "10", "dca_count": 1}
        )
        await self.engine.start()
        await self.engine.tick()
        old = self.engine.active_run()
        self.assertEqual(self.engine.run_summary()["filled_orders"], 1)
        await self.engine.stop()
        ledger = self.engine.ledger()
        await self.engine.reset_program("NEW STRATEGY RUN")
        self.assertIsNone(self.engine.active_run())
        self.assertIsNone(self.engine.latest_decision)
        self.assertEqual(self.store.history()[-1]["data"]["previous_run_id"], old["id"])
        await self.engine.start()
        self.assertNotEqual(self.engine.active_run()["id"], old["id"])
        self.assertEqual(self.engine.run_summary()["filled_orders"], 0)
        self.assertEqual(self.engine.ledger(), ledger)

    async def test_revision_change_starts_a_separate_run(self):
        old = self.engine.active_run()
        await self.engine.stop()
        with patch("kairos.engine.EXECUTION_REVISION", "test-new-policy"):
            await self.engine.start()
            self.assertNotEqual(self.engine.active_run()["id"], old["id"])
            self.assertEqual(self.engine.active_run()["execution_revision"], "test-new-policy")
            self.assertEqual(
                self.engine.orders()[0]["execution_revision"], old["execution_revision"]
            )

    async def test_reset_retains_previous_ledger_and_run_identity_without_migrating_history(self):
        run = self.engine.active_run()
        ledger = self.engine.ledger()
        await self.engine.stop()
        await self.engine.reset_paper()
        self.assertIsNone(self.engine.active_run())
        reset = self.store.history()[-1]["data"]
        self.assertEqual(reset["previous_run_id"], run["id"])
        self.assertEqual(reset["previous_ledger"], ledger)
        self.assertEqual(self.engine.orders()[0]["run_id"], run["id"])
        await self.engine.start()
        self.assertNotEqual(self.engine.active_run()["id"], run["id"])
        self.assertEqual(self.engine.decision_summary()["assessments"], 0)

    async def test_cumulative_fill_totals_are_idempotent_and_survive_history_deletion(self):
        state = htf.snapshot(self.engine)
        state["position"]["deadline"] = 0
        self.store.put(htf.key(self.engine), state)
        await self.engine.tick()
        await self.engine.stop()
        order = self.engine.orders()[0]
        before = self.engine.run_summary()
        self.store.save_order(order)  # Repeated settlement must not count the fee twice.
        self.assertEqual(self.engine.run_summary(), before)
        await self.engine.paper_order_history(order["id"], "delete", "DELETE PAPER ORDER")
        self.assertEqual(self.engine.run_summary(), before)

    async def test_legacy_fill_is_not_falsely_attributed_to_current_run(self):
        order = self.engine.orders()[0]
        legacy = {
            k: v
            for k, v in order.items()
            if k not in {"run_id", "settings_id", "execution_revision"}
        }
        legacy["id"] = "legacy-order"
        self.store.save_order(legacy)
        self.engine.event("fill", {"order_id": legacy["id"], "mode": "dry-run"})
        event = self.store.history()[-1]["data"]
        self.assertIsNone(event["run_id"])
        self.assertIsNone(event["execution_revision"])
