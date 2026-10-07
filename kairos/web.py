import asyncio
import contextlib
import fcntl
import hmac
import json
import os
import secrets
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

from kairos import diagnostics
from kairos.accounts import SOURCES, account_snapshot
from kairos.alpaca import Alpaca
from kairos.alpaca_engine import AlpacaEngine
from kairos.alpaca_markets import ACCOUNT_SOURCES, AlpacaMarkets
from kairos.clients import Jev, Kraken, ticker_feed
from kairos.domain import CANDLE_INTERVALS, SafetyError
from kairos.engine import Engine
from kairos.exchanges import ExchangeDesk
from kairos.futures_client import FuturesTrading
from kairos.okx import OKX
from kairos.okx_engine import OKXEngine
from kairos.okx_markets import ACCOUNT_SOURCES as OKX_ACCOUNT_SOURCES
from kairos.okx_markets import OKXMarkets
from kairos.operations import DiscordAlerts
from kairos.retail import RetailMarkets, usd_volume
from kairos.settings import schema
from kairos.store import Store, encode

STATIC = Path(__file__).parent / "static"


class Hub:
    def __init__(self):
        self.listeners = set()
        self.tickers = {}
        self.closed = False

    def close(self):
        self.closed = True
        for queue in tuple(self.listeners):
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
        self.listeners.clear()

    def publish(self, kind, data):
        if self.closed:
            return
        if kind == "ticker":
            self.tickers[data["symbol"]] = data
        message = f"event: {kind}\ndata: {encode(data)}\n\n".encode()
        for queue in tuple(self.listeners):
            if queue.full():
                # Disconnect slow readers; reconnect obtains a fresh snapshot/history.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
                self.listeners.discard(queue)
            else:
                queue.put_nowait(message)


@web.middleware
async def security(request, handler):
    if request.method not in {"GET", "HEAD"}:
        if request.app["engine"].shutting_down:
            raise web.HTTPServiceUnavailable(text="Service shutting down")
        token = request.headers.get("X-CSRF-Token", "")
        if (
            request.headers.get("Origin") != request.app["origin"]
            or not hmac.compare_digest(token, request.app["csrf"])
            or request.content_type != "application/json"
        ):
            raise web.HTTPForbidden(text="Origin, content type, or CSRF token rejected")
    try:
        return await handler(request)
    except SafetyError as exc:
        error_id = diagnostics.capture(exc, "api:" + request.match_info.route.resource.canonical)
        request.app["engine"].event(
            "request-error",
            {
                "message": str(exc),
                "error_id": error_id,
                "route": request.match_info.route.resource.canonical,
                "method": request.method,
                "status": 409,
            },
        )
        return web.json_response({"error": str(exc)}, status=409)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        diagnostics.capture(exc, "api-invalid-request")
        return web.json_response({"error": "Invalid request"}, status=400)
    except web.HTTPException:
        raise
    except Exception as exc:
        error_id = diagnostics.capture(exc, "api:" + request.match_info.route.resource.canonical)
        # Keep exception type/route, never headers, bodies, query strings or raw
        # exception text, which can contain credentials and upstream responses.
        request.app["engine"].event(
            "request-error",
            {
                "message": "Internal request failure",
                "error_id": error_id,
                "route": request.match_info.route.resource.canonical,
                "method": request.method,
                "status": 500,
                "exception_type": type(exc).__name__,
            },
        )
        return web.json_response(
            {"error": "Internal error; inspect engine state and reconcile"}, status=500
        )


async def response_headers(request, response):
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        }
    )


async def state(request):
    engine = request.app["engine"]
    return web.json_response(
        {**engine.snapshot(), "csrf": request.app["csrf"], "tickers": request.app["hub"].tickers}
    )


