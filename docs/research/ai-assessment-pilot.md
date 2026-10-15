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

The corrected batch is frozen but has not started. Neither attempt changes trades or establishes financial benefit.
