import copy
import unittest
from types import SimpleNamespace

from kairos import fibonacci_chart as chart
from kairos.meta_signals import MetaSignals, pack
from kairos.settings import DEFAULTS


class FibonacciChartTests(unittest.TestCase):
    def setUp(self):
        self.low_at = 1700000000
        self.high_at = self.low_at + 7200
        self.known = self.high_at + 10800
        self.observed = self.known + 1800
        self.engine = SimpleNamespace(
            settings={**DEFAULTS, "strategy": "fib-meta"},
            exchange="okx-demo",
            mode="paper",
            running=True,
            clock=lambda: self.observed + 3,
        )
        self.signals = MetaSignals("fib-meta", 60, context_minutes=240).checkpoint()
        self.signals["context"]["last_now"] = self.observed
        self.signals["parent"]["parent"] = pack(
            (
                self.low_at,
                chart.dec(100),
                self.low_at + 10800,
                self.high_at,
                chart.dec(120),
                self.known,
            )
        )
        self.signals["background"]["status"]["bullish"] = True
        self.state = {"position": None}

    def facts(self, family="fib-ema", low="100", high="120"):
        return {
            "swing_low_at": self.low_at,
            "swing_high_at": self.high_at,
            "swing_low": low,
            "swing_high": high,
            "swing_known_at": self.known,
            "selected_strategy": family,
            "confirmed_at": self.observed,
        }

    def view(self):
        return chart.view(self.engine, self.state, self.signals)

    def test_reference_uses_actual_parent_with_retracement_orientation_and_known_time(self):
        before = copy.deepcopy((self.state, self.signals, self.engine.settings))
        result = self.view()
        self.assertEqual(
            (result["kind"], result["phase"], result["family"]), ("retracement", "reference", None)
        )
        prices = {r["ratio"]: chart.dec(r["price"]) for r in result["levels"]}
        self.assertEqual(prices["0"], 120)
        self.assertEqual(prices["1"], 100)
        self.assertEqual(prices["0.382"], chart.dec("112.360"))
        self.assertEqual(prices["0.618"], chart.dec("107.640"))
        self.assertEqual(prices["4.236"], chart.dec("35.280"))
        self.assertEqual(result["known_at"], self.known)
        self.assertEqual(result["scope"]["pair"], self.engine.settings["pair"])
        self.assertEqual((self.state, self.signals, self.engine.settings), before)

    def test_actual_confirmed_extension_overrides_another_pending_candidate(self):
        self.signals["children"]["fib-confluence"]["pending"] = {
            "armed_at": self.observed - 900,
            "expires": self.observed + 1800,
            "facts": self.facts("fib-confluence"),
        }
        self.state["chart_setup"] = {
            "closed_at": self.observed,
            "facts": self.facts("fib-extension"),
        }
        result = self.view()
        self.assertEqual(
            (result["kind"], result["phase"], result["family"]),
            ("extension", "confirmed", "fib-extension"),
        )
        prices = {r["ratio"]: chart.dec(r["price"]) for r in result["levels"]}
        self.assertEqual(prices["0"], 100)
        self.assertEqual(prices["1"], 120)
        self.assertEqual(prices["1.618"], chart.dec("132.360"))
        self.assertEqual(prices["4.236"], chart.dec("184.720"))
        self.assertIsNone(result["trade"])
        # A previous confirmation does not masquerade as this bar's selected family.
        self.state["chart_setup"]["closed_at"] -= 900
        self.assertEqual(self.view()["family"], "fib-confluence")

    def test_pending_priority_is_labeled_candidate_and_sweep_keeps_its_family(self):
        for family in ("fib-sweep", "fib-ema"):
            self.signals["children"][family]["pending"] = {
                "armed_at": self.observed - 900,
                "expires": self.observed + 900,
                "facts": self.facts(family),
            }
        result = self.view()
        self.assertEqual(
            (result["phase"], result["family"], result["kind"]), ("armed", "fib-ema", "retracement")
        )
        self.signals["children"]["fib-ema"]["pending"]["expires"] = self.observed - 1
        self.assertEqual(self.view()["family"], "fib-sweep")

    def test_owned_geometry_and_percentage_plan_do_not_follow_a_new_parent(self):
        facts = self.facts("fib-extension")
        self.state["position"] = {
            "signal": facts,
            "entry_price": "121",
            "stop": "118.58",
            "target": "125.84",
            "deadline": self.observed + 28800,
            "protection_sealed": True,
            "percentages": {"stop": "2", "target": "4"},
            "exit_reason": "Target",
        }
        self.signals["parent"]["parent"] = pack(
            (
                self.low_at,
                chart.dec(200),
                self.low_at + 10800,
                self.high_at,
                chart.dec(240),
                self.known,
            )
        )
        before = copy.deepcopy(self.state)
        result = self.view()
        self.assertEqual(
            (result["kind"], result["phase"], result["low"]["price"]), ("extension", "owned", "100")
        )
        self.assertEqual(result["trade"]["target"], "125.84")
        self.assertNotEqual(
            result["trade"]["target"],
            next(r["price"] for r in result["levels"] if r["ratio"] == "1.618"),
        )
        self.assertEqual(result["trade"]["exit_reason"], "Target")
        self.assertEqual(self.state, before)
        self.engine.running = False
        self.assertFalse(self.view()["running"])
        self.state["position"]["protection_sealed"] = False
        self.assertEqual(self.view()["phase"], "entry-plan")

    def test_waiting_stale_scope_expired_parent_and_other_strategy_never_guess_anchors(self):
        self.signals["frame"] = 240
        self.assertEqual(self.view()["status"], "waiting")
        self.signals["frame"] = 60
        self.signals["parent"]["parent"] = None
        self.assertEqual(self.view()["status"], "waiting")
        self.signals["parent"]["parent"] = pack(
            (
                self.low_at,
                chart.dec(100),
                self.low_at + 10800,
                self.high_at,
                chart.dec(120),
                self.known - 1000000,
            )
        )
        self.assertEqual(self.view()["status"], "waiting")
        self.engine.settings["strategy"] = "vwap-meta"
        self.assertIsNone(self.view())

    def test_invalid_or_future_display_facts_fail_without_mutating_trade_state(self):
        self.state["chart_setup"] = {"closed_at": self.observed, "facts": self.facts()}
        for change in (
            {"swing_low": "NaN"},
            {"swing_high": "99"},
            {"swing_known_at": self.observed + 1},
            {"swing_high_at": self.low_at - 1},
            {"selected_strategy": "unrelated"},
        ):
            with self.subTest(change=change):
                self.state["chart_setup"]["facts"] = {**self.facts(), **change}
                before = copy.deepcopy(self.state)
                self.assertEqual(self.view()["status"], "unavailable")
                self.assertEqual(self.state, before)

    def test_existing_decision_records_retain_display_geometry_without_trading_side_effects(self):
        from kairos import meta_strategy
        from kairos.store import Store
        from tests.helpers import BTC

        store = Store(":memory:")
        self.addCleanup(store.close)
        self.engine.resolve = lambda _: BTC
        self.engine.balance = lambda _: 0
        self.engine.event = store.event
        self.state.update(entries_halted=False, signals=self.signals)
        before = copy.deepcopy(self.state)
        meta_strategy.report(self.engine, "hold", "Fixture waiting", self.state)
        recorded = store.history()[0]["data"]["state"]["fibonacci"]
        self.assertEqual(recorded["kind"], "retracement")
        self.assertEqual(recorded["known_at"], self.known)
        self.signals["parent"]["parent"] = None
        self.assertEqual(recorded["low"]["price"], "100")
        self.state["signals"] = before["signals"]
        self.assertEqual(self.state, before)
        self.assertEqual(store.orders(), [])

    def test_nonpositive_reference_extensions_are_omitted_not_clipped(self):
        self.state["chart_setup"] = {
            "closed_at": self.observed,
            "facts": self.facts(low="1", high="120"),
        }
        result = self.view()
        self.assertEqual(result["status"], "available")
        self.assertTrue(all(chart.dec(r["price"]) > 0 for r in result["levels"]))
        self.assertNotIn("4.236", {r["ratio"] for r in result["levels"]})
