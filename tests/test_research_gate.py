import asyncio
import copy
import unittest
from datetime import UTC, datetime, timedelta

from kairos.backtest import SCENARIOS
from kairos.domain import SafetyError, dec
from kairos.research_backtest import instrument, pilot_settings, simulate
from kairos.research_gate import AssessmentGate
from kairos.research_series import canonical
from kairos.research_signals import Setup
from tests.test_research_assessment import answer, fixture
from tests.test_research_backtest import START, candle


def sample(index=0, *, minutes=5, verdict="support", latency=30):
    s = fixture(minutes=minutes)
    delta = START - 300 + index * 300 - s["as_of"]
    s["as_of"] += delta
    s["expires_at"] += delta
    s["candidate_id"] = f"{s['setup']}:{s['as_of']}"
    s["facts"]["swing_known_at"] += delta
    for bar in s["bars"]:
        bar["closed_at"] += delta
    a = answer(s, verdict)
    clock = datetime(2026, 1, 1, tzinfo=UTC)
    return s, {
        "candidate_id": s["candidate_id"],
        "input_sha256": a["input_sha256"],
        "status": "complete",
        "answer": a,
        "started_at": clock.isoformat(),
        "received_at": (clock + timedelta(seconds=latency)).isoformat(),
    }


def setup(s, closed=None):
    return Setup(
        s["setup"],
        s["as_of"] + 300 if closed is None else closed,
        dec(100),
        dec(95),
        dec(105),
        {"candidate_id": s["candidate_id"], "armed_at": s["as_of"]},
    )


def run(gate, execution="ioc", *, low=98, daily_loss="12.50", opening=100):
    s, _ = sample()

    class OneSignal:
        def push(self, row, now):
            return {}, setup(s) if row[0] + 300 == START else None

    pair = instrument()
    scenario = {**SCENARIOS["base"], "name": "base"}
    rows = [candle(START - 60, opening), candle(START, opening, low=low)]
    rows.extend(candle(t, 110, low=109) for t in range(START + 60, START + 600, 60))
    return asyncio.run(
        simulate(
            rows,
            [candle(START - 300, 100)],
            pair,
            pilot_settings(pair, 5, scenario, 1000, 1000, 1000, dec(daily_loss)),
            scenario,
            "vwap-pullback",
            START,
            START + 600,
            signals=OneSignal(),
            economic_policy="positive-target",
            entry_execution=execution,
            entry_gate=gate,
        )
    )


