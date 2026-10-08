import copy
import time
import unittest
from unittest.mock import AsyncMock, patch

from kairos import okx_account as native
from kairos.domain import SafetyError, dec
from kairos.okx import PendingOKX
from kairos.okx_engine import OKXEngine
from kairos.okx_reconciliation import bill_evidence, demo_summary
from kairos.store import Store
from tests.test_okx import instrument
from tests.test_okx_account import fill, observation, order, snapshot
from tests.test_okx_engine import Venue


def incident(store):
    """Actual monetary regression; invented identities, no credentials/network/writes."""
    binding = {
        "uid": "123456",
        "venue": "okx-us",
        "environment": "demo",
        "baseline": {"USDT": "5000000", "BTC": "1", "USD": "10"},
        "quote": "USDT",
        "allocation": "500",
        "bills": [],
        "history": {},
        "bound_at": time.time() - 3600,
    }
    store.put("okx-account", binding)
    store.put(
        "ledger:paper",
        {"balances": {"USDT": "500"}, "initial": "500", "fees": {}, "account_delta": {}},
    )
    orders, executions, history = [], [], []
    stamp = int((time.time() - 20) * 1000)
    for side, quantity, price, fee, asset, offset in (
        ("buy", "0.00029899", "83288.4", "-0.000001046465", "BTC", 0),
        ("sell", "0.00029794", "83273.1", "-0.086836355949", "USDT", 1000),
    ):
        intent = order(side, ("a" if side == "buy" else "b") * 32)
        intent.update(
            instrument="BTC-USDT",
            pair="okx-demo:BTC-USDT:USDT",
            quote="USDT",
            volume=quantity,
            price=price,
            payload={"sz": quantity, "px": price},
        )
        row = fill(
            intent,
            instId="BTC-USDT",
            tradeQuoteCcy="USDT",
            fee=fee,
            feeCcy=asset,
            fillTime=str(stamp + offset),
            ts=str(stamp + offset),
        )
        result, _ = native.apply_executions(store, intent, [row], binding)
        observed = observation(
            intent, instId="BTC-USDT", tradeQuoteCcy="USDT", uTime=str(stamp + offset)
        )
        result = native.order_observation(result, observed)
        store.save_order(result)
        orders.append(result)
        executions.append(row)
        history.append(observed)
    bills = []
    balance = {k: dec(v) for k, v in binding["baseline"].items()}
    for intent in orders:
        execution = intent["executions"][0]
        for asset in ("BTC", "USDT"):
            quantity = dec(execution["quantity"] if asset == "BTC" else execution["cost"])
            direction = (1 if intent["side"] == "buy" else -1) * (1 if asset == "BTC" else -1)
            fee = dec(execution["fee_signed"]) if asset == execution["fee_currency"] else dec(0)
            change = quantity * direction + fee
            balance[asset] += change
            bills.append(
                {
                    "billId": execution["bill_id"]
                    if asset == execution["fee_currency"]
                    else str(int(execution["bill_id"]) + 200),
                    "ordId": intent["txid"],
                    "tradeId": execution["trade_id"],
                    "instType": "SPOT",
                    "instId": "BTC-USDT",
                    "type": "2",
                    "subType": "1" if direction > 0 else "2",
                    "ccy": asset,
                    "sz": str(quantity),
                    "px": execution["price"],
                    "fee": str(fee),
                    "balChg": str(change),
                    "bal": str(balance[asset]),
                    "ts": str(execution["record_time"]),
                }
            )
    account = {**snapshot(**balance), "timestamp": stamp + 2000}
    account["assets"]["USDT"].update(total="4999999.8211523425", available="4999999.8211523425")
    return binding, orders, bills, account, executions, history


class BillReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.binding, self.orders, self.bills, self.balance, _, _ = incident(self.store)
        self.ledger = self.store.get("ledger:paper")

    def evidence(self):
        return bill_evidence(
            self.binding,
            self.ledger,
            self.orders,
            self.bills,
            self.balance,
            native.mismatch(self.binding, self.ledger, self.balance),
        )

    def test_exact_money_and_dust_preserved_with_explicit_representation_proof(self):
        before = copy.deepcopy((self.binding, self.ledger, self.orders, self.bills, self.balance))
        proof = self.evidence()
        self.assertEqual(
            proof["precision_differences"]["USDT"],
            {
                "expected": "4999999.821152342051",
                "observed": "4999999.8211523425",
                "difference": "4.49E-10",
            },
        )
        self.assertEqual(len(proof["bill_checkpoints"]), 4)
        self.assertEqual(proof["ledger_adjustment"], "0")
        self.assertEqual(self.ledger["balances"], {"USDT": "499.821152342051", "BTC": "3.535E-9"})
        self.assertEqual(before, (self.binding, self.ledger, self.orders, self.bills, self.balance))

    def test_no_epsilon_live_fallback_or_unrelated_currency_allowance(self):
        for value in (
            "4999999.8211523420511",
            "4999999.8211523424",
            "4999999.8211523426",
            "5000000",
            "4999999.82",
        ):
            with self.subTest(value=value):
                self.balance["assets"]["USDT"]["total"] = value
                self.assertIsNone(self.evidence())
        self.balance["assets"]["USDT"]["total"] = "4999999.8211523425"
        self.binding["environment"] = "live"
        self.assertIsNone(self.evidence())
        self.binding["environment"] = "demo"
        self.binding["baseline"]["USD"] = "0.100000000000000001"
        self.balance["assets"]["USD"].update(total="0.1", available="0.1")
        with self.assertRaisesRegex(SafetyError, "no owned bill"):
            self.evidence()

    def test_missing_duplicate_revised_foreign_wrong_currency_and_checkpoint_bills_block(self):
        pristine = copy.deepcopy(self.bills)
        for change in (
            lambda b: b.pop(),
            lambda b: b.append(copy.deepcopy(b[0])),
            lambda b: b[0].update(ordId="999"),
            lambda b: b[0].update(tradeId="999"),
            lambda b: b[0].update(instId="ETH-USDT"),
            lambda b: b[0].update(instType="SWAP"),
            lambda b: b[0].update(type="1"),
            lambda b: b[0].update(ccy="USD"),
            lambda b: b[0].update(fee="0"),
            lambda b: b[0].update(balChg="0.000297943536"),
            lambda b: b[0].update(bal="1.000297943536"),
            lambda b: b[0].update(sz="0.000299"),
            lambda b: b[0].update(px="83288.5"),
            lambda b: b[0].update(ts="1"),
            lambda b: b[0].update(bal=None),
            lambda b: b[0].update(fee=0),
            lambda b: b[0].update(subType="2"),
            lambda b: b[0].update(billId="999"),
        ):
            self.bills = copy.deepcopy(pristine)
            change(self.bills)
            with self.subTest(bills=self.bills), self.assertRaises(SafetyError):
                self.evidence()
        self.bills = pristine
        for field in ("account_delta", "fees"):
            saved = copy.deepcopy(self.ledger[field])
            self.ledger[field]["BTC"] = "0"
            if field == "account_delta":
                self.assertIsNone(
                    self.evidence()
                )  # It no longer matches the summary representation.
            else:
                with self.assertRaisesRegex(SafetyError, "totals differ"):
                    self.evidence()
            self.ledger[field] = saved
        self.orders[0]["fee_reported"] = False
        with self.assertRaisesRegex(SafetyError, "unconfirmed"):
            self.evidence()

    def test_frozen_unavailable_and_unresolved_evidence_cannot_use_precision_path(self):
        row = self.balance["assets"]["USDT"]
        row["frozen"] = "1"
        self.assertIsNone(self.evidence())
        row["frozen"], row["available"] = "0", "4999998.8211523425"
        self.assertIsNone(self.evidence())
        row["available"] = row["total"]
        self.orders[0]["status"] = "uncertain"
        with self.assertRaisesRegex(SafetyError, "unresolved original order"):
            self.evidence()
        self.orders[0]["status"] = "closed"
        self.balance["timestamp"] = 1
        with self.assertRaisesRegex(SafetyError, "follows the balance snapshot"):
            self.evidence()

    def test_representation_model_is_exact_and_rejects_non_normal_cash(self):
        for source, reported in (
            ("4999999.821152342051", "4999999.8211523425"),
            ("4999999.8211523420510000001", "4999999.8211523425"),
            ("0.100000000000000001", "0.1"),
            ("1.000000003535", "1.000000003535"),
            ("9007199254740993", "9007199254740992"),
            ("9007199254740995", "9007199254740996"),
        ):
            self.assertEqual(demo_summary(dec(source)), dec(reported))
        for value in ("0", "-1", "1e-309", "1e309"):
            self.assertIsNone(demo_summary(dec(value)))


class EngineBillReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        binding, self.orders, self.bills, self.balance, fills, history = incident(self.store)
        self.venue = Venue(
            instrument(instId="BTC-USDT", quoteCcy="USDT", tradeQuoteCcyList=["USDT"])
        )
        self.client = self.venue.client
        self.venue.orders = {r["ordId"]: r for r in history}
        self.venue.fills = fills
        self.venue.cash = native.totals(self.balance)
        await self.client.catalog()
        self.engine = OKXEngine(self.store, self.client, None, lambda *_: None)
        self.engine.settings.update(pair="okx-demo:BTC-USDT:USDT", quote="USDT")
        self.engine.running = self.engine.armed = False
        self.engine.recovery_required = True
        pages = self.client.pages

        async def evidence_pages(path, **kwargs):
            if path.endswith(("/bills", "/bills-archive")):
                return copy.deepcopy(self.bills)
            return await pages(path, **kwargs)

        self.client.pages = evidence_pages
        self.operation = {
            "id": "consumed-fixture",
            "claimed": True,
            "submitted": True,
            "status": "RECONCILIATION_PENDING",
            "closing_ledger": self.engine.ledger(),
        }
        self.store.put("okx-operation", self.operation)
        self.store.put("okx-operation:" + self.operation["id"], self.operation)
        self.since = time.time() - 1000
        self.store.put(
            "okx-reconciliation",
            {
                "differences": native.mismatch(binding, self.engine.ledger(), self.balance),
                "since": self.since,
                "unresolved_orders": [],
            },
        )

    def unchanged(self, ledger, orders):
        self.assertEqual(self.engine.ledger(), ledger)
        self.assertEqual(self.engine.orders(), orders)
        self.assertEqual(self.store.get("okx-operation"), self.operation)
        self.assertEqual(self.store.get("okx-operation:" + self.operation["id"]), self.operation)
        self.assertFalse(self.engine.running or self.engine.armed)
        self.assertIsNone(self.engine.authorization)
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])

    async def test_restart_and_read_only_reconcile_explain_original_run_without_rewriting_or_replaying(
        self,
    ):
        ledger, orders = self.engine.ledger(), self.engine.orders()
        with patch.object(self.client.market_data, "configure", new=AsyncMock()):
            await self.engine.initialize()
        self.assertFalse(self.engine.recovery_required)
        self.assertIsNone(self.engine.last_error)
        await self.engine.reconcile()
        evidence = self.store.get("okx-reconciliation")
        self.assertEqual(evidence["status"], "matched")
        self.assertEqual(evidence["differences"], {})
        self.assertEqual(evidence["bill_proof"]["original_pending_since"], self.since)
        self.assertEqual(len(evidence["bill_proof"]["read_received_at"]), 2)
        self.assertEqual(
            self.engine.account_snapshot["assets"]["USDT"]["total"], "4999999.8211523425"
        )
        self.unchanged(ledger, orders)

    async def test_changed_second_bill_balance_history_or_identity_never_approves(self):
        original = self.engine.read_account
        for field in ("bills", "balance", "history", "config", "pending"):
            calls = 0

            async def changed(field=field):
                nonlocal calls
                proof = await original()
                calls += 1
                if calls == 2:
                    if field == "bills":
                        proof["bills"][0]["balChg"] = "0"
                    if field == "balance":
                        proof["balance"]["assets"]["USDT"]["total"] = "5000000"
                    if field == "history":
                        proof["history"][0]["fee"] = "1"
                    if field == "config":
                        proof["config"]["uid"] = "999"
                    if field == "pending":
                        proof["pending"] = [{"ordId": "999"}]
                return proof

            with self.subTest(field=field), patch.object(self.engine, "read_account", new=changed):
                with self.assertRaisesRegex(PendingOKX, "changed between"):
                    await self.engine.reconcile_account()
            self.assertTrue(self.engine.recovery_required)
            self.assertNotEqual(self.store.get("okx-reconciliation").get("status"), "matched")

    async def test_external_activity_and_missing_bills_remain_blocking(self):
        ledger, orders = self.engine.ledger(), self.engine.orders()
        self.bills.append({"billId": "999", "type": "1", "ordId": ""})
        with self.assertRaisesRegex(SafetyError, "External OKX bill"):
            await self.engine.reconcile_account()
        self.bills.pop()
        self.bills.pop()
        with self.assertRaisesRegex(SafetyError, "missing currency leg"):
            await self.engine.reconcile_account()
        self.unchanged(ledger, orders)
