# Native-book execution research

The next step after the [negative strategy diagnosis](quarter-strategy-diagnosis.md) is to establish executable liquidity and protective ownership before changing signals. This is research only. All eight setups remain; no trading, deployment, model inference, fee reduction, halt reset or strategy financial replay is authorized by the data-acquisition declaration.

The [feasibility outcome](native-book-feasibility-outcome.json) records the completed public-data checks. The [original forward declaration](native-book-forward-declaration.json) froze the first acquisition, which stopped early. The user then authorized a [segmented continuation](native-book-segmented-declaration.json) and, after a rate/cap warning, a [larger-cap final segment](native-book-final-segment-declaration.json). Neither changes the failed original run. Private responses, runners, native frames and generated HTML stay under the ignored repository-root `reports/`, with `reports/index.html` as the entry point.

## What the feasibility check established

On October 16, 2026 UTC, the anonymous OKX website catalog returned a BTC-USDT spot 400-level archive for July 1, 2026. The day was fixed before the query as the first day of the already-inspected Q3 development period. It is not a fresh return holdout.

| Check | Result |
| --- | --- |
| Public instrument/catalog metadata | HTTP 200, API code 0; exact spot instrument and day returned |
| Advertised book archive | 213.05 MB; HTTP range metadata reports 223,399,810 bytes |
| Bounded acquisition | First 1,048,576 compressed bytes only, HTTP 206; no full-file or full-day integrity claim |
| Saved complete records | 29,929 records spanning 454.808 seconds; one snapshot, 29,928 updates |
| Archive shape | `action`, `asks`, `bids`, `instId`, `ts`; levels contain price, base quantity and order count |
| Archive sequence evidence | No `seqId` or `prevSeqId`; missing updates cannot be ruled out from non-crossed reconstructed books or timestamp spacing |
| Historical trade-file metadata | HTTP 429 / code 50011; original response retained, no retry |
| U.S. public forward probe | One unauthenticated connection, 60.002 seconds; 402 saved frames: two acknowledgements, 370 book frames and 30 trade messages |
| U.S. native book shape | Four-field levels and `seqId`/`prevSeqId`; checksum is zero and therefore unavailable, not successfully validated |

The initial tar-stream inspector failed with `unexpected end of data` because the archive member was intentionally truncated. An offline reader validated the saved POSIX tar header and inspected complete JSON lines without another request. The original failure, raw prefix and later verification remain separate artifacts. Neither the incomplete gzip CRC nor the full archive hash was verified.

The public catalog's U.S.-branded page does not prove that its historical matching-engine stream equals the U.S. execution target. Aggregated L2 does not reveal our hypothetical queue priority. The trade channel can aggregate executions. Source timestamps and client receipt times were saved, but their difference is not calibrated exchange-to-client wire latency.

These limits rule out promoting this archive prefix into a financial execution benchmark. The U.S. probe does establish that the existing target endpoint can supply a bounded forward dataset without account authentication.

## Original continuous-capture declaration

After the signed declaration and passing exact-head CI, start one 24-hour public recording of BTC-USDT `books` and `trades` on the existing live U.S. endpoint. Start at the first successful connection after that gate, not at a later time selected by market returns. There is no alternate period or automatic restart.

The recorder uses existing locked `aiohttp` and Python standard-library capabilities. It imports no trading/account client or model SDK. Its private source and offline protocol-test hashes are in the declaration; CI does not execute private helpers. Eight separate offline checks exercise the saved native probe and reject sequence gaps, wrong instruments, nonfinite quantities, duplicate prices, missing sequences, updates before a snapshot and duplicate acknowledgements.

Bound the recording at 86,400 seconds or 512,000,000 serialized capture bytes, whichever comes first. Close gzip chunks every five minutes and hash them after closure. Record source timestamps, client UTC receipt times and monotonic receipt times. Require at least 2 GiB of free space before startup. A byte-bound stop is incomplete-duration acquisition, not a completed 24-hour sample.

