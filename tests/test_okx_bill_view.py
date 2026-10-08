import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from kairos.domain import SafetyError
from kairos.okx_markets import OKXMarkets
from kairos.web import create_app
from tests import test_okx_engine as fixtures


class BillViewTests(unittest.IsolatedAsyncioTestCase):
    book = fixtures.OKXEngineTests.book

    async def asyncSetUp(self):
        await fixtures.OKXEngineTests.asyncSetUp(self)
        self.engine.running = self.engine.armed = False
        self.engine.authorization = None
        self.markets = OKXMarkets(self.engine)
        self.proof = await self.engine.read_account()
        self.rows = [
            {
                "billId": "100",
                "ordId": "200",
                "tradeId": "300",
                "instType": "SPOT",
                "instId": "BTC-USD",
                "type": "2",
                "subType": "2",
                "ccy": "USDC",
                "balChg": "24.723551058051",
                "bal": "4999999.8211523425",
                "fee": "-0.086836355949",
                "sz": "24.810387414",
                "px": "83273.1",
                "ts": "1791420293204",
                "fillTime": "1791420293204",
                "uid": "do-not-export",
                "clOrdId": "do-not-export",
                "notes": "do-not-export",
            },
            {"billId": "101", "ordId": "foreign", "ccy": "BTC", "type": "1", "fee": ""},
        ]
        self.proof["bills"] = self.rows
        self.engine.read_account = AsyncMock(return_value=self.proof)
        self.engine.orders = lambda *args, **kwargs: [{"txid": "200", "instrument": "BTC-USD"}]
        self.before = list(self.store.db.execute("SELECT key,value FROM state ORDER BY key"))

    async def test_exact_native_bill_fields_and_unrecognized_activity_without_state_changes(self):
        result = await self.markets.account_snapshot("okx-bills")
        record, foreign = result["records"]
        self.assertEqual(record["balChg"], "24.723551058051")
        self.assertEqual(record["bal"], "4999999.8211523425")
        self.assertEqual(record["fee"], "-0.086836355949")
        self.assertIn("not accounting approval", record["classification"])
        self.assertIn("Unrecognized", foreign["classification"])
        self.assertEqual(foreign["fee"], "")
        self.assertIsNone(foreign["balChg"])
        self.assertIn("—", result["rows"][1])
        self.assertEqual(result["reported_balance"], self.proof["balance"])
        self.assertNotIn("do-not-export", json.dumps(result))
        self.assertEqual(self.proof["bills"], self.rows)
        self.assertEqual(
            self.before, list(self.store.db.execute("SELECT key,value FROM state ORDER BY key"))
        )
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])
        self.assertFalse(self.engine.running or self.engine.armed)

    async def test_no_binding_and_malformed_representation_do_not_guess_or_mutate(self):
        blank = OKXMarkets(
            SimpleNamespace(
                client=self.client,
                store=SimpleNamespace(get=lambda _: None),
                read_account=AsyncMock(),
            )
        )
        with self.assertRaisesRegex(SafetyError, "existing allocation"):
            await blank.account_snapshot("okx-bills")
        blank.engine.read_account.assert_not_awaited()
        self.proof["bills"] = [{**self.rows[0], "bal": 4999999.8211523425}]
        with self.assertRaisesRegex(SafetyError, "native string"):
            await self.markets.account_snapshot("okx-bills")
        self.assertEqual(
            self.before, list(self.store.db.execute("SELECT key,value FROM state ORDER BY key"))
        )

    async def test_baseline_or_mismatched_instrument_is_not_called_owned(self):
        baseline = copy.deepcopy(self.store.get("okx-account"))
        baseline["bills"] = ["100"]
        self.store.put("okx-account", baseline)
        result = await self.markets.account_snapshot("okx-bills")
        self.assertIn("Baseline", result["records"][0]["classification"])
        baseline["bills"] = []
        self.store.put("okx-account", baseline)
        self.proof["bills"][0]["instId"] = "ETH-USD"
        result = await self.markets.account_snapshot("okx-bills")
        self.assertIn("Unrecognized", result["records"][0]["classification"])

    async def test_existing_account_route_exposes_view_and_caches_without_trading(self):
        self.engine.ready = True
        client = TestClient(TestServer(create_app(self.engine)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        schema = await (await client.get("/api/settings-schema")).json()
        self.assertIn("okx-bills", schema["account_sources"])
        response = await client.get("/api/accounts/okx-bills")
        self.assertEqual(response.status, 200)
        result = await response.json()
        again = await (await client.get("/api/accounts/okx-bills")).json()
        self.assertEqual(result, again)
        self.engine.read_account.assert_awaited_once()
        self.assertEqual(len(result["records"]), 2)
        self.assertFalse(self.engine.running or self.engine.armed)
        self.assertEqual(
            self.before, list(self.store.db.execute("SELECT key,value FROM state ORDER BY key"))
        )
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])
