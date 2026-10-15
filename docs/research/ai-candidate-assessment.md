# AI candidate critique and second opinion

This replaces generic regime/quality annotations with one setup-specific assessment containing both a thesis critique and a bounded `permit`/`abstain` recommendation. It defaults to offline preparation. After mocked tests, an auth-only SDK check recognized the existing ChatGPT session without starting a model turn or login. A separate [16-invocation pilot](ai-assessment-pilot.md) is now approved and predeclared; the previous 62-invocation allocation remains exhausted. No trading result, entry veto or deployment is authorized.

The old annotations did not assist trading: neither model changed a signal, fill or protective action. Higher agreement with a deterministic reference did not measure prediction accuracy or incremental return. Their source, prompts, failures and reports remain historical evidence; they are not this new experiment's baseline results.

## Python integration

Use OpenAI's official **`openai-codex==0.162.1`** Python SDK, imported as `openai_codex`, through the optional `codex-research` extra. Existing project dependencies cannot provide its typed app-server protocol and subscription authentication. The SDK installs a matching runtime, so the research module no longer constructs `codex exec` commands or parses CLI stdout. The SDK still manages a local Codex app-server process over JSON-RPC; this is not process-free HTTP access.

One `AsyncCodex` client serves a batch, with a fresh ephemeral thread for each candidate. Do not reuse a conversation across candidates or expose later outcomes as context. Reuse the existing ChatGPT sign-in through the SDK, require the returned account type to be `chatgpt`, and never read/copy tokens, start login automatically or fall back to API-key billing. SDK environment overrides blank nonessential inherited values because the SDK merges, rather than replaces, the process environment.

Set explicit research `base_instructions` and setup-specific `developer_instructions`, a strict output schema, read-only sandbox and deny-all approval mode. Disable shell, patch, browser, image, code, memory, plugin, hook and skill tools; override MCP servers to empty and suppress project instructions. Do not rely on the SDK's default approval mode. Preserve returned structured turn output/usage privately and stop on a failed/incomplete turn, tool/action item, refusal, invalid answer or deadline. Request interruption on timeout/cancellation and close the SDK client; a request is not proof that the backend stopped or charged no usage. Partial usage can remain unknown when a turn times out. No application retry or alternate model/provider follows a failure.

Instructions can replace irrelevant coding-agent context, but lower tokens/latency are not yet measured. The old task input was about 2,680 characters while reported input usage averaged roughly 10–11 thousand tokens per call. Do not assume moving to the SDK eliminates all runtime/provider context.

## One assessment, two roles

The model critiques three aspects of the supplied setup, each as `support`, `oppose` or `unknown`, with a short reason and references to actual input fields:

| Setup | Context | Structure | Location |
| --- | --- | --- | --- |
| VWAP pullback | Orderly retracement versus transition lower | Observed VWAP pullback/recovery, not a touch alone | Extension away from the pullback region |
| VWAP reclaim | Reclaim versus repeated chop | Coherent reclaim versus isolated spike | Displacement from VWAP and nearby structure |
| Anchored VWAP | Context relative to the fixed confirmed-low anchor | Breakout/retest thesis and visible failed attempts | Extension from supplied anchored VWAP |
| Lower-band reversion | Mean recovery versus persistent decline | Rejection/recovery, not a band touch | Location versus VWAP/bands, without Gaussian claims |
| Fib confluence | Swing/context fit for long recovery | Recovery around the frozen retracement/VWAP zone | Remaining observed room toward the swing high |
| Fib EMA | Continuation versus deterioration | Retracement recovery without adding an EMA-recapture rule | Extension toward the swing-high target |
| Fib extension | Continuation versus exhaustion | Behavior around the known high, not future acceptance | Supplied 1.618 room without moving the target |
| Fib sweep | Recovery versus continuing breakdown | Observed sweep/reclaim, not hidden-stop inference | Location versus the frozen low/target |

`permit` is consistent only when all three checks support the thesis; any opposition or unknown requires `abstain`. Both roles share one response, not two calls. This is a declared research rubric, not a proven profitable filter or permission to submit an order. References are checked for existence and non-null scalar evidence, not semantic truth; a model can still misinterpret valid data.

## Point-in-time inputs

`research_assessment.snapshot()` prepares an **arming-time** snapshot from `Signals.pending` and completed rows. It includes candidate identity, source hash, expiry, the preliminary plan, whitelisted arming facts and at most 32 completed bars. The input is bounded to 16 KiB, not a promise about the provider's total token context. Missing minutes remain gaps. Future candles/pivot knowledge, nested/free-text facts, nonfinite values and extra snapshot fields are rejected. The schema contains no balances, quantities, fills, results or credentials.

This reviews a candidate worth monitoring, not an already confirmed trade. Future confirmation is not missing historical evidence. Some targets are unknown at arming and stay null; neither model nor caller may invent a future target. Additional features such as anchored VWAP must be captured when known, not reconstructed from later diagnostics. Old confirmation/next-open payloads are not suitable inputs for this arming-time contract.

The response echoes candidate identity and a canonical input hash. Code retains all authority over confirmation, current quotes, cost eligibility, sizing, ownership, stops/targets, deadlines, halts and ambiguous-write recovery. The present module never passes a recommendation to an executor. Any later experimental consumer must reject late or invalidated assessments, recheck the current candidate/rules, and never backdate a reply into a missed next-open entry. Owned reductions must never wait for the model.

## Preparation and evaluation boundary

Prepare a declared JSON list of snapshots, then run without the inference-approval flag:

```sh
uv run python -m kairos.research_shadow \
  --snapshots /path/to/arming-snapshots.json \
  --max-invocations 1 --output-dir /path/to/new-preparation
```

Default preparation creates private declaration/schema/request data and a `prepared_no_inference` summary without importing or starting the SDK. Duplicate candidates, excess batches and existing output directories are rejected rather than silently resampled or overwritten. The synthetic unit-test fixtures exercise the protocol; they are not real trade examples or model responses.

Inference needs the `codex-research` extra and `--approve-subscription-usage`, plus a separately approved model/effort, invocation budget and frozen sampling/evaluation plan. Do not run it under an exhausted allocation. The new pilot has its own 16-call allowance. Real SDK startup and ChatGPT account recognition passed on 2026-10-15 UTC without model turns; model behavior is not yet tested. Unit tests use an injected fake SDK, and the installed SDK's public method signatures are also checked.

Before an economic experiment, compare unchanged deterministic controls with this challenger and a predeclared activity-matched suppression control. Score net outcomes, missed opportunities, activity, costs, retained holdings, latency, abstentions and risk failures; fewer trades alone are not success. Preserve all eight setups at both intervals. Historical data/pretraining contamination remains a limitation; prospective shadow observations require separate authorization. No profitability or promotion follows from this contract.

Sources checked 2026-10-15: [official SDK](https://developers.openai.com/codex/sdk/#python-library), [Python getting started/authentication](https://github.com/openai/codex/blob/main/sdk/python/docs/getting-started.md), [API reference](https://github.com/openai/codex/blob/main/sdk/python/docs/api-reference.md), [authentication](https://developers.openai.com/codex/auth/), and [published package](https://pypi.org/project/openai-codex/0.162.1/). Acquisition receipts and the verified wheel are preserved privately. These sources supersede the earlier assumption that subscription-backed Python integration requires hand-launching `codex exec`; the ordinary `openai` API SDK remains a different authentication/billing path.
