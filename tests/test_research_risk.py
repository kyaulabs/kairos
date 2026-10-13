import asyncio
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from kairos.domain import ZERO, SafetyError, dec
from kairos.okx import ORDER_FIELDS
from kairos.research_backtest import main, pilot_settings, simulate, stop_risk_plan
from tests.test_research_backtest import PAIR, SCENARIO, START, ScheduledSignals, fixture


class OneSetup(ScheduledSignals):
    def push(self, row, now):
        checks, setup = super().push(row, now)
        return checks, setup if setup and setup.closed_at == START else None


def risk_run(price, *, previous_volume=10000, daily_loss="12.50"):
    data, bars = fixture(price_after=price, prior_volume=previous_volume, current_volume=10000)
    settings = pilot_settings(PAIR, 5, SCENARIO, 1000, 1000, 1000, dec(daily_loss))
    return asyncio.run(
        simulate(
            data,
            bars,
            PAIR,
            settings,
            SCENARIO,
            "vwap-pullback",
            START,
            START + 600,
            signals=OneSetup(),
        )
    )


class ResearchRiskTests(unittest.TestCase):
    def test_CLI_help_and_risk_argument_are_usable_on_supported_Python_versions(self):
        output = io.StringIO()
        with patch("sys.argv", ["kairos-research", "--help"]), redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                main()
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--risk-percent", output.getvalue())
        self.assertIn("1%", output.getvalue())

    def test_fee_inclusive_stop_sizing_reserves_dust_and_scales_down_with_equity(self):
        def plan(equity, dust):
            return stop_risk_plan(
                PAIR,
                dec("100.2"),
                dec(95),
                dec(equity),
                dec(1000),
                dec(1000),
                dec(dust),
                dec(100),
                SCENARIO,
                10,
                dec(1000),
                dec(1000),
                1,
            )

        quantity, risk = plan(1000, 0)
        self.assertEqual(dec(risk["capital_budget"]), 10)
        self.assertLessEqual(dec(risk["requested_stop_risk"]), 10)
        self.assertLessEqual(
            dec(risk["requested_stop_risk"]) + dec(risk["potential_unprotected_dust_cost"]), 10
        )
        self.assertGreater(
            (quantity + PAIR.lot) * dec(risk["loss_per_gross_unit"])
            + dec(risk["potential_unprotected_dust_cost"]),
            10,
        )
        self.assertGreater(
            dec(risk["loss_per_gross_unit"]), dec("100.2") - 95
        )  # Includes fees and adverse execution.
        smaller, reduced = plan(900, ".02")
        self.assertEqual(dec(reduced["capital_budget"]), 9)
        self.assertEqual(dec(reduced["dust_risk_reserved"]), 2)
        self.assertLessEqual(dec(reduced["requested_stop_risk"]), 7)
        self.assertLess(smaller, quantity)
        _, richer = plan(1100, 0)
        self.assertEqual(
            dec(richer["capital_budget"]), 10
        )  # No compounding above the initial budget.
        empty, _ = plan(900, ".1")
        self.assertEqual(empty, 0)  # Cannot silently ignore unprotected dust.
        empty, tiny = plan(1000, ".099994")
        self.assertLess(dec(tiny["risk_allowance"]), dec(tiny["potential_unprotected_dust_cost"]))
        self.assertEqual(
            empty, 0
        )  # Even a minimum-size partial fill would become unprotected dust.

    def test_full_fill_at_planned_stop_respects_one_percent_and_uses_supported_IOC_payloads(self):
        result = risk_run(95)
        buy = result["orders"][0]
        self.assertGreater(dec(buy["cost"]), 25)  # New sizing is not the old tiny order cap.
        self.assertEqual(buy["filled"], buy["volume"])
        self.assertLessEqual(dec(buy["risk_plan"]["requested_stop_risk"]), 10)
        self.assertLessEqual(1000 - dec(result["ending_liquidation_equity"]), dec("10.00001"))
        self.assertEqual(result["risk"]["budget_breaches"], 0)
        self.assertEqual(result["completed_trades"], 1)
        for order in result["orders"]:
            self.assertEqual(set(order["payload"]), ORDER_FIELDS)
            self.assertEqual(order["payload"]["ordType"], "ioc")
            self.assertEqual(order["payload"]["tdMode"], "cash")
            self.assertFalse(order["native_attached_stop"])
            self.assertEqual(order["stop_management"], "bot-managed")
            self.assertLessEqual(dec(order["volume"]) * dec(order["price"]), 1000)

    def test_price_gap_breach_is_reported_not_clipped_and_exit_survives_entry_halt(self):
        result = risk_run(70)
        self.assertTrue(result["halted"])
        self.assertGreater(1000 - dec(result["ending_liquidation_equity"]), 10)
        self.assertEqual(result["risk"]["budget_breaches"], 1)
        self.assertGreater(result["risk"]["stop_execution_overruns"], 0)
        self.assertGreater(dec(result["risk"]["max_observed_position_loss"]), 10)
        self.assertTrue(
            any(o["side"] == "sell" and o["reason"] == "Stop" for o in result["orders"])
        )
        self.assertLess(dec(result["retained_quantity"]), PAIR.minimum)
        self.assertFalse(result["risk"]["guaranteed_loss_cap"])
        self.assertFalse(result["risk"]["native_attached_stops_supported_by_kairos"])

    def test_partial_IOC_risk_uses_actual_fill_and_cash_inventory_reconciles(self):
        result = risk_run(95, previous_volume=".5")
        buy = result["orders"][0]
        self.assertLess(dec(buy["filled"]), dec(buy["volume"]))
        self.assertEqual(buy["status"], "canceled")
        self.assertLess(
            dec(buy["risk_plan"]["filled_stop_risk"]), dec(buy["risk_plan"]["requested_stop_risk"])
        )
        self.assertLessEqual(dec(result["risk"]["max_filled_stop_risk"]), 10)
        net = sum(
            (
                dec(o["filled"]) * (1 if o["side"] == "buy" else -1)
                + sum(
                    (
                        dec(x["fee_signed"])
                        for x in o.get("executions", [])
                        if x["fee_currency"] == "BTC"
                    ),
                    ZERO,
                )
                for o in result["orders"]
            ),
            ZERO,
        )
        self.assertEqual(net, dec(result["retained_quantity"]))

    def test_risk_greater_than_one_percent_is_rejected(self):
        with self.assertRaises(SafetyError):
            stop_risk_plan(
                PAIR,
                dec(100),
                dec(95),
                dec(1000),
                dec(1000),
                dec(1000),
                ZERO,
                dec(100),
                SCENARIO,
                10,
                dec(1000),
                dec(1000),
                dec("1.01"),
            )