async def settings_schema(request):
    contract = schema()
    contract["exchange"] = request.app["engine"].exchange
    if isinstance(request.app["engine"], OKXEngine):
        engine = request.app["engine"]
        quote = engine.settings["quote"]
        contract["strategies"] = {
            name: dict(value) for name, value in contract["strategies"].items()
        }
        contract["fields"]["live_budget" if engine.mode == "paper" else "paper_balance"][
            "products"
        ] = ["unavailable"]
        contract["fields"]["product"]["choices"] = {"spot": "Cash crypto spot only"}
        for name, strategy in contract["strategies"].items():
            if name != "twap":
                strategy["products"] = []
                strategy["help"] = (
                    "Unavailable on OKX. Use the separate finite execution-cycle diagnostic or finite spot TWAP; no predictive strategy has been ported."
                )
        for key in (
            "recover_initial",
            "recovery_check_seconds",
            "reinvest_profits",
            "api_auto_recovery",
        ):
            contract["fields"][key]["products"] = ["unavailable"]
        contract["field_labels"] = {
            key: f"{label} · {quote}"
            for key, label in {
                "paper_balance": "Hosted demo initial allocation",
                "live_budget": "Real-money initial allocation",
                "order_size": "Order cap",
                "max_exposure": "Exposure cap",
                "daily_loss": "Daily loss limit",
                "twap_limit": "Parent limit",
            }.items()
        }
        contract["account_sources"] = OKX_ACCOUNT_SOURCES
        contract["help"] = {
            "fees": engine.client.fee_note,
            "data": "Environment-pinned OKX U.S. books5 snapshots. Native timestamps and connection generations gate execution. Same-environment native REST candles are chart-only, never execution inputs. No other venue/environment fallback.",
        }
        for key in ("paper_balance", "live_budget"):
            contract["fields"][key]["help"] = (
                "Explicit initial allocation in the selected spending currency, separate from total account assets. A bound allocation is never resized/reset automatically. No funding, transfers or conversions are implemented. Every finite run still needs a separate preview and authorization."
            )
        contract["fields"]["pair"]["help"] = (
            "Choose an authenticated account-enabled OKX spot instrument and actual spending currency in the execution-cycle preview. USD, USDG, USDC and USDT balances stay distinct. Chart browsing never changes execution selection."
        )
        contract["fields"]["twap_limit"]["help"] = (
            "Enter and save an explicit parent price for finite spot TWAP before requesting its preview. The initial value 1 is an inert configuration placeholder, not a venue quote; preflight requires a currently marketable parent. Each IOC also obeys fresh-book slippage and venue bands."
        )
        for key in ("order_size", "max_exposure", "daily_loss"):
            contract["fields"][key]["help"] = (
                "Risk limit in the explicitly selected spending currency for the allocated bot ledger, not total exchange assets. Bounds never authorize execution or convert currencies. Stop retains holdings; market losses can continue."
            )
    elif isinstance(request.app["engine"], AlpacaEngine):
        contract["exchange"] = "alpaca"
        for name, field in contract["fields"].items():
            field["default"] = AlpacaEngine.defaults[name]
        contract["strategies"] = {
            k: dict(v) for k, v in contract["strategies"].items() if k != "arbitrage"
        }
        contract["fields"]["strategy"]["choices"] = {
            k: v["label"] for k, v in contract["strategies"].items()
        }
        contract["fields"]["product"]["choices"] = {"spot": "Spot · crypto / US stocks / ETFs"}
        for key in ("live_budget", "recover_initial", "recovery_check_seconds"):
            contract["fields"][key]["products"] = ["unavailable"]
        contract["field_labels"] = {
            "paper_balance": "Hosted paper allocation · USD",
        }
        contract["fields"]["paper_balance"]["help"] = (
            "Local USD allocation within a dedicated unused Alpaca paper account. Start validates available broker cash. This does not reset or fund Alpaca. Once connected, the allocation cannot be silently resized. Order/exposure/daily-loss caps remain independently configurable."
        )
        contract["help"] = {
            "fees": request.app["engine"].kraken.fee_note,
            "data": "Free Alpaca crypto data and IEX-only equity quotes. IEX is not consolidated NBBO. Equity execution uses whole-share limit orders during regular sessions only. Hosted paper fills do not model actual queue position, market impact or regulatory fees.",
        }
        contract["fields"]["candle_minutes"]["help"] = (
            "Alpaca crypto HTF uses 30 consecutive completed native bars from Alpaca US. "
            "At 60 minutes these are UTC-aligned hourly bars, not rolling minute aggregates. "
            "New setups require a new completed bar; execution and protective exits still "
            "check fresh quotes each engine cycle. Missing native bars block entries."
        )
        contract["account_sources"] = ACCOUNT_SOURCES
        contract["fields"]["pair"]["help"] = (
            "The saved Alpaca execution market, independent of the chart. Crypto supports passive limit strategies and scheduled programs. Stocks/ETFs support long-only whole-share DCA, TWAP and rebalancing; no equity HTF, scalp, margin or short sales. Slice budgets must afford at least one share."
        )
        for name, strategy in contract["strategies"].items():
            strategy["help"] = (
                "Alpaca hosted paper uses broker-side fills, not Kairos local simulation. "
                "Crypto passive entries use non-crossing GTC limits with local cancellation, "
                "no post-only guarantee and no taker fallback. Queues, price impact and "
                "actual liquidity are not faithfully simulated by hosted paper."
                if name in {"htf", "maker", "scalp"}
                else "Alpaca hosted-paper scheduled limit orders. Equity quantities are whole shares, "
                "long-only and regular-session only, using free IEX quotes. Schedules use wall "
                "time: closed-session/missed slots are skipped, not caught up. Mixed rebalance "
                "baskets wait for the equity session. No broker account resets or funding."
            )
    else:
        contract["fields"]["htf_policy"]["choices"] = {
            "pullback-v1": "Legacy pullback · experimental"
        }
        contract["fields"]["api_auto_recovery"]["products"] = ["unavailable"]
    return web.json_response(contract)


