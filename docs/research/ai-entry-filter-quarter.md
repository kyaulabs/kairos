# AI entry-filter full-quarter comparison

The February–April allocation stopped at its first protocol failure after **582 fresh calls: 581 accepted and one rejected malformed input hash**. Together with 179 reused pilot answers, 760/7,895 candidates are accepted. The 7,134 unattempted slots closed. A separately approved continuation never started; the user replaced it with [strategy diagnosis](quarter-strategy-diagnosis.md). Cumulative research usage is 840 calls, unresolved turns zero, and active inference allowance zero. No saved answer was repaired or retried.

The [quarter declaration](ai-entry-filter-quarter-declaration.json) freezes the inputs, code, reuse provenance, batch plan and evaluation before new quarter inference or AI-filtered financial outcomes. The [two-day pilot](ai-entry-filter-pilot.md) remains unchanged and demonstrated no incremental AI value in its own window.

## Fixed scope

- February 1 through April 30, 2024 UTC; May 1 is exclusive. Two native-bar warmup days are not scored. This repeatedly inspected quarter is development data, not a holdout; model-pretraining contamination remains possible.
- All 8,138 new arms from the accepted quarter signal source: 7,895 contract-valid candidates and 243 preserved target-already-passed exclusions. No selection on confirmation, fills or returns.
- The first 179 frozen inputs match the entire saved v3 pilot cohort exactly, including prices, clocks and hashes. Reuse every native answer and measured latency without repair. These are repeated observations, not another 179 calls or independent evidence. The remaining 7,716 inputs are assessed in chronological `(as_of, minutes, setup)` order.
- Unchanged official `openai-codex==0.162.1`, `gpt-6.1-sol`, high effort, ChatGPT-only authentication, denied tools and isolated ephemeral threads. No Platform billing or credential copying. Acquisition times differ; a model alias is not an immutable backend snapshot.
- One local SDK client at a time, in 44 sequential batches (43 × 179, then 19). The existing 179-request SDK safety ceiling is unchanged. It does not grant another allocation per batch: the quarter launcher enforces the single 7,716-call total and refuses an existing launch marker.

Stop the entire allocation at the first inference/protocol failure, including a startup, cleanup or quota error. Do not retry or resume a failed batch. Close unused slots and preserve any unresolved in-flight turn separately; missing usage is not zero and client closure does not prove backend generation stopped. At full consumption, cumulative research usage would be 7,974 invocations, including the 258 already spent, not counting reused answers twice.

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

The already-running 1,216-case financial comparison is a **partial-coverage** study. Failed and unattempted inputs fail closed in availability, AI and suppression. It is not complete quarter AI coverage, and lower activity cannot establish full-quarter opinion quality. Preserve its final or failed receipts without restarting inference. The earlier two-day result does not establish its outcome.

Open repository-root `reports/index.html` for baseline/comparison reports and related test receipts. The quarter group links its offline operational status page at `reports/ai-entry-filter-quarter-attempt-001/progress.html`. That page reports saved fresh-call records separately from the 179 reused answers. The local page may say replay pending until Apollo's final evidence is retrieved; it is not a continuously synchronized remote counter. Observation times are not model receipt times or research completion dates.

The existing navigation/chart checks remain structural/data checks. Real-browser canvas review is unverified. No permission to promote or deploy follows from finishing this development comparison.
