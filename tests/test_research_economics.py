import asyncio
import copy
import unittest

from kairos.domain import SafetyError, dec
from kairos.research_backtest import pilot_settings, simulate
from kairos.research_diagnostics import entry_economics
from kairos.research_signals import Setup
from tests.test_research_backtest import PAIR, SCENARIO, START, ScheduledSignals, candle, fixture
from tests.test_research_diagnostics import diagnostic_run


class ResearchEconomicPolicyTests(unittest.TestCase):
    def test_opt_in_rejects_cost_negative_target_without_erasing_signal_or_inventing_orders(self):
        data = fixture(price_after=100.3, prior_volume=10000, current_volume=10000)
        data[0][1][2] = "100.2"
        baseline = diagnostic_run(data, target="100.3")
        gated = diagnostic_run(data, target="100.3", economic_policy="positive-target")
        self.assertEqual(baseline["economic_policy"], "diagnostic")
        self.assertEqual(baseline["filled_entry_orders"], 1)
        self.assertEqual(gated["activity"]["signals"], baseline["activity"]["signals"])
        self.assertEqual(gated["gate_counts"], baseline["gate_counts"])
        self.assertEqual(gated["entry_attempts"], 0)
        self.assertEqual(gated["orders"], [])
        self.assertEqual(gated["paid_fees"], "0")
        self.assertEqual(dec(gated["ending_liquidation_equity"]), 1000)
        self.assertEqual(gated["diagnostics"]["economics"]["economic_rejections"], 1)
        record = gated["diagnostics"]["candidates"][0]
        self.assertEqual(record["outcome"], "blocked_target_economics")
        self.assertLess(dec(record["entry_economics"]["net_target_reward_per_gross_unit"]), 0)

    def test_boundary_is_rechecked_at_next_open_and_cannot_use_future_volume(self):
        data = fixture(prior_volume=10000, current_volume=10000)
        data[0][1][1:5] = ["100.6", "100.8", "100.5", "100.6"]
        at_close = entry_economics(PAIR, dec("100.2"), dec(95), dec(101), SCENARIO, 10)
        self.assertGreater(dec(at_close["net_target_reward_per_gross_unit"]), 0)
        gated = diagnostic_run(data, target=101, economic_policy="positive-target")
        record = gated["diagnostics"]["candidates"][0]
        self.assertLess(dec(record["next_open_entry_bound"]), 101)  # Raw price plan still valid.
        self.assertEqual(record["outcome"], "blocked_target_economics")
        changed = copy.deepcopy(data)
        for row in changed[0][1:]:
            row[6] = "999999999"
        mutated = diagnostic_run(changed, target=101, economic_policy="positive-target")
        self.assertEqual(mutated["orders"], [])
        self.assertEqual(
            mutated["diagnostics"]["candidates"][0]["entry_economics"], record["entry_economics"]
        )

    def test_positive_reward_below_one_R_is_allowed_and_owned_stop_survives_gap_halt(self):
        for volume in (10000, ".5"):
            with self.subTest(prior_volume=volume):
                data = fixture(price_after=70, prior_volume=volume, current_volume=10000)
                data[0][1][2] = "100.5"  # No target hit before the gap.
                baseline = diagnostic_run(data, target=101)
                gated = diagnostic_run(data, target=101, economic_policy="positive-target")
                buy = gated["orders"][0]
                self.assertGreater(dec(buy["entry_economics"]["net_planned_R"]), 0)
                self.assertLess(dec(buy["entry_economics"]["net_planned_R"]), 1)
                self.assertLessEqual(dec(buy["risk_plan"]["capital_budget"]), 10)
                for key in (
                    "net_return_pct",
                    "paid_fees",
                    "retained_quantity",
                    "risk",
                    "decision_reasons",
                ):
                    self.assertEqual(gated[key], baseline[key])
                self.assertTrue(
                    any(o["side"] == "sell" and o["reason"] == "Stop" for o in gated["orders"])
                )
                self.assertLess(dec(gated["retained_quantity"]), PAIR.minimum)
                if volume == 10000:
                    self.assertTrue(gated["halted"])
                    self.assertEqual(gated["risk"]["budget_breaches"], 1)
                else:
                    self.assertLess(dec(buy["filled"]), dec(buy["volume"]))

    def test_exact_break_even_is_ineligible_after_tick_rounding_and_received_currency_fees(self):
        class BreakEven(ScheduledSignals):
            def push(self, row, now):
                checks, setup = super().push(row, now)
                return checks, Setup(
                    "vwap-pullback", START, dec("99790.1"), dec(90000), dec("100010.1"), {}
                ) if setup and setup.closed_at == START else None

        scenario = {**SCENARIO, "slippage_bps": "0"}
        minutes = [
            candle(ts, dec("99790.1"), volume=10000) for ts in range(START - 60, START + 600, 60)
        ]
        bars = [candle(ts, dec("99790.1")) for ts in range(START - 300, START + 600, 300)]
        result = asyncio.run(
            simulate(
                minutes,
                bars,
                PAIR,
                pilot_settings(PAIR, 5, scenario, 1000, 1000, 1000, dec("12.50")),
                scenario,
                "vwap-pullback",
                START,
                START + 600,
                signals=BreakEven(),
                economic_policy="positive-target",
            )
        )
        economy = result["diagnostics"]["candidates"][0]["entry_economics"]
        self.assertEqual(dec(economy["entry_price"]), dec("99800.1"))
        self.assertEqual(dec(economy["target_exit_estimate"]), 100000)
        self.assertEqual(dec(economy["net_target_reward_per_gross_unit"]), 0)
        self.assertEqual(result["entry_attempts"], 0)

    def test_unknown_policy_fails_before_any_execution(self):
        with self.assertRaisesRegex(SafetyError, "Unknown research entry-economic policy"):
            diagnostic_run(economic_policy="approve-anything")
