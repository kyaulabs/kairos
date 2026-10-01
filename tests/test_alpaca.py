"""Hosted-paper safety and reconciliation with documented API-shaped fixtures; no network."""

import asyncio
import copy
import time
import unittest
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from kairos import htf
from kairos.alpaca import DATA_URL, PAPER_URL, Alpaca, iso
from kairos.alpaca_engine import AlpacaEngine
from kairos.clients import ExchangeRejected
from kairos.domain import SafetyError, dec
from kairos.exchanges import ExchangeDesk, require_flat
from kairos.programs import fill_cost
from kairos.settings import schema
from kairos.store import Store
from kairos.web import create_app
from tests.helpers import RuleEngine, fake_jev, fake_kraken
from tests.test_clients_web import Session


class PaperBroker:
    """Deterministic remote account fixture, not a fill simulator or profitability test."""

    def __init__(self):
        self.cash, self.holdings = dec(10000), {}
        self.orders, self.activities, self.calls = {}, [], []
        self.open, self.fill = True, True
        self.account_id = "paper-account-one"
        self.client = Alpaca(None, "paper-test-key", "paper-test-secret", allow_paper=True)
        self.client.request = AsyncMock(side_effect=self.request)

    async def request(self, method, path, *, params=None, payload=None, **kwargs):
        self.calls.append((method, path, copy.deepcopy(params or payload)))
        if path == "/v2/assets":
            crypto = params["asset_class"] == "crypto"
            return [
                {
                    "symbol": "BTC/USD" if crypto else "AAPL",
                    "class": params["asset_class"],
                    "status": "active",
                    "tradable": True,
                    "exchange": "CRYPTO" if crypto else "NASDAQ",
                    "min_trade_increment": ".0001",
                    "min_order_size": ".0001",
                    "price_increment": ".01",
                }
            ]
        if path == "/v2/account":
            return {
                "id": self.account_id,
                "status": "ACTIVE",
                "crypto_status": "ACTIVE",
                "currency": "USD",
                "cash": str(self.cash),
                "non_marginable_buying_power": str(self.cash),
            }
        if path == "/v2/positions":
            return [
                {
                    "symbol": k,
                    "qty": str(q),
                    "side": "long",
                    "current_price": "100" if k == "BTC/USD" else "50",
                }
                for k, q in self.holdings.items()
                if q
            ]
        if path == "/v2/clock":
            return {
                "timestamp": iso(time.time()),
                "is_open": self.open,
                "next_open": iso(time.time() + 86400),
            }
        if path == "/v2/account/activities":
            return copy.deepcopy(self.activities)
        if path == "/v2/orders" and method == "GET":
            return [
                copy.deepcopy(r)
                for r in self.orders.values()
                if params["status"] == "all" or r["status"] in {"new", "partially_filled"}
            ]
        if path == "/v2/orders" and method == "POST":
            row = {
                **payload,
                "id": f"remote-{len(self.orders) + 1}",
                "status": "new",
                "filled_qty": "0",
                "filled_avg_price": None,
            }
            self.orders[row["id"]] = row
            if self.fill:
                self.fill_order(row["id"], dec(payload["qty"]))
            return copy.deepcopy(row)
        if path == "/v2/orders:by_client_order_id":
            return copy.deepcopy(
                next(
                    r
                    for r in self.orders.values()
                    if r["client_order_id"] == params["client_order_id"]
                )
            )
        if path.startswith("/v2/orders/"):
            row = self.orders[path.rsplit("/", 1)[1]]
            if method == "DELETE":
                row["status"] = "canceled"
                return None
            return copy.deepcopy(row)
        if path.endswith("/orderbooks"):
            return {
                "orderbooks": {
                    "BTC/USD": {
                        "b": [{"p": 100, "s": 100}],
                        "a": [{"p": 100.1, "s": 100}],
                        "t": iso(time.time()),
                    }
                }
            }
        if path == "/v1beta3/crypto/us/latest/quotes":
            return {
                "quotes": {
                    "BTC/USD": {"bp": 100, "bs": 100, "ap": 100.1, "as": 100, "t": iso(time.time())}
                }
            }
        if path == "/v2/stocks/quotes/latest":
            return {
                "quotes": {
                    "AAPL": {"bp": 50, "bs": 10, "ap": 50.02, "as": 10, "t": iso(time.time())}
                }
            }
        raise AssertionError((method, path, params, payload))

    def fill_order(self, identifier, total):
        row = self.orders[identifier]
        delta = total - dec(row["filled_qty"])
        sign = 1 if row["side"] == "buy" else -1
        self.cash -= sign * delta * dec(row["limit_price"])
        self.holdings[row["symbol"]] = self.holdings.get(row["symbol"], dec(0)) + sign * delta
        row.update(
            filled_qty=str(total),
            filled_avg_price=row["limit_price"],
            status="filled" if total == dec(row["qty"]) else "partially_filled",
        )
        self.activities.append(
            {"id": f"fill-{len(self.activities)}", "activity_type": "FILL", "order_id": identifier}
        )


class AlpacaClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_hosts_headers_redirects_permissions_and_no_write_retries(self):
        session = Session({})
        client = Alpaca(session, "paper-test-key", "paper-test-secret")
        await client.request("GET", "/v2/account")
        args, options = session.calls[0]
        self.assertEqual(args, ("GET", PAPER_URL + "/v2/account"))
        self.assertEqual(options["headers"]["APCA-API-KEY-ID"], "paper-test-key")
        self.assertFalse(options["allow_redirects"])
        with self.assertRaises(SafetyError):
            await client.request("POST", "/v2/orders", payload={})
        client.allow_paper = True
        for method, path, data in (
            ("POST", "/v2/account", False),
            ("DELETE", "/v2/positions", False),
            ("POST", "/v2/orders", True),
        ):
            with self.assertRaises(SafetyError):
                await client.request(method, path, data=data)
        client.requests.last_request = 0
        await client.request("GET", "/v2/stocks/quotes/latest", data=True)
        self.assertEqual(session.calls[-1][0][1], DATA_URL + "/v2/stocks/quotes/latest")
        session.status = 503
        before = len(session.calls)
        with self.assertRaises(SafetyError):
            await client.request("POST", "/v2/orders", payload={})
        self.assertEqual(len(session.calls), before + 1)
        session.status = 422
        with self.assertRaises(ExchangeRejected):
            await client.request("POST", "/v2/orders", payload={})
        with self.assertRaises(ExchangeRejected):
            await client.request("POST", "/v2/orders", deadline=time.time() - 1)
        self.assertEqual(len(session.calls), before + 2)

    async def test_catalog_equity_limits_free_feed_and_order_shape(self):
        broker = PaperBroker()
        client = broker.client
        await client.catalog()
        btc, stock = client.pairs["alpaca:BTC/USD"], client.pairs["alpaca:AAPL"]
        self.assertEqual(stock.lot, 1)
        with self.assertRaises(SafetyError):
            stock.validate(dec(".5"), dec(50))
        await client.book(stock)
        self.assertEqual(broker.calls[-1][2]["feed"], "iex")
        params = {
            "pair": btc.id,
            "type": "buy",
            "ordertype": "limit",
            "volume": ".1",
            "price": "100",
            "cl_ord_id": "owned",
            "oflags": "post,fciq",
            "deadline": iso(time.time() + 5),
        }
        await client.add(params)
        payload = broker.calls[-1][2]
        self.assertEqual(payload["time_in_force"], "gtc")
        self.assertNotIn("post_only", payload)
        self.assertFalse(payload["extended_hours"])
        broker.open = False
        with self.assertRaisesRegex(SafetyError, "session"):
            await client.add(
                {**params, "pair": stock.id, "volume": "1", "price": "50", "oflags": "fciq"}
            )
        self.assertEqual(sum(c[0] == "POST" for c in broker.calls), 1)

    async def test_activity_pagination_cannot_silently_truncate_or_repeat(self):
        client = Alpaca(None)
        page = [{"id": str(i)} for i in range(100)]
        client.request = AsyncMock(side_effect=[page, [{"id": "100"}]])
        self.assertEqual(len(await client.activities("today")), 101)
        self.assertEqual(client.request.call_args.kwargs["params"]["page_token"], "99")
        client.request = AsyncMock(side_effect=[page, page])
        with self.assertRaisesRegex(SafetyError, "repeated"):
            await client.activities("today")


class AlpacaEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.broker = PaperBroker()
        self.store = Store(":memory:")
        self.engine = AlpacaEngine(self.store, self.broker.client, fake_jev(), lambda *_: None)
        await self.engine.initialize()
        await self.engine.configure(
            {**self.engine.settings, "strategy": "dca", "dca_amount": "60", "dca_count": 1}
        )

    async def asyncTearDown(self):
        await self.engine.close()
        self.store.close()

    async def start(self):
        await self.engine.start(confirmation="START ALPACA PAPER")

    async def buy(self, *, maker=False):
        pair = self.engine.resolve(self.engine.settings["pair"])
        book = await self.broker.client.book(pair)
        return await self.engine.place(
            pair, "buy", dec(".1"), dec("100" if maker else "100.20"), book, maker
        )

    async def test_idle_does_not_adopt_account_or_send_orders_and_start_requires_consent(self):
        await self.engine.tick()
        self.assertIsNone(self.store.get("alpaca-account"))
        await self.engine.configure({**self.engine.settings, "paper_balance": "750"})
        self.assertEqual(self.engine.balance("USD"), 750)
        with self.assertRaisesRegex(SafetyError, "confirmation"):
            await self.engine.start()
        self.broker.client.allow_paper = False
        with self.assertRaisesRegex(SafetyError, "ALLOW_ALPACA"):
            await self.start()
        self.assertFalse(any(c[0] != "GET" for c in self.broker.calls))
        for mode in ("trading", "dry-run", "paper"):
            with self.assertRaises(SafetyError):
                await self.engine.set_mode(mode, "ENABLE LIVE TRADING")
        with self.assertRaises(SafetyError):
            await self.engine.reset_paper()

    async def test_account_adoption_rejects_prior_holdings_orders_and_identity_changes(self):
        self.broker.holdings["BTC/USD"] = dec(1)
        with self.assertRaisesRegex(SafetyError, "unused"):
            await self.start()
        self.broker.holdings.clear()
        self.broker.orders["external"] = {"status": "canceled"}
        with self.assertRaisesRegex(SafetyError, "unused"):
            await self.start()
        self.broker.orders.clear()
        await self.start()
        self.assertEqual(self.engine.mode, "paper")
        self.broker.account_id = "another-account"
        with self.assertRaisesRegex(SafetyError, "changed"):
            await self.engine.reconcile_account()
        await self.engine.stop()
        with self.assertRaisesRegex(SafetyError, "resized"):
            await self.engine.configure({**self.engine.settings, "paper_balance": "1000"})

    async def test_remote_fills_and_fee_debits_are_reconciled_exactly_once(self):
        await self.start()
        order = await self.buy()
        self.assertEqual(order["mode"], "paper")
        self.assertFalse(order["fee_reported"])
        self.assertEqual(self.engine.balance("BTC"), dec(".1"))
        self.assertEqual(fill_cost(order), dec(order["cost"]) * dec("1.0025"))
        fee = {
            "id": "crypto-fee",
            "activity_type": "CFEE",
            "status": "executed",
            "symbol": "BTCUSD",
            "net_amount": "0",
            "qty": "-.00025",
        }
        self.broker.activities.append(fee)
        self.broker.holdings["BTC/USD"] -= dec(".00025")
        await self.engine.reconcile_account()
        ledger = self.engine.ledger()
        self.assertEqual(dec(ledger["fees"]["BTC"]), dec(".00025"))
        self.assertEqual(self.engine.balance("BTC"), dec(".09975"))
        await self.engine.reconcile_account()
        self.assertEqual(ledger, self.engine.ledger())
        await self.engine.stop()
        with self.assertRaisesRegex(SafetyError, "holdings"):
            require_flat(self.engine)

    async def test_passive_partial_fill_stop_cancels_remainder_without_taker_fallback(self):
        await self.start()
        self.broker.fill = False
        order = await self.buy(maker=True)
        self.broker.fill_order(order["txid"], dec(".03"))
        await self.engine.stop()
        self.assertEqual(self.engine.orders()[0]["status"], "canceled")
        self.assertEqual(self.engine.balance("BTC"), dec(".03"))
        await self.engine.reconcile_account()
        self.assertEqual(sum(c[0] == "POST" for c in self.broker.calls), 1)
        self.assertEqual(sum(c[0] == "DELETE" for c in self.broker.calls), 1)

    async def test_passive_fill_is_observed_before_balance_reconciliation(self):
        await self.start()
        order = await self.buy(maker=True)
        self.assertEqual(order["filled"], "0")
        await self.engine.reconcile_account()
        self.assertEqual(self.engine.balance("BTC"), dec(".1"))
        self.assertEqual(self.engine.orders()[0]["status"], "closed")

    async def test_external_activity_cash_changes_and_untracked_orders_fail_closed(self):
        await self.start()
        self.broker.activities.append(
            {"id": "external", "activity_type": "FILL", "order_id": "not-owned"}
        )
        with self.assertRaisesRegex(SafetyError, "External"):
            await self.engine.reconcile_account()
        self.broker.activities.clear()
        self.broker.cash += dec(".001")
        with self.assertRaisesRegex(SafetyError, "cash differs"):
            await self.engine.reconcile_account()
        self.broker.cash -= dec(".001")
        self.broker.orders["external"] = {
            "id": "external",
            "client_order_id": "external",
            "status": "new",
        }
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertTrue(self.engine.recovery_required)
        self.assertFalse(any(c[0] == "POST" for c in self.broker.calls))
        self.broker.orders.clear()

    async def test_stop_wins_during_broker_preflight(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.engine.reconcile_account

        async def delayed(**kwargs):
            entered.set()
            await release.wait()
            return await original(**kwargs)

        self.engine.reconcile_account = delayed
        start = asyncio.create_task(self.start())
        await entered.wait()
        stop = asyncio.create_task(self.engine.stop())
        await asyncio.sleep(0)
        release.set()
        with self.assertRaisesRegex(SafetyError, "canceled by Stop"):
            await start
        await stop
        self.assertFalse(self.engine.running)
        self.assertFalse(any(c[0] == "POST" for c in self.broker.calls))

    async def test_equity_capabilities_and_regular_session_scheduled_execution(self):
        for fields in (
            {"product": "margin"},
            {"strategy": "arbitrage"},
            {"recover_initial": True},
            {"pair": "alpaca:AAPL", "strategy": "htf"},
            {"pair": "alpaca:AAPL", "strategy": "scalp"},
        ):
            with self.assertRaises(SafetyError):
                await self.engine.configure({**self.engine.settings, **fields})
        await self.engine.configure({**self.engine.settings, "pair": "alpaca:AAPL"})
        await self.start()
        self.broker.open = False
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertEqual(self.engine.orders(), [])
        self.broker.open = True
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertEqual(self.engine.orders()[0]["volume"], "1")
        self.assertEqual(self.engine.balance("AAPL"), 1)
        self.assertEqual(self.broker.calls[-1][0], "GET")

    async def test_partial_crypto_fee_keeps_htf_ownership_and_original_protection(self):
        await self.engine.configure({**self.engine.settings, "strategy": "htf"})
        await self.start()
        plan = {
            "id": "owned-plan",
            "side": "buy",
            "entry_limit": "100",
            "stop": "97",
            "opened_at": time.time(),
            "deadline": time.time() + 86400,
            "exit_reason": None,
        }
        self.store.put(
            htf.key(self.engine),
            {"position": None, "entry_attempt": {"position": plan, "order_id": None}},
        )
        self.broker.fill = False
        order = await self.buy(maker=True)
        self.broker.fill_order(order["txid"], dec(".03"))
        self.broker.activities.append(
            {
                "id": "partial-fee",
                "activity_type": "CFEE",
                "status": "executed",
                "symbol": "BTCUSD",
                "net_amount": "0",
                "qty": "-.000075",
            }
        )
        self.broker.holdings["BTC/USD"] -= dec(".000075")
        await self.engine.reconcile_account()
        htf.prepare(self.engine)
        state = htf.snapshot(self.engine)
        self.assertEqual(state["position"]["stop"], plan["stop"])
        self.assertEqual(state["position"]["deadline"], plan["deadline"])
        self.assertEqual(state["entry_attempt"]["position"]["inventory_adjustment"], "-0.000075")
        await self.engine.stop()
        htf.prepare(self.engine)
        self.assertEqual(self.engine.balance("BTC"), dec(".029925"))
        self.assertEqual(
            htf.owned(self.engine, htf.snapshot(self.engine)["position"]), dec(".029925")
        )

    async def test_lost_submission_response_is_reconciled_by_durable_id_without_resubmission(self):
        await self.start()
        real_add = self.broker.client.add

        async def lost_response(params):
            await real_add(params)
            raise TimeoutError("response lost after acceptance")

        self.broker.client.add = lost_response
        with self.assertRaisesRegex(SafetyError, "uncertain"):
            await self.buy()
        self.assertEqual(self.engine.orders()[0]["status"], "uncertain")
        await self.engine.stop()
        await self.engine.reconcile()
        self.assertEqual(self.engine.orders()[0]["status"], "closed")
        self.assertEqual(self.engine.balance("BTC"), dec(".1"))
        self.assertEqual(sum(c[0] == "POST" for c in self.broker.calls), 1)

    async def test_scheduled_equity_inventory_can_be_sold_with_twap_without_a_reset(self):
        await self.engine.configure(
            {
                **self.engine.settings,
                "pair": "alpaca:AAPL",
                "dca_amount": "120",
                "order_size": "150",
                "max_exposure": "200",
            }
        )
        await self.start()
        await self.engine.tick()
        self.assertEqual(self.engine.balance("AAPL"), 2)
        await self.engine.stop()
        await self.engine.configure(
            {
                **self.engine.settings,
                "strategy": "twap",
                "twap_side": "sell",
                "twap_quantity": "2",
                "twap_limit": "49.90",
                "twap_slices": 2,
            }
        )
        await self.start()
        await self.engine.tick()
        self.assertEqual(self.engine.balance("AAPL"), 1)
        self.assertEqual([o["side"] for o in self.engine.orders()], ["buy", "sell"])
        self.assertEqual(self.engine.ledger()["initial"], "500")

    async def test_initial_exchange_is_saved_and_environment_changes_cannot_abandon_it(self):
        root = Store(":memory:")
        try:
            self.engine.run = AsyncMock()
            factories = {"kraken": lambda _: None, "alpaca": lambda _: self.engine}
            desk = ExchangeDesk({}, root, factories, default="alpaca")
            self.assertEqual(desk.active, "alpaca")
            await desk.initialize()
            self.assertEqual(root.get("active-exchange"), "alpaca")
            self.assertEqual(ExchangeDesk({}, root, factories, default="kraken").active, "alpaca")
            self.assertFalse(self.engine.running)
            self.assertFalse(self.engine.paper_armed)
        finally:
            root.close()

    async def test_legacy_kraken_state_cannot_be_bypassed_by_an_environment_default(self):
        root = Store(":memory:")
        try:
            root.put("ledger:dry-run", {"balances": {"ZUSD": "500", "LINK": "1"}})
            before = root.get("ledger:dry-run")
            desk = ExchangeDesk({}, root, {"kraken": None, "alpaca": None}, default="alpaca")
            self.assertEqual(desk.active, "kraken")
            self.assertEqual(root.get("ledger:dry-run"), before)
            self.assertIsNone(root.get("active-exchange"))
        finally:
            root.close()

    async def test_switch_preserves_isolated_state_and_remains_paused(self):
        peer_store = Store(":memory:")
        peer_broker = PaperBroker()
        peer = AlpacaEngine(peer_store, peer_broker.client, fake_jev(), lambda *_: None)
        await peer.initialize()
        app = {
            "engine": self.engine,
            "hub": type("Hub", (), {"tickers": {}, "publish": lambda *_: None})(),
            "feed_restart": asyncio.Event(),
            **{k: asyncio.Lock() for k in ("market_lock", "candle_lock", "account_lock")},
            **{
                k: {}
                for k in (
                    "market_cache",
                    "market_change_cache",
                    "spot_quote_cache",
                    "candle_cache",
                    "account_cache",
                )
            },
        }
        desk = ExchangeDesk(
            app, self.store, {"kraken": lambda _: self.engine, "alpaca": lambda _: peer}
        )
        desk.engines = {"kraken": self.engine, "alpaca": peer}
        peer.run = AsyncMock()
        before = self.engine.ledger()
        await desk.switch("alpaca", "SWITCH EXCHANGE")
        self.assertIs(app["engine"], peer)
        self.assertFalse(peer.running)
        self.assertFalse(peer.paper_armed)
        self.assertEqual(before, self.engine.ledger())
        self.assertEqual(self.store.get("active-exchange"), "alpaca")
        self.assertIsNone(peer_store.get("alpaca-account"))
        await peer.close()
        peer_store.close()


class AlpacaWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store, self.kraken_store = Store(":memory:"), Store(":memory:")
        self.broker = PaperBroker()
        self.engine = AlpacaEngine(self.store, self.broker.client, fake_jev(), lambda *_: None)
        await self.engine.initialize()
        self.kraken = RuleEngine(self.kraken_store, fake_kraken(), fake_jev(), lambda *_: None)
        await self.kraken.initialize()
        self.app = create_app(self.engine, "https://kairos.example.test")
        self.kraken_store.put("active-exchange", "alpaca")
        self.desk = ExchangeDesk(
            self.app,
            self.kraken_store,
            {"kraken": lambda _: self.kraken, "alpaca": lambda _: self.engine},
        )
        self.desk.engines = {"kraken": self.kraken, "alpaca": self.engine}
        self.app["exchanges"] = self.desk
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.headers = {"Origin": "https://kairos.example.test", "X-CSRF-Token": self.app["csrf"]}

    async def asyncTearDown(self):
        await self.client.close()
        await self.desk.close()
        self.store.close()
        self.kraken_store.close()

    async def test_provider_contract_catalog_accounts_and_estimates_do_not_change_kraken(self):
        original = schema()
        response = await self.client.get("/api/settings-schema")
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(data["exchange"], "alpaca")
        self.assertEqual(list(data["fields"]["product"]["choices"]), ["spot"])
        self.assertEqual(data["fields"]["paper_balance"]["default"], "500")
        self.assertNotIn("arbitrage", data["strategies"])
        self.assertEqual(schema(), original)
        pairs = await (await self.client.get("/api/pairs")).json()
        self.assertTrue(all(p["exchange"] == "alpaca" for p in pairs))
        stock = next(p for p in pairs if p["kind"] == "equity")
        self.assertNotIn("htf", stock["supported_strategies"])
        quotes = await (await self.client.get("/api/markets?ids=alpaca:AAPL")).json()
        self.assertEqual([p["id"] for p in quotes["markets"]], ["alpaca:AAPL"])
        self.assertIsNone(quotes["markets"][0]["change_pct"])
        account = await (await self.client.get("/api/accounts/alpaca-account")).json()
        self.assertEqual(account["source"], "alpaca-account")
        state_response = await self.client.get("/api/state")
        text = await state_response.text()
        self.assertNotIn("paper-test-key", text)
        self.assertNotIn("paper-test-secret", text)
        self.assertTrue((await state_response.json())["fees"]["estimated"])

    async def test_market_volume_refresh_requires_explicit_watchlist_ids(self):
        refresh = AsyncMock(return_value=[])
        self.app["retail"].refresh_volumes = refresh
        response = await self.client.get("/api/markets")
        self.assertEqual(response.status, 200)
        refresh.assert_awaited_once_with(set())
        refresh.reset_mock()
        response = await self.client.get("/api/markets?volume_ids=alpaca:AAPL")
        self.assertEqual(response.status, 409)
        refresh.assert_not_awaited()
        response = await self.client.get("/api/markets?ids=alpaca:AAPL&volume_ids=alpaca:AAPL")
        self.assertEqual(response.status, 200)
        refresh.assert_awaited_once_with({"alpaca:AAPL"})
        self.assertEqual(len((await response.json())["markets"]), 1)
        self.assertFalse(self.broker.orders)
        self.assertFalse(self.engine.running)

    async def test_initial_funding_ack_requires_csrf_and_confirmation_without_start(self):
        from tests.test_alpaca_funding import CONFIRM, FUNDING

        await self.engine.reconcile_account(adopt=True)
        self.broker.activities = [copy.deepcopy(FUNDING)]
        payload = {"initial_funding_id": FUNDING["id"], "confirmation": CONFIRM}
        response = await self.client.post("/api/reconcile", json=payload)
        self.assertEqual(response.status, 403)
        response = await self.client.post(
            "/api/reconcile", json={"initial_funding_id": FUNDING["id"]}, headers=self.headers
        )
        self.assertEqual(response.status, 409)
        self.assertFalse(self.store.get("alpaca-account")["baseline_activities"])
        response = await self.client.post("/api/reconcile", json=payload, headers=self.headers)
        self.assertEqual(response.status, 200)
        state = await response.json()
        self.assertFalse(state["running"] or state["paper_armed"])
        self.assertTrue(all(method == "GET" for method, _, _ in self.broker.calls))
        view = await (await self.client.get("/api/accounts/alpaca-activities")).json()
        self.assertIn("description", view["columns"])
        self.assertIn(FUNDING["description"], view["rows"][0])
        self.app["engine"] = self.kraken
        response = await self.client.post("/api/reconcile", json=payload, headers=self.headers)
        self.assertEqual(response.status, 409)

    async def test_switch_requires_csrf_stopped_flat_account_and_never_resumes(self):
        before = self.engine.ledger()
        payload = {"exchange": "kraken", "confirmation": "SWITCH EXCHANGE"}
        response = await self.client.post("/api/exchange", json=payload)
        self.assertEqual(response.status, 403)
        self.engine.running = True
        response = await self.client.post("/api/exchange", json=payload, headers=self.headers)
        self.assertEqual(response.status, 409)
        self.engine.running = False
        self.kraken.run = AsyncMock()
        response = await self.client.post("/api/exchange", json=payload, headers=self.headers)
        self.assertEqual(response.status, 200)
        state = await response.json()
        self.assertEqual(state["exchange"], "kraken")
        self.assertEqual(state["mode"], "dry-run")
        self.assertFalse(state["running"])
        self.assertEqual(before, self.engine.ledger())
        self.assertEqual(self.kraken_store.get("active-exchange"), "kraken")
        self.assertEqual(
            (await (await self.client.get("/api/settings-schema")).json())["exchange"], "kraken"
        )

    async def test_switch_lock_blocks_starts_but_stop_still_latches(self):
        async with self.desk.lock:
            response = await self.client.post(
                "/api/start", json={"confirmation": "START ALPACA PAPER"}, headers=self.headers
            )
            self.assertEqual(response.status, 409)
            response = await self.client.post("/api/stop", json={}, headers=self.headers)
            self.assertEqual(response.status, 200)
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.orders(), [])
