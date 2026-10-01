# Existing-method replay and research comparison

**The existing code paths were exercised, but this dataset does not establish an edge for the trading strategies.** Sparse minute inputs dominate HTF readiness and halt scalping. Scheduled programs execute, but their returns depend on market exposure and the assumed fills. The priority is better input/execution evidence, followed by cost-aware construction, not looser safety checks or a neural model.

This study calls the actual `htf.run`, `scalping.run`, `programs.run` and shared local `Engine` order/risk/ledger code. It does not reuse the seven-day momentum proxy. HTF is an explicit Jev-free permission ablation, not the complete deployed HTF/Jev strategy. The executor is the local `Engine`, not hosted `AlpacaEngine` reconciliation. Only the Alpaca fee reserve is modeled. No broker account, model service or production store was accessed.

## What was tested

The frozen family contains 728 sessions: two assets, 28 fixed quarterly seven-day windows across 2019–2025, six methods and two assumed execution scenarios, plus 56 HTF no-fill controls. This is 196 sampled days per asset, **not continuous seven-year performance**. First-week-of-quarter selection misses unselected events and may have seasonal bias. All sessions start independently with hypothetical $500; no live account is reset and no stopped session resumes.

[Method details](METHODS.md) and the signed [protocol](METHODS_PLAN.json) specify the $100 order/exposure caps, $12.50 loss halt, unchanged protective rules, 25-bps-per-side fee reserve, current Kraken rule scenarios and assumed OHLC paths. Quotes, passive fills and volume-based capacity are hypothetical. Native Kraken prices are not mixed with Alpaca data, and current Kraken minima are not represented as historical Alpaca rules.

| Method | Code exercised | What the result can describe |
|---|---|---|
| HTF pullback | Existing rolling-hour policy, fresh edges, sizing, ownership, residual handling, stops/targets/trims | Conditional performance with eligible-entry permission instead of Jev; not actual model decisions |
| Bollinger scalp | Existing one-minute band re-entry, efficiency/cost gates and protective execution | Input readiness and the behavior before the simulated halt; no empirical trade-performance sample |
| DCA | Existing seven daily $10 slots | Accumulation, partial fills, costs and retained inventory |
| TWAP | Existing 12 slices of an $80-derived parent over one hour | Bounded slicing under a fixed initial limit; not a predictive strategy |
| Rebalance | Existing 16%-asset/84%-cash target, 5pp band, $10 minimum, $100 turnover cap | Single-asset/cash allocation behavior, not the full BTC/ETH basket or optimized weights |
| Passive80 | Existing DCA with one $80 purchase | A bounded purchase reference; unfilled amounts stay in cash |

Market making, triangular arbitrage, leveraged products and equities remain untested: the required synchronous books, model decisions, derivative inputs or session data are absent. The range policy remains observation-only; eligible observations are not converted into invented trades.

## Outcomes

These are **mean seven-day session changes in net-liquidation equity**, relative to $500, under the registered assumptions. They are not annual returns, actual Alpaca returns or proof of alpha. Liquidation values include estimated exit costs without submitting a terminal liquidation order.

| Method | BTC base mean | BTC sensitivity mean | ETH base mean | ETH sensitivity mean | Base filled orders, BTC / ETH |
|---|---:|---:|---:|---:|---:|
| HTF, no Jev | +0.0042% | +0.0156% | +0.0164% | +0.0113% | 8 / 7 |
| Bollinger scalp | 0% | 0% | 0% | 0% | 0 / 0 |
| DCA | +0.1127% | +0.0458% | +0.0704% | +0.0494% | 194 / 168 |
| TWAP | +0.3660% | +0.3786% | +0.4416% | +0.3705% | 194 / 155 |
| Rebalance | +0.4992% | +0.4350% | +0.6046% | +0.5236% | 43 / 66 |
| Passive80 | +0.3228% | +0.2263% | +0.3516% | +0.2371% | 27 / 25 |

Cash and HTF's no-fill controls returned zero. The sensitivity changes path ordering, spread, slippage and capacity jointly; it is not guaranteed to produce a worse return. A higher sensitivity return does not show robustness to costs alone.

DCA's base median session was −0.0545% BTC and −0.0782% ETH despite positive means. The base medians for TWAP were +0.0293% / +0.0348%, rebalancing +0.0645% / +0.1776%, and Passive80 +0.0285% / +0.0001%. The entire distribution and every session remain in the machine report; favorable means are not selected as a certification statistic.

