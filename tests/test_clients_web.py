import base64
import hashlib
import hmac
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from kairos.clients import Jev, Kraken, signature
from kairos.domain import SafetyError
from kairos.engine import Engine
from kairos.store import Store
from kairos.web import create_app
from tests.helpers import BTC, fake_jev, fake_kraken


class Response:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.body


class Session:
    def __init__(self, body, status=200):
        self.body, self.status, self.calls = body, status, []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return Response(self.body, self.status)

    def post(self, *args, **kwargs):
        return self.request(*args, **kwargs)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_signature_encodes_expiry_and_nonce_correctly(self):
        secret = base64.b64encode(b"unit-test-only").decode()
        payload = {"nonce": 123, "expiretm": "+30"}
        expected = base64.b64encode(
            hmac.new(
                b"unit-test-only",
                b"/0/private/AddOrder" + hashlib.sha256(b"123nonce=123&expiretm=%2B30").digest(),
                hashlib.sha512,
            ).digest()
        ).decode()
        self.assertEqual(signature("/0/private/AddOrder", payload, secret), expected)

    async def test_nonced_requests_are_monotonic_even_if_clock_recedes(self):
        store = Store(":memory:")
        session = Session({"error": [], "result": {}})
        client = Kraken(session, store, "test-key", base64.b64encode(b"test-only").decode())
        store.put("nonce", 10**20)
        with patch("kairos.clients.asyncio.sleep", new=AsyncMock()):
            await client.request("BalanceEx", private=True)
            await client.request("BalanceEx", private=True)
        nonces = [entry[1]["data"]["nonce"] for entry in session.calls]
        self.assertEqual(nonces, [10**20 + 1, 10**20 + 2])
        store.close()

    async def test_central_write_gate_blocks_all_writes_with_readonly_client(self):
        store = Store(":memory:")
        client = Kraken(Session({}), store)
        for method in ("AddOrder", "CancelOrder", "Withdraw", "CancelAll"):
            with self.subTest(method=method), self.assertRaises(SafetyError):
                await client.request(method, private=True)
        self.assertEqual(client.session.calls, [])
        store.close()

    async def test_candle_reader_removes_unfinished_candle(self):
        store = Store(":memory:")
        client = Kraken(
            Session({"error": [], "result": {BTC.id: [[1], [2], [3]], "last": 2}}), store
        )
        self.assertEqual(await client.candles(BTC, 15), [[1], [2]])
        store.close()

    async def test_jev_response_validation(self):
        good = {
            "model": "test",
            "answers": {
                "action": {
                    "type": "choice",
                    "choice": "buy",
                    "confidence": 0.8,
                    "probabilities": {"buy": 0.9, "sell": 0.05, "hold": 0.05},
                }
            },
            "usage": {},
        }
        result = await Jev(Session(good), "test-placeholder").decide({})
        self.assertEqual(result["action"], "buy")
        for broken in (
            {},
            {"answers": {"action": {"choice": "withdraw"}}},
            {
                **good,
                "answers": {"action": {**good["answers"]["action"], "confidence": float("nan")}},
            },
        ):
            with self.subTest(broken=broken), self.assertRaises(SafetyError):
                await Jev(Session(broken), "test-placeholder").decide({})

    async def test_jev_rate_limit_abstains_without_retry(self):
        session = Session({"secret": "must-not-leak"}, 429)
        with self.assertRaisesRegex(SafetyError, "Jev HTTP 429"):
            await Jev(session, "test-placeholder").decide({})
        self.assertEqual(len(session.calls), 1)


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.engine = Engine(self.store, fake_kraken(), fake_jev(), lambda *_: None)
        await self.engine.initialize()
        self.app = create_app(self.engine, "https://kairos.example.test")
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.store.close()

    def headers(self):
        return {"Origin": "https://kairos.example.test", "X-CSRF-Token": self.app["csrf"]}

    async def test_page_assets_and_security_headers(self):
        for path in ("/", "/static/app.js", "/static/chart.js", "/static/vendor/d3.v7.9.0.min.js"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200)
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            await response.read()

    async def test_no_dotenv_route(self):
        for path in ("/.env", "/static/.env", "/static/../.env"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 404)

    async def test_state_does_not_expose_credentials(self):
        response = await self.client.get("/api/state")
        text = await response.text()
        self.assertNotIn("test-placeholder", text)
        self.assertNotIn("JEV_API_KEY", text)
        self.assertIn('"csrf"', text)
        self.assertIn('"us_stocks"', text)

    async def test_csrf_and_origin_required(self):
        for headers in (
            {},
            {"Origin": "https://evil.example", "X-CSRF-Token": self.app["csrf"]},
            {"Origin": "https://kairos.example.test", "X-CSRF-Token": "wrong"},
        ):
            response = await self.client.post("/api/start", json={}, headers=headers)
            self.assertEqual(response.status, 403)
            self.assertFalse(self.engine.running)

    async def test_start_stop_api(self):
        response = await self.client.post("/api/start", json={}, headers=self.headers())
        self.assertEqual(response.status, 200)
        self.assertTrue(self.engine.running)
        response = await self.client.post("/api/stop", json={}, headers=self.headers())
        self.assertEqual(response.status, 200)
        self.assertFalse(self.engine.running)

    async def test_live_api_cannot_bypass_server_gate(self):
        response = await self.client.post(
            "/api/mode",
            json={"mode": "trading", "confirmation": "ENABLE LIVE TRADING"},
            headers=self.headers(),
        )
        self.assertEqual(response.status, 409)
        self.engine.kraken.add.assert_not_awaited()

    async def test_sse_snapshot_and_buffering_header(self):
        response = await self.client.get("/api/events")
        self.assertEqual(response.headers["X-Accel-Buffering"], "no")
        self.assertEqual(await response.content.readline(), b"event: state\n")
        self.assertIn(b'"mode":"dry-run"', await response.content.readline())
        response.close()
