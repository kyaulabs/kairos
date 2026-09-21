# Kairos

Kairos runs Jev-assisted trading strategies against Kraken market data. The browser shows prices, assessments, orders, fills, and portfolio equity through a live event stream. D3 charts support pan, zoom, crosshairs, and fill markers. Public prices stream over WebSocket; live fills are detected during the order-reconciliation cycle, not through a private execution stream.

This is an experimental trading application, not evidence of a profitable strategy. Live spot execution is implemented but has not been verified with real orders. Test it with paper funds before installing a trade-capable key.

## Supported trading

| Product | Dry-run | Trading |
| --- | --- | --- |
| Crypto spot, USD-quoted markets | Real feeds and Jev, simulated fills | Kraken limit orders |
| Crypto margin | Simplified long/short simulation | Blocked |
| Direct US stocks and ETFs | Blocked pending API documentation | Blocked pending API documentation |
| xStocks | Not implemented; not a substitute for US stocks | Not implemented |

Kraken offers direct equities in its US product. However, the linked public Exchange specification does not document US brokerage order submission. Its `AddOrder.asset_class` parameter documents tokenized assets, and `TradeVolume` mentions equity classes, but a fee lookup is not an equities execution API. Kairos displays this limitation rather than submitting guessed stock requests. Stock support remains unfinished until Kraken supplies the applicable market-data, account, and order APIs.

References:

