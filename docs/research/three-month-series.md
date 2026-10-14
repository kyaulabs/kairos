# Three-month improvement series

The user requested a three-month #0 baseline followed by one-at-a-time implementations, retaining improvements only and archiving every HTML report. This supersedes the short-pilot default for this series. No deployment, restart, exchange orders or inference is authorized.

## Frozen window and controls

Use February 1–exclusive May 1, 2024, centered on March. Selection uses native daily prices and timestamp coverage, not strategy outcomes. Rank complete months by daily log-return volatility, requiring at least eight up days, eight down days and six direction changes. Choose the highest-ranked centered three-calendar-month window with at least 80% observed minute coverage and 99% native 5m/15m coverage.

March 2024 has 18 up days, 13 down days, 16 direction changes and 3.8193% daily log-return standard deviation. More volatile candidates failed coverage: the March 2020 window has 10.29% execution-minute coverage; April–June 2021 has 72.89%. Preserve the full ranking and each rejection. This is deliberately volatile development data, not representative history or an untouched holdout; every later comparison reuses it for selection.

The selected ZIP is 2,645,216 bytes, SHA-256 `380165e326961eeddd41dcedd4a6e623479fcd093ed43deaaaa74678e4c63d78`. Minute coverage is 105,391/129,600 (81.3202%); native 5m/15m coverage is 99.1590%/99.6875%. Missing data is not interpolated or assumed downtime. The preceding execution minute is absent; do not invent its liquidity. Native 5m/15m warmup remains two days. A separate seven-day native 60m warmup supports the context factor without changing the existing indicators' warmup.

Keep all eight setups at 5m/15m, $1,000 per independent case, ≤1% planned stop risk on the smaller of initial/current equity, assumed fees, cash IOC entries, bot-managed protection, ownership/dust reserves, the $12.50 daily entry-loss latch and four-hour deadline. No automatic restart, risk compounding, clipped loss or terminal liquidation. Kraken candles remain cross-venue proxies for OKX U.S. spot; historical books, fees and instrument rules are unverified. A three-month case stays halted if its entry latch trips; report subsequent inactivity rather than resetting it.

## Ordered experiments

`python -m kairos.research_series` first runs both existing economic policies for #0. Subsequent steps use positive-target eligibility and the latest accepted control. Each step reruns all 16 cases for the full window; the accepted control is reused from its archived full-window run. Profiles are generated only after the preceding result is complete. Settings, coverage, causal timestamps, requested risk and unaffected-case equality are checked.

| Item | Separate steps |
| --- | --- |
| #0 | Existing diagnostic versus positive-target entries; no new signal rules. |
| #1 | Pullback recovery without extra higher-low/micro-break; completed reclaim arming candle; band recovery without extra higher-low; confluence recovery; Fib EMA without mandatory EMA20 recapture; sweep recovery. Each is a separate factor and report. |
| #2 | Fib extension volume, 0.1-ATR acceptance distance and retest ablations, separately. |
| #3 | Fixed generic retracements (0.350/0.586, same zone width), unweighted mean, and simple bar-sign reversal conditioned on the same known structural/mean context. These are controls, not replacements for the named strategies. |
| #4 | Descriptive regime/hour/level-age/prior-volume strata, then a separate last-completed native 60m EMA20/50 context for Fib EMA. No fitted subgroup gates. |
| #5 | Two-ATR close-based trailing protection for trend-following setups only after a completed favorable move exceeding two ATR. Never lower the stop, stretch the target, change initial sizing or extend the deadline. |
| #6 | Existing adverse execution scenario applied to #0 positive-target and the accepted stack. This is robustness evidence, not a signal change. |
| #7 | Deferred until separate approval of supported subscription/OAuth shadow usage. No calls or credential reads in this series. |

Code inspection found anchored VWAP already confirms the first completed valid retest/rejection. Do not invent a redundant “earlier retest” change. Anchor age/contact evidence remains available for analysis. The earlier Fib EMA higher-low alternative was rejected and is not silently retried.

A candidate is carried forward only if positive-target fills and active case-days do not decline, zero-fill cases and risk breaches do not increase, and both mean independent-case marked return and pooled mean closed R improve. Keep rejected changes and their sources. Generic controls, descriptive analysis and stress tests do not replace the eight setups even if a reference wins. No threshold grid or annualized significance claim. Improvement here is a development selection, not established alpha.

## Archive and AI boundary

Apollo remains isolated under `/srv/kairos-research/kyau-vwap-fib`. Transfer only the selected ZIP, receipts and frozen code. Each step retains paired branded HTML/JSON, profile sources, parent/source hashes, selection, runtime and failures. Refuse an existing output directory rather than overwrite a prior attempt. Preserve partial reports on failure; use a newly declared output directory for a retry.

The first attempt completed #0 but failed final HTML rendering because the paired adapter omitted signals/active-days fields. The second completed the first timing challenger but failed comparison of JSON list rules with in-memory tuples. A third comparison then exposed genuine unchanged-case financial differences: the research executor used wall-clock fee-cache expiry without refreshing during the historical loop. CPU runtime could block entries and owned reductions. All results from these attempts are invalid strategy evidence, even where individual cases completed. Preserve their HTML, JSON, logs and source hashes; do not select a strategy from them.

The clock repair injects simulated time into the existing fee-cache checks and refreshes the constant assumed fee scenario at each observed execution minute. Expiry, reversal and failure guards remain enforced; production keeps its default wall clock. Restart #0 and every challenger from fresh independent accounts under the repaired common executor. This is an execution correction, not a signal improvement or a retuned threshold.

