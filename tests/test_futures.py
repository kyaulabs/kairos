import base64
import copy
import hashlib
import hmac
import json
import time
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from kairos import programs
from kairos.clients import Kraken
from kairos.domain import DEFAULTS, Pair, SafetyError, dec
from kairos.engine import Engine
from kairos.futures import new_ledger
from kairos.futures_client import FuturesTrading
from kairos.retail import linear_perpetual
from kairos.store import Store
from kairos.web import create_app
from tests.helpers import book, fake_jev, fake_kraken
from tests.test_clients_web import Session

CONTRACT = {
    "symbol": "PF_XBTUSD",
    "type": "flexible_futures",
    "contractSize": 1,
    "base": "BTC",
    "quote": "USD",
    "tradeable": True,
    "isExpired": False,
    "contractValueTradePrecision": 3,
    "tickSize": 0.1,
    "maxPositionSize": 10000,
    "marginLevels": [{"numNonContractUnits": 0, "initialMargin": 0.1, "maintenanceMargin": 0.05}],
}
PAIR = Pair(
    "futures:PF_XBTUSD", "PF_XBTUSD", "BTC", "USD", dec(".1"), dec(".001"), dec(".001"), dec(0)
)


def utc(seconds=None):
    return datetime.fromtimestamp(seconds or time.time(), UTC).isoformat()


class FutureSession(Session):
    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        response.headers = {}
        return response


