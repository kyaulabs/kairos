# Configurable three-timeframe Fibonacci meta

Implementation specification, not financial validation or permission to trade.
The [meta guide](vwap-fibonacci-metas.md) describes the controls and operating
limits. This extends Fibonacci meta with configurable clocks and percentage
protection; VWAP keeps its existing entry/exit rules and HTF and frozen historical
research profiles are unchanged. It remains deterministic, long-only cash spot
for finite OKX Demo and Kraken Dry-run; no Jev, shorts, margin or live eligibility
is added. Existing owned plans retain their original protection.

## Configuration

The requested starting profile is a set of editable values, not hardcoded clocks.

| Setting | Starting value | Meaning |
| --- | --- | --- |
| Context timeframe | 4h | Broader trend gate and higher-frame support/resistance |
| Fibonacci structure timeframe | 1h | Swing endpoints, Fibonacci areas and structure indicators |
| Entry confirmation timeframe | 15m | Completed-bar pattern confirmation, session VWAP and volume observations |
| Maximum holding period | 8h | Deadline fixed when the entry attempt is submitted |
| Stop-loss percentage | Required; not selected | Gross price decline from the confirmed entry price |
| Take-profit percentage | Required; not selected | Gross price advance from the confirmed entry price |

Selecting a different chart interval must not change any strategy setting.
Supported choices must use native candles or complete UTC-aligned aggregation;
unsupported combinations are rejected, never rounded or silently substituted.
Require context >= structure >= confirmation, with each higher duration an
integer multiple of the confirmation duration. Example: 4h/1h/5m is a different
profile from 4h/1h/15m and needs its own verification.

The holding duration is 60–1209600 seconds, with the existing fourteen-day upper
bound; eight hours is the default, not a fixed limit. Require finite percentages
with 0 < stop-loss % < 100 and 0 < take-profit % <= 1000000. Missing percentages
prevent arming. No percentage defaults or profitable parameter values are assumed.

Changes apply to future entries. Frame changes remain blocked while inventory is
owned, including dust. Changing the holding period or percentages must not alter
an existing position's saved protection. Ordinary stop/restart, UTC rollover and
partial fills must not renew a deadline or reset the loss latch.

## Signal rules

Only completed candles may affect a decision. Update every clock before evaluating
the confirmation bar that closes at that instant. Require fifty observed bars on
all three clocks, positive ATR and current, contiguous higher-frame evidence.
Missing candles are unknown, not flat bars. Initial native 1h/4h history may seed
its clock only from bars closed before the first replayed confirmation, on a
shared UTC boundary. This warms the default profile without importing future
higher states into earlier confirmations. Clocks without a supported native
bootstrap, including 6h/12h, accumulate complete groups instead. Show any remaining
readiness deficit rather than invent observations or place catch-up entries.

Use the existing EMA20/EMA50 and ATR14 arithmetic. ATR14 is the mean of the last
fourteen true ranges. The new context gate reuses the existing rising-regime rule:

```text
EMA20(context) > EMA50(context)
and
(EMA50(context, latest) - EMA50(context, previous)) / ATR14(context) > 0.03
```

Mixed, falling, unavailable or insufficiently warmed context blocks new longs.
Check the gate at arming, confirmation and final entry admission. Losing the gate
cancels unfilled entry arms; it does not renew their expiry. All child signal
engines keep advancing. A later bearish context does not close an owned position.

Report the latest confirmed context pivot low and high as support/resistance.
Use strict two-left/two-right-bar pivots, known only after the right bars close.
These levels are context information, not another distance filter, a target clamp
or an additional exit reason.

On the structure clock, retain the current causal low-to-high parent impulse.
Both endpoints require confirmed pivots, and the span must be at least 1.5 times
structure ATR at qualification. Retain the original ninety-six-structure-bar
expiry; nested swings must not extend it. A structure gap discards the lineage;
a close below the parent low invalidates it. No retrospective choice of the
best-looking swing or short entry from a declining leg is added.

Keep all four families in their current priority order:

