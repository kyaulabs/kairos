import unittest

from kairos.domain import Pair, SafetyError, dec
from kairos.replay import evaluate, replay
from kairos.settings import DEFAULTS


def rows(count=150):
    result = []
    for i in range(count):
        close = dec(100) + max(0, i - 29)
        result.append(
            [i * 3600, str(close), str(close + 1), str(close - 1), str(close), str(close), "10", 2]
        )
    return result


class ReplayTests(unittest.TestCase):
    def test_flat_market_never_forces_trades(self):
        data = [[i * 3600, "100", "101", "99", "100", "100", "10", 2] for i in range(150)]
        report = evaluate(data, DEFAULTS, fee_bps=80, spread_bps=2)
        for segment in (report["train"], report["holdout"]):
            self.assertEqual(segment["completed_trades"], 0)
            self.assertEqual(dec(segment["net_return_pct"]), 0)

    def test_fee_slippage_and_stop_losses_are_charged_on_full_trades(self):
        data = rows()
        data[33][3] = "90"  # Stop after a fresh entry; the recovered close cannot hide it.
        result = replay(data, DEFAULTS, fee_bps=80, spread_bps=2)
        first = result["trades"][0]
        self.assertEqual(first["opened_at"], data[32][0])
        self.assertEqual(first["closed_bar"], data[33][0])
        self.assertEqual(result["completed_trades"], 1)  # No re-entry on the still-active signal.
        self.assertIn("stop", first["reason"])
        self.assertLess(dec(first["net_pnl"]), -dec(3))
        self.assertGreater(dec(result["paid_fees"]), 0)
        self.assertGreater(dec(result["bar_close_max_drawdown_pct"]), 0)

    def test_new_entry_can_stop_within_its_opening_bar(self):
        data = rows()
        data[32][3] = "90"
        result = replay(data, DEFAULTS, fee_bps=80, spread_bps=2)
        self.assertEqual(result["trades"][0]["opened_at"], result["trades"][0]["closed_bar"])

    def test_future_changes_cannot_rewrite_completed_trades(self):
        data = rows()
        data[33][3] = "90"
        first = replay(data, DEFAULTS, fee_bps=80, spread_bps=2)["trades"][0]
        for row in data[100:]:
            row[1:6] = ["1", "1", "1", "1", "1"]
        self.assertEqual(first, replay(data, DEFAULTS, fee_bps=80, spread_bps=2)["trades"][0])

    def test_signal_already_active_at_start_never_forces_an_entry(self):
        data = [
            [i * 3600, str(100 + i), str(101 + i), str(99 + i), str(100 + i), str(100 + i), "10", 2]
            for i in range(150)
        ]
        result = replay(data, DEFAULTS, fee_bps=80, spread_bps=2)
        self.assertEqual(result["completed_trades"], 0)
        self.assertEqual(dec(result["open_quantity"]), 0)
        self.assertEqual(dec(result["paid_fees"]), 0)

    def test_shared_executor_applies_growth_headroom_and_preserves_a_trimmed_position(self):
        for reinvest in (False, True):
            with self.subTest(reinvest=reinvest):
                settings = {**DEFAULTS, "max_exposure": "10", "reinvest_profits": reinvest}
                result = replay(rows(), settings, fee_bps=80, spread_bps=2, initial=250, budget=10)
                self.assertFalse(result["halted"], result["halt_reason"])
                orders = result["orders"]
                self.assertLessEqual(dec(orders[0]["cost"]), 8)
                trims = [o for o in orders if "exposure trim" in o["reason"]]
                self.assertTrue(trims)
                self.assertEqual(result["completed_trades"], 0)
                self.assertGreater(dec(result["open_quantity"]), 0)
                for order in orders:
                    self.assertLessEqual(
                        dec(order["volume"]) * dec(order["price"]),
                        dec(order["risk_at_submission"]["order_cap"]),
                    )
                if reinvest:
                    self.assertGreater(dec(trims[0]["risk_at_submission"]["exposure_cap"]), 10)
                else:
                    self.assertEqual(dec(trims[0]["risk_at_submission"]["exposure_cap"]), 10)

    def test_large_gap_uses_multiple_bounded_trims_then_original_deadline(self):
        data = rows(40)
        data[32][2], data[32][4] = "300", "298"
        settings = {
            **DEFAULTS,
            "max_exposure": "10",
            "reinvest_profits": False,
            "htf_max_hold_seconds": 3600,
        }
        result = replay(data, settings, fee_bps=80, spread_bps=2, initial=250, budget=10)
        self.assertGreaterEqual(sum("exposure trim" in o["reason"] for o in result["orders"]), 2)
        self.assertTrue(any("deadline" in trade["reason"] for trade in result["trades"]))
        for order in result["orders"]:
            self.assertLessEqual(dec(order["volume"]) * dec(order["price"]), 10)

    def test_daily_loss_reduces_and_does_not_automatically_restart_after_halt(self):
        data = rows()
        data[33][3] = "95"
        settings = {**DEFAULTS, "max_exposure": "100", "daily_loss": "2", "htf_stop_bps": "1000"}
        result = replay(data, settings, fee_bps=80, spread_bps=2, initial=250)
        self.assertIn("daily loss", result["trades"][0]["reason"])
        self.assertTrue(result["halted"])
        self.assertIn("loss limit", result["halt_reason"])
        self.assertEqual(len(result["orders"]), 2)
        self.assertEqual(dec(result["open_quantity"]), 0)

    def test_supplied_market_minimums_are_enforced_by_the_paper_executor(self):
        pair = Pair("TESTUSD", "TEST/USD", "TEST", "ZUSD", dec(".01"), dec(".01"), dec(2), dec(250))
        result = replay(rows(), DEFAULTS, fee_bps=80, spread_bps=2, pair=pair)
        self.assertEqual(result["orders"], [])
        self.assertFalse(result["halted"])
        self.assertEqual(dec(result["ending_liquidation_equity"]), 1000)

    def test_missing_bars_and_invalid_costs_fail_closed(self):
        data = rows()
        del data[70]
        with self.assertRaises(SafetyError):
            replay(data, DEFAULTS, fee_bps=80, spread_bps=2)
        with self.assertRaises(SafetyError):
            replay(rows(), DEFAULTS, fee_bps=-1, spread_bps=2)
        with self.assertRaises(SafetyError):
            evaluate(rows(50), DEFAULTS, fee_bps=80, spread_bps=2)
