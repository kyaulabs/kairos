"""Initial paper cash journals are not trades or permission to adopt later funding."""

import asyncio
import copy
import sqlite3
import unittest
from unittest.mock import patch

from kairos.alpaca_engine import AlpacaEngine
from kairos.domain import SafetyError, dec
from kairos.store import Store
from tests.helpers import fake_jev
from tests.test_alpaca import PaperBroker

CONFIRM = "ACKNOWLEDGE INITIAL PAPER FUNDING"
FUNDING = {
    "id": "initial-cash-journal",
    "activity_type": "JNLC",
    "date": "2026-10-01",
    "status": "executed",
    "net_amount": "10000",
    "description": "Journal cash between accounts",
}


class InitialFundingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.broker = PaperBroker()
        self.engine = AlpacaEngine(self.store, self.broker.client, fake_jev(), lambda *_: None)
        await self.engine.initialize()
        await self.engine.configure(
            {**self.engine.settings, "strategy": "dca", "dca_amount": "10", "dca_count": 1}
        )

    async def asyncTearDown(self):
        await self.engine.close()
        self.store.close()

    def state(self):
        return dict(self.store.db.execute("SELECT key,value FROM state"))

    async def delayed(self):
        await self.engine.reconcile_account(adopt=True)
        self.broker.activities = [copy.deepcopy(FUNDING)]

    async def acknowledge(self):
        await self.engine.reconcile_initial_funding(FUNDING["id"], CONFIRM)

    async def test_first_start_accepts_initial_journal_but_preserves_correction_check(self):
        self.broker.activities = [copy.deepcopy(FUNDING)]
        await self.engine.start(confirmation="START ALPACA PAPER")
        self.assertTrue(self.engine.running)
        self.assertEqual(self.engine.balance("USD"), 500)
        self.assertEqual(
            self.store.get("alpaca-account")["baseline_activities"], {FUNDING["id"]: FUNDING}
        )
        self.assertTrue(all(method == "GET" for method, _, _ in self.broker.calls))
        self.broker.activities[0]["description"] = "Corrected journal"
        with self.assertRaisesRegex(SafetyError, "baseline activity was corrected"):
            await self.engine.reconcile_account()

    async def test_delayed_journal_needs_explicit_ack_and_only_baseline_changes(self):
        await self.delayed()
        before = self.state()
        with self.assertRaisesRegex(SafetyError, "cash journal"):
            await self.engine.reconcile_account()
        with self.assertRaisesRegex(SafetyError, "confirmation"):
            await self.engine.reconcile_initial_funding(FUNDING["id"], None)
        self.assertEqual(self.state(), before)
        await self.acknowledge()
        after = self.state()
        self.assertEqual(
            {k: v for k, v in after.items() if k != "alpaca-account"},
            {k: v for k, v in before.items() if k != "alpaca-account"},
        )
        self.assertEqual(
            self.store.get("alpaca-account")["baseline_activities"], {FUNDING["id"]: FUNDING}
        )
        self.assertFalse(self.engine.running or self.engine.paper_armed)
        self.assertEqual(self.store.history()[-1]["kind"], "account-reconciliation")
        await self.engine.reconcile_account()
        self.assertFalse(self.engine.orders())
        self.assertTrue(all(method == "GET" for method, _, _ in self.broker.calls))
        with self.assertRaisesRegex(SafetyError, "empty saved baseline"):
            await self.acknowledge()
        self.broker.activities.append({**FUNDING, "id": "later-journal"})
        with self.assertRaisesRegex(SafetyError, "cash journal"):
            await self.engine.reconcile_account()

    async def test_initial_journal_shape_is_not_a_blanket_cash_allowlist(self):
        for change in (
            {"net_amount": "9999"},
            {"net_amount": "-10000"},
            {"net_amount": "0"},
            {"status": "pending"},
            {"status": "correct"},
            {"status": "canceled"},
            {"currency": "EUR"},
            {"symbol": "AAPL"},
            {"qty": "1"},
            {"activity_type": "JNLS"},
        ):
            with self.subTest(change=change):
                self.broker.activities = [{**FUNDING, **change}]
                with self.assertRaises(SafetyError):
                    await self.engine.reconcile_account(adopt=True)
                self.assertIsNone(self.store.get("alpaca-account"))
        self.broker.activities = [FUNDING, {**FUNDING, "id": "second"}]
        with self.assertRaises(SafetyError):
            await self.engine.reconcile_account(adopt=True)
        self.assertIsNone(self.store.get("alpaca-account"))

    async def test_ack_refuses_cash_identity_positions_orders_and_running_changes(self):
        await self.delayed()
        before = self.state()
        cases = [
            (self.broker, "cash", dec(11000)),
            (self.broker, "account_id", "another-account"),
            (self.broker, "holdings", {"BTC/USD": dec(1)}),
            (self.broker, "orders", {"external": {"status": "canceled"}}),
            (self.broker, "activities", [{**FUNDING, "net_amount": "9000"}]),
            (self.broker, "activities", [FUNDING, {**FUNDING, "id": "extra"}]),
            (self.engine, "running", True),
            (self.engine, "recovery_required", True),
        ]
        for obj, attr, value in cases:
            with self.subTest(attr=attr), patch.object(obj, attr, value):
                with self.assertRaises(SafetyError):
                    await self.acknowledge()
                self.assertEqual(self.state(), before)
        with self.assertRaises(SafetyError):
            await self.engine.reconcile_initial_funding("wrong-id", CONFIRM)
        self.assertEqual(self.state(), before)

    async def test_changed_second_read_and_stop_during_read_cannot_acknowledge(self):
        await self.delayed()
        before = self.state()
        original = self.broker.client.request.side_effect
        reads = 0
        stop = None
        stopping = False

        async def request(method, path, **kwargs):
            nonlocal reads, stop
            if path == "/v2/account/activities":
                reads += 1
                if reads == 2:
                    if stopping:
                        stop = asyncio.create_task(self.engine.stop())
                        await asyncio.sleep(0)
                    else:
                        self.broker.activities[0]["description"] = "Changed during reads"
            return await original(method, path, **kwargs)

        self.broker.client.request.side_effect = request
        for stop_during_read in (False, True):
            stopping = stop_during_read
            reads = 0
            self.broker.activities = [copy.deepcopy(FUNDING)]
            with self.assertRaises(SafetyError):
                await self.acknowledge()
            if stop:
                await stop
            self.assertEqual(self.state(), before)
            self.assertFalse(self.engine.running)

    async def test_failed_audit_write_rolls_back_baseline(self):
        await self.delayed()
        before = self.state()
        with patch.object(self.store, "event", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.acknowledge()
        self.assertEqual(self.state(), before)
