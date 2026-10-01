# Execution-first research rerun

**Stage one is blocked. No new historical backtest or model fit was run.**

The revised sequence in [STAGED_PLAN.json](STAGED_PLAN.json) was committed before the audit. Execution validity must pass before momentum benchmarks, cost-aware positioning or later validation. Failed or unverified gates stop progression; they are not permission to relax assumptions until an experiment trades more often.

## Rerun results

| Check | Result |
|---|---|
| Execution/accounting tests | 67 passed; no failures, errors or skips |
| Causal-data and registry tests | 9 passed; no failures, errors or skips |
| Market-rule representation matches adapter | Failed |
| Eligible residual handling matches production | Failed |
| Dated BTC/ETH market rules available | Unverified |
| Historical execution assumptions justified | Unverified |

The software checks reuse Alpaca client/engine fixtures, production HTF regressions and the research simulator's execution tests. They cover partial fills, fee/inventory accounting, cancellations, fresh entries, retained ownership, hard-exit priority and permanent daily-loss halts. Data checks cover delayed availability, gaps, purged labels, immutable artifacts and consumed holdouts. Passing these tests does not establish execution fidelity.

## Concrete blockers

`Alpaca.catalog()` builds each crypto `Pair` from the asset's `min_order_size`, `min_trade_increment` and `price_increment`. Its configured minimum is a **base-asset quantity**, with a separate notional-minimum field. The research simulator instead takes a universal synthetic $10 minimum and a fixed 0.00000001 lot.

The API-shaped fixture demonstrates the difference. At a hypothetical price of $100, a quantity of 0.01 meets the fixture adapter's quantity minimum but fails the simulator's $10 floor. This is a counterexample to representation parity, **not a claim about actual BTC trading rules**. No current or historical authenticated asset catalog was queried, and fixture values must not replace dated market evidence.

`htf.entry_context()` permits an otherwise eligible fresh same-direction entry alongside a residual holding. The simulator's entry branch requires `not self.qty`, so a nonzero residual blocks every new entry. For a synthetic 0.00005 BTC residual and a fresh qualifying setup, the production entry context permits BUY while the simulator remains in `retained_dust` without queuing an entry. Entry-context permission is not broker authorization. The existing full HTF regression also passed: it preserves residual ownership through restart, rejects a purchase solely to clear dust, permits a genuinely eligible addition and eventually closes the combined holding.

These differences matter because tiny partial fills and residual holdings dominated study 001. Its low round-trip counts cannot distinguish a weak momentum signal from the simulator's market-rule and inventory restrictions. Neither a smaller arbitrary minimum nor unconditional dust top-ups would be a valid repair.

Historical executable quotes/depth, fill/cancellation timing and dated BTC/ETH precision/minimum snapshots remain unavailable in the retained bar dataset. Current public bars do not establish those facts. No credential access, broker test or prospective collection was added to this audit.

## Decision

Stop before stage two. Momentum versus cash/passive benchmarks, Boyd-style cost-aware position construction, PBO/DSR inference, volatility sizing and calibration were **not rerun**. Multi-agent trading and reinforcement learning remain deferred.

Before a meaningful development-only baseline, the simulator needs explicit per-asset quantity/lot/tick/notional rules and execution-only residual handling consistent with ownership, fresh-entry and protection requirements. Those changes need reviewed fixtures and an evidence-backed scenario specification. Verified historical execution fidelity is not established by making fixtures pass.

The earlier final window remains consumed and invalidated as confirmatory evidence. It was not read or reopened by this audit. The original plan, results, production code, portfolios and trading permissions are unchanged.

## Reproduction

From the checkout, using the development environment:

```sh
python -m research.check_execution_gate --root /path/to/existing-research-registry
```

The command registers its attempt before running checks. It loads no historical partitions and cannot launch a later experiment. Exit status **2** means the gate is blocked; it is not a successful performance test. Repeating the audit adds another physical attempt and must not replace the prior record.

[STAGED_RESULTS.json](STAGED_RESULTS.json) contains the result hash, exact audit/code/protocol identities, test logs, counterexamples and gate decisions. The original study registry was reused, retaining its one consumed final claim. This invocation added one completed audit with a blocked outcome, not a new final evaluation.
