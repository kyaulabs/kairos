"""Sequential, single-factor three-month research. Never registers live strategies.

Profiles are generated only after the preceding comparison completes. Each report
contains a fresh full-window challenger and its accepted full-window control.
Generic indicator controls and execution stress are evidence, not replacements
for the eight named strategies. No model calls or subscription credentials.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from types import ModuleType
from zipfile import ZipFile

from kairos import research_backtest as base
from kairos.backtest import archive_rows, date, write_reports
from kairos.domain import ZERO, SafetyError, dec

SIGNALS = Path(base.__file__).with_name("research_signals.py")
STEPS = (
    ("1a-pullback-recovery", "candidate", ("vwap-pullback",)),
    ("1b-reclaim-closed-arm", "candidate", ("vwap-reclaim",)),
    ("1c-band-recovery", "candidate", ("vwap-reversion",)),
    ("1d-confluence-recovery", "candidate", ("fib-confluence",)),
    ("1e-ema-recapture-ablation", "candidate", ("fib-ema",)),
    ("1f-sweep-recovery", "candidate", ("fib-sweep",)),
    ("2a-extension-volume", "candidate", ("fib-extension",)),
    ("2b-extension-acceptance", "candidate", ("fib-extension",)),
    ("2c-extension-retest", "candidate", ("fib-extension",)),
    ("3a-generic-retracements", "control", ("fib-confluence", "fib-ema")),
    ("3b-unweighted-mean", "control", tuple(base.VARIANTS)),
    ("3c-simple-reversal", "control", ("vwap-reversion", "fib-sweep")),
    ("4a-context-strata", "diagnostic", ()),
    ("4b-completed-60m-trend", "candidate", ("fib-ema",)),
    (
        "5-favorable-atr-trail",
        "candidate",
        ("vwap-pullback", "vwap-reclaim", "vwap-anchored", "fib-ema", "fib-extension"),
    ),
    ("6-adverse-execution", "stress", tuple(base.VARIANTS)),
)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def replace(source, old, new):
    if source.count(old) != 1:
        raise SafetyError("Declared source condition is absent or not unique")
    return source.replace(old, new, 1)


def profile(source, runner, name):
    """Trusted, explicit source edits; no grid or outcome-dependent thresholds."""
    if name == "1a-pullback-recovery":
        source = replace(source, "and (higher_low or micro_break)\n                )", ")")
    elif name == "1b-reclaim-closed-arm":
        source = replace(
            source,
            '        checks.setdefault("pending_setup", self.pending is not None)',
            """        if (self.variant == "vwap-reclaim" and self.pending
                and self.pending["armed_at"] == end and bullish):
            p = self.pending
            stop = min(p["stop"], close - atr)
            target = close + dec(RULES["default_target_r"]) * (close - stop)
            if ZERO < stop < close < target:
                setup = Setup(self.variant, end, close, stop, target, {
                    **p["facts"], **facts, "arming_facts": p["facts"],
                    "armed_at": end, "candidate_id": p["id"], "confirmed_at": end,
                    "immediate_arm_stop": str(p["stop"]), "immediate_arm_target": str(target),
                    "immediate_arm_expiry": p["expires"],
                })
                self.events["confirmed"] += 1
                self.pending = None
        checks.setdefault("pending_setup", self.pending is not None)""",
        )
        runner = replace(
            runner,
            """                "outcome": "confirmed",
            }
        return candidates[key]""",
            """                "outcome": "confirmed",
            }
            if setup.facts.get("immediate_arm_stop") is not None:
                candidates[key].update(
                    arm_clock=clock_facts(setup.closed_at), warmup_arm=setup.closed_at < start,
                    arm_reference=setup.facts["signal_close"], arm_stop=setup.facts["immediate_arm_stop"],
                    arm_target=setup.facts["immediate_arm_target"],
                    expires_at=setup.facts["immediate_arm_expiry"],
                    target_room_at_arm=str(dec(setup.facts["immediate_arm_target"]) - dec(setup.facts["signal_close"])),
                    level_context={},
                )
        return candidates[key]""",
        )
    elif name == "1c-band-recovery":
        source = replace(
            source,
            "                    and higher_low\n                    and bullish",
            "                    and bullish",
        )
    elif name == "1d-confluence-recovery":
        source = replace(
            source,
            "confirm = bullish and close > vwap and (higher_low or micro_break)",
            "confirm = bullish and close > vwap",
        )
    elif name == "1e-ema-recapture-ablation":
        source = replace(source, "                    and close >= self.fast\n", "")
    elif name == "1f-sweep-recovery":
        source = replace(
            source,
            'confirm = close > p["level"] and bullish and (higher_low or micro_break)',
            'confirm = close > p["level"] and bullish',
        )
    elif name == "2a-extension-volume":
        source = replace(
            source,
            """old["close"] <= top < close
                            and baseline
                            and dec(row[6]) >= baseline * dec(RULES["reclaim_volume_multiple"])""",
            'old["close"] <= top < close',
        )
    elif name == "2b-extension-acceptance":
        source = replace(
            source,
            'close >= p["level"] + atr * dec(RULES["breakout_acceptance_atr"])',
            'close >= p["level"]',
        )
    elif name == "2c-extension-retest":
        source = replace(
            source,
            '                    low >= p["level"] - tolerance\n                    and close >=',
            "                    close >=",
        )
    elif name == "3a-generic-retracements":
        source = replace(
            source,
            'top - width * dec(".618"), top - width * dec(".382")',
            'top - width * dec(".586"), top - width * dec(".350")',
        )
        source = source.replace('"fib_382"', '"generic_upper"').replace(
            '"fib_618"', '"generic_lower"'
        )
    elif name == "3b-unweighted-mean":
        source = replace(source, "        volume = dec(row[6])", "        volume = dec(1)")
    elif name == "3c-simple-reversal":
        source = replace(
            source,
            "trigger = bool(sigma and low < vwap - 2 * sigma and close < vwap)",
            'trigger = bool(old["close"] < dec(self.rows[-2][1]) and bullish and close < vwap)',
        )
        start = source.index('                p["rejected"] |=')
        end = source.index('            elif self.variant == "fib-confluence":', start)
        source = (
            source[:start]
            + '                confirm = bullish and close > old["close"] and close < vwap\n'
            + source[end:]
        )
        source = replace(
            source,
            "trigger, stop = low < bottom < close and bullish, low - buffer",
            'trigger, stop = old["close"] < dec(self.rows[-2][1]) and bullish, low - buffer',
        )
        source = replace(
            source,
            'confirm = close > p["level"] and bullish'
            + (
                " and (higher_low or micro_break)"
                if 'confirm = close > p["level"] and bullish and (higher_low or micro_break)'
                in source
                else ""
            ),
            'confirm = bullish and close > old["close"]',
        )
    elif name == "4b-completed-60m-trend":
        source = replace(
            source,
            "        self.events = Counter()",
            """        self.events = Counter()
        self.htf_rows, self.htf_index = (), 0
        self.htf_fast = self.htf_slow = None
        self.htf_count = 0
        self.htf_known_at = None""",
        )
        source = replace(
            source,
            "        setup = None\n",
            """        htf_rising = False
        while self.htf_index < len(self.htf_rows) and self.htf_rows[self.htf_index][0] + 3600 <= end:
            h = self.htf_rows[self.htf_index]
            price = dec(h[4])
            previous_slow = self.htf_slow
            self.htf_fast = price if self.htf_fast is None else self.htf_fast + dec(2) / 21 * (price - self.htf_fast)
            self.htf_slow = price if self.htf_slow is None else self.htf_slow + dec(2) / 51 * (price - self.htf_slow)
            self.htf_count += 1
            self.htf_known_at = h[0] + 3600
            self.htf_index += 1
            self.htf_rising = bool(previous_slow is not None and self.htf_fast > self.htf_slow >= previous_slow)
        htf_rising = self.htf_count >= 50 and getattr(self, "htf_rising", False)
        facts.update(htf_known_at=self.htf_known_at, htf_observed_bars=self.htf_count, htf_rising=htf_rising)
        setup = None
