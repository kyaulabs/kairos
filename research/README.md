# Kairos research

Offline evidence, not a trading feature. Nothing here starts an engine, loads credentials, binds an account, changes settings, or allocates funds. The production checkout and both portfolios stay unchanged.

The first study is fixed in [`PLAN.json`](PLAN.json): BTC/USD and ETH/USD on Alpaca US, evaluated separately with identical hypothetical $500 allocations. These are two asset replications, not a jointly executable portfolio. There are five arms: deterministic momentum and four separate additions. No combined winner or parameter search is authorized.

## Repository audit

Audit baseline: `7a9d4927681673cf9db296ff0109abb18ac458cd` (v0.3.1 tree).

| Component | Implemented behavior | Research gap |
| --- | --- | --- |
| `kairos/replay.py` | Legacy HTF rule adapter, chronological 70/30 segments, production paper risk executor, explicit synthetic open-low-high-close path and unlimited full fills | Not rolling-minute/Jev/pullback or hosted-Alpaca reproduction; no trial ledger, training-only preprocessing, data vintages, validation/final separation or statistical uncertainty |
| `market_data.CandleHistory`, `HTFReview` | Confirmed bars, contiguous rolling minutes, no forming-bar promotion, asynchronous reviews with expiry/signature rechecks | Production freshness is not a historical publication/revision archive |
| `Store.htf_minutes` | Durable per-pair confirmed-minute cache | No first-seen/revision vintages, raw response hash, historical bid/ask depth, universe or corporate-action provenance |
| `htf.py`, `htf_policy.py` | Fresh-signal gate, passive pullback entries, target-room cost screen, 80% exposure target/100% trim trigger, stops, deadline and daily-loss precedence; range observation only | Not the proposed deterministic momentum experiment; do not relabel its historical results |
| `HTFReview`, `settings.min_confidence` | Model confidence participates in a guarded decision threshold | Not calibrated probability of net trading profit; no labeled calibration study |
| `Alpaca.request`, `AlpacaEngine.start/place` | Fixed hosted-paper host, permission plus confirmation, no live mode; dedicated account binding, durable intents and no blind write retry | Authenticated execution quality not established by fixtures or public data reads |
| `AlpacaEngine.reconcile_account/apply_fee` | Reconcile fills before balances, account identity/external-activity checks, exact-once account fees and ownership adjustments | Per-order fees are unavailable, not zero; historical broker queue/rejection/latency distributions unavailable |
| `Alpaca.add` | Crypto non-crossing GTC intent, local cancellation, no exchange post-only guarantee; bounded IOC exits | Bar crossing is not evidence of queue priority or a maker fill; a stopped process cannot guarantee GTC cancellation |
| `Alpaca.bars/book` | Completed REST bars; free IEX equity quotes, crypto books; bounded pagination | Retrospective bars do not establish when a bar/correction was observable; IEX is not NBBO |
| `AlpacaEngine.validate_capabilities` | Equities: whole-share, long-only scheduled strategies in regular sessions | Equity HTF, fractional shares, shorts, margin, options and live Alpaca are unavailable |
| `alpaca_markets.py` | Watch/favorite-only volatile USD volume cache | UI cache is neither a research dataset nor an execution-liquidity measure |

`ALPACA.md` describes intended hosted-paper limitations accurately, but its statement that authenticated account access has never been exercised is broader than the operational record: credential loading and adapter initialization were subsequently verified. No inference about actual broker order execution follows. This task does not rewrite deployment history or run broker smoke trades.

## Study contract

Training: 2024-01-01 through 2024-09-30 UTC. Calibration: 2024-10-01 through 2024-12-31. Validation: 2025. Final evaluation: 2026-01-01 through 2026-08-31. December 2023 supplies warm-up only. All boundaries are half-open; labels must be fully available before the boundary minus a 24-hour embargo. Rolling feature history may cross a boundary backwards, but fitted transformations and labels may not cross forwards.

