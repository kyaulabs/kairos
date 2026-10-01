import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from kairos.domain import SafetyError
from kairos.research.artifacts import Artifacts, Registry, utc
from kairos.research.data import Bar, available_history, collect, purged_labels, validate_bars


def bars(n=200, start=0):
    return tuple(
        Bar(
            start + i * 3600,
            start + (i + 1) * 3600,
            start + (i + 1) * 3600 + 60,
            "100",
            "101",
            "99",
            "100",
            "10",
        )
        for i in range(n)
    )


class ResearchDataTests(unittest.TestCase):
    def test_future_and_delayed_bars_cannot_enter_information_set(self):
        data = bars()
        original = available_history(data, 10 * 3600 + 60, 5)
        changed = data[:10] + tuple(replace(b, close="1000", high="1001") for b in data[10:])
        self.assertEqual(original, available_history(changed, 10 * 3600 + 60, 5))
        self.assertEqual(original[-1].end, 10 * 3600)
        self.assertNotIn(data[9], available_history(data, 10 * 3600 + 59, 5))
        late = data[:9] + (replace(data[9], available=999999),) + data[10:]
        self.assertFalse(available_history(late, 11 * 3600 + 60, 5))
        self.assertFalse(available_history(data[:10], 12 * 3600, 5))

    def test_gaps_duplicates_invalid_ohlc_and_naive_time_fail(self):
        data = bars(10)
        self.assertFalse(available_history(data[:5] + data[6:], 10 * 3600 + 60, 10))
        for bad in (
            data + data[-1:],
            (replace(data[0], low="0"),),
            (replace(data[0], volume="NaN"),),
            (replace(data[0], available=0),),
        ):
            with self.assertRaises(SafetyError):
                validate_bars([asdict(b) for b in bad])
        with self.assertRaises(SafetyError):
            utc("2025-01-01")
        self.assertEqual(utc("2025-01-01T00:00:00Z"), utc("2024-12-31T19:00:00-05:00"))

    def test_label_intervals_and_embargo_are_not_random_row_splits(self):
        samples = [(10, 20, [], 1), (20, 31, [], 0), (25, 29, [], 1), (40, 45, [], 0)]
        self.assertEqual(purged_labels(samples, 0, 40, 10), [samples[0], samples[2]])
        self.assertEqual(purged_labels(samples, 30, 50, 0), [samples[3]])

    def test_objects_are_immutable_and_corruption_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            store = Artifacts(root)
            key = store.put({"a": 1})
            self.assertEqual(store.put({"a": 1}), key)
            self.assertNotEqual(store.put({"a": 2}), key)
            (Path(root) / (key + ".json")).write_text("{}")
            with self.assertRaises(SafetyError):
                store.get(key)
            with self.assertRaises(SafetyError):
                store.put({"a": 1})
            with self.assertRaises(SafetyError):
                store.get("../private")

    def test_failures_interrupts_and_repeated_attempts_are_retained(self):
        with tempfile.TemporaryDirectory() as root:
            registry = Registry(root)
            for exc in (ValueError, KeyboardInterrupt):
                with self.assertRaises(exc), registry.trial({"candidate": "same"}):
                    raise exc("deliberate test")
            with registry.trial({"candidate": "same"}):
                pass
            self.assertEqual(
                [r["kind"] for r in registry.entries()],
                ["started", "failed", "started", "failed", "started", "completed"],
            )
            with self.assertRaises(sqlite3.IntegrityError):
                registry.db.execute("DELETE FROM journal")
            registry.close()
            registry = Registry(root)
            self.assertEqual(len(registry.entries()), 6)
            registry.close()

    def test_seal_mismatch_and_second_final_even_under_new_study_fail(self):
        with tempfile.TemporaryDirectory() as root:
            r = Registry(root)
            spec = {"holdout": "fixed-dataset-window", "code": "abc", "model": "def"}
            r.seal("study", spec)
            with self.assertRaises(SafetyError):
                r.claim_final("study", {**spec, "code": "changed"}, "wrong")
            r.claim_final("study", spec, "first")
            r.seal("renamed-study", spec)
            with self.assertRaises(SafetyError):
                r.claim_final("renamed-study", spec, "second")
            r.close()

    def test_registry_refuses_unrelated_database(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "research.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE private (value TEXT)")
            db.close()
            before = path.read_bytes()
            with self.assertRaises(SafetyError):
                Registry(root)
            self.assertEqual(path.read_bytes(), before)

    def test_collector_records_vintage_and_separates_final_values(self):
        plan = json.loads(Path("research/PLAN.json").read_text())
        start = utc(plan["data_start"])

        def response(_):
            return {
                "bars": {
                    s: [{"t": plan["data_start"], "o": 100, "h": 101, "l": 99, "c": 100, "v": 2}]
                    for s in plan["universe"]
                },
                "next_page_token": None,
            }

        with tempfile.TemporaryDirectory() as root:
            r = Registry(root)
            key = collect(r, plan, response, lambda: utc(plan["final_end"]) + 10)
            manifest = r.artifacts.get(key)
            self.assertFalse(manifest["historical_revisions_known"])
            part = r.artifacts.get(manifest["partitions"]["BTC/USD"]["development"])
            self.assertEqual(part[0]["available"], start + 3660)
            self.assertEqual(r.artifacts.get(manifest["partitions"]["BTC/USD"]["final"]), [])
            self.assertLess(manifest["quality"]["BTC/USD"]["development"]["coverage"], 0.99)
            r.close()

    def test_collector_keeps_failed_pagination_in_registry(self):
        plan = json.loads(Path("research/PLAN.json").read_text())
        with tempfile.TemporaryDirectory() as root:
            r = Registry(root)
            with self.assertRaises(SafetyError):
                collect(
                    r,
                    plan,
                    lambda _: {"bars": {}, "next_page_token": "repeated"},
                    lambda: utc(plan["final_end"]) + 10,
                )
            self.assertEqual(r.entries()[-1]["kind"], "failed")
            self.assertEqual(sum(x["kind"] == "download_page" for x in r.entries()), 2)
            r.close()