Stop at the first connection/protocol/schema/sequence/storage failure, reset, unexpected snapshot or timestamp regression. Preserve the native failing message before rejecting it. Do not reconnect, fill a gap, change region or repair a saved record. Public subscription and heartbeat messages are the only outbound WebSocket operations. There are no account, VIP, login, private API or order calls.

Inline checks cover identity, schema and book sequence chains. Full offline reconstruction, native level checks, receipt-clock assessment, gzip/chunk integrity and coverage checks are still required before financial use. A zero checksum is not a passed CRC. A healthy recording is not a fill-probability estimate or evidence of a trading edge. Missing historical instrument rules, account fees, actual order arrival and own-order responses remain explicit uncertainties.

The live `progress.json` is a derived observer record, not a completed outcome. Offline HTML and Apollo copies are snapshots and do not auto-refresh. On completion or failure, retain the start/final receipts and every closed raw chunk; append the outcome and related checks to the dated report navigation. Do not report the data pilot as an independent strategy trial.

## First segmented continuation

The first recorder stopped at `2026-10-17T02:59:48.438967+00:00` after its local host suspended. Its last saved book was received at `02:04:21.385014 UTC`. Preserve that failed attempt, its freeze, all 43 closed chunks and the missing interval. Do not resume its connection or turn its failed continuous 24-hour gate into a pass.

The user then requested more collection to bring the preserved data to 24 hours in total. This creates a different, explicitly segmented dataset. A hash/CRC/protocol audit verified all 101,756 saved frames and 60,319,742 serialized bytes. The prior book source timestamps span **3h 31m 56.098s**. Connection startup, cleanup and host suspend do not contribute time. Full book reconstruction and market-data quality are not certified by this accounting audit.

Freeze one new Apollo connection for the remaining **20h 28m 3.902s** of book source-time. Keep the original U.S. endpoint, spot instrument and public book/trade channels. The combined serialized-byte cap remains 512,000,000 bytes, leaving 451,680,258 for the new segment. The maximum runtime is 73,804 seconds, including 120 seconds of startup/headroom; timeout or byte-cap stops do not complete the time target.

Start after signed-freeze exact-head CI and 16 offline protocol/coverage regressions on local and Apollo environments. Apollo is an existing always-on server; no system power policy is changed. An unprivileged sleep-inhibitor capability probe was denied. Preserve that result rather than escalating or claiming an inhibitor is held. The recorder instead checks Linux `CLOCK_BOOTTIME` against monotonic progress and stops if their difference exceeds one second.

Within the new segment, stop if successive book source timestamps or receipt times are more than ten seconds apart, or total source-vs-receipt progress differs by more than five seconds. These are conservative collection guards, not calibrated wire latency or guarantees of executable quote freshness. Preserve native failing records and stop without reconnecting. No automatic retry or third segment is authorized by this declaration.

Count from the new segment's own initial book snapshot. Once its accepted source span reaches the frozen remainder and both channels are acknowledged, keep the crossing native frame unchanged but cap credited time and the derived selection bound at the target. The combined manifest references two separate UTC windows, source files and host identities; it does not flatten or renumber the stream. Receipt monotonic clocks from different hosts are not comparable, and capture-location effects are not isolated.

The unobserved gap contributes zero time and no liquidity. Future analysis must not bridge it with book sequences, rolling warmup, pending plans, financial labels, owned inventory or protection paths. Censor boundary-crossing horizons and retain actual UTC loss-latch semantics; do not invent a terminal liquidation or reset a latch to obtain more activity. Meeting the segmented time target is not passing the original continuous gate, a strategy validation or permission to run a financial comparison without its own frozen methods.

Apollo's recorder updates saved HTML/JSON progress and the separate-segment manifest while running and when it stops. These pages do not auto-refresh in a browser; downloaded local/Apollo deliveries remain snapshots. The source-time counter is provisional until the full data-quality review. No model, account API, order or production action is introduced.

