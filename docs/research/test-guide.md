# Research test guide

Open repository-root `reports/index.html`. The protected offline bundle, including `reports/views/passive-maker-quarter-attempt-001--accepted-stress.html`, now lives under Git-ignored `reports/`, not the former home-directory quarter folder. Keep its assets and audit data together. The landing page starts with **Latest test group**, followed by earlier groups in reverse chronological order. Charts and detailed results are on the test pages, not the landing page; older detail pages also link back to the latest group.

The latest group is **SDK candidate critique pilot**, last response recorded **2026-10-15 01:32:41 UTC**. Both attempts are visible: the first stopped after one failed assessment, and the approved rerun returned 16 structurally valid assessments with two monitor-permits and 14 abstentions. The audit found two false anchor-clock claims and an incomplete explanation. No trading or financial result changed; current v3 wording is offline-only, not another model run.

## Run history

| Last recorded (UTC) | Test group | Reports |
| --- | --- | ---: |
| 2026-10-15 01:32:41 | [SDK candidate critique pilot, failed attempt and corrected rerun](ai-assessment-pilot.md) | 2 |
| 2026-10-14 21:07:01 | [Passive maker vs taker, full quarter](passive-maker-comparison.md) | 4 |
| 2026-10-14 20:34:22 | Passive maker execution pilot | 4 |
| 2026-10-14 17:14:49 | [GPT-6.1 Sol / high shadow and financial control](sol-high-shadow.md) | 2 |
| 2026-10-14 05:50:46 | [GPT-6 Luna / low shadow and financial control](three-month-series.md) | 2 |
| 2026-10-14 04:46:39 | [Corrected quarter strategy sequence](three-month-series.md) | 17 |
| 2026-10-14 02:58:13 | Invalid quarter attempt 003 | 2 |
| 2026-10-14 02:51:55 | Invalid quarter attempt 002 | 2 |
| 2026-10-14 02:29:30 | Invalid quarter attempt 001 | 2 |
| 2026-10-14 00:53:42 | Earlier pilots and short-window comparisons | 9 |

These are execution/report records, not the historical price-data dates. Each test page shows its price-data window separately. AI times come from the last completed response. Other times come from the original Apollo report's save time, with the report hash checked against the local archive. Local copy times and page-regeneration times do not determine test order. The offline `navigation.json` records every timestamp and its source.

## Charts and audit data

Test pages use a pinned, locally bundled Chart.js 4.5.1 browser build with its MIT license and acquisition receipt. They work offline without adding an application dependency. Financial views show marked return, filled entries and daily marked equity; controls select the interval, price-path scenario and equity-chart setup. Missing dates remain gaps. Older AI views show classification counts; SDK-pilot views show structural acceptance/failure and accepted monitoring recommendations. Neither measures trading performance or prediction accuracy. Failed and unattempted cases remain visible.

Each maker comparison contains **16 maker-entry cases plus 16 matched IOC controls**, not 16 IOC-only cases. Maker cases are shown by default; **Chart arm** selects maker entries, IOC controls or both. The entry-evidence panel counts actual archived `post_only` buy orders and filled entries, with expandable order records. Maker buys use the maker fee and recorded simulated `M` fill role; all owned exits remain taker IOC. The maker case table is shown first, with IOC controls separately collapsed. The unchanged archived comparison lists its IOC controls first; it is not a maker-only report.

Every test page links to its archived HTML report and JSON audit. Exact values remain available in tables without JavaScript. Six Markdown guides/results documents are rendered on the summaries-and-methods page, with `.md` links. The archive contains 46 report pairs in ten groups: the 44 prior reports remain byte-identical, including six labelled invalid financial reports, and two SDK-attempt reports were backfilled from preserved responses. Rendering them did not rerun inference. Related unit/CI and presentation verification is recorded separately, not counted as independent research.

## Reading the evidence

- Use corrected quarter attempt 004, not attempts 001–003. The invalid attempts exposed renderer, serialization and machine-clock fee-freshness problems.
- Maker pilots, CLI checks and diagnostic reproductions are implementation evidence, not independent strategy experiments. Flat equity from missed entries is not improvement.
- Earlier original v1/v2 pilots used different sizing; the [matched-risk history](experiment-history.json) identifies the controlled comparison. Previously traversed June data is not an untouched holdout.
- All eight setups at both intervals remain. Independent accounts are not a portfolio. Kraken candles are proxies, not historical OKX fills; planned risk is not a guaranteed realized-loss ceiling.
- AI agreement is not accuracy or profit. Nothing in the guide authorizes trading, deployment, leverage, a halt reset or further inference.

After every research/backtest/AI run, append its result, including failed, aborted and repeated attempts, and refresh the HTML history before calling the task done. Add verification receipts for related unit/CI checks. Do not replace old results or use regeneration times as run dates. The maintained local generator/checks live in `reports/_tools/`; rebuild from saved evidence with `uv run python reports/_tools/build_navigation.py`, then run `verify_navigation.py` and `node reports/_tools/verify_charts.cjs`. This refresh is offline and does not authorize new model calls or financial tests.
