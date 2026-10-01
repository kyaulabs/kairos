# BTC/ETH momentum study 001

**Conclusion: inconclusive. No strategy improvement is supported, and nothing is promoted to production.**

The fixed additions did not meet the preregistered 30-round-trip minimum. Validation produced at most seven round trips per active arm; the final window produced none. Cost and quality gating produced no entries. Sparse assumed fills and retained subminimum holdings dominate the remaining results. These are not estimates of a profitable Alpaca strategy.

The study used free public Alpaca US hourly BTC/USD and ETH/USD bars, with a separate hypothetical $500 for each asset. It made no authenticated requests, broker writes, Jev calls, fund allocations or production changes. The executable research code is separate from the live engine.

## Protocol and audit

[PLAN.json](PLAN.json) fixes the dates, parameters, costs and acceptance criteria. [SOURCES.md](SOURCES.md) contains the nine-paper implementation map and source-version caveats; [README.md](README.md) contains the repository gap analysis and commands.

- Fit: January–September 2024. Calibration/risk-reference fitting: October–December 2024.
- Validation: calendar 2025. Final: January–August 2026, evaluated once after sealing.
- Historical bar availability is **assumed** at close plus 60 seconds. Retrieval timestamps do not establish original publication times or historical revisions.
- Development coverage: 18,285 / 18,288 hours per asset. Final coverage: 5,831 / 5,832. No missing hour was synthesized.
- Base and stress scenarios retain 25-bps-per-side fee reserves. Spread, slippage, latency, rejection, partial fills and participation differ as declared in the plan. They are assumptions, not measured broker execution.
- The $100 order/exposure caps, $80 entry target, $12.50 permanent daily-loss halt, 300-bps stop and seven-day exit deadline apply to scenario arms. Rejected/untradeable exits cannot guarantee liquidation at a deadline. The full-investment price reference is deliberately exempt and not deployable.

The journal contains **189 physical attempts: 188 completed and one failed**, comprising two collections, two development families, four model fits, 180 simulations and one final family. One 24-page collection failed closed; the bounded 221-page collection succeeded. There are no unresolved starts and exactly one final opening. These counts include nesting, replications and benchmarks, not 189 independent candidate strategies.

The first validation report exposed a benchmark-definition error: the unconstrained buy-and-hold reference retained only its first partial fill. Commit `0eb0a7e` corrected that reference to full opening-price exposure with costs. Both validation reports remain preserved. Every model, risk scale, strategy, primary comparison and non-buy-hold benchmark was identical in the repeated validation. No threshold, window, strategy or acceptance criterion changed.

The final study used `0eb0a7e`. Post-run review found a simulator edge case where a pending exposure trim could mask a later hard exit. Commit `c3877a5` fixes priority and preserves the original submission deadline. All 180 stored simulation outputs were checked; none submitted an exposure-trim order. The final window was **not reopened or rerun**. Under the preregistered bug policy, the final run is **invalidated as confirmatory evidence**. Its figures remain descriptive records of the original frozen code, not validation of the corrected implementation. Genuinely new data is required for another confirmatory evaluation.

## Returns

Returns include paid fees and estimated liquidation costs on remaining holdings. The JSON `fees` fields contain paid fees, not hypothetical terminal liquidation fees. Round trips require a flat close; a partial sale leaving dust is not relabeled a completed round trip. Percentages are returns on each hypothetical $500 account, not on deployed exposure.

