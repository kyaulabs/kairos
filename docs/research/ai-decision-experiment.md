# OpenAI decision experiment

Proposed research only, reviewed 2026-10-13. The user is open to direct OpenAI integration if it helps the strategies. No client, dependency, credential, paid inference, trading permission or deployment is added by this plan. Resolve entry economics first, then test AI as a separate factor against the same deterministic strategy and executor.

## Verified API distinction

OpenAI's [Decisions guide](https://developers.openai.com/api/docs/guides/decisions) confirms a dedicated `POST /v1/decisions` endpoint. The supplied singular `/guide/decisions` URL returned 404 during this check; the plural `/guides/decisions` page and its Markdown version were available.

At the review date, Decisions is public beta and the guide lists only `gpt-6-luna`. It supports shared text/image evidence with named questions:

| API | Appropriate research role | Output |
| --- | --- | --- |
| Decisions `predicate` | Assess a clearly defined condition in the supplied evidence. | Estimated probability that the condition is true. |
| Decisions `choice` | Classify the observed regime or choose from a bounded set including abstention. | Supplied choice, per-option probabilities and a separate confidence field. |
| Decisions `score` | Rate setup quality against an explicit ordered rubric. | Probability-weighted level index, per-level probabilities and confidence. |
| Responses with Structured Outputs | Return a richer assessment with constrained fields and concise evidence references. | An object matching the requested schema, subject to refusal/incomplete-output handling. |

Decisions is similar in purpose to Jev, not a drop-in payload replacement. KAIROS's `Jev.decide()` in `kairos/clients.py` sends a `state` object and a question mapping to TypeSafe and expects answers keyed by question name. OpenAI uses `input`, a `questions` array and an `answers` array. Validate each contract separately before mapping it to common research fields.

The Decisions guide advertises about 10× lower latency than Responses. That is a provider claim, not a measured KAIROS result. Its listed price is $0.10 per million input tokens with no output/cache charges, subject to regional and long-context premiums. Recheck model availability, pricing and API schema before implementation. The Python SDK minimum listed is 3.26.0 if that SDK is chosen.

KAIROS already has `aiohttp` and an asynchronous review boundary. A small direct HTTP client can be sufficient; an SDK or agent framework needs a concrete benefit before adding dependencies. Do not replace the existing Jev integration or register these research strategies for live use merely to run the comparison.

## First experiment

Start with Decisions in shadow mode. At a candidate's arming candle, supply only the already completed market window and candidate facts. Ask one regime `choice` question with distinct categories such as `rising_trend`, `range`, `falling_trend` and `unclear`. Add a separate quality rubric only if the first question produces useful information; do not stack questions as entry requirements by default.

Use numerical OHLC/volume, normalized VWAP distance/slope, ATR, confirmed swing ages, pending-plan provenance and computed cost room. Missing data stays unavailable. VWAP is a volume-weighted HLC3 approximation; aggregate candles cannot reveal signed order flow, a historical OKX book or queue position. Models must not infer unseen data.

Shadow answers have no effect on signals, fills or protection. Compare them to the existing deterministic regime labels and a simple predeclared rule on identical inputs. Check whether AI adds information beyond facts already calculated by code. A paraphrase of the EMA trend is not an improvement.

If shadow evidence warrants a controlled decision test:

1. Compare the unchanged deterministic baseline with one Decisions challenger. Use the same eight setups, intervals, costs, sizing and fill assumptions. If a Jev comparator is included, send equivalent evidence and questions without assuming their confidence scales match.
2. Use `enter`/`abstain` only for a candidate code already permits. Avoid a bare spot `sell` label, which could be misread as opening a short. A model cannot revive an expired candidate, create a signal from no setup, override an economic rejection or grant permission.
3. Evaluate Responses as a separate challenger if richer interpretation is needed. Use a strict schema with bounded regime/action fields and references to supplied facts, not executable code or arbitrary price/quantity outputs.
4. Combine the APIs only if each contributes measurable value. A Responses explanation is not ground truth for training or calibrating Decisions, and model agreement is not independent confirmation.

Retain all eight strategy families and report per-family activity. A veto model that suppresses most trades can reduce losses mechanically; that does not meet the activity goal or prove an edge. Ranking is a later experiment if the independent cases become a shared-capital portfolio, not an implicit scope change now.

## Causality, latency and reproducibility