### HTF: too little valid trading evidence

Base HTF completed four BTC and three ETH flat inventory lineages, not seven independent tests of a profitable strategy. BTC's closed-lineage net P&L summed to $0.5845 across independent sessions; ETH's summed to $2.2932. BTC traded only in January 2021 and April 2023; ETH only in January 2021. The small gains are concentrated, conditional on assumed fills and inadequate for an edge claim.

The no-fill controls submitted 62 BTC and 17 ETH passive attempts without fills or profits. Missing queue evidence is economically material. Jev's approval, timing and discretionary exits are unknown; the permission ablation is not an upper or lower bound on its contribution. Production log wording such as "Jev approved" inside retained order reasons refers here to the clearly labeled authorization fixture, not an actual model call.

A seven-day session also cannot observe the full seven-day holding deadline of a fresh entry made after startup. Existing unit tests check that deadline mechanically; this historical sample does not establish its performance. Positions at the boundary are censored and retained, not forcibly closed to manufacture complete trades.

### Minute gaps dominate readiness

| Asset | Expected evaluation minutes | Observed | Missing | HTF rolling checks ready |
|---|---:|---:|---:|---:|
| BTC/USD | 282,240 | 273,513 | 8,727 (3.09%) | 12.62% |
| ETH/USD | 282,240 | 244,419 | 37,821 (13.40%) | 1.23% |

A missing minute breaks the required 1,800-minute contiguous suffix. Relatively few missing bars can therefore remove most 30-hour rolling windows. The archive does not supply the publication/availability evidence needed to distinguish every no-trade interval from an unavailable interval. No missing price or VWAP was invented.

Every scalp session halted before a trade. In each scenario, BTC had 20 zero-depth-model halts and eight incomplete-history halts; ETH had nine and 19. The model gives zero capacity when the adjacent published minute/volume is unavailable. That is **not observed evidence that the historical exchange order book was empty**. The result tests fail-closed behavior under this input/model, not the profitability of a continuously fed scalper.

Before those halts, 175 BTC and 12 ETH base decisions reached the insufficient-cost-room/out-of-range blocker, with additional efficiency-filter blocks. These are repeated dependent checks over truncated runs. They cannot establish that costs alone explain scalping inactivity.

### Programs: activity is not alpha

DCA and TWAP completed their scheduled processing while retaining unspent cash and acquired inventory. A program slot can be claimed yet produce no fill; `skipped_slots=0` is not a 100% fill rate. TWAP's wider sensitivity parent admitted more filled orders despite higher assumed frictions. This demonstrates why fees, limit availability and missed execution must be studied separately.

Rebalancing produced higher mean session changes than Passive80 here, but it had repeated opportunities to reach its target while the reference had only one bounded purchase attempt. DCA also budgets $70 rather than $80. Exposure, entry timing and realized participation were not risk-matched, so the differences do not certify rebalancing alpha.

The daily-loss halt triggered for BTC rebalancing in January 2021 and ETH rebalancing in January 2024 under both scenarios. No further orders were submitted after the halt; holdings remained and continued to be marked. The $12.50 setting is an execution halt, not a guaranteed bound on subsequent inventory losses or whole-session drawdown. Programs that finish also retain inventory; completion does not promise continuing protective execution.

## What the research can improve

The papers do not supply a ready-made profitable replacement. Their useful contributions are test design, cost/risk construction and requirements for credible forecasts. [SOURCES.md](SOURCES.md) pins the inspected versions, equations, source limitations and adaptations.

