# Kairos

[https://kyaulabs.com/](https://kyaulabs.com/)

[![Contributor Covenant](https://img.shields.io/badge/contributor%20covenant-2.1-4baaaa.svg?logo=open-source-initiative&logoColor=4baaaa)](CODE_OF_CONDUCT.md) &nbsp; [![Conventional Commits](https://img.shields.io/badge/conventional%20commits-1.0.0-fe5196?style=flat&logo=conventionalcommits)](https://www.conventionalcommits.org/en/v1.0.0/) &nbsp; [![GitHub](https://img.shields.io/github/license/kyaulabs/kairos?logo=creativecommons)](LICENSE) &nbsp; [![Gitleaks](https://img.shields.io/badge/protected%20by-gitleaks-blue?logo=git&logoColor=seagreen&color=seagreen)](https://github.com/zricethezav/gitleaks)  
[![Semantic Versioning](https://img.shields.io/github/v/release/kyaulabs/kairos?include_prereleases&logo=semver&sort=semver)](https://semver.org) &nbsp; [![Discord](https://img.shields.io/discord/88713030895943680?logo=discord&color=blue&logoColor=white)](https://discord.gg/DSvUNYm)

Kairos is an experimental Kraken trading bot that uses TypeSafe's Jev model to assess trading opportunities. A browser dashboard shows live prices, model assessments, orders, fills, and portfolio equity. Code enforces sizing and execution limits; Jev does not control those safeguards.

**Start with dry-run.** Live spot execution is implemented but has not been verified with real orders. Neither model confidence nor simulated returns establish profitability, and full-allocation trading can lose the entire allocation.

- [Capabilities](#capabilities)
- [Quick start](#quick-start)
- [Your first dry-run](#your-first-dry-run)
- [Strategies](#strategies)
- [Capital and recovery](#capital-and-recovery)
- [Live trading](#live-trading)
- [Deployment](#deployment)
- [Development and CI](#development-and-ci)
- [Project layout](#project-layout)
- [Documentation](#documentation)

## Capabilities

- Switch markets and strategies from the dashboard after stopping and reconciling orders.
- Use real Kraken data and Jev assessments in dry-run, without submitting exchange orders.
- Monitor prices with self-hosted D3 charts, including pan/zoom, crosshairs, buy/sell markers, and an equity view.
- Configure starting capital, order and exposure caps, daily loss limits, fee assumptions, and reinvestment.
- Persist settings, order intents, fills, and portfolio accounting in SQLite.
- Protect network access with nginx Basic Auth over TLS.

| Product | Dry-run | Trading |
| --- | --- | --- |
| USD-quoted crypto spot | Simulated fills using real market data | Guarded Kraken limit orders |
| Crypto margin | Separate long/short simulation | Disabled |
| Direct US stocks and ETFs | Not yet supported | Blocked pending a documented equities API |
| xStocks | Not implemented | Not implemented |

Kraken's US stock product is not the same as xStocks. Kairos does not substitute tokenized assets for direct equities or submit guessed stock orders. See [supported trading](OPERATIONS.md#supported-trading) for the API limitation.

Public prices stream over WebSocket. Live fills are detected during the order-reconciliation cycle, then sent to the browser; they are not streamed directly from Kraken's private execution channel.

## Quick start

Requires Linux, Python 3.12 or later, [uv](https://docs.astral.sh/uv/), and a Jev API key. A read-only Kraken key is enough for authenticated balance and fee checks. There is no frontend build step; Node.js is needed only for development checks.

Active development is on `develop`:

```sh
git clone --branch develop https://github.com/kyaulabs/kairos.git
cd kairos
uv sync --locked

# Create a private environment file only if one does not already exist.
if [ ! -e .env ]; then
  install -m 600 .env.example .env
fi
```

Use your editor to configure `.env` from [.env.example](.env.example). Keep it private and out of Git.

| Setting | Purpose |
| --- | --- |
| `JEV_API_KEY` | TypeSafe API access; assessments can incur charges |
| `KRAKEN_API_KEY`, `KRAKEN_PRIVATE_KEY` | Kraken API key and signing secret |
| `JEV_MODEL` | Defaults to `jev-latest` |
| `ALLOW_LIVE_TRADING` | Defaults to `false`; leave disabled for dry-run |
| `PUBLIC_ORIGIN` | Exact browser origin; defaults to `http://127.0.0.1:8000` |
| `PORT` | Loopback HTTP port, default `8000` |
| `DATA_DIR` | Persistent state directory, default `data` |

```sh
uv run kairos
```

Open <http://127.0.0.1:8000>. The server binds to loopback and always starts **paused in Dry-run**. Do not expose it directly to the network; use [nginx](#deployment) for authenticated access.

## Your first dry-run

1. Choose a crypto market, strategy, and the **Spot** product.
2. Set **Paper starting balance** to the amount you want to simulate, such as `100` USD.
3. Click **Use full starting allocation**, then review the order cap, exposure cap, daily loss limit, and fee assumptions.
4. Save settings and click **Reset selected paper portfolio** to apply the new starting balance.
5. Press **Start** and watch the assessments, fills, and equity chart.

The default paper profile starts with $1,000 and reinvestment enabled. Changing the starting balance does not change an existing portfolio until you reset it. A reset discards simulated holdings, not live holdings, and retains labeled order/event history.

Dry-run uses real feeds and real Jev calls. Taker fills are estimated from visible depth; maker fills require later crossing trades and assume limited participation. Neither model reconstructs actual queue priority or market impact.

## Strategies

| Strategy | Behavior |
| --- | --- |
| Higher-timeframe trend | Assesses completed 15-minute through daily candles. Code calculates trend and cost filters; Jev selects buy, sell, or hold. |
| Market making | Posts one fee-aware, post-only quote and reconciles it before replacement. Quotes expire after 30 seconds. This is rate-limited market making, not exchange-grade HFT. |
| Triangular arbitrage | Checks both directions of a BTC/ETH-bridged spot triangle after fees, rounding, depth, and slippage. Jev can veto a computed opportunity. |

Arbitrage legs execute sequentially, not atomically. A failed or partial leg stops the engine with intermediate inventory retained for review. Retail fees may eliminate every observed opportunity.

Existing spot holdings remain tracked when you change markets, but the newly selected strategy does not automatically trade the previous market's holdings. Paper margin uses separate accounting and does not support triangular arbitrage. Read [strategy details](OPERATIONS.md#strategies) and [margin assumptions](OPERATIONS.md#margin-simulation) before using them.

## Capital and recovery

Kairos has no scheduled end, trade-count limit, or profit target. It reuses available capital and proceeds, while allowing hold decisions when no trade qualifies. With reinvestment enabled, order and exposure caps scale with current equity relative to the initial allocation. Sizing reserves fees and respects exchange minimums.

For spot portfolios, **Recover original allocation once above 2× equity** can protect the starting allocation. With a $100 start, the bot attempts to reserve $100 only when active equity is greater than $200. It sells enough bot-owned inventory if cash is needed and execution limits permit, then excludes the reserve from future orders and compounds the remainder.

Recovery happens once and persists across restarts. It is a local accounting exclusion, not a separate Kraken wallet or external withdrawal. The cash stays on Kraken for you to withdraw manually. See [capital recovery](OPERATIONS.md#recover-the-original-investment) for timing and limitations.

## Live trading

Keep the server live-write gate disabled until you have reviewed the [operating guide](OPERATIONS.md#dry-run-and-trading) and tested the strategy with paper funds.

1. Install a dedicated trade-capable Kraken key with the required query, order-creation, and cancellation permissions. **Do not grant withdrawal permission.**
2. Set `ALLOW_LIVE_TRADING=true` and restart. The app still starts paused in Dry-run.
3. Configure a positive live allocation and appropriate order, exposure, loss, and fee settings.
4. Switch **Execution** to **Trading — real funds** and confirm. If paused, press Start; a running engine resumes after successful reconciliation and preflight.

The switch cannot make a read-only key trade. The bot records order intents before submission and uses actual cumulative fills for live accounting. An uncertain submission or cancellation blocks further execution until reconciled.

Stop cancels tracked orders; **it does not sell spot holdings**. Loss limits also stop new trading rather than guarantee a maximum loss. Closing the browser does not stop the engine. Restarting the process does, and unresolved live orders require reconciliation before trading resumes.

## Deployment

Use [deploy/nginx.conf](deploy/nginx.conf) for TLS, Basic Auth, and unbuffered dashboard events. Protect the entire site, including API routes and static assets. Set `PUBLIC_ORIGIN` to the HTTPS hostname and keep the backend port inaccessible from the network.

[deploy/kairos.service](deploy/kairos.service) provides an unprivileged systemd service example. Adapt the hostname, certificates, password file, user, and paths before installing either configuration. Full instructions are in [nginx and systemd](OPERATIONS.md#nginx-and-systemd).

Run only one process per data directory and do not share the bot's Kraken key with another order manager. Back up state while the process is stopped; losing the database loses the bot's allocation and reconciliation history.

## Development and CI

[GitHub Actions CI](.github/workflows/ci.yml) runs on pushes and pull requests, with a manual dispatch trigger. It checks Ruff lint and formatting, JavaScript syntax using Node.js 22, and the regression suite on Python 3.12, 3.13, and 3.14. Dependencies come from `uv.lock`; action revisions are pinned. CI has read-only repository permissions, no trading credentials, and does not run the real-API smoke test.

Run the same application checks locally:

```sh
uv sync --locked --dev
uv run --no-sync ruff check kairos tests
uv run --no-sync ruff format --check --output-format concise kairos tests
uv run --no-sync python -m unittest discover -v
node --check kairos/static/app.js
node --check kairos/static/chart.js
```

The tests use mocked exchange/model responses and local HTTP fixtures. They cover execution gates, partial fills, uncertain submissions, risk limits, arbitrage recovery, principal recovery, paper margin, and API security. They do not verify live profitability or real-order execution.

For an optional integration check, stop the app first and run `uv run kairos-smoke --jev`. This makes read-only Kraken requests and one billable Jev assessment, but no exchange writes. See [verification](OPERATIONS.md#verification).

Install [Gitleaks](https://github.com/gitleaks/gitleaks), then enable the repository hooks once per clone:

```sh
npm install -g @commitlint/cli @commitlint/config-conventional
git config --local core.hooksPath .github/hooks
```

The executable hooks scan staged changes for secrets and validate conventional commit messages. Commits require GPG signatures and detailed bodies. Work on a branch, commit and push each verified change separately, and use a pull request into `develop`; do not commit or push directly to `main` or `develop`.

## Project layout

```text
kairos/                  Exchange/model clients, strategies, accounting, and server
kairos/static/           Dashboard and self-hosted D3 assets
tests/                   Regression tests and exchange/model fixtures
deploy/                  Nginx and systemd examples
.github/workflows/ci.yml Lint and test automation
.env.example             Environment variable names and safe defaults
OPERATIONS.md            Detailed setup, execution, recovery, and deployment guide
```

## Documentation

- [Operating guide](OPERATIONS.md)
- [Contributing](CONTRIBUTING.md) and [security policy](SECURITY.md)
- [Kraken Exchange API](https://docs.kraken.com/exchange/api-reference/overview)
- [TypeSafe Jev documentation](https://docs.typesafe.ai/introduction)
- [Jev Trader](https://github.com/jarrodwatts/jev-trader), the reference project that inspired this bot
- [Project license](LICENSE) and [bundled D3 license](kairos/static/vendor/D3-LICENSE)
