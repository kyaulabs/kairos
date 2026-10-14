# Sol high shadow rerun

The user requested `gpt-6.1-sol` with reasoning effort `high` after the completed [three-month series](three-month-series.md). This authorizes a new, bounded subscription shadow run, not exchange orders, deployment or a model trading veto.

## Declaration

Use the same first 30 inputs, in the same order, as `7-codex-shadow-attempt-003`. That completed run used `gpt-6-luna` at `low`. Preserve its prompt, schema, causal input whitelist and financial source report. The new configuration changes both model and effort; differences cannot be attributed to either factor separately.

The prior declaration SHA-256 is `2dd6ec9478e73f7b7586f2e1c1c2c483a800e8d1781e6bb7b3408e67e026c67e`. Its source report is `quarter-2024-feb-apr-attempt-004/2b-extension-acceptance.json`, SHA-256 `c6a5bbbb9bb890e83bdde4d82d0504732354feaf56f4ce63edb4b11a3956b019`. Verify exact inputs, prompt, schema and report hash before inference. The 30-input subset covers 15 of the 16 independent setup/interval cases; do not add the omitted case after seeing answers. The financial verification still retains all eight setups at both intervals.

Cap this rerun at 30 official Codex client invocations, including failures. Use the existing ChatGPT OAuth session, no API-key fallback or API billing, no extracted tokens and no automatic application retries. Keep the read-only sandbox, ignored user configuration, disabled tools, explicit schema and 120-second timeout. Stop on client failure, tool action, malformed/refused answer or incomplete turn. Archive failures and do not spend remaining quota on an undeclared replacement.

The official model guide lists `gpt-6.1-sol` for Codex with ChatGPT sign-in, subject to account availability. The configuration reference documents `model_reasoning_effort`. Pass `--model gpt-6.1-sol` and `model_reasoning_effort="high"` to the installed official client. Preserve client version, requested settings, source/input hashes, raw events, responses, usage and receipt times. A model alias and requested setting are not proof of an immutable backend snapshot.

## Evaluation

Report completed/failed invocations, latency, token usage, agreement with the same deterministic references, and paired categorical agreement/disagreement with Luna/low on matching inputs. Agreement is not accuracy, calibration, expectancy or alpha. Do not choose a veto or retune the rubric from these results. Inputs are dependent retrospective development observations; pretraining contamination and repeated exposure remain limitations.

After shadow, rerun the unchanged accepted financial profile over February 1–exclusive May 1, 2024 on Apollo. All 16 cases must reproduce their archived financial/control fields after the existing identity normalization. Responses cannot alter entries, sizing, structural levels, bot-managed protection, deadlines, ownership or loss latches, and cannot become backdated fills. Preserve negative returns, risk overruns, halts and retained BTC.

Archive a branded HTML comparison, JSON provenance and financial-verification report. Add the new reports and rendered Markdown results to the existing offline contents page without replacing earlier artifacts.

## Results

The new run completed all 30 invocations with `gpt-6.1-sol` and requested effort `high`; no failures, application retries or API-key fallback occurred. Input order, candidate IDs, prompt, schema and source-report hash exactly match the prior completed 30-input run.

| Measure | Luna / low | Sol / high |
| --- | ---: | ---: |
| Completed invocations | 30/30 | 30/30 |
| Regime reference agreement | 10/30 | 16/30 |
| Room-quality reference agreement | 23/30 | 25/30 |
| Median request latency | 3.57 s | 12.72 s |
| 95th-percentile latency, nearest rank | 9.02 s | 21.29 s |
| Summed request latency | 123.36 s | 419.86 s |
| Reported input tokens | 303,307 | 324,007 |
| Reported cached input tokens | 153,344 | 42,240 |
| Reported output tokens | 2,443 | 6,681 |
| Reported reasoning output tokens | 0 | 4,250 |

The configurations agree with each other on 24/30 regime labels and 22/30 quality labels. Higher reference agreement does not establish more accurate classifications or a profitable trading rule. Latency includes the client process and validation. Vendor context and caching differ despite identical user inputs; reasoning usage is reported separately, not added again to output tokens. No dollar-cost estimate is inferred from API pricing for subscription usage.

After shadow, all 16 full-quarter financial/control cases reproduced the accepted profile in 263.98 seconds. Mean case return remains −2.8181%, with 15 modeled budget breaches and seven halted cases; no annotations changed execution. Apollo DNS initially failed before replay launch. The user supplied a VPN route, its existing SSH host key was verified, and the replay then succeeded.

The branded comparison is `7-sol-high-shadow-attempt-001/7-sol-high-comparison.html`; its financial control is `quarter-2024-feb-apr-sol-high-financial/7-shadow-financial.html`. Both are under the existing quarter archive locally and Apollo's isolated reports directory. The offline index includes these reports and this rendered Markdown. [Outcome provenance](sol-high-shadow-outcome.json) records metrics, equality receipts and hashes for all 102 new research artifacts. The prior results and original outcome manifest remain unchanged.

Retain this as a configuration comparison only. No model veto, production selection, deployment or alpha claim follows.

## Reproduction

```sh
python -m kairos.research_shadow \
  --report /path/to/quarter-2024-feb-apr-attempt-004/2b-extension-acceptance.json \
  --output-dir /path/to/new-sol-high-shadow-directory \
  --model gpt-6.1-sol --effort high --max-invocations 30 \
  --approve-subscription-usage
```

This command consumes subscription quota; it is not part of automated tests. Use a new output directory and obtain separate authorization before another inference run.

Official references: [Codex models](https://developers.openai.com/codex/models/) and [configuration reference](https://developers.openai.com/codex/config-reference/). Existing API research distinctions remain in the [OpenAI decision experiment](ai-decision-experiment.md).
