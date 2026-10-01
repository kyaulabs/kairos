"""Retrospective chronological Kraken price study with assumed Alpaca crypto costs.

Reuses the frozen unfunded price index. No Alpaca market data, fitting, broker IO,
new final holdout, production risk-control changes or automatic paper activation.
"""

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import zipfile
from pathlib import Path

from kairos.domain import SafetyError
from kairos.research.artifacts import Registry, utc
from kairos.research.statistics import paired_interval
from research import kraken_daily

DETAILS = {"daily_returns", "marks", "decisions", "episodes"}


def period_plan(plan, year):
    if year not in plan["years"] or not 2019 <= year <= 2025:
        raise SafetyError("Only registered 2019-2025 development windows are allowed")
    return {
        "evaluation_start": f"{year}-01-01T00:00:00Z",
        "last_mark": f"{year}-12-31T00:00:00Z",
        "lookback_days": plan["lookback_days"],
        "publication_delay_seconds": plan["publication_delay_seconds"],
    }


def pool(series):
    merged = {}
    for result in series:
        values = result["daily_returns"]
        if merged.keys() & values.keys():
            raise SafetyError("Overlapping annual observations")
        if any(int(t) >= kraken_daily.EXCLUDED_START for t in values):
            raise SafetyError("Consumed market period cannot enter pooling")
        merged.update(values)
    return dict(sorted(merged.items(), key=lambda p: int(p[0])))


def interval(left, right, plan):
    settings = plan["inference"]
    return paired_interval(
        left,
        right,
        seed=settings["seed"],
        samples=settings["bootstrap_samples"],
        block=settings["block_days"],
        alpha=settings["family_alpha"] / settings["comparisons"],
    )


def progression_gate(years, pooled, plan):
    settings = plan["inference"]
    if set(years) != {str(y) for y in plan["years"]}:
        raise SafetyError("Cannot assess an incomplete annual family")
    base = [v["alpaca25"]["indices"]["momentum"] for v in years.values()]
    stress = [v["alpaca25_friction_stress"]["indices"]["momentum"] for v in years.values()]
    days = sum(v["days"] for v in base)
    episodes = sum(v["signal_closed_episodes"] for v in base)
    base_returns = [v["net_index_change_pct"] for v in base]
    stress_returns = [v["net_index_change_pct"] for v in stress]
    checks = {
        "adequate_pooled_sample": days >= settings["pooled_minimum_days"]
        and episodes >= settings["pooled_minimum_signal_closed_episodes"],
        "base_consistency": sum(v > 0 for v in base_returns) >= settings["minimum_positive_years"]
        and statistics.median(base_returns) > 0,
        "stress_consistency": sum(v > 0 for v in stress_returns)
        >= settings["minimum_positive_years"]
        and statistics.median(stress_returns) > 0,
        "adjusted_pooled_lower_bounds_positive": set(pooled) == {"cash", "passive"}
        and all(v["lower"] is not None and v["lower"] > 0 for v in pooled.values()),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "days": days,
        "signal_closed_episodes": episodes,
        "base_positive_years": sum(v > 0 for v in base_returns),
        "stress_positive_years": sum(v > 0 for v in stress_returns),
        "median_base_change_pct": statistics.median(base_returns),
        "median_stress_change_pct": statistics.median(stress_returns),
    }