def fake_futures():
    client = FuturesTrading(None, "test-key", base64.b64encode(b"fixture-only").decode(), True)
    client.pairs, client.metadata = {PAIR.id: PAIR}, {PAIR.id: CONTRACT}
    client.account_uid = "fixture-wallet"
    client.catalog = AsyncMock(return_value=client.pairs)
    client.book = AsyncMock(side_effect=lambda pair: book(pair, "99.9", "100", "100"))
    client.market = AsyncMock(
        return_value={"markPrice": "100", "fundingRate": "0", "postOnly": False}
    )
    client.completed_candles = fake_kraken().candles
    client.fees = AsyncMock(return_value=(dec(10), dec(10)))
    client.wallet = {
        "type": "multiCollateralMarginAccount",
        "currencies": {"USD": {"quantity": "1000"}},
        "availableMargin": "1000",
        "unrealizedFunding": "0",
        "maintenanceMargin": "0",
    }
    client.positions, client.logs, client.executions, client.order_events = [], [], [], []
    client.account = AsyncMock(side_effect=lambda: copy.deepcopy(client.wallet))

    async def get(method, params=None):
        return {
            "openorders": {"openOrders": []},
            "openpositions": {"openPositions": client.positions},
            "pnlpreferences": {"preferences": []},
            "leveragepreferences": {"leveragePreferences": []},
            "historical-funding-rates": {"rates": []},
        }[method]

    async def history(method, since):
        return {
            "orders": client.order_events,
            "executions": client.executions,
            "account-log": client.logs,
        }[method]

    async def request(method, params=None, **kwargs):
        if method == "cancelallordersafter":
            return {"status": {"triggerTime": utc(time.time() + 60)}}
        if method == "cancelorder":
            return {"cancelStatus": {"status": "cancelled"}}
        if method != "sendorder":
            raise AssertionError(method)
        order = {
            "uid": "exchange-" + params["cliOrdId"],
            "clientId": params["cliOrdId"],
            "tradeable": params["symbol"],
            "direction": params["side"].title(),
            "limitPrice": params["limitPrice"],
            "quantity": params["size"],
            "filled": params["size"],
        }
        cost = dec(params["size"]) * dec(params["limitPrice"])
        fee = cost / 1000
        client.order_events.append(
            {"timestamp": time.time() * 1000, "event": {"OrderPlaced": {"order": order}}}
        )
        client.executions.append(
            {
                "event": {
                    "execution": {
                        "execution": {
                            "uid": order["uid"],
                            "order": order,
                            "quantity": params["size"],
                            "price": params["limitPrice"],
                            "orderData": {"fee": str(fee)},
                        }
                    }
                }
            }
        )
        previous = dec(client.wallet["currencies"]["USD"]["quantity"])
        client.wallet["currencies"]["USD"]["quantity"] = str(previous - fee)
        client.logs.append(
            {
                "id": len(client.logs) + 1,
                "margin_account": "flex",
                "asset": "USD",
                "collateral": "USD",
                "info": "futures trade",
                "old_balance": str(previous),
                "new_balance": str(previous - fee),
            }
        )
        client.positions = [
            {
                "symbol": PAIR.symbol,
                "side": "long" if params["side"] == "buy" else "short",
                "size": params["size"],
                "price": params["limitPrice"],
                "pnlCurrency": "USD",
            }
        ]
        return {"sendStatus": {"status": "filled", "order_id": order["uid"]}}

    client.get, client.history, client.request = (
        AsyncMock(side_effect=get),
        AsyncMock(side_effect=history),
        AsyncMock(side_effect=request),
    )
    return client


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_encoded_auth_and_write_allowlist_no_redirects(self):
        session = FutureSession({"result": "success", "logs": []})
        secret = base64.b64encode(b"fixture-only").decode()
        client = FuturesTrading(session, "test-key", secret)
        with self.assertRaisesRegex(SafetyError, "writes disabled"):
            await client.request("sendorder", {})
        for endpoint in ("transfer", "withdrawal", "batchorder"):
            with self.assertRaises(SafetyError):
                await client.request(endpoint)
        self.assertEqual(session.calls, [])
        await client.request("orders", {"continuation_token": "a+b/c="}, history=True)
        (verb, url), args = session.calls[0]
        encoded = "continuation_token=a%2Bb%2Fc%3D"
        digest = hashlib.sha256((encoded + "/api/history/v3/orders").encode()).digest()
        expected = base64.b64encode(
            hmac.new(b"fixture-only", digest, hashlib.sha512).digest()
        ).decode()
        self.assertEqual(args["headers"]["Authent"], expected)
        self.assertTrue(str(url).endswith(encoded))
        self.assertEqual(verb, "GET")
        self.assertFalse(args["allow_redirects"])

    async def test_post_signs_exact_payload_without_retries_or_leaking_errors(self):
        session = FutureSession({"result": "success"})
        client = FuturesTrading(
            session, "test-key", base64.b64encode(b"fixture-only").decode(), True
        )
        await client.request("sendorder", {"processBefore": "2026-09-22T00:00:00+00:00"})
        self.assertIn("%2B", session.calls[0][1]["data"])
        session.status = 503
        with self.assertRaisesRegex(SafetyError, "outcome may be unknown"):
            await client.request("sendorder", {})
        self.assertEqual(len(session.calls), 2)

    async def test_fee_query_uses_structured_spot_json_and_matching_signature(self):
        session = Session(
            {
                "error": [],
                "result": {
                    "fees": {PAIR.symbol: {"fee": ".05"}},
                    "fees_maker": {PAIR.symbol: {"fee": ".02"}},
                },
            }
        )
        store = Store(":memory:")
        self.addCleanup(store.close)
        secret = base64.b64encode(b"fixture-only").decode()
        spot = Kraken(session, store, "key", secret)
        self.assertEqual(await FuturesTrading(None).fees(spot, PAIR), (dec(2), dec(5)))
        args = session.calls[0][1]
        payload = args["data"]
        decoded = json.loads(payload)
        self.assertEqual(decoded["pair"], [{"asset": PAIR.symbol, "aclass": "derivatives"}])
        digest = hashlib.sha256((str(decoded["nonce"]) + payload).encode()).digest()
        signature = base64.b64encode(
            hmac.new(b"fixture-only", b"/0/private/TradeVolume" + digest, hashlib.sha512).digest()
        ).decode()
        self.assertEqual(args["headers"]["API-Sign"], signature)
        self.assertEqual(args["headers"]["Content-Type"], "application/json")

    async def test_catalog_isolated_and_excludes_inverse_dated_and_non_crypto(self):
        client = FuturesTrading(None)
        rows = [
            CONTRACT,
            {**CONTRACT, "symbol": "FI_XBTUSD", "type": "futures_inverse"},
            {**CONTRACT, "symbol": "FF_XBTUSD_260925"},
            {**CONTRACT, "tradfi": True},
            {**CONTRACT, "isExpired": True},
            {**CONTRACT, "contractSize": 10},
        ]
        client.get = AsyncMock(return_value={"instruments": rows})
        self.assertEqual(await client.catalog(), {PAIR.id: PAIR})
        self.assertTrue(linear_perpetual(CONTRACT))

    async def test_history_pagination_and_fail_closed_when_incomplete(self):
        client = FuturesTrading(None)
        client.request = AsyncMock(
            side_effect=[
                {"accountUid": "wallet", "elements": [{"uid": "1"}], "continuationToken": "next"},
                {"accountUid": "wallet", "elements": [{"uid": "2"}]},
            ]
        )
        self.assertEqual(await client.history("orders", 100), [{"uid": "1"}, {"uid": "2"}])
        self.assertEqual(client.request.call_args.args[1]["continuation_token"], "next")
        client.request = AsyncMock(return_value={"accountUid": "wallet", "elements": [{}] * 1000})
        self.assertEqual(len(await client.history("orders", 100)), 1000)
        client.request = AsyncMock(
            return_value={"accountUid": "wallet", "elements": [{}], "continuationToken": "repeated"}
        )
        with self.assertRaisesRegex(SafetyError, "reconciliation budget"):
            await client.history("orders", 100)
        client.request = AsyncMock(
            side_effect=[
                {"accountUid": "wallet", "logs": [{"id": i} for i in range(1, 1001)]},
                {"accountUid": "wallet", "logs": [{"id": 1001}]},
            ]
        )
        self.assertEqual(len(await client.history("account-log", 100)), 1001)
        self.assertEqual(client.request.call_args.args[1]["from"], 1001)

    async def test_fresh_depth_and_completed_candles(self):
        client = FuturesTrading(None)
        client.get = AsyncMock(
            return_value={"serverTime": utc(), "orderBook": {"bids": [[99, 2]], "asks": [[100, 3]]}}
        )
        self.assertEqual((await client.book(PAIR)).mid, dec("99.5"))
        client.get.return_value["serverTime"] = utc(time.time() - 60)
        with self.assertRaisesRegex(SafetyError, "stale"):
            await client.book(PAIR)
        end = int(time.time()) // 60 * 60
        client.candles = AsyncMock(
            return_value=[
                {"time": t, "open": "99", "close": "100", "high": "101", "low": "98", "volume": "1"}
                for t in (end - 60, end)
            ]
        )
        self.assertEqual(len(await client.completed_candles(PAIR, 1)), 1)


class FuturesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store, self.spot, self.client, self.jev = (
            Store(":memory:"),
            fake_kraken(),
            fake_futures(),
            fake_jev(),
        )
        self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None, self.client)
        await self.engine.initialize()
        await self.configure()

    async def asyncTearDown(self):
        self.store.close()

    async def configure(self, **updates):
        values = {
            **self.engine.settings,
            "product": "futures",
            "pair": PAIR.id,
            "futures_leverage": 2,
            "maker_fee_bps": "10",
            "taker_fee_bps": "10",
            "order_size": "200",
            "max_exposure": "1500",
            "futures_live_budget": "1000",
            "dca_amount": "100",
            "dca_count": 3,
            "dca_period_seconds": 60,
            "twap_quantity": "3",
            "twap_limit": "100",
            "twap_slices": 3,
            "twap_duration_seconds": 180,
            "futures_parent_notional": "300",
            **updates,
        }
        await self.engine.configure(values)

    async def place(self, side="buy", volume="1", price="100"):
        self.engine.running = True
        return await self.engine.place(
            PAIR, side, dec(volume), dec(price), await self.client.book(PAIR)
        )

    async def test_paper_long_short_reduction_never_spends_spot_or_sends_writes(self):
        spot = copy.deepcopy(self.engine.ledger())
        order = await self.place()
        self.assertEqual(self.engine.futures.position(PAIR), 1)
        self.assertEqual(dec(self.engine.futures.ledger()["cash"]), dec("999.9"))
        self.assertEqual(order["product"], "futures")
        await self.place("sell", "1", "99.9")
        self.assertEqual(self.engine.futures.position(PAIR), 0)
        await self.place("sell", "1", "99.9")
        self.assertEqual(self.engine.futures.position(PAIR), -1)
        await self.place("buy", "1", "100")
        self.assertEqual(self.engine.futures.position(PAIR), 0)
        self.assertEqual(self.engine.ledger(), spot)
        self.client.request.assert_not_awaited()
        self.spot.add.assert_not_awaited()

    async def test_fill_cumulative_idempotency_and_no_position_flip(self):
        self.engine.running = True
        order = self.engine.futures.intent(PAIR, "buy", dec(2), dec(100), False, False)
        self.store.save_order(order)
        self.engine.futures.apply(order, dec(1), dec(100), dec(".1"), "open")
        before = copy.deepcopy(self.engine.futures.ledger())
        self.engine.futures.apply(order, dec(1), dec(100), dec(".1"), "open")
        self.assertEqual(before, self.engine.futures.ledger())
        self.engine.futures.apply(order, dec(2), dec(200), dec(".2"), "closed")
        with self.assertRaisesRegex(SafetyError, "reversing"):
            await self.place("sell", "3", "99.9")
        self.assertEqual(self.engine.futures.position(PAIR), 2)

    async def test_risk_limits_precision_spread_and_reserved_collateral(self):
        for volume, price in [("3", "100"), (".0001", "100"), ("1", "100.05")]:
            with self.assertRaises(SafetyError):
                await self.place(volume=volume, price=price)
        self.engine.settings["order_size"] = "2000"
        with self.assertRaisesRegex(SafetyError, "collateral"):
            await self.place(volume="20")
        self.assertEqual(self.engine.futures.position(PAIR), 0)
        self.engine.settings["max_spread_bps"] = "1"
        with self.assertRaisesRegex(SafetyError, "spread"):
            await self.place()

    async def test_funding_accrues_once_and_missing_history_stops(self):
        await self.place()
        ledger = self.engine.futures.ledger()
        now = time.time()
        start = int(now // 3600) * 3600
        ledger["funding_ts"] = now - 10
        self.store.put("futures:dry-run", ledger)
        original_get = self.client.get.side_effect

        async def get(method, params=None):
            if method == "historical-funding-rates":
                return {
                    "rates": [
                        {"timestamp": utc(start - 3600), "fundingRate": "36"},
                        {"timestamp": utc(start), "fundingRate": "36"},
                    ]
                }
            return await original_get(method, params)

        self.client.get.side_effect = get
        with patch("kairos.futures.time.time", return_value=now):
            await self.engine.valuation()
            cash = self.engine.futures.ledger()["cash"]
            await self.engine.valuation()
            self.assertEqual(self.engine.futures.ledger()["cash"], cash)
        self.assertEqual(dec(cash), dec("999.8"))
        ledger = self.engine.futures.ledger()
        ledger["funding_ts"] = now - 4 * 3600
        self.store.put("futures:dry-run", ledger)
        with self.assertRaisesRegex(SafetyError, "funding history"):
            await self.engine.valuation()

    async def test_paper_liquidation_halts_and_persists(self):
        await self.place()
        ledger = self.engine.futures.ledger()
        ledger["cash"] = "1"
        self.store.put("futures:dry-run", ledger)
        self.client.market.return_value["markPrice"] = "99"
        with self.assertRaisesRegex(SafetyError, "maintenance"):
            await self.engine.valuation(True)
        self.assertTrue(self.engine.futures.ledger()["halted"])
        self.assertTrue(self.store.orders()[-1]["liquidation"])
        self.client.request.assert_not_awaited()

    async def test_htf_and_maker_use_futures_data_not_spot_execution(self):
        await self.engine.start()
        await self.engine.tick()
        self.assertTrue(self.client.completed_candles.await_count)
        self.assertEqual(self.engine.latest_decision["state"]["product"], "futures")
        self.spot.book.assert_not_awaited()
        self.spot.add.assert_not_awaited()
        await self.engine.stop()
        await self.configure(strategy="maker")
        await self.engine.start()
        await self.engine.tick()
        self.assertTrue(self.store.orders()[-1]["maker"])
        self.assertEqual(self.store.orders()[-1]["product"], "futures")

    async def test_dca_twap_namespace_parent_caps_and_restart_slot_identity(self):
        for strategy in ("dca", "twap"):
            await self.engine.stop()
            await self.engine.reset_paper()
            await self.configure(strategy=strategy)
            await self.engine.start()
            await self.engine.tick()
            self.assertIsNone(self.engine.last_error)
            program = self.store.get(programs.key(self.engine))
            self.assertIn("program:futures:", programs.key(self.engine))
            self.assertEqual(program["next_slot"], 1)
            count = len(self.store.orders())
            await self.engine.tick()
            self.assertEqual(len(self.store.orders()), count)
            restart = Engine(self.store, self.spot, self.jev, lambda *_: None, self.client)
            await restart.initialize()
            self.assertFalse(restart.running)
            self.assertEqual(restart.mode, "dry-run")
            await restart.start()
            self.assertEqual(self.store.get(programs.key(restart))["id"], program["id"])
        self.spot.add.assert_not_awaited()

    async def test_parent_requires_funding_and_reduce_only_opposing_position(self):
        await self.configure(strategy="dca", dca_amount="1000", dca_count=100)
        with self.assertRaisesRegex(SafetyError, "Pre-fund"):
            await self.engine.start()
        await self.configure(strategy="twap", futures_reduce_only=True)
        with self.assertRaisesRegex(SafetyError, "opposing"):
            await self.engine.start()
        self.assertEqual(self.store.orders(), [])

    async def test_short_twap_obeys_floor_and_parent_notional_cap(self):
        await self.configure(strategy="twap", twap_side="sell", twap_limit="99.9")
        await self.engine.start()
        await self.engine.tick()
        self.assertLess(self.engine.futures.position(PAIR), 0)
        await self.engine.stop()
        # A sell limit is a floor, not a bound on margin: the separate notional cap matters.
        await self.engine.reset_paper()
        await self.engine.reset_program("NEW STRATEGY RUN")
        self.client.book = AsyncMock(side_effect=lambda p: book(p, "400", "400.1", "100"))
        await self.engine.start()
        count = len(self.store.orders())
        await self.engine.tick()
        self.assertEqual(len(self.store.orders()), count)
        self.assertIn("parent notional", self.store.get(programs.key(self.engine))["message"])

    async def test_configure_and_mode_gates_do_not_allow_cross_product_execution(self):
        self.spot.allow_live = True
        for confirmation in (None, "ENABLE LIVE TRADING"):
            with self.assertRaisesRegex(SafetyError, "confirmation"):
                await self.engine.set_mode("trading", confirmation)
        self.client.allow_live = False
        with self.assertRaisesRegex(SafetyError, "ALLOW_FUTURES_TRADING"):
            await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        await self.place()
        await self.engine.stop()
        with self.assertRaisesRegex(SafetyError, "Close Futures"):
            await self.configure(product="spot", pair="XXBTZUSD")
        for strategy in ("rebalance", "arbitrage"):
            with self.assertRaisesRegex(SafetyError, "Futures support"):
                await self.configure(strategy=strategy)

    async def test_live_order_recovers_by_history_and_keeps_paper_separate(self):
        self.spot.allow_live = True
        paper = copy.deepcopy(self.engine.futures.ledger())
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        order = await self.place()
        self.assertEqual(order["status"], "closed")
        self.assertEqual(self.engine.futures.position(PAIR), 1)
        await self.engine.valuation()
        self.assertEqual(self.engine.futures.ledger("dry-run"), paper)
        self.spot.add.assert_not_awaited()
        send = next(
            c.args[1] for c in self.client.request.call_args_list if c.args[0] == "sendorder"
        )
        self.assertEqual(send["orderType"], "ioc")
        self.assertEqual(send["cliOrdId"], order["id"])
        self.assertIn("processBefore", send)
        await self.engine.futures.refresh(order)
        self.assertEqual(self.engine.futures.position(PAIR), 1)
        self.assertEqual(dec(self.engine.futures.ledger()["fees"]), dec(".1"))

    async def test_ambiguous_write_blocks_restart_and_cannot_be_rearmed(self):
        self.spot.allow_live = True
        await self.configure(strategy="dca")
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        original = self.client.request.side_effect

        async def lost_ack(method, params=None, **kwargs):
            if method == "sendorder":
                raise TimeoutError()
            return await original(method, params, **kwargs)

        self.client.request.side_effect = lost_ack
        await self.engine.start()
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.store.orders()[-1]["status"], "uncertain")
        with self.assertRaisesRegex(SafetyError, "reconcile"):
            await self.engine.reset_program("NEW STRATEGY RUN")
        with self.assertRaisesRegex(SafetyError, "Reconcile"):
            await self.engine.start()
        writes = [c for c in self.client.request.call_args_list if c.args[0] == "sendorder"]
        self.assertEqual(len(writes), 1)

    async def test_lost_ack_after_execution_recovers_without_duplicate(self):
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        original = self.client.request.side_effect

        async def lost_ack(method, params=None, **kwargs):
            result = await original(method, params, **kwargs)
            if method == "sendorder":
                raise TimeoutError()
            return result

        self.client.request.side_effect = lost_ack
        with self.assertRaisesRegex(SafetyError, "not settled"):
            await self.place()
        order = self.store.orders()[-1]
        self.assertEqual(order["status"], "uncertain")
        self.engine.running = False
        await self.engine.reconcile()
        self.assertEqual(self.store.orders()[-1]["status"], "closed")
        self.assertEqual(self.engine.futures.position(PAIR), 1)
        self.assertEqual(
            len([c for c in self.client.request.call_args_list if c.args[0] == "sendorder"]), 1
        )

    async def test_live_wallet_changes_fees_and_preferences_fail_closed(self):
        self.spot.allow_live = True
        self.client.wallet["currencies"]["USD"]["quantity"] = "2000"
        with self.assertRaisesRegex(SafetyError, "exactly"):
            await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        self.client.wallet["currencies"]["USD"]["quantity"] = "1000"
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        self.client.logs.append({"id": 1, "margin_account": "flex", "info": "transfer"})
        with self.assertRaisesRegex(SafetyError, "wallet change"):
            await self.place()
        self.client.logs.clear()
        self.client.fees.return_value = (dec(10), dec(100))
        with self.assertRaisesRegex(SafetyError, "fees"):
            await self.place()
        self.assertFalse(any(c.args[0] == "sendorder" for c in self.client.request.call_args_list))

    async def test_stop_during_live_preparation_prevents_submission(self):
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")

        async def stop_during_fees(*args):
            self.engine.running = False
            self.engine.stop_generation += 1
            return dec(10), dec(10)

        self.client.fees.side_effect = stop_during_fees
        with self.assertRaisesRegex(SafetyError, "Stopped"):
            await self.place()
        self.assertEqual(self.store.orders(), [])
        self.assertFalse(any(c.args[0] == "sendorder" for c in self.client.request.call_args_list))

    async def test_manual_reduce_requires_confirmation_and_can_exit_after_daily_loss(self):
        await self.place()
        await self.engine.stop()
        self.engine.settings["daily_loss"] = ".01"
        with self.assertRaisesRegex(SafetyError, "confirm"):
            await self.engine.close_futures(None)
        await self.engine.close_futures("REDUCE FUTURES POSITION")
        self.assertEqual(self.engine.futures.position(PAIR), 0)
        self.assertTrue(self.store.orders()[-1]["reduce_only"])

    async def test_reset_preserves_live_collateral_and_other_portfolios(self):
        self.store.put("futures:trading", new_ledger("321"))
        spot, live = self.engine.ledger(), self.engine.futures.ledger("trading")
        await self.engine.reset_paper()
        self.assertEqual(self.engine.ledger(), spot)
        self.assertEqual(self.engine.futures.ledger("trading"), live)

    async def test_close_endpoint_is_csrf_and_confirmation_protected(self):
        app = create_app(self.engine, "http://test", self.client)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post("/api/close-futures", json={})
            self.assertEqual(response.status, 403)
            response = await client.post(
                "/api/close-futures",
                json={},
                headers={"Origin": "http://test", "X-CSRF-Token": app["csrf"]},
            )
            self.assertEqual(response.status, 409)
        finally:
            await client.close()

    async def test_partial_cancel_history_survives_restart_and_deduplicates_fills(self):
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        order = self.engine.futures.intent(PAIR, "buy", dec(1), dec(100), False, False)
        order["created"] -= 86400
        self.store.save_order(order)
        row = {
            "uid": "historic-order",
            "clientId": order["id"],
            "tradeable": PAIR.symbol,
            "direction": "Buy",
            "quantity": "1",
            "limitPrice": "100",
            "filled": ".5",
        }
        self.client.order_events.append(
            {"timestamp": time.time() * 1000, "event": {"OrderCancelled": {"order": row}}}
        )
        execution = {
            "event": {
                "execution": {
                    "execution": {
                        "uid": "fill-1",
                        "order": row,
                        "quantity": ".5",
                        "price": "100",
                        "orderData": {"fee": ".05"},
                    }
                }
            }
        }
        self.client.executions.extend([execution, copy.deepcopy(execution)])
        await self.engine.futures.refresh(order)
        await self.engine.futures.refresh(order)
        self.assertEqual(order["status"], "canceled")
        self.assertEqual(self.engine.futures.position(PAIR), dec(".5"))
        self.assertEqual(dec(self.engine.futures.ledger()["fees"]), dec(".05"))
        self.client.executions.clear()
        with self.assertRaisesRegex(SafetyError, "not yet consistent"):
            await self.engine.futures.refresh(order)

    async def test_history_lag_retries_reads_without_resending_order(self):
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        original = self.client.history.side_effect
        attempts = 0

        async def lag(method, since):
            nonlocal attempts
            if method == "orders":
                attempts += 1
                if attempts == 1:
                    return []
            return await original(method, since)

        self.client.history.side_effect = lag
        with patch("kairos.futures.asyncio.sleep", new=AsyncMock()):
            order = await self.place()
        self.assertEqual(order["status"], "closed")
        self.assertEqual(attempts, 2)
        self.assertEqual(
            len([c for c in self.client.request.call_args_list if c.args[0] == "sendorder"]), 1
        )

    async def test_live_dca_uses_only_futures_with_durable_program_identity(self):
        await self.configure(strategy="dca")
        self.spot.allow_live = True
        self.jev.key = ""
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        await self.engine.start()
        await self.engine.tick()
        self.assertIsNone(self.engine.last_error)
        order = self.store.orders()[-1]
        self.assertEqual(order["status"], "closed")
        self.assertEqual(order["program_id"], self.store.get(programs.key(self.engine))["id"])
        self.jev.decide.assert_not_awaited()
        self.spot.add.assert_not_awaited()

    async def test_live_twap_uses_futures_price_bound(self):
        await self.configure(strategy="twap")
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        await self.engine.start()
        await self.engine.tick()
        self.assertIsNone(self.engine.last_error)
        self.assertEqual(self.store.orders()[-1]["price"], "100")
        self.assertEqual(self.store.orders()[-1]["filled"], "1")

    async def test_live_htf_uses_contract_execution_after_model_and_cost_filters(self):
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        await self.engine.start()
        await self.engine.tick()
        self.assertIsNone(self.engine.last_error)
        self.assertGreater(self.engine.futures.position(PAIR), 0)
        self.assertEqual(self.store.orders()[-1]["strategy"], "htf")
        self.assertEqual(self.engine.latest_decision["state"]["product"], "futures")
        self.spot.add.assert_not_awaited()

    async def test_live_maker_fill_is_reconciled_before_account_valuation(self):
        await self.configure(strategy="maker")
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        await self.engine.start()
        await self.engine.tick()
        self.assertIsNone(self.engine.last_error)
        sent = next(
            c.args[1] for c in self.client.request.call_args_list if c.args[0] == "sendorder"
        )
        self.assertEqual(sent["orderType"], "post")
        self.assertEqual(self.store.orders()[-1]["status"], "closed")

    async def test_futures_mode_change_remains_paused_and_live_positions_block_paper_start(self):
        await self.engine.start()
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        self.assertFalse(self.engine.running)
        await self.place()
        await self.engine.set_mode("dry-run", None)
        with self.assertRaisesRegex(SafetyError, "Live Futures positions remain"):
            await self.engine.start()

    async def test_isolated_preferences_nonusd_settlement_and_cash_gaps_stop_writes(self):
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        original = self.client.get.side_effect

        async def isolated(method, params=None):
            if method == "leveragepreferences":
                return {"leveragePreferences": [{"symbol": PAIR.symbol, "maxLeverage": 2}]}
            return await original(method, params)

        self.client.get.side_effect = isolated
        with self.assertRaisesRegex(SafetyError, "cross margin"):
            await self.place()

        async def nonusd(method, params=None):
            if method == "pnlpreferences":
                return {"preferences": [{"symbol": PAIR.symbol, "pnlCurrency": "BTC"}]}
            return await original(method, params)

        self.client.get.side_effect = nonusd
        with self.assertRaisesRegex(SafetyError, "settlement to USD"):
            await self.place()
        self.client.get.side_effect = original
        self.client.wallet["currencies"]["USD"]["quantity"] = "1100"
        with self.assertRaisesRegex(SafetyError, "cash history disagree"):
            await self.place()
        self.assertFalse(any(c.args[0] == "sendorder" for c in self.client.request.call_args_list))

    async def test_futures_short_entries_require_downward_move_to_cover_costs(self):
        self.jev.decide.return_value["action"] = "sell"
        rows = copy.deepcopy(await self.client.completed_candles(PAIR, 15))
        for i, row in enumerate(rows):
            row[4] = str(10000 - i)
        self.client.completed_candles.return_value = rows
        await self.engine.start()
        await self.engine.tick()
        self.assertFalse(self.engine.latest_decision["state"]["short_entry_eligible"])
        self.assertEqual(self.store.orders(), [])

    async def test_changing_credentials_cannot_adopt_a_different_wallet(self):
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE FUTURES")
        self.assertEqual(self.engine.futures.ledger()["account_uid"], "fixture-wallet")
        self.client.account_uid = "different-wallet"
        with self.assertRaisesRegex(SafetyError, "account crossover"):
            await self.place()
        self.assertEqual(self.store.orders(), [])

    async def test_settings_migration_keeps_required_legacy_risk_validation(self):
        old = {k: v for k, v in DEFAULTS.items() if not k.startswith("futures_")}
        self.store.put("settings", old)
        migrated = Engine(self.store, self.spot, self.jev, lambda *_: None, self.client)
        self.assertEqual(migrated.settings["futures_leverage"], 1)
        del old["daily_loss"]
        self.store.put("settings", old)
        with self.assertRaises(SafetyError):
            Engine(self.store, self.spot, self.jev, lambda *_: None, self.client)
