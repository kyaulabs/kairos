import copy
import unittest

from kairos import programs, review
from kairos.engine import Engine
from kairos.store import Store
from tests.helpers import BTC, fake_jev, fake_kraken
from tests.test_paper_history import order


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.engine = Engine(
            self.store, fake_kraken(), fake_jev(), lambda *_: None, clock=lambda: 1800000060
        )
        await self.engine.initialize()

    def record(self, identifier, **values):
        row = order(identifier, **values)
        self.store.save_order(row)
        return row

    async def test_read_only_counts_include_archives_but_not_other_runs_or_products(self):
        run = self.engine.ensure_run()
        self.record("partial", run_id=run["id"], filled="0.005", status="canceled")
        self.record("zero", run_id=run["id"], filled="0", status="expired")
        self.record("working", run_id=run["id"], filled="0", status="open")
        self.record("other-run", run_id="old")
        self.record("other-mode", run_id=run["id"], mode="trading")
        self.record("other-product", run_id=run["id"], product="margin")
        self.store.paper_order_history("partial", "archive")
        before = list(self.store.db.iterdump())
        result = self.engine.snapshot()["review"]
        self.assertEqual(
            result["orders"],
            {"records": 3, "filled": 1, "partial": 1, "working": 1, "terminal_unfilled": 1},
        )
        self.assertEqual(list(self.store.db.iterdump()), before)

    async def test_readiness_never_promotes_paused_or_stale_history(self):
        state = self.engine.snapshot()
        state["running"] = True
        state["htf_review"].update(
            window_end=1800000060, minute_rows=1770, required_minute_rows=1800
        )
        current = review.snapshot(state, 1800000061)["data"]
        self.assertEqual(current["consecutive_shortfall"], 30)
        self.assertEqual(current["consecutive_minutes"], 1770)
        # Shortfall is NOT the number of holes in the entire archived window.
        state["running"] = False
        self.assertIsNone(review.snapshot(state, 1800000061)["data"]["consecutive_minutes"])
        state["running"] = True
        self.assertIsNone(review.snapshot(state, 1800000120)["data"]["consecutive_minutes"])
        state["settings"]["candle_minutes"] = 15
        self.assertIsNone(review.snapshot(state, 1800000061)["data"]["consecutive_minutes"])

    async def test_dca_budget_uses_saved_config_and_reserves_without_netting_sides(self):
        self.engine.settings.update(strategy="dca", dca_count=7, dca_amount="10")
        programs.prepare(self.engine)
        program = programs.snapshot(self.engine)
        self.record(
            "buy",
            strategy="dca",
            program_id=program["id"],
            cost="9",
            fee="0",
            planning_fee_bps="25",
            filled="0.0009",
        )
        self.engine.settings["dca_amount"] = "50"
        result = self.engine.snapshot()["review"]["program"]
        self.assertTrue(result["configuration_changed"])
        self.assertEqual(result["budget"], "70")
        self.assertEqual(result["unspent_allowance"], "60.9775")
        self.assertEqual(result["markets"][BTC.id]["buy"]["filled"], "0.0009")
        self.assertEqual(result["markets"][BTC.id]["sell"]["filled"], "0")

    async def test_twap_partial_completion_retains_quantity_and_holdings(self):
        self.engine.settings.update(
            strategy="twap", twap_quantity="0.01", twap_limit="10000", twap_slices=2
        )
        programs.prepare(self.engine)
        program = self.store.get(programs.key(self.engine))
        program.update(status="complete", next_slot=2, next_at=None)
        self.store.put(programs.key(self.engine), program)
        self.record(
            "buy", strategy="twap", program_id=program["id"], volume="0.005", filled="0.002"
        )
        ledger = self.engine.ledger()
        ledger["balances"][BTC.base] = "0.002"
        self.store.put("ledger:dry-run", ledger)
        result = self.engine.snapshot()["review"]
        self.assertEqual(result["program"]["unfilled_quantity"], "0.008")
        self.assertEqual(result["program"]["status"], "complete")
        self.assertEqual(result["holdings"], {BTC.base: "0.002"})
        self.assertFalse(self.engine.running)

    async def test_unknown_and_foreign_assessments_are_not_zero_costs(self):
        state = self.engine.snapshot()
        source = {
            "strategy": "htf",
            "pair": BTC.id,
            "mode": "dry-run",
            "ts": 1800000061,
            "state": {"product": "spot", "spread_bps": "0"},
        }
        state["decision"] = source
        state["running"] = True
        self.assertEqual(review.snapshot(state, 1800000061)["costs"]["spread_bps"], "0")
        self.assertFalse(review.snapshot(state, 1800000060)["data"]["assessment_current"])
        for field, value in (("pair", "ETHUSD"), ("strategy", "maker"), ("mode", "trading")):
            state["decision"] = {**source, field: value}
            self.assertIsNone(review.snapshot(state, 1800000061)["costs"]["spread_bps"])

    async def test_check_counts_are_run_scoped_observations_with_missingness(self):
        run = self.engine.ensure_run()
        base = {
            "strategy": "htf",
            "pair": BTC.id,
            "mode": "dry-run",
            "product": "spot",
            "run_id": run["id"],
            "action": "hold",
        }
        self.store.event("decision", {**base, "state": {}})
        unavailable = self.engine.decision_summary()["entry_checks"]
        self.assertEqual(
            unavailable,
            {"observed": 0, "signals": None, "cost_observed": 0, "cost_qualified": None},
        )
        data = {
            "pullback_long": True,
            "pullback_short": False,
            "entry_eligible": False,
            "short_entry_eligible": False,
        }
        for _ in range(2):
            self.store.event("decision", {**base, "state": data})
        self.store.event(
            "decision", {**base, "run_id": "old", "state": {**data, "entry_eligible": True}}
        )
        self.engine.summary_cache = None
        observed = self.engine.decision_summary()["entry_checks"]
        self.assertEqual(
            observed, {"observed": 2, "signals": 2, "cost_observed": 2, "cost_qualified": 0}
        )
        original = copy.deepcopy(data)
        self.engine.snapshot()
        self.assertEqual(data, original)
