# VWAP and Fibonacci strategy improvement plan

Status as of 2026-10-13: the completed collection and controlled v1/v2 comparison are reviewed. Entry-economics diagnostics now reproduce the saved v2 financial results. The first Fib EMA higher-low timing alternative was tested and rejected for advancement; all eight baseline rules remain unchanged. Further timing/economic gates, confirmation ablations and AI experiments have not started. Work remains research-only; no strategy registration, deployment, hosted-demo order, live order or paid inference is authorized by this plan.

Keep all eight independent setups. Improve consistent, economically valid activity before optimizing profitability. Read the [original 11-PDF review](vwap-fibonacci-literature-review.md), [14-PDF supplement](expanded-literature-review.md) and [versioned bibliography](literature-sources.json). Together they cover 25 PDFs, 639 pages and 24 distinct papers; four unavailable full texts are not evidence. [OpenAI Responses and Decisions](ai-decision-experiment.md) remain optional challengers, not prerequisites for the first fixes.

The additions strengthen the cost-first sequence. B3's chronological Bitcoin study chiefly supports downside/risk-adjusted benefits, while C5's short-horizon gross reversal edge is below even its maker-cost benchmark. C1/C3 do not establish a universal BTC volume gate. M1–M3 motivate frozen-level, age and penetration diagnostics, not hidden-order inference. F6/F7 do not justify a neural architecture change because their splits, labels and idealized execution do not establish an independent net-profit result. B1/B2 require preserving selection history before further trials.

## Baseline and constraints

The authoritative baseline is `fair-v1-v2-1000-risk-1pct.{json,html}`, June 1–exclusive June 8, 2026. It contains 32 independent cases: v1/v2 × eight setups × 5m/15m. Both versions use the same executor and risk controls. Different stop distances and fills still produce different dollar positions.

| Metric | Original v1 with common risk controls | Refined v2 |
| --- | ---: | ---: |
| Signals | 426 | 242 |
| Filled entry orders, including partial fills | 218 | 146 |
| Closed trades | 191 | 123 |
| Mean independent-case return | −0.6126% | −0.3783% |
| Pooled mean closed R | −0.6276R | −0.5131R |
| Observed modeled risk-budget breaches | 2 | 0 |
| Halted cases | 2 | 0 |

V2 improves net return in 10/16 matched pairs but remains negative overall. These are independent runs, not a combined portfolio. The earlier $500/$25-order-cap pilot is not a fair profitability control. Preserve the matched report and archived wheels; do not overwrite them.

Unchanged controls for the first experiments:

- OKX U.S. spot target, with explicitly labeled Kraken BTC/USDT OHLC/volume proxies. Historical OKX books, account fees and instrument rules remain unavailable or unverified.
- Long-only, unleveraged, $1,000 per independent case; 5m and 15m signals, one active owned position per case.
- Completed candles and causally confirmed pivots. No backdated anchor knowledge, interpolated gaps, future-volume liquidity or same-candle hindsight fills.
- Daily UTC volume-weighted HLC3 VWAP unless explicitly swing-anchored. Neither mean nor sigma is actual trade-level VWAP dispersion.
- Next-open marketable cash IOC entries; partial remainders cancel. KAIROS does not support attached native SL/TP or `/trade/order-algo`. Stops and targets are bot-managed.
- At most 1% planned stop risk on `min(initial capital, current equity)`, capped at the starting $10 budget, including assumed costs and dust reserves. This is not a realized-loss guarantee.
- Base OLHC path, 2 bps spread, 10 bps slippage, 1% preceding-minute participation, 10 bps taker fees per side. Preserve the $12.50 daily entry-halt latch, $1,000 research order/exposure caps and four-hour holding deadline.
- Protective reductions remain active after the entry-loss halt. Retained dust and open holdings stay owned and marked; no terminal forced liquidation or clipped losses.

The execution-minute coverage is 87.35%. A missing minute can mean no trades or missing data; it is not known venue downtime. Coverage-related expiry and unfilled orders must remain separate from signal rejection.

## 1. Finish entry-economics diagnosis

Recommendation #1, the fair comparison, is done. Recommendation #2's diagnostic phase is also complete; timing and economic-eligibility fixes remain. The [entry-economics audit](entry-economics-audit.md) and [experiment history](experiment-history.json) preserve its evidence and the first rejected timing trial.

