"""Kraken-only daily price indices, NOT a portfolio/execution backtest or trading strategy."""

import argparse
import csv
import hashlib
import io
import json
import platform
import statistics
import subprocess
import zipfile
from pathlib import Path

from kairos.domain import SafetyError, dec
from kairos.market_data import CandleHistory
from kairos.research.artifacts import Registry, utc
from kairos.research.statistics import paired_interval, summary

DAY = 86400
EXCLUDED_START = utc("2026-01-01T00:00:00Z")


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def parse_daily(body, start, end):
    if end > EXCLUDED_START:
        raise SafetyError("Consumed 2026 market period is excluded from this pilot")
    history, rows = CandleHistory(None, 1440, 1), []
    for row in csv.reader(io.StringIO(body.decode("utf-8"))):
        if len(row) != 7:
            raise SafetyError("Expected Kraken headerless seven-column OHLCVT")
        ts = int(row[0])
        if not start <= ts < end:
            continue  # No excluded price/volume values enter validation/features/metrics.
        checked = history.validate([row[0], *row[1:5], "0", row[5], row[6]], vwap_optional=True)
        rows.append(
            {
                "start": checked[0],
                "open": checked[1],
                "high": checked[2],
                "low": checked[3],
                "close": checked[4],
                "volume": checked[6],
                "trades": checked[7],
            }
        )
    expected = list(range(start, end, DAY))
    if [r["start"] for r in rows] != expected:
        raise SafetyError("Daily coverage must be contiguous and unique; no gap filling")
    return rows


def signal(rows, index, lookback, delay):
    at = rows[index]["start"]
    known = [r for r in rows[:index] if r["start"] + DAY + delay <= at]
    if len(known) < lookback + 1:
        raise SafetyError("Insufficient available signal history")
    last, first = known[-1], known[-lookback - 1]
    return dec(last["close"]) / dec(first["close"]) - 1, last["start"] + DAY + delay


def price_index(rows, plan, arm, costs):
    """Unfunded normalized long/cash index; no minimum-lot or capacity claims.

    Full transitions use only the opening price plus declared frictions. Liquidation
    marks include exit costs. The index is deliberately NOT subject to Kairos risk
    controls and cannot be promoted as an executable portfolio or live strategy.
    """
    if arm not in {"momentum", "cash", "passive"}:
        raise SafetyError("Unregistered price-index arm")
    fee, half, slip = (
        dec(costs[k]) / scale
        for k, scale in (("fee_bps", 10000), ("spread_bps", 20000), ("slippage_bps", 10000))
    )
    if not 0 <= fee < 1 or not 0 <= half < 1 or not 0 <= slip < 1:
        raise SafetyError("Invalid index cost assumption")
    buy_factor, sell_factor = (
        (1 + half) * (1 + slip) * (1 + fee),
        (1 - half) * (1 - slip) * (1 - fee),
    )
    cash, quantity, previous, peak = dec(1), dec(0), dec(1), dec(1)
    fees, turnover, max_dd = dec(0), dec(0), dec(0)
    decisions, episodes, returns, marks = [], [], {}, []
    entry = None
    start, end = utc(plan["evaluation_start"]), utc(plan["last_mark"])
    if end >= EXCLUDED_START or start >= end:
        raise SafetyError("Invalid development-only index window")
    for i, row in enumerate(rows[:-1]):
        if not start <= row["start"] < end:
            continue
        following = rows[i + 1]
        if following["start"] != row["start"] + DAY or following["start"] > end:
            raise SafetyError("Missing next daily opening")
        momentum, available = signal(
            rows, i, plan["lookback_days"], plan["publication_delay_seconds"]
        )
        long = arm == "passive" or (arm == "momentum" and momentum > 0)
        opening = dec(row["open"])
        if long and not quantity:
            entry = {"entry": row["start"], "starting_index": str(cash)}
            quantity = cash / (opening * buy_factor)
            fees += quantity * opening * (1 + half) * (1 + slip) * fee
            turnover += quantity * opening
            cash = dec(0)
        elif not long and quantity:
            cash = quantity * opening * sell_factor
            fees += quantity * opening * (1 - half) * (1 - slip) * fee
            turnover += quantity * opening
            episodes.append(
                {
                    **entry,
                    "exit": row["start"],
                    "boundary": False,
                    "net_pct": float((cash / dec(entry["starting_index"]) - 1) * 100),
                }
            )
            quantity, entry = dec(0), None
        mark = cash + quantity * dec(following["open"]) * sell_factor
        returns[str(following["start"])] = float(mark / previous - 1)
        peak = max(peak, mark)
        max_dd = max(max_dd, (peak - mark) / peak * 100)
        decisions.append(
            {
                "at": row["start"],
                "information_available": available,
                "momentum": str(momentum),
                "long": long,
            }
        )
        marks.append({"at": following["start"], "index": str(mark)})
        previous = mark
    if not marks or len(marks) != (end - start) // DAY:
        raise SafetyError("Incomplete evaluation window")
    if quantity:
        final_price = dec(next(r["open"] for r in rows if r["start"] == end))
        fees += quantity * final_price * (1 - half) * (1 - slip) * fee
        turnover += quantity * final_price
        episodes.append(
            {
                **entry,
                "exit": end,
                "boundary": True,
                "net_pct": float((previous / dec(entry["starting_index"]) - 1) * 100),
            }
        )
    return {
        "arm": arm,
        "net_index_change_pct": float((previous - 1) * 100),
        "ending_index": str(previous),
        "fees_per_initial_index_unit": str(fees),
        "turnover_per_initial_index_unit": str(turnover),
        "daily_mark_drawdown_pct": float(max_dd),
        "signal_closed_episodes": sum(not e["boundary"] for e in episodes),
        "terminal_censored_episodes": sum(e["boundary"] for e in episodes),
        "active_days": sum(d["long"] for d in decisions),
        "mean_daily_return": statistics.mean(returns.values()),
        **summary(list(returns.values())),
        "daily_returns": returns,
        "marks": marks,
        "decisions": decisions,
        "episodes": episodes,
        "deployable": False,
    }


