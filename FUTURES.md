# Linear Futures

Kairos supports paper and explicitly gated live execution for qualified USD-quoted linear crypto perpetuals on Kraken Derivatives. HTF, market making, DCA and TWAP are supported. Inverse contracts, dated Futures, non-crypto contracts, Futures arbitrage and Futures basket rebalancing remain blocked. Public listings do not establish your account's regional eligibility; this is not a US CME Futures integration.

A supported contract has the `PF_` prefix, `flexible_futures` type, USD quote, contract size 1, published precision and margin tiers, and active, non-expired crypto metadata. One contract unit represents one base unit of exposure, not ownership of that coin. Fractional contract sizes follow Kraken's published trade precision. The Futures catalog never enters the spot execution catalog. Its equity, exposure and loss caps are separate from spot, paper margin and Earn; they are not a combined account-wide risk limit. Switching products does not liquidate existing spot holdings.

## Paper operation

1. Stop and reconcile any orders. Select **Linear crypto perpetuals · USD** under Asset class, or select the **Futures** product, then choose a supported Bot market. The header picker still changes only the chart.
2. Select HTF, market making, DCA or TWAP. Set the local leverage cap, notional order/exposure caps, loss limit and fee assumptions. The default leverage cap is 1×; 1–5× is configurable. Published margin tiers can require more collateral than that cap implies.
3. Set Paper balance under Capital, Save settings, and Reset selected paper portfolio if you want a new simulated allocation. Futures paper cash and positions are separate from paper spot, paper margin and live Futures.
4. Press Start. HTF and market making use Jev; DCA and TWAP do not.

HTF can open longs or shorts and reduces an opposing position before reversing direction. New longs require the existing rising-trend/cost filter; new shorts require a falling trend and a downward move exceeding round-trip costs. Model confidence cannot bypass these conditions. Market making uses post-only quotes, one tracked order at a time. Paper maker fills require a later book strictly through the limit, capped at 10% of visible crossing depth. Quotes expire locally after 30 seconds. This is a simulation, not an exchange queue model.

DCA amounts are **notional USD including entry-fee reserve**, not a collateral deposit. Futures DCA direction chooses buy/long or sell/short. TWAP uses contract quantity, a positive price ceiling for buys or floor for sells, and a separate parent notional cap. Both require enough collateral for the entire opening parent, round-trip fee reserve and the 20% starting-collateral buffer. The buffer is local sizing headroom, not segregated or protected exchange collateral.

For an existing position, **DCA/TWAP reduce existing position only** requires the opposing side and prevents opening or reversing exposure. Opening a scheduled run against an opposing position requires closing it first or explicitly selecting reduce-only. Missed and partially filled slices are not caught up. Run identities, claimed slots and orders survive restarts, separately from spot programs and separately for each mode. A completed run requires explicit New strategy run confirmation.

Paper valuation uses fresh mark prices, signed position PnL, fees and funding. Historical hourly absolute funding rates are integrated over the time the position was held; the current-hour ticker supplies the remaining interval when needed. Missing historical coverage stops valuation rather than inventing a rate. On a maintenance breach, the simulator attempts bounded visible-depth liquidation, charges an estimated extra 50 bps liquidation fee and latches a halt. Residual positions can remain. These are conservative simulation assumptions, not an exact reproduction of Kraken liquidation, assignment or insurance-fund rules.

## Live operation

**The entire dedicated collateral wallet remains exposed to exchange margin rules, including the local sizing buffer. A loss limit, Stop or process restart does not close positions or stop funding charges.**

1. Obtain eligible Kraken Derivatives access. Use a dedicated account/subaccount wallet with no unrelated trading, orders or positions. Select cross margin and USD PnL settlement on Kraken. Kairos reads these preferences but never changes them. Isolated margin is not modeled.
2. Manually prefund that wallet with USD only. No automatic conversion, transfer, withdrawal, top-up or balance import is implemented. At first arming, its USD balance must equal the explicitly configured **Futures live collateral · USD**, and the full amount must be available. Existing live allocation cannot be silently resized. Do not deposit, withdraw, convert or trade externally while Kairos manages this wallet.
3. Configure separate `KRAKEN_FUTURES_API_KEY` / `KRAKEN_FUTURES_PRIVATE_KEY` credentials with the required read and order permissions. Do not grant withdrawal permission. Keep the Spot key available for authenticated fee queries; it does not need spot order permission for Futures trading.
4. Set both `ALLOW_LIVE_TRADING=true` and `ALLOW_FUTURES_TRADING=true` on the server. Deploy backend/static changes together and restart only when safe. A restart remains paused in Dry-run; it does not reactivate trading or flatten existing exchange positions.
5. Save the Futures configuration, select Trading, and accept the separate Futures confirmation. Preflight checks the contract, current fees, collateral, settlement/margin preferences, account history, positions and unrelated orders. **Arming leaves Futures paused. Press Start separately.**

