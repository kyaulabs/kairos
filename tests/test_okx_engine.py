import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from kairos.domain import SafetyError, dec
from kairos.okx import OKX
from kairos.okx_engine import OKXEngine, fingerprint
from kairos.store import Store
from tests.test_okx import OKXResponse, instrument


class VenueResponse(OKXResponse):
    async def __aexit__(self, *args):
        self.venue.client.next_request = 0  # Fixtures have no network admission delay.


class Venue:
    """Deterministic native REST evidence, not a production execution/simulation path."""

    def __init__(self):
        self.orders, self.fills, self.calls = {}, [], []
        self.cash = {"USDC": dec(10000), "BTC": dec(1), "USD": dec(10)}
        self.client = OKX(
            self,
            "offline-fixture-key",
            "offline-fixture-secret",
            "offline-fixture-passphrase",
            environment="demo",
            allow_writes=True,
        )
        self.uid = "123456"
        self.lose_ack = False
        self.fraction = dec(1)
        self.report_lag = False
        self.lag_reads = 0
        self.defer = False
        self.fill_on_cancel = False

    def fill(self, oid, quantity, stamp):
        order = self.orders[oid]
        price, quote = dec(order["px"]), order["tradeQuoteCcy"]
        order.update(
            accFillSz=str(quantity),
            state="filled" if quantity == dec(order["sz"]) else "canceled",
            uTime=stamp,
        )
        fee_currency = "BTC" if order["side"] == "buy" else quote
        fee = -quantity * (1 if order["side"] == "buy" else price) * dec(".001")
        self.fills.append(
            {
                **order,
                "fillSz": str(quantity),
                "fillPx": str(price),
                "fee": str(fee),
                "feeCcy": fee_currency,
                "execType": "T",
                "billId": str(len(self.fills) + 200),
                "tradeId": str(len(self.fills) + 300),
                "fillTime": stamp,
                "ts": stamp,
            }
        )
        direction = 1 if order["side"] == "buy" else -1
        self.cash["BTC"] += direction * quantity
        self.cash[quote] -= direction * quantity * price
        self.cash[fee_currency] += fee

    def request(self, method, url, **kwargs):
        self.calls.append((method, str(url), kwargs))
        path = urlsplit(str(url)).path
        query = {k: v[0] for k, v in parse_qs(urlsplit(str(url)).query).items()}
        body = json.loads(kwargs["data"]) if kwargs.get("data") else None
        code = "0"
        stamp = str(int(time.time() * 1000))
        if path.endswith("/instruments"):
            rows = [instrument()]
        elif path.endswith("/public/time"):
            rows = [{"ts": stamp}]
        elif path.endswith("/price-limit"):
            rows = [
                {
                    "instId": "BTC-USD",
                    "instType": "SPOT",
                    "enabled": True,
                    "buyLmt": "60000",
                    "sellLmt": "40000",
                    "ts": stamp,
                }
            ]
        elif path.endswith("/config"):
            rows = [
                {
                    "uid": self.uid,
                    "acctLv": "2",
                    "autoLoan": False,
                    "enableSpotBorrow": False,
                    "perm": "read_only,trade",
                    "feeType": "0",
                }
            ]
        elif path.endswith("/balance"):
            cash = (
                {"USDC": dec(10000), "BTC": dec(1), "USD": dec(10)}
                if self.report_lag or self.fills and self.lag_reads > 0
                else self.cash
            )
            if self.fills:
                self.lag_reads = max(0, self.lag_reads - 1)
            rows = [
                {
                    "uTime": stamp,
                    "details": [
                        {"ccy": k, "cashBal": str(v), "availBal": str(v), "frozenBal": "0"}
                        for k, v in cash.items()
                    ],
                }
            ]
        elif path.endswith("/orders-pending"):
            rows = [r for r in self.orders.values() if r["state"] == "live"]
        elif path.endswith("/orders-history"):
            rows = list(self.orders.values())
        elif path.endswith(("/bills-archive", "/bills")):
            rows = [{"billId": f["billId"], "ordId": f["ordId"], "type": "2"} for f in self.fills]
        elif path.endswith(("/fills-history", "/fills")):
            rows = [f for f in self.fills if not query.get("ordId") or f["ordId"] == query["ordId"]]
        elif path.endswith("/trade-fee"):
            rows = [
                {
                    "instType": "SPOT",
                    "feeGroup": [{"groupId": "4", "maker": "-.001", "taker": "-.001"}],
                }
            ]
        elif path.endswith("/order") and method == "POST":
            oid = str(len(self.orders) + 100)
            quantity = dec(0) if self.defer else dec(body["sz"]) * self.fraction
            self.orders[oid] = {
                **body,
                "instType": "SPOT",
                "ordId": oid,
                "accFillSz": str(quantity),
                "state": "live" if self.defer else "filled" if self.fraction == 1 else "canceled",
                "uTime": stamp,
            }
            if quantity:
                self.fill(oid, quantity, stamp)
            if self.lose_ack:
                self.client.next_request = 0
                raise TimeoutError
            rows = [{"ordId": oid, "clOrdId": body["clOrdId"], "sCode": "0"}]
        elif path.endswith("/cancel-order"):
            oid = body["ordId"]
            if self.fill_on_cancel:
                self.fill(oid, dec(self.orders[oid]["sz"]), stamp)
            else:
                self.orders[oid].update(state="canceled", uTime=stamp)
            rows = [{"ordId": oid, "sCode": "51400" if self.fill_on_cancel else "0"}]
        elif path.endswith("/order"):
            rows = [
                r
                for r in self.orders.values()
                if (query.get("ordId") and r["ordId"] == query["ordId"])
                or (query.get("clOrdId") and r["clOrdId"] == query["clOrdId"])
            ]
            if not rows:
                code = "51603"
        else:
            raise AssertionError(f"Unimplemented fixture endpoint: {method} {path}")
        response = VenueResponse({"code": code, "data": rows})
        response.venue = self
        return response


class OKXEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.venue = Venue()
        self.client = self.venue.client
        await self.client.catalog()
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.engine = OKXEngine(self.store, self.client, None, lambda *_: None)
        self.pair = self.client.resolve("okx-demo:BTC-USD:USDC")
        self.engine.settings.update(pair=self.pair.id, quote="USDC")
        # Use native books5 fixtures; no external WebSocket connection in CI.
        self.client.session = None
        await self.client.market_data.configure([self.pair])
        self.client.session = self.venue
        self.client.market_data.task = asyncio.create_task(asyncio.sleep(1000))
        self.book()
        self.addAsyncCleanup(self.client.market_data.close)
        await self.engine.refresh_fees(force=True)
        proof = await self.engine.probe()
        self.engine.bind(proof, self.pair, "500")
        self.engine.authorization = {
            "id": "finite-fixture-run",
            "kind": "execution_cycle",
            "environment": "demo",
            "pair": self.pair.id,
            "settings_id": self.engine.settings_id(),
            "generation": 0,
            "deadline": time.time() + 60,
            "budget": "25.025",
            "fee_bps": "10",
            "market_rules": fingerprint(self.client.instruments[self.pair.id]),
            "buy_ceiling": "50000",
            "sell_floor": "49900",
            "attempts": {"buy": 1, "sell": 1},
        }
        self.engine.authorization_monotonic_deadline = time.monotonic() + 60
        self.engine.running = self.engine.armed = True
        self.engine.execution_purpose = "execution_cycle"

    def book(self):
        feed = self.client.market_data
        feed.message({"event": "subscribe", "arg": feed.argument()})
        feed.message(
            {
                "arg": feed.argument(),
                "action": "snapshot",
                "data": [
                    {
                        "ts": str(int(time.time() * 1000)),
                        "bids": [["49999.9", "1", "0", "1"]],
                        "asks": [["50000", "1", "0", "1"]],
                    }
                ],
            }
        )
        return feed.books[self.pair.id]

    async def test_normal_engine_path_uses_native_fills_fees_owned_inventory_and_no_model(self):
        buy = await self.engine.place(self.pair, "buy", dec(".0005"), dec(50000), self.book())
        self.assertEqual(buy["status"], "closed")
        self.assertEqual(len(buy["client_id"]), 32)
        self.assertEqual(buy["payload"]["tradeQuoteCcy"], "USDC")
        self.assertEqual(self.engine.balance("BTC"), dec(".0004995"))
        with self.assertRaises(SafetyError):
            await self.engine.place(self.pair, "sell", dec(".0005"), dec("49999.9"), self.book())
        sell = await self.engine.place(
            self.pair, "sell", self.engine.balance("BTC"), dec("49999.9"), self.book()
        )
        self.assertEqual(sell["status"], "closed")
        self.assertEqual(self.engine.balance("BTC"), 0)
        self.assertEqual(self.engine.balance("USDC"), dec("499.94997509995"))
        self.assertEqual(self.venue.cash["BTC"], 1)
        self.assertEqual(self.venue.cash["USD"], 10)
        fills = [row["data"] for row in self.store.history() if row["kind"] == "fill"]
        self.assertEqual([row["side"] for row in fills], ["buy", "sell"])
        self.assertEqual(
            [row["source_time"] for row in fills],
            [int(row["fillTime"]) for row in self.venue.fills],
        )
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 2)
        with self.assertRaises(SafetyError):
            await self.engine.place(self.pair, "buy", dec(".0001"), dec(50000), self.book())

    async def test_lost_acceptance_is_durable_and_read_reconciliation_does_not_resubmit(self):
        self.venue.lose_ack = True
        with self.assertRaisesRegex(SafetyError, "uncertain"):
            await self.engine.place(self.pair, "buy", dec(".0005"), dec(50000), self.book())
        self.assertEqual(self.store.orders()[0]["status"], "uncertain")
        self.assertEqual(self.store.orders()[0]["filled"], "0")
        self.engine.running = self.engine.armed = False
        self.engine.authorization = None
        self.store.put("settings", self.engine.settings)
        self.store.put(
            "okx-operation", {"id": "interrupted-entry", "status": "running", "phase": "entry"}
        )
        restarted = OKXEngine(self.store, self.client, None, lambda *_: None)
        await restarted.initialize()
        self.assertIsNone(restarted.last_error)
        self.assertEqual(self.store.orders()[0]["status"], "closed")
        self.assertEqual(restarted.balance("BTC"), dec(".0004995"))
        self.assertEqual(self.store.get("okx-operation")["status"], "interrupted")
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 1)
        self.assertFalse(restarted.armed)
        self.assertFalse(restarted.running)
        with self.assertRaises(SafetyError):
            await restarted.place(self.pair, "buy", dec(".0001"), dec(50000), self.book())
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 1)

    async def test_stop_reconciles_fill_racing_cancel_without_repeat_or_liquidation(self):
        await self.engine.settle()
        self.venue.defer = True
        with (
            patch.object(self.engine, "settle", AsyncMock()),
            patch.object(self.engine, "confirm_spot", AsyncMock()),
        ):
            order = await self.engine.place(self.pair, "buy", dec(".0005"), dec(50000), self.book())
        self.assertEqual(order["status"], "open")
        self.venue.fill_on_cancel = True
        await self.engine.stop()
        order = self.store.orders()[0]
        self.assertEqual(order["status"], "closed")
        self.assertEqual(order["cancel_state"], "rejected")
        self.assertEqual(self.engine.balance("BTC"), dec(".0004995"))
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.armed)
        await self.engine.stop()
        writes = [c for c in self.venue.calls if c[0] == "POST"]
        self.assertEqual(len(writes), 2)
        self.assertTrue(writes[1][1].endswith("/cancel-order"))

    async def test_temporary_reporting_lag_resolves_without_repair_or_duplicate_fee(self):
        self.venue.lag_reads = 3
        order = await self.engine.place(self.pair, "buy", dec(".0005"), dec(50000), self.book())
        self.assertEqual(order["status"], "closed")
        self.assertEqual(self.engine.balance("BTC"), dec(".0004995"))
        self.assertEqual(self.engine.ledger()["fees"], {"BTC": "5E-7"})
        self.assertEqual(self.store.get("okx-reconciliation")["status"], "matched")
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 1)

    async def test_restart_during_unresolved_exit_reads_fills_without_rearming(self):
        await self.engine.place(self.pair, "buy", dec(".0005"), dec(50000), self.book())
        self.venue.lose_ack = True
        with self.assertRaisesRegex(SafetyError, "uncertain"):
            await self.engine.place(
                self.pair, "sell", self.engine.balance("BTC"), dec("49999.9"), self.book()
            )
        self.store.put("settings", self.engine.settings)
        self.store.put(
            "okx-operation", {"id": "interrupted-exit", "status": "running", "phase": "exit"}
        )
        restarted = OKXEngine(self.store, self.client, None, lambda *_: None)
        await restarted.initialize()
        self.assertIsNone(restarted.last_error)
        self.assertFalse(restarted.running)
        self.assertFalse(restarted.armed)
        self.assertEqual(restarted.balance("BTC"), 0)
        self.assertEqual(self.store.get("okx-operation")["status"], "interrupted")
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 2)

    async def test_rotated_key_same_account_preserves_binding_and_missing_keys_remain_inert(self):
        binding = self.store.get("okx-account")
        self.client.key, self.client.secret = "rotated-offline-key", "rotated-offline-secret"
        await self.engine.settle()
        self.assertEqual(self.store.get("okx-account"), binding)
        self.client.key = ""
        calls = len(self.venue.calls)
        restarted = OKXEngine(self.store, self.client, None, lambda *_: None)
        await restarted.initialize()
        self.assertIn("credentials missing", restarted.last_error)
        self.assertEqual(len(self.venue.calls), calls)
        self.assertEqual(self.store.get("okx-account"), binding)
        self.assertFalse(restarted.running)
        self.assertFalse(restarted.armed)

    async def test_identity_change_and_external_activity_do_not_rebind_or_correct_balances(self):
        self.venue.uid = "999999"
        with self.assertRaisesRegex(SafetyError, "binding"):
            await self.engine.reconcile_account()
        self.assertEqual(self.store.get("okx-account")["uid"], "123456")
        self.venue.uid = "123456"
        self.venue.cash["USDC"] += 1
        with self.assertRaisesRegex(SafetyError, "balance differences"):
            await self.engine.reconcile_account()
        self.assertEqual(self.engine.balance("USDC"), 500)
        self.assertTrue(self.engine.recovery_required)
        now = self.engine.clock()
        self.engine.clock = lambda: now + 46
        with self.assertRaisesRegex(SafetyError, "exceeded 45s"):
            await self.engine.reconcile_account()
        self.assertEqual(self.engine.balance("USDC"), 500)
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])
