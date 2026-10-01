import copy
import json
import math
import unittest
from pathlib import Path

from kairos.domain import SafetyError, dec
from kairos.research.artifacts import utc
from research.broad_fees import period_plan, pool, progression_gate
from research.kraken_daily import DAY, price_index
from tests.test_kraken_daily import fixture


class BroadFeeTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(Path("research/BROAD_PLAN.json").read_text())

    def test_fee_attribution_matches_closed_form_without_changing_decisions(self):
        plan, rows = fixture()
        low = price_index(rows, plan, "momentum", self.plan["costs"]["alpaca25"])
        high = price_index(rows, plan, "momentum", self.plan["matched_2025_control"])
        self.assertEqual(low["decisions"], high["decisions"])
        self.assertGreater(low["signal_closed_episodes"], 1)
        n = low["signal_closed_episodes"] + low["terminal_censored_episodes"]
        expected = (
            (1 - dec(".0025")) / (1 + dec(".0025")) / ((1 - dec(".004")) / (1 + dec(".004")))
        ) ** n
        self.assertAlmostEqual(
            float(dec(low["ending_index"]) / dec(high["ending_index"])), float(expected), places=13
        )
        self.assertGreater(low["net_index_change_pct"], high["net_index_change_pct"])

    def test_annual_windows_do_not_inherit_prior_positions_or_stitch_boundaries(self):
        start = utc("2017-12-01T00:00:00Z")
        end = utc("2021-01-01T00:00:00Z")
        rows = [
            {
                "start": t,
                "open": str(100 + 10 * math.sin(i / 3)),
                "close": str(100 + 10 * math.sin(i / 3)),
            }
            for i, t in enumerate(range(start, end, DAY))
        ]
        outputs = []
        for year, days in ((2019, 364), (2020, 365)):
            plan = period_plan(self.plan, year)
            full = price_index(rows, plan, "momentum", self.plan["costs"]["alpaca25"])
            local = [
                r
                for r in rows
                if utc(f"{year - 1}-12-01T00:00:00Z")
                <= r["start"]
                < utc(f"{year + 1}-01-01T00:00:00Z")
            ]
            isolated = price_index(local, plan, "momentum", self.plan["costs"]["alpaca25"])
            self.assertEqual(full, isolated)
            self.assertEqual(full["days"], days)
            outputs.append(full)
        pooled = pool(outputs)
        self.assertEqual(len(pooled), 729)
        self.assertNotIn(str(utc("2020-01-01T00:00:00Z")), pooled)
        self.assertIn(str(utc("2019-12-31T00:00:00Z")), pooled)
        self.assertIn(str(utc("2020-01-02T00:00:00Z")), pooled)

    def test_pool_rejects_repeated_observations_and_consumed_period(self):
        sample = {"daily_returns": {"1514851200": 0.01}}
        with self.assertRaises(SafetyError):
            pool([sample, sample])
        with self.assertRaises(SafetyError):
            pool([{"daily_returns": {str(utc("2026-01-01T00:00:00Z")): 0.01}}])
        with self.assertRaises(SafetyError):
            period_plan(self.plan, 2026)

    def test_progression_requires_complete_family_stress_consistency_and_bounds(self):
        years = {
            str(y): {
                s: {
                    "indices": {
                        "momentum": {
                            "days": 364,
                            "signal_closed_episodes": 20,
                            "net_index_change_pct": 10 if y < 2024 else -5,
                        }
                    }
                }
                for s in ("alpaca25", "alpaca25_friction_stress")
            }
            for y in self.plan["years"]
        }
        bounds = {"cash": {"lower": 0.001}, "passive": {"lower": 0.001}}
        self.assertTrue(progression_gate(years, bounds, self.plan)["passed"])
        for bad in (
            {},
            {"cash": {"lower": 0.001}},
            {"cash": {"lower": None}, "passive": {"lower": 0.001}},
            {"cash": {"lower": 0.001}, "passive": {"lower": -0.001}},
        ):
            self.assertFalse(progression_gate(years, bad, self.plan)["passed"])
        changed = copy.deepcopy(years)
        changed["2022"]["alpaca25_friction_stress"]["indices"]["momentum"][
            "net_index_change_pct"
        ] = -5
        self.assertFalse(progression_gate(changed, bounds, self.plan)["passed"])
        del changed["2019"]
        with self.assertRaises(SafetyError):
            progression_gate(changed, bounds, self.plan)
