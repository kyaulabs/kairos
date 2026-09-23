# Kairos

Kairos runs Jev-assisted trading strategies against Kraken market data. The workspace fits the viewport: chart on the left, Jev assessment beside it, settings on the right, and bottom tabs for Orders, Activity, Portfolio, Exchange accounts, and Equity. Long content scrolls inside panels. Smaller screens use panel navigation instead of columns. Settings have Strategy, Capital, and Execution tabs; switching tabs preserves unsaved edits. The header's sun/moon toggle and accent dropdown select browser-local dark/light and violet/green/red appearance choices from the [brand pack](BRANDING.md), including while trading runs. The layout and Neo Sans Pro / OperatorMonoLig fonts are unchanged. Live fills are detected during order reconciliation, not through a private execution stream.

Product is the single execution-product selector under Strategy. Capital contains paper/live allocations, risk caps, reinvestment and product-specific leverage. Execution displays read-only Kraken account trading fees and contains spread, slippage and freshness limits. DCA/TWAP controls appear with their strategy; model and margin controls appear only where applicable. Unsupported strategies remain labeled, not silently substituted. A checked spot-only recovery switch stays visible until explicitly turned off before switching products. Hidden fields keep their saved values; an inactive draft is not submitted while saving another product or strategy.

The bot-settings contract lives in `kairos/settings.py`. The read-only `/api/settings-schema` endpoint supplies the browser's types, bounds and choices; it contains no credentials or live permissions. Legacy manual `maker_fee_bps` and `taker_fee_bps` settings are discarded on load. Other settings, portfolios, program identities and existing orders' fee snapshots are retained. See the [refactor analysis](REFACTOR-ANALYSIS.md) for duplicate controls removed and safeguards deliberately kept separate. Deploy the backend and static files together; the form stays unavailable if its contract cannot load.

Paper rows in **Orders** have right-aligned archive and trash icons. Archive hides a completed paper order and its linked fill/order events from the default dashboard while retaining the records used for accounting. **Show archived** reveals archived rows; their restore icon returns them to the default view. Archiving/restoring completed paper records is allowed while running. Live rows have no history actions, and unfinished paper rows cannot be changed. The table retains its bounded history: up to 100 visible and 100 archived orders.

Permanent deletion requires confirmation, a paused engine in Dry-run, settled paper orders and a flat paper portfolio for that row's product. Saved scalp plans, saved strategy runs and today's rebalance turnover can still require a record; a disabled trash icon explains the restriction. Archive these records instead, or explicitly reset/rearm the relevant paper state where appropriate. Deletion removes the order and its linked paper fill/order events from the active database, not backups or old disk pages. Balances, fees, P&L, independent assessments and live history remain unchanged. This is history management, not a portfolio reset or secure disk erasure. Other open dashboards refresh through the state stream; deletion cannot be undone through the UI. Deploy backend and static files together, then safely restart and reload to enable the controls.

The price chart displays native Kraken OHLC candles, defaulting to 1m bars. The chart selector offers 1m, 5m, 15m, 30m, 1h, 4h, and 1d intervals; it does not change the HTF strategy interval. Snapshots refresh roughly every five seconds, including the forming candle, and are shared briefly across browser tabs. Trading still uses only completed candles. The initial view spans 60 candle periods; pan/zoom can inspect up to 720 available bars. Follow live returns to the latest view. Wicks show high/low, bodies show open/close, and the crosshair lists all four prices and volume in base-asset, token, or contract units. Matching volume bars share the candle timeline. The current-price badge and dashed line follow the candle color; there is no candle dot. Portfolio equity remains a line chart in the Equity tab.

Click the market beside the logo to open the searchable retail-market picker. Filter crypto spot, margin-eligible crypto, FX, xStocks, or Futures. Public listings and margin metadata do not establish account eligibility. Selection changes only the chart, never bot settings or execution. You can browse while the bot runs. The Jev panel always names the configured bot market and separately labels its latest assessment market. Click the bot-market label to return to that chart. To change what the bot trades, stop it and search the custom Bot market dropdown under Settings > Strategy. It shows the same full catalog as the chart picker, including pairs quoted in other currencies. Unsupported quote currencies, margin/leverage combinations, xStocks, and unsupported Futures remain visible with reasons. Choose the Futures product for a qualified USD linear crypto perpetual; its USD collateral ledger is separate from spot. These are Kairos execution restrictions, not account-access determinations. Use arrow keys and Enter to choose, or Escape to discard a search. Selection changes a draft only; Save settings applies it. Changing products/leverage updates the limitations without discarding other draft fields. The portfolio's accounting currency remains fixed, and the backend still rejects unsupported configurations.

