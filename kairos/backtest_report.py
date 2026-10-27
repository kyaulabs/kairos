"""Self-contained KAIROS HTML reports; no browser libraries or remote assets."""

import base64
import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from kairos.domain import dec

STATIC = Path(__file__).with_name("static")


def stamp(ts):
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%d")


def instant(ts):
    return (
        datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%d %H:%M:%S")
        if ts is not None
        else "—"
    )


def number(value, *, percent=False, signed=False):
    value = dec(value)
    css = "positive" if value > 0 else "negative" if value < 0 else "muted"
    text = f"{value:+.2f}" if signed else f"{value:.2f}"
    return f'<span class="{css if signed else ""}">{text}{"%" if percent else ""}</span>'


def table(headers, rows, caption=""):
    heading = "".join(f'<th scope="col">{escape(h)}</th>' for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    title = f"<caption>{escape(caption)}</caption>" if caption else ""
    return f'<div class="table-scroll"><table>{title}<thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>'


def yearly(case):
    """Calendar periods, carried equity; never reset positions at year boundaries."""
    result = []
    previous = dec(case["settings"]["paper_balance"])
    ends = {}
    for ts, equity in sorted(case["daily_equity"].items(), key=lambda item: int(item[0])):
        ends[datetime.fromtimestamp(int(ts), UTC).year] = dec(equity)
    for year, end in ends.items():
        trades = sum(
            datetime.fromtimestamp(t["closed_at"], UTC).year == year for t in case["trades"]
        )
        result.append(
            {
                "year": year,
                "return_pct": str((end / previous - 1) * 100),
                "ending_equity": str(end),
                "trades": trades,
            }
        )
        previous = end
    return result


def equity_chart(case, index):
    initial = dec(case["settings"]["paper_balance"])
    data = [(case["start"], 0.0)] + [
        (int(ts) + 86400, float((dec(value) / initial - 1) * 100))
        for ts, value in sorted(case["daily_equity"].items(), key=lambda item: int(item[0]))
    ]
    if len(data) < 2:
        return "<p>No equity observations.</p>"
    low, high = min(v for _, v in data), max(v for _, v in data)
    margin = max((high - low) * 0.12, 0.1)
    low, high = low - margin, high + margin
    start, end = data[0][0], data[-1][0]

    def x(ts):
        return 64 + (ts - start) / max(1, end - start) * 896

    def y(value):
        return 20 + (high - value) / (high - low) * 220

    points = " ".join(f"{x(ts):.2f},{y(value):.2f}" for ts, value in data)
    grid = ""
    for i in range(5):
        value = low + (high - low) * i / 4
        height = y(value)
        grid += f'<line class="grid" x1="64" x2="960" y1="{height:.2f}" y2="{height:.2f}"/><text x="56" y="{height + 4:.2f}" text-anchor="end">{value:+.2f}%</text>'
    return f'''<svg class="chart" viewBox="0 0 1000 280" role="img" aria-labelledby="chart-{index}">
<title id="chart-{index}">Daily marked return, {escape(case.get("signal_version", ""))} {escape(case["policy"])}, {case["minutes"]} minutes, {escape(case["scenario"]["name"])}</title>
{grid}<line class="baseline" x1="64" x2="960" y1="{y(0):.2f}" y2="{y(0):.2f}"/>
<polyline class="equity" points="{points}"/><text x="64" y="268">{stamp(start)}</text>
<text x="960" y="268" text-anchor="end">{stamp(end)}</text></svg>
<p class="muted">Daily net liquidation marks · dashed line: cash · reported drawdown includes minute-path observations.</p>'''


def render(report, json_name):
    palette = (STATIC / "brand/palette.css").read_text()
    css = (STATIC / "backtest.css").read_text()
    logo = base64.b64encode((STATIC / "brand/kairos-wordmark-on-dark.svg").read_bytes()).decode()
    cases = report["cases"]
    comparison = []
    activity_rows = []
    economics_rows = []
    sections = []
    for index, case in enumerate(cases, 1):
        label = f"{case['policy']} · {case['minutes']}m · {case['scenario']['name']}"
        if case.get("signal_version"):
            label = f"{case['signal_version']} · {label}"
        if case.get("economic_policy") == "positive-target":
            label = f"positive-target · {label}"
        total = case["observed_minutes"] + case["missing_minutes"]
        coverage = case["observed_minutes"] / total * 100 if total else 0
        risk = case.get("risk")
        state = (
            "RISK BREACH"
            if risk and risk["budget_breaches"]
            else "HALTED"
            if case["halted"]
            else "Input gaps"
            if coverage < 95
            else "No entries"
            if not case["entry_attempts"]
            else "Small sample"
            if case["completed_trades"] < 30
            else "Simulation only"
        )
        comparison.append(
            [
                f'<a href="#case-{index}">{escape(label)}</a>',
                str(case["entry_attempts"]),
                str(case["completed_trades"]),
                number(case["net_return_pct"], percent=True, signed=True),
                number(case["path_drawdown_pct"], percent=True),
                number(case["paid_fees"]),
                f"{coverage:.2f}%",
                escape(state),
            ]
        )
        activity = case.get("activity")
        activity_metrics = ""
        if activity:
            idle = activity["longest_idle_hours"]
            activity_rows.append(
                [
                    f'<a href="#case-{index}">{escape(label)}</a>',
                    str(activity["signals"]),
                    f"{activity['signals_per_day']:.2f}",
                    f"{activity['filled_entries_per_day']:.2f}",
                    f"{activity['entry_fill_rate_pct']:.1f}%",
                    f"{activity['days_with_fills']}/{activity['study_days']:g}",
                    f"{idle:.1f}h",
                ]
            )
            activity_metrics = (
                '<h3>Trading activity</h3><div class="metrics">'
                + "".join(
                    f'<div class="metric"><span>{escape(name)}</span><strong>{value}</strong></div>'
                    for name, value in [
                        ("Valid setups", str(activity["signals"])),
                        ("Signals / day", f"{activity['signals_per_day']:.2f}"),
                        ("Filled entries / day", f"{activity['filled_entries_per_day']:.2f}"),
                        ("Entry fill rate", f"{activity['entry_fill_rate_pct']:.1f}%"),
                        (
                            "Days with fills",
                            f"{activity['days_with_fills']}/{activity['study_days']:g}",
                        ),
                        ("Longest idle", f"{idle:.1f}h"),
                    ]
                )
                + "</div>"
            )
            activity_metrics += table(
                ["Day (UTC)", "Valid setups", "Filled entries"],
                [
                    [
                        stamp(ts),
                        str(activity["signals_by_day"].get(str(ts), 0)),
                        str(activity["fills_by_day"].get(str(ts), 0)),
                    ]
                    for ts in range(case["start"], case["end"], 86400)
                ],
                "Daily activity; includes no-trade days, not just successful fills.",
            )
        diagnostic_tables = ""
        diagnostics = case.get("diagnostics")
        if diagnostics:
            economy = diagnostics["economics"]
            economics_rows.append(
                [
                    f'<a href="#case-{index}">{escape(label)}</a>',
                    str(economy["filled_plans"]),
                    str(economy.get("economic_rejections", 0)),
                    str(economy["nonpositive_target_reward"]),
                    str(economy["below_one_net_R"]),
                    f"{economy['losing_target_episodes']}/{economy['closed_target_episodes']}",
                    str(economy["losing_targets_with_multiple_IOCs"]),
                    str(economy["losing_targets_crossed_stop"]),
                ]
            )
            diagnostic_tables = table(
                [
                    "Armed (UTC)",
                    "Confirmed (UTC)",
                    "Confirmation bars",
                    "Outcome",
                    "Net planned R",
                    "Stop widening",
                ],
                [
                    [
                        instant(c.get("armed_at")),
                        instant(c.get("confirmed_at")),
                        escape(str(c.get("confirmation_bars", "—"))),
                        escape(c["outcome"]),
                        number(c["entry_economics"]["net_planned_R"], signed=True)
                        if c.get("entry_economics", {}).get("net_planned_R") is not None
                        else "—",
                        number(c["confirmation_stop_widening"])
                        if c.get("confirmation_stop_widening") is not None
                        else "—",
                    ]
                    for c in diagnostics["candidates"][-200:]
                ],
                "Latest 200 candidates, including expiry, blockers and unfilled IOC orders; full prices and causal facts in JSON.",
            ) + table(
                [
                    "First trigger (UTC)",
                    "Reason",
                    "IOC attempts",
                    "Last-fill delay (s)",
                    "Average exit",
                    "Net sold P&L",
                    "Later stop crossing",
                ],
                [
                    [
                        instant(e["first_trigger"]["observed_at"] if e["first_trigger"] else None),
                        escape(
                            e["first_trigger"]["reason"] if e["first_trigger"] else "Still held"
                        ),
                        str(e["IOC_attempts"]),
                        escape(str(e["last_fill_delay_seconds"])),
                        number(e["average_exit_fill_price"])
                        if e["average_exit_fill_price"] is not None
                        else "—",
                        number(e["net_sold_pnl"], signed=True),
                        instant(e["stop_crossed_after_target_at"]),
                    ]
                    for e in diagnostics["exits"][-200:]
                ],
                "Latest 200 exit episodes; first reason remains latched. Open episodes and retained BTC are not full liquidations.",
            )
            diagnostic_tables = f'<details><summary>Entry economics and exit timing</summary>{diagnostic_tables}<p class="muted">Net planned R projects the fixed target under assumed costs, not expected profit. Stop widening is arm stop minus confirmed stop. Prior level contacts count completed bars, not independent bounces. Clock coverage, rounding, sold-share cost attribution and every candidate are in JSON.</p></details>'
        annual = table(
            ["Year", "Net return", "Ending equity", "Closed trades"],
            [
                [
                    str(y["year"]),
                    number(y["return_pct"], percent=True, signed=True),
                    number(y["ending_equity"]),
                    str(y["trades"]),
                ]
                for y in yearly(case)
            ],
            "Carried portfolio; partial years remain partial. No annual restarts.",
        )
        gates = table(
            ["Check", "Windows"],
            [[escape(k), str(v)] for k, v in case["gate_counts"].items()],
            "One observation per assessed native window; gate failures overlap.",
        )
        lifecycle = (
            table(
                ["Setup lifecycle", "Count"],
                [[escape(k), str(v)] for k, v in activity.get("setup_lifecycle", {}).items()],
            )
            if activity and activity.get("setup_lifecycle")
            else ""
        )
        risk_notice = (
            f'<p class="warning">Bot-managed stops, not native attached orders. Planned risk cap: {escape(risk["percent"])}% including assumed fees/slippage. Largest requested stop risk: {number(risk["max_requested_stop_risk"])} · filled stop risk: {number(risk["max_filled_stop_risk"])} · largest marked/realized position loss: {number(risk["max_observed_position_loss"])} · budget breaches: {risk["budget_breaches"]}. Stop execution overruns: {risk["stop_execution_overruns"]}. This is not a guaranteed loss cap.</p>'
            if risk
            else ""
        )
        reasons = table(
            ["Decision reason", "Checks"],
            [
                [escape(k), str(v)]
                for k, v in sorted(case["decision_reasons"].items(), key=lambda item: -item[1])
            ],
        )
        trades = (
            table(
                ["Opened (UTC)", "Closed (UTC)", "Net P&L", "Exit"],
                [
                    [
                        stamp(t["opened_at"]),
                        stamp(t["closed_at"]),
                        number(t["net_pnl"], signed=True),
                        escape(t["reason"] or "Unknown"),
                    ]
                    for t in case["trades"][-200:]
                ],
                report.get(
                    "trade_description",
                    "Latest 200 closed owned lineages; complete orders and trades are in JSON.",
                ),
            )
            if case["trades"]
            else "<p>No completed owned lineages.</p>"
        )
        metrics = "".join(
            f'<div class="metric"><span>{escape(name)}</span><strong>{value}</strong></div>'
            for name, value in [
                ("Net return", number(case["net_return_pct"], percent=True, signed=True)),
                ("Path drawdown", number(case["path_drawdown_pct"], percent=True)),
                ("Closed trades", str(case["completed_trades"])),
                ("Fees · quote", number(case["paid_fees"])),
                ("Time in market", number(case["time_in_market_pct"], percent=True)),
                ("Retained quantity", escape(case["retained_quantity"])),
            ]
        )
        coverage_notice = (
            f'<p class="warning">Observed execution minutes: {coverage:.2f}%. Absent candles may mean no trades or missing data, not worker downtime; no prices were invented for them.</p>'
            if coverage < 95
            else ""
        )
        halt = (
            f'<p class="warning">Halt: {escape(case["halt_reason"] or "unknown")} · {stamp(case["halt_at"] or case["end"])}</p>'
            if case["halted"]
            else ""
        )
        configuration = {
            "settings": case["settings"],
            "scenario": case["scenario"],
            "maker_fee_bps": case["maker_fee_bps"],
            "taker_fee_bps": case["taker_fee_bps"],
            "pair_rules": case["pair_rules"],
            **({"economic_policy": case["economic_policy"]} if "economic_policy" in case else {}),
            **({"entry_execution": case["entry_execution"]} if "entry_execution" in case else {}),
            **({"risk": risk} if risk else {}),
        }
        sections.append(f"""<section class="panel" id="case-{index}"><span class="badge">{escape(state)}</span>
<h2>{escape(label)}</h2>{halt}{coverage_notice}{risk_notice}{activity_metrics}<div class="metrics">{metrics}</div>{equity_chart(case, index)}
<p class="muted">Full-allocation holding reference: {number(case["full_allocation_holding_return_pct"], percent=True, signed=True)}.
Not exposure-matched: this strategy uses an order cap of {escape(case["settings"]["order_size"])} and exposure cap of {escape(case["settings"]["max_exposure"])}.</p>
{annual}<details><summary>Why it traded—or waited</summary>{lifecycle}<div class="columns"><div>{gates}</div><div>{reasons}</div></div>
<p>{"Valid closed-bar setups" if activity else "Distinct multi-bar candidates"}: {case["distinct_candidates"]} · Filled entry orders: {case["filled_entry_orders"]} · Unfilled entry orders: {case["unfilled_entry_orders"]}</p></details>
{diagnostic_tables}<details><summary>Closed trades</summary>{trades}</details>
<details><summary>Exact configuration and assumptions</summary><pre>{escape(json.dumps(configuration, indent=2))}</pre></details></section>""")
    assumptions = "".join(f"<li>{escape(item)}</li>" for item in report["assumptions"])
    period = (
        f"{stamp(cases[0]['start'])} — {stamp(cases[0]['end'])} (end exclusive, UTC)"
        if cases
        else f"{stamp(report['start'])} — {stamp(report['end'])} (end exclusive, UTC)"
        if "start" in report
        else "No completed cases yet"
    )
    symbol = escape(report.get("symbol", "Kraken archive"))
    state = escape(report.get("status", "complete"))
    title = escape(report.get("title", "HTF comparison"))
    activity_panel = (
        f'<section class="panel"><h2>Activity first</h2>{table(["Setup · interval · scenario", "Signals", "Signals / day", "Filled / day", "Fill rate", "Days with fills", "Longest idle"], activity_rows)}<p class="muted">Signals, attempts, partial fills and completed trades are different. Fill rate counts orders with any fill, including partials—not the percentage of requested size filled. Idle time includes the study boundaries. Activity is not proof of profitability.</p></section>'
        if activity_rows
        else ""
    )
    economics_panel = (
        f'<section class="panel"><h2>Entry economics</h2>{table(["Setup · interval · scenario", "Filled plans", "Economic rejections", "Nonpositive net target", "Below 1 net R", "Losing / closed Targets", "Losing Targets: multiple IOCs", "Losing Targets: later stop"], economics_rows)}<p class="muted">Counts describe independent cases, not portfolio returns. Attribution overlaps. A Target trigger is not a profitable exit; projections are not expectancy. Diagnostic policy preserves baseline entries; positive-target is an opt-in research entry gate, never a delay to owned protection.</p></section>'
        if economics_rows
        else ""
    )
    paired_panel = ""
    if report.get("paired_comparison"):
        names = report.get("comparison_labels", {"v1": "v1", "v2": "v2"})
        paired_rows = []
        for pair in report["paired_comparison"]:
            v1, v2 = pair["v1"], pair["v2"]
            paired_rows.append(
                [
                    escape(f"{pair['policy']} · {pair['minutes']}m"),
                    f"{v1['signals']} / {v2['signals']}",
                    f"{v1['filled_entries']} / {v2['filled_entries']}",
                    f"{v1['active_days']} / {v2['active_days']}",
                    number(v1["net_return_pct"], percent=True, signed=True),
                    number(v2["net_return_pct"], percent=True, signed=True),
                    number(v1["mean_closed_R"], signed=True)
                    if v1["mean_closed_R"] is not None
                    else "—",
                    number(v2["mean_closed_R"], signed=True)
                    if v2["mean_closed_R"] is not None
                    else "—",
                ]
            )
        paired_panel = f'<section class="panel"><h2>Matched {escape(names["v1"])} / {escape(names["v2"])} comparison</h2>{table(["Setup · interval", f"Signals {names['v1']} / {names['v2']}", f"Filled {names['v1']} / {names['v2']}", f"Active days {names['v1']} / {names['v2']}", f"Net return {names['v1']}", f"Net return {names['v2']}", f"Mean closed R {names['v1']}", f"Mean closed R {names['v2']}"], paired_rows)}<p class="muted">{escape(report.get("comparison_note", "Identical capital, execution and risk policy; only the declared research rules differ."))} Closed R = sold-share net P&amp;L / sold-share planned stop risk of the actual entry fill. Fees are included; retained BTC and open positions remain in portfolio equity, not closed R. Means compare independent runs—not a combined portfolio.</p></section>'
    scope_note = escape(
        report.get(
            "scope_note",
            "Existing strategies only. No parameter optimization, Jev reconstruction or automatic strategy removal.",
        )
    )
    return f'''<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>KAIROS · {title}</title>
<style>{palette}\n{css}</style></head><body><main>
<header><img class="logo" src="data:image/svg+xml;base64,{logo}" alt="KAIROS"><div><div class="eyebrow">Historical strategy research</div>
<h1>{title}</h1><p class="muted">{symbol} · {escape(period)}</p></div><span class="badge">{state} · {len(cases)} simulations</span></header>
<div class="notice"><strong>Simulated execution—not a live-performance claim.</strong>
<p>{scope_note}
Positive results need robustness checks; low activity and losses remain visible.</p></div>
{paired_panel}{activity_panel}{economics_panel}<section class="panel"><h2>Comparison</h2>{table(["Policy · interval · scenario", "Attempts", "Trades", "Net return", "Drawdown", "Fees", "Minute coverage", "Status"], comparison)}
<p class="muted">Base and stress vary the assumed price path, spread, slippage and liquidity. Neither reconstructs actual fills. A small sample is not proof of an edge.</p>
<p><a href="{escape(json_name, quote=True)}">Complete JSON audit data</a></p></section>
{"".join(sections)}<section class="panel"><h2>What this test does—and does not establish</h2><ul>{assumptions}</ul>
<details><summary>Source and code identity</summary><pre>{escape(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))}</pre></details></section>
<footer>KAIROS · local historical research · self-contained report · no remote assets or trading controls</footer>
</main></body></html>'''
