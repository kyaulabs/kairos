# SDK candidate assessment pilot

A fresh allowance of **at most 16 Sol/high invocations, including failures**, was approved after an auth-only check confirmed that the official Python SDK recognized the existing ChatGPT session. That check made zero model turns and started no login. The earlier 62-call allocation remains exhausted; this is a separate allowance.

This pilot checks whether the [combined thesis critique and second opinion](ai-candidate-assessment.md) returns valid, grounded assessments. It does not test profitability or authorize trading. No recommendation reaches an executor and no financial replay is permitted.

## Frozen procedure

The [pre-inference declaration](ai-assessment-pilot-declaration.json) fixes the inputs, source hashes, sampling, instructions and evaluation before any new model call:

- Use `gpt-6.1-sol`, high effort, through `openai-codex==0.162.1`. These are requested alias/settings, not an immutable backend snapshot.
- Take the first new arm at or after February 1, 2024 UTC for each of eight setups at 5m and 15m that passes the frozen input contract. Process native bars chronologically with the original two-day warmup. Do not select on confirmation, orders, fills or outcomes. There were no input exclusions.
- Use the archived quarter attempt004 accepted signal source, retaining only its earlier EMA-recapture and extension-acceptance-distance ablations. The development window is reused, not a holdout.
- Capture up to 32 completed bars, frozen preliminary levels and same-time facts. Copy anchored VWAP from the signal state at arming for the anchored setup; do not reconstruct it from a later diagnostic. Unknown targets remain null and observed gaps remain gaps.
- Run in fixed `(minutes, setup)` order, one assessment per case. Use one managed SDK client with separate ephemeral threads, ChatGPT-only authentication, disabled tools and explicit base/developer instructions. Stop at the first failure, without application retry, replacement candidate, model switch or fallback.
- Keep all original financial reports and controls unchanged. Signal-only preparation imports no executor and does not simulate accounts, orders, protection or P&L.

The 16 request JSON inputs range from 4,462 to 4,724 bytes, median 4,651. This excludes trusted instructions, schema and provider context and is not a token estimate. There is one development-data example per case; the 16 histories are not independent market observations.

## Audit and limits

Require completed, schema-valid responses bound to their candidate and snapshot hash. Audit each returned context/structure/location reason against the cited scalar fields. Structural reference validity does not establish semantic truth. Record unsupported claims, missing evidence, contradictions and gap/context limitations rather than calling plausible prose accurate prediction.

Report permit/abstain counts without imposing a favorable recommendation quota. Report returned native usage and request-to-receipt latency, keeping cached-input/reasoning subsets separate from totals. A timeout may leave usage unknown. Neither lower tokens nor fewer permitted candidates proves usefulness.

This is an arming-time retrospective prompt test. Real request/receipt timestamps describe the pilot, not 2024 decisions. A virtual expiry comparison may assume bar availability at boundary plus one second, but cannot establish that a candidate remained valid or authorize a backdated fill. The old generic annotations used a different task, stage, inputs and runtime context, so this is not a paired performance comparison with those calls.

Before financial evaluation, freeze an independent experiment with unchanged deterministic and activity-matched suppression controls, current eligibility checks and honest response timing. Prospective observation and trading integration require separate authorization. Model pretraining contamination remains possible in this historical pilot.

## Status

Attempt001 stopped after its first invocation. The SDK turn completed, but the application gate incorrectly classified the native echoed `userMessage` as a tool/action item. Independently, the model packed multiple evidence paths into three strings, so its answer would still fail reference validation. The raw answer is not repaired or counted as accepted.

That call took 30.081024 seconds and returned 8,067 input tokens, no cached input and 1,237 output tokens, including 809 reasoning tokens. Total reported tokens were 9,304; reasoning is not added again. Its raw abstention cited grounded observations, including target room of 24.4 versus ATR 28.45 and preliminary downside distance 59.3475, but one plausible explanation is not proof of trading value.

The 15 unused slots from that stopped allowance are closed. The user separately approved fixing the protocol and rerunning the full 16-case batch once. The [rerun declaration](ai-assessment-rerun-declaration.json) preserves the same inputs/order and records the repeated first candidate. Contract v2 accepts only a verbatim native input echo, still rejects tools/altered or duplicate echoes, and constrains each evidence item to one canonical scalar pointer. Both fixes have offline regression tests, including the real saved turn. The corrected batch has a fresh cap of 16 including failures and still stops on its first failure, without automatic retries.

