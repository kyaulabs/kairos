import asyncio
import copy
import unittest

from kairos.backtest import HistoricalMarket
from kairos.backtest_report import render
from kairos.domain import BPS, ZERO, dec
from kairos.research_backtest import net_quantity, pilot_settings, simulate
from kairos.research_diagnostics import entry_economics, execution_estimate
from kairos.research_signals import Setup
from kairos.strategies import limit_price
from tests.test_research_backtest import PAIR, SCENARIO, START, ScheduledSignals, fixture


class SingleSignal(ScheduledSignals):
    def __init__(self, target=105):
        self.target = dec(target)

    def push(self, row, now):
        checks, setup = super().push(row, now)
        if not setup or setup.closed_at != START:
            return checks, None
        return checks, Setup(setup.variant, START, setup.reference, setup.stop, self.target, {})


def diagnostic_run(data=None, *, target=105, economic_policy="diagnostic"):
    minutes, bars = data or fixture()
    return asyncio.run(
        simulate(
            minutes,
            bars,
            PAIR,
            pilot_settings(PAIR, 5, SCENARIO, 1000, 1000, 1000, dec("12.50")),
            SCENARIO,
            "vwap-pullback",
            START,
            START + 600,
            signals=SingleSignal(target),
            economic_policy=economic_policy,
        )
    )


