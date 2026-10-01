"""Registered, fixed-family evaluation. Final values are loaded only after a one-shot claim."""

import hashlib
import platform
import statistics
from pathlib import Path

from kairos.domain import SafetyError
from kairos.research.artifacts import digest, utc
from kairos.research.data import validate_bars
from kairos.research.execution import simulate
from kairos.research.signals import (
    calibration_metrics,
    feature_series,
    fit_quality,
    probability,
    samples,
)
from kairos.research.statistics import (
    daily_returns,
    paired_interval,
    selection_diagnostics,
    summary,
)


def code_hash():
    root = Path(__file__).parent
    files = sorted(root.glob("*.py")) + [root.parent / "domain.py"]
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def specification(plan, dataset, development=None):
    return {
        "plan": digest(plan),
        "dataset": dataset,
        "code": code_hash(),
        "python": platform.python_version(),
        "development": development,
        "holdout": digest({k: plan[k] for k in ("source", "universe", "final_start", "final_end")}),
    }


def check_dataset(registry, key, plan):
    manifest = registry.artifacts.get(key)
    if registry.artifacts.get(manifest["plan"]) != plan:
        raise SafetyError("Dataset does not belong to the registered plan")
    if any(
        q["coverage"] < plan["criteria"]["minimum_coverage"]
        for parts in manifest["quality"].values()
        for q in parts.values()
    ):
        raise SafetyError("Coverage below preregistered minimum; do not change windows to fit data")
    if list(sorted(manifest["partitions"])) != sorted(plan["universe"]):
        raise SafetyError("Dataset universe differs from preregistration")
    return manifest


def evaluate_one(
    registry, spec, bars, views, plan, symbol, arm, scenario, start, end, model, scale=1
):
    attempt = {
        **spec,
        "operation": "simulate",
        "symbol": symbol,
        "arm": arm,
        "scenario": scenario,
        "start": start,
        "end": end,
        "scale": scale,
        "model": digest(model) if model else None,
    }
    with registry.trial(attempt) as trial:
        result = simulate(bars, views, plan, symbol, arm, scenario, start, end, model, scale)
        result["daily_returns"] = daily_returns(
            result["path"], float(plan["risk"]["initial_usd"]), start
        )
        result.update(summary(list(result["daily_returns"].values())))
        result["mean_exposure_usd"] = (
            statistics.mean(p["exposure"] for p in result["path"]) if result["path"] else 0
        )
        result["maximum_exposure_usd"] = max((p["exposure"] for p in result["path"]), default=0)
        key = registry.record(trial, "simulation", result)
    return key, result


def compact(result):
    return {
        k: v for k, v in result.items() if k not in ("path", "orders", "decisions", "daily_returns")
    }


