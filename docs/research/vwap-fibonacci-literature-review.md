# VWAP and Fibonacci paper review

Reviewed 2026-10-13: every PDF in the local `research/` folder, 11 papers and 221 pages. The review covers the extracted full text, methods, results, tables, limitations and appendix captions. It does not reproduce the authors' experiments or certify their data. Page references below use PDF page numbers.

The useful evidence is narrower than the titles suggest. Fibonacci papers give mixed evidence for support/resistance effects, often without an executable after-cost test. Five of the six VWAP papers concern executing an already chosen order against a benchmark, not predicting a profitable trade. None establishes profitability for KAIROS's long-only BTC/USDT, 5m/15m, OKX-target strategies.

Use this review with the [improvement plan](vwap-fibonacci-improvement-plan.md) and the separate [OpenAI experiment](ai-decision-experiment.md). Keep all eight setups. A weak result calls for a better controlled hypothesis, not automatic removal.

## Source inventory

These paths are relative to the repository root. The PDFs remain ignored and are not required to render this documentation. IDs identify the supplied versions, not later revisions from the same authors.

| ID | Paper | Local PDF | Pages |
| --- | --- | --- | ---: |
| F1 | Nikhil Gupta (2011), *Fibonacci Retracements and Self-Fulfilling Prophecy* | `research/fibonacci/Fibonacci Retracements and Self-Fulfilling Prophecy.pdf` | 25 |
| F2 | Arkaprava Chakrabarty and Ayan Majumdar (2023), *The Impact of Fibonacci Retracement to Forecast the Support and Resistance of Select Indian Stocks and Indices* | `research/fibonacci/TheImpactofFibonacciRetracement.pdf` | 6 |
| F3 | Lusindah and Erman Sumirat (2021), *Implementation of Fibonacci Retracements and Exponential Moving Average (EMA) Trading Strategy in Indonesia Stock Exchange* | `research/fibonacci/editor_in_chief,+EJBMR_1033+R300821.pdf` | 7 |
| F4 | Ikhlaas Gurrib, Mohammad Nourani and Rajesh Kumar Bhaskaran (2022), *Energy crypto currencies and leading U.S. energy stock prices: are Fibonacci retracements profitable?* | `research/fibonacci/s40854-021-00311-8.pdf` | 27 |
| F5 | Savva Shanaev and Ryan Gibson, *Can Returns Breed Like Rabbits? Econometric Tests for Fibonacci Retracements*, supplied SSRN manuscript | `research/fibonacci/ssrn-4212430.pdf` | 27 |
| V1 | Xingyu Zhou, Wenbin Chen and Mingyu Xu (2024), *Two Kinds of Learning Algorithms for Continuous-Time VWAP Targeting Execution* | `research/vwap/2411.06645v1.pdf` | 28 |
| V2 | Rémi Genet (2025), *Deep Learning for VWAP Execution in Crypto Markets: Beyond the Volume Curve* | `research/vwap/2502.13722v1.pdf` | 45 |
| V3 | Ruiyang Chen (2024), *A Review of VWAP Trading Algorithms: Development, Improvements and Limitations* | `research/vwap/A_Review_of_VWAP_Trading_Algorithms_Development_Im.pdf` | 7 |
| V4 | Takashi Kato, *VWAP Execution as an Optimal Strategy*, supplied long preprint | `research/vwap/VWAP_JSIAML_long_preprint.pdf` | 13 |
| V5 | Carlo Zarattini and Andrew Aziz (2023), *Volume Weighted Average Price (VWAP): The Holy Grail for Day Trading Systems* | `research/vwap/ssrn-4631351.pdf` | 26 |
| V6 | Sham M. Kakade, Michael Kearns, Yishay Mansour and Luis E. Ortiz (2004), *Competitive Algorithms for VWAP and Limit Order Trading* | `research/vwap/vwap.pdf` | 10 |

SHA-256 of each supplied PDF:

```text
F1 23178b26540a5b259528f786b34b4b863612ef68f2c343e3f003f09a333b7ee2
F2 5ecfdcc742792dd022b9683a450f088d694d3372d7fdd4d8cde6c2bfeb072360
F3 2c6fae32e2eeb166e591e32e090b94217c2bd602d936ba62ef34f9bd0576aeeb
F4 4e822b3ac7c50ae723a3a16ba7b252a4c78083cb6aaf181d66706a0ab4d350cd
F5 8f9e711205bec046bddcb3a8195f96c384feee5ca963ef82349526923f38787c
V1 a5af6f88b01d0c1c210b36eb2cf8a557c4b882a4b5c4a6a11d890883853be9c0
V2 24579fcc3820c776f84dc829936b31437e7e4de362c43333d05256d807b1e480
V3 d7bd90d039d5283134122c81cf3596e0d63595381cec18f1402937ddc8d6816a
V4 4852aca1fa2d32518620fe7fb5fd89fcafc86672d86cfe31e9626f3f18a5b7cb
V5 47137abd00730dd6970d5d6409b3908342d63d75ecfc9cf2badc7d206f6905c8
V6 2e15dafca208257c4c412438b6c7dc58e33755f22a1918a2bd8fd0597a2175e5
```

## Fibonacci evidence

### F1: Gupta

This honors thesis studies five FX pairs during 2003–2008 at 1m, 5m and 15m frequencies. It compares five familiar retracement ratios with 100 sets of randomly drawn levels. Some ratios, especially 23.6% and 38.2% at the faster frequencies, have statistically better bounce rates. The differences are generally less than one percentage point. The conclusion does not establish a growing self-fulfilling effect or a compelling economic advantage.

The swing/trend selection is retrospective. Missing minutes are carried forward, and bounce counts are not a net-return strategy with fees, spread, partial fills and position sizing. A bounce percentage is not a trading win rate.

