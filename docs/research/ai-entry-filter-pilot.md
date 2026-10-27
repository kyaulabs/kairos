# AI entry-filter financial pilot

The user approved **at most 179 fresh Sol/high invocations, including failures**, to test an additional offline entry veto. This is separate from the exhausted/closed earlier allowances (79 invocations consumed). It authorizes no production orders, exchange writes, deployment or automatic retries.

The [frozen declaration](ai-entry-filter-declaration.json) fixes the inputs, controls, timing and evaluation before new inference or financial outcomes are inspected. The SDK uses the current clock-explicit contract v3, not the older v2 answers. Sixteen candidates repeat earlier development examples; those are not independent observations or a blinded model-quality comparison.

## Fixed scope

- February 1–2, 2024 UTC, ending exclusively on February 3, with two days of native-bar warmup. This is the same previously inspected short maker pilot, not a replacement quarter or holdout.
- Every new arm in that window from the unchanged accepted quarter signal source: 185 arms, 179 input-contract-valid requests and six target-already-passed rejections. No selection on confirmation, fills or economic outcomes. Warmup arms and rejected inputs fail closed in filtered arms.
- All eight setups at 5m and 15m; $1,000 per independent account; unchanged positive-target economics, native increments, fees/dust/cash constraints, at most 1% planned stop risk and the $12.50 UTC entry-loss latch. These cases are neither a portfolio nor independent market histories.
- Both next-open IOC and the unchanged best-bid, 30-second maker entry policy, under base and adverse execution. All owned stops, targets and four-hour deadlines remain bot-managed taker IOC. Retained BTC is reported, not fictionally sold at study end.
- Official `openai-codex==0.162.1`, `gpt-6.1-sol`, high effort, existing ChatGPT authentication, read-only/deny-all and disabled tools. One managed client, fresh ephemeral thread per candidate, fixed chronological `(as_of, minutes, setup)` order. An alias is not an immutable backend snapshot.

Application processing stops at the first inference/protocol failure. Do not retry that candidate or silently replace it. Close unused slots, preserve native usage/unknowns, and report actual coverage. The financial replay still measures the observed fail-closed pipeline: failed and unattempted candidates cannot enter. A stopped batch is not a fully covered AI experiment.

## Financial comparison

Each execution/scenario combination retains these primary arms:

| Arm | Entry restriction |
| --- | --- |
| Baseline | Unchanged deterministic strategy; no assessment consumed |
| Availability | Every structurally accepted response is monitoring-eligible at its recorded response time, irrespective of recommendation |
| AI filter | Only an unchanged `permit` response is monitoring-eligible |
| Suppression 00 | Predeclared primary non-AI hash reference with the same permitted-arm count per setup/timeframe as AI |

Fifteen additional suppression seeds, 01–15, are retained as sensitivity references. The hash formula and all seeds are frozen, not selected for financial results. Matching is on **permitted arming-candidate counts among successful responses**, not realized fills, positions or active days. Report those actual differences rather than forcing a match.

The suppression controls condition retrospectively on each case's total AI permit count. They are outcome-blind conditional references, **not deployable causal non-AI policies**. Their agreement/distribution is not an independent-market significance test. They help distinguish candidate selection from simply suppressing activity, without claiming to solve that identification problem.

Responses become available at historical arming-bar close plus one second of assumed bar availability plus their measured request-to-receipt wall latency. The real 2026 request timestamps are not backdated into 2024 executions. Each independent case is evaluated separately; this does not establish historical candle-delivery timing or a shared multi-case inference queue. Availability and suppression use the same recorded per-candidate latency as AI.

The filter cannot confirm a pattern, change a target/stop, override current quotes/economics, sizing, ownership, cash, protection, deadlines or loss latches. A response received after the entry opportunity cannot create a later catch-up entry. A `permit` remains permission to continue monitoring, not order permission. Original signal generation continues unchanged after abstentions, so the fixed candidate universe cannot be manufactured by rearming earlier.

## Verification and interpretation

Before inference, reproduce all 64 corresponding archived maker-pilot baseline cases. The complete comparison then simulates 1,216 cases: four execution/scenario combinations, 16 setup/timeframe cases and 19 arms. Those are alternative account paths on the same two days, not 1,216 independent histories. If inference fails, complete the same fail-closed financial matrix and label the limited coverage.

Verify frozen arming/confirmation plans across all arms, native post-only/IOC roles, prior-minute liquidity, received-currency fees, partial ownership and the unchanged risk budget. Save every native response and its scalar bindings, actual/unknown usage, latency, failures and unattempted inputs. Structural validity does not establish semantic truth; do not repair decisions after seeing trades.