| Priority | Family | Pattern qualification and confirmation |
| --- | --- | --- |
| 1 | Confluence | Overlap the 38.2–61.8% retracement area with session VWAP confluence. Confirm a bullish close above VWAP and a higher-low reaction or reaction-high break. |
| 2 | EMA pullback | Overlap the retracement area with structure EMA20 above EMA50 and nonfalling EMA50. Confirm a bullish reaction-high break while those structure conditions still hold. |
| 3 | Extension | Confirm a completed-bar break above the parent high, with volume at least 1.2 times the preceding twenty confirmation bars' mean. Preserve immediate breakout confirmation. |
| 4 | Sweep | Sweep below the parent low and make a bullish close back above it. Confirm a bullish reclaim with a higher-low reaction or reaction-high break. |

The extension family retains its breakout signal, not its former 1.618 profit
objective. All four families use the configured percentage exits below.

A higher-low reaction means a low above the arming bar's low and a close above the
preceding confirmation close. Reaction-high tracking retains the existing rules.
Confluence keeps its 0.25-structure-ATR VWAP tolerance and twenty-observed-bar UTC
session minimum. Session VWAP uses HLC3 weighted by volume on the selected
confirmation clock. Bar-count settings remain bar counts when that clock changes.

Keep the existing three-confirmation-bar pending lifetime and family-specific
pre-entry invalidation/session rules. Structural and ATR levels may invalidate a
pending setup, but they must not become position stop-loss or take-profit prices.
A touch alone never qualifies an entry. Advance all families, select one
fixed-priority winner and do not fall through to another family after a rejected plan.
Keep the first-execution-minute admission window; do not backdate missed entries.

## Percentage protection and exits

Let P be the gross volume-weighted fill price of the single entry IOC, excluding
fees. For the saved percentages s and t:

```text
stop-loss trigger   = P * (1 - s / 100)
take-profit trigger = P * (1 + t / 100)
```

These are price percentages, not net returns or percentages of account equity.
Fibonacci levels and ATR must not remain alternate post-entry exit prices.

Before submission, freeze the percentages and persist a provisional percentage
plan using the maximum buy limit. Reserve its worst-case planned risk, including
fees, slippage and dust. After terminal native fill evidence is reconciled, seal
the prices against the confirmed entry VWAP. An unresolved entry never permits a
duplicate buy. Require fill prices and ownership lineage to match the original
IOC limits. Finalizing fill evidence must not clear an already latched exit.
After prices are sealed, do not widen the stop or stretch the target.

Persist deadline = first entry-submission time + configured holding duration
before order I/O. Use UTC wall-clock time, including time paused or offline.
A later fill, partial exit, restart or settings edit cannot extend it. A zero-fill
terminal IOC creates no owned position.

There are exactly three automated position-exit reasons:

1. **Stop:** the fresh execution-book midpoint reaches or falls below the saved stop.
2. **Target:** that midpoint reaches or exceeds the saved target.
3. **Deadline:** the saved time limit expires.

Keep the current tie priority: Stop, then Deadline, then Target. Persist the first
observed reason before exit I/O and retain it through partial fills and restart.
Do not add context reversal, an opposing signal, session end or a UTC daily reset
as a fourth exit reason. Entry-loss halts block entries, not protective reductions.

Check existing protection before fetching entry history. Missing higher-frame
history must not suppress these checks when fresh execution prices are available.
Exit through existing cash IOC, exit-only ownership checks. A trigger is not a
promise of execution at that price or instant. Bot-managed protection requires a
running worker, valid finite permission, fresh prices and executable liquidity.
Permission expiry does not renew authority or force liquidation. Outages, partial
fills and below-minimum dust can leave owned inventory beyond the intended time
limit. Keep it marked and risk-reserved; never invent a completed sale or attached
native protection.

Retain at most 1% planned stop risk on the smaller of initial equity, current
equity and 1,000 quote units, capped at ten quote units including modeled costs
and residual risk. The stop-distance percentage is separate from that budget.
Keep existing order/exposure ceilings, positive cost-adjusted target reward and
the persistent entry-loss latch. Skip an uneconomic or undersized trade; never
stretch its target or increase risk to make it pass. Realized losses can exceed
planned risk and must be reported without clipping.

Implementation verification must cover selectable clocks, missing/forming bars,
causal pivot timestamps, context gating, percentage fill-price binding, fee cases,
partial/zero fills, immutable protection and deadlines, all three exit reasons,
restart, ownership isolation and unchanged legacy research profiles. Unit tests
are not financial validation. Any historical study needs fresh, bounded approval;
existing research allowances remain closed. Saving these settings never selects,
arms or starts the strategy on another instance.
