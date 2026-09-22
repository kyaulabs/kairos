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

from kairos.clients import Jev, Kraken, ticker_feed
from kairos.domain import CANDLE_INTERVALS, SafetyError
from kairos.engine import Engine
from kairos.store import Store, encode

STATIC = Path(__file__).parent / "static"


class Hub:
    def __init__(self):
        self.listeners = set()
        self.tickers = {}

    def publish(self, kind, data):
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
        return web.json_response({"error": str(exc)}, status=409)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return web.json_response({"error": "Invalid request"}, status=400)
    except web.HTTPException:
        raise
    except Exception:
        # Do not expose credential-bearing exception details or remote response bodies.
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


async def catalog(request):
    engine = request.app["engine"]
    return web.json_response(
        [p.public() for p in engine.kraken.pairs.values() if p.quote == engine.settings["quote"]]
    )


async def markets(request):
    # Share a bounded-rate public ticker snapshot across all dashboard tabs.
    async with request.app["market_lock"]:
        cached = request.app["market_cache"]
        if not cached or time.monotonic() >= cached["expires"]:
            engine = request.app["engine"]
            tickers = await engine.kraken.market_tickers()
            data = {
                "received": time.time(),
                "markets": [
                    {"id": p.id, "symbol": p.symbol, **tickers.get(p.id, {})}
                    for p in engine.kraken.pairs.values()
                ],
            }
            cached.update(expires=time.monotonic() + 10, data=data)
        return web.json_response(cached["data"])


async def candles(request):
    minutes = int(request.query["interval"])
    if minutes not in CANDLE_INTERVALS:
        raise SafetyError("Unsupported candle interval")
    # Share short-lived public snapshots across tabs without caching strategy inputs.
    async with request.app["candle_lock"]:
        engine = request.app["engine"]
        pair = engine.kraken.pairs.get(request.query["pair"])
        if pair is None:
            raise SafetyError("Unsupported chart market")
        cache = request.app["candle_cache"]
        for key in list(cache):
            if time.monotonic() >= cache[key][0]:
                del cache[key]
        key = (pair.id, minutes)
        if key not in cache or time.monotonic() >= cache[key][0]:
            rows = await engine.kraken.ohlc(pair, minutes)
            data = {
                "pair": pair.id,
                "interval": minutes,
                "received": time.time(),
                "candles": [
                    {
                        "time": r[0],
                        "open": r[1],
                        "high": r[2],
                        "low": r[3],
                        "close": r[4],
                        "volume": r[6],
                    }
                    for r in rows[-720:]
                ],
            }
            if len(cache) >= 32:
                del cache[next(iter(cache))]
            cache[key] = (time.monotonic() + 5, data)
        return web.json_response(cache[key][1])


async def history(request):
    return web.json_response(request.app["engine"].store.history())


async def command(request):
    engine = request.app["engine"]
    data = await request.json()
    action = request.match_info["action"]
    if action == "settings":
        await engine.configure(data)
        request.app["feed_restart"].set()
    elif action == "mode":
        await engine.set_mode(data["mode"], data.get("confirmation"))
    elif action == "start":
        await engine.start()
    elif action == "stop":
        await engine.stop()
    elif action == "reconcile":
        await engine.reconcile(data.get("acknowledge") is True)
    elif action == "reset-paper":
        await engine.reset_paper()
    else:
        raise web.HTTPNotFound()
    return web.json_response(engine.snapshot())


async def events(request):
    hub = request.app["hub"]
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
        symbols = list(
            dict.fromkeys([engine.resolve(engine.settings["pair"]).symbol, "BTC/USD", "ETH/USD"])
        )
        task = asyncio.create_task(ticker_feed(app["session"], symbols, app["hub"].publish))
        try:
            await app["feed_restart"].wait()
            app["feed_restart"].clear()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def lifecycle(app):
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)  # noqa: ASYNC240 -- startup, before serving
    lock = (data_dir / "engine.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("Another Kairos process owns this data directory") from None
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
            session, os.environ.get("JEV_API_KEY", ""), os.environ.get("JEV_MODEL", "jev-latest")
        )
        engine = Engine(store, kraken, jev, app["hub"].publish)
        app["engine"], app["session"] = engine, session
        app["feed_restart"] = asyncio.Event()
        feed_task = None
        try:
            await engine.initialize()
            engine.task = asyncio.create_task(engine.run())
            feed_task = asyncio.create_task(feeds(app))
            yield
        finally:
            if feed_task:
                feed_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await feed_task
            await engine.close()
            store.close()
            lock.close()


async def index(request):
    return web.FileResponse(STATIC / "index.html")


def create_app(engine=None, origin=None):
    app = web.Application(middlewares=[security], client_max_size=16 * 1024)
    app["hub"] = Hub()
    app["candle_cache"] = {}
    app["candle_lock"] = asyncio.Lock()
    app["market_cache"] = {}
    app["market_lock"] = asyncio.Lock()
    app["csrf"] = secrets.token_urlsafe(32)
    app["origin"] = (origin or os.environ.get("PUBLIC_ORIGIN", "http://127.0.0.1:8000")).rstrip("/")
    app.on_response_prepare.append(response_headers)
    if engine is None:
        app.cleanup_ctx.append(lifecycle)
    else:
        app["engine"] = engine
        app["feed_restart"] = asyncio.Event()
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/pairs", catalog)
    app.router.add_get("/api/markets", markets)
    app.router.add_get("/api/history", history)
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