""",
        )
        source = replace(
            source,
            'self.variant == "fib-ema":\n                confirm = (',
            'self.variant == "fib-ema":\n                confirm = (\n                    htf_rising\n                    and',
        )
    elif name == "5-favorable-atr-trail":
        runner = replace(
            runner,
            "    coverage = {name: Counter() for name in",
            "    trailing_updates = []\n    coverage = {name: Counter() for name in",
        )
        runner = replace(
            runner,
            "        checks, setup = signals.push(row, minute)",
            """        checks, setup = signals.push(row, minute)
        if (position and variant in {"vwap-pullback", "vwap-reclaim", "vwap-anchored", "fib-ema", "fib-extension"}
                and not position.get("exit_reason") and signals.tr):
            atr = sum(signals.tr) / len(signals.tr)
            close = dec(row[4])
            entry = position["entry_cost"] / (position["owned"] / (1 - dec(taker) / BPS))
            peak_close = max(position.get("peak_close", entry), close)
            position["peak_close"] = peak_close
            proposed = min(position["target"] - pair.tick, peak_close - 2 * atr)
            if close > entry + 2 * atr and proposed > position["stop"]:
                trailing_updates.append({"position_id": position["id"], "known_at": closed_at,
                    "effective_at": minute + 1, "old_stop": str(position["stop"]), "new_stop": str(proposed),
                    "atr": str(atr), "completed_close": str(close)})
                position["stop"] = proposed""",
        )
        runner = replace(
            runner,
            '"candidates": list(candidates.values()),',
            '"candidates": list(candidates.values()),\n                "trailing_updates": trailing_updates,',
        )
    return source, runner


def module(source, name):
    loaded = ModuleType(name)
    loaded.__file__ = str(Path(base.__file__).with_name(name + ".py"))
    sys.modules[name] = loaded
    exec(compile(source, loaded.__file__, "exec"), loaded.__dict__)
    return loaded


def execute(job):
    archive, start, end, minutes, variant, source, runner_source, scenario_name, economics = job
    begun = time.perf_counter()
    signal_module = module(source, "quarter_signals")
    runtime = module(runner_source, "quarter_runner")
    pair = base.instrument()
    scenario = {**base.SCENARIOS[scenario_name], "name": scenario_name}
    settings = base.pilot_settings(
        pair, minutes, scenario, dec(1000), dec(1000), dec(1000), dec("12.50")
    )
    signals = signal_module.Signals(minutes, variant)
    with ZipFile(archive) as z:
        if hasattr(signals, "htf_rows"):
            signals.htf_rows = tuple(
                archive_rows(z, "XBTUSDT_60.csv", 60, start=start - 7 * 86400, end=end)
            )
        result = asyncio.run(
            runtime.simulate(
                archive_rows(z, "XBTUSDT_1.csv", 1, start=start - 60, end=end),
                archive_rows(
                    z, f"XBTUSDT_{minutes}.csv", minutes, start=start - 2 * 86400, end=end
                ),
                pair,
                settings,
                scenario,
                variant,
                start,
                end,
                8,
                10,
                signals=signals,
                risk_percent=1,
                economic_policy=economics,
            )
        )
    result["runtime_seconds"] = time.perf_counter() - begun
    result["signal_source_sha256"] = digest(source.encode())
    result["executor_source_sha256"] = digest(runner_source.encode())
    for order in result["orders"]:
        assert order["payload"]["ordType"] == "ioc" and order["payload"]["tdMode"] == "cash"
        assert not order["native_attached_stop"]
        if order["side"] == "buy":
            p = order["risk_plan"]
            assert (
                sum(
                    (
                        dec(p[k])
                        for k in (
                            "requested_stop_risk",
                            "dust_risk_reserved",
                            "potential_unprotected_dust_cost",
                        )
                    ),
                    ZERO,
                )
                <= dec(p["capital_budget"])
                <= 10
            )
            f = order["signal_facts"]
            assert f["armed_at"] <= f["confirmed_at"] <= order["created"]
            for k in ("swing_known_at", "anchor_known_at"):
                if f.get(k) is not None:
                    assert int(f[k]) <= f["armed_at"]
            if f.get("htf_known_at") is not None:
                assert f["htf_known_at"] <= f["confirmed_at"]
            if economics == "positive-target":
                assert dec(order["entry_economics"]["net_target_reward_per_gross_unit"]) > 0
    return result


def aggregate(cases):
    closed = [dec(t["realized_R"]) for c in cases for t in c["trades"]]
    return {
        "independent_cases": len(cases),
        "signals": sum(c["activity"]["signals"] for c in cases),
        "filled_entries": sum(c["filled_entry_orders"] for c in cases),
        "positive_target_fills": sum(
            c["diagnostics"]["economics"]["filled_plans"]
            - c["diagnostics"]["economics"]["nonpositive_target_reward"]
            for c in cases
        ),
        "active_case_days": sum(c["activity"]["days_with_fills"] for c in cases),
        "closed_trades": len(closed),
        "mean_closed_R": str(sum(closed, ZERO) / len(closed)) if closed else None,
        "mean_case_return_pct": str(
            sum((dec(c["net_return_pct"]) for c in cases), ZERO) / len(cases)
        ),
        "budget_breaches": sum(c["risk"]["budget_breaches"] for c in cases),
        "halted_cases": sum(c["halted"] for c in cases),
        "zero_fill_cases": [
            f"{c['policy']} {c['minutes']}m" for c in cases if not c["filled_entry_orders"]
        ],
    }


def pair_summary(case):
    return {
        "signals": case["activity"]["signals"],
        "filled_entries": case["filled_entry_orders"],
        "active_days": case["activity"]["days_with_fills"],
        "net_return_pct": case["net_return_pct"],
        "mean_closed_R": case["risk_units"]["mean_closed_R"],
    }


def improves(control, challenger):
    a, b = aggregate(control), aggregate(challenger)
    return bool(
        b["positive_target_fills"] >= a["positive_target_fills"]
        and b["active_case_days"] >= a["active_case_days"]
        and len(b["zero_fill_cases"]) <= len(a["zero_fill_cases"])
        and b["budget_breaches"] <= a["budget_breaches"]
        and b["mean_closed_R"] is not None
        and a["mean_closed_R"] is not None
        and dec(b["mean_case_return_pct"]) > dec(a["mean_case_return_pct"])
        and dec(b["mean_closed_R"]) > dec(a["mean_closed_R"])
    )


def contexts(cases):
    """Descriptive completed facts only; no fitted gates or significance claims."""
    result = {}
    for c in cases:
        groups = {}
        trades = {t["opened_at"]: t for t in c["trades"]}
        for candidate in c["diagnostics"]["candidates"]:
            facts = candidate.get("arming_facts", {})
            levels = candidate.get("level_context", {})
            age = max((v["age_seconds"] for v in levels.values()), default=0)
            volume = dec(facts.get("prior_volume_mean") or 0)
            labels = (
                "regime:" + facts.get("regime", "unknown"),
                "hour:" + str(candidate.get("arm_clock", {}).get("hour_UTC", "unknown")),
                "age:" + ("<6h" if age < 21600 else ">=6h"),
                "prior_volume:" + ("available" if volume else "unavailable"),
            )
            for label in labels:
                g = groups.setdefault(
                    label,
                    {
                        "candidates": 0,
                        "filled": 0,
                        "economic_rejections": 0,
                        "closed_trades": 0,
                        "sold_share_net_pnl": "0",
                        "closed_R_sum": "0",
                    },
                )
                g["candidates"] += 1
                g["filled"] += candidate.get("outcome") == "filled"
                g["economic_rejections"] += candidate.get("outcome") == "blocked_target_economics"
                trade = trades.get(candidate.get("next_open_at"))
                if trade is not None and candidate.get("outcome") == "filled":
                    g["closed_trades"] += 1
                    g["sold_share_net_pnl"] = str(
                        dec(g["sold_share_net_pnl"]) + dec(trade["net_pnl"])
                    )
                    g["closed_R_sum"] = str(dec(g["closed_R_sum"]) + dec(trade["realized_R"]))
        result[f"{c['policy']} {c['minutes']}m"] = groups
    return result


def canonical(case):
    mapping = {}
    for i, order in enumerate(case["orders"]):
        mapping.update(
            {
                order["id"]: f"order-{i}",
                order["client_id"]: f"client-{i}",
                order["run_id"]: "independent-run",
            }
        )

    def walk(value):
        if isinstance(value, str):
            return mapping.get(value, value)
        if isinstance(value, dict):
            return {
                k: walk(v)
                for k, v in value.items()
                if k not in {"runtime_seconds", "signal_source_sha256", "executor_source_sha256"}
            }
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(case)


def run_window(
    archive, start, end, source, runner, affected, scenario, economics, workers, report, path, side
):
    jobs = [
        (
            archive,
            start,
            end,
            m,
            v,
            source if v in affected else report["accepted_signal_source"],
            runner if v in affected else report["accepted_executor_source"],
            scenario,
            economics,
        )
        for m in (5, 15)
        for v in base.VARIANTS
    ]
    result = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(execute, j) for j in jobs]):
            case = future.result()
            result.append(case)
            report["cases"].append({**case, "signal_version": side})
            write_reports(report, path, create=False)
            print(path.stem, case["policy"], case["minutes"], case["net_return_pct"], flush=True)
    return sorted(result, key=lambda c: (c["minutes"], list(base.VARIANTS).index(c["policy"])))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--start", type=date, required=True)
    p.add_argument("--end", type=date, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument(
        "--resume-baseline",
        type=Path,
        help="Verified completed #0 checkpoint from a reporting-only failure; new output directory required",
    )
    args = p.parse_args()
    if args.end <= args.start or not 1 <= args.workers <= 8:
        p.error("Expected a chronological window and 1–8 workers")
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    signal_source, runner_source = SIGNALS.read_text(), Path(base.__file__).read_text()
    declaration = {
        "window": {"start": args.start, "end": args.end},
        "archive_sha256": digest(args.archive.read_bytes()),
        "signal_sha256": digest(signal_source.encode()),
        "executor_sha256": digest(runner_source.encode()),
        "steps": STEPS,
        "series_source_sha256": digest(Path(__file__).read_bytes()),
        "resumed_baseline": {
            "path": str(args.resume_baseline),
            "sha256": digest(args.resume_baseline.read_bytes()),
        }
        if args.resume_baseline
        else None,
        "controls": {
            "initial": "1000",
            "risk_percent": "1",
            "daily_loss": "12.50",
            "maker_bps": "8",
            "taker_bps": "10",
            "order_cap": "1000",
            "exposure_cap": "1000",
            "native_warmup_days": 2,
            "native_60m_warmup_days": 7,
            "deadline_seconds": 14400,
        },
        "anchor_timing": "Existing anchored VWAP already confirms the first completed valid retest/rejection. Do not invent a redundant timing change; anchor/context diagnostics remain in every report.",
        "acceptance": "Carry candidate changes only if positive-target fills and active case-days do not fall, zero-fill cases and risk breaches do not increase, and both mean case marked return and pooled mean closed R improve. Controls/diagnostics/stress do not replace the named setups. No thresholds are fitted to this window.",
        "limitations": "Intentionally volatile selected development period; all steps reuse it, so not a holdout. Proxy candles/fees/fills, gaps, partial exits and permanent entry-loss latches remain unchanged. No automatic restart or terminal liquidation.",
        "ai": "Deferred pending separate approval and supported subscription OAuth contract. No calls, keys, OAuth token reads or paid inference.",
    }
    (args.output_dir / "declaration.json").write_text(json.dumps(declaration, indent=2) + "\n")
    accepted, accepted_name = None, "0-positive-target"
    history = []
    stages = [("0-baseline", "baseline", tuple(base.VARIANTS)), *STEPS]
    for name, role, affected in stages:
        path = args.output_dir / (name + ".json")
        stage_dir = args.output_dir / name
        stage_dir.mkdir()
        started = time.perf_counter()
        report = {
            "title": "VWAP + Fibonacci · three-month " + name,
            "scope_note": declaration["limitations"],
            "symbol": "BTC/USDT · Kraken inputs / OKX spot target",
            "source": str(args.archive),
            "target_venue": "OKX U.S. spot",
            "broker_verified": False,
            "historical_final": False,
            "status": "running",
            "planned_cases": 32,
            "cases": [],
            "start": args.start,
            "end": args.end,
            "assumptions": base.ASSUMPTIONS,
            "rules": base.RULES,
            "risk_policy": {"initial_capital": "1000", "planned_percent": "1", "guaranteed": False},
            "instrument_snapshot": json.loads(base.INSTRUMENT.read_text()),
            "execution_revision": base.EXECUTION_REVISION,
            "variant_labels": base.VARIANTS,
            "comparison_labels": {
                "v1": "diagnostic" if role == "baseline" else accepted_name,
                "v2": "positive-target" if role == "baseline" else name,
            },
            "accepted_signal_source": signal_source,
            "accepted_executor_source": runner_source,
            "trial": {"name": name, "role": role, "affected": affected, "parent": accepted_name},
            "predeclared_series": declaration,
        }
        if name == "3b-unweighted-mean":
            report["assumptions"] = [
                "Control retains volume-weighted HLC3; the reference uses the same session/anchor with unweighted HLC3 mean and sigma. Raw volumes and execution liquidity are unchanged."
                if "population variance" in a
                else a
                for a in base.ASSUMPTIONS
            ]
        elif name == "3c-simple-reversal":
            report["scope_note"] += (
                " Reference replaces the band/sweep trigger with a simple prior-negative/current-bullish bar pattern conditioned on the same known structural/mean context; not an unconditional reversal or observed stop hunt."
            )
        elif name == "5-favorable-atr-trail":
            report["assumptions"] = [
                "Both arms retain frozen initial plans and bot-managed protection. The trend-family trial only raises stops after completed favorable closes; targets, initial sizing and the 4h deadline stay fixed. Effective stop updates are recorded; no exchange-held protection."
                if "Stops/targets are BOT-MANAGED" in a
                else a
                for a in base.ASSUMPTIONS
            ]
        write_reports(report, path, create=True)
        try:
            trial_signal, trial_runner = profile(signal_source, runner_source, name)
            (stage_dir / "research_signals.py").write_text(trial_signal)
            (stage_dir / "research_backtest.py").write_text(trial_runner)
            if role == "baseline" and args.resume_baseline:
                saved = json.loads(args.resume_baseline.read_text())
                frozen = saved["predeclared_series"]
                for k in ("archive_sha256", "signal_sha256", "executor_sha256", "window"):
                    assert frozen[k] == declaration[k], ("resume", k)
                assert len(saved["cases"]) == 32
                control, challenger = [], []
                for c in saved["cases"]:
                    assert c["signal_source_sha256"] == declaration["signal_sha256"]
                    assert c["executor_source_sha256"] == declaration["executor_sha256"]
                    clean = {k: v for k, v in c.items() if k != "signal_version"}
                    (control if c["economic_policy"] == "diagnostic" else challenger).append(clean)

                def order(c):
                    return c["minutes"], list(base.VARIANTS).index(c["policy"])

                control.sort(key=order)
                challenger.sort(key=order)
                assert len(control) == len(challenger) == 16
                assert len({(c["policy"], c["minutes"]) for c in control}) == 16
                assert len({(c["policy"], c["minutes"]) for c in challenger}) == 16
            elif role == "baseline":
                control = run_window(
                    args.archive,
                    args.start,
                    args.end,
                    signal_source,
                    runner_source,
                    tuple(base.VARIANTS),
                    "base",
                    "diagnostic",
                    args.workers,
                    report,
                    path,
                    "v1",
                )
            elif role == "stress":
                control = run_window(
                    args.archive,
                    args.start,
                    args.end,
                    SIGNALS.read_text(),
                    Path(base.__file__).read_text(),
                    tuple(base.VARIANTS),
                    "stress",
                    "positive-target",
                    args.workers,
                    report,
                    path,
                    "v1",
                )
            else:
                control = accepted
                report["cases"] = [{**c, "signal_version": "v1"} for c in control]
            if not (role == "baseline" and args.resume_baseline):
                challenger = run_window(
                    args.archive,
                    args.start,
                    args.end,
                    trial_signal,
                    trial_runner,
                    affected if role not in ("baseline", "stress") else tuple(base.VARIANTS),
                    "stress" if role == "stress" else "base",
                    "positive-target",
                    args.workers,
                    report,
                    path,
                    "v2",
                )
            pairs = []
            for a, b in zip(control, challenger, strict=True):
                assert (a["policy"], a["minutes"]) == (b["policy"], b["minutes"])
                for k in (
                    "settings",
                    "scenario",
                    "start",
                    "end",
                    "pair_rules",
                    "maker_fee_bps",
                    "taker_fee_bps",
                    "observed_minutes",
                    "missing_minutes",
                ):
                    assert a[k] == b[k], (name, k)
                if role != "baseline" and a["policy"] not in affected:
                    assert canonical(a) == canonical(b), (
                        name,
                        a["policy"],
                        a["minutes"],
                        "unexpected unaffected-case difference",
                    )
                pairs.append(
                    {
                        "policy": a["policy"],
                        "minutes": a["minutes"],
                        "v1": pair_summary(a),
                        "v2": pair_summary(b),
                    }
                )
            report["cases"] = []
            for a, b in zip(control, challenger, strict=True):
                report["cases"].extend(
                    [{**a, "signal_version": "v1"}, {**b, "signal_version": "v2"}]
                )
            passed = role == "candidate" and improves(control, challenger)
            if role == "baseline" or passed:
                accepted, accepted_name = (
                    challenger,
                    name + ("-positive-target" if role == "baseline" else ""),
                )
                signal_source, runner_source = trial_signal, trial_runner
            summary = {
                "name": name,
                "role": role,
                "accepted": passed,
                "control": aggregate(control),
                "challenger": aggregate(challenger),
                "wall_seconds": time.perf_counter() - started,
                "signal_sha256": digest(trial_signal.encode()),
                "executor_sha256": digest(trial_runner.encode()),
                "parent": report["trial"]["parent"],
            }
            history.append(summary)
            report.update(
                status="complete",
                paired_comparison=pairs,
                aggregates={"control": summary["control"], "challenger": summary["challenger"]},
                selection="accepted"
                if passed
                else "baseline established"
                if role == "baseline"
                else "evidence only"
                if role != "candidate"
                else "rejected; preceding control retained",
                wall_clock_seconds=summary["wall_seconds"],
            )
            if role == "diagnostic":
                report["context_strata"] = contexts(challenger)
                (stage_dir / "context-strata.json").write_text(
                    json.dumps(report["context_strata"], indent=2) + "\n"
                )
            del report["accepted_signal_source"], report["accepted_executor_source"]
            write_reports(report, path, create=False)
            (args.output_dir / "history.json").write_text(json.dumps(history, indent=2) + "\n")
            print(json.dumps(summary), flush=True)
        except BaseException as exc:
            # Preserve the error even if an unfinished comparison cannot render.
            report.pop("paired_comparison", None)
            report.update(
                status="failed", error=str(exc), wall_clock_seconds=time.perf_counter() - started
            )
            write_reports(report, path, create=False)
            raise
    (args.output_dir / "accepted-research-signals.py").write_text(signal_source)
    (args.output_dir / "accepted-research-backtest.py").write_text(runner_source)
    (args.output_dir / "COMPLETE.json").write_text(
        json.dumps(
            {
                "accepted": accepted_name,
                "steps": len(history),
                "ai": "awaiting separate OAuth/subscription shadow approval",
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
