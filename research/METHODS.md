# Existing-method archive study

This study tests the existing spot strategy code rather than another momentum proxy. [METHODS_PLAN.json](METHODS_PLAN.json) freezes the source, calendar selection, execution assumptions and outputs before historical method outcomes are examined.

## What runs

The offline adapter calls the production `Engine.tick`, `htf.run`, `scalping.run` and `programs.run` paths. Order validation, limits, fee reserves, cumulative-fill accounting, position ownership, fresh-entry state, residual handling, stops, targets, holding deadlines and daily-loss behavior remain in those functions. No production files are changed.

- **HTF pullback:** production rolling-hour signal and risk logic, with an explicit eligible-entry permission ablation instead of unavailable Jev decisions. This is not the complete deployed HTF/Jev strategy. Internal confidence of one is an authorization fixture, not an estimated probability.
- **Bollinger scalp:** production one-minute band re-entry, range-efficiency and net-target-room gates, sizing, stop, deadline and cooldown.
- **DCA:** seven daily $10 slots under the shared funding and exposure checks.
- **TWAP:** an $80-derived parent, 12 slices over one hour, with its initial limit fixed before later prices.
- **Rebalance:** one asset at 16% and cash at 84%, using the existing 5-percentage-point band, $10 minimum trade, $100 daily turnover and hourly cooldown.
- **Passive reference:** existing DCA code makes one bounded $80 purchase. Remaining cash and any unfilled amount are retained. Cash is also a zero-return reference.

The schedules and allocations are registered research configurations, not a claim about private runtime settings. DCA/TWAP/rebalancing are execution or allocation programs, not forecasts whose activity proves alpha. Each asset/session starts with hypothetical $500, $100 order/exposure caps and a $12.50 daily-loss limit. HTF retains its 80% target and 100% trim trigger. No session automatically restarts after a halt; retained holdings continue to be marked rather than liquidated.

## Data and execution limits

For each asset, use the first seven UTC days of every quarter in 2019–2025, with 30 hours of prior minute warm-up. These 28 fixed sessions span different years but are **not continuous seven-year performance**. The calendar sample misses unselected events and can have seasonal bias. Sessions are independent flat-start diagnostics, not automatic quarterly resets of a real account. None is a new confirmatory holdout; 2026 is excluded.

The existing Kraken archive supplies native minute OHLCVT. Missing minutes remain missing. Archive VWAP is unavailable and remains zero; it is not fabricated from closes. A streaming rolling OHLCV adapter is regression-tested against production `rolling_rows` without its live-feed persistence/VWAP requirements. The strategy functions do not use VWAP. A gap invalidates the rolling history, never the held position or its protection. Scalping's own incomplete-input checks remain active.

Every minute is represented by four assumed observations at seconds 1, 20, 40 and 59. Base uses open/low/high/close, 10-bps spread and slippage, and capacity capped at 1% of the immediately preceding adjacent published minute's volume. The joint sensitivity uses open/high/low/close, 20-bps spread, 30-bps slippage and 0.5% capacity. Neither path is known from OHLC; changing several assumptions jointly prevents attributing differences to one cause. Neither scenario is a mathematical performance bound.

IOC fills pay the adverse limit and share the minute's capacity, so partial fills remain possible. Passive fills require a strict crossing at a later assumed path observation before expiry; touches never fill. Production tick ordering cancels surviving orders afterward. These are **hypothetical fills, not reconstructed trade prints or queue evidence**. HTF also runs a no-passive-fill control under base costs. The study does not assume that Alpaca offers post-only guarantees.

All fills use the 25-bps-per-side Alpaca planning reserve, not authenticated historical fees. The local executor debits quote fees rather than reconstructing Alpaca fee-asset activities. Rounding/minima use the previously retained current Kraken catalog snapshot as fixed scenario rules, not invented minima or historical Alpaca rules. These qualifications prevent interpreting the study as verified broker execution.

The adapter changes only external market/model inputs, passive settlement assumptions and research logging/UI output. It never opens an exchange session. Per-worker stores are in-memory; raw data and all outcomes go to the separate Kraken research registry. Decision reason counts, first examples and a compact trace digest replace repeated UI events; orders and fill accounting remain intact.

## Scope of conclusions

Report every window, scenario, failed attempt, incomplete input, order, retained holding and halt. Compare session returns, fees, drawdown and inactivity with cash and the bounded passive reference. Policy-evaluation counts are repeated checks, not independent opportunities. No parameter search, fitting, model calls, PBO/DSR certification or automatic strategy promotion occurs.

Market making and triangular arbitrage require synchronous books, depth, queues and missing model decisions; their profitability is not testable from these candles. Spot candles also cannot test margin/Futures funding or equity sessions. The report maps measured weaknesses to all nine papers in [SOURCES.md](SOURCES.md), distinguishing plausible improvements from demonstrated ones. Any changed strategy needs a separately frozen comparison, not retrospective tuning or an automatic deployment.

```sh
python -m research.test_methods \
  --root /home/kyau/.local/share/kairos-research/kraken-btc-eth-001
```

The run retains 728 registered sessions: two assets × 28 windows × six methods × two scenarios, plus 56 HTF no-fill controls. It uses four offline worker processes. Passing software tests or producing profits in an assumed path does not pass the unresolved Alpaca execution-validity gate or authorize account access or paper activation.