| Research | Connection to the observed methods | Concrete follow-up and falsifier |
|---|---|---|
| PBO: Bailey et al. | Alternative windows, fills, horizons and filters create selection risk | Register all candidate configurations and failures before comparison. Reject a selected method if it repeatedly loses rank on withheld chronological blocks; do not drop inactive arms. |
| Deflated Sharpe Ratio: Bailey / López de Prado | Hundreds of physical replays are not independent strategy discoveries | Keep physical attempts distinct from configurations and price-path repetitions. DSR requires comparable return histories and a defensible search count; no DSR is inferred from these short, stopped sessions. |
| Crypto risks/returns: Liu / Tsyvinski | Their daily/weekly momentum evidence does not validate hourly pullbacks or one-minute mean reversion | After input readiness is fixed, preregister a small, slower-horizon comparison using current method code and the same costs. Reject it if its apparent benefit disappears against cost- and exposure-matched references. |
| Convex trading: Boyd et al. | Current target-room checks cover hypothetical costs, not expected return or fill probability; rebalancing uses fixed bands | Test cost-aware no-trade bands/position construction only with causal risk/return inputs. Charge both turnover and missed-execution opportunity costs. Reject gains that rely on hindsight forecasts, extra exposure or favorable assumed fills. This study implements no optimizer. |
| Volatility-managed portfolios: Moreira / Muir | Fixed caps and stops are not volatility-managed exposure | Test a capped, prior-data-only sizing variant against an equal-risk constant-exposure reference. Preserve all hard exits and loss halts. The paper uses inverse variance; a target-volatility heuristic must not be presented as its reproduction. |
| BOCPD: Adams / MacKay | Current range-efficiency and trend filters are not a Bayesian regime detector; a feed gap is not a market regime | First distinguish source unavailability from market observations. Then test whether a causal regime filter adds net value beyond existing filters. Reject it if it mainly removes recoveries or uses retrospective break labels. It cannot veto protection. |
| Deep momentum: Lim et al. | Their low-cost futures results do not transfer to 25-bps-per-side retail crypto | Defer a neural model until the deterministic method has informative, costed execution evidence. Any later fit must include turnover costs and the complete search history. Reject gains dependent on near-zero fees or unregistered tuning. |
| Calibration: Guo et al. | There are no historical Jev responses/logits here; fixture confidence one is not a probability | Collect dated model outputs and clearly defined, cost-aware outcomes only under separate authorization. Use disjoint chronological fitting/calibration/evaluation and prevalence baselines. Reject a model that fails reliability/Brier/log-loss or adds no net trading value. |
| Honest evaluation: Gençay | Sparse inputs and assumed fills can dominate results even when all software tests pass | Resolve input and execution evidence before promotion. Keep unsupported methods and no-fill controls visible. A statistical adjustment cannot repair unavailable information or fabricated execution evidence. |

The first practical follow-up is therefore an **input/execution evidence study**, not a parameter search: establish how the intended feed represents confirmed empty minutes versus missing data, obtain dated trade/quote evidence where available, and measure partial fills and cancellations in a separately authorized forward paper environment. Do not blindly forward-fill the archive or relax continuity, fees, minima or loss limits to make trades appear.

Once there is an informative sample, the next strategy experiments should address net expected reward versus stop/deadline risk and cost-aware allocation. A positive historical target room is not a probability that the target will be reached. Only then is it meaningful to compare volatility sizing or model calibration. No improvement is claimed as demonstrated by these replay results, and no paper's empirical results were reproduced.

## Audit and verification

Frozen evaluation source: `27fe52e`. The archive's full SHA-256 and selected-member CRCs/hashes were verified. The dataset retains every selected native row and its gaps. All 2026 prices remain outside analysis; no old final claim was reopened.

[METHODS_RESULTS.json](METHODS_RESULTS.json) contains every session summary, source/code identity, paired descriptive differences, derived lineage/hold diagnostics and all study events. Full orders, settings, marks, retained plans and decision examples remain in the separate Kraken content-addressed store at their referenced hashes. Repeated UI events were summarized by counts/examples and a compact trace digest, not represented as a full event tape.

- Selected dataset: `b1747dd0c1afb7bc23d6c77f28ddaab4a09b36001e50d0dccb0e8145bf1d2d88`
- Report: `d31b2d24e81e0c1bb462f8a4942a471cd72586122cdcfbf2eebe18ffca93da67`
- Current study: **730 completed attempts**: one family, one selection and 728 sessions. Completed diagnostics include safe halts; this is not 728 successful trading strategies.
- Kraken registry overall: 887 starts, 886 completed, the original failed transfer retained, zero unresolved starts and zero final claims.

All 427 Python tests passed, including method dispatch, rolling aggregation equivalence, publication lag, future perturbations, prior-only liquidity, passive touch/expiry/partial cases and inventory retained after program completion. The export also reconciled cash and inventory from every order, checked order caps and verified that no orders followed a halt/completion. Tests establish software behavior under assumptions, not actual Alpaca fills or profitability.

No production files, private runtime settings, balances, history, protective plans, orders, allocations, model calls, releases or deployments were changed. The study does not authorize paper activation.