async def catalog(request):
    # Discovery is separate from Engine.kraken.pairs, the unchanged execution catalog.
    retail, exchange = request.app["retail"], request.app["engine"].exchange
    return web.json_response(
        [{**row, "exchange": exchange} for row in (await retail.catalog()).values()]
    )


async def markets(request):
    # Share a bounded-rate public ticker snapshot across all dashboard tabs.
    async with request.app["market_lock"]:
        if isinstance(request.app["retail"], (AlpacaMarkets, OKXMarkets)):
            wanted = set(request.query["ids"].split(",")) - {""} if "ids" in request.query else None
            return web.json_response(
                await request.app["retail"].market_snapshot(
                    wanted,
                    request.app["engine"].settings["pair"],
                    volume_ids=set(request.query.get("volume_ids", "").split(",")) - {""},
                )
            )
        cached = request.app["market_cache"]
        if not cached or time.monotonic() >= cached["expires"]:
            engine = request.app["engine"]
            retail = request.app["retail"]
            instruments = await retail.catalog()
            extra, errors = await retail.extra_quotes()
            errors = {**retail.catalog_errors, **errors}
            spot = request.app["spot_quote_cache"]
            try:
                tickers = await engine.kraken.market_tickers()
                spot.update(values=tickers, received=time.time())
            except SafetyError:
                errors["spot"] = (
                    "Spot quotes unavailable; prior prices retain their original timestamps"
                )
            changes = request.app["market_change_cache"]
            if not changes or time.monotonic() >= changes["expires"]:
                values = await engine.kraken.market_changes(
                    {row["symbol"]: row["id"] for row in retail.extra["xstocks"].values()}
                )
                changes.update(
                    values=values,
                    received=time.time() if values else None,
                    expires=time.monotonic() + 60,
                )
            rates = retail.usd_rates(spot.get("values", {}))
            rows = []
            for identifier, instrument in instruments.items():
                quote = (
                    extra.get(identifier, {})
                    if instrument["kind"] in {"xstocks", "futures"}
                    else {
                        **spot.get("values", {}).get(identifier, {}),
                        "received": spot.get("received"),
                    }
                )
                derivative = instrument["kind"] == "futures"
                rows.append(
                    {
                        **instrument,
                        **quote,
                        **usd_volume(instrument, quote, rates, spot.get("received")),
                        "change_pct": quote.get("change_pct")
                        if derivative
                        else changes["values"].get(identifier),
                        "change_received": quote.get("received")
                        if derivative
                        else changes["received"],
                    }
                )
            data = {
                "received": max((row.get("received") or 0 for row in rows), default=0),
                "change_received": changes["received"],
                "errors": list(errors.values()),
                "markets": rows,
            }
            cached.update(expires=time.monotonic() + 10, data=data)
        data = cached["data"]
        if "ids" in request.query:
            wanted = set(request.query["ids"].split(",")) - {""}
            rows = [row for row in data["markets"] if row["id"] in wanted]
            if wanted != {row["id"] for row in rows}:
                raise SafetyError("Unknown market in quote selection")
            data = {**data, "markets": rows, "partial": True}
        return web.json_response(data)