class ResearchDiagnosticsTests(unittest.TestCase):
    def test_projected_bounds_match_executor_and_native_received_currency_accounting(self):
        result = diagnostic_run(fixture(price_after=95, prior_volume=10000, current_volume=10000))
        buy = result["orders"][0]
        economy = buy["entry_economics"]
        market = HistoricalMarket(PAIR, SCENARIO, dec(8), dec(10), 5, ())

        async def check():
            for price, side in ((dec(95), "sell"), (dec(105), "sell"), (dec(100), "buy")):
                market.mid = price
                book = await market.book(PAIR)
                self.assertEqual(
                    execution_estimate(PAIR, price, side, SCENARIO), limit_price(book, side, 10)
                )

        asyncio.run(check())
        projection = economy["filled_projection"]
        self.assertEqual(dec(projection["net_owned"]), net_quantity(buy))
        self.assertGreaterEqual(dec(projection["retained_quantity"]), 0)
        self.assertLess(dec(projection["retained_quantity"]), PAIR.lot)
        self.assertEqual(
            dec(economy["net_stop_loss_per_gross_unit"]),
            dec(buy["risk_plan"]["loss_per_gross_unit"]),
        )
        trade = result["trades"][0]
        self.assertEqual(dec(projection["net_stop_loss"]), -dec(trade["net_pnl"]))
        self.assertLess(abs(dec(trade["realized_R"]) + 1), dec("1e-24"))
        self.assertEqual(
            dec(projection["sold_share_cost_basis"]) + dec(projection["retained_cost_basis"]),
            dec(buy["cost"]),
        )

    def test_cost_negative_target_is_not_reported_as_a_profitable_exit(self):
        data = fixture(price_after=100.3, prior_volume=10000, current_volume=10000)
        data[0][1][2] = "100.2"  # No earlier profitable target touch in the entry minute.
        result = diagnostic_run(data, target="100.3")
        summary = result["diagnostics"]["economics"]
        self.assertEqual(summary["filled_plans"], 1)
        self.assertEqual(summary["nonpositive_target_reward"], 1)
        self.assertEqual(summary["losing_target_episodes"], 1)
        self.assertEqual(summary["losing_targets_cost_negative_at_entry"], 1)
        self.assertEqual(result["trades"][0]["reason"], "Target")
        self.assertLess(dec(result["trades"][0]["net_pnl"]), 0)
        plan = result["orders"][0]["entry_economics"]
        self.assertGreater(dec(plan["cost_share_of_gross_target_room"]), 1)
        self.assertLess(dec(plan["break_even_entry_fill_price"]), dec(result["orders"][0]["price"]))

    def test_partial_target_liquidation_exposes_later_stop_crossing_and_zero_fill_delay(self):
        data = fixture(prior_volume=10000, current_volume=10000)
        minutes = data[0]
        minutes[1][6] = ".001"  # Limits the next minute's first target reduction.
        minutes[2][6] = "0"  # No liquidity in the following minute.
        for row in minutes[3:]:
            row[1:5] = ["70", "71", "69", "70"]
        result = diagnostic_run(data)
        trade = result["trades"][0]
        audit = trade["exit_diagnostics"]
        self.assertEqual(trade["reason"], "Target")
        self.assertEqual(audit["first_trigger"]["observed_at"], START + 61)
        self.assertEqual(audit["stop_crossed_after_target_at"], START + 121)
        self.assertEqual(audit["IOC_attempts"], 3)
        self.assertEqual(audit["partial_fill_attempts"], 1)
        self.assertEqual(audit["zero_fill_attempts"], 1)
        self.assertEqual(audit["last_fill_delay_seconds"], 120)
        self.assertGreater(dec(audit["modeled_at_target_net_pnl_on_sold_share"]), 0)
        self.assertLess(dec(trade["net_pnl"]), 0)
        self.assertTrue(result["halted"])
        self.assertEqual(
            dec(audit["gross_target_pnl_before_sell_costs"])
            - dec(audit["exit_price_shortfall_from_target"])
            - dec(audit["sell_quote_fees"]),
            dec(trade["net_pnl"]),
        )
        self.assertEqual(
            dec(audit["modeled_at_target_net_pnl_on_sold_share"])
            + dec(audit["execution_delta_from_modeled_target"]),
            dec(trade["net_pnl"]),
        )
        self.assertEqual(
            sum(
                (
                    dec(audit[k])
                    for k in (
                        "target_to_observed_mid_shortfall",
                        "exit_spread_and_quote_tick_cost",
                        "exit_slippage_and_bound_tick_cost",
                    )
                ),
                ZERO,
            ),
            dec(audit["exit_price_shortfall_from_target"]),
        )
        self.assertLess(dec(audit["retained_quantity"]), PAIR.minimum)

    def test_open_holdings_are_not_invented_liquidations_or_closed_R(self):
        result = diagnostic_run(fixture(price_after=100, prior_volume=10000, current_volume=10000))
        self.assertEqual(result["completed_trades"], 0)
        self.assertEqual(result["risk_units"]["closed_trades"], 0)
        self.assertIsNone(result["risk_units"]["mean_closed_R"])
        audit = result["diagnostics"]["exits"][0]
        self.assertFalse(audit["episode_retired"])
        self.assertIsNone(audit["first_trigger"])
        self.assertIsNone(audit["average_exit_fill_price"])
        self.assertEqual(dec(audit["sold_quantity"]), 0)
        self.assertGreaterEqual(dec(audit["retained_quantity"]), PAIR.minimum)
        candidate = result["diagnostics"]["candidates"][0]
        self.assertEqual(
            sum(
                (
                    dec(candidate[k])
                    for k in (
                        "next_open_move_from_confirmation",
                        "entry_spread_and_quote_tick_cost",
                        "entry_slippage_and_bound_tick_cost",
                    )
                ),
                ZERO,
            ),
            dec(candidate["room_consumed_after_confirmation"]),
        )

    def test_missing_and_unfilled_candidates_and_clock_coverage_remain_visible(self):
        data = fixture(prior_volume=0, current_volume=0)
        unfilled = diagnostic_run(data)
        self.assertEqual(unfilled["diagnostics"]["candidates"][0]["outcome"], "unfilled_IOC")
        self.assertEqual(unfilled["diagnostics"]["economics"]["filled_plans"], 0)
        changed = copy.deepcopy(data)
        changed = ([r for r in changed[0] if r[0] != START], changed[1])
        missing = diagnostic_run(changed)
        self.assertEqual(
            missing["diagnostics"]["candidates"][0]["outcome"], "missing_next_execution_minute"
        )
        self.assertEqual(missing["entry_attempts"], 0)
        for strata in missing["diagnostics"]["clock_coverage"].values():
            self.assertEqual(sum(s["missing_minutes"] for s in strata.values()), 1)
            self.assertEqual(sum(s["observed_minutes"] for s in strata.values()), 9)
        tiny = entry_economics(
            PAIR,
            dec(100),
            dec(95),
            dec(105),
            SCENARIO,
            10,
            gross=PAIR.minimum,
            owned=PAIR.minimum * (1 - dec(10) / BPS),
        )
        self.assertFalse(tiny["filled_projection"]["meets_target_exit_minimum"])
        self.assertIsNone(tiny["filled_projection"]["net_target_pnl"])
        self.assertGreater(dec(tiny["filled_projection"]["retained_quantity"]), ZERO)

    def test_report_distinguishes_projection_from_exit_and_shows_unfilled_evidence(self):
        result = diagnostic_run(fixture(prior_volume=0, current_volume=0))
        html = render({"cases": [result], "assumptions": []}, "diagnostics.json")
        self.assertIn("KAIROS", html)
        self.assertIn("Entry economics and exit timing", html)
        self.assertIn("Nonpositive net target", html)
        self.assertIn("unfilled_IOC", html)
        self.assertIn("not expected profit", html)
        self.assertNotIn("https://", html)
        result["signal_version"] = "control"
        summary = {
            "signals": 1,
            "filled_entries": 0,
            "active_days": 0,
            "net_return_pct": "0",
            "mean_closed_R": None,
        }
        named = render(
            {
                "cases": [result],
                "assumptions": [],
                "comparison_labels": {"v1": "control", "v2": "earlier<script>"},
                "paired_comparison": [
                    {"policy": "fib-ema", "minutes": 5, "v1": summary, "v2": summary}
                ],
            },
            "trial.json",
        )
        self.assertIn("Matched control / earlier&lt;script&gt; comparison", named)
        self.assertNotIn("<script>", named)
