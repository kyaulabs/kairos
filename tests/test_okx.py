import asyncio
import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

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
    async def read(self):
        return json.dumps(self.body).encode()


class OKXSession(Session):
    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return OKXResponse(self.body, self.status)


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
            "action": "snapshot",
            "data": [
                {
                    "ts": str(int(time.time() * 1000)),
                    "bids": [["50000", "1", "0", "2"]],
                    "asks": [["50001", "1", "0", "2"]],
                    **changes,
                }
            ],
        }

    async def test_old_source_new_receipt_disconnect_and_foreign_market_stay_blocked(self):
        self.assertIn("wsuspap", PUBLIC_WS[self.client.environment])
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
