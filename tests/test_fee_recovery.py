import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from kairos import diagnostics, htf
from kairos.clients import Kraken, TransientFeeRead
from kairos.domain import SafetyError
from kairos.fees import AccountFees, FeeUnavailable
from kairos.store import Store
from tests.helpers import BTC, RuleEngine, book, fake_jev, fake_kraken, htf_baseline
from tests.test_clients_web import Session


class FeeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.spot = fake_kraken()
        self.engine = RuleEngine(self.store, self.spot, fake_jev(), lambda *_: None)
        await self.engine.initialize()
        await self.engine.start()

    async def asyncTearDown(self):
        await self.engine.close()
        await self.spot.market_data.close()
        self.store.close()

    def fail(self, error=None):
        self.engine.fees.attempts[BTC.id] -= 31
        self.spot.fees.side_effect = error or TransientFeeRead("Kraken fee request timed out")

    async def test_transient_read_waits_without_orders_then_resumes_with_fresh_baseline(self):
        before = self.engine.ledger()
        self.fail()
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertTrue(self.engine.fee_recovery)
        self.assertTrue(self.engine.snapshot()["fees"]["markets"][0]["stale"])
        calls = self.spot.fees.await_count
        for _ in range(3):
            await self.engine.tick()
        self.assertEqual(self.spot.fees.await_count, calls)
        self.assertEqual(self.engine.orders(), [])
        self.assertEqual(self.engine.ledger(), before)
        self.spot.fees.side_effect = None
        self.engine.fees.attempts[BTC.id] -= 61
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertFalse(self.engine.fee_recovery)
        self.assertIsNone(self.engine.last_error)
        self.assertIn("startup baseline", self.engine.latest_decision["reason"])
        self.assertEqual(self.engine.orders(), [])
        messages = [e for e in self.store.history() if e["kind"] == "fee-recovery"]
        self.assertEqual(len(messages), 2)
        self.spot.add.assert_not_awaited()

    async def test_protection_is_preserved_and_runs_immediately_when_fees_recover(self):
        await htf_baseline(self.engine, self.spot.candles)
        await self.engine.tick()
        plan = htf.snapshot(self.engine)["position"]
        ledger = self.engine.ledger()
        self.fail()
        await self.engine.tick()
        self.assertEqual(htf.snapshot(self.engine)["position"], plan)
        self.assertEqual(self.engine.ledger(), ledger)
        self.spot.book.side_effect = lambda p: book(p, "9500", "9510")
        self.spot.fees.side_effect = None
        self.engine.fees.attempts[BTC.id] -= 61
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertIn("protective stop", self.engine.orders()[-1]["reason"])

    async def test_explicit_stop_prevents_any_later_automatic_resume(self):
        self.fail()
        await self.engine.tick()
        await self.engine.stop()
        self.spot.fees.side_effect = None
        self.engine.fees.attempts[BTC.id] -= 61
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.fee_recovery)
        self.assertFalse(self.engine.snapshot()["fees"]["markets"][0]["stale"])
        self.assertEqual(self.engine.orders(), [])

    async def test_stop_during_failed_read_wins(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def fail(_):
            entered.set()
            await release.wait()
            raise TransientFeeRead("timeout")

        self.fail()
        self.spot.fees.side_effect = fail
        tick = asyncio.create_task(self.engine.tick())
        await entered.wait()
        stop = asyncio.create_task(self.engine.stop())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(tick, stop)
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.fee_recovery)

    async def test_permanent_failure_requires_intervention_even_after_background_success(self):
        self.fail(SafetyError("Kraken rejected request: Permission denied"))
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.fee_recovery)
        self.spot.fees.side_effect = None
        self.engine.fees.attempts[BTC.id] -= 61
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.orders(), [])

    async def test_live_or_unresolved_sessions_never_auto_recover(self):
        for mode, unresolved in (("trading", False), ("dry-run", True)):
            with self.subTest(mode=mode):
                self.engine.mode = mode
                self.engine.running = True
                self.engine.recovery_required = unresolved
                self.engine.fees.attempts[BTC.id] -= 61
                self.spot.fees.side_effect = TransientFeeRead("timeout")
                await self.engine.tick()
                self.assertFalse(self.engine.running)
                self.assertFalse(self.engine.fee_recovery)
        self.engine.mode = "dry-run"
        self.engine.recovery_required = False

    async def test_failure_during_submission_is_not_an_automatic_retry(self):
        await htf_baseline(self.engine, self.spot.candles)
        self.engine.place = AsyncMock(
            side_effect=FeeUnavailable("late fee failure", retryable=True)
        )
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.fee_recovery)
        self.engine.place.assert_awaited_once()


class FeeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_transient_tradevolume_reads_are_retryable(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        for method, status, errors, retry in (
            ("TradeVolume", 503, [], True),
            ("TradeVolume", 429, [], True),
            ("TradeVolume", 403, [], False),
            ("TradeVolume", 200, ["EAPI:Rate limit exceeded"], True),
            ("TradeVolume", 200, ["EAPI:Invalid nonce"], False),
            ("TradeVolume", 200, ["EGeneral:Permission denied"], False),
            ("TradeVolume", 200, ["EAPI:Invalid key", "EAPI:Rate limit exceeded"], False),
            ("AddOrder", 503, [], False),
        ):
            session = Session({"error": errors}, status)
            client = Kraken(
                session, store, "test-key", base64.b64encode(b"test-secret").decode(), True
            )
            with self.subTest(method=method, status=status, errors=errors):
                with self.assertRaises(SafetyError) as raised:
                    await client.request(method, private=True)
                self.assertEqual(isinstance(raised.exception, TransientFeeRead), retry)
                self.assertEqual(len(session.calls), 1)
        session = Session({})

        def timeout(*args, **kwargs):
            raise TimeoutError("upstream read timed out")

        session.request = timeout
        client = Kraken(session, store, "test-key", base64.b64encode(b"test-secret").decode(), True)
        with self.assertRaises(TransientFeeRead) as raised:
            await client.request("TradeVolume", private=True)
        self.assertIsInstance(raised.exception.__cause__, TimeoutError)


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_log_keeps_cause_frames_and_codes_but_not_secrets_or_ui_details(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "errors.jsonl"
            handler = diagnostics.configure(path)
            self.addCleanup(handler.close)
            self.addCleanup(diagnostics.logger.removeHandler, handler)
            diagnostics.register_secrets("actual-secret-value")
            spot = fake_kraken()

            async def fail(_):
                try:
                    raise TimeoutError(
                        "actual-secret-value Authorization: Bearer unknown-token https://host/path?private=hidden-value"
                    )
                except TimeoutError as exc:
                    failure = TransientFeeRead("Fee transport unavailable")
                    failure.diagnostic_details = {"exchange_errors": ["EService:Busy"]}
                    raise failure from exc

            spot.fees.side_effect = fail
            fees = AccountFees(spot, None)
            with self.assertRaises(FeeUnavailable) as raised:
                await fees.refresh([BTC])
            text = path.read_text()
            record = json.loads(text)
            self.assertEqual(record["id"], raised.exception.error_id)
            self.assertEqual(
                [c["type"] for c in record["chain"]], ["TransientFeeRead", "TimeoutError"]
            )
            self.assertTrue(record["chain"][1]["frames"])
            self.assertIn("EService:Busy", text)
            for secret in ("actual-secret-value", "unknown-token", "hidden-value"):
                self.assertNotIn(secret, text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("TimeoutError", json.dumps(fees.snapshot([BTC])))
            handler.maxBytes = 1
            diagnostics.capture(ValueError("another failure"), "test-rotation")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            backup_stat = await asyncio.to_thread(Path(str(path) + ".1").stat)
            self.assertEqual(backup_stat.st_mode & 0o777, 0o600)
