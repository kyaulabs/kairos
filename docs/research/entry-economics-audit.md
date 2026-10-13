# Entry economics and first timing trial

The diagnostic-only replay reproduced all 16 saved v2 cases from the matched June 1–exclusive June 8, 2026 comparison. The first Fib EMA timing alternative did not meet its predeclared acceptance rule and was not adopted. All eight baseline signal rules remain unchanged. No deployment, restart, orders or paid inference occurred.

This is the repeatedly inspected development week, not a holdout. Each case has its own $1,000 account and at most 1% planned stop risk under the shared base executor. Kraken candles are cross-venue proxies for the OKX spot target. Execution-minute coverage remains 87.35%; no missing prices or historical OKX books were invented. Planned risk is not a guaranteed realized-loss cap.

Read the [improvement plan](vwap-fibonacci-improvement-plan.md) for the remaining experiments. The [experiment history](experiment-history.json) records declarations, source and artifact hashes, verification and the rejected selection. Private reports and source snapshots remain on Apollo and in the local research store.

## Cost and timing audit

`kairos/research_diagnostics.py` shares the synthetic quote and tick-rounded IOC bound with risk sizing. It projects received-BTC buy fees, quote-currency sell fees and adverse exit execution. Actual-fill projections use net ownership, lot rounding, minimums and sold-share cost basis. Below-minimum BTC is retained, not hypothetically sold. A projected target reward is not expected profit.

The runner records candidate arms, frozen plans, confirmation delays, stop widening, next-open quotes, requested/filled quantities and outcomes. Expired, blocked and unfilled candidates remain visible. JSON includes UTC clock coverage, preceding-minute liquidity and frozen Fibonacci-level contacts using only completed trailing bars known before arming. Contacts count bars, not independent bounces; the bounded observed history is not a lifetime interaction count.

Exit evidence separates the first latched trigger, IOC attempts, partial/zero fills, last-fill delay, average sale price, retained BTC and later stop crossings. Sold-share P&L reconciles as:

```text
sold quantity × target − sold-share entry cost basis
− target-to-observed-mid shortfall
− exit spread/quote-tick cost
− exit slippage/bound-tick cost
− quote sell fees
```

Entry room consumed after confirmation is separately attributed to the next-open price move, ask spread/quote rounding and IOC slippage/bound rounding. The entry cost basis already includes the received-BTC fee through net ownership; it is not debited twice.

| Matched v2 evidence | Result |
| --- | ---: |
| Filled entry orders, including partial fills | 146 |
| Nonpositive modeled net target reward | 48 |
| Below one net planned R | 120 |
| Closed Target episodes | 45 |
| Losing Target episodes | 23 |
| Losing Targets already cost-negative at entry | 23 |
| Losing Targets needing multiple exit IOCs | 11 |
| Losing Targets crossing the stop after the Target trigger | 0 |

All 23 losing Target episodes also had nonpositive projected P&L at the target on their actual sold share. Last-fill delays among these episodes reached 1,320 seconds. Delay and adverse movement can worsen a loss, but they were not needed to explain why these targets were economically insufficient. Attribution counts overlap.

Fib EMA 5m had 13 cost-negative plans among 16 fills. Confirmation took one to three bars. The one-ATR confirmation rule widened the frozen arm stop in one of those fills; it was not changed. No accounting or protective-execution defect was established by this audit. A synthetic regression separately demonstrates a profitable planned Target followed by partial/zero fills, a later stop crossing and a realized loss while the first reason remains "Target". Owned reductions still run after the entry-loss latch.

Every saved deterministic field matched the authoritative v2 cases, including native execution deltas, fees, equity paths, risk, activity, orders, trades and sold-share R. Random order/client/run identities were normalized; runtime and comparison-only descriptions were excluded. Diagnostic metadata is additional evidence, not a changed permission or fill policy.

## Fib EMA higher-low alternative

The only rule alternative was `micro_break` → `(higher_low or micro_break)` in Fib EMA confirmation. A higher low still needs a rising close versus the previous completed candle. EMA20 recapture, EMA20 above EMA50, nonfalling EMA50, bullish close, frozen swing target, invalidation, expiry, next-open entry, stop-widening rule, sizing and executor were unchanged. Confirmation-time ATR can still change the actual stop under the existing rule.

The 32-case Apollo comparison retained all eight setups on both intervals. All 16 controls reproduced the diagnostic baseline, and all 14 non-EMA case pairs were identical. Source/control hashes and causal arm/confirmation checks passed. Neither arm had modeled risk-budget breaches.

| Fib EMA result | 5m control | 5m alternative | 15m control | 15m alternative |
| --- | ---: | ---: | ---: | ---: |
| Signals | 37 | 42 | 8 | 10 |
| Filled entries | 16 | 17 | 5 | 6 |
| Positive modeled target-reward fills | 3 | 5 | 1 | 1 |
| Active days | 6 | 6 | 2 | 2 |
| Net marked return | −1.1098% | −1.1150% | −0.0026% | −0.0920% |
| Mean closed R | −0.4349R | −0.4433R | −0.2980R | −0.2832R |

The alternative increased activity and recovered some target room, but did not improve primary 5m net outcomes. The 15m return worsened despite a small mean-R improvement. It therefore failed the declared advancement rule. These small, dependent samples do not establish a statistically reliable ranking. Keep the baseline; retain the alternative source and report as a rejected development trial, not a selected production rule.

The comparison took 39.04 measured wall seconds. The initial diagnostic replay took 21.00 wall seconds; the final replay adds explicit price-cost decomposition without changing financial results. Individual case runtimes and verification receipts are preserved with each report.

## Reproduction and next work

The protected Apollo workspace is `/srv/kairos-research/kyau-vwap-fib`. Reports have HTML/JSON companions; `versions/` retains source bundles, wheels, predeclared receipts, runners and logs. The selected archive and receipt remain unchanged, SHA-256 `5332ee5fe401c99fd4fd5321e6a4e592e82bb7ad28fc8d83548bdb3d1a55bdb5`. Reports are also under `/home/kyau/.local/share/kairos-research/vwap-fibonacci/`.

- `entry-economics-v2.{json,html}` and `.verification.json`: initial diagnostic replay.
- `entry-economics-v2-final.{json,html}` and `.verification.json`: final cost-attribution replay.
- `ema-higher-low-development.{json,html}`: paired timing trial; its runner and challenger source are archived under the matching version directory.
- A launcher failure caused by absent `/usr/bin/time` occurred before simulation. Its log is retained separately; no failed strategy run or result was discarded.

Reproduce the final baseline from its archived source directory with the research virtualenv:

```sh
cd /srv/kairos-research/kyau-vwap-fib/versions/entry-economics-v2-final/code
../../../.venv/bin/python -m kairos.research_backtest \
  --archive ../../../kairos-vwap-fib-pilot.zip \
  --output ../../../reports/new-diagnostic-replay.json
```

Use a new output path; reports refuse overwrites. The archived timing runner similarly refuses its existing output. Copy it and declare a new output path before reproduction, preserving the original hashes and report.

Next, declare another single-factor timing test or a single economic-eligibility boundary. First disclose the counterfactual activity loss from rejecting nonpositive reward; `net R ≥ 1` would reject 120 of the current 146 fills before position-path effects. Do not stretch targets, tighten stops inside invalidation or lower costs. No economic gate, extension ablation, other chronological week, AI adapter or trading permission has been added.