Prior v2 analysis found 48/146 filled entries with nonpositive modeled net reward at the intended target, 120/146 below 1 net planned R, and 23/45 actual "Target" exits losing money. Reconcile those counts to the preserved matched v2 cases with one shared cost calculation. Do not confuse theoretical target reward with expected profit or count a trigger label as a successful exit.

Add diagnostics without changing signals, sizing or fills:

1. Record each candidate's arm, confirmation, next-open quote, requested/filled entry and target prices. Measure bars spent confirming and target room consumed between those points. Include rejected, blocked and unfilled candidates, not just survivors. Record UTC hour, weekday and minute-of-hour alongside coverage and prior-volume facts; C4 motivates checking candle-boundary activity/cost effects without adding a session gate.
2. Project fee-inclusive target and stop outcomes using the same tick/lot rules, received-currency fees, slippage and ownership accounting as execution. For an all-sold illustrative unit, net target reward is `(1 − buy_base_fee) × target_exit_bound × (1 − sell_quote_fee) − entry_price`. Actual lot rounding, dust and sold-share cost allocation must use the ledger, not this shorthand.
3. Report net planned reward/stop-risk, cost share of gross target room, and the entry price at which the existing target ceases to cover modeled costs. Audit the current one-ATR confirmation stop widening as well; do not alter stops and entry timing in the same test.
4. Separate first exit trigger, IOC attempts, actual average exit, sold quantity, retained quantity and elapsed liquidation time. Decompose losing target exits into cost-negative plans, adverse price movement, partial/delayed exits and residual allocation.
5. Audit the executor's one-exit-attempt-per-minute rule and latched first reason. A later stop crossing after an initial target trigger must remain visible even if the episode is still labeled "Target". Inspect protective behavior without inventing immediate full fills or changing the liquidity budget.

The assumed base round trip is roughly 42 bps before tick/dust effects: two taker fees, two adverse slippage allowances and the spread. Small structural moves can be untradeable even when the candle pattern is correct.

Acceptance: accounting reconciles, cost attribution explains the flagged entries/exits, and diagnostic-only replay reproduces every saved v2 financial result. This gate passed: all 23 losing Target episodes were already cost-negative at entry, 11 needed multiple exit IOCs, and none crossed the stop later in this week. No accounting/protective defect was established. The final diagnostic replay took 21.83 wall seconds and preserved all deterministic financial fields, native execution deltas and sold-share R. If a defect is later found, fix it separately and replay both signal versions under that corrected executor. Do not compare a corrected challenger with an uncorrected baseline.

## 2. Recover target room without arbitrary targets

Test the earliest completed-candle confirmation that still proves the setup's intended recovery or acceptance. Do not execute on a forming candle or retroactively enter the arming candle. Keep the existing structural target and risk policy fixed for the first timing experiment.

| Setup | First hypothesis to test | What must stay |
| --- | --- | --- |
| `vwap-pullback` | Whether the first clear closed-bar VWAP rejection/recovery confirms before a later higher-low or reaction-high break consumes room. | Prior above-VWAP trend, identifiable pullback, invalidation and next-open entry. |
| `vwap-reclaim` | Whether the first completed reclaim can qualify without a second bullish continuation candle. Test later than the timing audit because this changes the arm/confirm split. | Genuine prior below-VWAP state, completed reclaim and the existing volume condition for this isolated test. |
| `vwap-anchored` | Whether the first valid retest/rejection around the frozen anchor gives better entry location; inspect repeated breakouts from weak anchors. | Confirmed-low knowledge time, frozen pending anchor and bounded retest lifetime. |
| `vwap-reversion` | Whether the first demonstrated recovery inside the lower band retains enough room to the mean before an extra higher-low requirement. | Long-only recovery, rejection evidence, clear stop and mean target; no catch-a-falling-knife touch entry. |
| `fib-confluence` | Whether recovery from the frozen retracement/VWAP zone can confirm before price has nearly reached the swing-high target. | Same confirmed swing, confluence zone and structural target. |
| `fib-ema` | Whether a rejection/higher-low recovery is sufficient without both an EMA20 recapture and reaction-high break. Change only one condition per test. | Bullish trend context, meaningful frozen swing and structural stop. |
| `fib-extension` | Whether confirmation delay, rather than lack of breakouts, explains the activity collapse. First measure the acceptance/retest sequence; ablate it in step 3. | Confirmed swing high, genuine breakout, fixed 1.618 extension and invalidation. |
| `fib-sweep` | Whether the first completed sweep-and-reclaim rejection is enough before another higher-low or micro-break confirmation. | A real sweep below a known swing low, completed reclaim and stop below the sweep. |

