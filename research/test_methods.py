"""Registered, network-free diagnostic of existing production spot methods."""

import argparse
import csv
import hashlib
import io
import json
import platform
import statistics
import subprocess
import uuid
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from kairos.domain import SafetyError
from kairos.market_data import CandleHistory
from kairos.research.artifacts import Registry, digest
from research.kraken_daily import file_hash
from research.method_replay import replay


def windows(plan):
    return [
        (f"{year}-{month:02d}", int(datetime(year, month, 1, tzinfo=UTC).timestamp()))
        for year in plan["years"]
        for month in plan["months"]
    ]


def code_identity():
    paths = sorted(Path("kairos").glob("*.py")) + [
        Path(__file__),
        Path("research/method_replay.py"),
    ]
    return {str(p): file_hash(p) for p in paths}


def select(registry, archive, plan, spec):
    with registry.trial({**spec, "operation": "method_minute_selection"}) as trial:
        if file_hash(archive) != plan["archive_sha256"]:
            raise SafetyError("Archive checksum mismatch")
        periods = windows(plan)
        panels = {}
        with zipfile.ZipFile(archive) as z:
            for rule in plan["rules"]:
                matches = [
                    i for i in z.infolist() if Path(i.filename).name == rule["id"] + "_1.csv"
                ]
                if len(matches) != 1 or matches[0].file_size > 512 * 1024**2:
                    raise SafetyError("Missing, ambiguous or oversized minute member")
                selected = {label: [] for label, _ in periods}
                h = hashlib.sha256()
                validator = CandleHistory(None, 1, 1)
                slot, prior = 0, -1
                with z.open(matches[0]) as source:
                    for line in source:
                        h.update(line)
                        ts = int(line.split(b",", 1)[0])
                        if ts <= prior:
                            raise SafetyError("Unordered or duplicate source minute")
                        prior = ts
                        while (
                            slot < len(periods)
                            and ts >= periods[slot][1] + plan["window_days"] * 86400
                        ):
                            slot += 1
                        if slot == len(periods):
                            continue  # Hash entire member/verify CRC, never decode excluded prices.
                        label, start = periods[slot]
                        if ts < start - plan["warmup_minutes"] * 60:
                            continue
                        raw = next(csv.reader(io.StringIO(line.decode())))
                        if len(raw) != 7:
                            raise SafetyError("Expected native OHLCVT, not fabricated VWAP")
                        selected[label].append(
                            validator.validate(
                                [raw[0], *raw[1:5], "0", raw[5], raw[6]], vwap_optional=True
                            )
                        )
                panels[rule["id"]] = {
                    "member": matches[0].filename,
                    "sha256": h.hexdigest(),
                    "panels": {
                        label: {"rows": len(rows), "artifact": registry.artifacts.put(rows)}
                        for label, rows in selected.items()
                    },
                }
        return registry.record(
            trial,
            "method_dataset",
            {
                "venue": "kraken",
                "archive_sha256": plan["archive_sha256"],
                "panels": panels,
                "vwap_available": False,
                "gap_filling": False,
                "alpaca_prices": False,
            },
        )


def worker(rows, rule, method, scenario, start, end):
    return replay(rows, rule, method, scenario, start, end)


def summarize(cases):
    grouped = {}
    for case in cases:
        key = f"{case['symbol']}:{case['method']}:{case['scenario']}"
        grouped.setdefault(key, []).append(case)
    result = {}
    for key, group in grouped.items():
        valid = [x for x in group if x["status"] == "completed"]
        returns = [float(x["summary"]["net_liquidation_change_pct"]) for x in valid]
        reasons = {}
        for x in valid:
            for reason, count in x["summary"]["decision_reasons"].items():
                reasons[reason] = reasons.get(reason, 0) + count
        result[key] = {
            "sessions": len(group),
            "completed": len(valid),
            "failed": len(group) - len(valid),
            "positive_sessions": sum(v > 0 for v in returns),
            "median_session_change_pct": statistics.median(returns) if returns else None,
            "mean_session_change_pct": statistics.mean(returns) if returns else None,
            "filled_orders": sum(x["summary"]["filled_orders"] for x in valid),
            "submitted_orders": sum(x["summary"]["submitted_orders"] for x in valid),
            "simulated_fees": sum(float(x["summary"]["simulated_fees"]) for x in valid),
            "halted_sessions": sum(x["summary"]["halt_reason"] is not None for x in valid),
            "missing_minutes": sum(x["summary"]["missing_minutes"] for x in valid),
            "max_path_drawdown_pct": max(
                (float(x["summary"]["path_drawdown_pct"]) for x in valid), default=None
            ),
            "decision_reasons": reasons,
        }
    return result


