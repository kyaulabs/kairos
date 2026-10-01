# Kraken prices with assumed Alpaca costs

This study extends the fixed seven-day momentum rule across 2019–2025 using Kraken BTC/USD and ETH/USD daily candles. It tests whether a broader history and the lower Alpaca planning fee justify a later risk-constrained simulation. It does not activate Alpaca Paper.

[BROAD_PLAN.json](BROAD_PLAN.json) freezes the specification before examining the new annual outcomes. The existing [price-index implementation](kraken_daily.py) is reused without changing signals, timing or accounting. Prices, volume and the artifact registry remain Kraken-only. No Alpaca price rows, credentials, account state or old final-window data are read.

The [completed study](BROAD_RESULTS.md) failed the progression gate on both assets. All annual scenarios, primary return series and audit records are preserved in [BROAD_RESULTS.json](BROAD_RESULTS.json).

## Method

A timestamp-only coverage check found the ETH/USD daily candle for January 12, 2018 missing. The original 2018–2025 draft and that check were retained before any new returns were examined. Both assets therefore use the common complete 2019–2025 window with December 2018 warm-up. No source is spliced into the gap. The 2,000-day, 100-episode and five-positive-year thresholds remain unchanged, as does the conservative 36-comparison correction.

Each year is evaluated separately from its January 1 opening through its December 31 opening, using prior causal warm-up and a flat initial index of one. This deliberately omits the last daily interval of every year and prevents a 2026 endpoint. Annual resets are separate normalized experiments, not resets of an actual account. No joined lifetime portfolio return is reported.

The rule remains long when the trailing seven-day close return is positive, otherwise cash. A bar becomes available at its close plus an assumed 60 seconds, so a midnight decision cannot use the immediately preceding daily close. These timestamps are conventions, not historical arrival evidence.

All years report momentum, cash and passive under zero costs, a base 25-bps-per-side fee with 10-bps full spread and 10-bps slippage per side, and the same 25-bps fee with 30-bps spread/slippage stress. The fee is Kairos's Alpaca planning reserve, not a verified account or historical fee schedule.

A separate 2025 control retains the previous 40-bps fee with identical spread/slippage. Its full index artifacts, and the 2025 gross artifacts, must match the prior Kraken study exactly. The 25-versus-40 comparison must retain identical decisions. A mismatch stops attribution rather than silently changing the comparison.

This is retrospective chronological evaluation of a fixed rule, not a fitted walk-forward model or blind holdout. There is no fitting, parameter search, favorable-year selection or adaptation after viewing earlier annual outcomes. 2025 was already examined as development data. All 2026 observations remain excluded.

## Decision gate

The base-case momentum/cash and momentum/passive comparisons form one 36-comparison family: two assets, two references and seven available annual windows, the unavailable 2018 window, and one pooled window. The four unavailable comparisons are not dropped from the correction. Paired seven-day moving-block intervals use 16,384 resamples, seed 41073 and one-sided alpha 0.05/36. Blocks cannot cross the omitted annual boundary intervals. These intervals describe mean daily differences under historical regime frequencies, not future regime probabilities or cumulative portfolio returns.

Both assets must have at least 2,000 pooled daily observations and 100 naturally closed signal episodes, at least five positive years and positive median annual changes under base and stress costs, and positive adjusted pooled lower bounds against both references. Annual windows below 30 naturally closed episodes are flagged separately. More candles or repeated scenarios do not create independent trades or independent candidate strategies.

A failed gate stops this candidate before risk-constrained simulation. A pass only permits that offline follow-up; it cannot pass the earlier execution-validity gate or authorize paper activation. No PBO/DSR is manufactured from one fixed rule and its reference/cost repetitions.

These are unfunded price indices, not executable portfolios. They omit market minima, capacity, partial fills and production risk controls. Before any forward paper trial, the offline execution representation must handle asset-specific rules, eligible residual additions and order chronology correctly, while preserving bounded sizing, independent protective exits, 80%/100% exposure hysteresis, the $12.50 daily-loss halt and no automatic restart. Actual paper account access and activation require separate authorization.

## Reproduction

The existing verified archive is reused without downloading or extracting another copy. The previous Kraken report must already be present in the separate Kraken artifact store, which also retains every new run and failure.

```sh
python -m research.broad_fees \
  --root /home/kyau/.local/share/kairos-research/kraken-btc-eth-001
```

The command records the plan, source hashes, Git revision, Python version, selected-row hashes, annual index objects and all outcomes. It makes no broker or other network calls. It does not import the legacy execution simulator or modify production trading paths.
