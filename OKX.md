# OKX U.S. spot execution

KAIROS supports separate OKX U.S. hosted-demo and real-money cash-spot environments. Both start paused and unarmed. Installing credentials or enabling a server gate does not authorize a trade.

Hosted-demo execution is **NOT VERIFIED** by this implementation's offline tests. Fixtures exercise the normal engine, dashboard commands, scheduler and accounting, but are not OKX executions. Live execution is also unverified. A qualification result requires an actual authorized demo buy and sell with explained final accounting. This diagnostic makes no profitability claim and does not qualify or start the frozen Alpaca trial.

## Private setup

1. In the official OKX interface, choose **Trade → Demo Trading → Personal Center → Demo Trading API → Create Demo Trading API Key**. Use a dedicated account context without another order manager. Grant Read and Trade, not Withdraw. Restrict the key to the server's IP where supported. Trade permission alone is not an endpoint restriction; KAIROS separately prohibits transfers, withdrawals, borrowing and account-setting writes.
2. Inspect **Demo Trading → Assets** for the virtual funds supplied by OKX. Choose an actual funded spending currency. Follow the [official demo guide](https://www.okx.com/en-us/help/how-to-use-demo-trading) if initial virtual funds are unavailable, before binding an account to KAIROS. Do not reset a bound account or adjust its virtual balance to make a failed run pass. KAIROS has no funding/reset API.
3. Configure cash-spot availability in OKX itself. KAIROS rejects unsupported account modes, borrowing settings/liabilities and conflicting working orders. Pre-existing virtual coins remain unallocated; they need not be sold to begin.
4. Install the credentials privately using the service's environment configuration. Never paste keys or passphrases into chat, logs or the dashboard. The names in [.env.example](.env.example) are exact:

| Environment | Credentials | Required write gates |
| --- | --- | --- |
| Hosted demo | `OKX_DEMO_API_KEY`, `OKX_DEMO_SECRET_KEY`, `OKX_DEMO_PASSPHRASE` | `ALLOW_OKX_DEMO_TRADING=true` |
| Real money | `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE` | Both `ALLOW_OKX_TRADING=true` and `ALLOW_LIVE_TRADING=true` |

All gates default to `false`. The existing `OKX_*` credential names are live credentials, never demo fallbacks. Alpaca remains paper-only. No key, passphrase or signature is returned to the browser.

Restart the service safely after changing its environment. Missing OKX keys do not prevent Kraken or Alpaca startup. `KAIROS_EXCHANGE` accepts `kraken`, `alpaca`, `okx-demo` and `okx` for a new installation. The saved dashboard selection takes precedence; changing this variable cannot bypass an existing non-flat venue.

## Environment and instrument identity

| Service | Hosted demo | Real money |
| --- | --- | --- |
| REST | `https://us.okx.com` | `https://us.okx.com` |
| REST environment header | `x-simulated-trading: 1` | `x-simulated-trading: 0` |
| Public WebSocket | `wss://wsuspap.okx.com:8443/ws/v5/public` | `wss://wsus.okx.com:8443/ws/v5/public` |
| Documented private WebSocket | `wss://wsuspap.okx.com:8443/ws/v5/private` | `wss://wsus.okx.com:8443/ws/v5/private` |

This implementation uses public `books5` full snapshots and authenticated REST order/fill reconciliation, not private-stream execution messages or a reconstructed incremental book. Business WebSockets are not needed. Credentials are not forwarded through redirects. There is no global-host, live/demo, other-venue or credential fallback.

Discovery uses the authenticated account's enabled SPOT instruments and `tradeQuoteCcyList`. A selection such as `okx-demo:BTC-USD:USDC` means native instrument `BTC-USD`, settled in **USDC**. It does not mean USD cash. USD, USDC, USDG and USDT are separate assets. An unavailable variant is rejected rather than replaced.

Only the explicitly selected execution instrument receives a book subscription. Browsing charts/favorites does not retarget execution. Charts read up to 300 native candles from the U.S. REST endpoint with the selected environment header. They preserve source opening times, base-asset volume and the forming/completed marker; daily bars open at UTC midnight. Missing bars are not invented. These candles are chart-only and never supply execution prices or enable predictive strategies. Market-list turnover and percentage-change data remain unavailable; no chart data comes from another venue or environment. Both venue timestamps and local receipt times remain visible. Reconnection invalidates old books; heartbeats do not freshen prices. Book reads wait at most ten seconds, and the feed allows five connection attempts before requiring an explicit restart.

OKX's `maxLmtAmt` is a USD-denominated exchange limit even for a USDT-priced order. KAIROS checks it with a fresh same-environment native quote/USD index, such as `USDT-USD`, rather than assuming stablecoins equal USD. The preview shows that reference and the estimated USD notional; each actual order records a new check, with freshness checked again after transport admission. Missing, mismatched or stale references block submission. This reference only checks the venue cap: it does not convert funds, supply execution prices or change the ledger's spending currency. Native USD-priced books need no FX reference, regardless of their settlement currency.

## Execution cycle

1. Stop and reconcile the current venue. It must have no working/uncertain orders, unresolved recovery or owned non-cash holdings. Select **OKX Demo — virtual funds**. The destination remains unarmed. The separate **OKX Live — real funds** selection cannot inherit demo authorization.
2. Use **Exchange accounts → OKX** to inspect native balances without allocating them. Open **Execution cycle…** in the settings controls.
3. Choose the exact instrument/currency. Prefer an enabled BTC market with adequate visible liquidity. Set the initial allocation, entry budget, absolute buy ceiling/sell floor if desired, exit-attempt limit and duration. The initial defaults are a 500-unit allocation, 25-unit order cap, 100-unit exposure cap and 12.50-unit daily-loss limit, denominated in the selected currency, not assumed dollars. These values do not fund the account.
4. Click **Run read-only preflight / preview**. This saves the explicit execution-market selection and reads broker evidence; it sends no order and does not establish an allocation binding. It checks identity, permissions, available funds, native rules, planning fees, clock, book age/spread, size and visible liquidity. If the budget cannot leave a sellable quantity after fee/lot allowances, it shows the minimum required amount and submits nothing.
5. Review the masked account, actual currency, budget/quantity, fee source, absolute and slippage bounds, attempt counts, duration and dust policy. A cycle permits one entry attempt and one to three explicitly selected exit attempts; duration is 30–900 seconds. The preview expires after two minutes. Changes or Stop invalidate permission.
6. Type the exact confirmation shown in the preview, then click **Authorize this finite run**. Demo cycles use `AUTHORIZE OKX DEMO EXECUTION_CYCLE`. Live cycles require `AUTHORIZE OKX LIVE REAL MONEY EXECUTION_CYCLE` and both live gates. These are separate permissions, not interchangeable confirmations.
7. Inspect **OKX finite-run evidence**, Orders and Portfolio. The buy uses a bounded cash IOC limit order. Only terminal execution evidence establishes acquired ownership. The sell uses fresh native data and the quantity actually acquired after fees/rounding. The engine reconciles executions and balances, then stops. No model, profitability threshold or passive maker fill controls this diagnostic.

When account fee discovery is unavailable, only a demo execution cycle can use a positive, explicitly supplied **Demo-only fallback planning allowance · bps**. The preview names that limitation and allowance. It is not an actual fee, cannot be used for live trading or TWAP, and cannot replace missing execution-fee evidence.

The command flow is `POST /api/okx-preview` followed by `POST /api/okx-authorize`. The latter accepts exactly `preview_id` and `confirmation`; it cannot increase approved terms. Both retain normal Origin, JSON and CSRF checks. Use the dashboard rather than putting credentials or CSRF tokens in shell history.

## Outcomes and recovery

| Result | Meaning |
| --- | --- |
| Not run / authorization required | No finite permission has been issued. |
| Preflight blocked / `PREFLIGHT_BLOCKED` | A required check failed. Inspect the exact check and corrective action; a preview failure authorizes nothing. |
| `NO_FILL` | The entry was terminal without execution; no sell follows. |
| `PARTIAL` | Some execution occurred, an exit was blocked, Stop interrupted the run, sellable inventory remains, or an operational budget bound failed. |
| `UNKNOWN` | Submission outcome is unresolved. Reconcile the original client intent; never repeat it blindly. |
| `RECONCILIATION_PENDING` | Execution/accounting evidence is incomplete or inconsistent. No accounting success is claimed. |
| `PASSED` | Positive buy and sell executions, no unresolved orders, matched accounting and no cycle residual. |
| `PASSED_WITH_DUST` | The same execution/accounting checks passed, but explicitly recorded unsellable inventory remains. This is not complete liquidation. |

The report retains client and broker IDs, native executions, fees by currency, exact residual, optional fresh-book value estimate, closing ledger and reconciliation evidence. `submitted` is true for established broker submission/rejection, false when nothing was sent, or `unknown` when delivery cannot be established. A value estimate is not a fill or a currency conversion.

Planning fees reserve capacity; they cannot guarantee the exchange's eventual charges. An actual quote-fee overrun remains recorded and prevents a passed result if entry debit exceeds the approved budget. The already-authorized owned exit can still reduce exposure; no extra purchase is permitted. Actual signed fill fees/rebates determine accounting: negative native fees are charges, positive values are rebates. Base fees reduce acquired inventory; quote fees affect that exact quote asset. Unexpected third-currency effects remain recorded and block further trading instead of being guessed, converted or silently taken from unrelated holdings.

The bot ledger is an explicit allocation, not the total account balance. Account baseline comparisons include pre-existing assets without importing them into bot ownership or profit. Native order observations and executions are deduplicated; cumulative quantities never manufacture fills. Temporary order/fill and balance lag has bounded read-only reconciliation. An IOC can wait up to 45 seconds for execution/fee evidence; settlement reads also have a 45-second bound. A missing response, timeout or restart never permits resubmission.

**Stop** revokes permission immediately. It attempts cancellation only for known owned working orders, reconciles cancellation/fill races and retains holdings. Cancellation acceptance is not terminal cancellation. Rejected or uncertain cancellation intents are not blindly replayed. An unresolved cancellation may require operator investigation at OKX. Stop's final settlement wait is bounded to ten seconds. A daily-loss halt is not a guaranteed maximum loss or surprise liquidation.

**Reconcile** performs broker reads and applies verified native evidence. It does not trade, acknowledge external orders, adjust balances or rearm. **Restart engine** restarts read-only services and reconciliation; another finite preview/confirmation is required to trade. Interrupted runs remain interrupted even if later reads resolve their orders. Shutdown and process restart preserve intents and revoke permission.

State lives in `DATA_DIR/okx-demo.sqlite3` and `DATA_DIR/okx-live.sqlite3`, separate from Kraken and Alpaca. Bindings use native account identity, venue and environment. Rotating a key for the same account preserves state; a different account cannot inherit its ledger. Do not remove a store to evade recovery. Back up the databases with the same SQLite/WAL precautions as other venues.

External activity after binding, changed account identity, unexplained balances, missing fees and unresolved orders block new execution. Even disclosed dust remains an owned holding under the existing flat-before-switch rule. Do not erase it or buy extra coins to conceal it.

## Finite TWAP

Save **TWAP** settings for the selected native instrument: side, base quantity, explicit parent limit, slices and duration. The initial parent limit `1` is deliberately inert, not a market-price suggestion. Preview rejects a currently nonmarketable parent limit. Choose a duration that leaves room for account reads and the engine interval; missed slots are not replayed.

Click **Preview TWAP…**, or choose **Finite TWAP** in the execution dialog, then review and type its separate confirmation. The demo phrase is `AUTHORIZE OKX DEMO TWAP`; live uses `AUTHORIZE OKX LIVE REAL MONEY TWAP`. Approval uses the existing `programs.prepare/run` scheduler, normal risk checks, durable intents and native accounting. Sell programs require already allocated, available inventory. They cannot sell unrelated demo holdings.

Completion stops and clears permission. `TWAP_COMPLETE` describes a completed schedule, not guaranteed full fills, flatness, round-trip qualification or profitability. A completed/changed schedule requires explicit **New strategy run…** before another preview. Every hosted TWAP run requires its own authorization; cycle consent does not authorize an additional experiment.

Predictive strategies, DCA, rebalancing, margin, derivatives, transfers, withdrawals, automatic funding, compounding and local OKX fill simulation are unavailable. Kraken and Alpaca retain their own strategies and recovery rules.

## Verification

Offline tests cover native routing/signatures, prohibited writes, account/currency isolation, fee signs, durable unknown outcomes, duplicate/late observations, partial/no fills, cancellation/fill races, restart, balance lag, finite cycle/TWAP paths and dashboard consent. Browser checks use deterministic native REST fixtures, not an authenticated OKX account.

No authenticated OKX read-only check or hosted order has been run for this implementation. Credentials and a gate installed by the operator are not finite-run authorization. The next external action is the dashboard's broker-read-only preview; inspect its actual account/instrument/budget terms before authorizing one demo cycle. Full qualification remains unavailable until that real hosted cycle finishes with explained accounting.

Regional contracts were checked against the [U.S. API reference](https://app.okx.com/docs-v5/en/), [U.S. API FAQ](https://www.okx.com/en-us/help/api-faq), [order-management guide](https://www.okx.com/docs-v5/trick_en/), [fee rules](https://www.okx.com/en-us/help/trading-fee-rules-faq) and [unified USD orderbook rules](https://www.okx.com/en-us/help/unified-usd-orderbook-faq). Documentation examples and mocked results do not establish account capability or execution.
