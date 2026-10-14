# Passive maker entry comparison

The user requested passive maker entries, then selected the original and previously accepted rules with a fixed best-bid order lasting 30 seconds. Exits stay taker IOC orders. This is offline execution research, not permission to trade, deploy or change the live adapter.

## Frozen comparison

Use February 1–exclusive May 1, 2024, the same reused development quarter as the [sequential study](three-month-series.md). Keep all eight setups at 5m and 15m. Compare the original positive-target rules and the accepted EMA-recapture/extension-acceptance ablations separately. Do not rerun selection or call the accepted rules optimal for maker execution.

For each rule profile, run base and adverse execution with fresh IOC controls and passive maker entries: 128 independent cases in four paired reports. Both arms retain the positive-target boundary, $1,000 starting capital, at most 1% planned stop risk on the smaller of initial capital/current equity, dust reserves, cash/minimums/ownership, the $12.50 daily entry-loss latch, four-hour holding deadline and bot-managed protection. No leverage, halt resets, terminal liquidation or clipped realized losses. Maker sizing must use its own entry fee and the same taker exit fee; it need not produce identical quantities.

Maker entry rules:

- Observe the first minute after the completed signal; a missing next minute still expires the candidate.
- Post once at that observed synthetic best bid, below the ask, and freeze the price. Expire after 30 seconds. No repricing, chasing or taker fallback.
- Require a strictly later synthetic path price below the limit before expiry. Neither a touch nor the submission quote creates a fill.
- Use only preceding completed-minute volume times the declared participation cap, shared with exits. Current/future volume cannot fund a fill.
- On the first eligible crossing, fill only available quantity and cancel any unfilled remainder before owned protection. Do not add to the position later.
- A crossing through both the entry and stop fills first, then triggers protective IOC at the observed adverse price. Never avoid losing entries by canceling them retrospectively.
- Assume 8 bps maker entry fees and 10 bps taker exit fees, received in BTC/USDT as in the existing native-accounting model. No historical account tier or rebate is asserted.

These are limit-order fill assumptions, not reconstructed OKX prints, queues, cancellation acknowledgements or depth. The current KAIROS adapter allows post-only in demo but not live; the research change does not alter either write gate.

## Timing limitation

The existing minute paths put extrema at seconds 20 and 40. With a 30-second order, OLHC can reach its low while the order rests; OHLC reaches its low after expiry. The adverse maker arm may therefore have no fills by construction. That is a timing sensitivity and activity failure, not free protection or evidence of profitability. Do not extend expiry, move extrema or choose another path after seeing outcomes.

This experiment cannot establish a real 30-second fill probability from minute candles. Better execution evidence would require historical trades/books or a separately authorized forward paper observation, not an optimistic candle-touch rule.

## Verification and delivery

Run a February 1–exclusive February 3 execution pilot on Apollo before the full quarter. The pilot is for implementation checks, not choosing prices, lifetimes, rules or thresholds. Preserve all failures and restart fresh after any executor repair.

Require every full-quarter IOC case to reproduce the corresponding corrected attempt-004 control after the existing identity normalization: original/accepted base and original/accepted adverse, 64 controls total. Signals and frozen plans must match between execution arms even when fills, fees, sizes, cash and later ownership differ. Report signals, attempts, partials, fill rate, active days, missed entries, fees, marked return, closed R, overruns, halts and retained BTC. Inactivity does not count as an improvement.

Archive the declaration, source/runner hashes, four paired HTML/JSON reports and pilot separately under the protected research roots. At completion, revise the [test guide](test-guide.md) and themed offline contents page into groups for the quarter sequence, maker execution, AI shadow, pilot checks and invalid attempts. Preserve earlier report bytes. No additional AI invocation is included or authorized.

Results are pending at this declaration.

## Reproduction

The archived comparison runner constructs the two frozen signal profiles and calls the shared research executor. For an original-rules pilot only, the ordinary CLI also supports:

```sh
kairos-research --archive /path/to/selected-quarter.zip \
  --start 2024-02-01 --end 2024-02-03 \
  --economic-policy positive-target --entry-execution maker-best-bid-30s \
  --output /path/to/new-maker-pilot.json
```

Use a new protected output path. Historical batches run on Apollo, outside deployed state.
