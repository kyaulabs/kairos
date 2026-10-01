"""Fixed causal features and a small entry-quality adaptation, not a DMN or Jev."""

import math
import statistics

from kairos.domain import SafetyError
from kairos.research.artifacts import utc
from kairos.research.data import available_history, purged_labels


def features(history, decision_at, plan):
    count = max(plan["momentum_hours"], plan["volatility_hours"], 24) + 1
    rows = available_history(history, decision_at, count)
    if not rows:
        return None
    prices = [float(b.close) for b in rows]
    returns = [b / a - 1 for a, b in zip(prices[:-1], prices[1:], strict=True)]
    vol = statistics.stdev(returns[-plan["volatility_hours"] :])
    short = statistics.stdev(returns[-plan["short_volatility_hours"] :])
    return {
        "momentum": prices[-1] / prices[-1 - plan["momentum_hours"]] - 1,
        "day_return": prices[-1] / prices[-25] - 1,
        "volatility": vol * math.sqrt(365 * 24),
        "instability": short / vol if vol else 0,
        "information_end": rows[-1].end,
        "decision_at": decision_at,
    }


def feature_series(bars, plan):
    count = max(plan["momentum_hours"], plan["volatility_hours"], 24) + 1
    return [
        features(bars[max(0, i - count + 1) : i + 1], b.available, plan) for i, b in enumerate(bars)
    ]


def vector(feature):
    return [feature[k] for k in ("momentum", "day_return", "volatility")]


def samples(bars, views, plan):
    """Labels are hypothetical delayed net price returns, NOT actual executed-trade wins."""
    scenario = plan["scenarios"]["base"]
    drag = (scenario["spread_bps"] / 2 + scenario["slippage_bps"]) / 10000
    fee = scenario["fee_bps"] / 10000
    result = []
    by_start = {b.start: b for b in bars}
    for b, view in zip(bars, views, strict=True):
        if view is None:
            continue
        entry_at = b.end + scenario["delay_bars"] * 3600
        exit_at = entry_at + plan["label_hours"] * 3600
        entry, exit_bar = by_start.get(entry_at), by_start.get(exit_at)
        if entry is None or exit_bar is None or entry.start < b.available:
            continue
        value = (
            float(exit_bar.open)
            * (1 - drag)
            * (1 - fee)
            / (float(entry.open) * (1 + drag) * (1 + fee))
            - 1
        )
        result.append((b.available, exit_bar.available, vector(view), int(value > 0)))
    return result


def sigmoid(value):
    return 1 / (1 + math.exp(-max(-35, min(35, value))))


def logit(model, values):
    scaled = [(x - m) / s for x, m, s in zip(values, model["means"], model["scales"], strict=True)]
    return model["weights"][0] + sum(
        w * x for w, x in zip(model["weights"][1:], scaled, strict=True)
    )


def probability(model, values):
    return sigmoid(logit(model, values) / model["temperature"])


def calibration_metrics(probabilities, labels):
    if not labels or len(probabilities) != len(labels):
        return {"n": 0, "brier": None, "nll": None, "ece": None, "bins": []}
    pairs = list(zip(probabilities, labels, strict=True))
    bins = []
    for i in range(15):
        group = [(p, y) for p, y in pairs if min(14, int(p * 15)) == i]
        if group:
            bins.append(
                {
                    "count": len(group),
                    "probability": statistics.mean(p for p, _ in group),
                    "frequency": statistics.mean(y for _, y in group),
                }
            )
    return {
        "n": len(labels),
        "brier": statistics.mean((p - y) ** 2 for p, y in pairs),
        "nll": -statistics.mean(
            y * math.log(max(p, 1e-15)) + (1 - y) * math.log(max(1 - p, 1e-15)) for p, y in pairs
        ),
        "ece": sum(b["count"] * abs(b["probability"] - b["frequency"]) for b in bins) / len(labels),
        "bins": bins,
    }


def fit_quality(observations, plan):
    fit_end, cal_end = utc(plan["calibration_start"]), utc(plan["validation_start"])
    embargo = plan["embargo_hours"] * 3600
    train = purged_labels(observations, utc(plan["fit_start"]), fit_end, embargo)
    calibration = purged_labels(observations, fit_end, cal_end, embargo)
    if len(train) < 100 or len(calibration) < 100 or len({s[3] for s in train}) < 2:
        raise SafetyError("Insufficient matured training/calibration labels")
    means = [statistics.mean(s[2][j] for s in train) for j in range(3)]
    scales = [statistics.stdev(s[2][j] for s in train) or 1 for j in range(3)]
    xs = [[1] + [(x - m) / sd for x, m, sd in zip(s[2], means, scales, strict=True)] for s in train]
    weights = [0.0] * 4
    cfg = plan["quality_fit"]
    for _ in range(cfg["epochs"]):
        gradient = [0.0] * 4
        for x, sample in zip(xs, train, strict=True):
            error = sigmoid(sum(w * v for w, v in zip(weights, x, strict=True))) - sample[3]
            for j in range(4):
                gradient[j] += error * x[j]
        weights = [
            w - cfg["learning_rate"] * (g / len(train) + (cfg["l2"] * w if j else 0))
            for j, (w, g) in enumerate(zip(weights, gradient, strict=True))
        ]
    model = {
        "means": means,
        "scales": scales,
        "weights": weights,
        "temperature": 1.0,
        "fit_labels": len(train),
        "calibration_labels": len(calibration),
        "fit_latest_label": max(s[1] for s in train),
        "calibration_latest_label": max(s[1] for s in calibration),
        "available_at": max(s[1] for s in calibration),
        "training_prevalence": statistics.mean(s[3] for s in train),
    }
    logits = [logit(model, s[2]) for s in calibration]
    labels = [s[3] for s in calibration]

    def loss(temp):
        return -statistics.mean(
            y * math.log(max(sigmoid(z / temp), 1e-15))
            + (1 - y) * math.log(max(1 - sigmoid(z / temp), 1e-15))
            for z, y in zip(logits, labels, strict=True)
        )

    # Fixed, bounded one-dimensional NLL fit; no threshold/hyperparameter search.
    lo, hi = cfg["temperature_min"], cfg["temperature_max"]
    for _ in range(60):
        left, right = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if loss(left) <= loss(right):
            hi = right
        else:
            lo = left
    model["temperature"] = (lo + hi) / 2
    model["calibration"] = calibration_metrics(
        [sigmoid(z / model["temperature"]) for z in logits], labels
    )
    model["uncalibrated"] = calibration_metrics([sigmoid(z) for z in logits], labels)
    return model


def allocation(arm, view, plan, scenario, model=None):
    if arm not in plan["arms"]:
        raise SafetyError("Unregistered research arm")
    if view is None or view["momentum"] <= 0:
        return 0.0
    if arm == "volatility":
        return min(1, plan["volatility_target_annual"] / max(view["volatility"], 1e-12))
    if arm == "cost":
        cost = 2 * scenario["fee_bps"] + scenario["spread_bps"] + 2 * scenario["slippage_bps"]
        return float(view["momentum"] / 7 * 10000 > cost + plan["cost_buffer_bps"])
    if arm == "instability":
        return float(view["instability"] <= plan["instability_ratio"])
    if arm == "quality":
        if model is None:
            raise SafetyError("Quality arm requires a frozen trained model")
        if model["available_at"] >= view["decision_at"]:
            raise SafetyError("Model fitting used information unavailable at decision time")
        return float(probability(model, vector(view)) >= plan["quality_threshold"])
    return 1.0