| Asset | Arm | Validation base % | Validation stress % | Final base % | Final stress % | Final base round trips |
|---|---|---:|---:|---:|---:|---:|
| BTC/USD | `momentum` | -0.0704 | -0.0728 | -0.1535 | 0.1013 | 0 |
| BTC/USD | `volatility` | -0.0704 | -0.0728 | -0.1535 | 0.1013 | 0 |
| BTC/USD | `cost` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 |
| BTC/USD | `instability` | -0.0704 | -0.0728 | -0.1535 | 0.1013 | 0 |
| BTC/USD | `quality` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 |
| BTC/USD | `cash` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 |
| BTC/USD | `capped_passive` | -0.3885 | -0.0351 | -0.1535 | 0.1901 | 0 |
| BTC/USD | `buy_hold_reference` | -7.0575 | -7.6135 | -10.9384 | -11.4711 | 0 |
| ETH/USD | `momentum` | -1.8971 | 0.2493 | -0.3360 | -0.1098 | 0 |
| ETH/USD | `volatility` | -1.6368 | 0.2493 | -0.3360 | -0.1098 | 0 |
| ETH/USD | `cost` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 |
| ETH/USD | `instability` | -1.8971 | 0.2493 | -0.3360 | -0.1098 | 0 |
| ETH/USD | `quality` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 |
| ETH/USD | `cash` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 |
| ETH/USD | `capped_passive` | -0.0387 | -0.5739 | -0.0094 | -0.1844 | 0 |
| ETH/USD | `buy_hold_reference` | -11.5605 | -12.0895 | -17.4830 | -17.9766 | 0 |

The full-investment reference measures price exposure, not attainable execution under Kairos limits. Comparing tiny residual holdings with full investment is not evidence of risk-adjusted outperformance. Primary tests instead compare each addition with momentum and its preregistered risk-matched passive reference.

Higher combined execution stress sometimes improved a realized scenario return because it changed fills, timing and exposure. This is not evidence that higher costs help. No favorable stress result was selected as a replacement for the base result.

## Execution and uncertainty

The final base momentum/volatility/instability paths were identical within each asset. Their mean exposures were only $5.71 for BTC and $5.08 for ETH. BTC momentum had one entry order; ETH momentum had one. Retained-dust states occupied 5,828 and 5,621 decision events respectively. The synthetic $10 minimum prevented closing these residuals. This simple cash/long harness does not reproduce production's eligible retained-plan top-ups; it must not be used to infer production HTF capacity or turnover.

The cost rule requires weekly momentum divided by seven to exceed scenario round-trip costs plus 10 bps. Combined with entry only on a fresh positive weekly-momentum transition, it admitted no entries. Quality's 0.55 threshold likewise admitted no fresh entries. These inactive ablations cannot establish either an improved trading policy or a general failure of cost/quality modeling. No threshold was relaxed after observing this outcome.

All final periods contain 243 daily return observations, but no active arm satisfies the 30-round-trip gate. Seven-day paired moving-block intervals use 4,096 resamples and Bonferroni alpha 0.05/16 for the 16 base comparisons. The following intervals are **descriptive only**; they do not manufacture independent trades from daily marks of a residual holding.

| Asset | Addition | Increment vs momentum, bps/day interval | Increment vs risk-matched passive, bps/day interval |
|---|---|---:|---:|
| BTC/USD | `volatility` | [0.0000, 0.0000] | [0.0000, 0.0000] |
| BTC/USD | `cost` | [-0.4413, 0.5974] | [0.0000, 0.0000] |
| BTC/USD | `instability` | [0.0000, 0.0000] | [0.0000, 0.0000] |
| BTC/USD | `quality` | [-0.4413, 0.5974] | [0.0000, 0.0000] |
| ETH/USD | `volatility` | [0.0000, 0.0000] | [-0.8687, 0.4921] |
| ETH/USD | `cost` | [-0.5132, 0.8848] | [0.0000, 0.0000] |
| ETH/USD | `instability` | [0.0000, 0.0000] | [-0.8687, 0.4921] |
| ETH/USD | `quality` | [-0.5132, 0.8848] | [0.0000, 0.0000] |

Prevalidation risk scales were 1 for momentum/volatility/instability and 0 for cost/quality in both assets. Zero means the inactive calibration arm yielded a cash reference, not successful risk matching. The JSON export reports realized volatility, Sharpe, drawdowns, turnover, fees, spread/slippage estimates, rejections, partial fills, exposure and remaining holdings for every arm/scenario/reference.

The complete five-candidate family contains constant return columns. DSR and CSCV/PBO are therefore unavailable, not zero and not recalculated after dropping inconvenient candidates. No selection diagnostics were run on final returns.

## Forecast quality

Each model used 6,352 matured training labels and 2,158 calibration labels. Final evaluation has 5,635 matured labels per asset. Labels are hypothetical delayed 24-hour net price outcomes, not the probability of profit from a realized passive trade. Overlapping labels are dependent; their count is not a count of independent trades.