The universe and dates are chosen before examining strategy returns. Public historical periods are not truly blind to human financial knowledge; this study cannot erase earlier general market knowledge or prior Kairos LINK exploration. The local registry records this study's evaluations, not every experiment ever performed outside it.

Hypotheses, tested separately:

- **Momentum:** positive trailing 168-hour return permits a long entry; otherwise cash. Fresh entry transitions, no pyramiding, a 3% stop, seven-day deadline and mandatory risk exits apply.
- **Volatility:** reduce entry size by `min(1, 0.60 / trailing annualized volatility)` using 168 hourly returns. No leverage and no new entry solely to rebalance dust.
- **Cost:** suppress an entry unless trailing weekly return divided by seven exceeds the scenario's round-trip cost plus 10 bps. This is a deliberately simple proxy, not an expected-return forecast or convex optimization.
- **Instability:** suppress entries when 24-hour volatility exceeds twice 168-hour volatility. This is a transparent stress proxy, not Bayesian changepoint detection.
- **Quality:** a fixed regularized logistic model uses trailing one-day/week returns and volatility. Fit preprocessing/model on training only; fit one temperature on the separate calibration period. The fixed entry cutoff is 0.55. Labels describe a hypothetical delayed 24-hour net price return, not realized broker trade profit. No Jev score is reinterpreted as a probability.

All additions remain subordinate to protective exits. Initial capital is $500, order/exposure caps $100, entry target at most $80 and daily loss $12.50. No reinvestment, shorting, leverage, funding or automatic resume after a daily-loss halt. Market moves and failed exits can exceed a loss threshold: it is a trigger, not guaranteed insurance.

Costs and execution are predeclared in the plan. Both passive and aggressive executions pay a 25-bps-per-side fee assumption. Passive entry requires a *subsequent strict crossing*, bounded volume participation, and expiry; touching is insufficient, and a miss has no taker fallback. Rejections and partial fills use reproducible common random numbers. Protective exits take priority and may also reject/partially fill. Base/stress delays are one/two full hourly bars. Hourly checks cannot reproduce production's roughly ten-second protection cadence. Spread, slippage, queue, participation, rejection and historical market-rule assumptions are **scenarios, not measured Alpaca execution facts**. The $10 minimum and precision in the plan are explicitly synthetic, not asserted broker capabilities.

Compare cash, an unconstrained buy-and-hold reference (not deployable under Kairos caps), capped passive exposure, and passive exposure scaled using prevalidation realized risk. Risk scales are estimated on the October–December 2024 calibration partition, never on validation/final returns. For that calculation only, quality uses its September-fitted coefficients with temperature fixed at 1, so future calibration labels cannot affect earlier decisions. Freeze each scale as `min(1, arm_volatility / capped_passive_volatility)`; unavailable/zero passive volatility gives scale zero, disclosed as an uninformative cash reference. Report realized out-of-sample risk too: this is not a guarantee of equal future risk. No ex-post leverage or volatility matching.

The capped passive benchmark retries entry on a later observed schedule after a flat exit, with the same caps, stops, deadline and permanent daily-loss halt. The unconstrained buy-and-hold *price reference* assumes full investment at the first observed opening with adverse spread/slippage and fees; it intentionally omits liquidity, latency, rejection, cap and protective restrictions. Neither benchmark is represented as the deployed HTF strategy. Only the executable scenario arms/capped references use passive expiry/partial-fill assumptions; the full price reference is not an executable alternative. Momentum and its additions consume a fresh *base momentum* transition; a suppressed/missed entry is not retried within that same positive episode.

## Acceptance and failure

Freeze the source revision, dataset, plan, learned model and validation report before opening final performance. The final family is consumed once, including if execution fails. A bug discovered after opening it requires a disclosed invalidation and genuinely new evaluation data, not a fresh registry or altered dates on the same data. No final-window tuning, early stopping, threshold selection or strategy selection.