These are proposals, not conclusions from the papers. The first `fib-ema` trial allowed higher-low recovery as an alternative to the reaction-high break while retaining EMA20 recapture. In the 32-case comparison, 5m positive-target-reward fills rose 3→5, but net return and mean closed R worsened slightly; 15m net return also worsened. It failed the declared advancement rule and was not adopted. All 14 non-EMA case pairs were identical. Preserve that rejected source/report, then predeclare the next single-factor test using the cost audit. Anchored VWAP and confluence remain untested timing hypotheses. Keep every setup in comparisons and disclose unchanged cases.

A net-reward eligibility rule is worth testing after timing improvements, but a blanket `net R ≥ 1` would reject most current v2 entries. First report that counterfactual activity loss. Test one predeclared economic boundary, not a grid of optimized thresholds. An impossible after-cost target cannot be rescued by increasing size or asking AI to approve it.

If insufficient room persists, wait for a better structurally valid entry or report the opportunity as economically ineligible. Do not push a target farther away merely to make its ratio look attractive, tighten a stop inside invalidation, lower fees, presume maker fills, or require a daily trade quota.

## 3. Test redundant confirmation separately

After choosing a timing change, freeze it and compare separate `fib-extension` challengers that remove only one of volume ≥1.2× prior mean, the 0.1-ATR acceptance distance, or retest necessity. Maintain swing confirmation, stop invalidation, next-open causality and costs. A small follow-up can test shorter confirmation for sweep/reclaim; do not bundle those changes into the extension ablation.

F4 shows that adding crossover confirmation can reduce activity without improving results. It does not prove that removing our confirmation is beneficial. Measure arms, confirmations, expiry reasons, entries/day, active days, net returns and closed R for each change. Expose the unchanged source and execution-control hashes.

Acceptance for a continuation: fewer missed economically valid opportunities, no safety regression, and a disclosed trade-off between activity and net outcomes. A smaller loss caused only by near-zero trading is not success.

## 4. Check structure, regimes and indicator contribution

Use these as later, separate experiments rather than new stacked gates:

- **Fibonacci placebo:** retain the same causal swings and zone widths, then compare the active ratios with fixed non-Fibonacci zones or seeded random placements. Keep the number of levels and touch opportunities comparable. Do not confuse generic swing support with a special Fibonacci effect. F1 and F5 motivate this control.
- **VWAP placebo:** compare the same session/anchor and lifecycle with an unweighted HLC3 mean. This isolates volume weighting; it is a stronger control than substituting an unrelated moving-average horizon. V5 motivates a mean-based control.
- **Trend versus range:** initially stratify outcomes using the existing regime facts, VWAP slope, directional efficiency and session age. Only then test one regime-specific condition. For reversion, a persistent falling trend may call for stronger recovery, not a short. Do not assume ±2σ has Gaussian tail probabilities.
- **Higher-timeframe context:** test one last-completed 60m trend context for Fib EMA, following F3's hypothesis. It needs sufficient native warmup and point-in-time alignment; do not manufacture it from missing minutes or copy the weekly EMA stack.
- **Anchor quality:** inspect impulse size, age, completed prior level interactions, time since the last interaction and repeated low replacement before proposing one causal meaningful-anchor rule. M1 motivates these diagnostics, but its whole-series zone-width scale must not be copied. Use trailing scale and freeze levels; do not retrospectively pick whichever anchor gives the best trade.
- **Volume context:** stratify by lagged relative volume and price direction, with clock context and execution liquidity reported separately. C1/C3's strongest premia are in smaller, less liquid coins; they do not support a universal BTC volume threshold. Do not infer signed flow or depth from OHLC volume. Seasonal normalization, if warranted, gets its own causal ablation later.
- **Generic reversal:** for reversion/sweep, a later simple last-completed-bar-sign comparator can test whether setup information exceeds ordinary short-horizon reversal. C5 motivates the control, not a new production strategy. Keep identical costs, ownership and execution; a directional advantage below costs is not a successful trade.
- **Time of day:** report UTC-hour, weekday and minute-of-hour activity, coverage and costs before any session filter. C4 documents venue-specific periodicity, not directional alpha or an OKX trading schedule. BTC trades continuously; QQQ's opening/closing-hour evidence does not establish a crypto schedule.

A trend-following trailing exit can be explored later under the unchanged initial risk ceiling and holding deadline. It needs its own paired test; V5 does not justify extending targets or removing protection now.

