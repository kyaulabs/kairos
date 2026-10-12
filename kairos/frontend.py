"""Restartable dashboard gateway. Trading and financial stores belong to the worker."""

import hmac
import json
import os
from importlib.metadata import version
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

from kairos import diagnostics
from kairos.restart import Control

STATIC = Path(__file__).parent / "static"


async def headers(request, response):
    # Keep the same authenticated-dashboard browser restrictions as the worker.
    from kairos.web import response_headers

    await response_headers(request, response)


async def control_state(request):
    return web.json_response(
        {
            "csrf": request.app["control"].read()["csrf"],
            "version": request.app["version"],
            "separate_worker": True,
            "stop_available": True,
        }
    )


async def proxy(request):
    app = request.app
    stopping = request.method == "POST" and request.path == "/api/stop"
    body = await request.read()
    if request.method not in {"GET", "HEAD"}:
        if (
            request.headers.get("Origin") != app["origin"]
            or request.content_type != "application/json"
            or not hmac.compare_digest(
                request.headers.get("X-CSRF-Token", ""), app["control"].read()["csrf"]
            )
        ):
            raise web.HTTPForbidden(text="Origin, content type, or CSRF token rejected")
        if stopping:
            try:
                if not isinstance(json.loads(body), dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                raise web.HTTPBadRequest(text="Stop requires a JSON object") from None
            # Stop is durable even when the worker is absent or still reconciling.
            app["control"].revoke()
    forwarded = {
        key: request.headers[key]
        for key in ("Origin", "X-CSRF-Token", "Content-Type", "Accept", "Last-Event-ID")
        if key in request.headers
    }
    try:
        async with app["session"].request(
            request.method,
            "http://worker" + request.raw_path,
            data=body or None,
            headers=forwarded,
            allow_redirects=False,
        ) as upstream:
            response_headers = {
                key: value
                for key, value in upstream.headers.items()
                if key.lower() in {"content-type", "cache-control", "x-accel-buffering"}
            }
            if request.path == "/api/state" and upstream.status == 200:
                data = await upstream.json()
                data.update(
                    worker_version=data["version"], version=app["version"], separate_worker=True
                )
                return web.json_response(data)
            if stopping and upstream.status >= 400:
                return web.json_response(
                    {
                        "stop_recorded": True,
                        "message": "Stop recorded durably; worker reconciliation is pending.",
                    },
                    status=202,
                )
            if request.path != "/api/events":
                return web.Response(
                    body=await upstream.read(), status=upstream.status, headers=response_headers
                )
            response = web.StreamResponse(status=upstream.status, headers=response_headers)
            await response.prepare(request)
            app["streams"].add(upstream)
            try:
                async for chunk in upstream.content.iter_any():
                    await response.write(chunk)
            except (ConnectionResetError, aiohttp.ClientError):
                pass
            finally:
                app["streams"].discard(upstream)
            return response
    except (aiohttp.ClientError, TimeoutError):
        if stopping:
            return web.json_response(
                {
                    "stop_recorded": True,
                    "message": "Stop recorded durably; worker reconciliation is pending.",
                },
                status=202,
            )
        return web.json_response(
            {
                "error": "Trading worker unavailable or reconciling; no request is replayed. Stop remains available."
            },
            status=503,
        )


async def lifecycle(app):
    connector = aiohttp.UnixConnector(path=str(app["socket"]))
    async with aiohttp.ClientSession(
        connector=connector, timeout=aiohttp.ClientTimeout(total=None, sock_connect=3, sock_read=90)
    ) as session:
        app["session"] = session
        yield


async def shutdown(app):
    # Disconnect SSE promptly. Never signal, stop or revoke the independent worker.
    for stream in tuple(app["streams"]):
        stream.close()


async def index(request):
    return web.FileResponse(STATIC / "index.html")


def create_app(directory=None, origin=None):
    directory = Path(directory or os.environ.get("DATA_DIR", "data"))
    app = web.Application(client_max_size=16 * 1024)
    app["control"] = Control(directory)
    diagnostics.register_secrets(app["control"].read()["csrf"])
    app["version"] = version("kairos-trader")
    app["origin"] = (origin or os.environ.get("PUBLIC_ORIGIN", "http://127.0.0.1:8000")).rstrip("/")
    app["socket"] = directory / "engine.sock"
    app["streams"] = set()
    app.cleanup_ctx.append(lifecycle)
    app.on_shutdown.append(shutdown)
    app.on_response_prepare.append(headers)
    app.router.add_get("/api/control", control_state)
    app.router.add_route("*", "/api/{path:.*}", proxy)
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC, show_index=False)
    return app


def main():
    load_dotenv(override=False)
    os.umask(0o077)
    web.run_app(
        create_app(),
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        access_log=None,
        print=lambda _: print(
            "Kairos dashboard gateway on loopback; trading worker is independent"
        ),
    )