Require at least 99% coverage, 180 daily observations and 30 completed round trips per compared active arm/asset. Never fill missing market bars. Report insufficient information as inconclusive. The primary family has 16 comparisons: four additions × two assets × two benchmarks (momentum and training-risk-matched passive). Use paired seven-day moving-block bootstrap, 4,096 fixed-seed draws, with one-sided Bonferroni-adjusted confidence bounds at `1 - 0.05/16`. Report daily observations rather than pretending overlapping hourly labels or correlated assets are independent trials.

A supported improvement requires positive net returns on both assets, positive adjusted lower bounds against both benchmarks, positive stress returns, maximum drawdown ≤10% and ≤baseline +1 percentage point, adequate samples, and no leakage/risk failures. It additionally requires verified point-in-time and execution inputs. **Retrospective bar-only simulations cannot meet that last requirement.** Adequate evidence with a nonpositive adjusted upper bound on incremental return, or a safety violation, rejects the improvement under the tested conditions. Other outcomes are inconclusive. These criteria do not change after results arrive.

Report every arm, failure, halt and sensitivity; fees, estimated spread/slippage, turnover, rejections, partial fills, exposure, open holdings, drawdown and daily net returns. Event processing orders closes, openings and later bar-publication events by timestamp. An opening cannot consume the just-closed bar's unpublished volume or be canceled using a later publication. Passive fills are confirmed only at bar expiry; an early cancel request does not erase possible intrabar fills. This conservative ambiguity is not measured queue simulation. Drawdown includes close marks and a separately labeled adverse bar-low bound, not a reconstructed intrabar path. Liquidation valuation is an estimate, never an invented closing fill. DSR and CSCV/PBO are design-window diagnostics, not leakage detectors or replacements for chronological final evaluation. Their independence/stationarity assumptions and small-family limitations must accompany any values. Diagnostics use the five fixed candidate columns within each asset/scenario replication, not cash/reference benchmarks or acquisition attempts. Physical attempts remain separately journaled; a failed/constant candidate makes the comparable-family diagnostic unavailable. CSCV uses eight equal blocks, disclosing any omitted trailing days; it does not refit models on shuffled time blocks.

## Commands

From this checkout with Python 3.12+, choose a persistent research-only directory (not production `data/`). No extra dependency is needed:

```sh
ROOT=/path/to/research-artifacts
python3 -m kairos.research collect --root "$ROOT"
python3 -m kairos.research development --root "$ROOT" --dataset DATASET_HASH
python3 -m kairos.research seal --root "$ROOT" --dataset DATASET_HASH --development REPORT_HASH
python3 -m kairos.research final --root "$ROOT" --dataset DATASET_HASH --development REPORT_HASH
python3 -m kairos.research journal --root "$ROOT"
```

`collect` prints a dataset manifest; each evaluation prints its report hash and summary. Detailed decisions/orders/returns and fitted models are separate verified objects referenced by that report. Preserve the entire directory. Do not run `final` until the reviewed source, data-quality checks and validation report are sealed. A final failure is still a consumed holdout. Identical-code/seed outputs are reproducible, but deliberate repeat validation runs remain separate physical trials.

## Boundaries

- No original paper's empirical result has been reproduced. [`SOURCES.md`](SOURCES.md) identifies exact inspected versions, methods, samples, costs, adaptations and falsifiers.
- Original licensed datasets, historical broker rules/fees/books/arrivals and a calibrated fill model are unavailable. These remain evidence gaps, not invented defaults.
- Use a separate `research-artifacts/` directory. Content-addressed JSON objects contain raw pages, data vintages, models and reports. An append-only SQLite journal records starts before work, successful results, exceptions and incomplete/crashed trials. Preserve it between runs.
- Checksums and append-only APIs detect ordinary mutation; they do not protect against a privileged owner deleting files or conducting unregistered external experiments. Signed PRs preserve the protocol and source history. Never put credentials, production databases, or licensed paper PDFs in a PR.
- The research package is not imported by the web service. No production strategy change, release, deployment or paper/live activation is part of this authorization.
