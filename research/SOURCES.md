# Source audit and implementation map

Inspected on 2026-09-30. `sources.json` pins retrieved URLs and content hashes. Downloaded papers remain outside the repository. An accessible working paper is not silently treated as the requested journal edition. Empirical findings below are the authors' reports, not independently reproduced Kairos results.

## 1. Probability of Backtest Overfitting

[Requested landing page](https://escholarship.org/uc/item/4w1110bb) returned HTTP 403. Inspected the [author PDF](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf), Bailey, Borwein, López de Prado and Zhu, revised February 27, 2015. Equivalence to the inaccessible repository edition is unverified.

**Method:** §2.1 defines overfitting through the out-of-sample rank of the in-sample winner. §2.2, Algorithm 2.3, partitions a synchronous trials-return matrix into equal blocks, enumerates half-block combinations and their complements, and calculates `omega = OOS_rank/(N+1)` and `lambda = log(omega/(1-omega))`. The distribution estimates how often selection underperforms the OOS median. This is a selection diagnostic, not chronological model training.

**Sample/findings/costs:** §6 uses synthetic 1,000-day random walks and an 8,800-configuration seasonal strategy grid, then a planted seasonal effect. Reported PBO is about 55% without the effect versus 13% with it. These are not executable crypto returns or broker-cost estimates. §5 explicitly warns that omitted costs and unavailable information remain separate problems.

**Inputs/map:** complete aligned net-return histories for all candidate trials, including failures disclosed rather than quietly dropped. Map to the experiment journal and design-only CSCV diagnostic. Faithful rank/combination arithmetic can be tested synthetically; reproducing the reported experiment needs its exact generator/settings. Kairos's small fixed family, serial dependence and any unequal/truncated block policy must be disclosed.

**Falsifier:** the selection procedure repeatedly ranks its chosen strategy below the median on withheld blocks, or apparent gains disappear in the fixed chronological evaluation. Low PBO alone cannot establish an edge or detect leakage.

## 2. Deflated Sharpe Ratio

[Author PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf), Bailey and López de Prado, July 31, 2014 version (first version April 15), forthcoming Journal of Portfolio Management copy.

**Method:** Eq. (1) approximates the expected maximum Sharpe across independent normally distributed trial Sharpe estimates. Eq. (2) applies the probabilistic Sharpe calculation against that selection-adjusted threshold, using sample length, skewness and kurtosis. Appendix 3 addresses dependent trials. Use per-observation Sharpe consistently inside the formula; do not insert annualized Sharpe into daily moments.

**Sample/findings/costs:** methodological derivation, a hypothetical Treasury-seasonality selection example, and Monte Carlo checks in Appendix 2—not an Alpaca or crypto trading study. The paper does not supply broker fee/fill assumptions applicable here. Its result concerns inflated evidence, not a profitable trading rule.

**Inputs/map:** all trial counts and Sharpe dispersion, aligned net returns and their first four moments. Journal both distinct specifications and physical attempts; do not claim failed/no-return trials have known Sharpe. Missing comparable series makes a complete-family DSR unavailable. Raw trial count is a sensitivity assumption, not an estimated effective independent count. DSR is a diagnostic, not the promotion gate.

**Falsifier:** a raw Sharpe advantage loses significance when search multiplicity/non-normality are included, or paired final net returns fail the preregistered criterion. Do not lower the bar by deleting trials.

## 3. Risks and Returns of Cryptocurrency

[NBER w24877 PDF](https://www.nber.org/system/files/working_papers/w24877/w24877.pdf), Liu and Tsyvinski, August 2018 working paper. This is not the later 2021 RFS edition.

**Method:** §4.1, Tables 14–18: time-series predictive regressions and weekly return quintile sorts, including cutoffs determined from the first two years for an out-of-sample exercise. §§4.2–4.5 separately examine attention, valuation proxies, volatility and mining/supply variables.

**Sample/findings/costs:** §2 uses CoinDesk prices: BTC 2011-01-01–2018-05-31, XRP 2013-08-04–2018-05-31, ETH 2015-08-07–2018-05-31. The authors report daily/weekly momentum and attention predictability, with weaker ETH momentum evidence. The reported return sorts/regressions do not establish a net implementable Alpaca strategy after its fees, queues and delays; no applicable broker execution-cost experiment is supplied in §4.1.

**Inputs/map:** price history plus Google searches, Crimson Hexagon Twitter counts, blockchain wallet counts, CRSP/CSMAR and macro/factor data for the wider study. Most non-price inputs and their historical publication vintages are unavailable here. The fixed 168-hour, long-or-cash BTC/ETH baseline is a Kairos adaptation, not the paper's regressions or quintile reproduction.

**Falsifier:** net momentum fails cash/capped-passive benchmarks under the fixed costs and delayed fills, or the effect does not persist across both predefined assets. A later crypto sample cannot be used to retune the original thresholds and still be called a replication.

## 4. Multi-Period Trading via Convex Optimization

[arXiv:1705.00109v1 PDF](https://arxiv.org/pdf/1705.00109v1), Boyd, Busseti, Diamond, Kahn, Koh, Nystrup and Speth, April 29, 2017. The PDF has placeholder publication DOI fields; the pinned arXiv version identifies the inspected text.

**Method:** §2.3 Eq. (2.2) models transaction costs with a linear spread component and nonlinear volume/volatility-dependent impact. §2.5 enforces self-financing; §4.1 Eq. (4.3) trades expected return against risk/transaction/holding costs under constraints. §5 Eq. (5.2) plans multiple periods and executes only the first action. Forecast generation is explicitly outside the method's claimed contribution.

**Sample/findings/costs:** §7: 2012–2016 daily data, December-2016 S&P 500 constituents continuously traded throughout; survivorship bias is expressly admitted. Examples use $100M/$10B portfolios, a 5-bps transaction-cost coefficient, 1-bp holding-cost coefficient and nonlinear impact. §7.3 Eq. (7.1) constructs illustrative forecasts by adding noise to **realized future returns**. Those examples illustrate optimization, not a causal predictive edge.

**Inputs/map:** credible ex-ante return/covariance/liquidity forecasts, positions, fees and constraints. No reliable historical spread/impact or return forecast is present in Kairos's archives. The first cost-suppression arm is a fixed no-trade heuristic, **not** SPO/MPO or a faithful implementation of Eq. (5.2). No solver/dependency is justified for this first comparison.

**Falsifier:** suppressed trading does not improve net benchmark-relative returns after opportunity costs, or only works with hindsight forecasts/zero impact. Any gain obtained with Eq. (7.1)-style future returns invalidates the experiment.

## 5. Volatility-Managed Portfolios

[Requested DOI](https://doi.org/10.1111/jofi.12513) returned HTTP 403. Inspected Moreira and Muir's [NBER w22208](https://www.nber.org/system/files/working_papers/w22208/w22208.pdf), April 2016, revised June 2016. The final journal text and any changed results were inaccessible and are not inferred.

**Method:** §2.2 Eqs. (1)–(2): multiply next month's factor excess return by `c / previous_month_realized_variance`; normalize `c` for comparable unconditional volatility. This is inverse **variance**, not automatically inverse volatility. A full-sample normalization constant would leak evaluation information in a forward research harness.

**Sample/findings/costs:** Table 1: Mkt/SMB/HML/Mom 1926–2015; RMW/CMA 1963–2015; ROE/IA 1967–2015; FX carry 1983–2015. The authors report improved factor Sharpe/alphas. §3.2/Table 4 examine market timing at 1 and 10 bps and volatility-dependent costs, while noting inadequate implementation-cost measures for all underlying factor portfolios. §3.3 discusses leverage constraints. These are not unleveraged retail crypto results.

**Inputs/map:** daily factor returns, monthly realized variances, financing and investable factor replication. Those original datasets/replication costs are not assembled here. Kairos's capped `target_vol / estimated_vol` entry scaling, with no leverage and training-only benchmark matching, is deliberately a **different adaptation**. It does not reproduce the paper's inverse-variance strategy.

**Falsifier:** lower risk is fully explained by lower constant exposure, costs erase gains, or drawdown/risk-matched net-return criteria fail. Reduced volatility by itself is not evidence of predictive skill.

## 6. Bayesian Online Changepoint Detection

[arXiv:0710.3742v1](https://arxiv.org/pdf/0710.3742v1), Adams and MacKay, October 19, 2007.

**Method:** §2 Eqs. (1)–(5) and Algorithm 1 recursively update run-length probabilities from predictive likelihoods and a hazard prior. Within a run, observations are assumed IID under a conjugate model; segment parameters are independent. §2.4 discusses tail truncation. With a constant hazard, the immediate reset probability alone is not a useful naïve varying alarm; inspect the run-length distribution rather than calling a fixed hazard a learned regime signal.

**Sample/findings/costs:** §3 includes 4,050 well-log measurements, DJIA daily returns from 1972-07-03 to 1975-06-30, and coal-mining-disaster intervals from 1851-03-15 to 1962-03-22. The DJIA example uses a zero-mean Gaussian with changing variance, gamma precision prior `a=1,b=1e-4`, and expected gap 250. Results demonstrate segmentation/predictive variance, not net trading profitability. Trading costs are not studied.

**Inputs/map:** ordered observations, observation likelihood, hazard/prior choices and numerical-tail policy. Exact BOCPD is not implemented in the first arm. A fixed short/long volatility-ratio entry filter is the lower-complexity instability hypothesis; it must not be labeled a BOCPD reproduction. Neither detector is allowed to veto protective exits.

**Falsifier:** filtering fails the paired net-return/risk-matched comparison, mostly removes recoveries, or is only effective with retrospective change labels. Detecting a historical break is not sufficient evidence of useful trading timing.

## 7. Enhancing Time Series Momentum Strategies Using Deep Neural Networks

[arXiv:1904.04912v3](https://arxiv.org/pdf/1904.04912v3), Lim, Zohren and Roberts, September 27, 2020 (not v1).

**Method:** §III Eq. (1) combines trading signals with volatility scaling; Eqs. (2)–(3) define a one-year sign baseline; Eqs. (14)–(16) learn positions directly through return/Sharpe objectives. §IV includes lasso, MLP, WaveNet and LSTM. §VI incorporates turnover costs in returns/training.

**Sample/findings/costs:** §V uses 88 ratio-adjusted continuous futures, Pinnacle CLC, 1990–2015; evaluation 1995–2015. Refit every five years; latest 10% of training for validation; 50 random hyperparameter trials. A 15% annual volatility target and 60-day EW volatility are used. The authors report large no-cost Sharpe gains, with performance depending strongly on costs; the abstract's relative outperformance is at about 2–3 bps, and §VI examines larger costs/turnover regularization. This differs materially from 25 bps **each side** for Kairos crypto planning.

**Inputs/map:** licensed continuous-futures history, roll conventions, model/training/search settings and execution costs. Unavailable here. A small fixed logistic entry-quality model is an adaptation testing incremental learned information; it is **not a DMN**, does not optimize Sharpe and does not justify adding a deep-learning framework. Report classification and trading performance separately.

**Falsifier:** learned entry quality fails fixed momentum/risk-matched benchmarks after costs, calibration drifts, or apparent gains depend on unregistered search. Better accuracy without better net returns does not pass.

## 8. On Calibration of Modern Neural Networks

[Guo et al., PMLR 70:1321–1330, ICML 2017](https://proceedings.mlr.press/v70/guo17a.html); main and supplementary PDFs inspected. No financial market sample.

**Method:** §2 Eq. (1) defines calibration; Eq. (3) gives binned ECE and Eq. (6) NLL. §4.2 Eq. (9) temperature-scales fixed logits using a positive scalar fitted on validation NLL. The paper assumes training/validation/test samples share a distribution. It does not establish calibration under trading-regime shift.

**Sample/findings/costs:** §5 evaluates vision (Birds, Cars, ImageNet, CIFAR-10/100, SVHN) and document classification (20 News, Reuters, SST binary/fine-grained), not asset returns. Example CIFAR splits are 45,000/5,000/10,000; Table 1 uses 15 ECE bins. Temperature scaling often improves ECE, but not every dataset. Trading/execution costs do not apply to these experiments.

**Inputs/map:** fixed model logits, precise labels and a disjoint chronological calibration subset. Use the scalar transform faithfully on the study's binary logits, but define the financial label explicitly and test out-of-sample reliability, NLL and Brier score against training prevalence. Calibration of a hypothetical net-bar-return label is not calibration of actual trade P&L. Jev confidence is not a substitute for logits or labeled outcomes.

**Falsifier:** calibrated probabilities fail held-out reliability/NLL/Brier checks or their economic use fails the trading criterion. Recalibrating on the final holdout invalidates the test.

## 9. What survives honest evaluation?

[arXiv:2608.27734v1](https://arxiv.org/pdf/2608.27734v1), Eray Gençay, August 27, 2026: *What survives honest evaluation? Leakage-safe, search-aware assessment of LLM-driven trading strategy discovery*. Verified that this ID matches the supplied title. It is a preprint, not independently validated evidence about Kairos.

**Method:** §3 uses a validated declarative feature/action surface and trial ledger. §4 Eqs. (1)–(2) index PSR/DSR to recorded search, §4.2 uses 16-block CSCV, and §4.3 uses held-out bootstrap inference and paired buy-and-hold tests. The planted leaky oracle in §6.1 survives DSR/PBO: statistics cannot repair an invalid information set.

**Sample/findings/costs:** §5 describes 453 US large-cap names plus SPY (2015–2026 data), top-200 trailing-liquidity selection, design 2017–2021/evaluation 2022–2025; and 39 multi-asset ETFs (2005–2026 data), design 2007–2016/evaluation 2017–2025. Costs: 1-bp commission, 2-bp spread, square-root impact, 50-bp annual short borrow; half/double cost stress. The author reports passive certification and rejection of the discovered active strategies across the tested searches. §8 qualifies the “point-in-time” claim: the stock candidate list is fixed/current, with residual survivorship bias. Do not describe it as a fully survivorship-free panel or general proof that active trading cannot work.

**Inputs/map:** full registered search, contemporaneous universe membership, data hashes, external-event publication times and return matrix. Map to restricted causal features, content-addressed data, journal, sealed final family and paired uncertainty. We do not reproduce the paper's LLM searches, proprietary panels or results. A local append-only API cannot certify that its owner never made external unregistered trials.

**Falsifier:** any decision changes when only future data is modified; failed trials disappear; final results can influence model fitting; or claimed incremental returns fail the preregistered paired comparison. Broader replication could also overturn the paper's sample-specific empirical findings.

## Reproduction status

**Empirical reproduction: none.** Original data/code environments have not been reconstructed. Formula/causality fixture tests are implementation checks, not return replications. The first Kairos study changes markets, period, capital, risk controls, features and execution assumptions; all reported results must be called adaptations. Inaccessible eScholarship and final Wiley editions remain explicitly unverified. No inaccessible appendix or table has been filled in from memory.