The picker starts with Market sorted A–Z. Click Market to reverse it; select Last price, 24h change, or 24h volume to sort highest-first, then click again for lowest-first. Sorting survives search, favorite filtering, and price refreshes for the current page session. Unavailable values stay at the bottom. Volume uses each market's native asset or contract units, not a common USD notional. Comparing volumes across products does not compare turnover.

Currency icons appear beside the selected chart pair, picker rows, and footer favorites. Missing artwork uses ticker-letter badges.

Stars add or remove footer favorites. They persist across reloads in this browser's local storage and synchronize between tabs on the same origin; clearing site data removes them. They do not synchronize to other browser profiles or devices. Footer prices update independently of the selected chart. An unavailable market shows no invented price. If storage is blocked, the picker warns that favorites last only for the current page session.

Picker and footer prices are last trades from a public Kraken ticker snapshot refreshed about every ten seconds, with a shared server cache. The volume column covers the last 24 hours. Spot/xStocks rolling 24h change comes directly from WebSocket v2 `change_pct`, collected in batches of at most 100 pairs with a 12-second total budget and cached for 60 seconds across all browser tabs. It has its own refresh timestamp and is marked stale after 90 seconds. Missing values display as —, not zero; partial or failed change snapshots do not remove REST quote prices. Futures use the Derivatives ticker's rolling `change24h`, refreshed with its quote. Failed venue refreshes retain old quotes and timestamps without hiding healthy venues. A cold refresh can take longer while catalogs and changes load. REST's midnight-UTC opening price is not used as a 24-hour baseline. The header is a bid/ask midpoint from the newest available snapshot or WebSocket quote, not the candle close. Prices older than 30 seconds are marked stale. Chart snapshots and browsing quotes are not execution inputs; execution uses separate public book and candle streams with REST bootstrap/recovery.

Spot-family execution subscribes to Kraken's free public [WebSocket v2 `book` channel](https://docs.kraken.com/api/docs/websocket-v2/book/) at 100 levels per side. Its scope follows saved bot markets, including triangle/basket legs and capital-recovery holdings, not chart navigation or favorites. [OHLC streaming](https://docs.kraken.com/api/docs/websocket-v2/ohlc/) uses fixed 1m candles for Bollinger or the saved HTF interval. Market-making and scheduled strategies need no candle subscription. No WebSocket key permission or authentication token is needed. Futures keeps its separate REST adapter; private orders, balances and account fees still use REST.

