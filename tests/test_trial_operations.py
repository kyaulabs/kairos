import asyncio
import copy
import time
import unittest
from unittest.mock import AsyncMock, patch

from kairos import htf
from kairos.alpaca_transport import PendingAlpacaAccount, PendingAlpacaData
from kairos.domain import SafetyError, dec
from kairos.multibar import PROTOCOL_HASH
from kairos.operations import DiscordAlerts
from tests import test_alpaca as paper
from tests import test_alpaca_htf_history as native
from tests.test_clients_web import Session


class ScheduledRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = paper.AlpacaEngineTests()
        await self.case.asyncSetUp()
        self.addAsyncCleanup(self.case.asyncTearDown)
        self.e = self.case.engine
        await self.e.configure({**self.e.settings, "strategy": "htf", "api_auto_recovery": True})
        await self.case.start()
        self.e.htf_review.refresh = AsyncMock(return_value=None)
        self.book = self.e.kraken.book

    async def enter(self):
        self.e.market_wait = (self.e.clock() - 301, time.monotonic() - 301)
        self.e.kraken.book = AsyncMock(side_effect=PendingAlpacaData("source stale"))
        await self.e.tick()
        self.assertEqual(self.e.operations.status, "retry-wait")
        self.assertEqual(self.e.retry_status["attempts"], 0)
        self.assertTrue(self.e.running)
        self.assertFalse(self.e.kraken.order_guard())

    def due(self):
        self.e.long_retry = (self.e.long_retry[0], 0)

    async def test_five_spaced_attempts_then_latched_halt(self):
        await self.enter()
        for i in range(5):
            before = len(self.case.broker.calls)
            await self.e.tick()
            self.assertEqual(len(self.case.broker.calls), before)
            self.due()
            await self.e.tick()
            self.assertEqual(self.e.retry_status["attempts"], i + 1)
        self.assertFalse(self.e.running)
        self.assertTrue(self.e.recovery_required)
        self.assertEqual(self.e.retry_status["status"], "halted")
        self.assertEqual(self.e.operations.status, "halted")
        self.e.kraken.book = self.book
        await self.e.tick()
        self.assertFalse(self.e.running)
        self.assertFalse(any(m != "GET" for m, _, _ in self.case.broker.calls))

    async def test_success_retains_run_and_checks_freshness_without_replaying_entry(self):
        run = copy.deepcopy(self.e.active_run())
        ledger = copy.deepcopy(self.e.ledger())
        await self.enter()
        self.due()
        self.e.kraken.book = self.book
        await self.e.tick()
        self.assertIsNone(self.e.long_retry)
        self.assertEqual(self.e.retry_status["status"], "restored")
        self.assertEqual(self.e.active_run(), run)
        self.assertEqual(self.e.ledger(), ledger)
        self.assertTrue(self.e.running)
        self.assertIsNone(htf.snapshot(self.e)["entry_signal"])
        self.assertFalse(self.e.orders())

    async def test_stop_during_retry_read_wins(self):
        await self.enter()
        self.due()
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(pair):
            entered.set()
            await release.wait()
            return await self.book(pair)

        self.e.kraken.book = delayed
        tick = asyncio.create_task(self.e.tick())
        await entered.wait()
        stop = asyncio.create_task(self.e.stop())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(tick, stop)
        self.assertFalse(self.e.running)
        self.assertIsNone(self.e.long_retry)
        self.assertFalse(self.e.orders())

    async def test_holdings_loss_and_uncertainty_block_schedule(self):
        for mutation in ("holdings", "loss", "uncertain"):
            with self.subTest(mutation=mutation):
                self.e.running = True
                self.e.recovery_required = False
                self.e.daily_pnl = "0"
                ledger = self.e.ledger()
                ledger["balances"] = {"USD": "500"}
                if mutation == "holdings":
                    ledger["balances"]["BTC"] = ".1"
                self.e.store.put("ledger:paper", ledger)
                if mutation == "loss":
                    self.e.daily_pnl = "-12.50"
                if mutation == "uncertain":
                    self.e.recovery_required = True
                self.assertFalse(self.e.schedule_retry(PendingAlpacaData("stale")))
                self.assertIsNone(self.e.long_retry)

    async def test_audit_failure_revokes_permission_and_does_not_claim_running(self):
        with patch.object(self.e.store, "event", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.e.schedule_retry(PendingAlpacaData("stale"))
        self.assertFalse(self.e.running)
        self.assertTrue(self.e.recovery_required)
        self.assertEqual(self.e.operations.snapshot()["status"], "halted")
        self.assertIsNone(self.e.long_retry)

    async def test_changed_account_or_permanent_failure_halts_without_five_retries(self):
        await self.enter()
        self.due()
        self.case.broker.account_id = "unexpected"
        await self.e.tick()
        self.assertFalse(self.e.running)
        self.assertTrue(self.e.recovery_required)
        self.assertEqual(self.e.retry_status["attempts"], 1)
        self.assertFalse(self.e.orders())

    async def test_account_short_deadline_escalates_only_with_existing_permission(self):
        self.e.wait_for_account_data(PendingAlpacaAccount("/v2/account", "timeout"))
        self.e.account_wait = (time.monotonic() - 121, 0)
        await self.e.tick()
        self.assertIsNotNone(self.e.long_retry)
        self.assertIsNone(self.e.account_wait)
        await self.e.stop()
        await self.e.tick()
        self.assertIsNone(self.e.long_retry)
        self.assertFalse(self.e.running)

    async def test_late_observer_response_cannot_be_attributed_to_changed_settings(self):
        await self.e.stop()
        self.e.observations.enabled = True
        cutoff = self.e.htf_review.window_end(self.e.clock())

        async def changed(*args, **kwargs):
            await self.e.configure({**self.e.settings, "candle_minutes": 30})
            return native.bars(cutoff)

        self.e.kraken.bars = AsyncMock(side_effect=changed)
        await self.e.tick()
        self.assertFalse(self.e.running)
        self.assertIsNone(self.e.observations.latest)
        self.assertFalse([x for x in self.e.store.history() if x["kind"] == "market-observation"])
        self.assertFalse(self.e.orders())

    async def test_paused_observer_does_not_reconcile_or_restore_permission(self):
        await self.e.stop()
        self.e.observations.enabled = True
        cutoff = self.e.htf_review.window_end(self.e.clock())
        self.e.kraken.bars = AsyncMock(return_value=native.bars(cutoff))
        self.case.broker.calls.clear()
        await self.e.tick()
        self.assertFalse(self.e.running)
        self.assertFalse(self.case.broker.calls)
        self.e.kraken.bars.assert_awaited_once()
        obs = [x for x in self.e.store.history() if x["kind"] == "market-observation"]
        self.assertEqual(len(obs), 1)
        self.assertEqual(len(obs[0]["data"]["native_history"]), 30)
        await self.e.tick()
        self.e.kraken.bars.assert_awaited_once()


class MultiBarTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = native.AlpacaNativeHTFTests()
        await self.case.asyncSetUp()
        self.addAsyncCleanup(self.case.asyncTearDown)
        self.e = self.case.engine
        await self.e.stop()
        await self.e.configure(
            {**self.e.settings, "htf_policy": "multibar-v2", "reinvest_profits": False}
        )
        # Qualification lifecycle is tested separately; strategy fixtures have that prerequisite.
        self.e.store.put("paper-qualification", {"status": "complete"})
        await self.e.start(confirmation="START ALPACA PAPER")

    async def test_native_startup_new_candidate_entry_no_model_and_protected_exit(self):
        await self.case.cycle()
        self.assertFalse(self.e.orders())
        self.assertEqual(self.e.active_run()["protocol_hash"], PROTOCOL_HASH)
        await self.case.next_hour()
        self.assertEqual(len(self.e.orders()), 1, self.e.latest_decision)
        self.case.jev.decide.assert_not_awaited()
        self.assertTrue(self.e.htf_review.snapshot()["native_bars"] == 30)
        position = htf.snapshot(self.e)["position"]
        self.assertTrue(position)
        self.assertLessEqual(position["deadline"], self.e.active_run()["trial_ends_at"])
        self.case.bid = dec(95)
        self.case.now += 16
        self.e.kraken.bars = AsyncMock(side_effect=SafetyError("history unavailable"))
        await self.e.tick()
        self.assertEqual(len(self.e.orders()), 2)
        self.assertEqual(self.e.balance("BTC"), 0)
        self.e.kraken.bars.assert_not_awaited()
        self.case.jev.decide.assert_not_awaited()

    async def test_candidate_expiry_and_consumption_cannot_rearm_same_pullback(self):
        await self.case.cycle()
        self.case.now = self.case.end + 3605
        self.case.end += 3600
        self.case.rows = self.case.rows[1:] + [
            [self.case.end - 3600, "99.95", "102.95", "96.95", "99.95", "99.95", "10", 1]
        ]
        await self.e.refresh_fees()
        book = await self.e.kraken.book(self.case.pair)
        view = self.e.htf_review.entry_view(self.case.rows, self.case.pair, book)
        candidate = view["candidate"]
        self.assertIsNotNone(candidate)
        state = htf.snapshot(self.e)
        state.update(candidate=candidate, candidate_window=view["candle_close_time"])
        self.e.store.put(htf.key(self.e), state)
        self.case.now += 30
        again = self.e.htf_review.entry_view(self.case.rows, self.case.pair, book)
        self.assertEqual(again["candidate"]["expires_at"], candidate["expires_at"])
        state["consumed_candidates"] = [candidate["id"]]
        self.e.store.put(htf.key(self.e), state)
        self.assertIsNone(
            self.e.htf_review.entry_view(self.case.rows, self.case.pair, book)["candidate"]
        )
        state["consumed_candidates"] = []
        self.e.store.put(htf.key(self.e), state)
        self.case.now = candidate["expires_at"]
        await self.e.refresh_fees()
        self.assertIsNone(
            self.e.htf_review.entry_view(self.case.rows, self.case.pair, book)["candidate"]
        )
        self.assertFalse(self.e.orders())

    async def test_recovery_retires_pending_candidate_but_keeps_exit_ownership(self):
        state = htf.snapshot(self.e)
        state["candidate"] = {"id": "pending-before-outage"}
        state["position"] = {"id": "owned-lineage", "stop": "90"}
        self.e.store.put(htf.key(self.e), state)
        htf.arm(self.e)
        state = htf.snapshot(self.e)
        self.assertIsNone(state["candidate"])
        self.assertIn("pending-before-outage", state["consumed_candidates"])
        self.assertEqual(state["position"], {"id": "owned-lineage", "stop": "90"})

    async def test_trial_deadline_cannot_extend_on_restart_and_flat_stops(self):
        run = self.e.active_run()
        end = run["trial_ends_at"]
        await self.e.stop()
        await self.e.start(confirmation="START ALPACA PAPER")
        self.assertEqual(self.e.active_run()["trial_ends_at"], end)
        self.case.now = end + 1
        await self.e.tick()
        self.assertFalse(self.e.running)
        with self.assertRaisesRegex(SafetyError, "trial ended"):
            await self.e.start(confirmation="START ALPACA PAPER")

    async def test_raw_setup_and_quote_gates_are_separate(self):
        await self.case.cycle()
        with patch.object(self.e.fees, "reserve", return_value=dec(1000)):
            await self.case.next_hour()
            self.assertFalse(self.e.orders())
            self.assertTrue(self.e.latest_decision["state"]["pullback_long"])
            self.assertFalse(self.e.latest_decision["state"]["entry_eligible"])
        self.case.now += 31
        await self.case.cycle()
        self.assertEqual(len(self.e.orders()), 1)
        state = htf.snapshot(self.e)
        self.assertTrue(state["consumed_candidates"])
        # Recorded candidate remains an independent event, not repeated HOLD counts.
        summary = self.e.snapshot()["decision_summary"]
        self.assertEqual(summary["distinct_candidates"], 1)
        self.assertEqual(summary["distinct_windows"], 2)
        self.assertEqual(len([x for x in self.e.store.history() if x["kind"] == "candidate"]), 1)


class QualificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = paper.AlpacaEngineTests()
        await self.case.asyncSetUp()
        self.addAsyncCleanup(self.case.asyncTearDown)
        self.e = self.case.engine
        await self.e.configure({**self.e.settings, "strategy": "htf", "order_size": "25"})
        await self.case.start()
        await self.e.stop()

    async def test_one_round_trip_is_owned_and_ends_paused_without_reset(self):
        initial = self.e.ledger()["initial"]
        prior_run = copy.deepcopy(self.e.active_run())
        settings = copy.deepcopy(self.e.settings)
        await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")
        self.assertEqual(len(self.e.orders()), 2)
        self.assertEqual(self.e.balance("BTC"), 0)
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertEqual(self.e.ledger()["initial"], initial)
        self.assertEqual(self.e.settings, settings)
        self.assertIn("qualification", self.e.active_run()["purpose"])
        self.assertEqual(self.e.active_run()["previous_run_id"], prior_run["id"])
        prior_event = next(
            x["data"]["run"]
            for x in self.e.store.history()
            if x["kind"] == "system" and x["data"].get("run", {}).get("id") == prior_run["id"]
        )
        self.assertEqual(prior_event, prior_run)
        with self.assertRaises(SafetyError):
            await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")
        self.assertEqual(len(self.e.orders()), 2)

    async def test_unfilled_passive_entry_is_canceled_once_without_fallback(self):
        self.case.broker.fill = False
        monotonic = time.monotonic
        elapsed = 0

        async def advance(_seconds):
            nonlocal elapsed
            elapsed += 61

        with (
            patch("time.monotonic", side_effect=lambda: monotonic() + elapsed),
            patch("kairos.alpaca_engine.asyncio.sleep", side_effect=advance),
        ):
            await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")
        self.assertEqual(sum(m == "POST" for m, _, _ in self.case.broker.calls), 1)
        self.assertEqual(sum(m == "DELETE" for m, _, _ in self.case.broker.calls), 1)
        self.assertEqual(self.e.orders()[0]["status"], "canceled")
        self.assertEqual(
            self.e.store.get("paper-qualification")["status"], "no entry fill; round trip untested"
        )
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_base_fee_residual_is_owned_retained_and_blocks_trial_start(self):
        request = self.case.broker.request

        async def fee(method, path, **kwargs):
            result = await request(method, path, **kwargs)
            if method == "POST" and kwargs["payload"]["side"] == "buy":
                qty = dec(result["qty"]) * dec(".0025")
                self.case.broker.holdings["BTC/USD"] -= qty
                self.case.broker.activities.append(
                    {
                        "id": "qualification-fee",
                        "activity_type": "CFEE",
                        "status": "executed",
                        "symbol": "BTCUSD",
                        "net_amount": "0",
                        "qty": str(-qty),
                    }
                )
            return result

        self.e.kraken.request.side_effect = fee
        await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")
        self.assertGreater(self.e.balance("BTC"), 0)
        self.assertEqual(self.e.balance("BTC"), htf.owned(self.e, htf.snapshot(self.e)["position"]))
        self.assertEqual(len(self.e.orders()), 2)
        self.assertEqual(self.e.store.get("paper-qualification")["status"], "residual retained")
        await self.e.configure(
            {**self.e.settings, "htf_policy": "multibar-v2", "reinvest_profits": False}
        )
        with self.assertRaisesRegex(SafetyError, "round trip"):
            await self.e.start(confirmation="START ALPACA PAPER")
        self.assertFalse(self.e.running)

    async def test_confirmation_and_stop_before_final_arming(self):
        with self.assertRaises(SafetyError):
            await self.e.qualify_paper("")
        actual = self.e.kraken.book

        async def stopped(pair):
            self.e.stop_generation += 1
            return await actual(pair)

        self.e.kraken.book = stopped
        with self.assertRaisesRegex(SafetyError, "canceled by Stop"):
            await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")
        self.assertFalse(self.e.running)
        self.assertFalse(self.e.orders())

    async def test_uncertain_submission_never_retried_and_requires_reconcile(self):
        self.e.kraken.add = AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(SafetyError):
            await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")
        self.e.kraken.add.assert_awaited_once()
        self.assertTrue(self.e.recovery_required)
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertEqual(self.e.orders()[0]["status"], "uncertain")


class DiscordTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_host_no_redirect_retry_mentions_or_secret_in_snapshot(self):
        for url in (
            "http://discord.com/api/webhooks/1/x",
            "https://evil.invalid/api/webhooks/1/x",
            "https://discord.com/api/webhooks/1/x?redirect=evil",
        ):
            a = DiscordAlerts(Session({}), url)
            a.send("halted")
            self.assertIsNone(a.task)
        url = "https://discord.com/api/webhooks/123/fixture-only-secret"
        session = Session({}, 429)
        a = DiscordAlerts(session, url)
        a.send("halted")
        await a.task
        self.assertEqual(len(session.calls), 1)
        args, kw = session.calls[0]
        self.assertFalse(kw["allow_redirects"])
        self.assertEqual(kw["json"]["allowed_mentions"], {"parse": []})
        self.assertNotIn(url, str(a.snapshot()))
        self.assertIn("429", a.snapshot()["status"])
        await a.close()
