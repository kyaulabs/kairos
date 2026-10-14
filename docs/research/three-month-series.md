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

Source plans and selection receipts are recorded before strategy outcomes. Results are pending at this freeze. The [experiment history](experiment-history.json) records subsequent completion, rejected candidates and the final accepted research profile. Existing baseline signal files and production strategy registration remain untouched.