## Larger-cap final segment

The new feed's observed data rate threatened to exhaust the frozen byte cap before the requested time target. The user explicitly selected a new larger-cap freeze. Stop attempt 002 without changing its active limit, preserve its files, then start one separately frozen Apollo segment with a **1,500,000,000-byte serialized cap** for that new segment alone. This is not an unbounded recorder or an automatic continuation after future failures.

Attempt 002 was operator-stopped at `2026-10-18T20:16:03.384054+00:00`. Its four closed gzip chunks passed hash/CRC/protocol verification: **14,126 frames / 13,323,831 serialized bytes / 19m 59.999s of book source-time**. Together with the original attempt, credited time is **3h 51m 56.097s**. The final segment must contribute **20h 8m 3.903s**. Its maximum runtime is 72,604 seconds including 120 seconds of startup/headroom. A byte/runtime stop before that source-time target is still incomplete.

Preserve the shutdown limitation rather than manufacturing a clean outcome. Attempt 002 inherited ignored `SIGINT` and had no `SIGTERM` cleanup handler. Its fifth gzip remains unchanged as an unsealed forensic file and contributes **zero credited time**. The actual operator-stop proof and an externally audited closed-prefix record are separate from the stale native progress file; no recorder `finished.json` is invented, and the original gzip is not repaired or appended.

The final recorder corrects that concrete shutdown problem: register both signals to cancel the task, close/hash the active gzip and save final metadata. Two offline subprocess regressions verify inherited-ignored `SIGINT` and `SIGTERM` cancellation with valid gzip closure. These join the original 16 checks for **18 offline regressions** on local and Apollo environments. This does not promise graceful cleanup after `SIGKILL`, power loss or every storage failure.

Require the new signed freeze and exact-head push/PR CI before startup. Keep the same anonymous U.S. endpoint, channels and first-failure/no-reconnect policy, including the previously frozen gap, clock and suspend guards. No fourth capture starts automatically. No account endpoint, paid data, inference, order or production permission is introduced.

The manifest now retains **three distinct credited windows**. A gap means excluded or unobserved time: part of the second gap contains a preserved but uncredited unsealed tail. Do not mislabel that tail as eligible data or claim every part of that gap was unobserved. Keep the raw timestamps and the conservative credited end bound; source-time accounting cannot turn missing/excluded liquidity into an executable path. All earlier boundary, clock, reconstruction, loss-latch and financial-study restrictions remain in force.

## Monitored twelve-hour total

Attempt 003 stopped at `2026-10-18T21:02:35.000209+00:00` with `Invalid book depth`. Its final incremental message had 453 ask changes, including 204 zero-quantity deletions; the standing book still had 400 asks after applying it. Reconstruction of all 18,082 saved book messages confirms the distinction. The original rejection, raw failing frame and earlier time credit remain unchanged.

The user's latest instruction is **12 hours total**, including the three preserved windows, rather than a fresh continuous window. The [new monitored declaration](native-book-12h-monitor-declaration.json) retains **4h 25m 45.897s** and requires **7h 34m 14.103s** more. Gaps and the unsealed tail count zero. This cannot pass the earlier continuous 24-hour gate or establish a continuous 12-hour sample.

The corrected recorder applies incremental insertions, replacements and zero-size deletions before checking live depth. Snapshots and reconstructed books remain bounded at 400 levels per side; delta lists are bounded by the native frame-size limit instead of an arbitrary larger row cap. Sequence, numeric, non-crossed-book, source/receipt and suspend guards remain fail-closed. Thirty-nine offline checks passed locally and on Apollo, including the saved failure, shutdown, retry and alert regressions.

The new campaign allows at most three new attempts within one 48-hour bound, with 1,500,000,000 serialized bytes per attempt. Only explicit transport disconnects/timeouts permit a fresh connection after its closed prefix is audited. Access, validation, storage, clock, byte/runtime bounds and unknown faults stop and alert. Each attempt keeps separate files, snapshots, sequences and UTC windows; no fourth attempt or silent limit change is allowed.