The corrected batch completed **16/16 structurally valid assessments**. Two recommended continued monitoring (`vwap-pullback:5` and `fib-ema:15`); 14 abstained. Across 48 checks there were 24 support, 24 oppose and no unknown verdicts. These are recommendations about armed candidates, not executed trades or demonstrated improvements.

| Case | Recommendation | Seconds | Input / output tokens |
| --- | --- | ---: | ---: |
| `fib-confluence:5` | abstain | 37.434 | 8,103 / 1,605 |
| `fib-ema:5` | abstain | 31.698 | 8,102 / 1,188 |
| `fib-extension:5` | abstain | 41.045 | 8,112 / 1,779 |
| `fib-sweep:5` | abstain | 23.901 | 8,091 / 1,038 |
| `vwap-anchored:5` | abstain | 34.258 | 8,075 / 1,490 |
| `vwap-pullback:5` | permit | 24.394 | 8,034 / 1,061 |
| `vwap-reclaim:5` | abstain | 41.530 | 8,019 / 1,674 |
| `vwap-reversion:5` | abstain | 16.070 | 8,025 / 690 |
| `fib-confluence:15` | abstain | 24.088 | 8,112 / 1,161 |
| `fib-ema:15` | permit | 32.158 | 8,118 / 1,025 |
| `fib-extension:15` | abstain | 30.717 | 8,102 / 1,155 |
| `fib-sweep:15` | abstain | 32.895 | 8,125 / 1,334 |
| `vwap-anchored:15` | abstain | 51.224 | 8,103 / 2,117 |
| `vwap-pullback:15` | abstain | 38.786 | 8,045 / 1,666 |
| `vwap-reclaim:15` | abstain | 45.945 | 8,054 / 1,845 |
| `vwap-reversion:15` | abstain | 17.110 | 8,026 / 773 |

The rerun used 129,246 input tokens and 21,601 output tokens, including a 16,167-token reasoning subset, with no reported cached input. Total was 150,847 tokens. Median request-to-receipt latency was 32.526 seconds; nearest-rank p95 and maximum were 51.224 seconds. These are sequential request measurements, not live queue or opportunity-validity tests. Final response receipt was **2026-10-15 01:32:41.507840 UTC**.

Including the failed call, this pilot used 17 invocations and 160,151 reported tokens. Together with the prior 62 invocations, cumulative research usage is 79 invocations. The rerun allowance is exhausted and the earlier 15 unused slots remain closed. Do not reuse them for further calls. Different tasks, inputs and provider contexts prevent a controlled token/latency comparison with the older generic annotations.

## Grounding findings

Both anchored-VWAP context critiques falsely said the anchor low was immediately undercut. The model matched an anchor **candle-open timestamp** to a bar's **close timestamp**, selecting the preceding candle. In both cases the actual anchor low was 41,910.6 and the minimum later supplied low was 41,939.1, so the alleged undercut did not occur. This exposes ambiguous input-clock labeling as well as a model interpretation error. Valid references alone did not catch it.

One anchored 5m explanation also ended mid-sentence at the 240-character limit. Historical VWAP/AVWAP series were absent; some critiques acknowledged that limitation while comparing earlier prices with the current supplied reference. Those comparisons do not prove past indicator crossings. No unknown verdicts, despite admitted information gaps, is not evidence of calibrated certainty.

The [outcome receipt](ai-assessment-pilot-outcome.json) records both attempts, usage, cases, source hashes and audit findings. Full requests, raw SDK turns and a readable evidence-by-evidence review are preserved privately. Nothing is repaired post hoc or counted as financial assistance. Before any further pilot, make candle-open, candle-close and pivot-known-at semantics explicit, expose unambiguous anchor identity/price, and leave room for complete short explanations. No additional inference is authorized by this recommendation.

The offline follow-up, contract v3, now states the exact open-to-close timestamp mapping and distinguishes pivot discovery time. It also asks for one complete sentence below 200 characters within the existing 240-character hard bound. These instructions have not been tested against another model response, and no new fields or retrospective answer repairs were added. A future pilot must freeze that version and obtain a fresh budget.

Protocol reliability improved; semantic reliability and economic value remain unproven. Neither attempt changes trades, risk controls, deployments or the archived financial results.
