# VWAP and Fibonacci metas

`vwap-meta` and `fib-meta` are deterministic, long-only cash strategies for Kraken
Dry-run and explicitly authorized, finite OKX Demo runs. They are unavailable on
Alpaca, margin, Futures and live execution. Adding or selecting them does not
start trading or change the default HTF strategy. Jev is not used.

VWAP priority is pullback, reclaim, anchored VWAP, then reversion. Fibonacci
priority is confluence, EMA pullback, extension, then sweep. Every child advances
on each completed confirmation bar, including while a position is owned or
entries are halted. One fixed-priority proposal wins; a rejected proposal does
not fall through to another family. All children share the existing cash ledger.

`meta_structure_minutes` selects 15m, 30m, 1h, 6h or 12h structure; the default is
1h. Confirmation and daily VWAP always use completed native 15m bars. Higher bars
require complete, UTC-aligned groups; missing bars are not padded. Both clocks
need 50 observed bars, and daily VWAP needs 20 session bars. Large frames can
require days of accumulation beyond the provider's initial history. Indicator
checkpoints survive restart; no historical download or alternate venue is added.

The rules retain EMA20/50, ATR14, confirmed two-right-bar pivots, causal parent
impulses, original arming expiries and immediate Fibonacci breakout confirmation.
A regression fixture compares signal geometry with the frozen deterministic
research implementation. This is not a new financial validation.

Before an entry, costs must leave positive modeled target reward. Planned stop
risk is at most 1% of the smaller of initial capital and current equity, capped
at 10 units of the selected quote currency. Order/exposure ceilings are 1,000
quote units or the existing smaller limits. Kraken paper uses quote-denominated
fees; OKX planning conservatively reserves the worse base/quote entry-fee case.
Dust remains owned and risk-reserved. No currency parity or attached native stop
is assumed.

Stops, targets and the fourteen-day maximum deadline are saved before submission.
The first exit trigger stays latched through partial fills and restarts. Existing
protection is checked before fetching entry history. Signals are consumed before
order I/O; startup, recovery, stale history and missed first-minute entries do
not produce catch-up orders. Venue permission, fees, order count, price bounds
and existing settlement safeguards still apply.

The marked entry-loss latch uses the smaller of the configured daily-loss limit
and 12.50 quote units. It blocks entries, not protective reductions, and survives
Stop, restart and UTC rollover. Kraken's explicit paper reset clears its meta
state. OKX has no implicit balance or latch reset. Unrelated inventory cannot be
adopted; retained ownership prevents switching strategies, markets or frames.

Protection only operates while running with fresh execution data and valid
permission. An OKX authorization can end before a position's fourteen-day
maximum; expiration does not liquidate holdings or renew permission. Gaps,
illiquidity, minimums, fees and partial fills can exceed planned risk. Overruns
are reported, not clipped. The completed research did not establish a robust
tradable edge, and these additions do not authorize deployment or promotion.
