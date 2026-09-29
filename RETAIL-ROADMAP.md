# Retail API and strategy roadmap

The agreed scope is Kraken retail markets and account data. Funding and Earn stay read-only. Each strategy uses an explicit, pre-funded allocation; Kairos must not convert currencies or transfer money to fund a trade. This is a staged integration, not a claim that every Kraken endpoint is implemented.

| Milestone | Status | Result |
| --- | --- | --- |
| Unified market and account views | Implemented | Spot/FX, margin eligibility, xStocks, and Futures discovery; quotes, candles, and separate read-only account snapshots. |
| Native-currency allocations | Planned | Persist allocations and reservations by venue, wallet, product, and currency without treating exchange balances as bot capital. |
| DCA and TWAP | Implemented | Persisted deterministic schedules in paper and explicitly gated live spot, using the existing fixed-quote allocation. |
| Threshold rebalancing | Implemented | A fixed-quote funded spot basket with target weights, drift bands, cooldown, and daily turnover limits. |
| USD linear crypto perpetuals | Implemented; real-account execution unverified | Separate paper/live collateral, HTF/maker/DCA/TWAP, funding, reduce-only exits and historical recovery. |
| Additional product execution | Requires separate approval and verification | Product-specific accounting, eligibility, simulation, risk, and recovery before live orders. |

## Implemented coverage

The chart picker and Strategy search share a catalog. Product filters distinguish crypto spot, margin-eligible crypto, fiat/FX, tokenized xStocks, and Futures. Margin eligibility comes from public pair metadata, not an account permission check. New product IDs are namespaced. xStocks remain browse-only; qualified linear perpetuals use a separate Futures execution catalog, never the spot catalog. Strategy shows restrictions for inverse, dated and non-crypto contracts.

xStocks discovery requests the current tokenized asset version and deduplicates legacy SPV aliases using canonical names. These instruments are tokens, not brokerage shares. Futures retain contract symbols, underlying pairs, contract types, and contract sizes; their quantities must not be treated as spot coin balances. Quotes retain their venue's receipt time after a failed refresh. Volume is in native units, not comparable USD turnover. Spot/xStocks rolling changes use WebSocket v2 snapshots; Futures use `change24h`.

The Exchange accounts tab reads only when opened, changed, or refreshed. Its 30-second server cache is separate from the trading ledger.

| View | API reads |
| --- | --- |
| Spot-family wallet | `BalanceEx`, including exchange asset IDs and trade holds |
| Spot-family orders and recent trades | `OpenOrders`, `TradesHistory` |
| Margin collateral and positions | `TradeBalance` in USD, `OpenPositions` |
| Earn allocations | `Earn/Allocations`, preserving native and converted currency labels |
| Deposit and withdrawal ledger entries | `Ledgers` filtered by transaction type |
| Futures wallets, positions, orders, recent fills | Derivatives `accounts`, `openpositions`, `openorders`, `fills` |

These are snapshots of the account accessible to the configured key, not imported bot holdings. Margin collateral overlaps spot balances; Earn and Futures remain separate views, with no summed equity or automatic allocation. History shows the first returned page and tables are capped at 1,000 rows. Pending funding status, funding-method discovery, additional wallet/subaccount selection, exhaustive account-view history pagination, and private streaming are not implemented. Futures execution recovery separately pages order, execution and cash-log history with a bounded reconciliation budget. Funding history uses ledger reads rather than the deprecated deposit/withdrawal status endpoints. The Funding Beta API is not integrated.

Public integration checks on 2026-09-22 returned 1,339 crypto spot, 12 FX, 172 canonical xStocks, and 296 Futures instruments. xStocks/Futures quotes and candles worked, as did rolling change for all 172 xStocks. Counts change with listings. Private signing, response normalization, failure handling, and execution isolation were checked with fixtures; no real private-account request, order, transfer, or Jev inference was made. Account access and entitlements remain unverified.

## Strategy requirements

DCA buys a fixed quote-currency amount on a schedule, with a total budget and end condition. The next due event and run identity persist so a restart cannot buy twice. Missed intervals are skipped, not accumulated. The budget is purchase amount including fees multiplied by purchase count; the whole run must be pre-funded. Funds, spread, size, or exposure failures skip a slot; data errors and uncertain orders can pause the engine.

TWAP splits an approved parent order into time-spaced children, bounded by its total quantity, duration, limit price, and available allocation. Each child settles before another can spend allocated funds. Partial fills reduce the remaining parent quantity; missed or unfilled slices are not added to later orders. The run pauses on completion or expiry, leaving any remainder unexecuted. Unknown submission or cancellation outcomes pause the schedule. Stop cancels tracked children, not unrelated exchange orders. TWAP reduces order concentration; it does not guarantee a better execution price.

Threshold rebalancing compares a spot basket with configured target weights and trades only outside a drift band. It uses a single funded quote currency, an explicit CASH weight, a cooldown, turnover limits including fees, and a minimum trade size. Only named basket holdings and allocated cash enter target valuation; other holdings remain untouched but still count toward engine-wide risk limits. Sells reduce allocated holdings; buys spend allocated cash and confirmed proceeds. A rejected sell must not fund a later buy. No automatic FX conversion, cross-wallet funding, or use of Earn collateral is permitted.

These methods use deterministic planning and execution safeguards in both paper and gated live spot. DCA and TWAP also support qualified linear Futures through separate collateral and contract accounting; rebalancing remains spot-only. See [Futures requirements](FUTURES.md). They make no Jev calls and have no model veto. Completion, rearming, and mode-specific progress are explicit; saving unrelated risk settings never resets a run. The broader multi-wallet allocation milestone remains planned. See [operating instructions](OPERATIONS.md#strategies) and the [TradingAgents review](TRADINGAGENTS-REVIEW.md). None of these methods implies profitability.

## Gates for broader execution

- Keep existing order identities, persisted intents, uncertain-write reconciliation, fee reserves, exposure/loss limits, and protected principal. Never retry an ambiguous write blindly.
- Keep paper and live ledgers separate. Account discovery must not make a wallet spendable or change the bot's configured market.
- Qualify xStocks for token units, rebasing/corporate-action effects, fees, sessions, and account eligibility. Do not infer share ownership from a stock-like ticker.
- Qualify margin for borrowing, collateral haircuts, rollover fees, maintenance, liquidation, and position reconciliation. Its current simulator is not a live margin adapter.
- Extend beyond the implemented USD linear crypto perpetuals only after qualifying inverse accounting, dated settlement/expiry, non-USD or isolated collateral, and other contract-specific recovery. Public Derivatives listings do not establish access to every US or regional futures product.
- Brokerage shares and DEX remain outside the verified integration. Require an applicable documented API and explicit scope before implementation. Withdrawals, transfers, automatic conversions, and Earn allocation/deallocation remain excluded.

API sources: [Spot REST specification](https://docs.kraken.com/openapi/spot-rest.yaml), [Futures authentication](https://docs.kraken.com/api/docs/guides/futures-rest/), [Kraken API index](https://docs.kraken.com/llms.txt), and [WebSocket v2 ticker](https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/ticker). See [operations](OPERATIONS.md#exchange-account-snapshots) for credentials and the [CLI review](KRAKEN-CAPABILITIES.md) for nonce and retry hazards. Kairos uses native HTTP/WebSocket clients; it does not install or execute the CLI.
