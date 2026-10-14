# Positive-target entry eligibility

Research-only experiment, completed through its first frozen chronological check on 2026-10-14. `--economic-policy positive-target` adds one necessary cost boundary at the observed next-open IOC quote. The default remains `diagnostic`, which reproduces baseline entries. All eight signal implementations, structural targets, stop rules, sizing and protective reductions remain unchanged. No production strategy, deployment, restart, orders or paid inference is added.

The [entry-economics audit](entry-economics-audit.md) found 48/146 cost-negative filled plans. Every one of its 23 losing Target episodes was already cost-negative at entry. The [experiment history](experiment-history.json) and [freeze receipt](positive-target-validation.json) preserve the predeclared criterion, hashes, development results and next-week decision.

## Boundary and development result

The eligibility condition is:

```text
(1 − received-BTC buy fee) × modeled target IOC exit bound × (1 − quote sell fee)
− next-open entry IOC bound > 0
```

Both bounds use the same synthetic quote, tick rounding, spread and slippage as execution. Zero reward is ineligible. Positive reward below one net R remains eligible; this is not a threshold sweep. The condition is checked at the next-open quote, not just the confirmation close, and uses no future liquidity. It applies only to entries and never blocks or delays an owned protective reduction.

This is an illustrative all-sold unit, not expected profit or a full-liquidation promise. Actual fills may be partial, fees reduce net BTC, lot/minimum residuals remain owned, and delayed exits or price gaps can lose money despite a positive projection. Existing planned-risk sizing reserves dust and assumed costs; actual loss overruns remain visible.

Static classification of the old fills would exclude 48/146 (32.9%); one net R would exclude 120/146 (82.2%). These are activity counterfactuals, not recomputed returns. The matched replay tests the changed position, cash and dust paths instead of assuming prior losing trades can simply be deleted.

| June 1–exclusive June 8 development result | Diagnostic | Positive-target |
| --- | ---: | ---: |
| Signals | 242 | 242 |
| Filled entries | 146 | 98 |
| Positive projected target-reward fills | 98 | 98 |
| Economic entry rejections | 0 | 60 |
| Closed trades | 123 | 81 |
| Mean independent-case marked return | −0.3783% | −0.2776% |
| Pooled mean closed R | −0.5131R | −0.4950R |
| Modeled risk-budget breaches | 0 | 0 |
| Zero-fill cases | 0 | 1 |

All 16 controls reproduced the saved final-v2 fields. Signals, candidate knowledge times, frozen plans and common execution/risk controls matched across every pair. The comparison took 40.12 wall seconds. Both arms use $1,000 per independent case, 5m/15m, the base IOC executor and at most 1% planned stop risk.

The rule preserved every previously positive-target filled opportunity, but recovered no additional valid fills. It met the declared development advancement criterion because that activity was retained, no breaches occurred and both aggregate net measures improved. This is permission for a separate chronological check, not evidence of profitability or a solved activity problem.

Activity remains weak in several cases. Fib EMA 5m falls from 16 fills on six active days to three fills on three days. Fib confluence 5m falls from six fills to one; Fib confluence 15m has nine signals and six economic rejections, but no attempts or fills. That case remains in the study; its flat return is inactivity, not success. Anchored VWAP 5m marked return worsens slightly despite removing negative plans. Reducing trading is not itself proof of a better strategy.

## Frozen chronological check

Before opening June 8–exclusive June 15, freeze the exact source and `positive-target` rule in the linked receipt and signed Git history. No settings or acceptance criteria may be tuned using that week's outcomes. Use a fresh $1,000 account per case, two native warmup days and the preceding execution minute. Keep all eight setups and both policies in a 32-case comparison. Audit coverage and preserve gaps rather than switching to a more favorable week.

Prior VWAP/Fibonacci reports and the June HTF smoke test cover June 1–8. Older broad HTF cases traversed January 2020–July 2026, including the proposed validation week. No week-specific output from that older method informed this eligibility rule, but this is not a blanket claim that the period was never processed. Call it frozen chronological validation, not an untouched holdout.

