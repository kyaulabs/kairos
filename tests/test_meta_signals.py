import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from kairos.domain import SafetyError, dec
from kairos.meta_signals import FAMILIES, FRAMES, Context, MetaSignals, ParentImpulse
from kairos.research_signals import Setup


def rows(count=3000, minutes=15):
    result = []
    previous = dec(10000)
    for i in range(count):
        phase = i % 96
        wave = phase if phase < 48 else 96 - phase
        close = dec(10000) + dec(i) * 2 + (wave - 24) * 35 + ((i * 17) % 23 - 11) * 12
        high = max(previous, close) + 10 + i % 17 * 4
        low = min(previous, close) - 10 - i % 19 * 5
        result.append(
            [
                i * minutes * 60,
                str(previous),
                str(high),
                str(low),
                str(close),
                str(close),
                str(1000 if i % 11 == 0 else 100 + i % 3 * 10),
                1,
            ]
        )
        previous = close
    return result


def record(setup):
    return (
        None
        if setup is None
        else [
            setup.variant,
            setup.closed_at,
            str(setup.reference),
            str(setup.stop),
            str(setup.target),
            setup.facts,
        ]
    )


def digest(values):
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class MetaSignalTests(unittest.TestCase):
    def test_frozen_research_geometry_priorities_and_restart_checkpoints(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/meta-signals.json").read_text())
        bars = rows()
        for policy in FAMILIES:
            for frame in (15, 30, 60, 360, 720):
                self.assertIn(frame, FRAMES)
                with self.subTest(policy=policy, frame=frame):
                    engine = MetaSignals(policy, frame)
                    setups = []
                    for i, row in enumerate(bars):
                        setup = engine.push(row, row[0] + 900)
                        if setup:
                            setups.append(record(setup))
                        if i in (47, 1451, 2453):
                            engine = MetaSignals.restore(
                                json.loads(json.dumps(engine.checkpoint()))
                            )
                    self.assertEqual(engine.context.engine.count, len(bars) // (frame // 15))
                    self.assertGreaterEqual(engine.context.engine.count, 50)
                    expected = fixture["expected"][f"{policy}:{frame}"]
                    self.assertEqual(digest(setups), expected["selected_sha256"])
                    self.assertEqual(dict(engine.raw_counts), expected["raw_counts"])
                    self.assertEqual(dict(engine.selected_counts), expected["selected_counts"])

    def test_reclaim_and_reversion_remain_active_with_shared_structure(self):
        base = [[i * 900, "10000", "10001", "9999", "10000", "10000", "100", 1] for i in range(79)]
        tails = {
            "vwap-reclaim": [
                ["10000", "10001", "9890", "9900", "100"],
                ["9900", "10210", "9890", "10200", "400"],
                ["10200", "10230", "10190", "10220", "100"],
            ],
            "vwap-reversion": [
                ["10000", "10001", "9999", "10000", "100"],
                ["10000", "10010", "9200", "9500", "100"],
                ["9500", "9910", "9420", "9900", "100"],
            ],
        }
        for family, tail in tails.items():
            engine = MetaSignals("vwap-meta", 15)
            stream = base + [
                [(79 + i) * 900, o, h, lo, c, c, volume, 1]
                for i, (o, h, lo, c, volume) in enumerate(tail)
            ]
            setups = [s for row in stream if (s := engine.push(row, row[0] + 900)) is not None]
            with self.subTest(family=family):
                self.assertTrue(any(s.facts["selected_strategy"] == family for s in setups))
                self.assertGreater(engine.raw_counts[family], 0)

    def test_partial_or_gapped_higher_bars_are_not_promoted(self):
        c = Context(60)
        for row in rows(4)[1:]:
            c.push(row)
        self.assertEqual(c.engine.count, 0)
        for row in rows(8)[4:]:
            c.push(row)
        self.assertEqual(c.engine.count, 1)
        self.assertEqual(c.closed_at, 7200)
        self.assertFalse(c.contiguous)
        c.push(rows(9)[-1])
        self.assertEqual(c.closed_at, 7200)
        self.assertIsNone(c.prior_slow)
        with self.assertRaises(SafetyError):
            c.push(rows(9)[-1])

    def test_all_children_advance_and_one_priority_winner_has_no_fallback(self):
        engine = MetaSignals("fib-meta", 15)
        for name, child in engine.engines.items():
            child.push = Mock(
                return_value=(
                    {},
                    Setup(name, 900, dec(100), dec(90), dec(120), {"candidate_id": name}),
                )
            )
        winner = engine.push(rows(1)[0], 900)
        self.assertEqual(winner.facts["selected_strategy"], "fib-confluence")
        self.assertEqual(set(engine.raw_counts), set(FAMILIES["fib-meta"]))
        for child in engine.engines.values():
            child.push.assert_called_once()

    def test_nested_parent_does_not_renew_expiry_and_gap_discards_it(self):
        p = ParentImpulse(15)
        parent = (0, dec(100), 1800, 2700, dec(140), 4500)
        nested = (5400, dec(110), 7200, 8100, dec(130), 9900)
        self.assertEqual(p.update(parent, 4500, dec(125), contiguous=True), parent)
        self.assertEqual(p.update(nested, 9900, dec(120), contiguous=True), parent)
        p.update(None, 4500 + 96 * 900 + 1, dec(120), contiguous=True)
        self.assertNotEqual(p.parent, parent)
        self.assertIsNone(p.update(None, 100000, dec(120), contiguous=False))

    def test_configurable_clocks_and_restart_keep_causal_background(self):
        for minutes in (5, 15, 30, 60):
            with self.subTest(minutes=minutes):
                engine = MetaSignals(
                    "fib-meta", 60, confirmation_minutes=minutes, context_minutes=240
                )
                for i, row in enumerate(rows(3000, minutes)):
                    setup = engine.push(row, row[0] + minutes * 60)
                    if setup:
                        context = setup.facts["background_context"]
                        self.assertTrue(context["bullish"])
                        self.assertLessEqual(context["closed_at"], setup.closed_at)
                        self.assertEqual(setup.facts["confirmation_minutes"], minutes)
                    if i == 2001:
                        saved = json.loads(json.dumps(engine.checkpoint()))
                        restored = MetaSignals.restore(saved)
                        self.assertEqual(saved, restored.checkpoint())
                        engine = restored
                self.assertEqual(engine.background.engine.count, 3000 * minutes // 240)
                self.assertEqual(engine.context.engine.count, 3000 * minutes // 60)
                self.assertEqual(next(iter(engine.engines.values())).count, 3000)

    def test_bullish_context_gates_arms_and_selected_entries_not_child_advancement(self):
        engine = MetaSignals("fib-meta", 60, context_minutes=240)
        native = [
            [i * 14400, str(100 + i), str(102 + i), str(99 + i), str(101 + i), "0", "10", 1]
            for i in range(60)
        ]
        asof = 60 * 14400
        engine.background.seed(native, asof)
        for name, child in engine.engines.items():
            child.push = Mock(
                return_value=(
                    {},
                    Setup(name, asof + 900, dec(160), dec(150), dec(180), {"candidate_id": name}),
                )
            )
        row = [asof, "160", "162", "159", "161", "0", "10", 1]
        selected = engine.push(row, asof + 900)
        self.assertEqual(selected.facts["selected_strategy"], "fib-confluence")
        engine.background.engine.fast = engine.background.engine.slow - 1
        for child in engine.engines.values():
            child.pending = {"expires": asof + 10000}
        self.assertIsNone(engine.push([asof + 900, *row[1:]], asof + 1800))
        for child in engine.engines.values():
            self.assertEqual(child.push.call_count, 2)
            self.assertIsNone(child.pending)
        self.assertEqual(
            dict(engine.context_rejections), {name: 1 for name in FAMILIES["fib-meta"]}
        )

    def test_native_seed_excludes_later_higher_states_and_partial_bars(self):
        native = [
            [i * 14400, "100", "105", "99", "101" if i < 60 else "10000", "0", "10", 1]
            for i in range(70)
        ]
        # The later rows deliberately carry an invalid future price: they must not enter indicators.
        context = Context(240, 5, strict_gaps=True)
        context.seed(native, 60 * 14400)
        self.assertEqual(context.engine.count, 60)
        self.assertEqual(context.engine.slow, 101)
        with self.assertRaises(SafetyError):
            context.seed(native, 70 * 14400)
        context.push([60 * 14400, "100", "105", "99", "102", "0", "10", 1])
        self.assertEqual(context.engine.count, 60)
        self.assertEqual(context.closed_at, 60 * 14400)

    def test_v1_checkpoint_loads_without_retargeting_legacy_signals(self):
        engine = MetaSignals("fib-meta", 60)
        for row in rows(100):
            engine.push(row, row[0] + 900)
        saved = engine.checkpoint()
        saved["version"] = 1
        for key in ("background", "context_minutes", "confirmation_minutes", "context_rejections"):
            saved.pop(key)
        saved["context"].pop("confirmation_minutes")
        saved["context"].pop("strict_gaps")
        restored = MetaSignals.restore(saved)
        self.assertIsNone(restored.background)
        self.assertEqual(restored.confirmation_minutes, 15)
        next_row = rows(101)[-1]
        self.assertEqual(
            record(engine.push(next_row, next_row[0] + 900)),
            record(restored.push(next_row, next_row[0] + 900)),
        )

    def test_forming_bars_and_unsupported_frames_fail(self):
        with self.assertRaises(SafetyError):
            MetaSignals("fib-meta", 120)
        with self.assertRaises(SafetyError):
            MetaSignals("vwap-meta", 15).push(rows(1)[0], 899)
