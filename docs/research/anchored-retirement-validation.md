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

Native OKX acquisition completed at 2026-10-16 03:50:26 UTC with 100% observed coverage at every interval. All 564 raw response hashes and 168,577 converted rows, including warmup, passed validation. The input SHA-256 is `60f8e0f42998285edd864489e73791172eb5ac6b611a840dee604c4281a77d55`. These were readiness checks, not financial outcomes.

## Results

The signed freeze `215faea23a2b53f28562e64805105c646190822d` and exact-head push/PR CI 38848116994 / 38848124738 passed before simulation. All 64 controls completed at **2026-10-16 04:10:11 UTC**; the financial audit reconciled 387 filled episodes. The [outcome receipt](anchored-retirement-outcome.json) preserves every aggregate, primary check and artifact hash.

| Path | All-eight mean account return | Anchored-retirement reference | Fills, all → retirement | Closed R, all → retirement |
| --- | ---: | ---: | ---: | ---: |
| IOC/base | −2.7743% | −2.5857% | 140 → 127 | −0.4430 → −0.4536 |
| IOC/adverse | −1.8354% | −1.6608% | 61 → 55 | −0.5566 → −0.5646 |
| Maker/base | −2.9462% | −2.8028% | 186 → 173 | −0.4577 → −0.4725 |
| Maker/adverse | 0% | 0% | 0 → 0 | undefined → undefined |

The cash reference keeps all sixteen independent accounts in the denominator. Its two cash accounts are zero-fill accounts, recorded separately from the retained strategies' zero-fill list. No simulated results were rewritten to construct the reference.

All four base anchored cases lost and had negative closed R:

| Anchored case | Marked return | Closed R | Closed episodes |
| --- | ---: | ---: | ---: |
| IOC 5m | −1.2260% | −0.2231 | 10 |
| IOC 15m | −1.7918% | −0.7286 | 3 |
| Maker 5m | −0.7112% | −0.1226 | 10 |
| Maker 15m | −1.5827% | −0.7195 | 3 |

**The predeclared retirement replication check did not pass.** Each execution mode has only thirteen closed anchored episodes, below the frozen minimum twenty. Do not lower that minimum, extend the period, or reset latches to manufacture more trades. The loss direction repeated, but retirement remains provisional rather than a validated fix.

The wider family also remains unsupported. Fifteen of sixteen IOC/base cases and all sixteen maker/base cases are negative. Fourteen IOC/base and fifteen maker/base accounts latch their entry-loss halt. There are seven and six realized risk-budget breaches, respectively, despite passing all planned-risk reservation checks. Planned 1% risk is not a guaranteed realized-loss cap; protection and loss latches were not weakened or reset.

Retirement reduces marked losses by holding two accounts in cash, but remaining returns are negative and pooled closed R worsens. There is no activity-preserving improvement or validated profitable replacement. Do not promote the lone positive IOC case, which has only four fills, or resume AI spending to rescue this result. Keep the current configurations out of promotion; a different strategy hypothesis requires its own declaration and new evidence.

The maintained `reports/index.html` contains every completed and blocked result with original receipt times. No production strategy, exchange state or deployment changed. Browser/canvas visual review remains unverified.
