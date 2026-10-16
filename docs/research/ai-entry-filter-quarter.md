# AI entry-filter full-quarter comparison

The February–April allocation stopped at its first protocol failure after **582 fresh calls: 581 accepted and one rejected malformed input hash**. Together with 179 reused pilot answers, 760/7,895 candidates are accepted. The 7,134 unattempted slots closed. A separately approved continuation never started; the user replaced it with [strategy diagnosis](quarter-strategy-diagnosis.md). Cumulative research usage is 840 calls, unresolved turns zero, and active inference allowance zero. No saved answer was repaired or retried.

The [quarter declaration](ai-entry-filter-quarter-declaration.json) freezes the inputs, code, reuse provenance, batch plan and evaluation before new quarter inference or AI-filtered financial outcomes. The [two-day pilot](ai-entry-filter-pilot.md) remains unchanged and demonstrated no incremental AI value in its own window.

## Fixed scope

- February 1 through April 30, 2024 UTC; May 1 is exclusive. Two native-bar warmup days are not scored. This repeatedly inspected quarter is development data, not a holdout; model-pretraining contamination remains possible.
- All 8,138 new arms from the accepted quarter signal source: 7,895 contract-valid candidates and 243 preserved target-already-passed exclusions. No selection on confirmation, fills or returns.
- The first 179 frozen inputs match the entire saved v3 pilot cohort exactly, including prices, clocks and hashes. Reuse every native answer and measured latency without repair. These are repeated observations, not another 179 calls or independent evidence. The remaining 7,716 inputs are assessed in chronological `(as_of, minutes, setup)` order.
- Unchanged official `openai-codex==0.162.1`, `gpt-6.1-sol`, high effort, ChatGPT-only authentication, denied tools and isolated ephemeral threads. No Platform billing or credential copying. Acquisition times differ; a model alias is not an immutable backend snapshot.
- One local SDK client at a time, in 44 sequential batches (43 × 179, then 19). The existing 179-request SDK safety ceiling is unchanged. It does not grant another allocation per batch: the quarter launcher enforces the single 7,716-call total and refuses an existing launch marker.

Stop the entire allocation at the first inference/protocol failure, including a startup, cleanup or quota error. Do not retry or resume a failed batch. Close unused slots and preserve any unresolved in-flight turn separately; missing usage is not zero and client closure does not prove backend generation stopped. The original full-consumption ceiling was 7,974 cumulative invocations, including the 258 previously spent. Actual usage stopped at 840; reused answers were not counted twice and no allocation remains active.

At the pilot's observed pace, new inference projects to about 66 sequential hours and 74 million native reported tokens. This is not a quota guarantee or a dollar price. Actual counts, failures and latency determine the report.

## Financial comparison

Reproduce all 64 archived accepted-quarter IOC/maker baseline cases on isolated Apollo before new inference. Then replay the saved cohort in 1,216 comparison cases: 16 setup/timeframe accounts × four execution/scenario combinations × 19 arms. Total simulations including the pre-inference reproduction are 1,280 alternative paths on one quarter, not independent histories or a portfolio. No model inference runs on Apollo.