The declared continuation criterion requires retaining at least the new control's positive-target filled activity, improved mean independent-case marked return and pooled mean closed R, and no additional modeled risk-budget breaches. Always disclose sparse/zero-fill cases, losing Target exits, open holdings, entry latches and overruns. A passing comparison still does not establish statistically reliable alpha, an OKX fill model or production readiness. Do not change the frozen rule after a failure.

Signed freeze commit `9c1e604` preceded data selection and replay. The selected June 8–15 ZIP is 207,242 bytes, SHA-256 `718df4d7c45fc53a49b29a33e70fb38d4567336bf8c08528a1158bc4eeb417b5`. It contains the week, two native warmup days and the preceding execution minute; the full archive and deployed state were not transferred. All source hashes still match the signed freeze.

| June 8–exclusive June 15 chronological result | Diagnostic | Positive-target |
| --- | ---: | ---: |
| Signals | 313 | 313 |
| Filled entries | 146 | 74 |
| Positive projected target-reward fills | 74 | 74 |
| Economic entry rejections | 0 | 100 |
| Closed trades | 128 | 66 |
| Mean independent-case marked return | −0.2033% | −0.1003% |
| Pooled mean closed R | −0.5611R | −0.4711R |
| Modeled risk-budget breaches / entry halts | 0 / 0 | 0 / 0 |
| Losing / closed Target episodes | 23 / 43 | 0 / 17 |

All 74 positive-target filled candidate IDs are retained exactly, with no additional valid activity recovered. Signal counts, causal price plans and paired execution controls match. The comparison took 40.58 wall seconds. A separate CLI smoke check reproduced the frozen positive-target reclaim 5m case, including financial, risk and diagnostic fields.

The directional continuation criterion passes, but evidence remains weak. Only 7,665 of 10,080 execution minutes are observed (76.04%), with 1,561 nonadjacent observed transitions. Native 5m coverage is 99.55%; 15m coverage is 100%. Missing minute candles do not prove downtime or authorize invented fills. Preserve this week rather than substitute a more favorable one.

Eight of the 16 gated cases have at most two fills. VWAP reversion 15m has no fills, one unfilled attempt and two economic rejections. Fib EMA 5m has one fill on one active day despite 62 signals. Gated marked returns worsen in reclaim 5m, reversion 5m and extension 15m. Several positive closed/marked values are tiny, sparse observations; they do not establish an edge. None of the 17 closed gated Target episodes loses money in this model, but stops and deadlines still leave aggregate returns and closed R negative. Delayed/partial exits remain capable of loss, as the regression tests demonstrate.

Retain the boundary as an opt-in research condition, not a production selection. Do not broaden history on the strength of a loss reduction with poor coverage and sparse activity. Next work is a separately declared entry-location/timing improvement on development data, preserving the cost condition, structural targets and protection. Do not retune this frozen comparison. Historical OKX books, account fees and instrument-rule history remain unavailable or unverified.

## Reproduction

Private report pairs are under `/srv/kairos-research/kyau-vwap-fib/reports/` and `/home/kyau/.local/share/kairos-research/vwap-fibonacci/`. Apollo `versions/positive-target-development/` and `versions/positive-target-validation-2026-06-08/` retain source bundles, wheels, declarations, comparison runners and logs. The report pairs are `positive-target-development.{json,html}` and `positive-target-validation-2026-06-08.{json,html}`. The selected development archive remains unchanged, SHA-256 `5332ee5fe401c99fd4fd5321e6a4e592e82bb7ad28fc8d83548bdb3d1a55bdb5`.

Run one policy through the existing research CLI, choosing a new output path:

```sh
kairos-research --archive /path/to/selected-week.zip \
  --start 2026-06-01 --end 2026-06-08 \
  --economic-policy positive-target --output /path/to/new-report.json
```

Use `--economic-policy diagnostic` for the unchanged control. Preserve both HTML/JSON reports, source hashes and the exact shared controls. The eligibility switch is not registered in production settings or exposed to trading permission. Further timing, extension, indicator-control and AI experiments remain separate factors.
