# Expanded VWAP and Fibonacci literature review

Reviewed 2026-10-13 after the user confirmed collection was finished. The complete local corpus contains 25 PDFs, 639 pages and 24 distinct papers. The [original review](vwap-fibonacci-literature-review.md) covers the first 11 PDFs and 221 pages; this supplement assesses the 14 added PDFs and 418 pages. Genet v1/v2 are revisions of one paper, not independent confirmations.

The review assesses each added paper's methods, results, execution assumptions and limitations, with relevant tables and appendices. It is not a line-by-line proof audit or reproduction of author data/code. Page references use PDF pages, including covers. Acquisition checks are not scientific validation. The [bibliography](literature-sources.json) records all supplied versions, paths, hashes and review locations without publishing PDFs or extracted full text. Four unavailable full texts are listed separately and are not reviewed evidence.

None of the added papers validates profitable KAIROS long-only, unleveraged BTC/USDT trading on 5m/15m with OKX cash IOC execution and bot-managed protection. The changes to the [improvement plan](vwap-fibonacci-improvement-plan.md) concern experiment design, not deployed rules. The [OpenAI experiment](ai-decision-experiment.md) remains a separate, optional research proposal.

## Findings that change the plan

- Diagnose target room and protective execution first. Statistical reversal, a support bounce, classification accuracy and proximity to market VWAP are different outcomes from executable net profit.
- Keep all eight setups. Test earlier valid confirmation and redundant conditions separately; do not add a universal high-volume or low-volume gate.
- Treat volume relative to its trailing history and time of day as context. Cross-sectional small-coin liquidity premia do not establish a BTC entry rule, and candle volume does not reveal signed flow or available OKX depth.
- Record level age and prior completed interactions using frozen causal levels. Price penetration can continue rather than reverse; a candle sweep does not prove a stop hunt.
- Preserve every attempted variant and its selection history. The repeatedly inspected June development week is not a holdout. Avoid precise Sharpe significance claims from seven daily observations.
- Do not adopt CNN/LSTM architectures on the strength of the Fibonacci/AI papers. Their comparisons suggest feature ablations, but random splits, overlapping windows, unclear labels and idealized fills undermine profitability claims. They do not evaluate Responses, Decisions or Jev.

## Added source inventory

IDs extend the original F/V identifiers. Complete titles, URLs, metadata and SHA-256 hashes are in the bibliography; source links below identify the published record or supplied preprint, not a reproduced result.

| ID | Supplied paper/version | PDF pages | Main use |
| --- | --- | ---: | --- |
| B1 | Bailey et al., probability of backtest overfitting; 2015 manuscript, 2017 publication | 34 | Selection-history controls |
| B2 | Bailey and López de Prado, deflated Sharpe ratio; 2014 manuscript | 22 | Limits on performance inference |
| B3 | Deprez and Frömmel, Bitcoin technical rules; accepted manuscript, May 2024 | 22 | Cost-aware chronological comparison |
| C1 | Bianchi, Babiak and Dickerson, volume/liquidity provision; 2022 manuscript | 42 | Volume-return interaction, transfer limits |
| C2 | Corbet et al., technical trading rules; 2019 manuscript | 13 | Trend-state hypotheses, gross/net distinction |
| C3 | Farag et al., liquidity provision; 2025 version of record | 19 | Risk/liquidity context, size dependence |
| C4 | Hansen, Kim and Kimbrough, periodicity; arXiv v2, November 2021, preceding 2024 publication | 39 | Clock/venue diagnostics |
| C5 | Kitron and Wengrowicz, short-horizon reversal; arXiv v1, August 2026 | 24 | Directional evidence versus economic edge |
| F6 | Khattak et al., Fibonacci/hybrid CNN; 2024 version of record | 25 | Exploratory feature ablation, leakage warning |
| F7 | Khattak et al., multi-granular Fibonacci/AI; 2026 version of record | 25 | Extended exploratory comparison, provenance issues |
| M1 | Chung and Bellotti, support/resistance; arXiv v1, January 2021 | 20 | Frozen zones, age and interaction diagnostics |
| M2 | Osler, currency orders; Staff Report 125, March 2001 | 45 | Order-clustering mechanism |
| M3 | Osler, stop-loss cascades; Staff Report 150, June 2002 | 44 | Continuation and stop-overrun risk |
| V2-v2 | Genet, VWAP execution; arXiv v2, April 2025 | 44 | Objective alignment, not directional alpha |

