import copy
import unittest
from types import SimpleNamespace

from kairos.domain import SafetyError
from kairos.okx_markets import OKXMarkets
from tests import test_okx as fixtures


class OKXChartTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = await fixtures.OKXTransportTests().prepare()
        self.markets = OKXMarkets(SimpleNamespace(client=self.client))
        self.market = (await self.markets.catalog())["okx-demo:BTC-USD:USDC"]
        self.rows = [
            ["1700000160000", "101", "103", "100", "102", "0", "999", "999", "0"],
            ["1700000040000", "100", "102", "99", "101", "2", "200", "200", "1"],
        ]
        self.client.session.body = {"code": "0", "data": self.rows}

    async def test_chart_preserves_old_source_times_gaps_base_volume_and_forming_marker(self):
        rows = await self.markets.candles(self.market, 1)
        self.assertEqual([r["time"] for r in rows], [1700000040, 1700000160])
        self.assertEqual([r["volume"] for r in rows], ["2", "0"])
        self.assertEqual([r["complete"] for r in rows], [True, False])
        self.assertFalse(self.client.market_data.pairs)
        self.assertIsNone(self.client.market_data.task)

    async def test_malformed_duplicate_and_foreign_candles_are_not_repaired(self):
        for change in ("duplicate", "volume", "price", "future"):
            rows = copy.deepcopy(self.rows)
            if change == "duplicate":
                rows.append(rows[0])
            elif change == "volume":
                rows[0][5] = ""
            elif change == "price":
                rows[0][2] = "90"
            else:
                rows[0][0] = "9999999960000"
            self.client.session.body = {"code": "0", "data": rows}
            self.client.next_request = 0
            with self.subTest(change=change), self.assertRaises(SafetyError):
                await self.markets.candles(self.market, 1)
        before = len(self.client.session.calls)
        with self.assertRaises(SafetyError):
            await self.markets.candles({"id": "okx:BTC-USD:USDC"}, 1)
        self.assertEqual(len(self.client.session.calls), before)
