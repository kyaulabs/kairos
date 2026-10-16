# Research test guide

Open repository-root `reports/index.html`. The protected offline bundle, including `reports/views/passive-maker-quarter-attempt-001--accepted-stress.html`, now lives under Git-ignored `reports/`, not the former home-directory quarter folder. Keep its assets and audit data together. The landing page starts with **Latest test group**, followed by earlier groups in reverse chronological order. Charts and detailed results are on the test pages, not the landing page; older detail pages also link back to the latest group.

The [quarter AI extension](ai-entry-filter-quarter.md) stopped at its first protocol failure after 582 new calls. It has 760 accepted answers including reuse, not full-quarter coverage. The unattempted 7,134-call continuation was cancelled before starting; cumulative research usage is 840, with zero active inference allowance. Let the already-running partial financial matrix finish without new inference.

The [quarter ledger diagnosis](quarter-strategy-diagnosis.md) reconciles 64 saved cases and 1,487 filled episodes. It proposes retiring anchored VWAP from the research allocation, not retuning costs or production. The [fresh Q3 validation](anchored-retirement-validation.md) preserves a failed Kraken coverage check and a separately approved public OKX candle source for the same period. Its financial execution requires signed input/code declarations and passing CI.

The latest completed AI financial result is the **AI entry-filter financial pilot**, completed **2026-10-15 05:17:37 UTC**. The approved 179-call v3 batch returned 30 monitoring permits and 149 abstentions; its saved recommendations actually vetoed entries in the offline comparison. Base IOC fills fell 13→1 and maker fills 5→1. AI marked returns were below both unchanged baseline and the primary activity-suppression reference: no incremental value demonstrated on these two previously inspected development days. All 16 suppression seeds remain visible; this is not a holdout or production permission. The earlier failed/16-case SDK pilot remains unchanged below.

## Run history

| Last recorded (UTC) | Test group | Reports |
| --- | --- | ---: |
| 2026-10-15 05:17:36 | [AI entry-filter financial pilot: baselines, 179 saved answers and paired financial controls](ai-entry-filter-pilot.md) | 9 |
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

These are execution/report records, not the historical price-data dates. Each test page shows its price-data window separately. AI times come from the last completed response. The new financial pilot uses preserved runner `completed_at`/`failed_at` receipts; older financial times come from the original Apollo report's save time, with its hash checked against the local archive. Local copy times and page-regeneration times do not determine test order. The offline `navigation.json` records every timestamp and its source.

## Charts and audit data

Test pages use a pinned, locally bundled Chart.js 4.5.1 browser build with its MIT license and acquisition receipt. They work offline without adding an application dependency. Financial views show marked return, filled entries and daily marked equity; controls select the interval, price-path scenario and equity-chart setup. Missing dates remain gaps. Older AI views show classification counts; SDK-pilot views show structural acceptance/failure and accepted monitoring recommendations. Neither measures trading performance or prediction accuracy. Failed and unattempted cases remain visible.

Each maker comparison contains **16 maker-entry cases plus 16 matched IOC controls**, not 16 IOC-only cases. Maker cases are shown by default; **Chart arm** selects maker entries, IOC controls or both. The entry-evidence panel counts actual archived `post_only` buy orders and filled entries, with expandable order records. Maker buys use the maker fee and recorded simulated `M` fill role; all owned exits remain taker IOC. The maker case table is shown first, with IOC controls separately collapsed. The unchanged archived comparison lists its IOC controls first; it is not a maker-only report.

Every test page links to its archived HTML report and JSON audit. Exact values remain available in tables without JavaScript. Ten Markdown guides/results documents are rendered on the summaries-and-methods page, with `.md` links. At the short pilot's close the archive contained 55 report pairs in eleven groups; quarter reports are appended as completed, not substituted for those results. All 46 pre-existing pairs remain byte-identical, including six labelled invalid financial reports and the two earlier SDK backfills. The short-pilot group added four baseline reproductions, one v3 inference audit and four actual AI-versus-control financial comparisons; its 16 conditional suppression seeds are retained within the audits, not counted as independent histories. Presentation rebuilds never rerun inference or finances. Related unit/CI and presentation verification is recorded separately, not counted as independent research.

## Reading the evidence

- Use corrected quarter attempt 004, not attempts 001–003. The invalid attempts exposed renderer, serialization and machine-clock fee-freshness problems.
- Maker pilots, CLI checks and diagnostic reproductions are implementation evidence, not independent strategy experiments. Flat equity from missed entries is not improvement.
- Earlier original v1/v2 pilots used different sizing; the [matched-risk history](experiment-history.json) identifies the controlled comparison. Previously traversed June data is not an untouched holdout.
- Historical reports retain all eight setups at both intervals. The proposed retirement reference replaces only the two anchored accounts with cash, without reallocating capital or changing the sixteen-account denominator. It reduces activity and is not evidence of a profitable strategy. Independent accounts are not a portfolio. Neither proxy nor native candles establish historical OKX fills; planned risk is not a guaranteed realized-loss ceiling.
- The latest AI financial pages compare baseline, response availability, AI and primary suppression, with all 16 conditional references retained. Matching is on permitted arming candidates, not fills. AI traded the same single candidate under each base execution policy; improved closed R on one episode does not offset weaker marked returns or establish an edge.
- AI agreement is not accuracy or profit. The short pilot closed at 258 cumulative invocations; the stopped quarter added 582, bringing total usage to 840. All unused slots and the unstarted continuation are closed. Nothing here authorizes more inference, trading, deployment, leverage or a halt reset.

After every research/backtest/AI run, append its result, including failed, aborted and repeated attempts, and refresh the HTML history before calling the task done. Add verification receipts for related unit/CI checks. Do not replace old results or use regeneration times as run dates. The maintained local generator/checks live in `reports/_tools/`; rebuild from saved evidence with `uv run python reports/_tools/build_navigation.py`, then run `verify_navigation.py` and `node reports/_tools/verify_charts.cjs`. This refresh is offline and does not authorize new model calls or financial tests.
