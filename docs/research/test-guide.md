# Research test guide

Open the protected offline `index.html`. It now starts with **Latest test group**, followed by earlier groups in reverse chronological order. Each group opens a short list of dated tests, also newest first. Charts and detailed results are on the test pages, not the landing page.

The latest group is **Passive maker vs taker, full quarter**, last recorded **2026-10-14 21:07:01 UTC**. It contains four comparisons covering 128 independent cases. Accepted-rule base maker entries average −1.4023%, versus −2.8181% for IOC entries, but fills fall from 698 to 413. Neither arm is profitable; adverse maker execution has no fills under the declared timing assumptions.

## Run history

| Last recorded (UTC) | Test group | Reports |
| --- | --- | ---: |
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

Test pages use a pinned, locally bundled Chart.js 4.5.1 browser build with its MIT license and acquisition receipt. They work offline without adding an application dependency. Financial views show marked return, filled entries and daily marked equity; controls select the interval, price-path scenario and equity-chart setup. Missing dates remain gaps. AI views show classification counts, not trading performance or prediction accuracy.

Each maker comparison contains **16 maker-entry cases plus 16 matched IOC controls**, not 16 IOC-only cases. Maker cases are shown by default; **Chart arm** selects maker entries, IOC controls or both. The entry-evidence panel counts actual archived `post_only` buy orders and filled entries, with expandable order records. Maker buys use the maker fee and recorded simulated `M` fill role; all owned exits remain taker IOC. The maker case table is shown first, with IOC controls separately collapsed. The unchanged archived comparison lists its IOC controls first; it is not a maker-only report.

Every test page links to its original, unchanged HTML report and JSON audit. Exact values remain available in tables without JavaScript. The four Markdown guides/results documents are rendered on a separate summaries-and-methods page, with original `.md` links. The archive still contains 44 original reports, including six clearly labelled invalid reports; derived chart views are not new tests.

## Reading the evidence

- Use corrected quarter attempt 004, not attempts 001–003. The invalid attempts exposed renderer, serialization and machine-clock fee-freshness problems.
- Maker pilots, CLI checks and diagnostic reproductions are implementation evidence, not independent strategy experiments. Flat equity from missed entries is not improvement.
- Earlier original v1/v2 pilots used different sizing; the [matched-risk history](experiment-history.json) identifies the controlled comparison. Previously traversed June data is not an untouched holdout.
- All eight setups at both intervals remain. Independent accounts are not a portfolio. Kraken candles are proxies, not historical OKX fills; planned risk is not a guaranteed realized-loss ceiling.
- AI agreement is not accuracy or profit. Nothing in the guide authorizes trading, deployment, leverage, a halt reset or further inference.