def run(root):
    plan = json.loads(Path("research/METHODS_PLAN.json").read_text())
    if plan["network_calls"] or plan["broker_writes"] or plan["paper_activation"]:
        raise SafetyError("Offline only")
    registry = Registry(root)
    try:
        spec = {
            "operation": "production_method_study",
            "plan": registry.artifacts.put(plan),
            "code": registry.artifacts.put(code_identity()),
            "python": platform.python_version(),
            "repository_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "historical_final": False,
        }
        with registry.trial(spec) as family:
            dataset_key = select(registry, Path(root) / "Kraken_OHLCVT_Full_2026Q2.zip", plan, spec)
            dataset = registry.artifacts.get(dataset_key)
            cases, futures = [], {}
            with ProcessPoolExecutor(max_workers=plan["workers"]) as pool:
                for rule in plan["rules"]:
                    for label, start in windows(plan):
                        rows = registry.artifacts.get(
                            dataset["panels"][rule["id"]]["panels"][label]["artifact"]
                        )
                        jobs = [
                            (m, n, s) for m in plan["methods"] for n, s in plan["scenarios"].items()
                        ]
                        jobs.append(
                            (
                                "htf_no_fill",
                                "base_no_fill",
                                {**plan["scenarios"]["base"], "passive": "none"},
                            )
                        )
                        for method, name, scenario in jobs:
                            identity = {
                                "symbol": rule["id"],
                                "window": label,
                                "method": method,
                                "scenario": name,
                            }
                            trial = uuid.uuid4().hex
                            trial_spec = {
                                **spec,
                                "operation": "production_method_session",
                                "dataset": dataset_key,
                                **identity,
                            }
                            registry.record(trial, "started", trial_spec)
                            future = pool.submit(
                                worker,
                                rows,
                                rule,
                                method,
                                scenario,
                                start,
                                start + plan["window_days"] * 86400,
                            )
                            futures[future] = (trial, trial_spec, identity)
                for future in as_completed(futures):
                    trial, trial_spec, identity = futures[future]
                    try:
                        result = future.result()
                        artifact = registry.record(trial, "method_session", result)
                        registry.record(trial, "completed", {"spec": digest(trial_spec)})
                        cases.append(
                            {
                                **identity,
                                "status": "completed",
                                "artifact": artifact,
                                "summary": {
                                    k: v
                                    for k, v in result.items()
                                    if k
                                    not in {
                                        "orders",
                                        "decision_examples",
                                        "settings",
                                        "htf",
                                        "scalp",
                                        "program",
                                        "daily_marks",
                                    }
                                },
                            }
                        )
                    except Exception as exc:
                        error = {"type": type(exc).__name__, "message": str(exc)}
                        artifact = registry.record(trial, "failed", error)
                        cases.append(
                            {**identity, "status": "failed", "artifact": artifact, "error": error}
                        )
            cases.sort(key=lambda c: (c["symbol"], c["window"], c["method"], c["scenario"]))
            report = {
                "spec": spec,
                "dataset": dataset_key,
                "cases": cases,
                "summary": summarize(cases),
                "broker_performance_verified": False,
                "full_jev_strategy_tested": False,
                "models_fitted": 0,
                "strategy_promoted": False,
                "unavailable": plan["unavailable"],
            }
            key = registry.record(family, "production_method_report", report)
        return {"report": key, "result": report}
    finally:
        registry.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root), indent=2))


if __name__ == "__main__":
    main()