async def candles(request):
    minutes = int(request.query["interval"])
    if minutes not in CANDLE_INTERVALS:
        raise SafetyError("Unsupported candle interval")
    # Share short-lived public snapshots across tabs without caching strategy inputs.
    async with request.app["candle_lock"]:
        retail = request.app["retail"]
        market = (await retail.catalog()).get(request.query["pair"])
        if market is None:
            raise SafetyError("Unsupported chart market")
        cache = request.app["candle_cache"]
        for key in list(cache):
            if time.monotonic() >= cache[key][0]:
                del cache[key]
        key = (market["id"], minutes)
        if key not in cache or time.monotonic() >= cache[key][0]:
            rows = await retail.candles(market, minutes)
            data = {
                "pair": market["id"],
                "interval": minutes,
                "received": time.time(),
                "volume_unit": market["chart_volume_unit"],
                "source": market.get("candle_source", ""),
                "candles": rows,
            }
            if len(cache) >= 32:
                del cache[next(iter(cache))]
            cache[key] = (time.monotonic() + 5, data)
        return web.json_response(cache[key][1])


async def history(request):
    return web.json_response(request.app["engine"].store.history())


async def accounts(request):
    source = request.match_info["source"]
    alpaca = isinstance(request.app["retail"], AlpacaMarkets)
    okx = isinstance(request.app["retail"], OKXMarkets)
    if source not in (OKX_ACCOUNT_SOURCES if okx else ACCOUNT_SOURCES if alpaca else SOURCES):
        raise web.HTTPNotFound()
    async with request.app["account_lock"]:
        cache = request.app["account_cache"]
        if source not in cache or time.monotonic() >= cache[source][0]:
            retail = request.app["retail"]
            try:
                data = (
                    await retail.account_snapshot(source)
                    if alpaca or okx
                    else await account_snapshot(retail.spot, retail.futures, source)
                )
            except (KeyError, ValueError, TypeError, AttributeError):
                raise SafetyError("Account response could not be interpreted safely") from None
            data["received"] = time.time()
            cache[source] = (time.monotonic() + 30, data)
        return web.json_response(cache[source][1])


async def manual_exit_preview(request):
    engine = request.app["engine"]
    if not isinstance(engine, AlpacaEngine):
        raise SafetyError("Manual qualification liquidation is Alpaca paper only")
    from kairos import manual_exit

    return web.json_response(await manual_exit.preview(engine))


