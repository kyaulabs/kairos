# Anchored-VWAP retirement validation

The [ledger diagnosis](quarter-strategy-diagnosis.md) proposes removing anchored VWAP from the research allocation, without changing production. The user approved fresh validation and selected public OKX candles after the original Kraken data failed coverage gates. No further AI inference is authorized.

## Frozen decision

Use July 1 through exclusive October 1, 2026 UTC, with two warmup days. This is the latest completed calendar quarter beyond the June 30 cutoff of the previously traversed archive. It was selected before acquiring or inspecting prices, returns or strategy outcomes. Do not substitute another period after results.

Run all eight unchanged accepted setups at 5m/15m, under both IOC and frozen maker entry policies, in base and adverse execution: 64 new simulated controls. Keep the accepted signal source, positive-target eligibility, modeled fees, $1,000 independent accounts, 1% planned stop-risk budget, $12.50 UTC entry-loss latch, four-hour deadline, bot-managed IOC protection, increments, cash and partial ownership unchanged.

The retirement reference replaces only the two anchored accounts with $1,000 cash. Capital is not reallocated; the mean still uses sixteen accounts. Its 64 account references are derived from the 64 controls, not another 64 simulations. Lower exposure is intentional and cannot demonstrate an activity-preserving trading improvement.

Directional replication requires all four base anchored cases to have negative marked return and defined negative closed R, with at least twenty closed anchored episodes per execution mode across the two intervals. Otherwise report mixed, sparse or unreplicated evidence. This is a descriptive decision check, not a significance test or proof of future losses. Inactive adverse maker cases are not robustness evidence.

Show every setup, including fresh winners and losses. Do not promote retained setups by ranking this validation period. The [declaration](anchored-retirement-declaration.json) binds inputs, code, the original pre-acquisition decision and the evaluation before financial results; exact-head CI must pass before execution.

## Preserved blocked source

The original Kraken Q3 archive has SHA-256 `96daae63c7b3acb522892e1f602f9c564d1a3fb659089d9b0699282f30d41226`. Its outcome-blind timestamp check found:

| Native interval | Coverage | Frozen minimum |
| --- | ---: | ---: |
| 1m | 68.1831% | 80% |
| 5m | 98.7847% | 99% |
| 15m | 100% | 99% |

Stop that attempt before calculating returns. Do not pad missing candles, lower the gates or replace the period. Its acquisition, source CRCs and failed coverage receipt remain under `reports/anchored-retirement-validation-attempt-001/`.

## Separately approved OKX source

Fetch only public `GET /api/v5/market/history-candles` for `BTC-USDT` from `us.okx.com`, at native 1m/5m/15m intervals. Preserve raw nine-field responses, request/receipt clocks, confirmed-bar status and hashes. No credentials, login, orders or private endpoints are needed.

The same coverage gates apply. Convert native OHLC and base volume using the existing `OKX.bars` mapping. The unused trade-count field has an explicit unavailable-field sentinel, not evidence of zero trades. No VWAP or missing candle is invented.

The first default-urllib request returned HTTP 403/code 1010. A clearly identified research-client User-Agent returned the public API response; both preflight responses are retained. No proxy, login or authentication workaround was used.

Native OKX candles remove the cross-venue candle proxy for this check, but do not reconstruct historical books, queues, account fees or instrument rules. Execution still uses declared synthetic paths, spreads, participation and slippage. Because provider and period both differ from development, their effects are not isolated. Treat this as a fresh-period check under an explicitly changed candle source, not a pure period-only replication.

Financial outcomes are pending at this freeze. The maintained `reports/index.html` contains completed and blocked evidence with original receipt times. No result authorizes deployment or proves that the remaining strategies have an edge.