Report after-cost marked returns, sold-share closed R, attempts, fills, active days, fees, exposure, halts/breaches, retained BTC and baseline opportunities suppressed. Compare AI with baseline, availability, primary suppression and all sensitivity seeds. Use the existing conservative descriptive improvement rule (no reduced positive-target fills/active days, no increased zero-fill cases or breaches, strictly better mean return and pooled closed R) against each primary comparator. Undefined R or inactive adverse maker execution cannot pass as robustness.

Trading net values include the modeled fees/spread/slippage, not an invented subscription-inference dollar cost. Report actual tokens/latency and unknown usage separately; an interruption request or client closure does not prove backend generation stopped.

Lower losses with fewer trades are not sufficient evidence of incremental AI value. A profitable-looking tiny result is not expectancy, a holdout result or production permission. No later quarter run or further inference is authorized by this pilot's outcome.

## Results

**No incremental AI value demonstrated.** The AI filter lowered marked returns below both the unchanged baseline and the predeclared primary suppression reference. It filled only the same **Fib EMA 15m candidate, `fib-ema:1706804100`**, under each base execution policy; those are alternative executions, not two independent wins.

| Execution / scenario | Baseline fills → AI | Baseline mean return | AI mean return | Suppression 00 mean return |
| --- | ---: | ---: | ---: | ---: |
| Base IOC | 13 → 1 | +0.006471% | +0.001002% | +0.006959% |
| Base maker `post_only` | 5 → 1 | +0.009478% | +0.001892% | +0.008745% |
| Adverse IOC | 1 → 0 | +0.001446% | 0% | +0.001446% |
| Adverse maker | 0 → 0 | 0% | 0% | 0% |

These are mean marked returns across 16 independent $1,000 accounts, not portfolio returns. They include modeled trading costs, not an invented inference price. The positive short-window marks do not replace or improve the negative full-quarter results.

Availability reproduced the baseline's financial outcomes in every path: coverage/timing exclusions did not explain the activity change. AI missed 12 baseline IOC fills and four baseline maker fills, added none, and reduced active case-days from 10/5 to one. Its single completed episode had R +0.0965 IOC / +0.1993 maker versus pooled baseline R −0.2503 / −0.1900; that sparse change does not establish quality. Mean trade R and marked returns have different weighting, and retained BTC remains reported rather than fictionally liquidated.

All 16 suppression seeds are retained. In base IOC, AI mean return was above eight, below three and equal to five references; in base maker it was above seven, below three and equal to six. These are conditional-reference counts, not significance tests. No primary comparison passed the frozen conservative improvement rule. Adverse maker inactivity establishes nothing; primary arms had no loss-budget breaches or entry halts.

The [outcome receipt](ai-entry-filter-outcome.json) records all controls and hashes. Signed freeze `afc3369` passed CI: 786 Python tests per 3.12/3.13/3.14 and 115 JavaScript tests. All 64 archived baseline cases reproduced before inference and again inside the 1,216-case comparison. The final audit verified all 179 input plans against baseline arms in every path and frozen arming/confirmation fields, actual maker/taker roles, selected-response availability and risk reserves across the full matrix. Total financial simulations: 1,280, all on the same two days.

Inference completed **179/179 structurally valid answers: 30 monitoring permits, 149 abstentions**, with five unknown component checks. Actual usage: 1,471,201 input + 240,338 output = **1,711,539 reported tokens**; 408,064 cached-input and 187,607 reasoning tokens are subsets. All 179 usage records were available. Client-wall latency: 10.917902–50.166637 seconds, median 30.455251, summed 5,538.837888 seconds. No retry, fallback or failed invocation occurred.

The two deliberately repeated anchor-clock context cases now correctly describe the 41,910.6 low as unbroken by the subsequent supplied minimum 41,939.1. Both still abstain, with complete sentences on those examples. This targeted development spot-check is not an exhaustive truth audit or a controlled attribution to instruction wording/backend behavior; no saved opinion was corrected.

Final SDK receipt: **2026-10-15 05:11:02 UTC**. Financial completion: **05:17:37 UTC**. All **258 cumulative research invocations** are consumed; earlier unused slots remain closed and no allowance remains. No production permission, exchange write, deployment, full-quarter AI run or further inference follows from this result.

Open `reports/index.html` for the maintained history. This study contributes four baseline-reproduction pairs, one complete inference-audit pair and four financial-comparison pairs under `reports/ai-entry-filter-pilot-attempt-001/`, with all sensitivity cases and opportunity/ownership audits linked.