OpenAI's public Codex authentication guide distinguishes ChatGPT sign-in from Platform API-key billing. Subscription authentication is supported for Codex clients; it does not establish subscription access to the general Responses or Decisions endpoints. Verify a documented Codex client route before the AI stage, ask for usage approval, and let the official client manage OAuth. Do not extract tokens or call undocumented consumer endpoints. The ordinary billing-help request returned HTTP 403; no access controls were bypassed. Historical AI assessment also has pretraining and response-timing contamination risks and cannot be backdated into next-open fills.

Source plans and selection receipts were recorded before strategy outcomes. The corrected series completed 17 paired reports in 5,092.27 summed step-wall seconds. Each candidate replayed all 16 independent cases over the fixed quarter; unchanged-family financial equality and common execution controls passed. Baseline signal files and production strategy registration remain untouched.

## Results

| Stage | Carry forward | Positive-target fills | Mean case return | Pooled closed R |
| --- | --- | ---: | ---: | ---: |
| #0 diagnostic reference | Existing reference | 677 of 1,145 total fills | −3.4096% | −0.4786R |
| #0 positive-target control | Fixed research cost boundary | 693 | −2.8427% | −0.4386R |
| 1a pullback recovery | No | 692 | −2.8553% | −0.4406R |
| 1b reclaim closed arming candle | No | 755 | −3.5387% | −0.4545R |
| 1c band recovery | No | 702 | −2.8748% | −0.4403R |
| 1d confluence recovery | No | 702 | −2.8526% | −0.4402R |
| 1e EMA20 recapture ablation | Base development only | 696 | −2.8297% | −0.4365R |
| 1f sweep recovery | No | 698 | −2.8572% | −0.4374R |
| 2a extension volume ablation | No | 770 | −3.0952% | −0.4320R |
| 2b extension acceptance-distance ablation | Base development only | 698 | −2.8181% | −0.4345R |
| 2c extension retest ablation | No | 701 | −2.8606% | −0.4352R |
| 3a generic retracements | Evidence control, not replacement | 694 | −2.8219% | −0.4315R |
| 3b unweighted mean | Evidence control, not replacement | 787 | −2.9463% | −0.4624R |
| 3c simple reversal | Evidence control, not replacement | 634 | −2.5602% | −0.4227R |
| 4a context strata | Descriptive; unchanged execution | 698 | −2.8181% | −0.4345R |
| 4b completed 60m trend | No | 686 | −2.8258% | −0.4406R |
| 5 favorable-move ATR trail | No | 704 | −2.8376% | −0.4359R |
| 6 adverse original / accepted stack | Robustness criterion fails | 372 / 376 | −2.0092% / −2.0112% | −0.5137R / −0.5118R |

Only 1e and 2b met the declared base-execution criteria, in that order. The combined gain is 0.024545 percentage points of mean case return, five positive-target fills and three active case-days (404→407). Fifteen modeled loss-budget breaches and seven halted cases remain. All requested planned risk stays within the ceiling; observed losses are not capped. The existing positive-target boundary itself increases quarter breach count from 13 to 15 versus diagnostic, so the short-week evidence is not a general risk improvement.

The accepted stack's adverse marked return worsens slightly despite improved pooled R. Retain its exported sources as development candidates, not a robust or profitable strategy. Simple reversal improves mean return/R but reduces valid activity and increases halted cases; it fails the carry-forward criterion. Unweighted means increase activity but worsen net outcomes. Trailing protection reduces breaches from 15 to 13 but worsens return/R. None is silently selected. Context strata describe dependence, not fitted gates or independent observations.

The user then approved at most 32 subscription shadow invocations. The first was rejected before inference because GPT-5.4 is retired for ChatGPT-authenticated Codex. The second used the documented GPT-6 Luna alias and returned a valid annotation, but the observer mistook a disabled-tool startup warning for a tool action. Both attempts and raw responses remain archived. After separating warnings from actions, a newly frozen 30-invocation attempt completed all 30; total invocations across attempts are 32. No API keys, API billing, token extraction, model tools or order authority were used. The official client manages its existing OAuth session.

The final annotations agree with the simple deterministic regime reference in 10/30 cases and room-quality reference in 23/30. Agreement is not accuracy, calibrated probability or demonstrated economic value. One completed response was repeated across attempts; these are not independent observations. GPT-6 Luna is a model alias, not an immutable snapshot. Historical timestamps, reused development data and possible pretraining contamination prevent prospective inference claims. No model veto is selected.

After the shadow stage, all 16 accepted-profile financial cases were rerun over the same quarter and reproduced their archived financial/control fields. Runtime was 265.81 seconds. Shadow results are never backdated into next-open fills. This verification has its own branded HTML/JSON; the annotated shadow report adds no trading result.

[Three-month outcome](three-month-outcome.json) records every aggregate, parent, report/source hash, invalid attempt and shadow receipt. [Experiment history](experiment-history.json) links the selection. The local archive is `/home/kyau/.local/share/kairos-research/vwap-fibonacci/quarter-2024-feb-apr/`; Apollo reports remain under `/srv/kairos-research/kyau-vwap-fib/reports/`. Reports are protected local files, not committed research data.

The subsequent user-requested [Sol high shadow rerun](sol-high-shadow.md) completed the same 30 inputs with `gpt-6.1-sol` at `high`. Reference agreement rose to 16/30 for regime and 25/30 for room quality; latency and token usage increased. All 16 financial controls again reproduced unchanged. Both model and effort changed, so this does not isolate either factor or establish accuracy, profitability or a trading veto.