## B1: Probability of backtest overfitting

[Bailey, Borwein, López de Prado and Zhu](https://doi.org/10.21314/JCF.2016.322) propose combinatorially symmetric cross-validation on a synchronized matrix of candidate-strategy performance. For each partition, select the best in-sample candidate and examine its out-of-sample rank. PBO estimates how often the selected winner performs below the out-of-sample median. The paper also examines performance degradation and loss probability. See PDF pp. 5–15 for definitions/method and pp. 23–28 for applications and limitations.

This addresses selection among trials, not whether an executor is causal, fees are realistic or a strategy is profitable. Being better than other losing candidates is insufficient. Reusing partitions cannot create new market regimes, and subsamples need enough observations to estimate the chosen statistic. A single seven-day sample with sparse cases is unsuitable for a confident KAIROS PBO estimate.

Keep a trial ledger before running further comparisons, including failed and discarded variants, exact inputs, common controls and the reason for selection. Do not generate a large parameter grid merely to calculate PBO or optimize its reported value. Later, sufficient synchronized chronological evidence can support a disclosed selection-risk analysis alongside untouched evaluation.

## B2: Deflated Sharpe ratio

[Bailey and López de Prado](https://doi.org/10.3905/jpm.2014.40.5.094) adjust Sharpe significance for selection across effectively independent trials and for non-normal returns. The calculation depends on track length, trial count/dependence, variation in estimated Sharpe ratios, skewness and kurtosis. The appendices discuss correlated trials and the uncertainty of estimating their effective number. See PDF pp. 4–11, appendices on pp. 12–15 and supporting exhibits on pp. 17–22.

The correction does not repair lookahead, unrealistic execution or missing trials. Sixteen cases on the same BTC week are correlated, not sixteen independent market histories. Twenty neural-network initializations are also not twenty independent out-of-sample periods. Seven daily marks cannot support stable higher-moment estimates or a precise annualized Sharpe/DSR claim.

Report activity, marked returns, closed R, drawdown, costs and uncertainty limitations first. Preserve trial dependence and history for later analysis. If DSR becomes supportable, disclose the return sampling frequency, effective-trial assumptions and sensitivity rather than presenting one precise probability as proof of an edge.

## B3: Cost-aware Bitcoin technical rules

[Deprez and Frömmel](https://biblio.ugent.be/publication/01HY3C3S169G1N6QNYR55NZMFB) study Bitstamp BTC/USD from 2012–2022. They test 75,360 specifications across six rule classes and daily, 60m, 30m and 10m frequencies. Positions use lagged signals. Monthly portfolios select rules using the preceding year, with bootstrap/FDR treatment and out-of-sample evaluation in 2014–2021. Baseline costs combine the lowest historical platform fee and daily half-spread; deeper spread and higher-fee scenarios test sensitivity. See PDF pp. 2–8, results through p. 14, and Appendix B on pp. 18–20.

This is the most directly useful added trading study because it separates selection, chronology and costs. Its stronger result is lower downside risk and better risk-adjusted performance, not consistently significant absolute outperformance of buy-and-hold. High-frequency results are sensitive to small extra costs; excess-return break-even margins are often only a few basis points. OBV and moving-average findings vary across periods.

Do not copy its large search, best parameters, shorts or margin extensions. Its historical minimum fee/touch-spread assumptions are not our OKX account conditions. Use lagged, long-or-cash comparisons, cost sensitivity and downside/activity attribution. More trades are useful only when the structural opportunity survives the shared execution costs.

## C1: Volume and liquidity provision

[Bianchi, Babiak and Dickerson](https://doi.org/10.1016/j.jbankfin.2022.106547) study daily cross-sectional reversal and detrended volume during March 2017–March 2022. Aggregated data span many venues; portfolio membership selects the top 100 liquid pairs using the prior month's Amihud ranking. Returns and volume shocks form long/short portfolios. Separate regressions use a whole-sample liquidity ranking. See PDF pp. 5–14, exchange checks on pp. 14–15, Table 5 on p. 27 and data construction on pp. 35–37.

Low-volume reversal premia concentrate in smaller, less liquid, more volatile coins. Equal weighting produces stronger results than value weighting; cost-adjusted value-weighted returns are not significantly positive. Individual-exchange results weaken, notably on Binance. The supplied manuscript has a cost inconsistency: main text specifies 30/40 bps for long/short sides, while Table 5's caption says 20/30 bps. The exact net-cost implementation needs author-code confirmation. Daily hypothetical execution and aggregate prices do not reconstruct actual orders or funding.

This does not justify either removing BTC volume confirmation or demanding unusually low volume. Detrended volume, raw activity and executable liquidity are different quantities. First stratify existing candidates by lagged relative volume and direction, keeping costs and fills unchanged. No cross-sectional premium or long/short return transfers directly to one BTC spot case.

## C2: Technical-rule conditional returns

[Corbet, Eraslan, Lucey and Sensoy](https://doi.org/10.1016/j.frl.2019.04.027) use Bitfinex one-minute data from 2014–2018 and compare variable/fixed moving-average and trading-range-breakout signals. Missing prices are carried forward. Variable moving averages have the clearest positive conditional-return evidence; fixed moving averages are weaker and some breakout results reverse direction. Table notes apply a Bonferroni correction. See PDF pp. 3–8 and result tables on pp. 10–13.

The study is not an executable after-cost, out-of-sample portfolio evaluation. Commissions, spreads, liquidity and operational risk remain future work. Signal/return alignment must be audited before translating contemporaneous-close conditional statistics into next-open entries. Its large gross-return claims should not be treated as investable returns.

For `fib-ema` and VWAP trend setups, distinguish a persistent bullish state from requiring another crossover to confirm entry. Test that distinction after the timing/cost audit, using completed candles. The paper supplies a hypothesis, not evidence for relaxing all confirmations or introducing more moving-average filters.

## C3: Returns from liquidity provision

[Farag, Luo, Yarovaya and Zieba](https://doi.org/10.1016/j.jbankfin.2025.107411) construct a five-minute cross-sectional reversal proxy using 122 cryptocurrencies over 2017–2022. The strategy buys prior relative losers and shorts winners, scaled as a 50%-margin long/short investment. They examine lagged volatility, crash/tail risk, risk aversion, Tether liquidity, stock-market conditions and Uniswap variables, including recursive forecast comparisons. See PDF pp. 3–7, size subsamples on pp. 10–11 and conclusion on pp. 12–14.

This is a liquidity-premium proxy, not recorded market-maker fills or KAIROS BTC expectancy. Requiring availability since 2017 and limiting missing data creates a selection concern; prices are forward-filled, although authors report an untabulated no-fill robustness check. No exchange-specific after-cost IOC strategy is demonstrated. Large-coin subsamples weaken or reverse several relationships. Repeated daily predictors against five-minute returns also require careful dependence treatment before transferring significance claims.

Use observed trailing volatility and relative activity as diagnostics, not an instruction to trade turmoil or illiquidity. The extra VIX, Uniswap, cross-asset and Tether series are not in our candle archive. Adding them now would expand scope without resolving the measured target-cost problem.

## C4: Periodicity in volatility and liquidity

[Hansen, Kim and Kimbrough](https://arxiv.org/abs/2109.12142v2) analyze BTC/ETH on Binance and Coinbase Pro and WETH/USDC on Uniswap V2. The supplied PDF is November 2021 arXiv v2, not the later 2024 journal text suggested by its filename. Core high-frequency comparisons cover October 2020–May 2021; centralized-exchange extensions cover 2019–2021. A separate daily volatility forecast uses 2017–2019 training and 2020–July 2021 evaluation. See PDF pp. 7–10, 12–24 and 24–27.

Volatility and volume vary by weekday, hour and minute of the hour, with bursts around hour, quarter-hour and some five-minute boundaries. Periodic volatility models improve out-of-sample likelihood. However, patterns differ by venue; relative Amihud illiquidity is not measured bid/ask depth or proof that an hour has greater impact. The authors explicitly qualify that interpretation. Algorithmic trading/funding explanations are plausible mechanisms, not causal identification or directional alpha.

Report UTC hour, weekday and minute-of-hour for arms, confirmations, missing execution minutes and fills. Our next-open entries occur at candle boundaries, so clock-related frictions matter. Inspect whether a global prior-volume mean labels ordinary seasonal activity as a breakout. Do not copy Binance/Coinbase hours into an OKX session gate, change UTC VWAP resets, delay protection or infer fills from current-minute volume.

## C5: Short-horizon mean reversion

[Kitron and Wengrowicz](https://arxiv.org/abs/2608.21888v1) compare 183 Binance spot pairs with 187 US-listed instruments using 15-minute walk-forward directional models and model-free sign statistics. Crypto mean AUC is about 0.531. Jointly removing flat-bar and one-bar boundary channels reduces the class gap to about 0.011; refitting inside bootstrap replications widens uncertainty. A frozen later-period check supports a directional difference but has changing coverage. See PDF pp. 2–7, economic/mechanism discussion on pp. 10–13 and appendices on pp. 20–24.

The gross edge peaks around 1.3 bps, below even the paper's 5-bps maker round-trip benchmark. Its five-minute economic effect is smaller still. This does not validate profitable reversal under our roughly 42-bps base IOC round trip. The universe uses a post-sample volume ranking and survivors; a June 2026 selection overlaps the reported February–August holdout. Delistings and relaxed coverage cuts remain relevant. Cross-asset dependence is substantial, and authors disclose a related trading operation.

Signed-flow conditioning is descriptive, not causal identification. Depth comes from coarse perpetual-book snapshots, not our spot candles; the reported depth-consumption null does not validate a liquidity-sweep strategy. A simple last-bar-sign comparator can later test whether reversion/sweep information exceeds generic reversal, on identical executable inputs. Keep it a diagnostic/placebo, not a ninth deployed strategy or a reason to lower assumed costs.

## F6: Fibonacci and hybrid CNN prediction

[Khattak et al. (2024)](https://doi.org/10.1186/s40537-024-00908-7) use BTC/USDT one-minute data from October 7–December 13, 2022, 18 technical indicators plus a 15-period Fibonacci feature, and several neural architectures. They compare binary through ten-class outputs with/without Fibonacci across repeated initializations. Reported hybrid-model improvements are mixed; absolute binary accuracies in Table 10 are near 50–52%, not the much larger values of the comparison study. See PDF pp. 10–15 and pp. 20–23.

Stratified random sampling spans the full dataset, after normalization is described. Overlapping windows and fit-only preprocessing are not adequately controlled. The December 1–13 investment set lies inside that sampled period and also informs configuration selection, so it is not an independent trading holdout. The presented ROI comparison lacks a sufficiently specified exchange-cost/fill/protection ledger. McNemar p-values concern paired prediction errors, not net-profit significance, and serial dependence and many comparisons matter.

The useful idea is a matched with/without-feature ablation, not a supported CNN advantage or proof of Fibonacci psychology. Our fixed causal swings need generic-level placebos as well. Do not add TensorFlow or let class strength choose exposure; code retains the ≤1% planned-risk ceiling.

## F7: Multi-granular Fibonacci and AI

[Khattak et al. (2026)](https://doi.org/10.1186/s40537-026-01372-1) extend the comparison to BTC/ETH and 1m/5m data, binary/four-class labels and full/half-capital simulated positions without stop-loss controls. The paper reports both ROI gains and large degradations, and acknowledges random-sampling lookahead, omitted fees/spread/slippage/latency and limited October–December 2022 coverage. See PDF pp. 11–15 and pp. 18–24.

Further construction issues need clarification before replication. Table 2 gives BTC one-minute maximum high of $21,480.65 but five-minute maximum high of $48,189.84 despite the stated common period; these cannot be ordinary aggregation of the same one-minute tape. Return labels are explicitly defined from normalized closing prices, which changes percentage magnitudes and is undefined at zero. The written strong-trend inequality does not define symmetric down/up tails correctly. Text also mixes a 15-period window with '15 days' and describes labels inside the model matrix; actual future-label exclusion is not independently established.

The BTC one-minute results overlap the earlier study's sample/results and are not independent confirmation. Large percentages in the feature-comparison table are changes versus non-Fibonacci configurations; later prose/table comparisons do not consistently preserve that distinction. They must not become expected KAIROS returns. No demonstrated multi-timeframe causal fusion or after-cost edge warrants adding a hybrid neural model. Transfer only the demand for explicit labels, feature controls and chronological validation to the optional AI experiment.

## M1: Support/resistance memory and decay

[Chung and Bellotti](https://arxiv.org/abs/2101.07410v1) examine one-minute EUR/USD, Lloyds and Brent series in 2018. Rolling extrema define support/resistance zones; discovery pauses while price is inside a zone, freezing it until bounce or penetration. Prior interactions predict higher conditional bounce rates relative to shuffled-return controls, with asset-dependent evidence of decay. See PDF pp. 3–8, 8–12 and 13–19.

This is an event-probability study, not net-profit validation. Zone width uses average absolute increments of the entire price series, so copying the procedure directly into replay would leak future scale. Bayesian calculations assume independent events; charts initially show 68%, not 95%, intervals. Several high-bounce buckets are small, micro-decay significance is uneven, and overlapping discovery windows need dependence-aware evaluation.

Keep frozen levels and known-at timestamps. Record level age, completed prior interactions and time since last interaction before trying a quality condition. Count only information available before arming. Use trailing scale and comparable seeded/generic-zone controls; never choose whichever historical level bounced best afterward.

## M2: Order clustering and exchange-rate dynamics

[Osler's Staff Report 125](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr125.pdf) examines a large FX dealer's conditional orders in 1999–2000. Take-profit orders cluster at round numbers; stop buys cluster above and stop sells below. Calibrated simulations illustrate how this asymmetry can generate reversals at a level and continuation after penetration. See PDF pp. 11–22, simulation design/results on pp. 22–30 and conclusion on pp. 31–32.

The simulations intentionally exaggerate conditional order-flow dominance to demonstrate a mechanism. They are not realistic BTC return forecasts. One bank's private conditional orders, FX quotation conventions and historical trading institutions differ from public OKX spot data. Round-number clustering does not prove that Fibonacci ratios attract orders, and candle highs/lows do not reveal hidden stop inventories.

Treat rejection and accepted penetration as different causal events for confluence, extension and sweep. Round-number distance can later be a fixed generic-level control, not a replacement strategy. No 'smart money' or stop-hunt explanation should be inferred from a wick alone.

## M3: Stop-loss orders and price cascades

[Osler's Staff Report 150](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr150.pdf) combines documented order-clustering patterns with minute-by-minute Reuters FX quotes from 1996–1998. Movements after crossing round numbers are larger and last longer than reversals, with bootstrap comparisons to arbitrary levels and tests across direction/time of day. Most effects last hours, not days. See PDF pp. 7–12, 16–20 and conclusion on pp. 22–23.

The order sample and quote sample are not contemporaneous event-level stop executions. Crossing classifications use the later price path; they are analysis labels, not immediately available entry facts. The evidence supports a possible positive-feedback mechanism, not identification of any individual sweep or a profitable fade of every crossed low. It does not supply modern BTC slippage or a guaranteed stop price.

For `fib-sweep`, require actual completed reclaim/rejection rather than a touch during continuing decline. More importantly, preserve honest gaps, delay, partial exit and stop-overrun reporting. Cascades argue against widening/removing protection or clipping a loss at its planned budget; they do not authorize slower protective exits.

## V2-v2: Revised VWAP execution paper

[Genet's April 2025 revision](https://arxiv.org/abs/2502.13722v2) retains direct optimization of VWAP allocation loss versus forecasting volume first. Hourly Binance perpetuals cover five assets from 2020–July 2024, with chronological final-20% testing, random internal validation and repeated initializations. A simple temporal linear network and softmax allocations can outperform volume-forecast benchmarks. The BTC Table 2 comparison remains approximately 15.87 versus 11.97 bps of absolute benchmark deviation. See PDF pp. 9–20, conclusion/code availability on pp. 24–25 and appendix tables on pp. 28–29.

Market impact is negligible by assumption and allocated trades use bin VWAP; spread, commissions, actual depth and partial IOC fills are not established by that metric. Random validation and overlapping forecast windows require purging in a transferred experiment. Dynamic variants sometimes degrade results, and the claim that every proposed method beats flat allocation is stronger than the table supports: the BTC fixed volume-loss allocation is slightly worse on absolute deviation.

V2 is additional version provenance, not another independent positive experiment. Keep the original PDF and review. Optimize the outcome that matters to KAIROS: net executable opportunity and protection under shared controls. Do not import end-loaded 6–48-hour scheduling into a next-open entry or urgent stop, and do not treat these learned allocations as evidence for directional VWAP alpha or an OpenAI classifier.

## Evidence mapped to all eight setups

These are controlled research questions, not new rules or promised improvements. Existing F1–F5 and V1–V6 evidence remains in the original review.

| Setup | Relevant evidence | Next interpretation |
| --- | --- | --- |
| `vwap-pullback` | C2/C4, original V5 | Measure trend state, confirmation delay and clock/cost context before adding a filter. |
| `vwap-reclaim` | C1/C4 | Keep completed reclaim; examine relative-volume context without assuming high volume proves direction or liquidity. |
| `vwap-anchored` | M1/M2 | Preserve frozen causal anchor; diagnose age and prior interactions separately from timing. |
| `vwap-reversion` | C3/C5 | Test recovery economics against generic reversal; large-coin gross direction is not enough for IOC profit. |
| `fib-confluence` | M1/M2, original F1/F5 | Separate generic support/VWAP proximity from special ratio information using matched levels. |
| `fib-ema` | B3/C2/F6/F7 | Distinguish bullish state from late extra confirmation; no neural architecture change before cost diagnosis. |
| `fib-extension` | M2/M3, original F4 | Distinguish accepted penetration from rejection; ablate volume/acceptance/retest one at a time. |
| `fib-sweep` | C5/M3 | Require completed recovery; no inferred hidden stops, depth or guaranteed fade. |

B1/B2 apply to every comparison. V2-v2 applies to objective selection and execution diagnostics, not entry direction.

## Revised order of work

1. Reconcile the preserved matched v2 cost-negative plans and losing Target exits. Add timing, target-room, reduction-delay and clock/coverage diagnostics without changing financial results.
2. Test one earlier valid closed-candle confirmation, starting where the economic diagnosis warrants it. Keep structural targets, stop invalidation, sizing and executor fixed.
3. Test redundant confirmations separately. Preserve activity and disclose trade-offs instead of selecting a nearly inactive loss reduction.
4. Use generic-level, unweighted-mean and simple reversal controls to establish indicator contribution. Inspect level age and regime/volume/clock strata before adding gates.
5. Freeze decisions and the full trial ledger before opening another short, coverage-audited chronological week. Only then consider broader history and adverse execution. PBO/DSR require more evidence than the current pilot.
6. Evaluate optional Decisions/Responses independently against deterministic facts, with frozen prospective shadow inputs and no control over risk/protection. These papers do not authorize paid calls, deployment or trading.

## Unavailable full texts

The collection manifest records four papers without local PDFs. Their metadata can guide later acquisition, but no findings from them support this review:

- Wen et al. (2022), [intraday momentum/reversal](https://doi.org/10.1016/j.najef.2022.101733). Collector notes report readable author-uploaded text through a separate research tool, but ordinary downloads failed; no full text was assessed here.
- Tsinaslanidis, Guijarro and Voukelatos (2022), [automatic Fibonacci identification/evaluation](https://doi.org/10.1016/j.eswa.2021.115893). Institutional download attempts timed out.
- Anghel (2022), [trading costs and rule overperformance](https://doi.org/10.1016/j.econlet.2022.110584). Full-text access failed.
- López de Prado, Lipton and Zoonekynd (2026), [Sharpe ratio inference](https://doi.org/10.3905/jpm.2026.1.837), with alternate preprint title *How to Use the Sharpe Ratio*. Full-text access failed.

The bibliography marks these as not reviewed. No access controls were bypassed, no absent source was replaced by its abstract, and no strategy implementation was needed to complete this review.
