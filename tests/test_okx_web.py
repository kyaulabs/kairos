import copy
import json
import unittest

from aiohttp.test_utils import TestClient, TestServer

from kairos import settings
from kairos.okx_markets import OKXMarkets
from kairos.web import create_app
from tests import test_okx_cycle as fixtures


class OKXWebTests(unittest.IsolatedAsyncioTestCase):
    book = fixtures.CycleTests.book

    async def asyncSetUp(self):
        await fixtures.CycleTests.asyncSetUp(self)
        self.engine.ready = True
        self.app = create_app(self.engine, origin="https://kairos.example")
        self.http = TestClient(TestServer(self.app))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)
        self.headers = {"Origin": "https://kairos.example", "X-CSRF-Token": self.app["csrf"]}

    async def test_native_catalog_schema_accounts_and_no_foreign_price_fallback(self):
        original = copy.deepcopy(settings.STRATEGIES)
        self.assertIsInstance(self.app["retail"], OKXMarkets)
        schema = await (await self.http.get("/api/settings-schema")).json()
        self.assertEqual(settings.STRATEGIES, original)
        self.assertEqual(schema["exchange"], "okx-demo")
        self.assertEqual(list(schema["fields"]["product"]["choices"]), ["spot"])
        self.assertEqual(schema["strategies"]["htf"]["products"], ["spot"])
        self.assertEqual(
            settings.FIELDS["htf_policy"]["choices"]["multibar-v2"],
            "Multi-bar pullback · 14-day paper trial",
        )
        catalog = await (await self.http.get("/api/pairs")).json()
        self.assertIn(self.pair.id, [row["id"] for row in catalog])
        markets = await (await self.http.get("/api/markets?kind=all")).json()
        self.assertTrue(all(row["id"].startswith("okx-demo:") for row in markets["markets"]))
        self.assertTrue(all(row["volume"] is None for row in markets["markets"]))
        response = await self.http.get(
            "/api/candles", params={"pair": self.pair.id, "interval": "60"}
        )
        self.assertEqual(response.status, 200)
        chart = await response.json()
        self.assertEqual(chart["volume_unit"], "base asset")
        self.assertIn("OKX U.S. demo BTC-USD", chart["source"])
        self.assertEqual(len(chart["candles"]), 40)
        self.assertEqual(chart["candles"][0]["volume"], "2")
        self.assertFalse(chart["candles"][-1]["complete"])
        selected = dict(self.client.market_data.pairs)
        generation = self.client.market_data.generation
        response = await self.http.get(
            "/api/candles", params={"pair": "okx-demo:BTC-USD:USDG", "interval": "1440"}
        )
        self.assertEqual(response.status, 200)
        self.assertIn("bar=1Dutc", self.venue.calls[-1][1])
        self.assertEqual(self.client.market_data.pairs, selected)
        self.assertEqual(self.client.market_data.generation, generation)
        self.assertEqual(self.engine.settings["pair"], self.pair.id)
        self.assertFalse([call for call in self.venue.calls if call[0] == "POST"])

    async def test_command_flow_requires_origin_csrf_exact_preview_and_explicit_confirmation(self):
        body = {
            "kind": "execution_cycle",
            "pair": self.pair.id,
            "allocation": "500",
            "budget": "25",
        }
        response = await self.http.post("/api/okx-preview", json=body)
        self.assertEqual(response.status, 403)
        response = await self.http.post("/api/okx-preview", headers=self.headers, json=body)
        self.assertEqual(response.status, 200, await response.text())
        preview = (await response.json())["preview"]
        self.assertFalse([call for call in self.venue.calls if call[0] == "POST"])
        response = await self.http.post(
            "/api/okx-authorize",
            headers=self.headers,
            json={"preview_id": preview["id"], "confirmation": "START ALPACA PAPER"},
        )
        self.assertEqual(response.status, 409)
        response = await self.http.post(
            "/api/okx-authorize",
            headers=self.headers,
            json={"preview_id": preview["id"], "confirmation": preview["confirmation"]},
        )
        self.assertEqual(response.status, 200, await response.text())
        if self.engine.operation_task:
            await self.engine.operation_task
        state = await (await self.http.get("/api/state")).json()
        self.assertEqual(state["execution_cycle"]["status"], "PASSED_WITH_DUST")
        self.assertFalse(state["running"])
        self.assertFalse(state["armed"])
        self.assertEqual(len(self.store.orders()), 2)
        serialized = json.dumps(state)
        for secret in (self.client.key, self.client.secret, self.client.passphrase):
            self.assertNotIn(secret, serialized)
        self.assertNotIn('"uid":', serialized)
        self.assertEqual(state["account_identity"], "••••3456")
        for path, body in [
            ("start", {}),
            ("reset-paper", {}),
            ("mode", {"mode": "trading", "confirmation": "ENABLE LIVE TRADING"}),
        ]:
            response = await self.http.post("/api/" + path, headers=self.headers, json=body)
            self.assertEqual(response.status, 409)
        self.assertEqual(len([call for call in self.venue.calls if call[0] == "POST"]), 2)