The [pilot methods](ai-entry-filter-pilot.md#financial-comparison) remain fixed: unchanged baseline, response availability, AI entry veto, primary suppression seed 00 and all 15 additional SHA-256 seeds. Suppression matches per-case permitted-arm totals, not fills or exposure. Conditioning on future case totals makes these nondeployable conditional references, not causal randomized policies or p-values. No best seed is selected.

Each independent account starts at $1,000. Keep positive-target economics, native cash/lot/dust limits, planned stop risk at most 1% of `min(initial, current equity)`, the $12.50 UTC entry-loss latch, four-hour deadline and owned protection. Maker entries remain best-bid post-only with a 30-second lifetime and strict later crossing; exits remain bot-managed taker IOC. No retiming, target/stop changes, missed-entry catch-up, halt reset or fictional terminal liquidation.

Availability remains arm close + one second assumed candle delivery + measured request-to-receipt latency, independently per case. No historical OKX books, queues, fee histories, venue delivery times or shared inference queue are established by Kraken candles and current instrument metadata.

Save all candidate/input/raw-response bindings, actual and unknown tokens/latency, native fee roles, risk reserves, partial BTC, funding/latch paths and missed opportunities. Audit frozen arm/confirmation plans across every arm and seed. Native opinions stay unchanged; structural validity does not establish semantic truth.

Report marked net returns, sold-share closed R, actual fills/days/exposure, fees, retained BTC and risk breaches/halts. Apply the unchanged conservative descriptive improvement rule against baseline, availability and primary suppression, and retain every sensitivity result. Reduced activity, undefined R and inactive adverse maker cases cannot establish robustness. Trading costs do not include an invented subscription-inference dollar charge.

## Status and evidence

The original declaration recorded zero new calls before execution; it remains historical. All 64 quarter controls reproduced before inference and all 31,580 snapshot/path bindings passed. Inference ended on October 15, 2026 at 23:54:54 UTC, with all 582 usage records known: 5,516,010 reported tokens, including the failed turn. Cached input and reasoning output are subsets, not additional tokens.

The failed native transport completed but echoed a different `input_sha256` for `vwap-anchored:1707545400`. The application rejected it with `Assessment bound to a different candidate/snapshot`, regardless of its `permit` opinion. The original response remains unchanged. This protocol failure is not a financial-performance finding.

All **1,216 partial-coverage comparison cases completed at 2026-10-16 04:44:38 UTC**. All 64 archived controls reproduced again; frozen plans, native roles, availability, ownership paths, risk reserves and all sixteen conditional references passed the final audit. Failed and unattempted inputs remain unavailable. This is completed financial processing of an incomplete AI cohort, not complete quarter AI coverage. The [outcome receipt](ai-entry-filter-quarter-outcome.json) binds the results and artifacts.

| Path | Arm | Fills | Active case-days | Mean marked account return | Pooled closed R |
| --- | --- | ---: | ---: | ---: | ---: |
| IOC/base | Baseline | 698 | 407 | −2.818141% | −0.4345 |
| IOC/base | Availability | 53 | 33 | −0.051226% | −0.3231 |
| IOC/base | AI | 14 | 10 | −0.034703% | −0.2658 |
| IOC/base | Suppression 00 | 13 | 13 | −0.041647% | −0.1893 |
| IOC/adverse | Baseline | 376 | 255 | −2.011230% | −0.5118 |
| IOC/adverse | Availability | 13 | 11 | −0.039578% | −0.2320 |
| IOC/adverse | AI | 4 | 4 | −0.006671% | −0.1354 |
| IOC/adverse | Suppression 00 | 2 | 2 | +0.000034% | +0.3261 |
| Maker/base | Baseline | 413 | 310 | −1.402256% | −0.4322 |
| Maker/base | Availability | 22 | 19 | −0.061442% | −0.3727 |
| Maker/base | AI | 4 | 4 | −0.064232% | −0.3563 |
| Maker/base | Suppression 00 | 5 | 4 | −0.037201% | −0.2259 |

All adverse-maker arms are inactive, with undefined closed R. Gated primary arms have zero observed budget breaches or loss halts, alongside sharply reduced activity. Independent accounts and alternative paths are not a portfolio or independent market histories.

**No conservative improvement rule passed** against baseline, availability or primary suppression. Base IOC AI has a better marked return than primary suppression but worse closed R and fewer active days. Maker AI has worse marked return than availability and every suppression seed. Against all sixteen conditional reference returns, AI is above nine/below seven in IOC/base, above four/below twelve in adverse IOC, and below all sixteen in maker/base. These are descriptive rankings, not p-values.

The accepted cohort contains 160 monitoring permits and 600 abstentions. Native structural validation does not establish semantic accuracy. Missing coverage and different exposure prevent attributing near-zero marks against the negative full baseline to full-quarter model selectivity. Opportunity differences are nonadditive because ownership, funding, sizing and latch paths differ.

The saved original analysis has one limitation sentence that calls the planned 7,716-call cap new spending. A reporting addendum corrects it to the actual 582 calls without rewriting native answers, original financial reports or numerical audits. No further calls or simulations were introduced by reporting. No incremental value meeting the declared rule was demonstrated in this partial-coverage study.

Open repository-root `reports/index.html` for baseline/comparison reports and related test receipts. The quarter group links its offline operational status page at `reports/ai-entry-filter-quarter-attempt-001/progress.html`. That page reports saved fresh-call records separately from the 179 reused answers. The local page may say replay pending until Apollo's final evidence is retrieved; it is not a continuously synchronized remote counter. Observation times are not model receipt times or research completion dates.

The existing navigation/chart checks remain structural/data checks. Real-browser canvas review is unverified. No permission to promote or deploy follows from finishing this development comparison.
