import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kairos import asset_names, frontend
from kairos.preferences import DEFAULTS, Conflict, Preferences, validate
from kairos.restart import Control


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Preferences(self.directory.name)

    def test_atomic_persistence_separate_fields_and_stale_drafts(self):
        before = self.store.read()
        self.assertEqual(before["values"], DEFAULTS)
        self.store.save("candle_interval", 15, 0)
        self.store.save("appearance", {"theme": "light", "accent": "blue"}, 0)
        with self.assertRaises(Conflict):
            self.store.save("candle_interval", 1, 0)
        again = Preferences(self.directory.name).read()
        self.assertEqual(again["values"]["candle_interval"], 15)
        self.assertEqual(again["values"]["appearance"]["accent"], "blue")
        self.assertEqual(again["revisions"]["candle_interval"], 1)
        self.assertEqual(os.stat(self.store.path).st_mode & 0o777, 0o600)

    def test_indicators_favorites_and_chart_selection_keep_native_identity(self):
        values = copy.deepcopy(DEFAULTS["indicators"])
        values.update(macd=True, collapsed=True)
        values["hidden"] = {"macd": True}
        values["settings"] = {
            "macd": {
                "fast": 10,
                "slow": 24,
                "signal": 8,
                "width": 1.5,
                "style": "dotted",
                "line": "info",
            }
        }
        self.store.save("indicators", values, 0)
        for exchange in ("kraken", "alpaca", "okx", "okx-demo"):
            self.store.save(
                f"favorites:{exchange}", [f"{exchange}:ETH-USD:USDC", f"{exchange}:BTC-USD:USD"], 0
            )
            self.store.save(f"chart_market:{exchange}", f"{exchange}:BTC-USD:USDC", 0)
        again = self.store.read()["values"]
        self.assertEqual(again["indicators"], values)
        self.assertNotEqual(again["favorites:okx"], again["favorites:okx-demo"])
        self.assertEqual(again["chart_market:okx-demo"], "okx-demo:BTC-USD:USDC")

    def test_invalid_or_execution_preferences_never_mutate_the_store(self):
        bad = [
            ("authorization", {}),
            ("running", True),
            ("settings", {"order_size": "1000"}),
            ("candle_interval", True),
            ("candle_interval", 2),
            ("appearance", {"theme": "light", "accent": "url(evil)"}),
            ("appearance", {"theme": "light", "accent": "blue", "extra": True}),
            ("favorites:kraken", ["BTC", "BTC"]),
            ("favorites:alpaca", [None]),
            ("favorites:okx", ["a" * 129]),
            ("favorites:okx", [str(i) for i in range(101)]),
            ("chart_market:okx", "../BTC?arbitrary=1"),
            ("indicators", {}),
        ]
        for fields in (
            {"period": 0},
            {"period": 1.5},
            {"period": float("nan")},
            {"line": "url(evil)"},
            {"width": True},
            {"extra": 1},
        ):
            value = copy.deepcopy(DEFAULTS["indicators"])
            value["settings"] = {"rsi": fields}
            bad.append(("indicators", value))
        value = copy.deepcopy(DEFAULTS["indicators"])
        value["settings"] = {"macd": {"fast": 26, "slow": 12}}
        bad.append(("indicators", value))
        before = self.store.read()
        for key, value in bad:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.store.save(key, value, 0)
        self.assertEqual(self.store.read(), before)
        for revision in (True, -1, "0", 0.5):
            with self.assertRaises(ValueError):
                self.store.save("candle_interval", 15, revision)
        for key, value in DEFAULTS.items():
            validate(key, value)

    def test_public_files_and_symlinks_are_not_adopted(self):
        self.store.path.chmod(0o644)
        with self.assertRaises(PermissionError):
            Preferences(self.directory.name)
        self.store.path.unlink()
        target = Path(self.directory.name) / "unrelated"
        target.write_text("preserve")
        self.store.path.symlink_to(target)
        with self.assertRaises(OSError):
            Preferences(self.directory.name)
        self.assertEqual(target.read_text(), "preserve")


class PreferenceGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.control = Control(self.directory.name)
        self.marker = self.control.read()
        self.headers = {"Origin": "https://kairos.test", "X-CSRF-Token": self.marker["csrf"]}
        self.client = await self.gateway()

    async def gateway(self):
        client = TestClient(
            TestServer(frontend.create_app(self.directory.name, "https://kairos.test"))
        )
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return client

    async def test_second_device_and_gateway_restart_retain_preferences_without_worker(self):
        financial = [
            Path(self.directory.name) / f"{name}.sqlite3"
            for name in ("kairos", "alpaca-paper", "okx-demo", "okx-live")
        ]
        for path in financial:
            path.write_bytes(b"untouched financial store")
        result = await self.client.post(
            "/api/preferences",
            json={"key": "candle_interval", "value": 15, "revision": 0},
            headers=self.headers,
        )
        self.assertEqual(result.status, 200)
        other = await self.gateway()
        response = await other.get("/api/preferences")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual((await response.json())["values"]["candle_interval"], 15)
        conflict = await other.post(
            "/api/preferences",
            json={"key": "candle_interval", "value": 1, "revision": 0},
            headers=self.headers,
        )
        self.assertEqual(conflict.status, 409)
        await self.client.close()
        again = await self.gateway()
        self.assertEqual(
            (await (await again.get("/api/preferences")).json())["values"]["candle_interval"], 15
        )
        self.assertEqual(self.control.read(), self.marker)
        self.assertTrue(
            all(path.read_bytes() == b"untouched financial store" for path in financial)
        )

    async def test_same_origin_csrf_types_body_limits_and_trading_key_rejection(self):
        body = {"key": "candle_interval", "value": 15, "revision": 0}
        for headers in (
            {},
            {**self.headers, "Origin": "https://evil.test"},
            {**self.headers, "X-CSRF-Token": "bad"},
        ):
            self.assertEqual(
                (await self.client.post("/api/preferences", json=body, headers=headers)).status, 403
            )
        self.assertEqual(
            (
                await self.client.post(
                    "/api/preferences", data=json.dumps(body), headers=self.headers
                )
            ).status,
            403,
        )
        for body in (
            [],
            {"key": "running", "value": True, "revision": 0},
            {"key": "candle_interval", "value": False, "revision": 0},
        ):
            self.assertEqual(
                (
                    await self.client.post("/api/preferences", json=body, headers=self.headers)
                ).status,
                400,
            )
        self.assertEqual(
            (
                await self.client.post(
                    "/api/preferences", json={"big": "x" * 20000}, headers=self.headers
                )
            ).status,
            413,
        )
        self.assertEqual(self.control.read(), self.marker)

    async def test_broken_preference_storage_never_disables_durable_stop(self):
        await self.client.close()
        path = Path(self.directory.name) / "dashboard.sqlite3"
        path.write_bytes(b"corrupt database; do not silently reset")
        again = await self.gateway()
        self.assertEqual((await again.get("/api/preferences")).status, 503)
        self.assertTrue((await (await again.get("/api/control")).json())["stop_available"])
        self.assertEqual(self.control.read(), self.marker)
        response = await again.post("/api/stop", json={}, headers=self.headers)
        self.assertEqual(response.status, 202)
        self.assertTrue((await response.json())["stop_recorded"])
        self.assertEqual(path.read_bytes(), b"corrupt database; do not silently reset")


class AssetNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_ids_aliases_and_ambiguous_symbols(self):
        rows = [
            {"id": "another-bitcoin", "symbol": "btc", "name": "Wrong Bitcoin"},
            {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin"},
            {"id": "dogecoin", "symbol": "doge", "name": "Dogecoin"},
            {"id": "one", "symbol": "dup", "name": "One"},
            {"id": "two", "symbol": "dup", "name": "Two"},
            {"id": "example", "symbol": "xyz", "name": "Example asset"},
        ]
        self.assertEqual(
            asset_names.names_from(rows),
            {
                "BTC": "Bitcoin",
                "XBT": "Bitcoin",
                "DOGE": "Dogecoin",
                "XDG": "Dogecoin",
                "XYZ": "Example asset",
            },
        )
        self.assertNotIn("BTC", asset_names.names_from(rows[:1]))
        for invalid in ({}, [], [{"symbol": "btc", "name": 123}], [None]):
            with self.assertRaises(ValueError):
                asset_names.names_from(invalid)

    async def test_server_cache_restart_concurrent_reads_and_provider_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Preferences(directory)
            now, calls, failing = [200000], [], [False]

            async def provider(request):
                calls.append(dict(request.headers))
                if failing[0]:
                    return web.Response(status=429)
                return web.json_response([{"id": "bitcoin", "symbol": "btc", "name": "Bitcoin"}])

            app = web.Application()
            app.router.add_get("/coins", provider)
            server = TestServer(app)
            await server.start_server()
            self.addAsyncCleanup(server.close)
            with patch.object(asset_names, "SOURCE", str(server.make_url("/coins"))):
                catalog = asset_names.AssetNames(store, clock=lambda: now[0])
                results = await asyncio.gather(*(catalog.snapshot() for _ in range(3)))
                self.assertEqual(len(calls), 1)
                self.assertTrue(all(item["names"]["BTC"] == "Bitcoin" for item in results))
                restarted = asset_names.AssetNames(Preferences(directory), clock=lambda: now[0])
                self.assertEqual((await restarted.snapshot())["fetched_at"], 200000)
                self.assertEqual(len(calls), 1)
                failing[0] = True
                now[0] += 86401
                for _ in range(3):
                    stale = await restarted.snapshot()
                    self.assertEqual(stale["names"]["BTC"], "Bitcoin")
                    self.assertEqual(stale["fetched_at"], 200000)
                self.assertEqual(len(calls), 2)
                self.assertTrue(
                    all(
                        "Authorization" not in request and "X-CSRF-Token" not in request
                        for request in calls
                    )
                )
                self.assertEqual(store.read()["values"], DEFAULTS)