Book messages are decoded as decimals and checked against [Kraken's CRC32 of the top ten levels](https://docs.kraken.com/api/docs/guides/spot-ws-book-v2/) on every snapshot/update. Deletions, repeated price updates and depth truncation are applied before checking the checksum. Crossed, malformed, out-of-order and stale books are rejected. Cache reads retain the original observation time and use the stricter of ten seconds or the operator's freshness limit. Disconnects and invalid messages revoke already-issued stream books, so an interrupted pre-submission plan must be replanned. Reconnection waits 5–30 seconds and starts with new snapshots; deltas are never merged across connections. If a stream book is unavailable, execution requests fresh, validated REST depth instead. Failed REST reads do not revive old data.

Candle history starts with REST's completed rows, excluding its final uncommitted row. Stream updates replace the current bucket; a later bucket confirms completion. The clock alone never turns a cached forming candle into an entry signal. Missing boundaries trigger REST recovery, and missing or stale candles in the required strategy window block execution rather than being filled in. Healthy streaming avoids repeated execution-depth and candle-history requests, but valuation, fees and chart browsing retain their REST reads. Execution shows read-only stream health and the last candle-read source at the last engine check. Engine cadence is unchanged: streaming does not run protection checks while paused or on every price update. Deploy backend/static changes together and activate through a safe operator-controlled restart.

HOLD means no new trade. With no position in the assessed market, the display calls it WAIT. Confidence is confidence in that assessment, not a probability of profit. The D3 panel shows buy-minus-sell model bias, the three score weights, recent assessments (not fills), and the HTF historical-move/fee-slippage comparison. It does not fabricate probabilities for deterministic strategies. Changing saved settings clears the current assessment; history remains in Activity.

This is an experimental trading application, not evidence of a profitable strategy. Live spot and qualified linear Futures execution are implemented but have not been verified with real orders. Test it with paper funds before installing a trade-capable key.

## Supported trading

| Product | Dry-run | Trading |
| --- | --- | --- |
| Crypto spot, USD-quoted markets | Real feeds and Jev, simulated fills | Kraken limit orders |
| Crypto margin | Simplified long/short simulation | Blocked |
| Direct US stocks and ETFs | Not implemented | No brokerage integration verified |
| xStocks | Browse-only tokens; no simulator | Read-only account data; execution blocked |
| USD linear crypto perpetuals | Separate long/short simulation with funding and maintenance checks | Explicitly gated, prefunded cross-margin execution |
| Inverse, dated and other Futures | Browse-only | Execution blocked |

Kraken offers direct equities in its US product. No applicable retail brokerage order path was found in the official CLI or public Exchange documentation reviewed; this is not a claim that no partner or institutional equities API exists. The CLI supports xStocks and equity/index futures, neither of which is brokerage share ownership. xStocks remain read-only. Qualified linear crypto perpetuals support HTF, market making, DCA and TWAP through a separate Futures adapter; see [Futures setup, recovery and limitations](FUTURES.md). DEX is not integrated. See the [Kraken CLI review](KRAKEN-CAPABILITIES.md) for product coverage, tested public endpoints, and integration requirements. Do not reuse Kairos's API key with the CLI: their different nonce units can break Kairos authentication. CLI timeout retries also conflict with Kairos's ambiguous-order recovery safeguards.

References:

- [Kraken API overview](https://docs.kraken.com/exchange/api-reference/overview)
- [Kraken Spot REST specification](https://docs.kraken.com/openapi/spot-rest.yaml)
- [Direct stock trading on Kraken Pro](https://support.kraken.com/articles/how-to-trade-stocks-on-kraken-pro)
- [TypeSafe evaluation API](https://docs.typesafe.ai/api)
- [Jev numerical limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
- [Jev Trader reference project](https://github.com/jarrodwatts/jev-trader)

## Exchange account snapshots

Open **Exchange accounts** in the bottom dock, choose a source, and use **Refresh**. These are live exchange reads even when the bot is in Dry-run. They do not import balances into its allocation, manage unrelated orders, or reset paper funds. Tables preserve exchange asset IDs and native units; there is no consolidated equity total. Margin collateral overlaps spot balances and must not be added to them.

Spot-family views use the existing server-side Spot client and nonce sequence. Query Funds covers balances, collateral, and Earn allocations; order/position and trade-history views need the corresponding open/closed order query permissions. Deposit and withdrawal ledger views need Query Ledger Entries. They show recorded transactions, not pending funding status. Never grant withdrawal permission to make a read view work. Kairos's endpoint allowlist rejects transfers, withdrawals, and Earn writes regardless of key permissions.

For Futures account views alone, use separately issued, read-only Derivatives credentials in `KRAKEN_FUTURES_API_KEY` and `KRAKEN_FUTURES_PRIVATE_KEY`. They are not Spot credentials and do not use Spot nonces. Public Futures browsing works without them. Missing credentials, permission errors, and unavailable venues are reported without enabling writes or exposing keys. Authenticated private reads have been tested with fixtures, not a real account; verify your entitlements before relying on these views.

Reads share a 30-second server cache. They run only when opening an unloaded view, changing sources, or pressing Refresh. Successful snapshots show their timestamp; failed refreshes retain old rows with an explicit warning. History is the first returned page, not a complete export, and each table is capped at 1,000 rows. The API reads the account available to the configured key; there is no wallet/subaccount selector. See the [coverage and roadmap](RETAIL-ROADMAP.md) for omitted endpoints and planned strategies.

Deploy backend and static changes together. Restart only when safe for the running bot, then reload the browser. A restart leaves the engine paused in Dry-run; resuming requires an explicit Start. No funds are converted, transferred, withdrawn, or assigned to a strategy by activating this feature.

## Run locally

Requires Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/). Runtime dependencies are `aiohttp` for HTTP/WebSocket serving and clients, and `python-dotenv` for loading the existing environment file. Accounting, persistence, and signing use Python's standard library. The frontend has no build step.

```sh
uv sync --locked
uv run kairos
```

The process loads `.env` without overwriting exported environment variables. Do not copy `.env.example` over an existing `.env`. The server binds only to `127.0.0.1:8000`. Local access is for development; use authenticated nginx for network access.

Text uses locally installed Neo Sans Pro and OperatorMonoLig Nerd Font, with OperatorMonoSSmLig Nerd Font for bold monospace. Install your licensed copies on the computer running the browser, for example under `~/.local/share/fonts/` on Linux, then run `fc-cache -f`. CSS references their local face names; these fonts are not uploaded or bundled in Git. System fonts are the fallback.

Font Awesome Pro icons use two self-hosted webfonts. Install your licensed v7.2.0 files on the server:

```sh
FONT_AWESOME='/path/to/Font Awesome v7.2.0'
install -d kairos/static/fonts
install -m 644 "$FONT_AWESOME/fonts/fontawesome/fa-regular-400.woff2" kairos/static/fonts/
install -m 644 "$FONT_AWESOME/fonts/fontawesome/fa-solid-900.woff2" kairos/static/fonts/
```

The Pro files are ignored by Git and must be supplied separately for each deployment. Only install fonts you are licensed to serve. Without them, buttons keep their text icon fallbacks and accessible labels. Do not publish the licensed binaries in the repository.

The app always starts **paused in Dry-run**, even if the previous process was trading. A browser disconnect does not stop a running engine. A process restart does.

The existing variable names are preserved:

| Variable | Purpose |
| --- | --- |
| `KRAKEN_API_KEY` | Kraken API key |
| `KRAKEN_PRIVATE_KEY` | Kraken base64 signing secret |
| `KRAKEN_FUTURES_API_KEY`, `KRAKEN_FUTURES_PRIVATE_KEY` | Separate Derivatives credentials; order permission only for gated live Futures |
| `JEV_API_KEY` | TypeSafe API key |
| `JEV_MODEL` | Defaults to `jev-latest`; pin a version for comparable experiments |
| `ALLOW_LIVE_TRADING` | Defaults to `false`; only `true` permits exchange writes |
| `ALLOW_FUTURES_TRADING` | Defaults to `false`; Futures require both flags and explicit separate arming |
| `PUBLIC_ORIGIN` | Exact browser origin, including scheme and nonstandard port |
| `PORT` | Loopback listener port; defaults to `8000` |
| `DATA_DIR` | SQLite database and process lock; defaults to `data` |

Only one process may own a data directory. Do not run multiple bot instances against the same Kraken key, share that key with another order manager, or manually spend its allocated balances. Use a dedicated key and preferably a dedicated account/subaccount. External account activity can invalidate the local allocation and order accounting.

## Starting capital and continuous operation

The initial paper profile is $1,000, full-allocation sizing, and reinvestment enabled. Continuous strategies have no trade-count limit, profit target, or scheduled end; DCA and TWAP instead have finite, explicitly configured runs. Available funds are reused across trades, including realized proceeds. The bot can hold cash or inventory when there is no qualifying action; it does not force trades just to stay busy.

To start with $100:

1. Stop the engine.
2. Open Settings > Capital and set **Paper starting balance** to `100`.
3. Set **Order cap** and **Exposure cap** to `100` if you intend full-allocation sizing. Choose **Scale order/exposure caps with equity (reinvest)** explicitly. No shortcut changes these settings together.
4. Set the daily loss limit and review the account-reported trading fees in Execution.
5. Save settings, then **Reset selected paper portfolio** to apply the new starting balance.
6. In Settings > Strategy, choose the bot's market, strategy, and Spot product; save and press Start. The header market picker does not configure trading.

Reinvestment scales both caps by `current active equity / initial allocation`. A $100 starting order cap becomes $150 when equity is $150, or $80 when equity is $80. Sizing reserves fees and rounds to Kraken's lot size; it cannot invest literally every last fractional cent. The daily loss limit is an absolute USD amount and does not scale. Turning reinvestment off makes the caps fixed USD amounts.

Paper defaults are not recommended risk limits for real money. The full-allocation profile can lose the entire allocation. Stale data, invalid responses, loss limits, insufficient margin, or uncertain order outcomes may stop the engine. There is no promise of maximum returns or uninterrupted exchange availability.

Settings and accounting persist in SQLite. Changing the paper starting balance does not alter an existing paper ledger until you reset it. A reset discards that portfolio's simulated holdings and recovery state, but retains the labeled order/event history. It never resets live holdings.

## Recover the original investment

Enable **Recover original allocation once above 2× equity** for a spot portfolio. The check interval is configurable and is evaluated while the engine is running; it cannot run more frequently than an engine cycle.

For an original $100 allocation:

- At exactly $200 active equity, nothing happens.
- Above $200 net active equity, the bot attempts to reserve $100.
- If necessary, it sells bot-owned inventory through bounded IOC limit orders. Minimum sizes, order caps, fees, price bounds, and actual fills still apply. It may take several checks to raise enough cash.
- While recovery is pending, it does not reinvest the cash being raised.
- It reserves only when cash is available and net active equity still exceeds the threshold.
- The $100 is removed from the bot's spendable ledger. The remainder continues compounding.

The reserve is a **local accounting exclusion**, not a separate Kraken wallet or bank transfer. Kraken still holds that cash in the same account. The dashboard labels it as protected reserve, and the bot does not include it in subsequent order sizing. Other account users, applications, or an explicit increase to the bot allocation could still spend account cash.

Recovery happens once per portfolio, persists across restarts, and does not register as a trading loss. Its amount is the original allocation, not a later increase to the allocation. There are no withdrawal or transfer API calls, no withdrawal destination settings, and no need to grant withdrawal permission. You withdraw the reserved amount manually. This feature is not available for the simplified paper-margin portfolio.

## Strategies

**Higher-timeframe trend:** uses completed 1-minute, 5-minute, 15-minute, 30-minute, hourly, four-hour, or daily candles. The default is 15 minutes; shorter intervals increase decision frequency, noise, and potential fee costs. Code calculates 8/21-period averages and historical returns. Jev assesses buy/sell/hold; code enforces trend and cost filters. Each completed candle is assessed at most once. Spot sells only reduce bot-owned inventory. Historical momentum is not a forecast of the next move.

**Bollinger range scalping:** paper-only spot longs and qualified linear Futures longs/shorts, with no Jev calls. Uses completed 1-minute candles, a configurable rolling window, band re-entry, efficiency and exact-notional cost filters. A saved midpoint target, price stop and holding deadline govern one position; stop/time/risk exits do not wait for profitability or a fresh candle. Checks run only while started, not while paused or offline. Read [scalping configuration and limits](SCALPING.md) before use.

**Market making:** asks Jev which side to quote. It places one post-only order, with a fee-aware offset from the midpoint, and reconciles/cancels before replacing it. Quotes expire at Kraken after 30 seconds. The default cycle is 15 seconds; the configurable minimum is 10 seconds. This is rate-limited market making, not exchange-grade HFT. Wide fee-aware quotes may rarely fill, and filled quotes remain exposed to adverse selection.

**Triangular arbitrage:** considers both directions of one BTC/ETH-bridged triangle for the selected USD pair. Code calculates the three legs using visible depth, lot rounding, fees, and bounded worst-case prices. Jev may veto the opportunity, but does not perform the arithmetic. The entire route is rechecked after inference. Orders execute sequentially; the cycle is not atomic. A failed or partial leg stops the engine with its intermediate inventory preserved for review. Not every pair has a suitable triangle, and retail fees may eliminate every observed opportunity.

**DCA:** buys a fixed USD amount including the account-reported taker-fee reserve. Set the purchase amount, count, and period in seconds. The total run budget is amount × count and must already exist in the bot's allocated cash when the run first starts. Defaults are $100 × 10 purchases, one day apart. The first slot is immediate; later slots follow the persisted start time. Each slot attempts one bounded IOC limit order. Unfilled amounts and missed slots are not added to later purchases.

**TWAP:** set buy/sell, total base-asset quantity, a positive parent limit, slice count, and total duration in seconds. Defaults are 0.001 base units, 12 slices, and one hour; the zero default price must be replaced with your explicit limit before saving TWAP. The complete parent needs allocated cash at its worst-case limit plus fees, or bot-owned inventory for a sell. Slices are lot-rounded; only the final slice receives rounding dust. A buy never exceeds the parent limit; a sell never goes below it. Nonmarketable limits skip that slice. Partial or missed slices are not stacked onto later ones, so completion can leave quantity unexecuted. Each slice must span at least one configured engine interval.

**Threshold rebalancing:** enter 1–10 markets and an explicit cash weight, for example `BTC/USD=50,ETH/USD=30,CASH=20`. Weights must total exactly 100; markets must use this database's quote currency and have distinct base assets. The basket is valued from allocated cash and the named bot-owned holdings using fresh books. Other holdings are not rebalanced or included in basket capital, but remain subject to engine-wide exposure and loss limits. The Bot market field becomes an anchor chart, not the basket definition.

Rebalancing acts only when a weight differs from its target by more than the configured percentage-point band. It considers overweight sells before underweight buys and sends at most one settled order per cooldown. Later checks revalue confirmed fills rather than assuming sale proceeds. Defaults are a 5-point band, one-hour cooldown, $10 minimum trade, and $100 UTC-day turnover including fees. The turnover cap counts buys and sells across run IDs, so rearming cannot erase today's trading. It uses the UTC day of order submission. No currency conversion or wallet transfer is made to fund an order.

The descriptions above cover spot programs. Futures DCA/TWAP use contract quantities, notional budgets and reserved margin, with optional reduce-only operation. Follow the separate [Futures instructions](FUTURES.md).

For spot programs:

1. Stop and reconcile orders, select Spot and the strategy, configure its fields and risk limits, then Save settings.
2. Start in paper mode first. All three also support the existing explicitly confirmed live-spot path. Every live order rechecks Kraken fees against the account-rate snapshot used to plan it before submitting.
3. Inspect **Strategy signals** in the existing assessment panel and the Orders/Activity tabs. These programs do not call Jev, require a Jev key, or fabricate confidence values.
4. Stop to pause. Progress is separate for each mode and strategy. After restart, the engine remains paused in Dry-run; starting again resumes the saved schedule, skipping missed slots. Expired/completed finite runs pause without catch-up orders.
5. To change a saved program's parameters or repeat a completed run, stop, save the new settings, and explicitly confirm **New strategy run…**, then Start. This creates a new schedule/budget allowance, not money. It never changes balances or holdings. Unrelated risk-setting changes do not reset progress. Resetting the paper spot portfolio clears its paper program progress, not live runs or history.

Each due slot/check is durably claimed before data requests. A crash between that claim and order intent skips the opportunity rather than risking a duplicate. The intent records its run ID and slot before submission; confirmed cumulative fills are applied once. Unknown submissions/cancellations block execution and rearming until reconciled. A late data response cannot submit beyond its slot deadline. Market-data errors may pause the engine; spread, funds, size, and cap failures are reported without forcing a trade. Existing capital recovery has priority and can leave later slices unfunded. No profit or complete-fill guarantee is implied.

The [TradingAgents review](TRADINGAGENTS-REVIEW.md) identifies possible offline HTF research, not a runtime dependency or new model authority.

Switch the bot's strategy or market by stopping, changing settings, saving, and starting again. Browsing another chart does not change either setting. Existing spot holdings stay in the ledger and count toward portfolio exposure. A new strategy does not automatically manage or liquidate positions in a previously selected market.

## Dry-run and Trading

Dry-run uses real Kraken data. HTF, market making, and arbitrage use real Jev evaluations; Bollinger scalping, DCA, TWAP, and rebalancing are deterministic and make no model calls. Scalping is paper-only. Paper execution never calls `AddOrder`, including `validate=true`, or Futures `sendorder`, and does not submit exchange cancellations for paper orders. Paper trading requires a Spot key with authenticated fee-query access, but no order permissions. Browsing public markets needs no private key.

Paper taker fills walk displayed depth up to the limit. Paper maker fills require a later public trade strictly through the resting limit on the opposing aggressor side, capped at 10% of that trade's volume. These estimates do not reconstruct queue priority, hidden liquidity, promotional rebates outside the returned trading-fee schedule, or your market impact. Simulated results cannot establish live profitability.

For real spot trading:

1. Install a dedicated trade-capable key with balance/fee queries, open/closed order queries, create/modify orders, and cancel/close permissions. Do **not** grant withdrawal permissions.
2. Set `ALLOW_LIVE_TRADING=true` on the server and restart. The engine still starts paused in Dry-run.
3. Configure a positive **Live capital allocation**, order/exposure caps, and daily loss limit. Review the read-only Kraken trading fees in Execution. Caps must fit the live allocation. For full allocation, set both starting caps equal to the live allocation and keep reinvestment enabled.
4. Change **Execution** to **Trading — real funds** and confirm. Kraken balances, status, and fees are checked. A read-only key cannot execute an order; order authorization remains Kraken's responsibility.
5. Press Start if paused. If the engine was already running when you changed modes, it resumes after successful reconciliation and preflight.

The switch uses the same engine, not a mock live implementation. Directional/arbitrage orders are IOC limits; maker orders are post-only GTD limits. Every intent is saved before submission with a unique client order ID. Accepted orders are not counted as fills. Cumulative exchange execution quantities, costs, and fees drive accounting.

Changing back to Dry-run stops the loop and reconciles tracked orders first. If cancellation or submission status is uncertain, the switch fails closed and retains its previous mode, paused. No blind write retries occur. Use Reconcile and inspect the client ID at Kraken; a missing order after an ambiguous submission is not proof that it was never accepted.

Live allocation tracks only funds assigned to this bot and assets bought by it. Pre-existing account crypto is not imported or sold. Fee preference is quote currency (`fciq`); the application records Kraken's quote-denominated cumulative fees. Balance discrepancies stop execution rather than silently inventing funds.

Trading fees come from authenticated Kraken `TradeVolume` responses for each saved bot market, not public base-tier examples or operator-entered rates. The same source drives paper fills, Jev's cost context, sizing, program budgets and per-leg arbitrage/rebalance calculations. Futures queries specify the derivatives asset class and require a Spot key for the same account as the Futures wallet. Chart browsing never changes the queried bot markets.

Fee snapshots are memory-only and valid for less than 60 seconds. Startup, Save and engine checks refresh successful snapshots from 30 seconds old, leaving time for price reads and order preparation before expiry; Start refreshes them explicitly. Paused engine checks also refresh rates without placing orders. Failed reads retain a 60-second retry interval, invalidate cached rates for trading, and retain the previous rates visibly marked stale. Missing permissions, incomplete/malformed data or expired snapshots block new orders in both modes; there is no fixed-tier fallback. Setup and public browsing remain available.

Live submission rechecks the planned rate. An increase stops the order for replanning rather than silently spending more; Futures openings also recheck their closing-fee reserve. Existing order snapshots remain fixed for paper fills and recovery. Confirmed live fills use Kraken's reported fees. Negative maker rates are displayed and simulated as rebates, but reserves never assume an uncredited rebate is spendable cash. If Kraken returns a single fee schedule rather than separate maker/taker rates, that schedule applies to both sides.

Displayed percentages are per side, not round-trip costs. They exclude spread, slippage, funding and margin borrowing. Paper margin opening/rollover assumptions and the additional estimated Futures liquidation charge remain simulation assumptions, not verified account charges. Real account fee responses have not been probed during development.

## Margin simulation

Margin has a separate paper portfolio. It supports long and short positions on markets advertising the selected 2×–5× leverage. Opposite-side fills reduce an existing position before a reversal can occur. There is no live margin path and no leverage field in live spot requests.

The simulation tracks collateral cash, signed positions, average entry, initial margin, fees, unrealized P&L, and a configurable maintenance threshold. Rollover charges accrue proportionally over elapsed time using a configurable four-hour rate, including time spent paused. Maintenance monitoring continues while the margin product is selected, even when the trading strategy is paused. Below the threshold it attempts simulated liquidation against available depth and stops; a partial liquidation can leave remaining positions.

This is a simplified cross-collateral research model. It does not reproduce Kraken's collateral haircuts, asset-specific borrowing schedules, eligibility rules, liquidation engine, or jurisdictional restrictions. It is not evidence that your US account is eligible for live margin. Close positions through the strategy or reset the paper margin portfolio before changing its market, product, leverage, or funding assumptions.

## Risk and recovery

Code, not Jev, controls order sizes, inventory, exposure, fees, minimum orders, staleness, and daily loss. The UI cannot disable the server's live-write gate or enable live margin. Funding/withdrawal endpoints are not allowed by the client.

The daily loss measure is the change in marked-to-market equity since the first valuation of the UTC day, with explicit capital allocation/recovery changes excluded. It includes unrealized P&L and trading fees, but not external Jev API charges. This is not an exchange-held stop-loss: a gap, disconnection, or existing position can exceed the limit. Stop and loss-limit actions cancel tracked orders but **do not liquidate spot holdings**.

GTD expiry limits how long maker orders can remain after a process failure. There is no account-wide cancel-all or dead-man switch that could cancel your unrelated orders. On restart, unresolved live intents block trading until reconciled. Interrupted arbitrage cycles also require explicit acknowledgement after you inspect remaining holdings.

Back up the data directory while the process is stopped, including SQLite WAL files if present. Losing the database loses the bot's allocation, recovery state, and order reconciliation history. Do not start a replacement database against a live account with unresolved bot activity.

## Nginx and systemd

`deploy/nginx.conf` protects the entire site, API, SSE stream, and static assets with HTTP Basic Auth over TLS. The application adds Origin/CSRF checks for changes, a same-origin content security policy, and no-store response headers. Basic Auth has no per-user roles; every authenticated user can control trading.

Example password-file creation:

```sh
sudo htpasswd -cB /etc/nginx/kairos.htpasswd your-user
```

Use `-c` only for the first user; it replaces the file. Ensure nginx can read it and other users cannot. Replace the example hostname and certificate paths in the nginx configuration, then set the application's `PUBLIC_ORIGIN=https://your-hostname` to the exact browser origin. Keep port 8000 inaccessible from the network. No nginx location should bypass authentication.

```sh
sudo nginx -t
sudo systemctl reload nginx
```

For a persistent service, install the project and its locked virtual environment under `/srv/kairos`, create an unprivileged `kairos` user, give it ownership of `data`, and restrict `.env` to that user. `deploy/kairos.service` expects those paths and the existing virtual environment. Do not run multiple workers or instances for the same account.

```sh
sudo install -o kairos -g kairos -m 700 -d /srv/kairos/data
sudo cp deploy/kairos.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kairos
```

After a service restart you must explicitly start the engine again. The example configuration must be adapted and tested on the deployment host; it does not provision DNS, TLS certificates, nginx, or users.

## Verification

Enable the versioned Git hooks once per clone. Install Gitleaks separately and ensure both tools are on `PATH`:

```sh
npm install -g @commitlint/cli @commitlint/config-conventional
git config --local core.hooksPath .github/hooks
```

The hooks are executable in Git. Pre-commit scans the index with Gitleaks and redacts findings; commit-msg validates the supplied message with the repository's commitlint configuration. Missing tools fail the commit rather than skipping checks. Commits must be GPG-signed, have detailed conventional messages, and stay on feature branches. Commit and push each coherent, verified change as it completes.

Run the application checks:

```sh
uv sync --locked --dev
uv run ruff check kairos tests
uv run python -m unittest discover -v
node --check kairos/static/chart.js
node --test tests/*.test.cjs
node --check kairos/static/app.js
node --check kairos/static/settings.js
node --check kairos/static/markets.js
node --check kairos/static/strategy-market.js
node --check kairos/static/accounts.js
node --check kairos/static/appearance.js
```

Stop the app before the bounded integration check so authenticated nonces remain ordered:

```sh
uv run kairos-smoke --jev
```

This checks public depth, completed candles, a WebSocket tick, authenticated balances/fees, and one real Jev evaluation. The Jev call can incur normal API charges. No order, validation-order, cancellation, or funding call is made. It prints pass/fail summaries, not credentials, balances, or response bodies.

D3 7.9.0 is self-hosted in `kairos/static/vendor/`; its ISC license is included there. Its distribution was checked against the npm package integrity value. The currency artwork is a self-hosted subset of spothq/cryptocurrency-icons under CC0; [source revision, attribution, and update notes](kairos/static/vendor/crypto-icons/NOTICE.md) accompany the SVGs. There are no browser calls to third-party CDNs.
