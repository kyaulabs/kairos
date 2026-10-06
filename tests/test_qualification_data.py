import asyncio
import copy
import time
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from kairos import qualification_data
from kairos.alpaca_transport import PendingAlpacaData, RejectedAlpacaData
from kairos.domain import SafetyError, dec
from kairos.multibar import PROTOCOL_HASH
from tests import test_settlement


class QualificationDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = test_settlement.SettlementTests()
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.case.asyncTearDown)
        self.e, self.b = self.fixture.e, self.fixture.b
        self.retry = patch("kairos.qualification_data.RETRY_SECONDS", 0.001)
        self.retry.start()
        self.addCleanup(self.retry.stop)
        self.b.calls.clear()

    async def qualify(self):
        await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")

    def writes(self):
        return [c for c in self.b.calls if c[0] != "GET"]

    async def test_read_waits_before_entry_and_inside_exit_preflight_replan_without_replay(self):
        book, execution, valuation = self.e.kraken.book, self.e.execution_book, self.e.valuation
        faults = {"initial": False, "entry": False, "exit": False}
        buy_plans = []

        async def current(pair):
            if not faults["initial"]:
                faults["initial"] = True
                raise PendingAlpacaData("old source, new receipt")
            value = await book(pair)
            if faults["exit"]:
                value = replace(
                    value, bids=[(dec("99.8"), dec(100))], asks=[(dec("99.9"), dec(100))]
                )
                self.e.kraken.market_data.rest_books[pair.id] = value
            return value

        async def pre_submit(pair, side, price, value, maker):
            if side == "buy":
                buy_plans.append(price)
                if not faults["entry"]:
                    faults["entry"] = True
                    raise PendingAlpacaData("entry preflight aged")
            return await execution(pair, side, price, value, maker)

        async def risk(enforce=False):
            if self.e.balance("BTC") and not faults["exit"]:
                faults["exit"] = True
                raise PendingAlpacaData("reserve valuation book aged")
            return await valuation(enforce)

        with (
            patch.object(self.e.kraken, "book", side_effect=current),
            patch.object(self.e, "execution_book", side_effect=pre_submit),
            patch.object(self.e, "valuation", side_effect=risk),
        ):
            await self.qualify()
        self.assertTrue(all(faults.values()))
        self.assertEqual(buy_plans, [dec(100), dec(100)])
        writes = self.writes()
        self.assertEqual([c[2]["side"] for c in writes], ["buy", "sell"])
        self.assertEqual(dec(writes[-1][2]["limit_price"]), dec("99.71"))
        self.assertTrue(self.e.qualification_execution_complete)
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertIsNone(self.e.qualification_data_scope)
        self.assertIsNone(self.e.store.get("multibar-trial:" + PROTOCOL_HASH))
        self.assertEqual(self.e.settings["stale_seconds"], 10)

    async def test_expired_exit_wait_keeps_holding_and_consumes_qualification(self):
        execution = self.e.execution_book

        async def stale(pair, side, *args):
            if side == "sell":
                raise PendingAlpacaData("old exit book")
            return await execution(pair, side, *args)

        with (
            patch("kairos.qualification_data.WAIT_SECONDS", 0.5),
            patch.object(self.e, "execution_book", side_effect=stale),
        ):
            with self.assertRaisesRegex(PendingAlpacaData, "deadline exhausted"):
                await self.qualify()
        self.assertEqual(len(self.writes()), 1)
        self.assertGreater(self.e.balance("BTC"), 0)
        failure = copy.deepcopy(self.e.store.get("paper-qualification"))
        self.assertFalse(
            self.e.running or self.e.paper_armed or self.e.qualification_execution_complete
        )
        with self.assertRaisesRegex(SafetyError, "already claimed"):
            await self.qualify()
        self.assertEqual(self.e.store.get("paper-qualification"), failure)

    async def test_stop_interrupts_backoff_without_rearming_or_submitting(self):
        reached = asyncio.Event()

        async def stale(*args):
            reached.set()
            raise PendingAlpacaData("no fresh entry book")

        with (
            patch("kairos.qualification_data.RETRY_SECONDS", 5),
            patch.object(self.e, "execution_book", side_effect=stale),
        ):
            task = asyncio.create_task(self.qualify())
            await asyncio.wait_for(reached.wait(), 2)
            await asyncio.wait_for(self.e.stop(), 1)
            with self.assertRaisesRegex(SafetyError, "canceled by Stop"):
                await task
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertFalse(self.writes())
        self.assertIsNone(self.e.qualification_data_scope)

    async def test_definitive_transport_rejection_after_intent_is_not_retried(self):
        add = AsyncMock(side_effect=RejectedAlpacaData("book aged while queued"))
        with patch.object(self.e.kraken, "add", add):
            with self.assertRaises(RejectedAlpacaData):
                await self.qualify()
        self.assertEqual(add.await_count, 1)
        self.assertEqual(len(self.e.orders()), 1)
        self.assertEqual(self.e.orders()[0]["status"], "rejected")
        self.assertFalse(self.writes())

    async def test_uncertain_write_and_permanent_errors_do_not_retry(self):
        with patch.object(self.e.kraken, "add", side_effect=TimeoutError("unknown outcome")) as add:
            with self.assertRaisesRegex(SafetyError, "uncertain"):
                await self.qualify()
        self.assertEqual(add.await_count, 1)
        self.assertEqual(self.e.orders()[0]["status"], "uncertain")
        operation = AsyncMock(side_effect=SafetyError("clock regression"))
        with self.assertRaisesRegex(SafetyError, "clock regression"):
            await qualification_data.run(
                self.e,
                operation,
                generation=self.e.stop_generation,
                deadline=time.monotonic() + 10,
                leg="test",
                armed=False,
            )
        self.assertEqual(operation.await_count, 1)

    async def test_data_error_after_confirmed_buy_never_replays_the_write(self):
        place = self.e.place

        async def after_write(*args, **kwargs):
            await place(*args, **kwargs)
            raise PendingAlpacaData("data error after an order was already sent")

        with patch.object(self.e, "place", side_effect=after_write) as calls:
            with self.assertRaises(PendingAlpacaData):
                await self.qualify()
        self.assertEqual(calls.await_count, 1)
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.e.orders()), 1)
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertFalse(self.e.qualification_execution_complete)

    async def test_wait_polls_data_not_full_account_preflight_while_source_is_old(self):
        operation = AsyncMock(side_effect=PendingAlpacaData("old source"))
        with patch.object(
            self.e.kraken, "book", side_effect=PendingAlpacaData("still old")
        ) as book:
            with self.assertRaisesRegex(PendingAlpacaData, "deadline exhausted"):
                await qualification_data.run(
                    self.e,
                    operation,
                    generation=self.e.stop_generation,
                    deadline=time.monotonic() + 0.03,
                    leg="preparation",
                    armed=False,
                )
        self.assertEqual(operation.await_count, 1)
        self.assertGreater(book.await_count, 0)
        self.assertFalse(self.writes())
        self.assertIsNone(self.e.store.get("paper-qualification"))

    async def test_deadline_and_stop_are_checked_at_transport_boundary(self):
        self.e.running = self.e.paper_armed = True
        self.e.qualification_data_scope = {
            "generation": self.e.stop_generation,
            "deadline": time.monotonic() - 1,
            "armed": True,
        }
        self.assertFalse(self.e.kraken.order_guard())
        self.e.qualification_data_scope["deadline"] = time.monotonic() + 10
        self.assertTrue(self.e.kraken.order_guard())
        self.e.stop_generation += 1
        self.assertFalse(self.e.kraken.order_guard())
        self.e.running = self.e.paper_armed = False
        self.e.qualification_data_scope = None
