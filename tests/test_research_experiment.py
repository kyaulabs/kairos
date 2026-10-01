import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from kairos.domain import SafetyError, dec
from kairos.research.artifacts import Registry, utc
from kairos.research.execution import Scenario, simulate
from kairos.research.signals import allocation, feature_series, fit_quality, probability
from kairos.research.statistics import daily_returns, paired_interval, selection_diagnostics
from tests.test_research_data import bars


def plan():
    result = json.loads(Path("research/PLAN.json").read_text())
    for scenario in result["scenarios"].values():
        scenario.update(reject_probability=0, partial_probability=0, participation=1)
    result["assumed_market_rules"]["minimum_usd"] = "1"
    return result


def views(data):
    return [
        {
            "momentum": 0.1 if i else -0.1,
            "day_return": 0.01,
            "volatility": 0.5,
            "instability": 1,
            "information_end": b.end,
            "decision_at": b.available,
        }
        for i, b in enumerate(data)
    ]


class ResearchExperimentTests(unittest.TestCase):
    def execute(self, data, config=None, signals=None, **kwargs):
        return simulate(
            data,
            signals or views(data),
            config or plan(),
            "BTC/USD",
            "momentum",
            "base",
            data[0].start,
            data[-1].end,
            **kwargs,
        )

    def test_passive_touch_does_not_fill_and_miss_has_no_taker_fallback(self):
        data = list(bars(10))
        data[3] = replace(data[3], low="99.95")
        result = self.execute(data)
        self.assertEqual(len(result["orders"]), 1)
        self.assertEqual(result["orders"][0]["filled"], "0")
        self.assertEqual(result["open_quantity"], "0")
        data[3] = replace(data[3], low="99.94")
        filled = self.execute(data)
        order = filled["orders"][0]
        self.assertGreater(dec(order["filled"]), 0)
        self.assertGreater(order["submitted"], order["created"])
        self.assertEqual(order["confirmed"], data[3].end)
        self.assertEqual(
            dec(order["fee"]), dec(order["filled"]) * dec(order["price"]) * dec(".0025")
        )

    def test_starting_in_positive_signal_does_not_force_entry(self):
        data = bars(10)
        signals = views(data)
        signals[0]["momentum"] = 0.1
        self.assertEqual(self.execute(data, signals=signals)["orders"], [])

    def test_full_and_partial_fills_preserve_cash_and_inventory(self):
        for partial in (0, 1):
            config, data = plan(), list(bars(24))
            config["scenarios"]["base"]["partial_probability"] = partial
            data[4] = replace(data[4], low="90")
            result = self.execute(data, config)
            cash, quantity, fees = dec(500), dec(0), dec(0)
            for order in result["orders"]:
                filled, price, fee = map(dec, (order["filled"], order["price"], order["fee"]))
                sign = 1 if order["side"] == "buy" else -1
                quantity += sign * filled
                cash -= sign * filled * price + fee
                fees += fee
                self.assertGreaterEqual(quantity, 0)
                self.assertLessEqual(filled * price, 100)
            self.assertEqual(dec(result["cash"]), cash)
            self.assertEqual(dec(result["open_quantity"]), quantity)
            self.assertEqual(dec(result["fees"]), fees)
            if partial:
                self.assertGreater(result["partial_fills"], 0)

    def test_protection_never_calls_or_waits_for_quality_model(self):
        config, data = plan(), bars(5)
        state = Scenario(config, "BTC/USD", "quality", "base", None, 1)
        state.qty, state.stop, state.opened = dec(".5"), dec(100), data[0].start
        with patch(
            "kairos.research.execution.allocation", side_effect=AssertionError("model veto")
        ):
            state.observe(data[1], views(data)[1])
        self.assertEqual(state.pending["side"], "sell")
        self.assertIn("stop", state.pending["reason"])

    def test_daily_loss_halt_does_not_restart_the_next_day(self):
        config, data = plan(), list(bars(60))
        config["risk"]["daily_loss_usd"] = "1"
        data[4] = replace(data[4], open="90", low="89", close="90")
        result = self.execute(data, config)
        self.assertTrue(result["halted"])
        self.assertEqual(sum(o["side"] == "buy" for o in result["orders"]), 1)
        self.assertTrue(all(d["halted"] for d in result["decisions"] if d["at"] > 86400))

    def test_exposure_trim_is_bounded_and_does_not_liquidate_to_zero(self):
        data = list(bars(10))
        for i in range(4, 10):
            data[i] = replace(data[i], open="200", high="201", low="199", close="200")
        result = self.execute(data)
        trims = [o for o in result["orders"] if o["reason"] == "exposure trim"]
        self.assertTrue(trims)
        self.assertGreater(dec(result["open_quantity"]), 0)
        self.assertLessEqual(dec(result["open_quantity"]) * 200, 80)
        self.assertEqual(result["completed_round_trips"], 0)

    def test_rejections_do_not_spend_cash_or_invent_inventory(self):
        config = plan()
        config["scenarios"]["base"]["reject_probability"] = 1
        result = self.execute(bars(10), config)
        self.assertEqual(result["cash"], "500")
        self.assertEqual(result["open_quantity"], "0")
        self.assertEqual(result["rejections"], 1)

    def test_opening_cannot_consume_unpublished_liquidity_or_future_cancel(self):
        config, data = plan(), bars(5)
        state = Scenario(config, "BTC/USD", "momentum", "base", None, 1)
        state.qty, state.stop, state.opened = dec(".5"), dec(97), 0
        state.pending = {"created": 1, "due": data[2].start, "side": "sell", "reason": "stop"}
        # Previous bar has completed but arrives one minute AFTER this order's opening.
        state.opening(data[2], data[1])
        self.assertEqual(state.qty, dec(".5"))
        self.assertEqual(state.orders[-1]["filled"], "0")
        # Correct event ordering: an IOC fill at t happens before a signal arriving at t+60.
        state.pending = {"created": 1, "due": data[3].start, "side": "sell", "reason": "stop"}
        state.opening(data[3], data[1])
        self.assertEqual(state.qty, 0)

    def test_unavailable_bar_features_and_future_perturbations_do_not_rewrite_decisions(self):
        data = tuple(
            replace(b, close=str(100 + math.sin(i / 10)), high="102", low="98")
            for i, b in enumerate(bars(500))
        )
        config = plan()
        original = feature_series(data, config)
        changed = data[:350] + tuple(replace(b, close="900", high="901") for b in data[350:])
        future = feature_series(changed, config)
        self.assertEqual(original[:350], future[:350])
        first = self.execute(data, config, original)
        second = self.execute(changed, config, future)
        cutoff = data[350].start
        self.assertEqual(
            [d for d in first["decisions"] if d["at"] < cutoff],
            [d for d in second["decisions"] if d["at"] < cutoff],
        )
        self.assertEqual(
            [o for o in first["orders"] if o.get("confirmed", cutoff) < cutoff],
            [o for o in second["orders"] if o.get("confirmed", cutoff) < cutoff],
        )

    def test_fit_scaling_and_calibration_exclude_validation_and_immature_labels(self):
        config = plan()
        begin, split, last = [
            utc(config[k]) for k in ("fit_start", "calibration_start", "validation_start")
        ]
        train = [
            (begin + i * 3600, begin + (i + 25) * 3600, [math.sin(i), i % 5, 0.5], i % 2)
            for i in range(120)
        ]
        calibration = [
            (split + i * 3600, split + (i + 25) * 3600, [math.sin(i), i % 5, 0.5], i % 2)
            for i in range(120)
        ]
        future = [(last + i * 3600, last + (i + 25) * 3600, [1e9, 1e9, 1e9], 1) for i in range(20)]
        model = fit_quality(train + calibration, config)
        self.assertEqual(model, fit_quality(train + calibration + future, config))
        immature = (split - 3600, split + 3600, [1e12, 1e12, 1e12], 1)
        self.assertEqual(model, fit_quality(train + calibration + [immature], config))
        self.assertLess(model["fit_latest_label"], split - config["embargo_hours"] * 3600)
        self.assertLess(model["available_at"], last)
        self.assertTrue(0 < probability(model, [0, 0, 0.5]) < 1)
        with self.assertRaises(SafetyError):
            allocation(
                "quality",
                {"momentum": 0.1, "decision_at": begin},
                config,
                config["scenarios"]["base"],
                model,
            )

    def test_uncertainty_is_paired_reproducible_and_does_not_invent_missing_days(self):
        left = {str(i * 86400): 0.002 + (i % 3) * 0.0001 for i in range(40)}
        right = {k: v - 0.001 for k, v in left.items()}
        options = dict(seed=12, samples=128, block=7, alpha=0.01)
        result = paired_interval(left, right, **options)
        self.assertEqual(result, paired_interval(left, right, **options))
        self.assertAlmostEqual(result["lower"], 0.001)
        self.assertAlmostEqual(result["upper"], 0.001)
        marks = [{"at": 86400, "equity": 501}, {"at": 3 * 86400, "equity": 502}]
        self.assertEqual(list(daily_returns(marks, 500, 0)), ["86400"])
        self.assertFalse(selection_diagnostics([[0] * 40, [0] * 40], 2)["available"])

    def test_selection_diagnostics_use_equal_design_blocks_and_disclose_truncation(self):
        columns = [[0.001 * j + 0.01 * math.sin(i / (j + 1)) for i in range(83)] for j in (1, 2, 3)]
        result = selection_diagnostics(columns, 3)
        self.assertTrue(result["available"])
        self.assertEqual(result["combinations"], 70)
        self.assertEqual(result["trailing_days_omitted"], 3)
        self.assertTrue(0 <= result["pbo"] <= 1)

    def test_full_family_freezes_models_and_consumes_final_once(self):
        from dataclasses import asdict
        from datetime import UTC, datetime

        from kairos.research.experiment import run, seal

        config = plan()
        origin = utc("2024-01-01T00:00:00Z")
        for key, hour in [
            ("data_start", 0),
            ("fit_start", 200),
            ("calibration_start", 700),
            ("validation_start", 1000),
            ("final_start", 1500),
            ("final_end", 1900),
        ]:
            config[key] = datetime.fromtimestamp(origin + hour * 3600, UTC).isoformat()
        config["quality_fit"]["epochs"] = 5
        config["criteria"]["bootstrap_samples"] = 128
        data = [
            replace(
                b,
                open=str(100 + math.sin(i / 10)),
                close=str(100 + math.sin(i / 10)),
                high="102",
                low="98",
                volume="100",
            )
            for i, b in enumerate(bars(1900, origin))
        ]
        with tempfile.TemporaryDirectory() as root:
            registry = Registry(root)
            partitions = {
                symbol: {
                    "development": registry.artifacts.put([asdict(b) for b in data[:1500]]),
                    "final": registry.artifacts.put([asdict(b) for b in data[1500:]]),
                }
                for symbol in config["universe"]
            }
            dataset = registry.artifacts.put(
                {
                    "plan": registry.artifacts.put(config),
                    "partitions": partitions,
                    "quality": {
                        symbol: {"development": {"coverage": 1}, "final": {"coverage": 1}}
                        for symbol in config["universe"]
                    },
                }
            )
            development = run(registry, config, dataset, "development")
            seal(registry, config, dataset, development)
            final = run(registry, config, dataset, "final", development)
            a, b = registry.artifacts.get(development), registry.artifacts.get(final)
            self.assertEqual(b["conclusion"], "inconclusive")
            self.assertEqual(set(b["hypotheses"]), set(config["arms"][1:]))
            for symbol in config["universe"]:
                self.assertEqual(a["assets"][symbol]["model"], b["assets"][symbol]["model"])
                self.assertEqual(
                    a["assets"][symbol]["risk_scales"], b["assets"][symbol]["risk_scales"]
                )
                self.assertFalse(
                    b["assets"][symbol]["scenarios"]["base"]["design_diagnostics"]["available"]
                )
            with self.assertRaises(SafetyError):
                run(registry, config, dataset, "final", development)
            registry.close()

    def test_final_failure_is_consumed_before_final_dataset_access(self):
        from kairos.research.experiment import run, specification

        config = plan()
        with tempfile.TemporaryDirectory() as root:
            registry = Registry(root)
            spec = specification(config, "0" * 64, "1" * 64)
            registry.seal(config["name"], spec)
            with self.assertRaises(FileNotFoundError):
                run(registry, config, "0" * 64, "final", "1" * 64)
            with self.assertRaises(SafetyError):
                run(registry, config, "0" * 64, "final", "1" * 64)
            self.assertEqual(sum(e["kind"] == "final_opened" for e in registry.entries()), 1)
            self.assertEqual(sum(e["kind"] == "failed" for e in registry.entries()), 2)
            registry.close()
