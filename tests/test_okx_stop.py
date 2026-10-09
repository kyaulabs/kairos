import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from kairos import programs
from kairos.domain import SafetyError, dec
from kairos.okx_engine import OKXEngine
from tests import test_okx_engine as fixtures
from tests import test_okx_twap_pricing as pricing


class OKXStopTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.OKXEngineTests.asyncSetUp
    book = fixtures.OKXEngineTests.book
    prepare = pricing.TWAPPricingTests.prepare
    proposal = pricing.TWAPPricingTests.proposal

    async def test_stop_after_one_sell_records_fill_fees_holdings_and_survives_restart(self):
        await self.prepare("sell")
        await self.engine.tick()
        before = self.engine.ledger()
        program = self.store.get(programs.key(self.engine))
        await self.engine.stop()
        report = self.store.get("okx-operation")
        self.assertEqual(report["status"], "STOPPED")
        self.assertEqual(report["phase"], "stopped")
        self.assertTrue(report["submitted"])
        self.assertEqual(len(report["orders"]), 1)
        self.assertEqual(report["orders"][0]["side"], "sell")
        self.assertEqual(dec(report["orders"][0]["filled"]), dec(".0002"))
        self.assertEqual(report["fees"], self.store.orders()[-1]["fees"])
        self.assertEqual(dec(report["residual"]), dec(".0002995"))
        self.assertEqual(report["closing_ledger"], before)
        self.assertEqual(report["program_execution"]["orders"]["filled"], 1)
        self.assertEqual(report["reconciliation"]["status"], "matched")
        self.assertEqual(self.store.get("okx-operation:" + report["id"]), report)
        self.assertIsNone(self.engine.last_error)
        self.assertFalse(self.engine.running or self.engine.armed)
        self.assertIsNone(self.engine.authorization)
        await self.engine.stop()
        self.assertEqual(self.store.get("okx-operation"), report)
        self.store.put("settings", self.engine.settings)
        restarted = OKXEngine(self.store, self.client, None, lambda *_: None)
        await restarted.initialize()
        restarted.clock = lambda: program["next_at"] + 601
        await restarted.tick()
        self.assertEqual(self.store.get("okx-operation"), report)
        self.assertEqual(self.store.get(programs.key(restarted)), program)
        self.assertEqual(restarted.ledger(), before)
        self.assertFalse(restarted.running or restarted.armed)
        self.assertEqual(len(self.store.orders()), 2)  # Prior owned buy, one sell.
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 2)

    async def test_stop_during_tick_waits_for_fill_settlement_before_final_report(self):
        await self.prepare()
        filled, release = asyncio.Event(), asyncio.Event()
        place = self.engine.place

        async def pause_after_fill(*args, **kwargs):
            result = await place(*args, **kwargs)
            filled.set()
            await release.wait()
            return result

        with patch.object(self.engine, "place", pause_after_fill):
            tick = asyncio.create_task(self.engine.tick())
            await asyncio.wait_for(filled.wait(), 3)
            stop = asyncio.create_task(self.engine.stop())
            await asyncio.sleep(0)
            self.assertFalse(self.engine.running or self.engine.armed)
            release.set()
            await asyncio.wait_for(asyncio.gather(tick, stop), 3)
        report = self.store.get("okx-operation")
        self.assertEqual(report["status"], "STOPPED")
        self.assertEqual(report["counts"]["filled"], 1)
        self.assertTrue(report["submitted"])
        self.assertEqual(report["reconciliation"]["status"], "matched")
        self.assertIsNone(self.engine.last_error)
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 1)

    async def test_stop_during_pre_intent_tick_error_owns_the_final_no_order_report(self):
        await self.prepare()
        entered, release = asyncio.Event(), asyncio.Event()

        async def interrupted(*args, **kwargs):
            entered.set()
            await release.wait()
            raise SafetyError("Engine stopped before order submission")

        with patch.object(self.engine, "place", interrupted):
            tick = asyncio.create_task(self.engine.tick())
            await asyncio.wait_for(entered.wait(), 3)
            stop = asyncio.create_task(self.engine.stop())
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(asyncio.gather(tick, stop), 3)
        report = self.store.get("okx-operation")
        self.assertEqual(report["status"], "STOPPED")
        self.assertFalse(report["submitted"])
        self.assertEqual(report["orders"], [])
        self.assertIsNone(self.engine.last_error)
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])

    async def test_stop_before_first_window_reports_no_submission(self):
        await self.prepare()
        before = self.engine.ledger()
        await self.engine.stop()
        report = self.store.get("okx-operation")
        self.assertEqual(report["status"], "STOPPED")
        self.assertFalse(report["submitted"])
        self.assertEqual(report["orders"], [])
        self.assertEqual(report["fees"], {})
        self.assertEqual(report["program_execution"]["outcome"], "no_fills")
        self.assertEqual(report["closing_ledger"], before)
        self.assertIsNone(self.engine.last_error)
        await self.engine.tick()
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])

    async def open_child(self):
        await self.prepare()
        self.venue.defer = True
        with (
            patch.object(self.engine, "confirm_spot", AsyncMock()),
            patch.object(self.engine, "settle", AsyncMock()),
        ):
            await self.engine.tick()
        self.assertEqual(self.store.orders()[0]["status"], "open")
        self.assertEqual(self.store.get("okx-operation")["status"], "running")

    async def test_stop_report_uses_final_fill_when_cancellation_races_execution(self):
        await self.open_child()
        self.venue.fill_on_cancel = True
        await self.engine.stop()
        report = self.store.get("okx-operation")
        self.assertEqual(report["status"], "STOPPED")
        self.assertEqual(report["orders"][0]["status"], "closed")
        self.assertGreater(dec(report["orders"][0]["filled"]), 0)
        self.assertGreater(dec(report["fees"]["BTC"]), 0)
        self.assertEqual(dec(report["residual"]), self.engine.balance("BTC"))
        self.assertEqual(report["reconciliation"]["status"], "matched")
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 2)

    async def test_failed_stop_reconciliation_remains_blocking_not_healthy_stopped(self):
        await self.open_child()
        with patch.object(
            self.engine,
            "cancel_active",
            AsyncMock(side_effect=SafetyError("native cancellation evidence unavailable")),
        ):
            with self.assertRaisesRegex(SafetyError, "cancellation evidence"):
                await self.engine.stop()
        report = self.store.get("okx-operation")
        self.assertEqual(report["status"], "RECONCILIATION_PENDING")
        self.assertEqual(report["phase"], "stopped")
        self.assertTrue(report["submitted"])
        self.assertEqual(report["orders"][0]["status"], "open")
        self.assertTrue(self.engine.recovery_required)
        self.assertIn("cancellation evidence", self.engine.last_error)
        self.assertFalse(self.engine.running or self.engine.armed)

    async def test_unexpected_stop_read_failure_cannot_be_reported_as_healthy(self):
        await self.prepare()
        with patch.object(
            self.engine, "settle", AsyncMock(side_effect=RuntimeError("private transport detail"))
        ):
            with self.assertRaises(RuntimeError):
                await self.engine.stop()
        report = self.store.get("okx-operation")
        self.assertEqual(report["status"], "RECONCILIATION_PENDING")
        self.assertNotIn("private transport detail", report["message"])
        self.assertFalse(report["submitted"])
        self.assertTrue(self.engine.recovery_required)
        self.assertFalse(self.engine.running or self.engine.armed)

    async def test_unknown_submission_at_stop_is_not_reported_as_no_submission(self):
        await self.open_child()
        order = self.store.orders()[0]
        order.update(status="uncertain", txid=None, write_outcome="unknown")
        self.store.save_order(order)
        with patch.object(
            self.engine,
            "cancel_active",
            AsyncMock(side_effect=SafetyError("original submission still unresolved")),
        ):
            with self.assertRaises(SafetyError):
                await self.engine.stop()
        report = self.store.get("okx-operation")
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertEqual(report["submitted"], "unknown")
        self.assertEqual(report["program_execution"]["outcome"], "unresolved")
        self.assertTrue(self.engine.recovery_required)
        self.assertFalse(self.engine.running or self.engine.armed)
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 1)

    async def test_legacy_summary_is_unchanged_while_latest_evidence_shows_actual_fill(self):
        await self.prepare("sell")
        await self.engine.tick()
        original = self.store.get("okx-operation")
        original.update(status="interrupted", message="Historical restart revoked permission")
        self.store.put("okx-operation", original)
        self.store.put("okx-operation:" + original["id"], original)
        self.engine.running = self.engine.armed = False
        self.engine.authorization = None
        ledger, history = self.engine.ledger(), self.store.history()
        state = self.engine.snapshot()
        evidence = state["execution_cycle_evidence"]
        self.assertEqual(state["execution_cycle"], original)
        self.assertFalse(original["submitted"])
        self.assertTrue(evidence["submitted"])
        self.assertEqual(evidence["counts"]["filled"], 1)
        self.assertEqual(len(evidence["orders"]), 1)  # Excludes earlier entry authorization.
        self.assertEqual(evidence["orders"][0]["side"], "sell")
        self.assertEqual(evidence["fees"], self.store.orders()[-1]["fees"])
        self.assertEqual(self.engine.ledger(), ledger)
        self.assertEqual(self.store.history(), history)
        await self.engine.stop()
        self.assertEqual(self.store.get("okx-operation"), original)
        self.assertEqual(self.store.get("okx-operation:" + original["id"]), original)