| Asset | Final model Brier | Prevalence Brier | Final model log loss | Prevalence log loss | Final model ECE |
|---|---:|---:|---:|---:|---:|
| BTC/USD | 0.219253 | 0.217988 | 0.630618 | 0.628005 | 0.074697 |
| ETH/USD | 0.228972 | 0.226785 | 0.650710 | 0.646154 | 0.078178 |

Temperature scaling improved calibration-partition log loss, but both models scored worse than their training-prevalence forecast on validation and final Brier/log loss. That observation does not establish statistical significance. It provides no reason to call these scores calibrated trade-profit probabilities or to substitute them for Jev confidence.

## Reproduction and adaptation status

None of the nine papers' empirical tables was reproduced. The implementations below are scoped adaptations or diagnostic fixtures, not transfers of published returns.

| Source | Implemented here | Empirical status |
|---|---|---|
| Probability of Backtest Overfitting | Design-only eight-block CSCV; stable/reversed-rank fixtures | Comparable family is degenerate; no empirical PBO estimate |
| Deflated Sharpe Ratio | Skew/kurtosis-adjusted raw-trial sensitivity with disclosed assumptions | Constant candidates prevent a complete-family estimate |
| Cryptocurrency risks and returns | Separate fixed BTC/ETH time-series momentum baseline | Different venue/period/constraints; insufficient executable sample |
| Multi-period convex optimization | Explicit costs and a fixed cost-suppression ablation | No convex optimizer or validated forecast/impact model; gate inactive |
| Volatility-managed portfolios | Capped 60% annualized-volatility entry sizing | Not the paper's inverse-variance portfolio; no supported incremental result |
| Bayesian online changepoint detection | A 24h/168h volatility-ratio ablation | Not BOCPD or a changepoint posterior; no separate final effect |
| Deep neural momentum | Fixed small logistic quality ablation | No DMN/futures replication or Sharpe-trained network; gate inactive |
| Neural-network calibration | Separate temperature fit, Brier/log-loss/ECE/reliability bins | No held-out advantage over prevalence; not a profit-probability claim |
| Honest financial-AI evaluation | Versioned artifacts, availability assumptions, purged chronological splits, trial journal and sealed final family | Historical vintages/arrival/queue evidence still unavailable |

## Decision

All four incremental hypotheses are **inconclusive** under the frozen criteria. None qualifies as supported; the inadequate executable sample also prevents declaring a general negative result about the papers' methods. The repository now has an auditable offline test path, not an empirically validated trading improvement.

Do not promote these arms, loosen gates, increase capital or tune on this consumed final window. A future study would need separately preregistered data and verified availability/execution inputs, including actual market minima, venue depth and fill/cancellation behavior. No such acquisition, broker test or activation is authorized by this report.

## Artifacts and verification

[RESULTS.json](RESULTS.json) exports the dataset manifest, both validation reports, final report, fitted models, all journal rows and every trial specification/failure. Raw market pages and detailed order/decision/return objects remain in the preserved research directory, keyed by SHA-256; they are not republished as a source dataset. Exact replay requires those retained objects, not a potentially revised fresh download.

- `final`: `e57dc8cfdf510b3568f958a496d26f1b2acf1a075c77fda4845245ee26dd1ce6`
- `superseded_validation`: `8eb6e5b06bbf373b55b883c020fa4664c0eb8a1f08cf54b7174a712027f8047e`
- `validation`: `820159b52681081005b5c87dc1f097e9b92ac429a85df3779932bba2a689f143`
- Dataset: `2db8f22880d4381cd0381307c1be9c2f3f4dfb6294b50e000f6559e592cd3514`
- Frozen evaluation code: `7247071e7b1d4b90603ec904175915eb668683e28b576249aedf9d61095fad05`

The current implementation passes 411 Python tests (27 research tests) and the existing 73 frontend tests in CI. Regressions cover causal features, delayed information, purged labels, frozen fits, event ordering, exact accounting, partial/rejected fills, hard-exit priority, permanent halts and one-time final consumption. Statistical fixtures cover paired uncertainty and known stable/reversed CSCV rankings. These checks establish software behavior, not profitability.
