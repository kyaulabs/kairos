import copy
import json
import math
import unittest
from pathlib import Path

from kairos.domain import SafetyError, dec
from kairos.research.artifacts import utc
from research.kraken_daily import DAY, parse_daily, price_index, signal


def fixture():
    plan = json.loads(Path("research/KRAKEN_PLAN.json").read_text())
    start = utc("2025-01-01T00:00:00Z")
    rows = [
        {
            "start": start + i * DAY,
            "open": str(100 + 10 * math.sin(i / 3)),
            "close": str(100 + 10 * math.sin(i / 3)),
        }
        for i in range(50)
    ]
    from datetime import UTC, datetime

    plan["evaluation_start"] = datetime.fromtimestamp(start + 10 * DAY, UTC).isoformat()
    plan["last_mark"] = datetime.fromtimestamp(start + 49 * DAY, UTC).isoformat()
    return plan, rows


class KrakenDailyTests(unittest.TestCase):
    def test_excluded_year_is_filtered_before_price_decoding(self):
        start = utc("2025-12-30T00:00:00Z")
        body = (
            f"{start},100,101,99,100,3,2\n"
            f"{start + DAY},100,101,99,100,3,2\n"
            f"{start + 2 * DAY},DO_NOT_USE,NAN,NAN,NAN,NAN,NAN\n"
        ).encode()
        rows = parse_daily(body, start, start + 2 * DAY)
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[-1]["start"], utc("2026-01-01T00:00:00Z"))
        with self.assertRaises(SafetyError):
            parse_daily(body, start, start + 3 * DAY)

    def test_missing_duplicate_and_invalid_candles_are_not_repaired(self):
        row = "0,100,101,99,100,3,2\n"
        for body, end in ((row, 2 * DAY), (row + row, DAY), (row.replace("101", "98"), DAY)):
            with self.assertRaises(SafetyError):
                parse_daily(body.encode(), 0, end)

    def test_publication_delay_excludes_just_closed_and_current_bars(self):
        plan, rows = fixture()
        expected = signal(rows, 20, 7, 60)
        changed = copy.deepcopy(rows)
        changed[19]["close"] = "1000000"
        changed[20]["close"] = "0.000001"
        self.assertEqual(expected, signal(changed, 20, 7, 60))
        self.assertEqual(expected[1], rows[19]["start"] + 60)
        self.assertLess(expected[1], rows[20]["start"])

    def test_cash_and_single_passive_round_trip_match_cost_identity(self):
        plan, rows = fixture()
        for row in rows:
            row.update(open="100", close="100")
        costs = {"fee_bps": 40, "spread_bps": 10, "slippage_bps": 10}
        result = price_index(rows, plan, "passive", costs)
        expected = (
            (1 - dec(".0005"))
            * (1 - dec(".001"))
            * (1 - dec(".004"))
            / ((1 + dec(".0005")) * (1 + dec(".001")) * (1 + dec(".004")))
        )
        self.assertAlmostEqual(float(result["ending_index"]), float(expected), places=14)
        self.assertEqual(result["signal_closed_episodes"], 0)
        self.assertEqual(result["terminal_censored_episodes"], 1)
        cash = price_index(rows, plan, "cash", costs)
        self.assertEqual(dec(cash["ending_index"]), 1)
        self.assertEqual(cash["fees_per_initial_index_unit"], "0")

    def test_future_prices_cannot_rewrite_past_decisions_or_returns(self):
        plan, rows = fixture()
        costs = plan["costs"]["base_assumption"]
        a = price_index(rows, plan, "momentum", costs)
        changed = copy.deepcopy(rows)
        for row in changed[30:]:
            row.update(open="1000", close="1000")
        b = price_index(changed, plan, "momentum", costs)
        cutoff = rows[30]["start"]
        self.assertGreater(a["active_days"], 0)
        self.assertEqual(
            [d for d in a["decisions"] if d["at"] < cutoff],
            [d for d in b["decisions"] if d["at"] < cutoff],
        )
        self.assertEqual(
            {k: v for k, v in a["daily_returns"].items() if int(k) < cutoff},
            {k: v for k, v in b["daily_returns"].items() if int(k) < cutoff},
        )

    def test_higher_costs_cannot_improve_same_price_index_path(self):
        plan, rows = fixture()
        outputs = [price_index(rows, plan, "momentum", c) for c in plan["costs"].values()]
        self.assertGreater(outputs[0]["signal_closed_episodes"], 0)
        self.assertEqual(outputs[0]["decisions"], outputs[1]["decisions"])
        self.assertEqual(outputs[1]["decisions"], outputs[2]["decisions"])
        self.assertGreater(outputs[0]["net_index_change_pct"], outputs[1]["net_index_change_pct"])
        self.assertGreater(outputs[1]["net_index_change_pct"], outputs[2]["net_index_change_pct"])

    def test_consumed_period_cannot_become_a_new_mark(self):
        plan, rows = fixture()
        plan["last_mark"] = "2026-01-01T00:00:00Z"
        with self.assertRaises(SafetyError):
            price_index(rows, plan, "cash", plan["costs"]["gross"])
