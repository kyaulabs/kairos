import asyncio
import base64
import hashlib
import hmac
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from kairos.clients import Jev, Kraken, signature
from kairos.domain import SafetyError
from kairos.engine import Engine
from kairos.store import Store
from kairos.web import create_app
from tests.helpers import BTC, CROSS, ETH, candle_rows, fake_jev, fake_kraken


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

    async def test_chart_ohlc_includes_forming_candle_using_public_request(self):
        store = Store(":memory:")
        rows = candle_rows()
        session = Session({"error": [], "result": {BTC.id: rows, "last": rows[-2][0]}})
        client = Kraken(session, store)
        self.assertEqual(await client.ohlc(BTC, 1), rows)
        args, kwargs = session.calls[0]
        self.assertEqual(args, ("GET", "https://api.kraken.com/0/public/OHLC"))
        self.assertEqual(kwargs["params"], {"pair": BTC.id, "interval": 1})
        self.assertEqual(kwargs["headers"], {})
        store.close()

    async def test_market_tickers_are_public_and_use_24_hour_volume(self):
        store = Store(":memory:")
        row = {"a": ["101"], "b": ["99"], "c": ["100"], "v": ["2", "12.5"]}
        session = Session({"error": [], "result": {BTC.id: row, "UNKNOWN": row}})
        client = Kraken(session, store)
        client.pairs = {BTC.id: BTC}
        self.assertEqual(
            await client.market_tickers(),
            {BTC.id: {"bid": "99", "ask": "101", "last": "100", "volume": "12.5"}},
        )
        args, kwargs = session.calls[0]
        self.assertEqual(args, ("GET", "https://api.kraken.com/0/public/Ticker"))
        self.assertEqual(kwargs["params"], {})
        self.assertEqual(kwargs["headers"], {})
        store.close()

    async def test_market_change_snapshots_use_rolling_ws_values_and_dogecoin_alias(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        session = Mock()
        socket = AsyncMock()
        socket.__aenter__.return_value = socket
        session.ws_connect.return_value = socket
        doge = replace(BTC, id="XDGUSD", symbol="XDG/USD")
        client = Kraken(session, store)
        client.pairs = {p.id: p for p in (BTC, ETH, CROSS, doge)}
        messages = [
            {
                "channel": "ticker",
                "type": "update",
                "data": [{"symbol": BTC.symbol, "change_pct": 99}],
            },
            {
                "channel": "ticker",
                "type": "snapshot",
                "data": [
                    {"symbol": BTC.symbol, "change_pct": 2.5},
                    {"symbol": "DOGE/USD", "change_pct": 0},
                    {"symbol": CROSS.symbol, "change_pct": "NaN"},
                ],
            },
            {"success": False, "symbol": ETH.symbol},
        ]
        socket.receive.side_effect = [
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(m)) for m in messages
        ]
        self.assertEqual(await client.market_changes(), {BTC.id: "2.5", doge.id: "0"})
        session.ws_connect.assert_called_once_with("wss://ws.kraken.com/v2", heartbeat=20)
        subscription = socket.send_json.call_args.args[0]
        self.assertIn("DOGE/USD", subscription["params"]["symbol"])
        self.assertTrue(subscription["params"]["snapshot"])
        session.request.assert_not_called()

    async def test_market_change_subscriptions_are_batched_and_keep_valid_partial_results(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        session = Mock()
        socket = AsyncMock()
        socket.__aenter__.return_value = socket
        session.ws_connect.return_value = socket
        client = Kraken(session, store)
        pairs = [replace(BTC, id=f"P{i}", symbol=f"COIN{i}/USD") for i in range(101)]
        client.pairs = {p.id: p for p in pairs}
        snapshot = {
            "channel": "ticker",
            "type": "snapshot",
            "data": [{"symbol": p.symbol, "change_pct": -1.5} for p in pairs[:100]],
        }
        socket.receive.side_effect = [
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(snapshot)),
            TimeoutError(),
        ]
        with patch("kairos.clients.asyncio.sleep", new=AsyncMock()):
            result = await client.market_changes()
        self.assertEqual(result, {p.id: "-1.5" for p in pairs[:100]})
        self.assertEqual(
            [len(c.args[0]["params"]["symbol"]) for c in socket.send_json.call_args_list], [100, 1]
        )
        socket.__aexit__.assert_awaited_once()

    async def test_change_feed_failure_is_unavailable_and_cancellation_propagates(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        session = Mock()
        client = Kraken(session, store)
        client.pairs = {BTC.id: BTC}
        for error in (aiohttp.ClientError("do not expose"), asyncio.CancelledError()):
            session.ws_connect.side_effect = error
            if isinstance(error, asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await client.market_changes()
            else:
                self.assertEqual(await client.market_changes(), {})

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
        for path in (
            "/",
            "/static/app.js",
            "/static/chart.js",
            "/static/markets.js",
            "/static/strategy-market.js",
            "/static/vendor/d3.v7.9.0.min.js",
            "/static/vendor/crypto-icons/symbols.js",
            "/static/vendor/crypto-icons/btc.svg",
        ):
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

    async def test_program_api_requires_csrf_confirmation_and_preserves_holdings(self):
        await self.engine.configure(
            {**self.engine.settings, "strategy": "dca", "dca_count": 1, "dca_amount": "25"}
        )
        response = await self.client.post("/api/start", json={}, headers=self.headers())
        self.assertEqual(response.status, 200)
        await self.engine.tick()
        before = self.engine.ledger()
        response = await self.client.post("/api/start", json={}, headers=self.headers())
        self.assertEqual(response.status, 409)
        self.assertEqual(
            (
                await self.client.post(
                    "/api/reset-program", json={"confirmation": "NEW STRATEGY RUN"}
                )
            ).status,
            403,
        )
        self.assertEqual(
            (await self.client.post("/api/reset-program", json={}, headers=self.headers())).status,
            409,
        )
        response = await self.client.post(
            "/api/reset-program", json={"confirmation": "NEW STRATEGY RUN"}, headers=self.headers()
        )
        self.assertEqual(response.status, 200)
        self.assertIsNone((await response.json())["program"])
        self.assertEqual(self.engine.ledger(), before)
        self.engine.kraken.add.assert_not_awaited()
        self.engine.jev.decide.assert_not_awaited()

    async def test_chart_candles_include_forming_bar_and_share_snapshot_across_tabs(self):
        path = f"/api/candles?pair={BTC.id}&interval=1"
        responses = await asyncio.gather(self.client.get(path), self.client.get(path))
        data = [await response.json() for response in responses]
        self.assertTrue(all(response.status == 200 for response in responses))
        self.assertEqual(data[0], data[1])
        self.assertEqual(data[0]["pair"], BTC.id)
        self.assertEqual(data[0]["interval"], 1)
        self.assertEqual(len(data[0]["candles"]), 120)
        self.assertEqual(data[0]["candles"][-1]["close"], candle_rows()[-1][4])
        self.assertEqual(data[0]["candles"][-1]["volume"], candle_rows()[-1][6])
        self.engine.kraken.ohlc.assert_awaited_once_with(BTC, 1)
        self.engine.kraken.candles.assert_not_awaited()
        self.engine.kraken.add.assert_not_awaited()

    async def test_chart_bounds_history_without_dropping_the_forming_candle(self):
        rows = candle_rows(count=721)
        self.engine.kraken.ohlc.side_effect = None
        self.engine.kraken.ohlc.return_value = rows
        rows[-1][6] = "0"
        rows[-2][6] = "123.456"
        response = await self.client.get(f"/api/candles?pair={BTC.id}&interval=1")
        data = await response.json()
        self.assertEqual(data["candles"][-1]["volume"], "0")
        self.assertEqual(data["candles"][-2]["volume"], "123.456")
        self.assertEqual(len(data["candles"]), 720)
        self.assertEqual(data["candles"][0]["time"], rows[1][0])
        self.assertEqual(data["candles"][-1]["time"], rows[-1][0])

    async def test_chart_rejects_unsupported_intervals_and_unknown_markets(self):
        for query in ("interval=10", "interval=0", "interval=no", "interval=1.0", ""):
            response = await self.client.get(f"/api/candles?pair={BTC.id}&{query}")
            self.assertIn(response.status, (400, 409))
        response = await self.client.get("/api/candles?pair=UNKNOWN&interval=1")
        self.assertEqual(response.status, 409)
        self.engine.kraken.ohlc.assert_not_awaited()

    async def test_chart_snapshot_expires_and_failure_is_not_reported_as_fresh(self):
        path = f"/api/candles?pair={BTC.id}&interval=1"
        with patch("kairos.web.time") as clock:
            clock.monotonic.return_value = 100
            clock.time.return_value = 1000
            response = await self.client.get(path)
            self.assertEqual((await response.json())["received"], 1000)
            clock.monotonic.return_value = 106
            self.engine.kraken.ohlc.side_effect = SafetyError("Market data unavailable")
            response = await self.client.get(path)
            self.assertEqual(response.status, 409)
            self.engine.kraken.ohlc.side_effect = lambda pair, minutes: candle_rows(minutes)
            clock.time.return_value = 1006
            response = await self.client.get(path)
            self.assertEqual((await response.json())["received"], 1006)
        self.assertEqual(self.engine.kraken.ohlc.await_count, 3)

    async def test_chart_cache_separates_markets_and_intervals_without_changing_bot(self):
        for interval in (1, 5):
            response = await self.client.get(f"/api/candles?pair={BTC.id}&interval={interval}")
            data = await response.json()
            self.assertEqual(data["interval"], interval)
            self.assertEqual(data["candles"][1]["time"] - data["candles"][0]["time"], interval * 60)
        await self.engine.start()
        for pair in (ETH, CROSS):
            response = await self.client.get(f"/api/candles?pair={pair.id}&interval=1")
            self.assertEqual((await response.json())["pair"], pair.id)
            self.engine.kraken.ohlc.assert_awaited_with(pair, 1)
        self.assertEqual(
            set(self.app["candle_cache"]), {(BTC.id, 1), (BTC.id, 5), (ETH.id, 1), (CROSS.id, 1)}
        )
        self.assertEqual(self.engine.settings["pair"], BTC.id)
        self.assertTrue(self.engine.running)
        self.engine.kraken.add.assert_not_awaited()
        self.engine.jev.decide.assert_not_awaited()

    async def test_chart_cache_is_bounded_while_browsing(self):
        with patch("kairos.web.time") as clock:
            clock.monotonic.return_value = 100
            clock.time.return_value = 1000
            self.app["candle_cache"].update({(str(i), 1): (200, {}) for i in range(32)})
            response = await self.client.get(f"/api/candles?pair={BTC.id}&interval=1")
            self.assertEqual(response.status, 200)
            self.assertEqual(len(self.app["candle_cache"]), 32)
            self.assertIn((BTC.id, 1), self.app["candle_cache"])

    async def test_market_snapshots_and_full_strategy_catalog_keep_execution_gates(self):
        responses = await asyncio.gather(
            self.client.get("/api/markets"), self.client.get("/api/markets")
        )
        data = [await response.json() for response in responses]
        self.assertEqual(data[0], data[1])
        self.assertEqual({row["id"] for row in data[0]["markets"]}, {BTC.id, ETH.id, CROSS.id})
        self.engine.kraken.market_tickers.assert_awaited_once()
        self.engine.kraken.market_changes.assert_awaited_once()
        changes = {row["id"]: row["change_pct"] for row in data[0]["markets"]}
        self.assertEqual(changes, {BTC.id: "2.5", ETH.id: "-1.25", CROSS.id: None})
        response = await self.client.get("/api/pairs")
        self.assertEqual({row["id"] for row in await response.json()}, {BTC.id, ETH.id, CROSS.id})
        response = await self.client.post(
            "/api/settings", json={**self.engine.settings, "pair": CROSS.id}, headers=self.headers()
        )
        self.assertEqual(response.status, 409)
        self.assertIn("quote currency", (await response.json())["error"])
        self.assertEqual(self.engine.settings["pair"], BTC.id)
        self.engine.kraken.add.assert_not_awaited()
        self.engine.jev.decide.assert_not_awaited()

    async def test_changes_cache_has_its_own_timestamp_and_does_not_block_quotes_on_failure(self):
        with patch("kairos.web.time") as clock:
            clock.monotonic.return_value = 100
            clock.time.return_value = 1000
            response = await self.client.get("/api/markets")
            self.assertEqual((await response.json())["change_received"], 1000)
            clock.monotonic.return_value = 111
            clock.time.return_value = 1011
            response = await self.client.get("/api/markets")
            data = await response.json()
            self.assertEqual(data["received"], 1011)
            self.assertEqual(data["change_received"], 1000)
            self.engine.kraken.market_changes.assert_awaited_once()
            clock.monotonic.return_value = 161
            clock.time.return_value = 1061
            self.engine.kraken.market_changes.return_value = {}
            response = await self.client.get("/api/markets")
            data = await response.json()
            self.assertEqual(data["received"], 1061)
            self.assertIsNone(data["change_received"])
            self.assertTrue(all(row["change_pct"] is None for row in data["markets"]))
            self.assertTrue(all(row["last"] == "9995" for row in data["markets"]))
            self.assertEqual(self.engine.kraken.market_changes.await_count, 2)

    async def test_failed_market_refresh_does_not_relabel_stale_prices_as_fresh(self):
        with patch("kairos.web.time") as clock:
            clock.monotonic.return_value = 100
            clock.time.return_value = 1000
            response = await self.client.get("/api/markets")
            self.assertEqual((await response.json())["received"], 1000)
            clock.monotonic.return_value = 111
            self.engine.kraken.market_tickers.side_effect = SafetyError("Market data unavailable")
            response = await self.client.get("/api/markets")
            self.assertEqual(response.status, 200)
            data = await response.json()
            self.assertTrue(any("Spot quotes unavailable" in error for error in data["errors"]))
            self.assertTrue(all(row["received"] == 1000 for row in data["markets"]))
            self.assertEqual(data["received"], 1000)
            self.engine.kraken.market_tickers.side_effect = None
            clock.monotonic.return_value = 122
            clock.time.return_value = 1022
            response = await self.client.get("/api/markets")
            self.assertEqual((await response.json())["received"], 1022)

    async def test_sse_snapshot_and_buffering_header(self):
        response = await self.client.get("/api/events")
        self.assertEqual(response.headers["X-Accel-Buffering"], "no")
        self.assertEqual(await response.content.readline(), b"event: state\n")
        self.assertIn(b'"mode":"dry-run"', await response.content.readline())
        response.close()