def run(root):
    root = Path(root)
    plan = json.loads(Path("research/KRAKEN_PLAN.json").read_text())
    if plan["venue"] != "kraken" or plan["alpaca_data_allowed"]:
        raise SafetyError("Kraken-only pilot")
    registry = Registry(root)
    try:
        spec = {
            "operation": "kraken_daily_price_pilot",
            "plan": registry.artifacts.put(plan),
            "code": file_hash(Path(__file__)),
            "repository_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "python": platform.python_version(),
            "historical_final": False,
        }
        with registry.trial(spec) as family:
            archive = root / "Kraken_OHLCVT_Full_2026Q2.zip"
            with registry.trial({**spec, "operation": "kraken_archive_selection"}) as acquisition:
                if file_hash(archive) != plan["archive_sha256"]:
                    raise SafetyError("Official Kraken archive checksum mismatch")
                dataset, provenance = {}, {}
                with zipfile.ZipFile(archive) as z:
                    for symbol in plan["universe"]:
                        matches = [
                            i for i in z.infolist() if Path(i.filename).name == symbol + "_1440.csv"
                        ]
                        if len(matches) != 1 or matches[0].file_size > 5 * 1024**2:
                            raise SafetyError("Missing/ambiguous/oversized Kraken daily member")
                        info = matches[0]
                        body = z.read(info)  # ZIP reader verifies selected-member CRC.
                        selected = parse_daily(body, utc(plan["data_start"]), utc(plan["data_end"]))
                        dataset[symbol] = selected
                        provenance[symbol] = {
                            "member": info.filename,
                            "member_sha256": hashlib.sha256(body).hexdigest(),
                            "crc32": info.CRC,
                            "rows": len(selected),
                            "selected_rows": registry.artifacts.put(selected),
                        }
                dataset_key = registry.record(
                    acquisition,
                    "kraken_dataset",
                    {
                        "venue": "kraken",
                        "archive_sha256": plan["archive_sha256"],
                        "partitions": provenance,
                        "availability": plan["availability"],
                        "historical_rules_verified": False,
                        "historical_quotes_or_queue": False,
                        "alpaca_inputs": [],
                    },
                )
            report = {
                "spec": spec,
                "dataset": dataset_key,
                "assets": {},
                "status": "exploratory_development_only",
                "broker_performance_supported": False,
                "old_execution_gate_passed": False,
            }
            for symbol, rows in dataset.items():
                report["assets"][symbol] = {}
                for name, costs in plan["costs"].items():
                    outputs, details = {}, {}
                    for arm in plan["arms"]:
                        with registry.trial(
                            {
                                **spec,
                                "operation": "price_index",
                                "symbol": symbol,
                                "arm": arm,
                                "scenario": name,
                                "dataset": dataset_key,
                            }
                        ) as trial:
                            result = price_index(rows, plan, arm, costs)
                            key = registry.record(trial, "index", result)
                        outputs[arm] = {
                            "artifact": key,
                            **{
                                k: v
                                for k, v in result.items()
                                if k not in {"daily_returns", "marks", "decisions", "episodes"}
                            },
                        }
                        details[arm] = result
                    settings = plan["inference"]
                    intervals = {
                        other: paired_interval(
                            details["momentum"]["daily_returns"],
                            details[other]["daily_returns"],
                            seed=settings["seed"],
                            samples=settings["bootstrap_samples"],
                            block=settings["block_days"],
                            alpha=settings["family_alpha"] / settings["primary_comparisons"],
                        )
                        for other in ("cash", "passive")
                    }
                    report["assets"][symbol][name] = {
                        "indices": outputs,
                        "paired_mean_daily_intervals": intervals,
                        "adequate_sample": details["momentum"]["days"] >= settings["minimum_days"]
                        and details["momentum"]["signal_closed_episodes"]
                        >= settings["minimum_signal_closed_episodes"],
                    }
            checks = []
            for asset in report["assets"].values():
                base, stress = asset["base_assumption"], asset["stress_assumption"]
                checks.append(
                    {
                        "adequate_sample": base["adequate_sample"],
                        "positive_base_and_stress": base["indices"]["momentum"][
                            "net_index_change_pct"
                        ]
                        > 0
                        and stress["indices"]["momentum"]["net_index_change_pct"] > 0,
                        "positive_adjusted_lower_bounds": all(
                            v["lower"] is not None and v["lower"] > 0
                            for v in base["paired_mean_daily_intervals"].values()
                        ),
                    }
                )
            report["exploratory_checks"] = dict(zip(plan["universe"], checks, strict=True))
            report["exploratory_criteria_met"] = all(all(c.values()) for c in checks)
            key = registry.record(family, "kraken_report", report)
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