Kraken deprecated `/feeschedules` and `/feeschedules/volumes` in June 2026. Kairos uses the central Spot `TradeVolume` endpoint with an explicit `derivatives` asset-class query, not those stale schedules. Missing fee data or underestimated fees block orders. Actual fill fees are recorded even if they exceed the reserve, then trading stops for review.

Orders carry durable client IDs before submission. Opening orders obey notional, collateral, margin-tier and fee checks; opposing orders are reduce-only and cannot cross through zero. There is a local 20% starting-collateral buffer. Notional checks use current prices: subsequent market movement or a sell filling at a better price can increase marked or filled notional. Exceeding a marked exposure cap or TWAP parent cap stops further trading, but does not undo a fill or guarantee a maximum loss.

Live takers are IOC limit orders; makers are post-only. Requests have a short `processBefore` deadline and are never automatically retried. Before submission, Kairos arms Kraken's account-wide dead-man switch for 60 seconds. Normal cycles reconcile/cancel tracked quotes before replacement; a longer cycle can let the switch expire the quote. The switch cancels orders, not positions, and is another reason this must be a dedicated wallet. Kairos never disables it with a zero timeout.

## Stops and recovery

Stop cancels tracked orders and retains positions. While paused, valuation continues for the selected portfolio, including funding. **Reduce Futures position…** submits one confirmed reduce-only IOC within the price and order cap, even after the daily loss limit. A partial fill or order cap can leave a residual position; inspect the portfolio and repeat deliberately if needed. This control is not an unconditional flatten-all market order. If exchange state disagrees with Kairos, manage the risk directly on Kraken rather than forcing the bot through a reconciliation failure.

The five-second terminal-order cache is not sufficient for restart recovery. Kairos instead pages Derivatives history by durable client ID, deduplicates executions, and commits cumulative fills with the Futures ledger atomically. Cancellations require consistent terminal history; absence from open orders alone is not proof of cancellation. Lost acknowledgements are never resubmitted. An outcome that cannot be proven remains blocked for investigation, including a request that may never have reached Kraken.

Live valuation compares confirmed local quantities with exchange positions and checks the dedicated wallet against its USD cash-log chain. Settled cash and unrealized funding come from the account. External trades, liquidation/assignment, cash movement, non-USD settlement, isolated margin, missing history and inconsistent snapshots halt execution. History reads are bounded at 50 pages of 1,000 records; reaching that budget stops reconciliation rather than assuming a complete history. The Exchange accounts tab remains a separate first-page read-only view.

Restart warns if live Futures positions remain. Paper Start is blocked until those real positions are managed; changing to Dry-run does not close them. Close positions before changing Futures market, product or leverage. Paper reset clears only paper Futures state and schedules; it never resets live collateral or spot ledgers. New strategy run changes scheduling state, not funds.

## Verification and limits

Local regression tests cover paper and mocked live execution, fee signing, contract qualification, collateral and margin limits, funding, liquidation, long/short accounting, reduce-only exits, program isolation, partial fills, restart history, ambiguous writes, stop races and API confirmation/CSRF controls. Browser checks exercise all four strategies and responsive settings without exchange writes or model inference. A public-only probe returned 256 qualified contracts and working PF_XBTUSD depth, marks and 719 completed 15-minute candles; listings change.

No real private-account entitlement, fee response, order, fill, liquidation or recovery has been verified against Kraken. Treat this as experimental software, not a certified execution system or evidence of profitability. Validate permissions and paper behavior before deciding to enable a trade-capable key. Do not rely on your current read-only key or disabled derivatives account as the application's safety boundary.

API references: [Derivatives REST schema](https://docs.kraken.com/openapi/futures-rest.yaml), [history schema](https://docs.kraken.com/openapi/futures-history-rest.yaml), [Spot fee schema](https://docs.kraken.com/openapi/spot-rest.yaml), and [Futures authentication](https://docs.kraken.com/api/docs/guides/futures-rest/), reviewed 2026-09-22.
