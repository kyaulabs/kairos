"""Offline-only assessment replay filter; no SDK, orders or production permissions."""

import hashlib
import math
from collections import defaultdict
from datetime import datetime

from kairos.domain import SafetyError
from kairos.research_assessment import digest, validate_answer, validate_snapshot


class AssessmentGate:
    """Add an entry veto without changing signal generation or owned protection.

    Suppression is a retrospective, outcome-blind reference conditional on each
    case's AI permit count, not a deployable causal non-AI trading strategy.
    """

    def __init__(self, snapshots, results, policy, seed=0):
        if policy not in ("availability", "ai", "suppression") or type(seed) is not int:
            raise ValueError("Unknown research gate policy or seed")
        if len(snapshots) != len(results):
            raise ValueError("Complete, positionally bound assessments required")
        self.policy, self.records = policy, {}
        groups = defaultdict(list)
        for snapshot, result in zip(snapshots, results, strict=True):
            validate_snapshot(snapshot)
            key = (snapshot["setup"], snapshot["minutes"], snapshot["candidate_id"])
            if key in self.records:
                raise ValueError("Duplicate assessment case")
            if (
                result.get("status") != "complete"
                or result.get("candidate_id") != snapshot["candidate_id"]
                or result.get("input_sha256") != digest(snapshot)
            ):
                raise ValueError("Missing, failed or mismatched assessment")
            answer = validate_answer(result["answer"], snapshot)
            started = datetime.fromisoformat(result["started_at"])
            received = datetime.fromisoformat(result["received_at"])
            if started.utcoffset() is None or received.utcoffset() is None:
                raise ValueError("Assessment timing needs explicit time zones")
            elapsed = (received - started).total_seconds()
            if not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError("Invalid recorded response latency")
            self.records[key] = {
                "as_of": snapshot["as_of"],
                "expires_at": snapshot["expires_at"],
                "available_at": snapshot["as_of"] + 1 + elapsed,
                "decision": answer["decision"],
                "input_sha256": digest(snapshot),
            }
            groups[key[:2]].append(key)
        self.selected = set()
        for keys in groups.values():
            permits = [key for key in keys if self.records[key]["decision"] == "permit"]
            if policy == "suppression":
                keys = sorted(
                    keys,
                    key=lambda key: hashlib.sha256(
                        f"assessment-suppression-v1|{seed}|{key[0]}|{key[1]}|{key[2]}".encode()
                    ).digest(),
                )
                self.selected.update(keys[: len(permits)])
            else:
                self.selected.update(keys if policy == "availability" else permits)

    def evaluate(self, setup, now, minutes):
        if not math.isfinite(now):
            raise SafetyError("Invalid replay clock")
        key = (setup.variant, minutes, setup.facts.get("candidate_id"))
        record = self.records.get(key)
        if record is None:
            return {"allowed": False, "policy": self.policy, "reason": "unassessed"}
        if setup.facts.get("armed_at") != record["as_of"]:
            raise SafetyError("Assessment arming binding mismatch")
        if setup.closed_at < record["as_of"] or now < setup.closed_at:
            raise SafetyError("Assessment replay clock precedes candidate confirmation")
        # Original signal expiry is a confirmation boundary. A confirmation at
        # that boundary may use the ordinary next-open quote at boundary + 1.
        if setup.closed_at > record["expires_at"] or record["available_at"] > record["expires_at"]:
            reason = "expired"
        elif record["available_at"] > now:
            reason = "response_pending"
        elif key not in self.selected:
            reason = "abstain" if self.policy == "ai" else "suppressed"
        else:
            reason = "permit_monitoring"
        return {
            "allowed": reason == "permit_monitoring",
            "policy": self.policy,
            "reason": reason,
            "available_at": record["available_at"],
            "expires_at": record["expires_at"],
            "input_sha256": record["input_sha256"],
        }