An Apollo watchdog checks its child every two seconds and persists failure events. The worker stops if its parent heartbeat disappears. A separately restartable local user service polls Apollo every 15 seconds and sends persistent critical desktop alerts for failures, stale monitor heartbeats or lost monitoring connections. A real simulated failure reached the desktop notification server, and a forced relay-process exit was followed by automatic restart. These are related engineering checks, not strategy trials or confirmed human receipt.

The user declined phone notifications after an unconfirmed generic test. No phone service remains active. Desktop alerts cannot arrive while this computer sleeps or is logged out; queued failures appear on wake/login when the service is available. No between-turn chat push is promised. Saved HTML does not auto-refresh. The counter still requires full data-quality review, and checksum 0 remains unavailable. No inference, financial replay, account request, order or production change is authorized.

Attempt 1 later disconnected at `2026-10-20T08:10:00.053801+00:00`. The watchdog audited its **4h 41m 26.901s** and started attempt 2. That connection stopped at `09:08:16.645134 UTC` on OKX's documented `notice` code `64008`, which warns 60 seconds before a service-upgrade disconnection. The collector had treated the unfamiliar control envelope as a validation fault. This was not evidence of invalid book data. Its stop and desktop-server failure acknowledgement remain preserved; acknowledgement does not prove human receipt.

After the user said to continue, an external audit checked the second connection's 38,118 saved frames, gzip integrity, market protocol, reconstructed book and receipt/source clocks. Its **56m 52.200s** prefix brings the total to **10h 4m 4.998s**, with **1h 55m 55.002s** remaining. All 123 earlier closed-file hashes match. The notice contributes no book/trade time or liquidity. Neither failed receipt nor its old gate is rewritten.

The [final-attempt declaration](native-book-12h-final-declaration.json) uses only **attempt 3 of the existing 3**, with the original deadline of `2026-10-22T03:28:32.160295+00:00`. It does not create another three-attempt budget or renew 48 hours. The corrected parser admits only the exact documented `64008` control envelope, without changing the book, sequence or coverage clock. Normal liveness/sequence guards remain active until actual closure. Unknown or malformed notices still stop; a disconnect during this final attempt cannot start a fourth. Forty-three offline checks passed locally and on Apollo, and the updated desktop path received a simulated failure acknowledgement. Native files and dated checks remain under ignored `reports/native-book-12h-final-attempt-001/`. Full quality review and any financial study are still separate gates.

Attempt 3 then disconnected at `2026-10-20T16:10:08.357166+00:00` with abnormal closure `1006`, after **53m 44.097s** of audited source time. No maintenance notice preceded this disconnect. Code `1006` does not identify whether the venue, network or client caused it; the old collector did not retain the underlying socket exception. Its files, failure and desktop-server acknowledgement remain preserved. The three-connection allowance was exhausted, so collection stopped at **10h 57m 49.095s**, short by **1h 2m 10.905s**.

The user then approved five additional connections and requested redundancy and automatic recovery. The [redundant-capture declaration](native-book-redundant-declaration.json) permits connections **4–8**, with at most **two live streams**. Each has its own native snapshot, sequence chain and files. Independently audited source intervals are combined by **union**, not by adding overlapping durations. Failed connections are replaced after an audited prefix and a five-second pause; the other stream can continue. All 134 earlier closed-file hashes match. The original deadline and 1.5 GB per-connection cap remain; there is no ninth connection or renewed 48-hour period.

The Apollo user service has a 12-second independent systemd watchdog and bounded automatic restart. A recovering supervisor verifies Linux PID/start-time and command ownership before adopting healthy workers. Durable slot reservations and file locks prevent duplicate controllers or reused connection numbers. Confirmed process loss can recover only after sealed-prefix validation; unsealed tails stay excluded. The local desktop relay can start only this owned service when an active job's heartbeat is stale. Unknown exits, validation, access, storage, clock and limit faults still stop all streams. New diagnostics retain the underlying transport exception when available.