class AssessmentGateTests(unittest.TestCase):
    def test_response_clock_is_available_bar_plus_measured_latency_not_2026_receipt(self):
        s, r = sample(latency=400)
        gate = AssessmentGate([s], [r], "ai")
        decision = gate.evaluate(setup(s), START + 1, 5)
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["available_at"], s["as_of"] + 401)
        self.assertEqual(decision["reason"], "response_pending")

    def test_expiry_and_binding_cannot_be_revived_or_backdated(self):
        s, r = sample()
        gate = AssessmentGate([s], [r], "ai")
        self.assertTrue(gate.evaluate(setup(s, s["expires_at"]), s["expires_at"] + 1, 5)["allowed"])
        self.assertEqual(
            gate.evaluate(setup(s, s["expires_at"] + 1), s["expires_at"] + 2, 5)["reason"],
            "expired",
        )
        bad = setup(s)
        bad.facts["armed_at"] -= 1
        with self.assertRaises(SafetyError):
            gate.evaluate(bad, START + 1, 5)
        with self.assertRaises(SafetyError):
            gate.evaluate(setup(s), START - 1, 5)
        self.assertFalse(gate.evaluate(setup(s), START + 1, 15)["allowed"])

    def test_same_candidate_id_in_two_intervals_keeps_separate_bindings(self):
        a, ar = sample(minutes=5)
        b, br = sample(minutes=15, verdict="oppose")
        gate = AssessmentGate([a, b], [ar, br], "ai")
        self.assertTrue(gate.evaluate(setup(a), START + 1, 5)["allowed"])
        self.assertFalse(gate.evaluate(setup(b), START + 1, 15)["allowed"])
        self.assertTrue(
            AssessmentGate([b], [br], "availability").evaluate(setup(b), START + 1, 15)["allowed"]
        )

    def test_rejected_incomplete_or_tampered_results_are_not_silently_permits(self):
        s, r = sample()
        for changes in (
            {"status": "failed"},
            {"input_sha256": "b" * 64},
            {"candidate_id": "other"},
            {"received_at": "2025-01-01T00:00:00+00:00"},
            {"received_at": "2026-01-01T00:00:30"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                AssessmentGate([s], [{**r, **changes}], "ai")
        with self.assertRaises(ValueError):
            AssessmentGate([s], [], "ai")
        with self.assertRaises(ValueError):
            AssessmentGate([s, s], [r, r], "ai")
        bad = copy.deepcopy(r)
        bad["answer"]["checks"][0]["evidence"] = ["/facts/not_supplied"]
        with self.assertRaises(ValueError):
            AssessmentGate([s], [bad], "ai")

    def test_hash_controls_match_per_case_permit_counts_and_ignore_input_order(self):
        pairs = [
            sample(i, minutes=m, verdict="support" if i % 3 == 0 else "oppose")
            for m in (5, 15)
            for i in range(10)
        ]
        snapshots, results = map(list, zip(*pairs, strict=True))
        sets = []
        for seed in range(4):
            gate = AssessmentGate(snapshots, results, "suppression", seed)
            again = AssessmentGate(
                list(reversed(snapshots)), list(reversed(results)), "suppression", seed
            )
            self.assertEqual(gate.selected, again.selected)
            for m in (5, 15):
                self.assertEqual(sum(key[1] == m for key in gate.selected), 4)
            sets.append(frozenset(gate.selected))
        self.assertGreater(len(set(sets)), 1)

    def test_permit_does_not_change_ioc_or_maker_accounting_or_protection(self):
        s, r = sample()
        for execution in ("ioc", "maker-best-bid-30s"):
            for low in (98, 80):
                with self.subTest(execution=execution, low=low):
                    a = run(None, execution, low=low, daily_loss=".01")
                    b = run(AssessmentGate([s], [r], "ai"), execution, low=low, daily_loss=".01")
                    for c in b["diagnostics"]["candidates"]:
                        c.pop("entry_gate", None)
                    self.assertEqual(canonical(a), canonical(b))
                    self.assertTrue(any(o["side"] == "sell" for o in b["orders"]))

    def test_monitor_permit_cannot_override_price_or_target_cost_eligibility(self):
        s, r = sample()
        for opening, expected in ((110, "blocked_price_plan"), (104.7, "blocked_target_economics")):
            with self.subTest(opening=opening):
                case = run(AssessmentGate([s], [r], "ai"), opening=opening)
                self.assertEqual(case["orders"], [])
                self.assertEqual(case["diagnostics"]["candidates"][0]["outcome"], expected)

    def test_empty_success_cohort_fails_closed_without_inventing_response_latency(self):
        gate = AssessmentGate([], [], "ai")
        case = run(gate)
        self.assertEqual(case["orders"], [])
        decision = case["diagnostics"]["candidates"][0]["entry_gate"]
        self.assertEqual(decision["reason"], "unassessed")
        self.assertNotIn("available_at", decision)

    def test_abstention_or_late_response_never_queues_a_missed_entry(self):
        for kwargs, expected in (
            ({"verdict": "oppose"}, "abstain"),
            ({"latency": 400}, "response_pending"),
        ):
            s, r = sample(**kwargs)
            c = run(AssessmentGate([s], [r], "ai"))
            self.assertEqual(c["orders"], [])
            self.assertEqual(c["activity"]["signals"], 1)
            self.assertEqual(
                c["diagnostics"]["candidates"][0]["outcome"], "blocked_assessment_" + expected
            )
