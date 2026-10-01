# Kraken daily price pilot

This is a separate Kraken-only **price-signal and cost-sensitivity study**, not a rerun of the Alpaca execution simulator. It cannot pass the earlier execution-validity gate or establish broker profitability. No production strategy, account, portfolio or risk control changes.

The [completed pilot](KRAKEN_RESULTS.md) failed its declared cost criteria; [KRAKEN_RESULTS.json](KRAKEN_RESULTS.json) preserves all outcomes and audit records.

## Frozen scope

[KRAKEN_PLAN.json](KRAKEN_PLAN.json) is frozen before examining returns. Use Kraken XBT/USD (BTC) and ETH/USD only, with December 2024 warm-up and 2025 development observations. The last price mark is the December 31, 2025 opening, yielding 364 open-to-open daily returns without a 2026 endpoint. No 2026 row enters analysis.

2025 market behavior has already been examined in the Alpaca development study. Kraken is another venue, **not an independent market sample or a fresh holdout**. No model fitting, parameter search, cost-aware optimizer, volatility overlay, probability calibration, PBO/DSR estimate or production promotion is part of this pilot.

The signal is positive trailing seven-day close return. At a daily opening, only bars whose assumed publication time precedes that opening may contribute. Assuming close plus 60 seconds excludes the immediately preceding daily bar at midnight; this is a conservative timing convention, not verified publication history.

Three normalized, unfunded indices are compared: long/cash momentum, always cash and passive long. Full fractional transitions occur at opening prices with assumed adverse spread/slippage and fees. Entries pay `(1 + half_spread) × (1 + slippage) × (1 + fee)`; exits receive `(1 - half_spread) × (1 - slippage) × (1 - fee)`. Liquidation marks include exit costs, and fees are charged on transitions rather than invented daily rebalances. Terminal liquidation is labeled boundary-censored, not counted as a naturally completed signal episode.

These indices deliberately omit venue lot/minimum/capacity constraints and Kairos's order/exposure caps, stops, deadlines and daily-loss controls. **They are not executable portfolios or candidate production strategies.** Their unit starting value is a mathematical normalization, not a fund allocation. They do not repair or bypass the simulator defects identified in the staged audit.

Costs are fixed sensitivities:

| Scenario | Fee per side | Full spread | Slippage per side |
|---|---:|---:|---:|
| Gross reference | 0 bps | 0 bps | 0 bps |
| Base assumption | 40 bps | 10 bps | 10 bps |
| Stress assumption | 80 bps | 30 bps | 30 bps |

These are not authenticated account rates. Kraken's public pair response returned empty fee arrays. Current pair metadata is preserved as source context, not asserted to describe 2025 market rules.

Primary comparisons are base-assumption momentum versus cash and passive for each asset: four comparisons, paired seven-day moving-block intervals, 4,096 resamples and one-sided Bonferroni alpha 0.05/4. Gross/stress results are descriptive. Require at least 180 days and 30 signal-closed episodes per asset before treating the pilot as informative evidence. Daily marks and correlated BTC/ETH returns are not independent trades. Both assets must have positive base/stress index changes and adjusted positive incremental lower bounds; no favorable scenario replaces an unfavorable primary result. Even a pass would remain exploratory price evidence, not a broker-performance result.

## Data and reproduction

Kraken's official [time-and-sales page](https://support.kraken.com/articles/360047543791-downloadable-historical-market-data-time-and-sales-) led to its [OHLCVT archive](https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data). The latter provides daily and hourly candles without needing the much larger trade archive.

The complete OHLCVT archive through June 2026 consists of five published parts. The downloaded archive is 8,972,380,104 bytes and matches all part hashes and the published complete SHA-256:

```text
fc81b54cba6e12af3e9422dde9416179e6ef76af4831d48d839fbdb43018eaa4
```

The first sequential transfer timed out. Its failed receipt is retained; bounded range requests resumed it without duplicating the archive. The final checksum verifies the reused prefix as well as the resumed bytes. About 10.6 GB remained free after acquisition, above the five-GiB reserve.

Only exact `XBTUSD_1440.csv` and `ETHUSD_1440.csv` members are selected. ZIP CRCs and selected-file SHA-256 hashes are recorded. USD-stablecoin and wrapped-token pairs are not substitutes. Missing/duplicate daily rows fail closed; no synthetic candles or cross-venue backfill. Source CSVs lack VWAP, so validation uses the existing optional-VWAP sentinel without deriving a false VWAP.

The archive contains excluded years, but rows outside December 2024–December 2025 are filtered before price decoding, features or outcomes. Historical revisions and original observation times remain unknown. Candles contain no historical executable quotes, queue priority or account-specific fees.

Use a **separate Kraken research directory** containing the checksum-verified `Kraken_OHLCVT_Full_2026Q2.zip`:

```sh
python -m research.kraken_daily --root /path/to/kraken-research
```

The command verifies the archive checksum again and records dataset selection, every index/scenario attempt and the report in an append-only registry. Alpaca's database, raw data, models, trial journal and consumed final claim are not used or modified. Repeated exploratory runs must retain their earlier attempts; they cannot create a new holdout.