## 5. Optional AI comparison

Use the [AI experiment plan](ai-decision-experiment.md), beginning with shadow-only Decisions regime/quality classification at candidate arming. Compare it with deterministic facts and, if useful, the existing Jev approach. Test Responses separately for richer structured interpretation. Avoid an expensive multi-agent stack or a combined Responses-plus-Decisions pipeline until a simpler challenger shows incremental value.

AI cannot make a cost-negative fixed target profitable by classification. Model probability, choice confidence and rubric score are not measured win probabilities. No model controls sizing, credentials, order types, stops, deadlines, daily-loss latches or ambiguous-write recovery.

## Verification and advancement

Run short batches over SSH on `apollo.kyaulabs`, isolated under `/srv/kairos-research/kyau-vwap-fib`. Keep deployed trading stores and selections untouched. Start on the existing development week with base execution, one changed factor and a saved 16-case v2 control. Use a single 32-case baseline/challenger comparison where practical; do not launch a large parameter sweep. Add tests for causal timing, economic rounding/fees and lifecycle behavior actually changed.

Before each batch, record its hypothesis, source/configuration hashes, data windows, common execution controls and acceptance/selection rule. Preserve all attempted variants, failed runs and rejection reasons in versioned research history, not only the winner. Record prompt/model trials separately if AI is tested. B1/B2 motivate this ledger; they do not justify a parameter sweep or a precise selection-risk statistic on one short week. Setup/interval cases and random seeds share market observations and are not independent trials or portfolio returns.

Every HTML/JSON report must include:

- Signal arms/confirmations, signals and filled entries/day, attempts, fill rate, active/zero-trade days and idle gaps, broken down by setup and interval.
- Net marked equity including open holdings, closed sold-share R, expectancy, fees, spread/slippage attribution, exposure and drawdown. Closed R uses net sold-share P&L divided by that sold share's actual-fill planned stop risk.
- Target-trigger versus realized-exit evidence, partial reductions, retained dust, data coverage, native minimums and economic blockers.
- Requested/filled planned risk, realized loss, overruns, entry halts, execution assumptions, source/model hashes and measured wall/case runtime.

Freeze the next selected rules and acceptance criteria before opening another short week. Use June 8–exclusive June 15, 2026 only after verifying it has not already informed tuning and checking its coverage. Give it a fresh independent start and sufficient prior warmup. Poor coverage must be reported, not silently replaced with a more favorable week. If inspected and used for tuning, that week becomes development data too.

Only changes that transfer with meaningful activity, reconciled costs and intact protection merit broader chronological and adverse-execution checks. A week with two trades is inconclusive. Later, use synchronized day/week blocks for uncertainty and account for all attempted variants; overlapping trades and random model seeds are not independent evidence. B3 motivates chronological, long-or-cash and cost-stress comparisons rather than gross conditional returns. Calculate PBO/DSR only when track length and candidate histories support their assumptions, with trial-dependence and non-normality sensitivity disclosed. Do not annualize seven daily marks into a confident Sharpe significance claim. Establish positive after-cost expectancy and plausible venue execution before describing a strategy as profitable. No untouched holdout or profitable result is claimed now.

## Preserved artifacts

Local reports are under `/home/kyau/.local/share/kairos-research/vwap-fibonacci/`; Apollo reports are under `/srv/kairos-research/kyau-vwap-fib/reports/`.

- `pilot-v1.{json,html}`: original smaller-capital exploratory run.
- `pilot-v2-risk-1pct.{json,html}`: refined signal/risk pilot.
- `fair-v1-v2-1000-risk-1pct.{json,html}`: matched comparison and current baseline.
- `entry-economics-v2-final.{json,html}` and its verification receipt: diagnostic-only replay with cost/timing attribution; the initial `entry-economics-v2` replay remains preserved.
- `ema-higher-low-development.{json,html}`: first timing alternative, rejected for advancement; declarations, source hashes and selection are in the experiment history.
- Apollo `versions/`: preserved original, refined and comparison wheels; comparison runner is archived with its version.

The selected archive is `kairos-vwap-fib-pilot.zip`, SHA-256 `5332ee5fe401c99fd4fd5321e6a4e592e82bb7ad28fc8d83548bdb3d1a55bdb5`, with a provenance receipt. It contains the short pilot and warmup, not the full historical archive. Reports remain owner-private. The original HTF study, stopped at the user's request, stays separate and is not restarted by this plan.
