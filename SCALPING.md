# Bollinger range scalping

Bollinger range scalping is a paper-only, deterministic strategy for spot longs and qualified USD linear Futures longs/shorts. It makes no Jev calls. Existing model-assisted strategies keep Jev. Live scalping is rejected by the server even when both exchange write gates are enabled.

Start with a flat selected paper portfolio. Select **Bollinger range scalping · paper**, choose the bot market and product, and save explicit capital and risk limits. Keep the engine interval between 10 and 30 seconds. Chart market, interval, zoom and panning never change these execution inputs. Account-specific Kraken trading fees must be available, including in paper mode.

| Setting | Default | Allowed |
|---|---:|---:|
| Rolling window | 30 completed 1-minute candles | 20–120 |
| Band width | 2 standard deviations | 1–3 |
| Maximum trend efficiency | 0.35 | 0.05–0.8 |
| Additional net-cost margin | 10 bps | 1–1,000 bps |
| Price stop | 50 bps | 5–500 bps |
| Maximum holding time | 300 seconds | 60–1,800 seconds |
| Post-exit cooldown | 60 seconds | 0–3,600 seconds |

The bands use the arithmetic mean and population standard deviation of closing prices in the rolling window. One additional completed candle supplies the preceding window for re-entry confirmation. Missing minutes, an unavailable latest completed candle, invalid OHLC values and inadequate history block execution; the strategy does not forward-fill gaps.

A long candidate requires the previous close below its lower band and the latest close back inside the current band, below the midpoint. Futures shorts use the mirrored upper-band condition. Spot never opens shorts. The efficiency filter divides the window's absolute endpoint movement by the sum of its absolute close-to-close movements. High efficiency indicates directional movement; flat windows and windows exceeding the configured threshold are rejected. This filter cannot reliably identify every trend or prevent losses.

An entry also needs a fresh book inside the current range and sufficient distance to the midpoint. The cost filter projects adverse entry/exit prices using the current spread and configured slippage, rounds against the trade to native ticks, and charges the account's taker fee on each distinct notional. Projected net room must exceed the additional safety margin. Panel net-room figures also subtract that margin; they are not expected profit. Future spreads, fills, fee changes and funding are uncertain; this is an estimate, not a profit forecast. Every entry is a bounded paper IOC, not a maker quote. Each completed candle is claimed before submission, including attempts that fill partially or not at all.

The midpoint target, stop derived from the entry limit, and absolute holding deadline are saved before the order. They do not move with later bands or chart navigation. Changing scalping parameters while paused affects subsequent entries, not the saved protection plan. Only one position is permitted, with no scaling in or implicit reversal. Tagged confirmed fills establish ownership; unrelated holdings are not adopted.

While running, each engine check considers the fixed target, price stop, holding deadline and loss/exposure limits. A triggered exit is latched and attempts one bounded reduction per check until flat. It does not wait for a profitable exit or a new candle. Futures reductions remain reduce-only and may proceed after a daily-loss halt. The cooldown starts after the position is flat.

**Stop pauses these checks; it does not close positions.** Restart remains paused and retains the plan and deadline, including elapsed downtime. Start resumes management before considering another entry. Close or reset the paper position before switching strategy or market. One-time capital recovery cannot be combined with scalping. Paper reset clears the selected portfolio's plan without changing live portfolios.

These are local simulation triggers, not exchange-held stop orders or guaranteed maximum losses. Price stops are not fee-inclusive loss caps. Spread/freshness checks, missing fees, order caps, exchange minimums, thin depth and funding or maintenance failures can prevent an exit or leave a residual position. Inspect the error and portfolio; paper-only dust may require an explicit paper reset. No transfers, conversions, automatic funding or live orders are introduced.

The assessment panel shows closing prices against the latest window's fixed band levels, not historical rolling-band curves. While holding, it labels the saved entry window and entry cost estimate separately from the current protection plan. Jev's other strategies show model weights, buy-minus-sell bias, recent assessments and historical-move/cost context. None of those displays measures the probability of profit or proves that an order filled.

Regression tests use mocked exchange/model clients. They verify rules, accounting, failure handling and interface behavior, not profitability. No historical performance study, real account fee probe, paid inference or live scalping experiment has been performed.
