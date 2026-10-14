# Research test guide

Use this guide to choose a test group rather than treating every report as one experiment. The themed offline `index.html` is in the protected quarter report directory locally and on Apollo. Keep it with its report folders so relative links work.

## Contents

- [Maker versus taker entries](#maker-versus-taker-entries)
- [Quarter strategy sequence](#quarter-strategy-sequence)
- [AI shadow comparisons](#ai-shadow-comparisons)
- [Execution pilots](#execution-pilots)
- [Invalid and superseded attempts](#invalid-and-superseded-attempts)
- [Earlier short-window research](#earlier-short-window-research)

## Maker versus taker entries

[Declaration and results](passive-maker-comparison.md). This requested comparison holds the original and accepted signal rules fixed and changes entry execution. It includes all eight setups at 5m/15m, base/adverse assumptions, maker entry fees and taker protective exits. Results are pending. A flat account caused by missed maker fills is not an improvement.

## Quarter strategy sequence

[Rules, chronology and results](three-month-series.md), [outcome provenance](three-month-outcome.json). Use corrected attempt 004 only: 17 deterministic paired stages on February–April 2024. Only two small ablations met the declared base-execution criteria. The accepted stack remains negative and did not improve both adverse primary outcomes.

## AI shadow comparisons

[Initial shadow within the quarter](three-month-series.md), [Sol/high rerun](sol-high-shadow.md), [rerun provenance](sol-high-shadow-outcome.json). The initial stage used 32 official subscription client invocations across three attempts, with 30 completed annotations in the final attempt. The separately requested Sol/high rerun completed 30 more on identical inputs. Reference agreement is not accuracy, expected profit or permission to trade. Financial replays reproduced all 16 controls; neither model changed execution.

## Execution pilots

The maker pilot uses February 1–2 of the same quarter to check fills, timing, native fees and risk before the full replay. It is not a holdout or a strategy-selection stage. Unit tests use small constructed examples to check behavior; they are not historical performance evidence.

## Invalid and superseded attempts

Quarter attempts 001–003 are not valid strategy evidence. They exposed rendering, serialization and machine-clock fee freshness problems. Attempt 004 restarted fresh after the executor repair. Keep invalid reports visible in their own group, not mixed with completed financial evidence. Initial AI client/model/event-observer failures are preserved separately in their raw receipts.

## Earlier short-window research

The quarter results supersede neither the provenance nor the limitations of earlier studies. See the [entry-cost audit](entry-economics-audit.md), [positive-target comparison](positive-target-eligibility.md), [chronological June declaration](positive-target-validation.json) and [experiment history](experiment-history.json). Differently sized original v1/v2 pilots are not controlled financial comparisons. June data was already traversed; it is not an untouched holdout. The older HTF replay uses a different strategy and accounting model and must not be pooled with the eight VWAP/Fibonacci setups.

All cases are independent accounts, not a portfolio. Kraken candles are proxies for OKX U.S. spot, not historical OKX fills. Planned risk is not a guaranteed realized-loss ceiling. No report authorizes live trading, deployment, leverage, a halt reset or additional AI usage.
