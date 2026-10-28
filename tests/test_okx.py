import asyncio
import base64
import hashlib
import hmac
import json
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from kairos.domain import SafetyError, dec
from kairos.okx import OKX, PUBLIC_WS, REST, OKXRejected, PendingOKX
from tests.test_clients_web import Response, Session


def instrument(**changes):
    return {
        "instId": "BTC-USD",
        "instType": "SPOT",
        "baseCcy": "BTC",
        "quoteCcy": "USD",
        "tradeQuoteCcyList": ["USD", "USDC", "USDG"],
        "state": "live",
        "tickSz": ".1",
        "lotSz": ".00000001",
        "minSz": ".00001",
        "groupId": "4",
        "maxLmtSz": "100",
        "maxLmtAmt": "1000000",
        **changes,
    }


class OKXResponse(Response):
    @property
    def content(self):
        return self

    async def readexactly(self, size):
        self.requested_error_bytes = size
        raw = await self.read()
        if len(raw) < size:
            raise asyncio.IncompleteReadError(raw, size)
        return raw[:size]

    async def read(self):
        return json.dumps(self.body).encode()


class OKXSession(Session):
    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.last_response = OKXResponse(self.body, self.status)
        return self.last_response


class OKXTransportTests(unittest.IsolatedAsyncioTestCase):
    def client(self, environment="demo", body=None):
        session = OKXSession(body or {"code": "0", "data": []})
        return OKX(
            session,
            "fixture-okx-key",
            "fixture-okx-secret",
            "fixture-passphrase",
            environment=environment,
            allow_writes=True,
        )

    async def prepare(self, environment="demo"):
        client = self.client(environment, {"code": "0", "data": [instrument()]})
        await client.catalog()
        client.next_request = 0
        client.write_guard = lambda *_: True
        return client

    def payload(self):
        return {
            "instId": "BTC-USD",
            "tdMode": "cash",
            "clOrdId": "a" * 32,
            "side": "buy",
            "ordType": "ioc",
            "sz": ".0001",
            "px": "50000",
            "tradeQuoteCcy": "USDC",
            "stpMode": "cancel_taker",
            "pxAmendType": "0",
        }

    async def test_exact_signature_query_bytes_and_regional_environment_headers(self):
        for environment, flag in (("live", "0"), ("demo", "1")):
            client = await self.prepare(environment)
            await client.request("GET", "/api/v5/account/balance", params={"ccy": "USD,USDC"})
            (method, url), kw = client.session.calls[-1]
            self.assertEqual(str(url), REST + "/api/v5/account/balance?ccy=USD%2CUSDC")
            headers = kw["headers"]
            raw = headers["OK-ACCESS-TIMESTAMP"] + "GET/api/v5/account/balance?ccy=USD%2CUSDC"
            expected = base64.b64encode(
                hmac.new(b"fixture-okx-secret", raw.encode(), hashlib.sha256).digest()
            ).decode()
            self.assertEqual(headers["OK-ACCESS-SIGN"], expected)
            self.assertEqual(headers["x-simulated-trading"], flag)
            self.assertFalse(kw["allow_redirects"])
            self.assertIsNone(kw["data"])
            client.next_request = 0
            client.session.body = {
                "code": "0",
                "data": [{"sCode": "0", "ordId": "123", "clOrdId": "a" * 32}],
            }
            await client.request(
                "POST", "/api/v5/trade/order", payload=self.payload(), deadline=time.time() + 4
            )
            (method, url), kw = client.session.calls[-1]
            raw = (
                kw["headers"]["OK-ACCESS-TIMESTAMP"].encode()
                + b"POST/api/v5/trade/order"
                + kw["data"]
            )
            self.assertEqual(
                kw["headers"]["OK-ACCESS-SIGN"],
                base64.b64encode(
                    hmac.new(b"fixture-okx-secret", raw, hashlib.sha256).digest()
                ).decode(),
            )
            self.assertEqual(json.loads(kw["data"]), self.payload())
            self.assertEqual(kw["headers"]["x-simulated-trading"], flag)
            self.assertIn("expTime", kw["headers"])

    async def test_prohibited_endpoints_payloads_and_no_write_gate_cannot_send(self):
        client = await self.prepare()
        before = len(client.session.calls)
        for path in (
            "/api/v5/asset/transfer",
            "/api/v5/asset/withdrawal",
            "/api/v5/account/set-account-level",
            "/api/v5/trade/order-algo",
        ):
            with self.assertRaises(SafetyError):
                await client.request("POST", path, payload={})
        for change in (
            {"tdMode": "cross"},
            {"ordType": "market"},
            {"ordType": "optimal_limit_ioc"},
            {"tradeQuoteCcy": "USDT"},
            {"stpMode": "cancel_maker"},
            {"ccy": "USDC"},
            {"sz": ".000100001"},
            {"attachAlgoOrds": [{"slTriggerPx": "90", "slOrdPx": "-1"}]},
        ):
            with self.assertRaises(SafetyError):
                await client.request(
                    "POST", "/api/v5/trade/order", payload={**self.payload(), **change}
                )
        client.allow_writes = False
        with self.assertRaises(SafetyError):
            await client.request("POST", "/api/v5/trade/order", payload=self.payload())
        client.allow_writes = True
        client.write_guard = lambda *_: False
        with self.assertRaises(SafetyError):
            await client.request("POST", "/api/v5/trade/order", payload=self.payload())
        self.assertEqual(len(client.session.calls), before)

    async def test_stop_while_write_waits_for_admission_prevents_network_submission(self):
        client = await self.prepare()
        before = len(client.session.calls)
        await client.request_lock.acquire()
        task = asyncio.create_task(
            client.request("POST", "/api/v5/trade/order", payload=self.payload())
        )
        await asyncio.sleep(0)
        client.write_guard = lambda *_: False
        client.request_lock.release()
        with self.assertRaises(SafetyError):
            await task
        self.assertEqual(len(client.session.calls), before)

    async def test_missing_keys_wrong_environment_errors_and_redirects_never_fallback(self):
        client = OKX(OKXSession({}), environment="demo")
        with self.assertRaises(SafetyError):
            await client.catalog()
        self.assertFalse(client.session.calls)
        client = self.client(
            body={"code": "50101", "data": [], "msg": "fixture-okx-key fixture-passphrase"}
        )
        with self.assertRaises(OKXRejected) as caught:
            await client.request("GET", "/api/v5/account/config")
        self.assertIn("environment mismatch", str(caught.exception))
        self.assertNotIn("fixture", str(caught.exception))
        self.assertEqual(len(client.session.calls), 1)
        client.session.status = 302
        client.next_request = 0
        with self.assertRaises(SafetyError):
            await client.request("GET", "/api/v5/account/config")
        self.assertEqual(client.environment, "demo")
        self.assertTrue(
            all(str(args[1]).startswith(REST + "/") for args, _ in client.session.calls)
        )

    async def test_http_401_retains_only_numeric_venue_code_without_secret_body(self):
        client = self.client(
            body={
                "code": "50105",
                "msg": "fixture-passphrase fixture-okx-key",
                "data": [{"secret": "fixture-okx-secret"}],
            }
        )
        client.session.status = 401
        with self.assertRaises(SafetyError) as caught:
            await client.request("GET", "/api/v5/account/config")
        self.assertNotIsInstance(caught.exception, PendingOKX)
        self.assertIn("HTTP 401 code 50105", str(caught.exception))
        self.assertNotIn("fixture", str(caught.exception))
        self.assertEqual(client.last_request["venue_code"], "50105")
        self.assertEqual(client.session.last_response.requested_error_bytes, 4097)
        self.assertEqual(len(client.session.calls), 1)

    async def test_http_error_malformed_oversized_or_success_code_never_leaks_or_succeeds(self):
        for body in (
            {"code": "0", "msg": "must-not-leak"},
            {"code": "50105<script>", "msg": "must-not-leak"},
            {"code": "50105", "msg": "must-not-leak" * 1000},
            {"code": "50105" * 20},
            ["must-not-leak"],
            None,
        ):
            client = self.client()
            client.session.body, client.session.status = body, 401
            with (
                self.subTest(body_type=type(body).__name__),
                self.assertRaises(SafetyError) as caught,
            ):
                await client.request("GET", "/api/v5/account/config")
            self.assertIn("HTTP 401", str(caught.exception))
            self.assertNotIn("code ", str(caught.exception))
            self.assertNotIn("must-not-leak", str(caught.exception))
            self.assertNotIn("venue_code", client.last_request)
            self.assertEqual(len(client.session.calls), 1)

    async def test_error_body_failure_keeps_original_HTTP_classification_and_no_retry(self):
        for status, writing in ((401, False), (429, False), (503, False), (503, True)):
            client = await self.prepare()
            client.session.status = status
            client.session.body = {"code": "50105", "msg": "fixture-okx-secret"}
            before = len(client.session.calls)
            with patch.object(OKXResponse, "readexactly", AsyncMock(side_effect=TimeoutError)):
                with (
                    self.subTest(status=status, writing=writing),
                    self.assertRaises(SafetyError) as caught,
                ):
                    await client.request(
                        "POST" if writing else "GET",
                        "/api/v5/trade/order" if writing else "/api/v5/account/config",
                        payload=self.payload() if writing else None,
                    )
            self.assertIn(f"HTTP {status}", str(caught.exception))
            self.assertEqual(
                isinstance(caught.exception, PendingOKX), not writing and status in (429, 503)
            )
            self.assertEqual(len(client.session.calls), before + 1)

    async def test_non_json_HTTP_error_keeps_status_without_echoing_body(self):
        client = self.client()
        client.session.status = 401
        with patch.object(
            OKXResponse, "read", AsyncMock(return_value=b"<html>fixture-okx-secret</html>")
        ):
            with self.assertRaises(SafetyError) as caught:
                await client.request("GET", "/api/v5/account/config")
        self.assertIn("HTTP 401", str(caught.exception))
        self.assertNotIn("fixture", str(caught.exception))
        self.assertNotIn("venue_code", client.last_request)

    async def test_registered_secret_is_not_echoed_even_if_shaped_like_venue_code(self):
        client = self.client(body={"code": "50105", "data": []})
        client.session.status = 401
        with patch("kairos.diagnostics._secrets", {"50105"}):
            with self.assertRaises(SafetyError) as caught:
                await client.request("GET", "/api/v5/account/config")
        self.assertNotIn("50105", str(caught.exception))
        self.assertNotIn("venue_code", client.last_request)

    async def test_canceled_error_read_is_not_swallowed_or_retried(self):
        client = self.client()
        client.session.status = 401
        with patch.object(
            OKXResponse, "readexactly", AsyncMock(side_effect=asyncio.CancelledError)
        ):
            with self.assertRaises(asyncio.CancelledError):
                await client.request("GET", "/api/v5/account/config")
        self.assertEqual(len(client.session.calls), 1)

    async def test_http_200_per_order_failure_and_timeouts_do_not_mean_acceptance(self):
        client = await self.prepare()
        for status, kind in (("51008", OKXRejected), ("50004", SafetyError)):
            client.next_request = 0
            client.session.body = {
                "code": "0",
                "data": [
                    {
                        "sCode": status,
                        "sMsg": "fixture-okx-secret",
                        "ordId": "",
                        "clOrdId": "a" * 32,
                    }
                ],
            }
            before = len(client.session.calls)
            with self.assertRaises(kind) as caught:
                await client.request("POST", "/api/v5/trade/order", payload=self.payload())
            self.assertNotIn("fixture", str(caught.exception))
            self.assertEqual(len(client.session.calls), before + 1)
            if status == "50004":
                self.assertNotIsInstance(caught.exception, OKXRejected)

    async def test_account_quote_mapping_fee_groups_and_documented_signed_rates(self):
        client = await self.prepare()
        usd = client.resolve("okx-demo:BTC-USD:USD")
        usdc = client.resolve("okx-demo:BTC-USD:USDC")
        self.assertNotEqual(usd.id, usdc.id)
        self.assertEqual(usdc.quote, "USDC")
        self.assertEqual(client.instruments[usdc.id]["quoteCcy"], "USD")
        client.session.body = {
            "code": "0",
            "data": [
                {
                    "instType": "SPOT",
                    "feeGroup": [
                        {"groupId": "4", "maker": ".0001", "taker": "-.0012"},
                        {"groupId": "1", "maker": "-.01", "taker": "-.02"},
                    ],
                }
            ],
        }
        maker, taker = await client.fees([usdc])
        self.assertEqual(maker[usdc.id], dec(-1))
        self.assertEqual(taker[usdc.id], dec(12))
        client.next_request = 0
        client.session.body["data"][0]["feeGroup"] = []
        with self.assertRaisesRegex(SafetyError, "fee group"):
            await client.fees([usdc])

    async def test_chart_reads_keep_environment_but_send_no_credentials(self):
        for environment, flag in (("demo", "1"), ("live", "0")):
            client = self.client(environment)
            await client.request(
                "GET",
                "/api/v5/market/candles",
                params={"instId": "BTC-USDT", "bar": "1Dutc", "limit": "300"},
            )
            (method, url), kwargs = client.session.calls[-1]
            self.assertEqual(method, "GET")
            self.assertTrue(str(url).startswith(REST + "/api/v5/market/candles?"))
            self.assertEqual(kwargs["headers"]["x-simulated-trading"], flag)
            self.assertFalse(any(k.startswith("OK-ACCESS-") for k in kwargs["headers"]))
            self.assertFalse(kwargs["allow_redirects"])

    async def test_pagination_missing_or_repeated_evidence_is_not_complete(self):
        client = self.client()
        rows = [{"billId": str(i)} for i in range(100)]
        with patch.object(client, "request", AsyncMock(side_effect=[rows, rows])):
            with self.assertRaisesRegex(SafetyError, "repeated"):
                await client.pages("/api/v5/trade/fills", cursor_key="billId", instType="SPOT")
        with patch.object(
            client, "request", AsyncMock(side_effect=[rows, [{"billId": "100"}]])
        ) as request:
            result = await client.pages("/api/v5/trade/fills", cursor_key="billId", instType="SPOT")
            self.assertEqual(len(result), 101)
            self.assertEqual(request.call_args.kwargs["params"]["after"], "99")


class OKXDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = OKXTransportTests().client(body={"code": "0", "data": [instrument()]})
        await self.client.catalog()
        self.client.session = None
        self.pair = self.client.resolve("okx-demo:BTC-USD:USDC")
        self.feed = self.client.market_data
        await self.feed.configure([self.pair])
        self.feed.message({"event": "subscribe", "arg": self.feed.argument()})
        self.addAsyncCleanup(self.feed.close)

    def message(self, **changes):
        return {
            "arg": self.feed.argument(),
            "data": [
                {
                    "ts": str(int(time.time() * 1000)),
                    "bids": [["50000", "1", "0", "2"]],
                    "asks": [["50001", "1", "0", "2"]],
                    **changes,
                }
            ],
        }

    async def test_port_advisory_does_not_discard_snapshots_but_upgrade_notice_does(self):
        queue = asyncio.Queue()
        socket = MagicMock()
        socket.__aenter__.return_value = socket
        socket._response = SimpleNamespace(url=PUBLIC_WS[self.client.environment])
        socket.send_json = AsyncMock()

        async def receive(**kwargs):
            return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(await queue.get()))

        socket.receive = AsyncMock(side_effect=receive)
        connect = AsyncMock(return_value=socket)
        self.client.session = SimpleNamespace(ws_connect=connect)
        advisory = {
            "event": "notice",
            "code": "64010",
            "msg": "WebSocket port 8443 will be retired soon. Please migrate to port 443.",
        }
        for row in (
            advisory,
            {"event": "subscribe", "arg": self.feed.argument()},
            self.message(),
        ):
            queue.put_nowait(row)
        await self.feed.configure([self.pair])
        book = await asyncio.wait_for(self.client.book(self.pair), 1)
        connect.assert_awaited_once_with("wss://wsuspap.okx.com/ws/v5/public", max_msg_size=2**20)
        socket.send_json.assert_awaited_once_with(
            {"op": "subscribe", "args": [self.feed.argument()]}
        )
        self.assertEqual(self.feed.status, "streaming")
        self.assertIsNone(self.feed.error)
        stamp, generation = book.received, self.feed.generation
        self.feed.message(advisory)
        self.assertIs(await self.client.book(self.pair), book)
        self.assertEqual((book.received, self.feed.generation), (stamp, generation))
        queue.put_nowait({"event": "notice", "code": "64008", "msg": "not retained"})
        for _ in range(100):
            if self.feed.status == "reconnecting":
                break
            await asyncio.sleep(0.001)
        self.assertEqual(self.feed.status, "reconnecting")
        self.assertFalse(self.feed.connected)
        self.assertFalse(self.feed.books)
        self.assertNotIn("not retained", self.feed.error)
        with self.assertRaises(PendingOKX):
            book.fresh(10)

    async def reconnect_sessions(self, sessions, *, blocked):
        sockets, old_books = [], []
        for rows in sessions:
            queue = asyncio.Queue()
            for row in rows:
                queue.put_nowait(row)
            socket = MagicMock()
            socket.__aenter__.return_value = socket
            socket._response = SimpleNamespace(url=PUBLIC_WS[self.client.environment])
            socket.send_json = AsyncMock()

            async def receive(queue=queue, **kwargs):
                row = await queue.get()
                if row is None:
                    old_books.extend(self.feed.books.values())
                    return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)
                return SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=row if isinstance(row, str) else json.dumps(row),
                )

            socket.receive = AsyncMock(side_effect=receive)
            sockets.append(socket)
        connect = AsyncMock(side_effect=sockets)
        self.client.session = SimpleNamespace(ws_connect=connect)
        sleep = asyncio.sleep

        async def backoff(seconds):
            await sleep(0)

        with patch("kairos.okx_data.asyncio.sleep", side_effect=backoff):
            await self.feed.configure(list(self.feed.pairs.values()))
            for _ in range(1000):
                if self.feed.blocked or (
                    not blocked
                    and connect.await_count == len(sockets)
                    and len(self.feed.books) == len(self.feed.pairs)
                ):
                    break
                await sleep(0.001)
            else:
                self.fail("Public reconnect fixture did not reach its bounded terminal state")
        return connect, old_books

    async def test_healthy_snapshots_reset_reconnect_budget_and_old_books_stay_invalid(self):
        ack = {"event": "subscribe", "arg": self.feed.argument()}
        connect, old_books = await self.reconnect_sessions(
            [[ack, self.message(), None] for _ in range(6)] + [[ack, self.message()]],
            blocked=False,
        )
        self.assertFalse(self.feed.blocked)
        self.assertEqual(connect.await_count, 7)
        self.assertEqual(self.feed.status, "streaming")
        self.assertIsNone(self.feed.error)
        (await self.client.book(self.pair)).fresh(10)
        self.assertEqual(len(old_books), 6)
        for book in old_books:
            with self.assertRaisesRegex(PendingOKX, "connection changed"):
                book.fresh(10)
        self.assertEqual({call.args[0] for call in connect.await_args_list}, {PUBLIC_WS["demo"]})

    async def test_controls_and_stale_snapshots_do_not_reset_failure_budget(self):
        ack = {"event": "subscribe", "arg": self.feed.argument()}
        for snapshot in ([], [self.message(ts=str(int((time.time() - 60) * 1000)))]):
            with self.subTest(stale_snapshot=bool(snapshot)):
                await self.feed.close()
                self.client.session = None
                await self.feed.configure([self.pair])
                rows = [ack, "pong", {"event": "notice", "code": "64010"}, *snapshot, None]
                connect, _ = await self.reconnect_sessions([rows] * 5, blocked=True)
                self.assertEqual(connect.await_count, 5)
                self.assertTrue(self.feed.blocked)
                self.assertEqual(self.feed.status, "blocked")
                self.assertFalse(self.feed.books)
                self.assertIn("5 consecutive connection failures", self.feed.error)
                self.assertIn("explicitly restart the feed", self.feed.error)

    async def test_partial_market_readiness_cannot_reset_failure_budget(self):
        second = replace(self.pair, id="okx-demo:ETH-USD:USDC", symbol="ETH/USDC", base="ETH")
        self.client.pairs[second.id] = second
        self.client.instruments[second.id] = {"instId": "ETH-USD"}
        await self.feed.configure([self.pair, second])
        rows = [
            *({"event": "subscribe", "arg": self.feed.argument(p)} for p in (self.pair, second)),
            self.message(),
            None,
        ]
        connect, _ = await self.reconnect_sessions([rows] * 5, blocked=True)
        self.assertEqual(connect.await_count, 5)
        self.assertTrue(self.feed.blocked)
        self.assertIn("5 consecutive connection failures", self.feed.error)
        self.assertFalse(self.feed.books)

    async def test_invalid_snapshot_blocks_immediately_without_recovery_retries(self):
        ack = {"event": "subscribe", "arg": self.feed.argument()}
        connect, _ = await self.reconnect_sessions(
            [[ack, self.message(bids=[["50002", "1"]]), None]], blocked=True
        )
        self.assertEqual(connect.await_count, 1)
        self.assertTrue(self.feed.blocked)
        self.assertFalse(self.feed.books)
        self.assertNotIn("consecutive connection failures", self.feed.error)

    async def test_unknown_notices_still_require_a_new_snapshot(self):
        for value in ("64008", "64099", None, "invalid-not-retained"):
            with self.subTest(code=value), self.assertRaisesRegex(PendingOKX, "new snapshot"):
                self.feed.message({"event": "notice", "code": value})

    async def test_documented_snapshot_only_books5_omits_action_but_rejects_updates(self):
        self.feed.message(self.message(instId="BTC-USD", seqId=123))
        self.assertEqual((await self.client.book(self.pair)).bids[0][0], dec(50000))
        with self.assertRaisesRegex(SafetyError, "full snapshot"):
            self.feed.message({**self.message(), "action": "update"})
        with self.assertRaisesRegex(SafetyError, "payload instrument"):
            self.feed.message(self.message(instId="ETH-USD"))

    async def test_old_source_new_receipt_disconnect_and_foreign_market_stay_blocked(self):
        self.assertEqual(PUBLIC_WS[self.client.environment], "wss://wsuspap.okx.com/ws/v5/public")
        self.feed.message(self.message(ts=str(int((time.time() - 15) * 1000))))
        book = self.feed.books[self.pair.id]
        with self.assertRaisesRegex(PendingOKX, "source age"):
            book.fresh(10)
        self.feed.message(self.message())
        book = await self.client.book(self.pair)
        self.assertEqual(book.pair.quote, "USDC")
        self.assertEqual(book.environment, "demo")
        book.fresh(10)
        with self.assertRaises(SafetyError):
            self.feed.message(
                {**self.message(), "arg": {"channel": "books5", "instId": "BTC-USDT"}}
            )
        self.feed.invalidate()
        with self.assertRaises(PendingOKX):
            book.fresh(10)
        with self.assertRaises(SafetyError):
            await self.client.book(self.client.resolve("okx-demo:BTC-USD:USD"))