For KAIROS, adopt the placebo comparison, not the retrospective construction. Use the same causally confirmed swing, zone width, lifecycle, execution and costs for Fibonacci and non-Fibonacci levels. Evaluate the economic size of the difference. This is useful methodological evidence, but weak support for executable alpha. See the methodology, results and conclusion in the middle and final sections of the PDF; [source record](https://digitalcommons.macalester.edu/economics_honors_projects/41/).

### F2: Chakrabarty and Majumdar

The paper studies five high-beta Indian stocks and NIFTY over roughly ten years of daily observations. It describes retracement support/resistance and reports regressions, not a fully specified out-of-sample trading system. Most p-values in Tables III–IV are well above conventional significance thresholds, despite stronger claims in the prose. The constituent selection uses a date after the studied price period, creating a selection-bias concern.

Do not translate counted support levels into profitable signals or copy the ratios as validated BTC parameters. The paper is a low-confidence illustration of level construction. Its useful question is whether a level's proximity adds information beyond ordinary price structure. See PDF pp. 3–6, especially the result tables and conclusion, printed journal pp. 166–169.

### F3: Lusindah and Sumirat

The authors combine weekly EMA screening with daily Fibonacci pullbacks, trendlines, volume and stochastic context for Indonesian equities during 2018–2020. They report 73 trades, 63 winners and an approximately 164–165% cumulative return. The study supports the hypothesis that pullbacks behave differently in bullish versus non-bullish markets.

The implementation is substantially manual, stops depend on the author's risk profile, constituent selection is not clearly point-in-time, and costs and independent validation are unclear. Reported return figures and trade-table arithmetic are not consistently explained. These limitations prevent treating the win rate as a reproducible strategy result.

Test a single completed higher-timeframe trend context for `fib-ema`, after entry economics are fixed. Do not import four specific EMAs, stochastic, volume and trendline requirements together. That would make attribution impossible and could suppress activity again. See PDF pp. 4–7, printed pp. 405–408; [DOI](https://doi.org/10.24018/ejbmr.2021.6.4.1033).

### F4: Gurrib, Nourani and Bhaskaran

Ten energy stocks and four energy cryptocurrencies are studied on daily prices from November 2017 to January 2020. Levels based on the completed prior trend fit stocks better than the energy tokens. The important ablation is that adding a price-crossover rule produces fewer or no trades without superior performance.

The reported returns assume unrestricted long/short one-unit transactions and terminal closing. Risk is proxied with price standard deviations rather than a conventional portfolio return series. Tables 4–5 include extreme Sharpe values when activity and estimated risk are near zero; the authors acknowledge that those measures are unreliable. Energy tokens are not BTC, and shorting a collapsing market does not transfer to long-only spot. Transparent transaction-cost and executable-liquidity evidence is absent.

Use this as a warning against extra confirmation by default. For `fib-extension`, separately remove the volume, acceptance-distance or retest requirement rather than relaxing everything together. Preserve causal swing confirmation and frozen levels. See PDF pp. 15–24 and Tables 3–5; [DOI](https://doi.org/10.1186/s40854-021-00311-8).

### F5: Shanaev and Gibson

This is the strongest econometric Fibonacci study in the supplied set. It uses lagged price relative to expanding all-time extrema, tests international indices and FX during 2012–2021, and examines daily S&P 500 long/short portfolios. It includes calendar/reversal controls, factor models, multiple-testing treatment, alternative levels and GARCH specifications. The simpler level sets containing endpoints, 38.2%, 50% and 61.8% perform better than larger sets; proximity to support is associated with higher subsequent returns. Some text prints 61.2% inconsistently, so the exact implemented ratio requires source-code confirmation.

That is evidence for a particular daily proximity effect, not validation of confirmed local swings or a 1.618 extension. The portfolio is cross-sectional and long/short. Transaction costs, borrowing, turnover and an untouched trading holdout are not established by the presented factor alpha. Choosing the best set within the studied sample still needs external validation.

For KAIROS, test whether entry location within a frozen swing explains net outcomes, and compare to identical non-Fibonacci zones. Keep the level count small. Do not substitute all-time extrema for local swing plans without a separate experiment. See PDF pp. 7–18, Tables 2–7 and appendices; [SSRN record](https://ssrn.com/abstract=4212430).

## VWAP evidence

### V1: Zhou, Chen and Xu

The authors derive adaptive dynamic programming and continuous-time actor–critic methods for liquidating an existing position while tracking a fraction of market trading speed. Their synthetic market has Brownian prices, linear permanent/temporary impact and mean-reverting volume with Poisson jumps. Learning is tested in two simulated environments across random seeds.

The results are conditional on that simulator. TWAP beats the learned methods in the low-impact/high-terminal-penalty environment; some learned methods beat it in the high-impact environment. Terminal inventory is cleared under a modeled penalty, and exploratory Gaussian actions do not by themselves enforce KAIROS's no-short, owned-inventory and bounded-IOC constraints.

This is not evidence that an LLM can choose profitable entries. For our small account, audit observed execution frictions before considering an execution learner. Never replace urgent protective exits with exploratory scheduling. See PDF pp. 4–7 and 17–25, especially Tables 3–4; [arXiv version](https://arxiv.org/abs/2411.06645v1).

### V2: Genet

This paper directly optimizes allocation weights for VWAP execution instead of predicting the volume curve first. It uses hourly Binance perpetual data, five crypto assets, a chronological final-20% test split, and repeated training runs. A simple temporal linear network with softmax allocations often beats flat allocation and volume-prediction benchmarks. In Table 2, BTC absolute benchmark deviation falls from about 15.87 bps to 11.97 bps for one configuration. Better volume-prediction R² does not imply better execution.

The test targets proximity to the period VWAP, not directional profit. Allocated trades are priced at each bin's VWAP, with negligible market impact assumed; that does not establish available IOC fills, spread, fees or adverse selection. The internal validation split is random within the training period. Overlapping lookback/forecast windows require purging in a transferred experiment; repeated random seeds are not independent market samples. Hourly perpetual scheduling over 6–48 hours is far from a small 5m/15m spot entry and four-hour protection deadline.

Adopt the lesson about objectives: optimize net executable outcomes, not indicator accuracy or volume fit. Do not copy end-loaded execution for stops. Dynamic versions sometimes perform worse, so extra adaptation needs its own control. See PDF pp. 9–20, Tables 1–4 and appendix plots; [arXiv version](https://arxiv.org/abs/2502.13722v1), [author code](https://github.com/remigenet/DeepLearningVWAP).

### V3: Chen

This seven-page review covers VWAP benchmarks, volume forecasts and dynamic scheduling. It usefully separates static volume curves from adaptive execution and discusses forecasting and operational limitations.

It presents no new controlled BTC strategy experiment. Several quantitative claims summarize other works, and the reference mapping is not sufficiently precise to accept every attribution without checking the primary source. In particular, the general claim that dynamic algorithms are better cannot replace the mixed results in V1–V2.

Use it as a literature map, not as independent proof or a reason to add machine learning dependencies. See PDF pp. 3–6, printed pp. 187–190; [DOI](https://doi.org/10.54254/2754-1169/135/2024.18582).

### V4: Kato

Kato studies VWAP execution under a generalized Almgren–Chriss impact model. The exact allocation proportional to current volume divided by final total volume is anticipating: the denominator is not known at decision time. A static causal solution uses a harmonic-mean volume profile under the stated assumptions. Adaptive and static optima coincide only in special cases. Risk aversion changes liquidation timing.

The unaffected price is modeled as a martingale, and the results depend on particular impact/volume assumptions. They address execution of existing inventory, not alpha from price crossing a daily VWAP. KAIROS cannot use the final day's volume to plan earlier orders.

Separate execution quality from signal quality. Causal liquidity observations may bound child orders, but future volume must never finance a fill. Protection urgency is different from benchmark tracking. See the model, optimal-strategy results and numerical risk-aversion sections, PDF pp. 2–11.

### V5: Zarattini and Aziz

This is the one supplied VWAP paper with an explicit directional trading system. It goes long above intraday VWAP and short below, reverses on completed one-minute crossings, and closes at the US equity-session end. On QQQ during 2018–2023, the authors report 671% total return, a 2.1 Sharpe ratio and approximately 9.4% maximum drawdown. They also test leveraged TQQQ, SMA placebos and time-of-day attribution.

The result assumes zero slippage and a very low $0.0005/share commission. It uses both directions, full-equity positions, compounding, regular US sessions and, for TQQQ, leveraged exposure. Its own discussion notes sensitivity to commissions. There is no transferable guarantee from above-VWAP price to future trend, and retrospective session-hour selection needs external testing.

The useful hypotheses are trend continuation versus choppy crossings, low win rate with asymmetric winners, SMA controls, and time-of-day diagnostics. For BTC, start with our existing trend-pullback/reclaim cases and UTC-hour observations. Do not import equity trading hours, leverage, shorts, unrestricted VWAP reversals or fee assumptions. A trailing exit is a later isolated hypothesis, not a remedy for targets that already lose after costs. See PDF pp. 5–10, 17–23 and Tables 3–5; [SSRN record](https://ssrn.com/abstract=4631351).

### V6: Kakade, Kearns, Mansour and Ortiz

The paper develops online algorithms with competitive guarantees relative to VWAP and one-way trading. It distinguishes a simplified price/volume model from an order book with finite liquidity, price/time priority, own market impact and partial executions. Adaptive decisions use observed volume; fixed schedules have poor worst-case guarantees under the stated bounds.

The first model permits transactions at the posted price without finite-depth friction and requires terminal completion. The order-book model has restrictive price/volume assumptions and omits cancellation behavior. Its bounds are relative benchmark guarantees, not expected profit or calibration for OKX retail orders.

Use finite-liquidity and partial-fill diagnostics, but do not claim that an OHLC candle reconstructs a book or queue. Preserve owned quantities and disclose incomplete liquidation. See PDF pp. 1–8 and the proof appendices on pp. 9–10.

## What the papers change in our research

1. Audit target economics and execution before adding indicators or AI. A target touch, support bounce or VWAP-tracking improvement is not net profit.
2. Keep pivots causal and freeze pending swing plans. Endpoint selection matters at least as much as the named ratios.
3. Test earlier valid confirmation and remove redundant checks one at a time. The evidence does not justify accumulating filters.
4. Compare Fibonacci against generic support/resistance and VWAP against a matched unweighted mean. Preserve the setup families regardless of the result.
5. Separate trend continuation from mean reversion. The lower −2σ spot setup remains a long recovery, not a short. Daily volume-weighted HLC3 sigma is not a normally distributed return model.
6. Judge any model by incremental after-cost outcomes, retained activity, latency and reliability. These papers do not evaluate OpenAI Responses, Decisions or Jev.

The existing June pilot is development data because it has been repeatedly inspected. Another short, frozen test can check whether a change transfers; it cannot alone establish robustness or replace prospective execution evidence.
