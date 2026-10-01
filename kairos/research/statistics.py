"""Dependence-aware descriptive intervals and design-only selection diagnostics."""

import itertools
import math
import random
import statistics as stats

from kairos.domain import SafetyError


def daily_returns(path, initial, start):
    previous, previous_at = initial, start
    result = {}
    for row in path:
        if row["at"] % 86400:
            continue
        if row["at"] - previous_at == 86400:
            result[str(row["at"])] = row["equity"] / previous - 1
        previous, previous_at = row["equity"], row["at"]
    return result


def sharpe(values):
    if len(values) < 3:
        return None
    sd = stats.stdev(values)
    return stats.mean(values) / sd if sd else None


def summary(values):
    sr = sharpe(values)
    return {
        "days": len(values),
        "annualized_sharpe": sr * math.sqrt(365) if sr is not None else None,
        "annualized_volatility": stats.stdev(values) * math.sqrt(365) if len(values) > 1 else None,
    }


def paired_interval(left, right, *, seed, samples, block, alpha):
    times = sorted(set(map(int, left)) & set(map(int, right)))
    if not 0 < alpha < 0.5 or samples < 100 or block < 1:
        raise SafetyError("Invalid uncertainty settings")
    values = [left[str(t)] - right[str(t)] for t in times]
    starts = [
        i
        for i in range(len(times) - block + 1)
        if times[i + block - 1] - times[i] == (block - 1) * 86400
    ]
    if not starts or len(values) < 2 * block:
        return {"n": len(values), "lower": None, "upper": None, "mean": None}
    rng, means = random.Random(seed), []
    for _ in range(samples):
        resample = []
        while len(resample) < len(values):
            offset = rng.choice(starts)
            resample.extend(values[offset : offset + block])
        means.append(stats.mean(resample[: len(values)]))
    means.sort()
    return {
        "n": len(values),
        "mean": stats.mean(values),
        "lower": means[int(alpha * (samples - 1))],
        "upper": means[int((1 - alpha) * (samples - 1))],
        "alpha_one_sided": alpha,
        "method": "paired non-circular moving-block bootstrap",
        "block_days": block,
        "resamples": samples,
        "seed": seed,
    }


def selection_diagnostics(matrix, physical_trials, blocks=8):
    """Aligned DESIGN return columns only. CSCV never fits or selects final models.

    Raw counts assume independence for the DSR threshold; this is explicitly a
    sensitivity calculation, not an estimated effective number of independent trials.
    """
    if len(matrix) < 2 or len({len(c) for c in matrix}) != 1:
        return {"available": False, "reason": "Incomplete comparable candidate family"}
    srs = [sharpe(c) for c in matrix]
    length = len(matrix[0])
    if any(s is None for s in srs) or length < blocks * 3 or blocks % 2:
        return {"available": False, "reason": "Insufficient/constant candidate series"}
    n = max(physical_trials, len(matrix))
    normal = stats.NormalDist()
    gamma = 0.5772156649015329
    threshold = stats.stdev(srs) * (
        (1 - gamma) * normal.inv_cdf(1 - 1 / n) + gamma * normal.inv_cdf(1 - 1 / (n * math.e))
    )
    dsrs = []
    for values, sr in zip(matrix, srs, strict=True):
        mean, sd = stats.mean(values), stats.pstdev(values)
        skew = stats.mean(((v - mean) / sd) ** 3 for v in values)
        kurt = stats.mean(((v - mean) / sd) ** 4 for v in values)
        variance = 1 - skew * sr + (kurt - 1) * sr * sr / 4
        dsrs.append(
            normal.cdf((sr - threshold) * math.sqrt((length - 1) / variance))
            if variance > 0
            else None
        )
    width, logits = length // blocks, []
    for combination in itertools.combinations(range(blocks), blocks // 2):
        train = {i for b in combination for i in range(b * width, (b + 1) * width)}
        ins = [sharpe([c[i] for i in sorted(train)]) for c in matrix]
        outs = [sharpe([c[i] for i in range(blocks * width) if i not in train]) for c in matrix]
        if any(x is None for x in ins + outs):
            return {"available": False, "reason": "Constant CSCV block; no silent trial deletion"}
        best = max(range(len(matrix)), key=lambda j: ins[j])
        value = outs[best]
        rank = 1 + sum(x < value for x in outs) + (sum(x == value for x in outs) - 1) / 2
        omega = rank / (len(matrix) + 1)
        logits.append(math.log(omega / (1 - omega)))
    return {
        "available": True,
        "dsr_raw_trial_sensitivity": dsrs,
        "trial_count": n,
        "sharpe_threshold_per_day": threshold,
        "pbo": sum(x <= 0 for x in logits) / len(logits),
        "cscv_blocks": blocks,
        "combinations": len(logits),
        "trailing_days_omitted": length - blocks * width,
        "caveat": "Small heterogeneous dependent family; normal trial-Sharpe/PSR approximations. Not a leakage test, posterior profit probability or final-window selection rule.",
    }
