import asyncio
import copy
import time
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from kairos import htf
from kairos.clients import ExchangeRejected
from kairos.domain import SafetyError, dec
from kairos.qualification_exit import CONFIRMATION, KEY, recover
from tests import test_alpaca as paper


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = paper.AlpacaEngineTests()
        await self.case.asyncSetUp()
        self.addAsyncCleanup(self.case.asyncTearDown)
        self.e, self.broker = self.case.engine, self.case.broker
        pair = self.e.resolve("alpaca:BTC/USD")
        self.e.kraken.pairs[pair.id] = replace(pair, lot=dec("1e-9"))
        await self.e.configure({**self.e.settings, "strategy": "htf", "order_size": "25"})
        await self.case.start()
        await self.e.stop()
        self.broker.fill = False
        original = time.monotonic
        elapsed = 0

        async def advance(_):
            nonlocal elapsed
            elapsed += 61

        with (
            patch("time.monotonic", side_effect=lambda: original() + elapsed),
            patch("kairos.alpaca_engine.asyncio.sleep", side_effect=advance),
        ):
            await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")
        self.broker.fill = True
        self.request = self.broker.request
        self.debit = dec(".000000433")

        async def delayed_fee(method, path, **kw):
            result = await self.request(method, path, **kw)
            if method == "POST":
                self.broker.holdings["BTC/USD"] -= self.debit
            return result

        self.e.kraken.request.side_effect = delayed_fee
        with (
            patch("kairos.alpaca_engine.asyncio.sleep", new=AsyncMock()),
            self.assertRaisesRegex(SafetyError, "positions differ"),
            patch("kairos.settlement.supported", return_value=False),  # retained pre-fix failure
        ):
            await self.e.qualify_paper("ONE ADDITIONAL ALPACA PAPER ATTEMPT")
        self.q = self.e.store.get("paper-qualification")
        self.before = copy.deepcopy(self.e.ledger())
        self.sent = 0

        async def guarded(method, path, **kw):
            if method == "POST":
                self.assertFalse(self.e.running or self.e.paper_armed)
                self.assertTrue(self.e.recovery_required)
                permit = self.e.kraken.recovery_exit_guard
                if not permit(path, kw["payload"]):
                    raise ExchangeRejected("Stopped")
                self.assertFalse(permit(path, {**kw["payload"], "side": "buy"}))
                self.assertFalse(permit("/v2/positions", kw["payload"]))
                self.assertFalse(permit(path, {**kw["payload"], "qty": "1"}))
                self.sent += 1
            return await self.request(method, path, **kw)

        self.guarded = guarded
        self.e.kraken.request.side_effect = guarded

    async def test_owned_net_exit_keeps_unclassified_debit_and_normal_start_blocked(self):
        result = await recover(self.e, self.q["id"], CONFIRMATION)
        self.assertEqual(self.sent, 1)
        self.assertEqual(self.broker.holdings["BTC/USD"], 0)
        self.assertEqual(self.e.balance("BTC"), self.debit)
        self.assertEqual(self.e.ledger()["fees"], self.before["fees"])
        self.assertEqual(result["unclassified_base_debit"], str(self.debit))
        self.assertEqual(self.e.store.get("paper-qualification:" + self.q["id"]), self.q)
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertIsNone(self.e.kraken.recovery_exit_guard)
        self.assertTrue(self.e.recovery_required)
        self.assertNotEqual(self.e.store.get("paper-qualification")["status"], "complete")
        with self.assertRaises(SafetyError):
            await recover(self.e, self.q["id"], CONFIRMATION)
        await self.e.initialize()
        self.assertTrue(self.e.recovery_required)
        self.assertIn("Automatic account verification pending", self.e.last_error)
        protection = copy.deepcopy(htf.snapshot(self.e)["position"])
        await self.e.reconcile()
        closed = [
            row["data"]["position"]
            for row in self.e.store.history()
            if row["kind"] == "htf-position-closed"
        ]
        self.assertEqual(len(closed), 1)
        for key, value in protection.items():
            if key != "inventory_adjustment":
                self.assertEqual(closed[0][key], value)
        self.assertEqual(dec(closed[0]["inventory_adjustment"]), -self.debit)
        self.assertIsNone(htf.snapshot(self.e)["position"])
        self.assertTrue(self.e.fee_settlement_pending)
        self.assertFalse(self.e.recovery_required)
        self.assertFalse(self.e.qualification_execution_complete)
        self.assertEqual(self.e.balance("BTC"), 0)
        self.assertEqual(self.e.ledger()["fees"], self.before["fees"])
        # Only actual later activity clears the financial difference; manual recovery
        # must not be relabeled a successful normal qualification.
        self.broker.activities.append(
            {
                "id": "posted-base-fee",
                "activity_type": "CFEE",
                "symbol": "BTCUSD",
                "qty": str(-self.debit),
                "net_amount": "0",
                "status": "executed",
            }
        )
        self.broker.cash -= dec(".01")
        self.broker.activities.append(
            {
                "id": "posted-sell-fee",
                "activity_type": "FEE",
                "net_amount": "-.01",
                "status": "executed",
            }
        )
        await self.e.reconcile()
        self.assertEqual(self.e.balance("BTC"), 0)
        self.assertEqual(self.e.store.get(KEY)["status"], "settled")
        self.assertNotEqual(self.e.store.get("paper-qualification")["status"], "complete")

    async def test_changed_reads_or_external_activity_never_claim_permission(self):
        for fault in ("positions", "activity", "cash", "identity"):
            with self.subTest(fault=fault):
                reads = 0

                async def changed(method, path, fault=fault, **kw):
                    nonlocal reads
                    result = await self.request(method, path, **kw)
                    if path == "/v2/positions":
                        reads += 1
                        if fault == "positions" and reads == 2:
                            result[0]["qty"] = str(dec(result[0]["qty"]) - dec("1e-9"))
                    if path == "/v2/account" and fault == "cash":
                        result["cash"] = str(dec(result["cash"]) - 1)
                    if path == "/v2/account" and fault == "identity":
                        result["id"] = "changed"
                    if path == "/v2/account/activities" and fault == "activity":
                        result.append(
                            {"id": "external", "activity_type": "JNLC", "net_amount": "1"}
                        )
                    return result

                self.e.kraken.request.side_effect = changed
                with self.assertRaises(SafetyError):
                    await recover(self.e, self.q["id"], CONFIRMATION)
                self.assertIsNone(self.e.store.get(KEY))
                self.assertEqual(len(self.e.orders()), 2)

    async def test_restart_retains_failed_qualification_holdings_latch(self):
        await self.e.initialize()
        self.assertTrue(self.e.recovery_required)
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertEqual(self.e.ledger(), self.before)
        self.assertEqual(len(self.e.orders()), 2)

    async def test_stop_after_claim_revokes_only_exit_payload_permission(self):
        async def stopped(method, path, **kw):
            if method == "POST":
                self.e.stop_generation += 1
            return await self.guarded(method, path, **kw)

        self.e.kraken.request.side_effect = stopped
        with self.assertRaises(ExchangeRejected):
            await recover(self.e, self.q["id"], CONFIRMATION)
        self.assertEqual(self.sent, 0)
        self.assertEqual(self.e.orders()[-1]["status"], "rejected")
        self.assertIsNotNone(self.e.store.get(KEY))
        self.assertTrue(self.e.recovery_required)
        self.assertIsNone(self.e.kraken.recovery_exit_guard)

    async def test_canceled_unknown_write_retains_claim_and_never_replays(self):
        async def uncertain(method, path, **kw):
            if method == "POST":
                raise asyncio.CancelledError
            return await self.request(method, path, **kw)

        self.e.kraken.request.side_effect = uncertain
        with self.assertRaises(asyncio.CancelledError):
            await recover(self.e, self.q["id"], CONFIRMATION)
        self.assertEqual(self.e.orders()[-1]["status"], "uncertain")
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertIsNone(self.e.kraken.recovery_exit_guard)
        with self.assertRaises(SafetyError):
            await recover(self.e, self.q["id"], CONFIRMATION)
