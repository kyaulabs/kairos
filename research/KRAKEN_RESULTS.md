# Kraken daily pilot results

**The fixed seven-day momentum price rule did not survive the declared cost assumptions in this 2025 development sample. No strategy promotion is supported.**

This is the separate daily price/cost pilot requested after the execution-validity audit. Its unfunded indices are **not Kairos portfolio returns**: they assume full fractional transitions and omit broker fills, market minima, capacity and production risk controls. The earlier execution gate remains blocked. Neither its defects nor Alpaca's data were imported into this study.

## Results

Both assets supplied 396 contiguous daily bars, including warm-up, and 364 open-to-open evaluation returns from January 1 to the December 31, 2025 opening. Each momentum index naturally closed 34 signal episodes, exceeding the fixed minimum of 30. These are index episodes, not broker trades or independent observations.

| Asset | Gross momentum | Base-cost momentum | Stress-cost momentum | Base-cost passive | Signal-closed episodes |
|---|---:|---:|---:|---:|---:|
| BTC/USD (`XBTUSD`) | +0.65% | −30.75% | −56.98% | −6.36% | 34 |
| ETH/USD | −2.89% | −33.19% | −58.50% | −11.86% | 34 |

Cash returned 0%. Base costs assume a 40-bps fee per side, 10-bps full spread and 10-bps slippage per side. Stress assumes 80, 30 and 30 bps respectively. These are sensitivities, **not authenticated Kraken account fees**. Gross results assume zero frictions and are not executable claims.

Momentum was active on 187 BTC days and 176 ETH days. Its base-case daily-mark drawdowns were 33.19% and 47.85%; hypothetical fees were 0.22294 and 0.22901 per initial index unit. Fees are already included in the reported index changes. Frequent entry/exit transitions consumed the weak gross result. This does not demonstrate that all momentum rules fail or that actual Kairos accounts would experience these returns: production's sizing, stops, deadlines and loss limits are deliberately absent from these price indices.

No parameter, fee, date or signal threshold was changed after the result. Cash, passive and momentum were reported under all three fixed scenarios. Hourly optimization and alternative lookbacks were not run.

## Uncertainty and decision

Primary comparisons use paired seven-day moving-block intervals with 4,096 draws and one-sided Bonferroni alpha 0.05/4. Bounds below describe the **mean daily return difference**, not cumulative index performance.

| Base-case comparison | Mean difference, bps/day | Adjusted interval, bps/day |
|---|---:|---:|
| BTC momentum minus cash | −9.06 | [−26.38, +7.88] |
| BTC momentum minus passive | −9.65 | [−23.52, +8.43] |
| ETH momentum minus cash | −7.74 | [−38.70, +28.58] |
| ETH momentum minus passive | −11.93 | [−40.37, +20.21] |

Every primary interval includes zero. Both assets fail the positive base/stress index-change criterion and positive adjusted lower-bound criterion. Thus this pilot supplies **no statistically supported incremental improvement**. The observed cost test is unfavorable, but the intervals do not establish a general negative expected return for momentum across future periods.

Only one fixed signal was tested; cash/passive are references and cost cases are sensitivities. PBO/DSR were not fabricated from those repetitions as though they were a strategy-search family. There was no optimizer, learned model, calibration, multi-agent system or reinforcement learning.

2025 was already examined as market development data during the Alpaca work. A different venue does not turn it into an independent sample. This was not a new final test. The earlier consumed final window was neither reused nor relabeled; all 2026 rows were excluded before price decoding and no 2026 mark was permitted.

## Provenance and reproduction

The official Kraken OHLCVT archive is 8,972,380,104 bytes. All five published part hashes and the complete archive SHA-256 matched. The first transfer timed out; its receipt remains alongside the successful bounded-range resume. No duplicate assembled archive was kept, and approximately 10.6 GB remained free on the download filesystem.

The study selected only `XBTUSD_1440.csv` and `ETHUSD_1440.csv`, verified ZIP CRCs, recorded member/selected-row hashes and rejected gaps or duplicates. No stablecoin/wrapped-token pair substitution or Alpaca backfill occurred. The retained archive also contains hourly data, but it was not used for this daily pilot.

The separate Kraken registry contains **22 attempts: 21 completed and one failed transfer**, with no unresolved starts and no final claims. It contains two archive-transfer records, one dataset selection, one study family and 18 index/scenario runs. Transfer receipts were imported after acquisition with their original timestamps and an explicit retrospective-import flag; they were not represented as pre-existing SQLite journal entries.

Frozen evaluation source: `dc8b37b`. [KRAKEN_PLAN.json](KRAKEN_PLAN.json) contains the preregistration and [KRAKEN.md](KRAKEN.md) explains the price-index assumptions and reproduction command. [KRAKEN_RESULTS.json](KRAKEN_RESULTS.json) publishes all aggregate results, daily return/episode series, source metadata and trial records. Detailed decision/mark objects remain at their referenced hashes in the Kraken-only artifact store. No raw Alpaca rows, fitted models, portfolio histories or credentials are present.

- Official archive SHA-256: `fc81b54cba6e12af3e9422dde9416179e6ef76af4831d48d839fbdb43018eaa4`
- Selected dataset: `1fccafbaed97287b6ee2d051f9e3645c2fe1eb84d13f64a0dce8aa8486ae2a96`
- Report: `a8795f8b56aa34c88656f1b9d0dcdef3d880b6be7f6a8f809fe37d82dd3b0621`

Verification passed 418 Python tests, including seven new tests for publication lag, future perturbations, cost identities, monotone friction effects, gap rejection and exclusion of the consumed period. Historical arrival times, revisions, executable quotes, queue behavior and account-specific fees remain unverified. Archive checksums establish file integrity, not historical execution fidelity.

No production changes, orders, account access, allocation, release or deployment resulted from this study.