def run(root):
    root = Path(root)
    plan = json.loads(Path("research/BROAD_PLAN.json").read_text())
    if (
        plan["venue_data"] != "kraken"
        or plan["alpaca_market_data_allowed"]
        or plan["paper_activation_authorized"]
    ):
        raise SafetyError("Offline Kraken-price study only")
    if plan["universe"] != ["XBTUSD", "ETHUSD"] or plan["years"] != list(range(2019, 2026)):
        raise SafetyError("Cannot drop assets or years from the registered family")
    registry = Registry(root)
    try:
        spec = {
            "operation": "broad_alpaca_cost_study",
            "plan": registry.artifacts.put(plan),
            "code": kraken_daily.file_hash(Path(__file__)),
            "index_code": kraken_daily.file_hash(Path(kraken_daily.__file__)),
            "statistics_code": kraken_daily.file_hash(Path("kairos/research/statistics.py")),
            "repository_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "python": platform.python_version(),
            "final_evaluation": False,
        }
        with registry.trial(spec) as family:
            previous = registry.artifacts.get(plan["previous_report"])
            if registry.artifacts.get(previous["dataset"])["venue"] != "kraken":
                raise SafetyError("Matched control must be Kraken-only")
            with registry.trial({**spec, "operation": "broad_archive_selection"}) as selection:
                archive = root / "Kraken_OHLCVT_Full_2026Q2.zip"
                if kraken_daily.file_hash(archive) != plan["archive_sha256"]:
                    raise SafetyError("Archive checksum mismatch")
                rows, parts = {}, {}
                with zipfile.ZipFile(archive) as z:
                    for symbol in plan["universe"]:
                        matches = [
                            i for i in z.infolist() if Path(i.filename).name == symbol + "_1440.csv"
                        ]
                        if len(matches) != 1 or matches[0].file_size > 5 * 1024**2:
                            raise SafetyError("Invalid daily member selection")
                        body = z.read(matches[0])
                        rows[symbol] = kraken_daily.parse_daily(
                            body, utc(plan["data_start"]), utc(plan["data_end"])
                        )
                        parts[symbol] = {
                            "rows": len(rows[symbol]),
                            "member": matches[0].filename,
                            "sha256": hashlib.sha256(body).hexdigest(),
                            "selected_rows": registry.artifacts.put(rows[symbol]),
                        }
                dataset = registry.record(
                    selection,
                    "broad_dataset",
                    {
                        "venue": "kraken",
                        "archive_sha256": plan["archive_sha256"],
                        "partitions": parts,
                        "alpaca_market_data": False,
                        "assumed_publication_delay_seconds": plan["publication_delay_seconds"],
                    },
                )
            report = {
                "spec": spec,
                "dataset": dataset,
                "assets": {},
                "matched_2025": {},
                "status": "retrospective_exploratory",
                "unavailable_years": plan["unavailable_years"],
                "paper_activation": False,
                "execution_validity_gate_passed": False,
                "model_fits": 0,
            }
            for symbol, data in rows.items():
                years, details = {}, {}
                for year in plan["years"]:
                    years[str(year)], details[year] = {}, {}
                    scenarios = dict(plan["costs"])
                    if year == 2025:
                        scenarios["fee40_control"] = plan["matched_2025_control"]
                    for name, costs in scenarios.items():
                        outputs, details[year][name] = {}, {}
                        for arm in plan["arms"]:
                            with registry.trial(
                                {
                                    **spec,
                                    "operation": "annual_price_index",
                                    "dataset": dataset,
                                    "symbol": symbol,
                                    "year": year,
                                    "scenario": name,
                                    "arm": arm,
                                }
                            ) as trial:
                                result = kraken_daily.price_index(
                                    data, period_plan(plan, year), arm, costs
                                )
                                key = registry.record(trial, "annual_index", result)
                            details[year][name][arm] = result
                            outputs[arm] = {
                                "artifact": key,
                                **{k: v for k, v in result.items() if k not in DETAILS},
                            }
                        years[str(year)][name] = {"indices": outputs}
                    for arm in plan["arms"]:
                        if year == 2025:
                            for scenario, old in (
                                ("gross", "gross"),
                                ("fee40_control", "base_assumption"),
                            ):
                                if (
                                    years["2025"][scenario]["indices"][arm]["artifact"]
                                    != previous["assets"][symbol][old]["indices"][arm]["artifact"]
                                ):
                                    raise SafetyError(
                                        "Matched 2025 control changed; do not interpret fee attribution"
                                    )
                            if (
                                details[year]["alpaca25"][arm]["decisions"]
                                != details[year]["fee40_control"][arm]["decisions"]
                            ):
                                raise SafetyError("Fee-only comparison changed decisions")
                    base = details[year]["alpaca25"]
                    years[str(year)]["adjusted_base_intervals"] = {
                        other: interval(
                            base["momentum"]["daily_returns"], base[other]["daily_returns"], plan
                        )
                        for other in ("cash", "passive")
                    }
                    years[str(year)]["adequate_annual_episodes"] = (
                        base["momentum"]["signal_closed_episodes"]
                        >= plan["inference"][
                            "annual_minimum_signal_closed_episodes_for_interpretation"
                        ]
                    )
                pooled = {
                    a: pool([details[y]["alpaca25"][a] for y in plan["years"]])
                    for a in plan["arms"]
                }
                bounds = {
                    other: interval(pooled["momentum"], pooled[other], plan)
                    for other in ("cash", "passive")
                }
                report["assets"][symbol] = {
                    "years": years,
                    "pooled_base_intervals": bounds,
                    "pooled_returns": registry.artifacts.put(pooled),
                    "gate": progression_gate(years, bounds, plan),
                }
                report["matched_2025"][symbol] = {
                    "old_artifacts_identical": True,
                    "decisions_identical": True,
                    "fee_only_change_pp": years["2025"]["alpaca25"]["indices"]["momentum"][
                        "net_index_change_pct"
                    ]
                    - years["2025"]["fee40_control"]["indices"]["momentum"]["net_index_change_pct"],
                }
            report["advance_to_risk_constrained_followup"] = all(
                a["gate"]["passed"] for a in report["assets"].values()
            )
            key = registry.record(family, "broad_cost_report", report)
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