def run(registry, plan, dataset, stage, development=None):
    spec = specification(plan, dataset, development)
    with registry.trial({**spec, "operation": stage}) as family:
        if stage not in {"development", "final"}:
            raise SafetyError("Unknown research stage")
        if stage == "final":
            registry.claim_final(plan["name"], spec, family)
        manifest = check_dataset(registry, dataset, plan)
        prior = registry.artifacts.get(development) if development else None
        if stage == "final" and (
            not prior
            or prior["stage"] != "development"
            or prior["spec"] != specification(plan, dataset)
        ):
            raise SafetyError("Final requires the exact development result and code")
        start = utc(plan["validation_start"] if stage == "development" else plan["final_start"])
        end = utc(plan["final_start"] if stage == "development" else plan["final_end"])
        report = {
            "stage": stage,
            "spec": spec,
            "assets": {},
            "conclusion": "inconclusive",
            "reason": "Bar-only data lacks historical arrival/revision/queue and actual broker execution evidence.",
        }
        for symbol in plan["universe"]:
            parts = manifest["partitions"][symbol]
            rows = registry.artifacts.get(parts["development"])
            if stage == "final":
                rows += registry.artifacts.get(parts["final"])
            bars = validate_bars(rows)
            views = feature_series(bars, plan)
            observations = samples(bars, views, plan)
            if stage == "development":
                with registry.trial(
                    {**spec, "operation": "fit_quality", "symbol": symbol}
                ) as fitting:
                    model = fit_quality(observations, plan)
                    model_key = registry.record(fitting, "model", model)
            else:
                model_key = prior["assets"][symbol]["model"]
                model = registry.artifacts.get(model_key)
            asset = {"model": model_key, "scenarios": {}, "risk_scales": {}}
            if stage == "development":
                # All risk-reference fitting precedes validation. Quality uses its fitted
                # coefficients but T=1 so calibration labels cannot alter these past decisions.
                risk_model = {
                    **model,
                    "temperature": 1.0,
                    "available_at": model["fit_latest_label"],
                }
                risk_start, risk_end = utc(plan["calibration_start"]), utc(plan["validation_start"])
                _, passive = evaluate_one(
                    registry,
                    spec,
                    bars,
                    views,
                    plan,
                    symbol,
                    "capped_passive",
                    "base",
                    risk_start,
                    risk_end,
                    risk_model,
                )
                passive_vol = passive["annualized_volatility"] or 0
                for arm in plan["arms"]:
                    _, historical = evaluate_one(
                        registry,
                        spec,
                        bars,
                        views,
                        plan,
                        symbol,
                        arm,
                        "base",
                        risk_start,
                        risk_end,
                        risk_model,
                    )
                    asset["risk_scales"][arm] = (
                        min(1, (historical["annualized_volatility"] or 0) / passive_vol)
                        if passive_vol
                        else 0
                    )
            else:
                asset["risk_scales"] = prior["assets"][symbol]["risk_scales"]
            metrics_samples = [s for s in observations if start <= s[0] and s[1] < end]
            labels = [s[3] for s in metrics_samples]
            asset["quality_label_evaluation"] = {
                "label": "hypothetical delayed 24h net price return, not realized trading profit",
                "calibrated": calibration_metrics(
                    [probability(model, s[2]) for s in metrics_samples], labels
                ),
                "training_prevalence": calibration_metrics(
                    [model["training_prevalence"]] * len(labels), labels
                ),
            }
            for scenario in plan["scenarios"]:
                outputs, details = {}, {}
                for arm in plan["arms"] + ["cash", "buy_hold_reference", "capped_passive"]:
                    key, result = evaluate_one(
                        registry, spec, bars, views, plan, symbol, arm, scenario, start, end, model
                    )
                    outputs[arm] = {"artifact": key, **compact(result)}
                    details[arm] = result
                for arm in plan["arms"]:
                    key, result = evaluate_one(
                        registry,
                        spec,
                        bars,
                        views,
                        plan,
                        symbol,
                        "train_risk_matched_passive",
                        scenario,
                        start,
                        end,
                        model,
                        asset["risk_scales"][arm],
                    )
                    outputs[arm]["risk_matched"] = {"artifact": key, **compact(result)}
                    if arm == "momentum":
                        outputs[arm]["scenario_conclusion"] = "reference_not_an_addition"
                        continue
                    criteria = plan["criteria"]
                    intervals = {}
                    for name, other in [
                        ("momentum", details["momentum"]),
                        ("risk_matched", result),
                    ]:
                        intervals[name] = paired_interval(
                            details[arm]["daily_returns"],
                            other["daily_returns"],
                            seed=plan["seed"],
                            samples=criteria["bootstrap_samples"],
                            block=criteria["bootstrap_block_days"],
                            alpha=criteria["family_alpha"] / criteria["primary_comparisons"],
                        )
                    outputs[arm]["paired_mean_daily_return_intervals"] = intervals
                    enough = (
                        details[arm]["days"] >= criteria["minimum_evaluation_days"]
                        and details[arm]["completed_round_trips"]
                        >= criteria["minimum_completed_round_trips"]
                        and details["momentum"]["completed_round_trips"]
                        >= criteria["minimum_completed_round_trips"]
                    )
                    outputs[arm]["adequate_sample"] = enough
                    dd = max(
                        details[arm]["bar_close_drawdown_pct"],
                        details[arm]["adverse_bar_low_drawdown_pct"],
                    )
                    baseline_dd = max(
                        details["momentum"]["bar_close_drawdown_pct"],
                        details["momentum"]["adverse_bar_low_drawdown_pct"],
                    )
                    outputs[arm]["checks"] = {
                        "adequate_sample": enough,
                        "positive_net_return": details[arm]["net_return_pct"] > 0,
                        "adjusted_paired_lower_bounds_positive": all(
                            v["lower"] is not None and v["lower"] > 0 for v in intervals.values()
                        ),
                        "drawdown_below_absolute_limit": dd <= criteria["maximum_drawdown_pct"],
                        "drawdown_increase_bounded": dd
                        <= baseline_dd + criteria["maximum_drawdown_increase_pp"],
                        "verified_execution_and_vintages": False,
                    }
                    outputs[arm]["scenario_conclusion"] = (
                        "unsupported"
                        if enough
                        and any(
                            v["upper"] is not None and v["upper"] <= 0 for v in intervals.values()
                        )
                        else "inconclusive"
                    )
                diagnostics = {
                    "available": False,
                    "reason": "Never run selection diagnostics on final holdout",
                }
                if stage == "development":
                    times = sorted(
                        set.intersection(*(set(details[a]["daily_returns"]) for a in plan["arms"]))
                    )
                    diagnostics = selection_diagnostics(
                        [[details[a]["daily_returns"][t] for t in times] for a in plan["arms"]],
                        physical_trials=len(plan["arms"]),
                    )
                asset["scenarios"][scenario] = {"arms": outputs, "design_diagnostics": diagnostics}
            report["assets"][symbol] = asset
        report["hypotheses"] = {}
        for arm in plan["arms"][1:]:
            base = [a["scenarios"]["base"]["arms"][arm] for a in report["assets"].values()]
            stress = [a["scenarios"]["stress"]["arms"][arm] for a in report["assets"].values()]
            report["hypotheses"][arm] = {
                "conclusion": "unsupported"
                if any(a["scenario_conclusion"] == "unsupported" for a in base)
                else "inconclusive",
                "both_assets_base_checks": all(all(a["checks"].values()) for a in base),
                "both_assets_positive_stress_return": all(a["net_return_pct"] > 0 for a in stress),
                "scope": "Only the preregistered bar scenarios; no broker-performance support claim",
            }
        return registry.record(family, "report", report)


def seal(registry, plan, dataset, development):
    report = registry.artifacts.get(development)
    if report["stage"] != "development" or report["spec"] != specification(plan, dataset):
        raise SafetyError("Development report/code differs from the proposed final specification")
    return registry.seal(plan["name"], specification(plan, dataset, development))
