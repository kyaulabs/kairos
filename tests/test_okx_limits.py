import asyncio
import time
import unittest
from unittest.mock import patch

from kairos import okx_cycle
from kairos.domain import SafetyError, dec
from kairos.okx_engine import OKXEngine
from kairos.store import Store
from tests import test_okx_engine as fixtures
from tests.test_okx import instrument


class OKXLimitTests(unittest.IsolatedAsyncioTestCase):
    book = fixtures.OKXEngineTests.book

    async def asyncSetUp(self):
        self.venue = fixtures.Venue(
            instrument(instId="BTC-USDT", quoteCcy="USDT", tradeQuoteCcyList=["USDT"])
        )
        self.client = self.venue.client
        await self.client.catalog()
        self.pair = self.client.resolve("okx-demo:BTC-USDT:USDT")
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.engine = OKXEngine(self.store, self.client, None, lambda *_: None)
        self.engine.settings.update(pair=self.pair.id, quote="USDT")
        self.client.session = None
        await self.client.market_data.configure([self.pair])
        self.client.session = self.venue
        self.client.market_data.task = asyncio.create_task(asyncio.sleep(1000))
        self.addAsyncCleanup(self.client.market_data.close)
        self.book()
        self.values = {
            "kind": "execution_cycle",
            "pair": self.pair.id,
            "allocation": "500",
            "budget": "25",
        }

    async def test_usdt_cycle_values_only_native_cap_and_preserves_other_assets(self):
        preview = await okx_cycle.preview(self.engine, self.values)
        limits = preview["venue_limit_preview"]
        self.assertEqual(limits["price_currency"], "USDT")
        self.assertEqual(limits["usd_reference"]["instrument"], "USDT-USD")
        self.assertEqual(limits["usd_reference"]["rate"], "0.99952")
        self.assertEqual(
            dec(limits["usd_notional"]),
            dec(limits["quantity"]) * dec(limits["price"]) * dec("0.99952"),
        )
        self.assertIsNone(self.engine.ledger())
        await okx_cycle.authorize(self.engine, preview["id"], preview["confirmation"])
        await self.engine.operation_task
        result = self.store.get("okx-operation")
        self.assertEqual(result["status"], "PASSED_WITH_DUST")
        self.assertEqual(len(result["orders"]), 2)
        self.assertTrue(
            all(
                o["venue_limits"]["usd_reference"]["instrument"] == "USDT-USD"
                for o in result["orders"]
            )
        )
        self.assertEqual(self.venue.cash["USDC"], 10000)
        self.assertEqual(self.venue.cash["USD"], 10)
        self.assertEqual(self.venue.cash["BTC"], 1 + dec(result["residual"]))
        self.assertFalse(self.engine.armed or self.engine.running)
        reads = [c for c in self.venue.calls if "/market/index-tickers" in c[1]]
        self.assertGreaterEqual(len(reads), 3)
        for _, _, kwargs in reads:
            self.assertEqual(kwargs["headers"]["x-simulated-trading"], "1")
            self.assertFalse(any(k.startswith("OK-ACCESS-") for k in kwargs["headers"]))

    async def test_cap_and_stale_reference_block_preview_before_binding_or_intent(self):
        self.venue.instrument["maxLmtAmt"] = "24"
        self.venue.usd_rate = "1.1"
        with self.assertRaisesRegex(SafetyError, "USD notional"):
            await okx_cycle.preview(self.engine, self.values)
        self.assertIsNone(self.engine.ledger())
        self.assertFalse(self.store.orders())
        self.venue.usd_rate = "0.9"
        preview = await okx_cycle.preview(self.engine, self.values)
        self.assertLess(dec(preview["venue_limit_preview"]["usd_notional"]), 24)
        self.venue.index_age = 30
        with self.assertRaisesRegex(SafetyError, "reference age"):
            await okx_cycle.preview(self.engine, self.values)
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])

    async def test_twap_preview_checks_largest_rounded_child_not_parent_total(self):
        self.venue.instrument["maxLmtAmt"] = "11"
        self.engine.settings.update(
            twap_quantity=".00060001",
            twap_limit="50000",
            twap_slices=3,
            twap_duration_seconds=180,
        )
        preview = await okx_cycle.preview(self.engine, {**self.values, "kind": "twap"})
        self.assertEqual(preview["venue_limit_preview"]["quantity"], "0.00020001")
        self.assertLess(dec(preview["venue_limit_preview"]["usd_notional"]), 11)
        self.assertGreater(dec(preview["budget"]), 11)
        self.assertIsNone(self.engine.ledger())
        self.assertFalse(self.store.orders())

    async def test_changed_cap_reference_before_submission_is_known_not_sent(self):
        preview = await okx_cycle.preview(self.engine, self.values)
        self.venue.index_age = 30
        await okx_cycle.authorize(self.engine, preview["id"], preview["confirmation"])
        await self.engine.operation_task
        result = self.store.get("okx-operation")
        self.assertEqual(result["status"], "PREFLIGHT_BLOCKED")
        self.assertFalse(result["submitted"])
        self.assertEqual(self.store.orders()[0]["write_outcome"], "not_sent")
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])
        self.assertEqual(self.engine.balance("USDT"), 500)

    async def test_reference_freshness_is_rechecked_after_transport_admission(self):
        preview = await okx_cycle.preview(self.engine, self.values)
        original = self.client.write_guard
        calls = 0

        def guard(path, payload):
            nonlocal calls
            if path.endswith("/order"):
                calls += 1
                if calls == 2:
                    future = time.time() + 20
                    with patch("kairos.okx.time.time", return_value=future):
                        return original(path, payload)
            return original(path, payload)

        self.client.write_guard = guard
        await okx_cycle.authorize(self.engine, preview["id"], preview["confirmation"])
        await self.engine.operation_task
        result = self.store.get("okx-operation")
        self.assertEqual(calls, 2)
        self.assertIn("reference age", result["message"])
        self.assertFalse(result["submitted"])
        self.assertEqual(self.store.orders()[0]["write_outcome"], "not_sent")
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])

    async def test_bad_index_identity_rate_and_timestamp_are_not_replaced_with_parity(self):
        original = self.client.request
        for changes in (
            {"instId": "USDC-USD"},
            {"idxPx": "0"},
            {"idxPx": "NaN"},
            {"ts": str(int((time.time() + 30) * 1000))},
        ):

            async def request(method, path, changes=changes, **kwargs):
                if path.endswith("/index-tickers"):
                    return [
                        {
                            "instId": "USDT-USD",
                            "idxPx": "0.99952",
                            "ts": str(int(time.time() * 1000)),
                            **changes,
                        }
                    ]
                return await original(method, path, **kwargs)

            with patch.object(self.client, "request", request), self.assertRaises(SafetyError):
                await okx_cycle.preview(self.engine, self.values)
        self.assertIsNone(self.engine.ledger())
        self.assertFalse(self.store.orders())
