import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from kairos.domain import SafetyError, dec
from kairos.meta_signals import FAMILIES, FRAMES, Context, MetaSignals, ParentImpulse
from kairos.research_signals import Setup


def rows(count=3000):
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
                i * 900,
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
            for frame in FRAMES:
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

    def test_forming_bars_and_unsupported_frames_fail(self):
        with self.assertRaises(SafetyError):
            MetaSignals("fib-meta", 240)
        with self.assertRaises(SafetyError):
            MetaSignals("vwap-meta", 15).push(rows(1)[0], 899)
