"""Regression checks for sequential research and its causal single factors."""

import asyncio
import unittest
from collections import deque
from pathlib import Path

from kairos import research_backtest as runner
from kairos.backtest_report import render
from kairos.domain import SafetyError, dec
from kairos.research_series import SIGNALS, STEPS, improves, module, pair_summary, profile
from kairos.research_signals import Setup
from tests.test_research_backtest import PAIR, SCENARIO, START, candle
from tests.test_research_diagnostics import diagnostic_run


class ResearchSeriesTests(unittest.TestCase):
    def setUp(self):
        self.source = SIGNALS.read_text()
        self.executor = Path(runner.__file__).read_text()

    def signals(self, name, variant):
        source, _ = profile(self.source, self.executor, name)
        return module(source, "series_test_signals").Signals(5, variant)

    def test_every_declared_profile_compiles_and_keeps_all_eight_setups(self):
        # Also exercise composition when every candidate is accepted in sequence.
        source, executor = self.source, self.executor
        for name, role, _ in STEPS:
            a, b = profile(source, executor, name)
            signal_module = module(a, "series_test_signals")
            execution_module = module(b, "series_test_executor")
            self.assertEqual(signal_module.VARIANTS, runner.VARIANTS)
            self.assertEqual(signal_module.RULES, runner.RULES)
            self.assertEqual(execution_module.ECONOMIC_POLICIES, runner.ECONOMIC_POLICIES)
            if role == "candidate":
                source, executor = a, b

    def test_reclaim_can_confirm_completed_arming_bar_without_backdating(self):
        original = module(self.source, "series_original").Signals(5, "vwap-reclaim")
        challenger = self.signals("1b-reclaim-closed-arm", "vwap-reclaim")
        rows = [[i * 300, "100", "101", "99", "100", "0", "1", "1"] for i in range(50)]
        rows += [[15000, "91", "92", "89", "90", "0", "1", "1"]]
        for row in rows:
            original.push(row, row[0] + 300)
            challenger.push(row, row[0] + 300)
        recovery = [15300, "90", "103", "90", "102", "0", "10", "1"]
        with self.assertRaises(SafetyError):
            challenger.push(recovery, 15599)
        _, unchanged = original.push(recovery, 15600)
        _, setup = challenger.push(recovery, 15600)
        self.assertIsNone(unchanged)
        self.assertIsNotNone(setup)
        self.assertEqual(setup.closed_at, 15600)
        self.assertEqual(setup.facts["armed_at"], setup.facts["confirmed_at"])
        self.assertEqual(setup.facts["candidate_id"], "vwap-reclaim:15600")
        self.assertTrue(setup.stop < setup.reference < setup.target)
        self.assertIsNone(challenger.pending)

    def test_unweighted_control_does_not_change_raw_volume_or_liquidity_input(self):
        source, _ = profile(self.source, self.executor, "3b-unweighted-mean")
        weighted = module(self.source, "series_weighted").WeightedPrices()
        unweighted = module(source, "series_unweighted").WeightedPrices()
        rows = [
            [0, "100", "100", "100", "100", "0", "9", "1"],
            [300, "200", "200", "200", "200", "0", "1", "1"],
        ]
        for row in rows:
            weighted.add(row)
            unweighted.add(row)
        self.assertEqual(weighted.bands()[0], 110)
        self.assertEqual(unweighted.bands()[0], 150)
        self.assertEqual([r[6] for r in rows], ["9", "1"])

    def test_future_hourly_close_cannot_enter_current_context(self):
        signals = self.signals("4b-completed-60m-trend", "fib-ema")
        signals.htf_rows = (
            (-3300, "100", "101", "99", "100", "0", "1", "1"),
            (300, "100", "1001", "99", "1000", "0", "1", "1"),
        )
        signals.push([0, "100", "101", "99", "100", "0", "1", "1"], 300)
        self.assertEqual(signals.htf_count, 1)
        self.assertEqual(signals.htf_known_at, 300)
        self.assertEqual(signals.htf_fast, 100)
        self.assertEqual(signals.htf_index, 1)
        self.assertFalse(getattr(signals, "htf_rising", False))

    def test_completed_paired_report_keeps_signals_active_days_and_economics(self):
        case = diagnostic_run(economic_policy="positive-target")
        report = {
            "status": "complete",
            "assumptions": runner.ASSUMPTIONS,
            "start": START,
            "end": START + 600,
            "cases": [{**case, "signal_version": "v1"}, {**case, "signal_version": "v2"}],
            "comparison_labels": {"v1": "accepted", "v2": "challenger"},
            "paired_comparison": [
                {
                    "policy": case["policy"],
                    "minutes": 5,
                    "v1": pair_summary(case),
                    "v2": pair_summary(case),
                }
            ],
        }
        html = render(report, "paired.json")
        self.assertIn("Matched accepted / challenger comparison", html)
        self.assertIn("Signals accepted / challenger", html)
        self.assertIn("Active days accepted / challenger", html)
        self.assertIn("Entry economics", html)

    def test_unrecognized_source_condition_fails_instead_of_silent_noop(self):
        with self.assertRaises(SafetyError):
            profile(
                self.source.replace("and close >= self.fast", "and close > self.fast"),
                self.executor,
                "1e-ema-recapture-ablation",
            )

    def test_inactivity_and_risk_regressions_cannot_win_selection(self):
        def case(fills, active, returns, closed_r, breaches=0):
            return {
                "policy": "fib-ema",
                "minutes": 5,
                "activity": {"signals": 10, "days_with_fills": active},
                "filled_entry_orders": fills,
                "diagnostics": {
                    "economics": {"filled_plans": fills, "nonpositive_target_reward": 0}
                },
                "trades": [{"realized_R": str(closed_r)}],
                "net_return_pct": str(returns),
                "risk": {"budget_breaches": breaches},
                "halted": False,
            }

        control = [case(5, 4, -1, -1)]
        self.assertFalse(improves(control, [case(1, 1, 0, 0)]))
        self.assertFalse(improves(control, [case(5, 4, 1, 1, 1)]))
        self.assertTrue(improves(control, [case(6, 5, 1, 1)]))

    def test_trailing_uses_completed_favorable_move_and_preserves_initial_plan(self):
        class Scheduled:
            tr = deque([dec(2)], maxlen=14)

            def push(self, row, now):
                end = row[0] + 300
                return {}, Setup(
                    "vwap-pullback", end, dec(100), dec(95), dec(120), {}
                ) if end == START else None

        _, source = profile(self.source, self.executor, "5-favorable-atr-trail")
        runtime = module(source, "series_trailing")
        minutes = [candle(ts, dec(100), volume=10000) for ts in range(START - 60, START + 600, 60)]
        minutes[5] = candle(START + 240, dec(105), volume=10000)
        minutes[6] = candle(START + 300, dec(104), volume=10000)
        bars = [candle(START - 300, dec(100)), candle(START, dec(105), low=dec(99), high=dec(106))]
        results = []
        for simulator in (runner.simulate, runtime.simulate):
            results.append(
                asyncio.run(
                    simulator(
                        minutes,
                        bars,
                        PAIR,
                        runner.pilot_settings(PAIR, 5, SCENARIO, 1000, 1000, 1000, dec("12.50")),
                        SCENARIO,
                        "vwap-pullback",
                        START,
                        START + 600,
                        signals=Scheduled(),
                        economic_policy="positive-target",
                    )
                )
            )
        baseline, trial = results
        self.assertEqual(baseline["orders"][0]["protection"], trial["orders"][0]["protection"])
        self.assertEqual(baseline["orders"][0]["risk_plan"], trial["orders"][0]["risk_plan"])
        updates = trial["diagnostics"]["trailing_updates"]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["known_at"], START + 300)
        self.assertEqual(updates[0]["effective_at"], START + 301)
        self.assertGreater(dec(updates[0]["new_stop"]), dec(updates[0]["old_stop"]))
        self.assertEqual(baseline["completed_trades"], 0)
        self.assertEqual(trial["completed_trades"], 1)
        self.assertEqual(trial["trades"][0]["reason"], "Stop")
        self.assertLess(
            dec(trial["trades"][0]["net_pnl"]), 0
        )  # The later gap is not a guaranteed profitable exit.


if __name__ == "__main__":
    unittest.main()
