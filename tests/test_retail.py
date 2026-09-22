import asyncio
import base64
import copy
import hashlib
import hmac
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from kairos.accounts import SOURCES, account_snapshot
from kairos.clients import Kraken
from kairos.domain import SafetyError
from kairos.engine import Engine
from kairos.retail import Futures, RetailMarkets, futures_signature
from kairos.store import Store
from kairos.web import create_app
from tests.helpers import BTC, fake_jev, fake_kraken
from tests.test_clients_web import Session

XSTOCK = {
    "altname": "AAPLxUSD",
    "wsname": "AAPLx/USD",
    "base": "AAPLx",
    "quote": "USD",
    "status": "online",
    "aclass_base": "tokenized_asset",
    "leverage_buy": [2],
    "leverage_sell": [2],
}
FUTURE = {
    "symbol": "PF_XBTUSD",
    "base": "BTC",
    "quote": "USD",
    "pair": "BTC:USD",
    "type": "flexible_futures",
    "contractSize": 1,
    "tradeable": True,
    "isExpired": False,
}


def retail_clients():
    spot = fake_kraken()
    fx = replace(BTC, id="ZEURZUSD", symbol="EUR/USD", base="ZEUR")
    spot.pairs[fx.id] = fx

    async def spot_request(method, params=None, private=False):
        if method == "AssetPairs":
            return {
                "AAPLSPV/USD": XSTOCK,
                "AAPLx/USD": XSTOCK,
                "OFFLINE": {**XSTOCK, "status": "cancel_only"},
            }
        if method == "Ticker":
            return {
                "AAPLxUSD": {"a": ["341"], "b": ["339"], "c": ["340"], "v": ["1", "500"]},
                "AAPLSPVUSD": {"a": ["555"], "b": ["553"], "c": ["554"], "v": ["1", "900"]},
            }
        if method == "OHLC":
            return {
                "AAPLxUSD": [[1700000000, "339", "341", "338", "340", "339.5", "2", 5]],
                "last": 1700000000,
            }
        return {
            "BalanceEx": {
                "ZUSD": {
                    "balance": "300000",
                    "hold_trade": "50",
                    "credit": "0",
                    "credit_used": "0",
                },
                "AAPL.T": {"balance": "2"},
            },
            "OpenOrders": {
                "open": {
                    "EXCHANGE-ORDER": {
                        "descr": {
                            "pair": "XBTUSD",
                            "type": "buy",
                            "ordertype": "limit",
                            "price": "100",
                        },
                        "vol": "1",
                        "vol_exec": "0",
                        "status": "open",
                    }
                }
            },
            "TradesHistory": {
                "trades": {
                    "TRADE": {
                        "pair": "XBTUSD",
                        "type": "buy",
                        "vol": "1",
                        "price": "100",
                        "cost": "100",
                        "fee": "0.4",
                    }
                },
                "count": 80,
            },
            "OpenPositions": {
                "POSITION": {
                    "pair": "XBTUSD",
                    "type": "buy",
                    "vol": "1",
                    "vol_closed": "0",
                    "cost": "100",
                    "margin": "20",
                }
            },
            "TradeBalance": {
                "eb": "300000",
                "tb": "300000",
                "e": "300100",
                "m": "20",
                "n": "100",
                "mf": "300080",
                "ml": "1500500",
            },
            "Ledgers": {
                "ledger": {
                    "ENTRY": {
                        "asset": "ZUSD",
                        "amount": "-100",
                        "fee": "0",
                        "time": 1700000000,
                        "balance": "500",
                    }
                },
                "count": 70,
            },
            "Earn/Allocations": {
                "converted_asset": "USD",
                "items": [
                    {
                        "native_asset": "ETH",
                        "amount_allocated": {"total": {"native": "1", "converted": "3000"}},
                    }
                ],
            },
            "SystemStatus": {"status": "online"},
        }[method]

    async def futures_get(method):
        return {
            "instruments": {
                "instruments": [
                    FUTURE,
                    {**FUTURE, "symbol": "EXPIRED", "isExpired": True},
                    {**FUTURE, "symbol": "CLOSED", "tradeable": False},
                ]
            },
            "tickers": {
                "tickers": [
                    {
                        "symbol": "PF_XBTUSD",
                        "bid": 199,
                        "ask": 201,
                        "last": 200,
                        "vol24h": 12,
                        "change24h": -2,
                        "markPrice": 200.5,
                        "fundingRate": 0.01,
                    },
                    {"symbol": "INDEX", "last": 300},
                ]
            },
            "accounts": {
                "accounts": {
                    "cash": {"type": "cashAccount", "balances": {"xbt": "2"}},
                    "flex": {
                        "type": "multiCollateralMarginAccount",
                        "currencies": {"usd": {"quantity": "500"}},
                        "api_secret": "must-not-appear",
                    },
                }
            },
            "openpositions": {
                "openPositions": [
                    {
                        "symbol": "PF_XBTUSD",
                        "side": "long",
                        "size": 2,
                        "price": 200,
                        "unrealizedFunding": 0.1,
                    }
                ]
            },
            "openorders": {
                "openOrders": [
                    {
                        "order_id": "FORDER",
                        "symbol": "PF_XBTUSD",
                        "side": "buy",
                        "orderType": "lmt",
                        "limitPrice": 199,
                        "unfilledSize": 1,
                    }
                ]
            },
            "fills": {
                "fills": [
                    {
                        "fill_id": "FILL",
                        "symbol": "PF_XBTUSD",
                        "side": "buy",
                        "size": 1,
                        "price": 200,
                        "fillTime": "2026-09-22T00:00:00Z",
                    }
                ]
            },
        }[method]

    spot.request = AsyncMock(side_effect=spot_request)
    futures = SimpleNamespace(
        get=AsyncMock(side_effect=futures_get),
        candles=AsyncMock(
            return_value=[
                {
                    "time": 1700000000,
                    "open": "199",
                    "high": "201",
                    "low": "198",
                    "close": "200",
                    "volume": "3",
                }
            ]
        ),
    )
    return spot, futures


class RetailTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_deduplicates_rebased_aliases_and_keeps_execution_separate(self):
        spot, futures = retail_clients()
        before = dict(spot.pairs)
        directory = RetailMarkets(spot, futures)
        catalog = await directory.catalog()
        self.assertEqual(set(catalog), set(before) | {"xstocks:AAPLxUSD", "futures:PF_XBTUSD"})
        self.assertEqual(catalog["ZEURZUSD"]["kind"], "fx")
        self.assertTrue(catalog[BTC.id]["margin"])
        self.assertEqual(catalog["xstocks:AAPLxUSD"]["raw_id"], "AAPLxUSD")
        self.assertEqual(catalog["futures:PF_XBTUSD"]["contract_size"], "1")
        self.assertTrue(catalog["futures:PF_XBTUSD"]["execution_reason"])
        self.assertEqual(spot.pairs, before)
        await directory.catalog()
        self.assertEqual(spot.request.await_count, 1)
        futures.get.assert_awaited_once_with("instruments")

    async def test_venue_failure_keeps_prior_quotes_and_timestamp_without_hiding_other_venue(self):
        spot, futures = retail_clients()
        directory = RetailMarkets(spot, futures)
        await directory.catalog()
        with patch("kairos.retail.time") as clock:
            clock.time.return_value = 1000
            quotes, errors = await directory.extra_quotes()
            self.assertFalse(errors)
            self.assertEqual(quotes["xstocks:AAPLxUSD"]["last"], "340")
            self.assertEqual(quotes["futures:PF_XBTUSD"]["received"], 1000)
            futures.get.side_effect = SafetyError("private details must not be exposed")
            clock.time.return_value = 1050
            quotes, errors = await directory.extra_quotes()
            self.assertEqual(quotes["futures:PF_XBTUSD"]["received"], 1000)
            self.assertEqual(quotes["xstocks:AAPLxUSD"]["received"], 1050)
            self.assertIn("futures", errors)
            self.assertNotIn("private details", str(errors))
        directory.expires = 0
        catalog = await directory.catalog()
        self.assertIn("futures:PF_XBTUSD", catalog)
        self.assertIn("futures", directory.catalog_errors)
        self.assertEqual(spot.pairs[BTC.id], BTC)

    async def test_xstocks_candles_use_canonical_asset_class_without_touching_bot(self):
        spot, futures = retail_clients()
        directory = RetailMarkets(spot, futures)
        catalog = await directory.catalog()
        rows = await directory.candles(catalog["xstocks:AAPLxUSD"], 5)
        self.assertEqual(rows[0]["volume"], "2")
        spot.request.assert_awaited_with(
            "OHLC", {"pair": "AAPLxUSD", "interval": 5, "asset_class": "tokenized_asset"}
        )
        spot.add.assert_not_awaited()

    async def test_futures_auth_is_separate_get_only_and_does_not_follow_redirects(self):
        secret = base64.b64encode(b"unit-test-only").decode()
        expected = base64.b64encode(
            hmac.new(
                b"unit-test-only", hashlib.sha256(b"/api/v3/accounts").digest(), hashlib.sha512
            ).digest()
        ).decode()
        self.assertEqual(futures_signature("/api/v3/accounts", secret), expected)
        session = Session({"result": "success", "accounts": {}})
        client = Futures(session, "unit-key", secret)
        await client.get("accounts")
        args, kwargs = session.calls[0]
        self.assertEqual(args, ("GET", "https://futures.kraken.com/derivatives/api/v3/accounts"))
        self.assertEqual(kwargs["headers"], {"APIKey": "unit-key", "Authent": expected})
        self.assertFalse(kwargs["allow_redirects"])
        for method in ("sendorder", "cancelorder", "withdrawal", "transfer", "../accounts"):
            with self.assertRaises(SafetyError):
                await client.get(method)
        self.assertEqual(len(session.calls), 1)
        with self.assertRaisesRegex(SafetyError, "not configured"):
            await Futures(session).get("accounts")
        self.assertEqual(len(session.calls), 1)

    async def test_futures_public_candles_have_no_auth_and_convert_milliseconds(self):
        session = Session(
            {
                "candles": [
                    {
                        "time": 1700000040000,
                        "open": "100",
                        "high": "102",
                        "low": "99",
                        "close": "101",
                        "volume": "3",
                    }
                ]
            }
        )
        client = Futures(session, "unit-key", "not-used-for-public-data")
        rows = await client.candles("PF_XBTUSD", 60)
        self.assertEqual(rows[0]["time"], 1700000040)
        self.assertEqual(rows[0]["volume"], "3")
        self.assertTrue(session.calls[0][0][1].endswith("/trade/PF_XBTUSD/1h"))
        self.assertEqual(session.calls[0][1]["headers"], {})
        for symbol, minutes in (("../accounts", 1), ("PF_XBTUSD", 2)):
            with self.assertRaises(SafetyError):
                await client.candles(symbol, minutes)
        self.assertEqual(len(session.calls), 1)

    async def test_futures_rejection_is_redacted_without_retry(self):
        session = Session({"result": "error", "error": "credential-bearing-remote-body"})
        with self.assertRaisesRegex(SafetyError, "Futures request rejected") as error:
            await Futures(session).get("instruments")
        self.assertNotIn("credential-bearing", str(error.exception))
        self.assertEqual(len(session.calls), 1)

    async def test_account_views_preserve_units_and_mark_incomplete_history(self):
        spot, futures = retail_clients()
        spot.key = spot.secret = "unit-test-only"
        for source in SOURCES:
            snapshot = await account_snapshot(spot, futures, source)
            self.assertEqual(snapshot["source"], source)
            self.assertTrue(snapshot["columns"])
            self.assertNotIn("must-not-appear", str(snapshot))
            self.assertTrue(all(len(row) == len(snapshot["columns"]) for row in snapshot["rows"]))
        wallet = await account_snapshot(spot, futures, "spot-balances")
        self.assertIn(["AAPL.T", "2", "—", "—", "—"], wallet["rows"])
        self.assertTrue((await account_snapshot(spot, futures, "spot-trades"))["truncated"])
        self.assertTrue((await account_snapshot(spot, futures, "withdrawals"))["truncated"])
        spot.request.assert_awaited_with("Ledgers", {"type": "withdrawal"}, private=True)
        earn = await account_snapshot(spot, futures, "earn")
        self.assertIn(["converted_asset", "USD"], earn["rows"])
        self.assertTrue(
            any(
                "currencies.usd.quantity" in row[0]
                for row in (await account_snapshot(spot, futures, "futures-balances"))["rows"]
            )
        )
        with self.assertRaises(SafetyError):
            await account_snapshot(spot, futures, "Withdraw")
        spot.add.assert_not_awaited()
        spot.cancel.assert_not_awaited()

    async def test_new_spot_read_allowlist_never_grants_funding_writes(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        client = Kraken(
            Session({"error": [], "result": {}}),
            store,
            "unit-key",
            base64.b64encode(b"unit-test-only").decode(),
            allow_live=True,
        )
        for method in (
            "Withdraw",
            "WalletTransfer",
            "AccountTransfer",
            "Earn/Allocate",
            "Earn/Deallocate",
            "DepositAddresses",
        ):
            with self.assertRaises(SafetyError):
                await client.request(method, private=True)
        self.assertEqual(client.session.calls, [])
        with patch("kairos.clients.asyncio.sleep", new=AsyncMock()):
            for method in (
                "TradeBalance",
                "OpenPositions",
                "TradesHistory",
                "Ledgers",
                "Earn/Allocations",
            ):
                await client.request(method, private=True)
        self.assertEqual(len(client.session.calls), 5)


class RetailWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.spot, self.futures = retail_clients()
        self.engine = Engine(self.store, self.spot, fake_jev(), lambda *_: None)
        await self.engine.initialize()
        self.app = create_app(self.engine, "https://kairos.example.test", self.futures)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.store.close()

    async def test_discovery_charts_and_market_failures_cannot_enable_new_execution(self):
        settings = dict(self.engine.settings)
        ledger = copy.deepcopy(self.engine.ledger())
        response = await self.client.get("/api/pairs")
        catalog = {row["id"]: row for row in await response.json()}
        for identifier, unit in (
            ("xstocks:AAPLxUSD", "token units"),
            ("futures:PF_XBTUSD", "contract units"),
        ):
            self.assertTrue(catalog[identifier]["execution_reason"])
            response = await self.client.get(f"/api/candles?pair={identifier}&interval=1")
            data = await response.json()
            self.assertEqual(data["pair"], identifier)
            self.assertEqual(data["volume_unit"], unit)
            response = await self.client.post(
                "/api/settings",
                json={**settings, "pair": identifier},
                headers={"Origin": "https://kairos.example.test", "X-CSRF-Token": self.app["csrf"]},
            )
            self.assertEqual(response.status, 409)
        self.spot.market_tickers.side_effect = SafetyError("spot offline")
        data = await (await self.client.get("/api/markets")).json()
        quotes = {row["id"]: row for row in data["markets"]}
        self.assertEqual(quotes["futures:PF_XBTUSD"]["last"], "200")
        self.assertEqual(quotes["xstocks:AAPLxUSD"]["last"], "340")
        self.assertTrue(any("Spot quotes unavailable" in error for error in data["errors"]))
        self.assertEqual(self.engine.settings, settings)
        self.assertEqual(self.engine.ledger(), ledger)
        self.spot.add.assert_not_awaited()
        self.engine.jev.decide.assert_not_awaited()

    async def test_account_reads_are_cached_separate_and_fail_closed_without_credentials(self):
        before = copy.deepcopy(self.engine.ledger())
        response = await self.client.get("/api/accounts/spot-balances")
        self.assertEqual(response.status, 409)
        self.spot.request.assert_not_awaited()
        self.spot.key = self.spot.secret = "unit-test-only"
        results = await asyncio.gather(
            self.client.get("/api/accounts/spot-balances"),
            self.client.get("/api/accounts/spot-balances?method=Withdraw"),
        )
        snapshots = [await r.json() for r in results]
        self.assertEqual(snapshots[0], snapshots[1])
        self.spot.request.assert_awaited_once_with("BalanceEx", None, private=True)
        self.assertNotIn("unit-test-only", str(snapshots))
        for path in ("/api/accounts/sendorder", "/api/accounts/Withdraw", "/api/accounts/transfer"):
            self.assertEqual((await self.client.get(path)).status, 404)
        self.assertEqual(self.engine.ledger(), before)
        self.assertEqual(self.engine.settings["pair"], BTC.id)
        self.assertFalse(self.engine.running)
        self.spot.add.assert_not_awaited()
        self.spot.cancel.assert_not_awaited()

    async def test_account_errors_never_refresh_or_replace_a_successful_snapshot(self):
        self.spot.key = self.spot.secret = "unit-test-only"
        with patch("kairos.web.time") as clock:
            clock.monotonic.return_value = 100
            clock.time.return_value = 1000
            response = await self.client.get("/api/accounts/spot-balances")
            self.assertEqual((await response.json())["received"], 1000)
            clock.monotonic.return_value = 131
            self.spot.request.side_effect = SafetyError("Account data unavailable")
            self.assertEqual((await self.client.get("/api/accounts/spot-balances")).status, 409)
            self.assertEqual(self.app["account_cache"]["spot-balances"][1]["received"], 1000)
            self.spot.request.side_effect = None
            self.spot.request.return_value = []
            response = await self.client.get("/api/accounts/spot-balances")
            self.assertEqual(response.status, 409)
            self.assertIn("interpreted safely", (await response.json())["error"])
            self.assertEqual(self.app["account_cache"]["spot-balances"][1]["received"], 1000)
