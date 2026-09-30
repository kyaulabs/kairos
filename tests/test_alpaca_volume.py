"""Read-only, watchlist-scoped Alpaca volume; no broker credentials or network."""

import copy
import unittest
from unittest.mock import patch

from kairos.alpaca import iso, timestamp
from kairos.alpaca_markets import AlpacaMarkets
from kairos.domain import SafetyError
from tests.test_alpaca import PaperBroker


class AlpacaVolumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.broker = PaperBroker()
        await self.broker.client.catalog()
        self.markets = AlpacaMarkets(self.broker.client)
        self.now = timestamp("2026-09-30T15:07:00Z")
        self.cutoff = int(self.now) // 300 * 300
        clock = self.enterContext(patch("kairos.alpaca_markets.time"))
        self.clock, self.monotonic = clock.time, clock.monotonic
        self.clock.return_value, self.monotonic.return_value = self.now, 1000
        self.crypto, self.stock = "alpaca:BTC/USD", "alpaca:AAPL"
        self.calls = []
        self.pages = [
            {
                "bars": {"BTC/USD": [{"t": iso(self.cutoff - 300), "v": "1.25", "vw": "125000"}]},
                "next_page_token": None,
            }
        ]
        self.stocks = {
            "AAPL": {"dailyBar": {"t": "2026-09-30T04:00:00Z", "v": 1200, "vw": "50.25"}}
        }
        self.broker.client.request.side_effect = self.request

    async def request(self, method, path, *, params, **kwargs):
        self.assertEqual(method, "GET")
        if path in {"/v1beta3/crypto/us/bars", "/v2/stocks/snapshots"}:
            self.assertTrue(kwargs["data"])
            self.calls.append((path, copy.deepcopy(params)))
            if path.endswith("snapshots"):
                return self.stocks
            page = self.pages.pop(0)
            if isinstance(page, Exception):
                raise page
            return page
        return await self.broker.request(method, path, params=params, **kwargs)

    async def test_browse_and_quote_selection_never_warm_volume_cache(self):
        await self.markets.market_snapshot(None, self.crypto)
        await self.markets.market_snapshot({self.crypto, self.stock}, self.crypto)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.markets.volumes, {})
        with self.assertRaisesRegex(SafetyError, "targeted quote selection"):
            await self.markets.market_snapshot(None, self.crypto, {self.stock})
        with self.assertRaisesRegex(SafetyError, "targeted quote selection"):
            await self.markets.market_snapshot({self.crypto}, self.crypto, {self.stock})
        self.assertEqual(self.calls, [])
        await self.markets.market_snapshot({self.stock}, self.crypto, {self.stock})
        self.assertEqual([p for p, _ in self.calls], ["/v2/stocks/snapshots"])
        self.assertEqual(self.calls[0][1], {"symbols": "AAPL", "feed": "iex"})
        result = await self.markets.market_snapshot(None, self.crypto)
        rows = {row["id"]: row for row in result["markets"]}
        self.assertNotIn("volume", rows[self.crypto])
        self.assertEqual(rows[self.stock]["volume"], "60300.00")
        self.assertEqual(rows[self.stock]["volume_unit"], "USD")
        self.assertEqual(rows[self.stock]["volume_session"], "2026-09-30")
        self.assertEqual(len(self.calls), 1)

    async def test_crypto_exact_window_pagination_and_decimal_sum(self):
        self.pages = [
            {
                "bars": {"BTC/USD": [{"t": iso(self.cutoff - 86400), "v": ".1", "vw": "100.1"}]},
                "next_page_token": "next",
            },
            {
                "bars": {"BTC/USD": [{"t": iso(self.cutoff - 300), "v": ".2", "vw": "200.2"}]},
                "next_page_token": None,
            },
        ]
        result = await self.markets.market_snapshot({self.crypto}, self.crypto, {self.crypto})
        row = result["markets"][0]
        self.assertEqual(row["volume"], "50.05")
        self.assertEqual(row["volume_unit"], "USD")
        self.assertEqual(row["volume_asof"], self.cutoff)
        self.assertEqual(row["volume_received"], self.now)
        first = self.calls[0][1]
        self.assertEqual(first["symbols"], "BTC/USD")
        self.assertEqual(first["timeframe"], "5Min")
        self.assertEqual(first["start"], iso(self.cutoff - 86400))
        self.assertEqual(first["end"], iso(self.cutoff - 1))
        self.assertEqual(self.calls[1][1]["page_token"], "next")
        self.assertFalse(self.broker.orders)

    async def test_five_minute_cache_and_removed_favorites_do_not_refresh(self):
        await self.markets.refresh_volumes({self.crypto})
        original = copy.deepcopy(self.markets.volumes)
        self.monotonic.return_value += 299
        await self.markets.refresh_volumes({self.crypto})
        self.assertEqual(len(self.calls), 1)
        self.monotonic.return_value += 1000
        self.clock.return_value += 1299
        # A cached former favorite still appears in the chooser but is not fetched.
        result = await self.markets.market_snapshot(None, self.crypto)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["markets"][0]["volume_received"], self.now)
        self.assertEqual(self.markets.volumes, original)
        self.pages = [SafetyError("Rate limited")]
        errors = await self.markets.refresh_volumes({self.crypto})
        self.assertTrue(errors)
        self.assertEqual(self.markets.volumes, original)
        await self.markets.refresh_volumes({self.crypto})
        self.assertEqual(len(self.calls), 2)  # Failure attempts are throttled too.

    async def test_partial_pages_and_invalid_bars_never_replace_good_cache(self):
        await self.markets.refresh_volumes({self.crypto})
        original = copy.deepcopy(self.markets.volumes)
        bad_bars = [
            {"t": iso(self.cutoff), "v": 100},
            {"t": iso(self.cutoff - 86400 - 300), "v": 100},
            {"t": iso(self.cutoff - 299), "v": 100},
            {"t": iso(self.cutoff - 300), "v": -1},
            {"t": iso(self.cutoff - 300), "v": "NaN"},
        ]
        for bar in bad_bars:
            with self.subTest(bar=bar):
                self.monotonic.return_value += 300
                self.pages = [{"bars": {"BTC/USD": [bar]}}]
                self.assertTrue(await self.markets.refresh_volumes({self.crypto}))
                self.assertEqual(self.markets.volumes, original)
        for pages in (
            [{"bars": {}, "next_page_token": "repeat"}] * 2,
            [{"bars": {}, "next_page_token": str(i)} for i in range(20)],
            [
                {
                    "bars": {"BTC/USD": [{"t": iso(self.cutoff - 300), "v": 1, "vw": 100}]},
                    "next_page_token": "next",
                }
            ]
            * 2,
            [{"bars": {"UNREQUESTED/USD": []}}],
        ):
            self.monotonic.return_value += 300
            self.pages = pages
            self.assertTrue(await self.markets.refresh_volumes({self.crypto}))
            self.assertEqual(self.markets.volumes, original)

    async def test_empty_is_unavailable_but_reported_zero_is_valid(self):
        self.pages = [{"bars": {}}]
        self.stocks = {"AAPL": {"dailyBar": None}}
        errors = await self.markets.refresh_volumes({self.crypto, self.stock})
        self.assertEqual(len(errors), 2)
        self.assertFalse(self.markets.volumes)
        self.monotonic.return_value += 300
        self.pages = [{"bars": {"BTC/USD": [{"t": iso(self.cutoff - 300), "v": 0}]}}]
        self.stocks["AAPL"]["dailyBar"] = {"t": "2026-09-29T04:00:00Z", "v": 0}
        self.assertFalse(await self.markets.refresh_volumes({self.crypto, self.stock}))
        self.assertEqual(self.markets.volumes[self.crypto]["volume"], "0")
        self.assertEqual(self.markets.volumes[self.stock]["volume"], "0")
        self.assertEqual(self.markets.volumes[self.stock]["volume_session"], "2026-09-29")

    async def test_missing_or_invalid_vwap_does_not_fall_back_to_latest_price(self):
        await self.markets.refresh_volumes({self.crypto, self.stock})
        original = copy.deepcopy(self.markets.volumes)
        for vwap in (None, 0, -1, "NaN"):
            self.monotonic.return_value += 300
            self.pages = [
                {"bars": {"BTC/USD": [{"t": iso(self.cutoff - 300), "v": 2, "vw": vwap}]}}
            ]
            self.stocks["AAPL"]["dailyBar"]["vw"] = vwap
            self.assertEqual(len(await self.markets.refresh_volumes({self.crypto, self.stock})), 2)
            self.assertEqual(self.markets.volumes, original)

    async def test_unknown_and_oversized_subscriptions_rejected_before_io(self):
        oversized = {f"alpaca:FAKE{i}" for i in range(101)}
        self.broker.client.pairs.update(dict.fromkeys(oversized))
        for wanted in ({"missing"}, oversized):
            with self.assertRaises(SafetyError):
                await self.markets.refresh_volumes(wanted)
        self.assertFalse(self.calls)