async def command(request):
    data = await request.json()
    action = request.match_info["action"]
    desk = request.app.get("exchanges")
    if action == "exchange":
        if desk is None:
            raise SafetyError("Exchange switching is unavailable in this test instance")
        engine = await desk.switch(data["exchange"], data.get("confirmation"))
        return web.json_response(engine.snapshot())
    if desk and desk.lock.locked() and action != "stop":
        raise SafetyError("Exchange switch in progress; retry after it completes")
    engine = request.app["engine"]
    if action in {"okx-preview", "okx-authorize"}:
        if not isinstance(engine, OKXEngine):
            raise SafetyError("Select the intended isolated OKX environment first")
        from kairos import okx_cycle

        if action == "okx-preview":
            proposal = await okx_cycle.preview(engine, data)
            request.app["feed_restart"].set()
            return web.json_response({"preview": proposal, "state": engine.snapshot()})
        if set(data) != {"preview_id", "confirmation"}:
            raise SafetyError("Authorize the exact saved OKX preview, not replacement parameters")
        await okx_cycle.authorize(engine, data["preview_id"], data["confirmation"])
    elif action == "settings":
        await engine.configure(data)
        request.app["feed_restart"].set()
    elif action == "mode":
        await engine.set_mode(data["mode"], data.get("confirmation"))
    elif action == "start":
        await engine.start(confirmation=data.get("confirmation"))
    elif action == "restart":
        await engine.start(restart=True, confirmation=data.get("confirmation"))
        request.app["feed_restart"].set()
    elif action == "stop":
        await engine.stop()
    elif action == "reconcile":
        if "external_exit_order_id" in data and not isinstance(engine, AlpacaEngine):
            raise SafetyError("Manual qualification liquidation is Alpaca paper only")
        if "initial_funding_id" in data:
            if not isinstance(engine, AlpacaEngine):
                raise SafetyError("Initial paper funding acknowledgement is Alpaca-only")
            await engine.reconcile_initial_funding(
                data["initial_funding_id"], data.get("confirmation")
            )
        elif isinstance(engine, AlpacaEngine):
            if "external_exit_order_id" not in data:
                raise SafetyError(
                    "Alpaca account checks are automatic; manual Reconcile is not required"
                )
            from kairos import manual_exit

            await manual_exit.reconcile(
                engine,
                data.get("qualification_id"),
                data["external_exit_order_id"],
                data.get("evidence_hash"),
                data.get("confirmation"),
            )
        else:
            await engine.reconcile(data.get("acknowledge") is True)
    elif action == "qualify-paper":
        if not isinstance(engine, AlpacaEngine):
            raise SafetyError("Execution qualification is Alpaca paper only")
        await engine.qualify_paper(data.get("confirmation"), data.get("previous_qualification_id"))
    elif action == "qualify-exit":
        if not isinstance(engine, AlpacaEngine):
            raise SafetyError("Qualification recovery is Alpaca paper only")
        from kairos.qualification_exit import recover

        await recover(engine, data.get("qualification_id"), data.get("confirmation"))
    elif action == "paper-order":
        await engine.paper_order_history(
            data["order_id"], data["operation"], data.get("confirmation", "")
        )
    elif action == "reset-paper":
        await engine.reset_paper()
    elif action == "reset-program":
        await engine.reset_program(data.get("confirmation"))
    elif action == "close-futures":
        await engine.close_futures(data.get("confirmation"))
    else:
        raise web.HTTPNotFound()
    return web.json_response(engine.snapshot())


