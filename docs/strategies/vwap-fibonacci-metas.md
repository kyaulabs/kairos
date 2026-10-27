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

`meta_structure_minutes` selects 15m, 30m, 1h, 4h, 6h or 12h structure; the default
is 1h. VWAP meta keeps native 15m confirmation and its existing signal-derived
stops/targets and fourteen-day maximum hold. Fibonacci meta has independent
context and confirmation clocks and percentage-based protection:

| Setting | Default | Meaning |
| --- | --- | --- |
| `fib_context_minutes` | 240 (4h) | Bullish background trend required before entry |
| `meta_structure_minutes` | 60 (1h) | Confirmed Fibonacci swing and structure indicators |
| `fib_confirmation_minutes` | 15 | Entry clock; choices 5m, 15m, 30m and 1h |
| `fib_max_hold_seconds` | 28800 (8h) | Configurable 60–1209600-second maximum hold |
| `fib_stop_pct` | 0, unset | Required gross entry-price decline percentage, greater than zero and less than 100 |
| `fib_target_pct` | 0, unset | Required gross entry-price advance percentage, greater than zero and at most 1000000 |

Save a context >= structure >= confirmation profile; higher durations must be
integer multiples of confirmation. Selecting Fibonacci with unset percentages
is allowed, but new-position Start/finite Demo preview is blocked until both are
positive. No stop/target percentages are assumed. Changing the chart has no effect.
The [three-timeframe specification](fibonacci-three-timeframe-proposal.md) records
the signal rules and percentage definitions.

All clocks use completed, UTC-aligned candles; missing bars are not padded. Each
clock needs 50 observed bars, and daily VWAP needs 20 confirmation-session bars.
Fibonacci bootstrap seeds supported native higher clocks only with candles known
before its first replayed confirmation. Default 1h/4h clocks can warm from those
native reads; 6h/12h clocks still need accumulation beyond the initial history.
Checkpoints survive restart. No archive download, alternate venue or forming-bar
fallback is added.

The rules retain EMA20/50, ATR14, confirmed two-right-bar pivots, causal parent
impulses, original arming expiries and immediate Fibonacci breakout confirmation.
Fibonacci requires context EMA20 > EMA50 and completed EMA50 slope / ATR14 > 0.03.
Unknown, mixed or falling context cancels pending arms and blocks new longs, not
protection. Context support/resistance is informational; it does not add an exit.
Regression fixtures preserve legacy research geometry and checkpoint recovery.
They do not financially validate the new profile or other selectable parameters.

Before an entry, costs must leave positive modeled target reward. Planned stop
risk is at most 1% of the smaller of initial capital and current equity, capped
at 10 units of the selected quote currency. Order/exposure ceilings are 1,000
quote units or the existing smaller limits. Kraken paper uses quote-denominated
fees; OKX planning conservatively reserves the worse base/quote entry-fee case.
Dust remains owned and risk-reserved. No currency parity or attached native stop
is assumed.

For Fibonacci, freeze configured percentages and a provisional limit-price plan
before entry. Once terminal entry fills are reconciled, seal stop = entry VWAP ×
(1 − stop % / 100) and target = entry VWAP × (1 + target % / 100), excluding fees
from that VWAP. The deadline is first submission time plus the saved holding
duration; partial fills and restart never renew it. Only Stop, Target or Deadline
can trigger an automated position exit. Trend reversal and session end do not.

The first exit trigger stays latched through partial fills and restarts. Existing
protection is checked before fetching entry history. Signals are consumed before
order I/O; startup, recovery, stale history and missed first-minute entries do
not produce catch-up orders. Venue permission, fees, order count, price bounds
and existing settlement safeguards still apply. Existing pre-upgrade owned plans
keep their original stop, target and deadline, including a former fourteen-day
Fibonacci deadline; they are not silently converted to percentage protection.
Frame changes are blocked while inventory is owned. Percentage/holding edits
apply only to future positions.

The marked entry-loss latch uses the smaller of the configured daily-loss limit
and 12.50 quote units. It blocks entries, not protective reductions, and survives
Stop, restart and UTC rollover. Kraken's explicit paper reset clears its meta
state. OKX has no implicit balance or latch reset. Unrelated inventory cannot be
adopted; retained ownership prevents switching strategies, markets or frames.

For OKX Demo, save the market, Fibonacci profile and explicit percentages, then
use the existing finite strategy preview. It includes the saved meta terms;
review those terms, native-currency limits, fees, duration and order count before
confirming. Selection, Save and preview do not start trading. The exact one-use
confirmation authorizes and starts only that finite Demo run.

Protection only operates while running with fresh execution data and valid
permission. An OKX authorization can end before a position's saved holding
maximum; expiration does not liquidate holdings or renew permission. Gaps,
illiquidity, minimums, fees and partial fills can exceed planned risk. Overruns
are reported, not clipped. The completed research did not establish a robust
tradable edge, and these additions do not authorize deployment or promotion.
