<p align="center"><img src="https://raw.githubusercontent.com/{{REPOSITORY}}/{{TAG}}/.github/media/kairos-dark.svg" width="473" alt="Kairos logo on a dark background"></p>

# 🚀 Kairos {{VERSION}}

Experimental trading software. Start in paper mode; neither model confidence nor backtests establish profitability. Protective exits operate only while the engine is running and connected.

## 📝 Changes since {{PREVIOUS}}

{{CHANGES}}

{{COMPARE}}

## 📦 Install

### 💻 Local Python server

Requires Linux, Python 3.12+ and [uv](https://docs.astral.sh/uv/). Kairos uses aiohttp, not PHP; `php -S` cannot run its backend.

```sh
git clone --branch {{TAG}} --depth 1 https://github.com/{{REPOSITORY}}.git kairos
cd kairos
uv sync --locked --no-dev
test -e .env || install -m 600 .env.example .env
```

Configure your private `.env` using `.env.example`. Paper trading needs Kraken authenticated fee-query access, not order permissions. Model-assisted strategies also need a Jev key and may incur API charges. Leave both live-trading flags disabled.

```sh
uv run --no-sync kairos
```

Open <http://127.0.0.1:8000>. The engine starts paused in Dry-run. Review limits before pressing Start. Do not expose the unauthenticated loopback server to the network.

### 🌐 nginx and systemd

Use the same checkout and dependency setup, then adapt [`deploy/kairos.service`](https://github.com/{{REPOSITORY}}/blob/{{TAG}}/deploy/kairos.service) and [`deploy/nginx.conf`](https://github.com/{{REPOSITORY}}/blob/{{TAG}}/deploy/nginx.conf) for your service account, paths, hostname, certificates and Basic Auth password file. Keep the backend on loopback, protect **all** routes with TLS and authentication, and set `PUBLIC_ORIGIN` to the exact HTTPS origin.

Validate nginx configuration with `sudo nginx -t` before reloading it. Follow the [deployment instructions](https://github.com/{{REPOSITORY}}/blob/{{TAG}}/OPERATIONS.md#nginx-and-systemd) to install and start the service. Kairos still starts paused; service startup is not authorization to trade.

Optional locally licensed Font Awesome Pro fonts are not distributed in the release. Supply them locally or use the bundled icon fallbacks.

## 🔄 Update

Read these notes and the [operations guide](https://github.com/{{REPOSITORY}}/blob/{{TAG}}/OPERATIONS.md) before updating. Preserve `.env`, the configured `DATA_DIR`, database and optional font assets. Never reset a portfolio as an update step.

### 💻 Local Python server

1. Stop the engine in the dashboard, then stop the foreground server. Holdings remain; local protection is suspended during the update.
2. Back up the SQLite database using the standard library. Adjust `data` if you configured a different `DATA_DIR`:

   ```sh
   python3 -c "import sqlite3; src=sqlite3.connect('data/kairos.sqlite3'); dst=sqlite3.connect('data/kairos-before-{{VERSION}}.sqlite3'); src.backup(dst); dst.close(); src.close()"
   ```

3. With a clean tracked working tree, fetch and check out the release without overwriting local configuration:

   ```sh
   git fetch origin tag {{TAG}}
   git switch --detach {{TAG}}
   uv sync --locked --no-dev
   uv run --no-sync kairos
   ```

4. Confirm the retained balances, orders, protection and limits, then resume paper mode. A long outage can require rolling-history warm-up.

### 🌐 nginx and systemd

Stop the engine, then stop your installed Kairos service before backing up and updating its checkout using the commands above. Restart that service afterward; use `systemctl --user` for a user unit or `sudo systemctl` for a system unit. Keep the configured service user and filesystem permissions intact. nginx needs no restart for an application-only update; validate and reload it only if its configuration changed.

Verify service health and retained state before resuming the engine. Private diagnostics are in `DATA_DIR/kairos-errors.jsonl`; never publish credentials or unredacted logs in an issue.

## ✅ Verification

{{VERIFICATION}}

Source archives are available below. This release does not bundle credentials, portfolio data or licensed fonts, and publishing it does not deploy or restart an existing trading instance.