- [Kraken API overview](https://docs.kraken.com/exchange/api-reference/overview)
- [Kraken Spot REST specification](https://docs.kraken.com/openapi/spot-rest.yaml)
- [Direct stock trading on Kraken Pro](https://support.kraken.com/articles/how-to-trade-stocks-on-kraken-pro)
- [TypeSafe evaluation API](https://docs.typesafe.ai/api)
- [Jev numerical limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
- [Jev Trader reference project](https://github.com/jarrodwatts/jev-trader)

## Run locally

Requires Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/). Runtime dependencies are `aiohttp` for HTTP/WebSocket serving and clients, and `python-dotenv` for loading the existing environment file. Accounting, persistence, and signing use Python's standard library. The frontend has no build step.

```sh
uv sync --locked
uv run kairos
```

The process loads `.env` without overwriting exported environment variables. Do not copy `.env.example` over an existing `.env`. The server binds only to `127.0.0.1:8000`. Local access is for development; use authenticated nginx for network access.

The app always starts **paused in Dry-run**, even if the previous process was trading. A browser disconnect does not stop a running engine. A process restart does.

The existing variable names are preserved:

| Variable | Purpose |
| --- | --- |
| `KRAKEN_API_KEY` | Kraken API key |
| `KRAKEN_PRIVATE_KEY` | Kraken base64 signing secret |
| `JEV_API_KEY` | TypeSafe API key |
| `JEV_MODEL` | Defaults to `jev-latest`; pin a version for comparable experiments |
| `ALLOW_LIVE_TRADING` | Defaults to `false`; only `true` permits exchange writes |
| `PUBLIC_ORIGIN` | Exact browser origin, including scheme and nonstandard port |
| `PORT` | Loopback listener port; defaults to `8000` |
| `DATA_DIR` | SQLite database and process lock; defaults to `data` |

Only one process may own a data directory. Do not run multiple bot instances against the same Kraken key, share that key with another order manager, or manually spend its allocated balances. Use a dedicated key and preferably a dedicated account/subaccount. External account activity can invalidate the local allocation and order accounting.

## Starting capital and continuous operation

The initial paper profile is $1,000, full-allocation sizing, and reinvestment enabled. There is no trade-count limit, profit target, or scheduled end. Available funds are reused across trades, including realized proceeds. The bot can hold cash or inventory when there is no qualifying action; it does not force trades just to stay busy.

To start with $100:

1. Stop the engine.
2. Set **Paper starting balance** to `100`.
3. Click **Use full starting allocation**. This sets the starting order and exposure caps to `100` and enables reinvestment.
4. Set the daily loss limit and fee assumptions you want.
5. Save settings, then **Reset selected paper portfolio** to apply the new starting balance.
6. Choose the market, strategy, and Spot product; save and press Start.

Reinvestment scales both caps by `current active equity / initial allocation`. A $100 starting order cap becomes $150 when equity is $150, or $80 when equity is $80. Sizing reserves fees and rounds to Kraken's lot size; it cannot invest literally every last fractional cent. The daily loss limit is an absolute USD amount and does not scale. Turning reinvestment off makes the caps fixed USD amounts.

Paper defaults are not recommended risk limits for real money. The full-allocation profile can lose the entire allocation. Stale data, invalid responses, loss limits, insufficient margin, or uncertain order outcomes may stop the engine. There is no promise of maximum returns or uninterrupted exchange availability.

Settings and accounting persist in SQLite. Changing the paper starting balance does not alter an existing paper ledger until you reset it. A reset discards that portfolio's simulated holdings and recovery state, but retains the labeled order/event history. It never resets live holdings.

## Recover the original investment

Enable **Recover original allocation once above 2× equity** for a spot portfolio. The check interval is configurable and is evaluated while the engine is running; it cannot run more frequently than an engine cycle.

For an original $100 allocation:

- At exactly $200 active equity, nothing happens.
- Above $200 net active equity, the bot attempts to reserve $100.
- If necessary, it sells bot-owned inventory through bounded IOC limit orders. Minimum sizes, order caps, fees, price bounds, and actual fills still apply. It may take several checks to raise enough cash.
- While recovery is pending, it does not reinvest the cash being raised.
- It reserves only when cash is available and net active equity still exceeds the threshold.
- The $100 is removed from the bot's spendable ledger. The remainder continues compounding.

The reserve is a **local accounting exclusion**, not a separate Kraken wallet or bank transfer. Kraken still holds that cash in the same account. The dashboard labels it as protected reserve, and the bot does not include it in subsequent order sizing. Other account users, applications, or an explicit increase to the bot allocation could still spend account cash.

Recovery happens once per portfolio, persists across restarts, and does not register as a trading loss. Its amount is the original allocation, not a later increase to the allocation. There are no withdrawal or transfer API calls, no withdrawal destination settings, and no need to grant withdrawal permission. You withdraw the reserved amount manually. This feature is not available for the simplified paper-margin portfolio.

## Strategies

**Higher-timeframe trend:** uses completed 1-minute, 5-minute, 15-minute, 30-minute, hourly, four-hour, or daily candles. The default is 15 minutes; shorter intervals increase decision frequency, noise, and potential fee costs. Code calculates 8/21-period averages and historical returns. Jev assesses buy/sell/hold; code enforces trend and cost filters. Each completed candle is assessed at most once. Spot sells only reduce bot-owned inventory. Historical momentum is not a forecast of the next move.

**Market making:** asks Jev which side to quote. It places one post-only order, with a fee-aware offset from the midpoint, and reconciles/cancels before replacing it. Quotes expire at Kraken after 30 seconds. The default cycle is 15 seconds; the configurable minimum is 10 seconds. This is rate-limited market making, not exchange-grade HFT. Wide fee-aware quotes may rarely fill, and filled quotes remain exposed to adverse selection.

**Triangular arbitrage:** considers both directions of one BTC/ETH-bridged triangle for the selected USD pair. Code calculates the three legs using visible depth, lot rounding, fees, and bounded worst-case prices. Jev may veto the opportunity, but does not perform the arithmetic. The entire route is rechecked after inference. Orders execute sequentially; the cycle is not atomic. A failed or partial leg stops the engine with its intermediate inventory preserved for review. Not every pair has a suitable triangle, and retail fees may eliminate every observed opportunity.

Switch strategies or markets by stopping, changing settings, saving, and starting again. Existing spot holdings stay in the ledger and count toward portfolio exposure. A new strategy does not automatically manage or liquidate positions in a previously selected market.

## Dry-run and Trading

Dry-run uses real Kraken data and real Jev evaluations. It never calls `AddOrder`, including `validate=true`, and does not submit exchange cancellations for paper orders. The current read-only key is sufficient for authenticated read checks. Paper trading itself does not need private Kraken calls.

Paper taker fills walk displayed depth up to the limit. Paper maker fills require a later public trade strictly through the resting limit on the opposing aggressor side, capped at 10% of that trade's volume. These estimates do not reconstruct queue priority, hidden liquidity, rebates, or your market impact. Simulated results cannot establish live profitability.

For real spot trading:

1. Install a dedicated trade-capable key with balance/fee queries, open/closed order queries, create/modify orders, and cancel/close permissions. Do **not** grant withdrawal permissions.
2. Set `ALLOW_LIVE_TRADING=true` on the server and restart. The engine still starts paused in Dry-run.
3. Configure a positive **Live capital allocation**, order/exposure caps, daily loss limit, and fee assumptions. Caps must fit the live allocation. For full allocation, set both starting caps equal to the live allocation and keep reinvestment enabled.
4. Change **Execution** to **Trading — real funds** and confirm. Kraken balances, status, and fees are checked. A read-only key cannot execute an order; order authorization remains Kraken's responsibility.
5. Press Start if paused. If the engine was already running when you changed modes, it resumes after successful reconciliation and preflight.

The switch uses the same engine, not a mock live implementation. Directional/arbitrage orders are IOC limits; maker orders are post-only GTD limits. Every intent is saved before submission with a unique client order ID. Accepted orders are not counted as fills. Cumulative exchange execution quantities, costs, and fees drive accounting.

Changing back to Dry-run stops the loop and reconciles tracked orders first. If cancellation or submission status is uncertain, the switch fails closed and retains its previous mode, paused. No blind write retries occur. Use Reconcile and inspect the client ID at Kraken; a missing order after an ambiguous submission is not proof that it was never accepted.

Live allocation tracks only funds assigned to this bot and assets bought by it. Pre-existing account crypto is not imported or sold. Fee preference is quote currency (`fciq`); the application records Kraken's quote-denominated cumulative fees. Balance discrepancies stop execution rather than silently inventing funds.

## Margin simulation

Margin has a separate paper portfolio. It supports long and short positions on markets advertising the selected 2×–5× leverage. Opposite-side fills reduce an existing position before a reversal can occur. There is no live margin path and no leverage field in live spot requests.

The simulation tracks collateral cash, signed positions, average entry, initial margin, fees, unrealized P&L, and a configurable maintenance threshold. Rollover charges accrue proportionally over elapsed time using a configurable four-hour rate, including time spent paused. Maintenance monitoring continues while the margin product is selected, even when the trading strategy is paused. Below the threshold it attempts simulated liquidation against available depth and stops; a partial liquidation can leave remaining positions.

This is a simplified cross-collateral research model. It does not reproduce Kraken's collateral haircuts, asset-specific borrowing schedules, eligibility rules, liquidation engine, or jurisdictional restrictions. It is not evidence that your US account is eligible for live margin. Close positions through the strategy or reset the paper margin portfolio before changing its market, product, leverage, or funding assumptions.

## Risk and recovery

Code, not Jev, controls order sizes, inventory, exposure, fees, minimum orders, staleness, and daily loss. The UI cannot disable the server's live-write gate or enable live margin. Funding/withdrawal endpoints are not allowed by the client.

The daily loss measure is the change in marked-to-market equity since the first valuation of the UTC day, with explicit capital allocation/recovery changes excluded. It includes unrealized P&L and trading fees, but not external Jev API charges. This is not an exchange-held stop-loss: a gap, disconnection, or existing position can exceed the limit. Stop and loss-limit actions cancel tracked orders but **do not liquidate spot holdings**.

GTD expiry limits how long maker orders can remain after a process failure. There is no account-wide cancel-all or dead-man switch that could cancel your unrelated orders. On restart, unresolved live intents block trading until reconciled. Interrupted arbitrage cycles also require explicit acknowledgement after you inspect remaining holdings.

Back up the data directory while the process is stopped, including SQLite WAL files if present. Losing the database loses the bot's allocation, recovery state, and order reconciliation history. Do not start a replacement database against a live account with unresolved bot activity.

## Nginx and systemd

`deploy/nginx.conf` protects the entire site, API, SSE stream, and static assets with HTTP Basic Auth over TLS. The application adds Origin/CSRF checks for changes, a same-origin content security policy, and no-store response headers. Basic Auth has no per-user roles; every authenticated user can control trading.

Example password-file creation:

```sh
sudo htpasswd -cB /etc/nginx/kairos.htpasswd your-user
```

Use `-c` only for the first user; it replaces the file. Ensure nginx can read it and other users cannot. Replace the example hostname and certificate paths in the nginx configuration, then set the application's `PUBLIC_ORIGIN=https://your-hostname` to the exact browser origin. Keep port 8000 inaccessible from the network. No nginx location should bypass authentication.

```sh
sudo nginx -t
sudo systemctl reload nginx
```

For a persistent service, install the project and its locked virtual environment under `/srv/kairos`, create an unprivileged `kairos` user, give it ownership of `data`, and restrict `.env` to that user. `deploy/kairos.service` expects those paths and the existing virtual environment. Do not run multiple workers or instances for the same account.

```sh
sudo install -o kairos -g kairos -m 700 -d /srv/kairos/data
sudo cp deploy/kairos.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kairos
```

After a service restart you must explicitly start the engine again. The example configuration must be adapted and tested on the deployment host; it does not provision DNS, TLS certificates, nginx, or users.

## Verification

Enable the versioned Git hooks once per clone. Install Gitleaks separately and ensure both tools are on `PATH`:

```sh
npm install -g @commitlint/cli @commitlint/config-conventional
git config --local core.hooksPath .github/hooks
```

The hooks are executable in Git. Pre-commit scans the index with Gitleaks and redacts findings; commit-msg validates the supplied message with the repository's commitlint configuration. Missing tools fail the commit rather than skipping checks. Commits must be GPG-signed, have detailed conventional messages, and stay on feature branches. Commit and push each coherent, verified change as it completes.

Run the application checks:

```sh
uv sync --locked --dev
uv run ruff check kairos tests
uv run python -m unittest discover -v
node --check kairos/static/chart.js
node --check kairos/static/app.js
```

Stop the app before the bounded integration check so authenticated nonces remain ordered:

```sh
uv run kairos-smoke --jev
```

This checks public depth, completed candles, a WebSocket tick, authenticated balances/fees, and one real Jev evaluation. The Jev call can incur normal API charges. No order, validation-order, cancellation, or funding call is made. It prints pass/fail summaries, not credentials, balances, or response bodies.

D3 7.9.0 is self-hosted in `kairos/static/vendor/`; its ISC license is included there. Its distribution was checked against the npm package integrity value. There are no browser calls to third-party CDNs.