async def events(request):
    hub = request.app["hub"]
    if hub.closed:
        raise web.HTTPServiceUnavailable(text="Service shutting down")
    if len(hub.listeners) >= 12:
        raise web.HTTPServiceUnavailable(text="Too many event streams")
    queue = asyncio.Queue(maxsize=128)
    hub.listeners.add(queue)
    response = web.StreamResponse(
        headers={"Content-Type": "text/event-stream", "X-Accel-Buffering": "no"}
    )
    try:
        await response.prepare(request)
        await response.write(
            f"event: state\ndata: {encode(request.app['engine'].snapshot())}\n\n".encode()
        )
        while True:
            try:
                async with asyncio.timeout(15):
                    message = await queue.get()
                if message is None:
                    break
                await response.write(message)
            except TimeoutError:
                await response.write(b": heartbeat\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        hub.listeners.discard(queue)
    return response


async def feeds(app):
    while True:
        engine = app["engine"]
        candle = None
        spot = engine.settings["product"] != "futures"
        if spot and engine.settings["strategy"] == "scalp":
            candle = (
                engine.resolve(engine.settings["pair"]),
                1,
                engine.settings["scalp_window"] + 1,
            )
        await engine.kraken.market_data.configure(
            engine.fee_scope if spot else [], candle, max_age=engine.settings["stale_seconds"]
        )
        symbols = ["BTC/USD", "ETH/USD"]
        if engine.settings["product"] != "futures" and engine.settings["pair"]:
            symbols = list(
                dict.fromkeys([engine.resolve(engine.settings["pair"]).symbol, *symbols])
            )

        def publish(kind, data, source=engine):
            if app["engine"] is source:
                app["hub"].publish(kind, data)

        task = (
            None
            if isinstance(engine, (AlpacaEngine, OKXEngine))
            else asyncio.create_task(ticker_feed(app["session"], symbols, publish))
        )
        try:
            await app["feed_restart"].wait()
            app["feed_restart"].clear()
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


async def shutdown(app):
    # aiohttp drains handlers BEFORE cleanup_ctx. Never leave SSE handlers or
    # the trading loop alive throughout that grace period.
    engine = app["engine"]
    engine.shutting_down = True
    app["hub"].close()
    try:
        await engine.stop()  # Latches Stop before awaiting an in-flight cycle.
    except Exception as exc:
        error_id = diagnostics.capture(exc, "shutdown-reconciliation")
        engine.event(
            "error",
            {
                "error_id": error_id,
                "message": "Shutdown reconciliation incomplete; inspect tracked orders before restarting",
            },
        )


async def lifecycle(app):
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)  # noqa: ASYNC240 -- startup, before serving
    lock = (data_dir / "engine.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("Another Kairos process owns this data directory") from None
    diagnostic_handler = diagnostics.configure(data_dir / "kairos-errors.jsonl")
    store = Store(str(data_dir / "kairos.sqlite3"))
    async with aiohttp.ClientSession() as session:
        kraken = Kraken(
            session,
            store,
            os.environ.get("KRAKEN_API_KEY", ""),
            os.environ.get("KRAKEN_PRIVATE_KEY", ""),
            os.environ.get("ALLOW_LIVE_TRADING", "false").lower() == "true",
        )
        jev = Jev(
            session,
            os.environ.get("JEV_API_KEY", ""),
            os.environ.get("JEV_MODEL", Jev.DEFAULT_MODEL),
        )
        futures = FuturesTrading(
            session,
            os.environ.get("KRAKEN_FUTURES_API_KEY", ""),
            os.environ.get("KRAKEN_FUTURES_PRIVATE_KEY", ""),
            kraken.allow_live
            and os.environ.get("ALLOW_FUTURES_TRADING", "false").lower() == "true",
        )
        alpaca_store = Store(str(data_dir / "alpaca-paper.sqlite3"))
        alpaca = Alpaca(
            session,
            os.environ.get("ALPACA_PAPER_API_KEY", ""),
            os.environ.get("ALPACA_PAPER_SECRET_KEY", ""),
            allow_paper=os.environ.get("ALLOW_ALPACA_PAPER_TRADING", "false").lower() == "true",
        )
        okx_stores = {
            name: Store(str(data_dir / filename))
            for name, filename in (("okx", "okx-live.sqlite3"), ("okx-demo", "okx-demo.sqlite3"))
        }
        okx_clients = {}
        for name, prefix, environment in (("okx", "OKX", "live"), ("okx-demo", "OKX_DEMO", "demo")):
            gate = os.environ.get(f"ALLOW_{prefix}_TRADING", "false").lower() == "true"
            okx_clients[name] = OKX(
                session,
                os.environ.get(prefix + "_API_KEY", ""),
                os.environ.get(prefix + "_SECRET_KEY", ""),
                os.environ.get(prefix + "_PASSPHRASE", ""),
                environment=environment,
                allow_writes=gate and (environment == "demo" or kraken.allow_live),
            )
        alerts = DiscordAlerts(session, os.environ.get("KAIROS_DISCORD_WEBHOOK_URL", ""))

        def alpaca_engine(publish):
            engine = AlpacaEngine(alpaca_store, alpaca, jev, publish, observe=True)
            engine.operations.alerts = alerts
            return engine

        desk = ExchangeDesk(
            app,
            store,
            {
                "kraken": lambda publish: Engine(store, kraken, jev, publish, futures=futures),
                "alpaca": alpaca_engine,
                "okx": lambda publish: OKXEngine(
                    okx_stores["okx"], okx_clients["okx"], jev, publish
                ),
                "okx-demo": lambda publish: OKXEngine(
                    okx_stores["okx-demo"], okx_clients["okx-demo"], jev, publish
                ),
            },
            default=os.environ.get("KAIROS_EXCHANGE", "kraken"),
        )
        app["exchanges"], app["session"] = desk, session
        app["feed_restart"] = asyncio.Event()
        feed_task = None
        try:
            await desk.initialize()
            feed_task = asyncio.create_task(feeds(app))
            yield
        except Exception as exc:
            diagnostics.capture(exc, "application-lifecycle")
            raise
        finally:
            if feed_task:
                feed_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await feed_task
            await desk.close()
            await alerts.close()
            await kraken.market_data.close()
            alpaca_store.close()
            for okx_store in okx_stores.values():
                okx_store.close()
            store.close()
            lock.close()
            diagnostics.logger.removeHandler(diagnostic_handler)
            diagnostic_handler.close()


async def index(request):
    return web.FileResponse(STATIC / "index.html")


def create_app(engine=None, origin=None, futures=None):
    app = web.Application(middlewares=[security], client_max_size=16 * 1024)
    app["hub"] = Hub()
    app["candle_cache"] = {}
    app["candle_lock"] = asyncio.Lock()
    app["market_cache"] = {}
    app["market_change_cache"] = {}
    app["spot_quote_cache"] = {}
    app["account_cache"] = {}
    app["account_lock"] = asyncio.Lock()
    app["market_lock"] = asyncio.Lock()
    app["csrf"] = secrets.token_urlsafe(32)
    diagnostics.register_secrets(app["csrf"])
    app["origin"] = (origin or os.environ.get("PUBLIC_ORIGIN", "http://127.0.0.1:8000")).rstrip("/")
    app.on_response_prepare.append(response_headers)
    app.on_shutdown.append(shutdown)
    if engine is None:
        app.cleanup_ctx.append(lifecycle)
    else:
        app["engine"] = engine
        app["retail"] = (
            OKXMarkets(engine)
            if isinstance(engine, OKXEngine)
            else AlpacaMarkets(engine.kraken)
            if isinstance(engine, AlpacaEngine)
            else RetailMarkets(engine.kraken, futures)
        )
        app["feed_restart"] = asyncio.Event()
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/settings-schema", settings_schema)
    app.router.add_get("/api/pairs", catalog)
    app.router.add_get("/api/markets", markets)
    app.router.add_get("/api/history", history)
    app.router.add_get("/api/reconcile", manual_exit_preview)
    app.router.add_get("/api/accounts/{source}", accounts)
    app.router.add_get("/api/candles", candles)
    app.router.add_get("/api/events", events)
    app.router.add_post("/api/{action}", command)
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC, show_index=False)
    return app


def main():
    # Runtime consumption only; secrets are never returned by an endpoint or logged.
    load_dotenv(override=False)
    os.umask(0o077)
    web.run_app(
        create_app(),
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        access_log=None,
        print=lambda _: print("Kairos listening on loopback; use nginx for authenticated access"),
    )


if __name__ == "__main__":
    main()