Fifty-one offline checks passed on each host. Real subprocess fixtures killed one stream and verified replacement without losing the other, then killed the supervisor and verified adoption of the same surviving processes without extra connection slots. The initial development failure remains in verification history. Real service-watchdog and first sealed dual-stream checks are startup gates before PR approval. Both connections still share Apollo, its network and the public endpoint; this is not independent-host or network redundancy. Apollo has active user sessions but no linger, and no logout/reboot persistence or host-policy change is claimed. Dated evidence stays in ignored `reports/native-book-12h-redundant-attempt-001/`; no financial or inference permission is renewed.

## Execution study after the data gate

Freeze a short execution-only comparison after the data-quality review and before its financial outcomes. Apply the same native-data execution assumptions to both control and challenger. Do not compare a new optimistic executor with only the old synthetic adverse executor.

First investigate a liquidation-capacity bound alongside existing risk, cash, order and exposure limits. Current bid depth is not a guarantee of future exit capacity; cancellations, latency, market impact, quote age and already-consumed quantities need conservative treatment. Quantity below native minimums means no entry. Distinguish reduced notional and risk from improved per-trade expectancy. Do not spend the same observed liquidity twice when considering faster reductions.

Keep $1,000 per independent case, both 5m/15m intervals and all eight setups. Planned stop risk remains at most 1% of `min(initial capital, current equity)`, with fees/dust reserved. Preserve the $12.50 UTC daily entry-loss latch, four-hour deadline, partial ownership and retained inventory. Planned risk is not a guaranteed realized loss cap. Protective reductions remain allowed while entries are halted; no latch reset or fictional terminal sale.

The existing research engine attempts protection at most once per execution minute. That is a declared model/policy feature, not evidence of an OKX processing delay. Event-driven reduction can be investigated separately from signal changes, without assuming replenishment after every observed update.

The existing `OKX._validate_write` allows cash IOC orders, demo post-only orders and exact owned-order cancellation. Its fixed field/endpoint allowlist excludes attached algo fields and `/trade/order-algo`. No application change is made here. Continue bot-managed protection unless a separately authorized integration reconstructs the complete native lifecycle.

[OKX documents TP/SL and OCO capabilities](https://us.okx.com/docs-v5/en/#order-book-trading-algo-trading-post-place-algo-order), but attached algo creation has a parent-full-fill caveat. A partially filled then cancelled entry needs explicit protection of net acquired BTC after received-currency fees. Generic `conditional` TP+SL parameters are not equivalent to OCO. Account support, acknowledgement, partial fills, cancellation races, residual quantities and disconnect reconciliation are untested; no native order was submitted.

Only after execution feasibility is established should one interpretable cost-conditioned entry challenger be declared. Preserve the other setups and compare against the unchanged baseline and cash using deployable, ex-ante controls. Fit only on training data, retain all candidates and purge overlapping label/exit information. Do not reuse future-total matched suppression as a causal randomized policy. No threshold, feature sweep or financial winner is selected in this acquisition stage.

## Boundaries inherited from the completed work

The [partial-AI quarter](ai-entry-filter-quarter.md) remains incomplete coverage with no declared conservative improvement. Total model calls remain 840; active inference allowance remains zero. Recording public market data does not renew either stopped allocation.

[Anchored retirement](anchored-retirement-validation.md) remains provisional: 13 closed episodes per mode failed the frozen minimum of 20. All eight variants stay in the research set. The saved strategy losses, realized risk overruns, inactive adverse maker path and malformed native answer are unchanged.

The earlier [strategy improvement plan](vwap-fibonacci-improvement-plan.md) and literature reviews remain development evidence, not validated production parameters. No new profitability, full-quarter AI coverage, guaranteed protection or deployment claim follows from the data checks in this document. Browser/canvas visual review remains unverified.
