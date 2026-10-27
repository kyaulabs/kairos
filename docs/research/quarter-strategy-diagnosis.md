# Quarter trade-ledger diagnosis

Additional AI spending is stopped. The user replaced the unstarted 7,134-call continuation with strategy diagnosis and fresh-period validation. No continuation calls ran; cumulative research usage remains 840 and active inference allowance is zero. The original malformed-hash response stays rejected and unchanged.

The saved February–April 2024 ledgers reconcile across all 64 baseline cases and 1,487 filled episodes. This is an accounting analysis of existing results, not another backtest or independent sample. The [outcome receipt](quarter-strategy-diagnosis-outcome.json) binds the source reports and analyzer. The stopped [AI comparison](ai-entry-filter-quarter.md) remains separate.

## What explains the losses

| Base execution | Filled entries | Mean marked account return | Net sold-share P&L | Observed-mid price contribution | Attributed execution and fees |
| --- | ---: | ---: | ---: | ---: | ---: |
| IOC | 698 | −2.8181% | −$451.12 | −$94.60 | $356.51 |
| Maker | 413 | −1.4023% | −$224.64 | −$19.01 | $205.63 |

Dollar sums describe sixteen independent $1,000 accounts, not a funded portfolio. Marked returns also include retained BTC: approximately +$0.22 IOC and +$0.28 maker in aggregate. Retained inventory is not fictionally sold.

For each episode, the analyzer reconciles:

```text
observed-mid price contribution
− entry execution penalty
− allocated received-BTC buy fee
− exit spread/quote-tick cost
− exit slippage/bound-tick cost
− quote sell fee
= net sold-share P&L
```

Entry mids use the actual later crossing observation for passive fills, not the earlier placement mid. IOC entry mids use the observed next open. Exit mids use actual recorded sell observations. This holds actual sold shares, fill selection, ownership and exits fixed. It is not a fee-free strategy replay, isolated entry alpha or an executable counterfactual.

Costs explain much of the loss, but removing attributed costs would not remove the negative base observed-price contribution. Conversely, adverse IOC has a small positive observed-mid contribution and large execution losses. Cost and signal quality must not be conflated. Adverse maker has no fills, so its zero return is inactivity rather than robustness.

Only one IOC case and four maker cases have positive base marked returns. There are 608/698 IOC fills and 303/413 maker fills below one projected net R. Positive target reward was deliberately a weaker condition than expectancy or a one-net-R floor. This diagnosis does not add or optimize such a floor.

Median confirmation delay is one completed setup bar in both base modes, not universally three bars. Median projected net R is 0.386 IOC and 0.598 maker; median target-room cost share is 45.6% and 37.5%. Among the 284 IOC and 171 maker fills with a fixed target already known at arming, median arm-to-entry room consumption is 28.5% and 14.2%. Other setups choose targets at confirmation; their missing arm target room is not zero. These descriptive subsets do not prove that earlier entry would improve the strategy.

Exit behavior also matters. Base IOC has 337 Stop, 203 Target and 158 deadline triggers; maker has 224, 117 and 72. Multiple exit IOC attempts occur in 336/698 IOC episodes and 203/413 maker episodes. Maximum last-fill delay after the first trigger reaches 4,800 seconds IOC and 2,040 seconds maker. The four-hour deadline initiates protective reduction; it does not guarantee immediate complete liquidation.

Each of base IOC, adverse IOC and base maker has one retired Target-triggered episode with negative sold-share P&L despite positive projected P&L at the target. These needed 21, 9 and 7 exit attempts, respectively, without a later stop crossing. Positive projected target reward does not guarantee a profitable realized Target. These are observed liquidity/execution limitations, not grounds to invent fills or weaken protection. Earlier timing ablations mostly failed; neither another earlier-entry rule nor a tighter stop is established by this diagnosis.

## Research decision

Propose retiring `vwap-anchored` at both intervals from the research allocation, rather than adding another filter to rescue it. Keep its source and original results for reproduction. Do not change production strategies.

Anchored VWAP contributes −$177.13 of base IOC sold-share P&L and −$124.17 of maker P&L. Both intervals have negative observed-mid contribution in both base execution modes. All three confirmation-time regime groups also have negative net P&L. A rising-only filter is not supported: maker rising-regime observed-price contribution is negative at both intervals, and 15m IOC is negative too.

Retirement leaves the other accounts and their capital unchanged; the two retired accounts stay in cash. It reduces activity and cannot satisfy the earlier activity-preserving improvement rule. The remaining strategies are still negative in aggregate on development data. Do not present retirement as a profitable replacement strategy or select the few historical winners as proven alternatives.

The [fresh-period validation](anchored-retirement-validation.md) tests this retirement decision without a threshold sweep, cheaper fees, wider targets, tighter stops or maker retiming. Its period was selected before viewing new returns. A failed data gate is preserved, not repaired by padding candles or silently choosing another period. The completed native-OKX check repeated anchored losses but had only thirteen closed episodes per base execution mode, below the frozen minimum twenty. Retirement remains provisional. Both the all-eight and cash-retirement allocations still lose; no strategy improvement or production candidate was established.

## Verification and limits

Native execution roles and fill clocks, sold-share basis, base ownership, closed-trade P&L, marked returns and retained P&L reconcile within $10⁻¹⁸. All failed and unfilled candidate outcomes remain in the original reports. One initial analysis failure on a nullable arm target-room field is retained; the analyzer now preserves that missing value as null. No financial or native-response evidence was rewritten.

February–April remains repeatedly inspected development data. The observations share one BTC market history, and execution modes are alternative paths, not independent replications. Synthetic paths, assumed fees and current instrument metadata do not establish historical OKX queues, books, fill probabilities or rules. Lower losses alone do not establish an edge.

Open `reports/index.html` for every case, episode attribution, blocked validation and related verification. Report generation and unit/CI checks are not additional scientific experiments. Real-browser canvas review remains unverified.
