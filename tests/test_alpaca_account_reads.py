"""Account-read recovery, with no network, credentials or experimental orders."""

import asyncio
import copy
import time
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp

from kairos import htf, programs
from kairos.alpaca import Alpaca
from kairos.alpaca_transport import PendingAlpacaAccount
from kairos.domain import SafetyError, dec
from tests import test_alpaca as fixtures
from tests.test_clients_web import Session


class FailingSession(Session):
    def __init__(self, error):
        super().__init__({})
        self.error = error

    def request(self, *args, **kwargs):
        super().request(*args, **kwargs)
        raise self.error


class AccountTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_allowlisted_transient_gets_are_recoverable_and_never_retried_here(self):
        for path in (
            "/v2/account",
            "/v2/positions",
            "/v2/orders",
            "/v2/account/activities",
            "/v2/clock",
        ):
            for error in (TimeoutError(), aiohttp.ServerDisconnectedError()):
                client = Alpaca(FailingSession(error), "fixture-key", "fixture-secret")
                with self.assertRaises(PendingAlpacaAccount) as caught:
                    await client.request("GET", path)
                self.assertEqual(caught.exception.details["endpoint"], path)
                self.assertEqual(len(client.session.calls), 1)
                self.assertFalse(client.requests.busy)
        for method, path in (
            ("POST", "/v2/orders"),
            ("DELETE", "/v2/orders/private-id"),
            ("GET", "/v2/orders/private-id"),
        ):
            client = Alpaca(
                FailingSession(TimeoutError()), "fixture-key", "fixture-secret", allow_paper=True
            )
            with self.assertRaises(SafetyError) as caught:
                await client.request(method, path)
            self.assertNotIsInstance(caught.exception, PendingAlpacaAccount)
            self.assertEqual(len(client.session.calls), 1)
            self.assertEqual("outcome may be unknown" in str(caught.exception), method != "GET")

    async def test_http_retry_classification_and_server_backoff(self):
        for status in (429, 500, 502, 503, 504, 400, 401, 403, 404, 422):
            client = Alpaca(Session({}, status), "fixture-key", "fixture-secret")
            with self.assertRaises(SafetyError) as caught:
                await client.request("GET", "/v2/account")
            self.assertEqual(
                isinstance(caught.exception, PendingAlpacaAccount),
                status in (429, 500, 502, 503, 504),
            )
            self.assertEqual(len(client.session.calls), 1)
            if status == 429:
                self.assertGreater(
                    client.requests.snapshot()["quotas"]["trading"]["retry_in_seconds"], 59
                )

    async def test_malformed_payload_and_tls_failure_are_not_recoverable(self):
        for error in (
            aiohttp.ClientPayloadError("bad payload"),
            aiohttp.ServerFingerprintMismatch(b"a", b"b", "fixture", 443),
            ValueError("bad JSON"),
        ):
            client = Alpaca(FailingSession(error), "fixture-key", "fixture-secret")
            with self.assertRaises(SafetyError) as caught:
                await client.request("GET", "/v2/account")
            self.assertNotIsInstance(caught.exception, PendingAlpacaAccount)
            self.assertEqual(len(client.session.calls), 1)

    async def test_full_queue_read_is_typed_without_sending_and_writes_remain_fatal(self):
        client = Alpaca(Session({}), "fixture-key", "fixture-secret", allow_paper=True)
        client.requests.waiters = {n: (1, "trading") for n in range(64)}
        with self.assertRaises(PendingAlpacaAccount):
            await client.request("GET", "/v2/account")
        with self.assertRaises(SafetyError) as caught:
            await client.request("POST", "/v2/orders")
        self.assertNotIsInstance(caught.exception, PendingAlpacaAccount)
        self.assertFalse(client.session.calls)


class AccountRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = fixtures.AlpacaEngineTests()
        await self.case.asyncSetUp()
        self.addAsyncCleanup(self.case.asyncTearDown)
        self.engine = self.case.engine
        await self.engine.configure({**self.engine.settings, "strategy": "htf"})
        await self.case.start()
        self.engine.htf_review.refresh = AsyncMock(return_value=None)
        self.failures = []

    def fail(self, path="/v2/orders"):
        async def request(method, endpoint, **kwargs):
            if method == "GET" and endpoint == path:
                self.failures.append(endpoint)
                raise PendingAlpacaAccount(endpoint, "timeout")
            return await self.case.broker.request(method, endpoint, **kwargs)

        self.engine.kraken.request = AsyncMock(side_effect=request)

    def restore(self):
        self.engine.kraken.request = AsyncMock(side_effect=self.case.broker.request)

    def due(self):
        self.engine.account_wait = (self.engine.account_wait[0], 0)

    def assert_no_writes(self):
        self.assertFalse(any(m != "GET" for m, _, _ in self.case.broker.calls))

    async def test_full_pass_retry_blocks_orders_and_rebaselines_without_replaying_intent(self):
        before = copy.deepcopy(self.engine.ledger())
        last_success = self.engine.account_read_status["last_success_at"]
        self.fail()
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertFalse(self.engine.recovery_required)
        self.assertIsNotNone(self.engine.account_wait)
        self.assertFalse(self.engine.kraken.order_guard())
        self.assertEqual(self.engine.account_read_status["last_success_at"], last_success)
        await self.engine.tick()  # No immediate retry or ordinary strategy evaluation.
        self.assertEqual(len(self.failures), 1)
        state = htf.snapshot(self.engine)
        state.update(entry_signal="buy", pending_signal="buy")
        self.engine.store.put(htf.key(self.engine), state)
        self.restore()
        self.due()
        before_calls = len(self.case.broker.calls)
        await self.engine.tick()
        self.assertEqual(
            [p for _, p, _ in self.case.broker.calls[before_calls:]],
            ["/v2/account", "/v2/orders", "/v2/account/activities", "/v2/account", "/v2/positions"],
        )
        self.assertTrue(self.engine.running)
        self.assertIsNone(self.engine.account_wait)
        self.assertIsNone(self.engine.last_error)
        self.assertIsNone(htf.snapshot(self.engine)["entry_signal"])
        self.assertEqual(self.engine.ledger(), before)
        status = self.engine.snapshot()["account_reads"]
        self.assertEqual(status["recovery"]["status"], "restored")
        self.assertEqual(status["recovery"]["attempts"], 1)
        self.assertGreaterEqual(status["last_success_at"], last_success)
        events = [e["data"] for e in self.engine.store.history(100) if e["kind"] == "account-read"]
        self.assertEqual([e["status"] for e in events], ["waiting", "restored"])
        self.assertEqual(events[-1]["endpoint"], "/v2/orders")
        self.assertIn("duration_seconds", events[-1])
        self.assert_no_writes()

    async def test_three_failed_recovery_passes_exhaust_and_never_resume(self):
        self.fail()
        await self.engine.tick()
        for _ in range(3):
            self.due()
            await self.engine.tick()
        self.assertEqual(len(self.failures), 4)
        self.assertFalse(self.engine.running)
        self.assertTrue(self.engine.recovery_required)
        self.assertEqual(self.engine.account_read_status["recovery"]["status"], "halted")
        self.assertIn("exhausted", self.engine.last_error)
        self.restore()
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assert_no_writes()

    async def test_deadline_and_loss_latch_prevent_another_read(self):
        for loss in (False, True):
            self.engine.running = True
            self.engine.recovery_required = False
            self.fail()
            await self.engine.tick()
            before = len(self.failures)
            if loss:
                self.engine.daily_pnl = "-12.50"
            else:
                self.engine.account_wait = (time.monotonic() - 121, 0)
            await self.engine.tick()
            self.assertFalse(self.engine.running)
            self.assertTrue(self.engine.recovery_required)
            self.assertEqual(len(self.failures), before)
        self.assert_no_writes()

    async def test_successful_reads_do_not_clear_new_loss_or_account_mismatch(self):
        for mismatch in (False, True):
            self.engine.running = True
            self.engine.recovery_required = False
            self.engine.daily_pnl = "0"
            self.fail()
            await self.engine.tick()
            self.restore()
            self.due()
            if mismatch:
                self.case.broker.cash += 1
            else:
                self.engine.store.put(
                    "day:paper", {"date": time.strftime("%Y-%m-%d", time.gmtime()), "equity": "600"}
                )
            await self.engine.tick()
            self.assertFalse(self.engine.running)
            self.assertTrue(self.engine.recovery_required)
            self.assertEqual(self.engine.account_read_status["recovery"]["status"], "halted")
        self.assert_no_writes()

    async def test_stop_during_recovery_read_and_explicit_reconcile_cannot_auto_start(self):
        self.fail()
        await self.engine.tick()
        self.restore()
        self.due()
        entered, release = asyncio.Event(), asyncio.Event()
        actual = self.engine.kraken.account

        async def delayed():
            entered.set()
            await release.wait()
            return await actual()

        self.engine.kraken.account = delayed
        tick = asyncio.create_task(self.engine.tick())
        await entered.wait()
        stop = asyncio.create_task(self.engine.stop())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(tick, stop)
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.engine.account_wait)
        self.assertEqual(self.engine.account_read_status["recovery"]["status"], "stopped")
        self.engine.kraken.account = actual
        await self.engine.reconcile()
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assert_no_writes()

    async def test_active_and_uncertain_orders_never_enter_automatic_recovery(self):
        self.case.broker.fill = False
        order = await self.case.buy(maker=True)
        for status in ("open", "uncertain"):
            self.engine.running = True
            self.engine.recovery_required = False
            order["status"] = status
            self.engine.store.save_order(order)
            self.assertFalse(
                self.engine.wait_for_account_data(PendingAlpacaAccount("/v2/orders", "timeout"))
            )
            self.assertIsNone(self.engine.account_wait)
            self.assertTrue(self.engine.recovery_required)

    async def test_partial_fee_reconciliation_is_idempotent_and_owned_protection_survives(self):
        plan = {
            "id": "owned",
            "side": "buy",
            "entry_limit": "100",
            "stop": "97",
            "opened_at": time.time(),
            "deadline": time.time() + 86400,
            "exit_reason": None,
        }
        self.engine.store.put(
            htf.key(self.engine),
            {"position": None, "entry_attempt": {"position": plan, "order_id": None}},
        )
        await self.case.buy()
        await self.engine.reconcile_account()
        htf.prepare(self.engine)
        position = copy.deepcopy(htf.snapshot(self.engine)["position"])
        self.case.broker.cash -= dec(".1")
        self.case.broker.activities.append(
            {"id": "one-fee", "activity_type": "FEE", "status": "executed", "net_amount": "-.1"}
        )
        self.case.broker.calls.clear()
        self.fail("/v2/positions")
        await self.engine.tick()
        after_fee = copy.deepcopy(self.engine.ledger())
        self.assertEqual(dec(after_fee["fees"]["USD"]), dec(".1"))
        self.restore()
        self.due()
        await self.engine.tick()
        self.assertEqual(self.engine.ledger(), after_fee)
        self.assertEqual(htf.snapshot(self.engine)["position"], position)
        self.assertEqual(len(self.engine.orders()), 1)
        self.assert_no_writes()

    async def test_inflight_recovery_deadline_cancels_read_and_stays_halted(self):
        self.fail()
        await self.engine.tick()
        self.engine.account_wait = (time.monotonic() - 119.99, 0)
        self.engine.kraken.account = AsyncMock(side_effect=lambda: None)

        async def slow():
            await asyncio.sleep(10)

        self.engine.kraken.account.side_effect = slow
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertTrue(self.engine.recovery_required)
        self.assertIn("deadline exhausted", self.engine.last_error)
        self.assertIsNone(self.engine.account_wait)
        self.assert_no_writes()

    async def test_manual_reconcile_ends_wait_without_resuming_and_guard_rejects_queued_post(self):
        self.fail()
        await self.engine.tick()
        # Use the real final wire gate, with a fixture transport; no order can be sent.
        client = self.engine.kraken
        client.session = Session({})
        with self.assertRaisesRegex(SafetyError, "submission canceled"):
            await Alpaca.request(client, "POST", "/v2/orders")
        self.assertFalse(client.session.calls)
        self.restore()
        await self.engine.reconcile()
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.engine.account_wait)
        self.assertEqual(self.engine.account_read_status["recovery"]["status"], "reconciled")
        self.assert_no_writes()

    async def test_equity_clock_failure_cannot_reset_budget_with_only_account_success(self):
        await self.engine.stop()
        await self.engine.configure(
            {**self.engine.settings, "strategy": "dca", "pair": "alpaca:AAPL"}
        )
        await self.case.start()
        self.fail("/v2/clock")
        await self.engine.tick()
        for _ in range(3):
            self.due()
            await self.engine.tick()
        self.assertEqual(len(self.failures), 4)
        self.assertFalse(self.engine.running)
        self.assertTrue(self.engine.recovery_required)
        self.assertEqual(self.engine.account_read_status["last_failure"]["endpoint"], "/v2/clock")
        self.assert_no_writes()

    async def test_claimed_scheduled_slot_is_not_replayed_after_account_recovery(self):
        await self.engine.stop()
        await self.engine.configure(
            {**self.engine.settings, "strategy": "dca", "dca_amount": "10", "dca_count": 1}
        )
        await self.case.start()
        actual = self.engine.kraken.balances
        self.engine.kraken.balances = AsyncMock(
            side_effect=PendingAlpacaAccount("/v2/account", "timeout")
        )
        await self.engine.tick()
        self.assertIsNotNone(self.engine.account_wait)
        self.assertEqual(programs.snapshot(self.engine)["next_slot"], 1)
        self.engine.kraken.balances = actual
        self.due()
        await self.engine.tick()
        await self.engine.tick()
        self.assertEqual(programs.snapshot(self.engine)["next_slot"], 1)
        self.assertFalse(self.engine.orders())
        self.assert_no_writes()

    async def test_audit_write_failure_halts_instead_of_leaving_recovery_enabled(self):
        with patch.object(self.engine, "event", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.engine.wait_for_account_data(PendingAlpacaAccount("/v2/orders", "timeout"))
        self.assertFalse(self.engine.running)
        self.assertTrue(self.engine.recovery_required)
        self.assertIsNone(self.engine.account_wait)
        self.assertEqual(self.engine.account_read_status["recovery"]["status"], "halted")
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assert_no_writes()

    async def test_strategy_phase_read_failure_uses_same_recovery_gate(self):
        # Failure after outer reconciliation, e.g. a pre-order balance read.
        self.engine.htf_review.refresh = AsyncMock(
            side_effect=PendingAlpacaAccount("/v2/account", "timeout")
        )
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertIsNotNone(self.engine.account_wait)
        self.assert_no_writes()
