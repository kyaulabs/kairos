"""Stage-one audit only. No historical partitions, model fitting or network requests.

Run from the checkout: python -m research.check_execution_gate --root RESEARCH_ROOT
A blocked gate returns exit status 2; it is a completed audit, not a passed study.
"""

import argparse
import asyncio
import hashlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from kairos import htf
from kairos.domain import dec
from kairos.research.artifacts import Registry, digest
from kairos.research.execution import Scenario
from kairos.research.experiment import code_hash
from kairos.store import Store
from tests.test_alpaca import PaperBroker
from tests.test_research_data import bars

EXECUTION_TESTS = (
    "passive_touch_does_not_fill_and_miss_has_no_taker_fallback",
    "starting_in_positive_signal_does_not_force_entry",
    "full_and_partial_fills_preserve_cash_and_inventory",
    "protection_never_calls_or_waits_for_quality_model",
    "daily_loss_halt_does_not_restart_the_next_day",
    "exposure_trim_is_bounded_and_does_not_liquidate_to_zero",
    "rejections_do_not_spend_cash_or_invent_inventory",
    "opening_cannot_consume_unpublished_liquidity_or_future_cancel",
    "unavailable_bar_features_and_future_perturbations_do_not_rewrite_decisions",
    "pending_trim_cannot_mask_or_delay_stop_deadline_or_halt",
    "buy_hold_price_reference_is_full_exposure_not_a_tiny_partial",
    "signal_arrival_cannot_erase_an_already_submitted_potential_fill",
)


def run_tests(names):
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromNames(names)
    )
    return {
        "passed": result.wasSuccessful() and not result.skipped,
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "log": stream.getvalue(),
    }


async def compare_fixtures(plan):
    broker = PaperBroker()  # request is AsyncMock; never an authenticated client session.
    pair = (await broker.client.catalog())["alpaca:BTC/USD"]
    book = await broker.client.book(pair)
    quantity, price, residual = dec(".01"), dec(100), dec(".00005")
    store = Store(":memory:")
    try:
        engine = SimpleNamespace(
            mode="paper",
            store=store,
            settings={
                "pair": pair.id,
                "product": "spot",
                "slippage_bps": 10,
                "daily_loss": "12.50",
                "max_spread_bps": 100,
            },
            balance=lambda currency: residual if currency == pair.base else dec(499),
            orders=lambda **_: [],
            daily_pnl=dec(0),
            exposure=residual * price,
            limits=lambda: (dec(100), dec(100)),
            fees=SimpleNamespace(reserve=lambda *_: dec(25)),
        )
        store.put(htf.key(engine), {"entry_signal": "hold", "position": None})
        context = htf.entry_context(engine, pair, {"entry_eligible": True}, book)
        state = Scenario(plan, "BTC/USD", "momentum", "base", None, 1)
        state.qty, state.cash, state.opened = residual, dec(499), 1
        state.stop, state.exit_reason, state.previous_signal = dec(97), "deadline", False
        bar = bars(2)[1]
        state.observe(
            bar, {"momentum": 0.1, "information_end": bar.end, "decision_at": bar.available}
        )
        return {
            "scope": "Synthetic API-shaped BTC fixture, NOT actual or historical BTC market rules",
            "adapter_rules": {
                "minimum_quantity": str(pair.minimum),
                "lot": str(pair.lot),
                "tick": str(pair.tick),
                "minimum_notional": str(pair.cost_minimum),
            },
            "research_rules": plan["assumed_market_rules"],
            "minimum_counterexample": {
                "quantity": str(quantity),
                "price": str(price),
                "production_rejects": htf.below_minimum(pair, quantity, price),
                "research_rejects": quantity * price < state.minimum,
            },
            "residual_counterexample": {
                "quantity": str(residual),
                "fresh_same_direction_setup": True,
                "production_entry_context": context,
                "research_action": state.decisions[-1]["action"],
                "research_entry_queued": bool(state.pending and state.pending["side"] == "buy"),
            },
            "fixture_requests": [{"method": m, "path": p} for m, p, _ in broker.calls],
        }
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    protocol = json.loads(Path("research/STAGED_PLAN.json").read_text())
    plan = json.loads(Path("research/PLAN.json").read_text())
    registry = Registry(args.root)
    try:
        spec = {
            "operation": "staged_execution_audit",
            "study": protocol["study"],
            "protocol": registry.artifacts.put(protocol),
            "prior_plan": digest(plan),
            "research_code": code_hash(),
            "repository_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "audit_code": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "historical_partitions_read": [],
            "final_evaluation": False,
        }
        with registry.trial(spec) as trial:
            execution = run_tests(
                [
                    "tests.test_alpaca.AlpacaClientTests",
                    "tests.test_alpaca.AlpacaEngineTests",
                    "tests.test_htf.HTFTests",
                    *[
                        "tests.test_research_experiment.ResearchExperimentTests.test_" + n
                        for n in EXECUTION_TESTS
                    ],
                ]
            )
            data = run_tests(["tests.test_research_data"]) if execution["passed"] else None
            comparisons = asyncio.run(compare_fixtures(plan)) if data and data["passed"] else None
            gates = {
                "execution_and_accounting_tests_pass": execution["passed"],
                "causal_data_tests_pass": bool(data and data["passed"]),
                "market_rule_representation_matches_adapter": False,
                "eligible_residual_handling_matches_production": False,
                "dated_market_rules_available": False,
                "execution_scenario_assumptions_justified": False,
            }
            if comparisons:
                m = comparisons["minimum_counterexample"]
                r = comparisons["residual_counterexample"]
                gates["market_rule_representation_matches_adapter"] = (
                    m["production_rejects"] == m["research_rejects"]
                    and comparisons["adapter_rules"]["lot"] == comparisons["research_rules"]["lot"]
                )
                gates["eligible_residual_handling_matches_production"] = (
                    "buy" in r["production_entry_context"]["allowed_actions"]
                ) == r["research_entry_queued"]
            passed = all(gates.values())
            report = {
                "spec": spec,
                "gate_passed": passed,
                "gates": gates,
                "execution_tests": execution,
                "causal_data_tests": data,
                "comparisons": comparisons,
                "unverified_evidence": [
                    "Dated BTC/ETH asset precision/minimum snapshots",
                    "Historical executable quotes/depth, fills and cancellation timing",
                ],
                "later_stages": "not run; stage-one evidence must be resolved and reviewed first",
                "historical_backtests_run": 0,
                "models_fitted": 0,
                "final_window_opened": False,
            }
            key = registry.record(trial, "staged_gate_report", report)
        print(json.dumps({"report": key, "result": report}, indent=2))
        return 0 if passed else 2
    finally:
        registry.close()


if __name__ == "__main__":
    raise SystemExit(main())