- Bind inputs and outputs to candidate ID, input hash, signal revision, model/version, prompt/question hash, data cutoff, request start, response receipt and expiry. Keep model assessments separate from trading outcomes.
- Do not backdate a model answer. The current immediate-next-open executor cannot wait for inference and still claim that historical opening fill. Arming-time classification can arrive before a later confirmation; otherwise the original opportunity is missed, or a separately declared delayed-execution model is required.
- A cached arming assessment applies only to that candidate until its original expiry/invalidation. It is not blanket trading approval. Confirmation still rechecks current structure, price, costs, funds and risk. Changed candidate, settings, position or stopped session invalidates a discretionary result.
- Use bounded asynchronous calls outside the execution/protection lock. Timeout, refusal, malformed/missing answer or budget exhaustion means abstain in the experimental decision arm. Existing position reductions never wait for a model or fail because inference is unavailable.
- Pin a supported model version where available and record the returned model identifier. A floating alias is not an immutable experiment. Preserve raw validated outputs privately for exact replay; a seed or temperature setting alone does not guarantee repeatability.
- At first, use text only. If image inputs are later useful, Decisions requires inline base64 images; hosted image URLs and file IDs are not supported in the reviewed guide. Render only the past window with fixed scales and no future candles, equity outcomes or annotated winners.

Historical AI replay has an extra leakage risk: the provider's model may have learned later prices or narratives during pretraining. Removing dates, symbols and future outcomes from inputs reduces obvious leakage but does not establish a clean holdout. Treat historical AI results as exploratory. The decisive check is a frozen prospective shadow period recorded after the prompt/model policy is chosen, using no orders. Later training or threshold calibration must use chronological development data, never the final evaluation outcomes.

## Validation and failure handling

Validate named answers against exactly the requested question set. Check types, allowed choices, finite probabilities in [0,1], consistent probability distributions, confidence range and valid score bounds. Handle refusals explicitly; do not coerce them to an invented favorable answer. A fractional score is an expected level index, not a probability of profitable trading.

The common research result should retain the API's probability distribution and confidence separately. Do not flatten them into the existing Jev `confidence` field as if they had identical meaning. Establish thresholds from labeled development examples and the costs of errors; no arbitrary "80% confidence" profitability rule.

Use local fixtures first for refusals, duplicate/missing names, invalid numbers, unsupported choices, HTTP errors, quotas, timeouts, late responses and candidate invalidation. Regression checks must prove no model output can change risk caps, native order fields, stop/target plans, deadlines, ownership, loss latches, write gates or reconciliation. A model failure must not disable an owned protective reduction.

No tools, shell execution, web search, exchange endpoints or credential access are needed for this experiment. Fix the inference host, require TLS, reject credential-bearing redirects, redact secrets and keep provider keys server-side. Send only minimal market/candidate evidence, never private exchange credentials, account identities or unrelated holdings. Archive downloaded/public text as evidence, not instructions to the model or executor.

For Responses, use `store: false` for the proposed stateless call. That setting does not itself promise zero retention. Decisions has up to 30-day default abuse-monitoring retention and possible prompt-cache retention; ZDR/HIPAA eligibility requires the applicable agreements and account configuration. Do not assume eligibility or copy Responses-only settings into Decisions. Review the applicable data controls before sending any private input.

## Evaluation and costs

No paid call should run until the model, request/input caps and a total spend ceiling are explicitly approved. The first budget can cover a finite set of development candidates rather than every candle across all history. Deduplicate identical inputs where appropriate and preserve the actual request count. Stop inference at the cap; do not substitute another paid provider automatically.

At the listed Decisions base rate, 500 calls of 4,000 input tokens would cost about $0.20 before premiums. This is an illustration, not a quote, authorization or promise that our context fits 4,000 tokens. Responses has separate model/input/output pricing. Report actual token usage, billing currency and inference cost independently from trading fees; a combined net-economic view needs explicit currency conversion, not assumed USD/USDT parity.

Measure incremental changes in filled entries, active days, idle gaps, marked returns, closed R, costs, open exposure and risk overruns. Also report latency percentiles, answers that arrived too late, abstentions, refusal/error rates, reproducibility and cost per useful assessment. Selection quality must be scored against all eligible candidates, including rejected opportunities, with a consistently defined executable outcome and no invented profit for an unresolved position.

If using a forward-event predicate, define the event and horizon before evaluation. Calibrate with appropriate chronological labels and report Brier/calibration results separately from strategy expectancy. The probability of a chosen category, rubric confidence and probability of an executable winning trade are different quantities.

Continue only if the challenger improves net executable outcomes beyond a simple rule while retaining useful activity and acceptable reliability/cost. One tuned week, convincing explanations or accurate regime labels alone are insufficient. No claim of AI profitability is made here.

## Official sources

Checked without authentication or inference calls on 2026-10-13:

- [Decisions guide](https://developers.openai.com/api/docs/guides/decisions), including types, examples, beta model, pricing and SDK requirements.
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) for Responses schema constraints and refusal handling.
- [Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses), especially state storage and `text.format`.
- [Data controls](https://developers.openai.com/api/docs/guides/your-data) and [pricing](https://developers.openai.com/api/docs/pricing), referenced by the Decisions guide; account-specific eligibility and future applicable rates still require verification.

The relevant existing review lifecycle is `kairos/htf_review.py`; research signals and execution are in `kairos/research_signals.py` and `kairos/research_backtest.py`. Reuse their safety boundaries where applicable rather than building a second trading executor.
