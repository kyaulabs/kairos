# Broad Alpaca-cost study results

**The fixed seven-day rule failed the preregistered progression gate on both assets.** Lower fees improved the matched 2025 result but did not establish a useful signal. No risk-constrained simulation or Alpaca Paper activation followed.

This is seven years of Kraken BTC/USD and ETH/USD price history with assumed Alpaca crypto costs. It is not Alpaca market history, an executable Kairos portfolio or a fresh confirmatory holdout. All 2026 observations remain excluded.

## Annual outcomes

Each asset supplied 2,588 contiguous daily bars including warm-up, with 2,550 evaluated daily returns across 2019–2025. Annual windows start flat at the January 1 opening and finish at the December 31 opening. Their normalized outcomes are not joined into a lifetime portfolio return.

The base case uses a 25-bps fee per side, 10-bps full spread and 10-bps slippage per side. Stress retains the 25-bps fee but raises spread and slippage to 30 bps each. Fees are Kairos's planning reserve, not verified historical/account rates. All figures below are unfunded, unconstrained price-index changes.

| Asset | Year | Gross momentum | Base momentum | Stress momentum | Base passive | Closed signal episodes |
|---|---:|---:|---:|---:|---:|---:|
| BTC | 2019 | +43.54% | +5.91% | −15.68% | +93.96% | 37 |
| BTC | 2020 | +139.84% | +84.19% | +51.10% | +299.67% | 32 |
| BTC | 2021 | +33.49% | −1.50% | −21.58% | +61.50% | 38 |
| BTC | 2022 | −59.30% | −68.50% | −74.00% | −64.32% | 32 |
| BTC | 2023 | +80.52% | +36.43% | +10.59% | +152.98% | 35 |
| BTC | 2024 | +64.65% | +30.56% | +9.71% | +117.50% | 29 |
| BTC | 2025 | +0.65% | −23.32% | −37.47% | −6.08% | 34 |
| ETH | 2019 | +11.75% | −10.67% | −24.49% | −0.74% | 27 |
| ETH | 2020 | +165.34% | +97.36% | +58.07% | +480.52% | 36 |
| ETH | 2021 | +257.41% | +172.30% | +122.05% | +399.14% | 34 |
| ETH | 2022 | −29.46% | −44.95% | −54.29% | −67.65% | 31 |
| ETH | 2023 | +6.03% | −19.87% | −35.04% | +90.27% | 34 |
| ETH | 2024 | −7.58% | −28.45% | −40.95% | +45.88% | 31 |
| ETH | 2025 | −2.89% | −26.02% | −39.67% | −11.60% | 34 |

Cash returned zero in every case. BTC naturally closed 237 signal episodes and ETH 227. The 2024 BTC and 2019 ETH windows fall below the separate 30-episode annual interpretation threshold; they remain in the report and pooled sample, not silently discarded. Episodes and daily returns are dependent observations, not independent broker trades.

The pooled sample requirement passed for both assets. BTC had four positive base years and three positive stress years, below the required five. Its median annual changes were +5.91% base and −15.68% stress. ETH had two positive years under either cost case; its medians were −19.87% and −35.04%. Large gains in selected bull years do not establish consistency across the full period.

Production stops, sizing, deadlines, liquidity limits and loss halts are absent from these indices. For example, the BTC 2022 base daily-mark drawdown reached 71.67%. These losses must not be interpreted as outcomes of Kairos's bounded, risk-controlled portfolio.

## Fee-only comparison

The 2025 gross and 40-bps control results exactly reproduce all 12 prior full index artifacts, including cash and passive references for both assets. All six 25-versus-40 decision paths are identical. Only the fee assumption changes; spread, slippage, dates and the signal remain fixed.

| Asset | Prior 40-bps momentum | 25-bps momentum | Improvement |
|---|---:|---:|---:|
| BTC | −30.75% | −23.32% | +7.44 percentage points |
| ETH | −33.19% | −26.02% | +7.17 percentage points |

Cheaper fees reduce the drag but do not rescue the 2025 rule. Fees use the frozen normalized quote-cost formula, not Alpaca's actual fee-asset bookkeeping or fill reports.

## Uncertainty and progression

Paired seven-day moving-block intervals use 16,384 resamples and one-sided alpha 0.05/36. The original family correction retains the four unavailable 2018 comparisons; 32 intervals were actually computed. Annual-boundary gaps are not filled and blocks cannot cross them.

| Pooled base comparison | Mean difference, bps/day | Adjusted interval, bps/day |
|---|---:|---:|
| BTC momentum minus cash | +1.94 | [−12.98, +16.71] |
| BTC momentum minus passive | −15.93 | [−28.34, −2.31] |
| ETH momentum minus cash | +4.93 | [−13.56, +23.65] |
| ETH momentum minus passive | −16.91 | [−32.12, +1.67] |

These are differences in mean daily returns, not cumulative or median annual changes. Neither asset has the required positive lower bounds against both references. BTC's interval against passive is entirely negative under this descriptive bootstrap. That is evidence against this fixed rule in this sample, not proof that all momentum designs fail or a forecast of future returns.

The sample, consistency and adjusted-bound requirements were not relaxed after seeing results. No year, scenario or losing run was dropped. No fitting, parameter search, optimizer, calibration, volatility overlay, PBO/DSR estimate or paper experiment followed. The prior execution-validity gate remains unresolved; more price observations do not fix its representation and evidence gaps.

## Data quality and audit

A timestamp-only precheck found ETH/USD missing January 12, 2018. The original 2018–2025 draft was retained and rejected before any new index performance was examined. Both assets then used the common complete 2019–2025 window with December 2018 warm-up. No price was invented and no venue was spliced into the gap. The 2,000-day, 100-episode, five-positive-year and 36-comparison thresholds were retained. Omitting 2018 limits regime coverage and is disclosed rather than treated as neutral missingness.

The existing 8.97-GB Kraken archive was rechecked against its full SHA-256. The selected members passed CRC validation, and all selected dates, duplicates and candle values were validated. No additional archive download or duplicate copy was needed. There are no Alpaca market rows, account reads or credential accesses in the run.

The study added 135 completed attempts: one timestamp precheck, one dataset selection, one study family and 132 annual index/scenario runs. The separate Kraken registry now retains 157 attempts overall, with the original failed transfer preserved, 156 completed attempts, no unresolved starts and zero final claims. The preliminary missing-data finding and rejected draft are preserved in the journal.

Frozen source: `599bc95`. [BROAD_PLAN.json](BROAD_PLAN.json) is the signed preregistration and [BROAD.md](BROAD.md) describes the method. [BROAD_RESULTS.json](BROAD_RESULTS.json) publishes all annual/scenario summaries, all primary daily return series, dataset provenance and study events. Detailed annual objects remain in the Kraken-only content-addressed store at their referenced hashes.

- Dataset: `0980b086052b39757ab8f4cc2c8cd94a09549e73ed34b39a17cc01fa9d97ba07`
- Report: `a61234c1e0c32c48c3448f7dddc7d1e2f0f8e4b391224616efb4ddf8719b7566`

All 422 Python tests passed, including new regressions for exact fee attribution, independent annual windows, leap-year boundaries, overlap/consumed-period rejection and fail-closed progression. This verifies the software, not historical execution fidelity. No production controls, balances, orders, allocation, service state, releases or deployments were changed.
