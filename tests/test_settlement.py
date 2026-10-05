import copy
import unittest
from dataclasses import replace
from decimal import ROUND_DOWN
from unittest.mock import AsyncMock, patch

from kairos import htf, settlement
from kairos.domain import SafetyError, dec
from tests import test_alpaca as paper


class SettlementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = paper.AlpacaEngineTests()
        await self.case.asyncSetUp()
        self.addAsyncCleanup(self.case.asyncTearDown)
        self.e, self.b = self.case.engine, self.case.broker
        self.b.clock = lambda: self.e.clock()
        pair = self.e.resolve("alpaca:BTC/USD")
        self.e.kraken.pairs[pair.id] = replace(pair, lot=dec("1e-9"))
        await self.e.configure({**self.e.settings, "strategy": "htf", "order_size": "25"})
        await self.case.start()
        await self.e.stop()
        self.request = self.b.request
        self.fees = []

        async def delayed(method, path, **kw):
            if method == "POST":
                self.assertTrue(self.e.kraken.order_guard())
                self.assertTrue(self.e.kraken.order_filter(path, kw["payload"]))
                if self.e.fee_settlement_pending:
                    for changed in (
                        {"side": "buy" if kw["payload"]["side"] == "sell" else "sell"},
                        {"qty": "1"},
                        {"extended_hours": True},
                        {"extra": "unapproved"},
                    ):
                        self.assertFalse(
                            self.e.kraken.order_filter(path, {**kw["payload"], **changed})
                        )
                kw["before_send"]()
            result = await self.request(method, path, **kw)
            if method == "POST":
                row = self.b.orders[result["id"]]
                if row["side"] == "buy":
                    amount = settlement.rounded(dec(row["filled_qty"]) * dec(".0015"), dec("1e-9"))
                    self.b.holdings["BTC/USD"] -= amount
                    fee = {
                        "activity_type": "CFEE",
                        "symbol": "BTCUSD",
                        "qty": str(-amount),
                        "net_amount": "0",
                    }
                else:
                    amount = settlement.rounded(
                        dec(row["filled_qty"]) * dec(row["filled_avg_price"]) * dec(".0025"),
                        dec(".01"),
                    )
                    self.b.cash -= amount
                    fee = {"activity_type": "FEE", "net_amount": str(-amount)}
                self.b.cash = self.b.cash.quantize(dec(".01"), rounding=ROUND_DOWN)
                self.fees.append(
                    {
                        "id": "fee-" + row["id"],
                        "status": "executed",
                        "date": settlement.native_day(paper.iso(self.e.clock())),
                        **fee,
                    }
                )
            return result

        self.e.kraken.request.side_effect = delayed

    async def round_trip(self):
        with patch("kairos.alpaca_engine.asyncio.sleep", new=AsyncMock()):
            await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")

    async def test_normal_round_trip_waits_for_real_fees_and_never_double_debits(self):
        await self.round_trip()
        q = self.e.store.get("paper-qualification")
        self.assertEqual(q["status"], "execution complete; fee settlement pending")
        self.assertTrue(q["execution_complete"])
        self.assertEqual(self.b.holdings["BTC/USD"], 0)
        self.assertEqual(self.e.balance("BTC"), 0)
        self.assertEqual(self.e.balance("USD"), self.b.cash - dec(9500))
        self.assertEqual(self.e.ledger()["fees"], {"USD": "0"})
        state = copy.deepcopy(self.e.store.get(settlement.KEY))
        self.assertGreater(dec(state["debits"]["BTC"]), 0)
        self.assertGreater(dec(state["debits"]["USD"]), 0)
        balances = copy.deepcopy(self.e.ledger()["balances"])
        for _ in range(2):
            await self.e.reconcile_account()
        self.assertEqual(self.e.ledger()["balances"], balances)
        self.assertEqual(self.e.store.get(settlement.KEY)["id"], state["id"])
        self.b.activities.extend(self.fees)
        await self.e.reconcile()
        self.assertEqual(self.e.store.get("paper-qualification")["status"], "complete")
        self.assertFalse(self.e.fee_settlement_pending)
        self.assertEqual(self.e.balance("USD"), self.b.cash - dec(9500))
        self.assertEqual(self.e.balance("BTC"), 0)
        self.assertEqual(dec(self.e.ledger()["fees"]["BTC"]), -dec(self.fees[0]["qty"]))
        self.assertEqual(dec(self.e.ledger()["fees"]["USD"]), -dec(self.fees[1]["net_amount"]))
        ledger = copy.deepcopy(self.e.ledger())
        await self.e.reconcile()
        self.assertEqual(self.e.ledger(), ledger)
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_pending_fees_do_not_allow_pyramiding_or_veto_owned_hard_exits(self):
        # Stop the normal check after entry reconciliation, preserving the actual
        # buy and its net ownership; then exercise the ordinary strategy exit path.
        original = self.e.place

        async def entry_only(pair, side, *args, **kw):
            if side == "sell":
                raise SafetyError("fixture interruption before exit")
            return await original(pair, side, *args, **kw)

        with (
            patch.object(self.e, "place", side_effect=entry_only),
            patch("kairos.alpaca_engine.asyncio.sleep", new=AsyncMock()),
            self.assertRaisesRegex(SafetyError, "fixture interruption"),
        ):
            await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")
        await self.e.reconcile()
        self.assertEqual(
            htf.owned(self.e, htf.snapshot(self.e)["position"]), self.b.holdings["BTC/USD"]
        )
        self.e.running = self.e.paper_armed = True
        pair = self.e.resolve("alpaca:BTC/USD")
        book = await self.e.kraken.book(pair)
        with self.assertRaisesRegex(SafetyError, "no pyramiding"):
            await self.e.place(pair, "buy", dec(".01"), book.bids[0][0], book, maker=True)
        self.assertEqual(len(self.e.orders()), 1)
        state = htf.snapshot(self.e)
        state["position"]["deadline"] = self.e.clock() - 1
        self.e.store.put(htf.key(self.e), state)
        self.e.htf_review.refresh = AsyncMock(
            side_effect=AssertionError("hard exit must not need bars")
        )
        await self.e.tick()
        self.assertEqual(self.e.orders()[-1]["side"], "sell")
        self.assertTrue(self.e.orders()[-1]["exit_only"])
        self.assertEqual(self.b.holdings["BTC/USD"], 0)
        self.assertTrue(self.e.running)
        self.assertEqual(self.e.operations.status, "settling-fees")
        self.e.jev.decide.assert_not_awaited()
        await self.e.stop()
        await self.e.tick()
        self.assertEqual(len(self.e.orders()), 2)
        self.assertFalse(self.e.running)
        self.assertFalse(self.e.kraken.order_guard())

    async def test_pending_evidence_failures_never_expand_or_reclassify_the_debit(self):
        await self.round_trip()
        ledger = copy.deepcopy(self.e.ledger())
        state = copy.deepcopy(self.e.store.get(settlement.KEY))
        for fault in (
            "cash",
            "credit",
            "positions",
            "activity",
            "missing_fill",
            "changed_reads",
            "order",
        ):
            with self.subTest(fault=fault):
                reads = 0

                async def bad(method, path, fault=fault, **kw):
                    nonlocal reads
                    value = await self.request(method, path, **kw)
                    if path == "/v2/account":
                        reads += 1
                        if fault == "cash":
                            value["cash"] = str(dec(value["cash"]) - 1)
                        if fault == "credit":
                            value["cash"] = str(dec(value["cash"]) + 1)
                        if fault == "changed_reads":
                            value["cash"] = str(dec(value["cash"]) - dec(".001") * reads)
                    if path == "/v2/positions" and fault == "positions":
                        value = [
                            {"symbol": "BTCUSD", "side": "long", "qty": "1", "current_price": "100"}
                        ]
                    if path == "/v2/account/activities":
                        if fault == "activity":
                            value.append(
                                {"id": "external", "activity_type": "JNLC", "net_amount": ".01"}
                            )
                        if fault == "missing_fill":
                            value = [r for r in value if r["activity_type"] != "FILL"]
                    if path == "/v2/orders" and fault == "order":
                        value[0]["client_order_id"] = "foreign"
                    return value

                self.e.kraken.request.side_effect = bad
                with self.assertRaises(SafetyError):
                    await self.e.reconcile_account()
                self.assertEqual(self.e.ledger(), ledger)
                self.assertEqual(self.e.store.get(settlement.KEY), state)

    async def test_restart_expiry_and_closed_epoch_cannot_grant_new_permission(self):
        await self.round_trip()
        state = self.e.store.get(settlement.KEY)
        await self.e.initialize()
        self.assertTrue(self.e.recovery_required)
        self.assertFalse(self.e.running or self.e.paper_armed)
        await self.e.reconcile_account()
        self.assertEqual(self.e.store.get(settlement.KEY)["reserves"], state["reserves"])
        self.e.clock = lambda: state["deadline"] + 1
        with self.assertRaisesRegex(SafetyError, "48 hours"):
            await self.e.reconcile_account()
        self.b.activities.extend(self.fees)
        await self.e.reconcile()  # Actual records, not a timeout reset, settle the epoch.
        self.b.cash -= dec(".001")
        with self.assertRaisesRegex(SafetyError, "without a new confirmed fill"):
            await self.e.reconcile_account()
        self.assertEqual(len(self.e.orders()), 2)

    async def test_post_fix_authorization_is_bound_once_to_settled_historical_recovery(self):
        from kairos.qualification_exit import CONFIRMATION, recover
        from tests.test_qualification_exit import RecoveryTests

        old = RecoveryTests()
        await old.asyncSetUp()
        self.addAsyncCleanup(old.case.asyncTearDown)
        e, b = old.e, old.broker
        await recover(e, old.q["id"], CONFIRMATION)
        await e.reconcile()
        with self.assertRaises(SafetyError):
            await e.qualify_paper("ONE POST-FIX PAPER QUALIFICATION", old.q["id"])
        self.assertIsNone(e.store.get("paper-qualification-recheck"))
        b.cash -= dec(".01")
        b.activities.extend(
            [
                {
                    "id": "old-base-fee",
                    "activity_type": "CFEE",
                    "symbol": "BTCUSD",
                    "qty": str(-old.debit),
                    "net_amount": "0",
                    "status": "executed",
                },
                {
                    "id": "old-sell-fee",
                    "activity_type": "FEE",
                    "net_amount": "-.01",
                    "status": "executed",
                },
            ]
        )
        await e.reconcile()
        archived = copy.deepcopy(e.store.get("paper-qualification:" + old.q["id"]))
        with self.assertRaises(SafetyError):
            await e.qualify_paper("ONE POST-FIX PAPER QUALIFICATION", "wrong-id")
        e.kraken.request.side_effect = old.request
        b.charge_crypto_fees = True
        with patch("kairos.alpaca_engine.asyncio.sleep", new=AsyncMock()):
            await e.qualify_paper("ONE POST-FIX PAPER QUALIFICATION", old.q["id"])
        new = e.store.get("paper-qualification")
        self.assertEqual(new["after_recovery_of"], old.q["id"])
        self.assertNotEqual(new["run_id"], old.q["run_id"])
        self.assertEqual(new["entry_filled"], e.orders()[-2]["filled"])
        self.assertEqual(e.store.get("paper-qualification:" + old.q["id"]), archived)
        self.assertEqual(new["status"], "execution complete; fee settlement pending")
        self.assertEqual(len(e.orders()), 5)
        with self.assertRaises(SafetyError):
            await e.qualify_paper("ONE POST-FIX PAPER QUALIFICATION", old.q["id"])
        self.assertEqual(len(e.orders()), 5)

    async def test_legacy_complete_marker_cannot_start_trial_before_actual_fees_settle(self):
        from kairos.multibar import PROTOCOL_HASH

        await self.round_trip()
        q = self.e.store.get("paper-qualification")
        q["status"] = "complete"  # A pre-fix completion marker is not enough.
        self.e.store.put("paper-qualification", q)
        await self.e.reconcile()
        await self.e.configure(
            {
                **self.e.settings,
                "htf_policy": "multibar-v2",
                "candle_minutes": 60,
                "max_exposure": "100",
                "daily_loss": "12.50",
                "reinvest_profits": False,
                "recover_initial": False,
            }
        )
        with self.assertRaisesRegex(SafetyError, "fees must settle"):
            await self.e.start(confirmation="START ALPACA PAPER")
        self.assertIsNone(self.e.store.get("multibar-trial:" + PROTOCOL_HASH))
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_fee_arrival_between_reads_requires_two_new_matching_snapshots(self):
        await self.round_trip()
        reads = 0

        async def arrive(method, path, **kw):
            nonlocal reads
            if path == "/v2/account/activities":
                reads += 1
                if reads == 2:
                    self.b.activities.extend(self.fees)
            return await self.request(method, path, **kw)

        self.e.kraken.request.side_effect = arrive
        await self.e.reconcile()
        self.assertEqual(reads, 3)
        self.assertFalse(self.e.fee_settlement_pending)
        self.assertEqual(self.e.store.get("paper-qualification")["status"], "complete")
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_known_no_submit_rejection_is_retained_without_inventing_broker_history(self):
        from kairos.clients import ExchangeRejected

        async def reject(method, path, **kw):
            if method == "POST":
                raise ExchangeRejected("Known no-submit quote rejection")
            return await self.request(method, path, **kw)

        self.e.kraken.request.side_effect = reject
        with self.assertRaisesRegex(ExchangeRejected, "Known no-submit"):
            await self.round_trip()
        await self.e.reconcile()
        self.assertEqual(
            self.e.store.get("paper-qualification")["status"],
            "interrupted; manual reconciliation required",
        )
        self.assertFalse(self.e.fee_settlement_pending)
        self.assertEqual(self.e.orders()[0]["status"], "rejected")
        self.assertIsNone(self.e.orders()[0]["txid"])
        self.assertEqual(len(self.b.orders), 0)
        self.assertEqual(self.e.balance("USD"), 500)
        with self.assertRaises(SafetyError):
            await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")

    async def another_round_trip(self):
        import uuid

        self.e.running = self.e.paper_armed = True
        self.e.recovery_required = False
        pair = self.e.resolve("alpaca:BTC/USD")
        state = htf.snapshot(self.e)
        state["position"] = None
        state["entry_attempt"] = {
            "order_id": None,
            "position": {
                "id": str(uuid.uuid4()),
                "side": "buy",
                "entry_limit": "100",
                "stop": "97",
                "opened_at": self.e.clock(),
                "deadline": self.e.clock() + 86400,
                "exit_reason": None,
            },
        }
        self.e.store.put(htf.key(self.e), state)
        book = await self.e.kraken.book(pair)
        await self.e.place(pair, "buy", dec(".1"), dec("100"), book, maker=True)
        await self.e.reconcile_account()
        book = await self.e.kraken.book(pair)
        await self.e.place(pair, "sell", self.e.balance("BTC"), dec("99.9"), book, exit_only=True)
        await self.e.reconcile_account()
        self.e.running = self.e.paper_armed = False

    async def test_continued_trading_and_partial_next_day_posting_keep_old_deadline(self):
        await self.round_trip()
        initial = copy.deepcopy(self.e.store.get(settlement.KEY))
        original_fees = list(self.fees)
        self.e.clock = lambda: initial["since"] + 86400
        await self.another_round_trip()
        self.assertEqual(len(self.e.orders()), 4)
        self.assertEqual(self.e.store.get(settlement.KEY)["deadline"], initial["deadline"])
        snapshots = [r for r in self.e.store.history() if r["kind"] == "fee-day"]
        self.assertEqual(len(snapshots), 1)
        # Newer fees cannot erase the older pending debt or renew its deadline.
        self.b.activities.extend(self.fees[len(original_fees) :])
        self.e.next_account_check = 0
        await self.e.tick()
        self.assertTrue(self.e.fee_settlement_pending)
        self.assertEqual(self.e.store.get(settlement.KEY)["deadline"], initial["deadline"])
        balances = dict(self.e.ledger()["balances"])
        self.b.activities.extend(original_fees)
        self.e.next_account_check = 0
        await self.e.tick()
        self.assertFalse(self.e.fee_settlement_pending)
        self.assertEqual(self.e.ledger()["balances"], balances)
        self.assertFalse(self.e.running or self.e.paper_armed)
        ledger = self.e.ledger()
        self.e.next_account_check = 0
        await self.e.tick()
        self.assertEqual(self.e.ledger(), ledger)
        self.assertEqual(self.e.account_check_status["status"], "matched")

    async def test_older_fee_cannot_consume_newer_periods_observed_debit(self):
        await self.round_trip()
        self.e.clock = lambda: self.e.orders()[0]["created"] + 86400
        await self.another_round_trip()
        rows = copy.deepcopy(self.fees)
        shift = -dec(rows[2]["qty"]) / 4
        rows[0]["qty"] = str(dec(rows[0]["qty"]) - shift)
        rows[2]["qty"] = str(dec(rows[2]["qty"]) + shift)
        # Aggregate fees still exactly match the broker debit and fit both caps.
        # Attribution across native days does not match and must not pass.
        self.b.activities.extend(rows)
        await self.e.tick()
        self.assertEqual(self.e.account_check_status["status"], "blocked")
        self.assertIn("period", self.e.last_error)
        self.assertTrue(self.e.recovery_required)
        self.assertTrue(self.e.fee_settlement_pending)
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_old_partial_fee_cannot_use_newer_days_cash_rounding_allowance(self):
        await self.round_trip()
        self.e.clock = lambda: self.e.orders()[0]["created"] + 86400
        await self.another_round_trip()
        partial = {
            **self.fees[1],
            "id": "partial-usd",
            "net_amount": str(dec(self.fees[1]["net_amount"]) + dec(".02")),
        }
        # Posting/creation can happen on another day; native fee date is retained.
        partial["created_at"] = paper.iso(self.e.clock())
        self.b.activities.extend([self.fees[0], *self.fees[2:], partial])
        await self.e.tick()
        self.assertTrue(self.e.fee_settlement_pending)
        old = self.e.store.get(settlement.KEY)["periods"][partial["date"]]
        self.assertEqual(old["status"], "pending")
        self.b.activities.append({**partial, "id": "remaining-usd", "net_amount": "-.02"})
        self.e.next_account_check = 0
        await self.e.tick()
        self.assertFalse(self.e.fee_settlement_pending)
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_undated_fee_cannot_be_assigned_across_overlapping_native_days(self):
        await self.round_trip()
        self.e.clock = lambda: self.e.orders()[0]["created"] + 86400
        await self.another_round_trip()
        self.b.activities.extend({k: v for k, v in f.items() if k != "date"} for f in self.fees)
        await self.e.tick()
        self.assertEqual(self.e.account_check_status["status"], "blocked")
        self.assertIn("unambiguous native settlement period", self.e.last_error)
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_paused_automatic_match_completes_qualification_without_writes_or_start(self):
        await self.round_trip()
        self.b.activities.extend(self.fees)
        self.b.calls.clear()
        await self.e.tick()
        self.assertEqual(self.e.store.get("paper-qualification")["status"], "complete")
        self.assertFalse(self.e.recovery_required or self.e.running or self.e.paper_armed)
        self.assertTrue(all(method == "GET" for method, _, _ in self.b.calls))
        count = len(self.b.calls)
        await self.e.tick()
        self.assertEqual(len(self.b.calls), count)

    async def test_automatic_exceptions_and_matches_notify_once_while_already_halted(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        await self.round_trip()
        await self.e.tick()
        alerts = SimpleNamespace(
            send=Mock(), snapshot=lambda: {"configured": True}, close=AsyncMock()
        )
        self.e.operations.alerts = alerts
        self.b.cash -= dec("1")
        self.e.next_account_check = 0
        await self.e.tick()
        alerts.send.assert_called_once_with("automatic accounting blocked; investigation required")
        self.e.next_account_check = 0
        await self.e.tick()
        self.assertEqual(alerts.send.call_count, 1)
        self.b.cash += dec("1")
        self.b.activities.extend(self.fees)
        self.e.next_account_check = 0
        await self.e.tick()
        alerts.send.assert_called_with("account verified; trading remains paused")
        self.e.next_account_check = 0
        await self.e.tick()
        self.assertEqual(alerts.send.call_count, 2)
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_next_round_trip_cannot_exhaust_pending_fee_headroom(self):
        await self.round_trip()
        state = self.e.store.get(settlement.KEY)
        price = dec("100.1")
        observed = dec(state["debits"]["USD"]) + dec(state["debits"]["BTC"]) * price
        state["unposted_reserve"] = {"USD": str(dec("12.49") - observed)}
        self.e.store.put(settlement.KEY, state)
        self.assertEqual(settlement.entry_budget(self.e, price), 0)

    async def test_mismatch_blocks_without_reset_and_later_match_does_not_resume(self):
        await self.round_trip()
        self.b.cash -= dec("1")
        ledger = self.e.ledger()
        await self.e.tick()
        self.assertEqual(self.e.account_check_status["status"], "blocked")
        self.assertTrue(self.e.recovery_required)
        self.assertEqual(self.e.ledger(), ledger)
        self.b.cash += dec("1")
        self.b.activities.extend(self.fees)
        self.e.next_account_check = 0
        await self.e.tick()
        self.assertFalse(self.e.running or self.e.paper_armed or self.e.recovery_required)
        self.assertEqual(self.e.account_check_status["status"], "matched")

    async def test_unposted_allowances_are_not_fees_and_reduce_entry_cash_and_risk_equity(self):
        await self.round_trip()
        book = await self.e.kraken.book(self.e.resolve("alpaca:BTC/USD"))
        reserve = settlement.reserve_usd(self.e, book.asks[0][0])
        self.assertGreater(reserve, 0)
        self.assertEqual(
            settlement.entry_budget(self.e, book.asks[0][0]), self.e.balance("USD") - reserve
        )
        await self.e.valuation()
        self.assertEqual(dec(self.e.equity), self.e.balance("USD") - reserve)
        self.assertEqual(self.e.ledger()["fees"], {"USD": "0"})
        state = self.e.store.get(settlement.KEY)
        state["unposted_reserve"]["USD"] = "13"
        self.e.store.put(settlement.KEY, state)
        with self.assertRaisesRegex(SafetyError, "pending-fee risk limit"):
            settlement.entry_budget(self.e, book.asks[0][0])

    async def test_new_fill_cannot_be_declared_paid_by_existing_currency_fee_totals(self):
        await self.round_trip()
        # Settle the buy fee only; an old positive BTC fee must not cover a new buy.
        self.b.activities.append(self.fees[0])
        await self.e.reconcile_account()
        # The fixture normally debits immediately. Delay the new BTC debit too.
        self.e.kraken.request.side_effect = self.request
        await self.another_round_trip()
        self.b.activities.append(self.fees[1])
        await self.e.reconcile_account()
        self.assertTrue(self.e.fee_settlement_pending)
        self.assertGreater(dec(self.e.store.get(settlement.KEY)["unposted_reserve"]["BTC"]), 0)

    async def test_background_uncertain_order_reads_never_cancel_or_resubmit(self):
        await self.round_trip()
        self.e.running = self.e.paper_armed = True
        self.e.recovery_required = False
        self.b.fill = False
        state = htf.snapshot(self.e)
        state["entry_attempt"] = {
            "order_id": None,
            "position": {
                "id": "pending-entry",
                "side": "buy",
                "entry_limit": "100",
                "stop": "97",
                "opened_at": self.e.clock(),
                "deadline": self.e.clock() + 86400,
                "exit_reason": None,
            },
        }
        self.e.store.put(htf.key(self.e), state)
        pair = self.e.resolve("alpaca:BTC/USD")
        book = await self.e.kraken.book(pair)
        await self.e.place(pair, "buy", dec(".01"), dec("100"), book, maker=True)
        order = self.e.orders()[-1]
        order["status"] = "uncertain"
        self.e.store.save_order(order)
        self.e.running = self.e.paper_armed = False
        self.b.calls.clear()
        await self.e.tick()
        self.assertEqual(self.e.account_check_status["status"], "blocked")
        self.assertTrue(self.e.recovery_required)
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertTrue(all(method == "GET" for method, _, _ in self.b.calls))
        self.assertEqual(len(self.b.orders), 3)

    async def test_stop_during_automatic_check_cannot_restore_execution_permission(self):
        import asyncio

        await self.round_trip()
        self.b.activities.extend(self.fees)
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.e.kraken.account

        async def held():
            entered.set()
            await release.wait()
            return await original()

        self.e.kraken.account = held
        check = asyncio.create_task(self.e.check_account_automatically())
        await entered.wait()
        stop = asyncio.create_task(self.e.stop())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(check, stop)
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertFalse(self.e.kraken.order_guard())
        self.assertEqual(len(self.e.orders()), 2)

    async def test_missing_debit_preserves_native_allowance_until_real_debit_and_fee_arrive(self):
        # A maker fill without a visible base debit must not sell its possible fee.
        self.e.kraken.request.side_effect = self.request
        await self.round_trip()
        retained = self.e.balance("BTC")
        self.assertGreater(retained, 0)
        self.assertEqual(retained, settlement.retained_base(self.e))
        self.assertTrue(self.e.store.get(settlement.KEY)["unwitnessed"])
        buy, sell = self.e.orders()
        btc_fee = dec(buy["filled"]) * dec(".0015")
        usd_fee = dec(sell["cost"]) * dec(".0025")
        self.b.holdings["BTC/USD"] -= btc_fee
        self.b.cash -= usd_fee
        self.b.activities.extend(
            [
                {
                    "id": "late-buy",
                    "activity_type": "CFEE",
                    "status": "executed",
                    "symbol": "BTCUSD",
                    "qty": str(-btc_fee),
                    "net_amount": "0",
                },
                {
                    "id": "late-sell",
                    "activity_type": "FEE",
                    "status": "executed",
                    "net_amount": str(-usd_fee),
                },
            ]
        )
        await self.e.tick()
        self.assertFalse(self.e.fee_settlement_pending)
        self.assertEqual(settlement.retained_base(self.e), 0)
        self.assertEqual(self.e.balance("BTC"), retained - btc_fee)
        self.assertEqual(self.e.store.get("paper-qualification")["status"], "residual retained")
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_fee_activity_before_balance_debit_recovers_without_reclassification_or_writes(
        self,
    ):
        self.e.kraken.request.side_effect = self.request
        await self.round_trip()
        buy, sell = self.e.orders()
        btc_fee, usd_fee = dec(buy["filled"]) * dec(".0015"), dec(sell["cost"]) * dec(".0025")
        self.b.activities.extend(
            [
                {
                    "id": "early-buy",
                    "activity_type": "CFEE",
                    "status": "executed",
                    "symbol": "BTCUSD",
                    "qty": str(-btc_fee),
                    "net_amount": "0",
                },
                {
                    "id": "early-sell",
                    "activity_type": "FEE",
                    "status": "executed",
                    "net_amount": str(-usd_fee),
                },
            ]
        )
        self.b.calls.clear()
        await self.e.tick()
        self.assertEqual(self.e.account_check_status["status"], "blocked")
        posted = self.e.ledger()["fees"]
        self.b.holdings["BTC/USD"] -= btc_fee
        self.b.cash -= usd_fee
        self.e.next_account_check = 0
        await self.e.tick()
        self.assertEqual(self.e.account_check_status["status"], "matched")
        self.assertEqual(self.e.ledger()["fees"], posted)
        self.assertFalse(self.e.fee_settlement_pending)
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertTrue(all(method == "GET" for method, _, _ in self.b.calls))

    async def test_v073_pending_migration_preserves_already_posted_fee_coverage(self):
        await self.round_trip()
        self.b.activities.append(self.fees[0])
        await self.e.reconcile_account()
        state = self.e.store.get(settlement.KEY)
        for key in (
            "unwitnessed",
            "witnessed",
            "observed",
            "coverage_floor",
            "unposted_reserve",
            "utc_day",
            "periods",
            "baseline_fee_ids",
        ):
            state.pop(key, None)
        for reserve in state["reserves"].values():
            reserve.pop("transaction_time", None)
            reserve.pop("period", None)
        self.e.store.put(settlement.KEY, state)
        self.b.activities.append(self.fees[1])
        await self.e.tick()
        self.assertFalse(self.e.fee_settlement_pending)
        self.assertEqual(self.e.store.get("paper-qualification")["status"], "complete")
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_actual_fee_corrections_fail_closed(self):
        await self.round_trip()
        self.b.activities.extend(self.fees)
        await self.e.reconcile()
        self.b.activities[-1]["net_amount"] = "-.09"
        with self.assertRaisesRegex(SafetyError, "corrected"):
            await self.e.reconcile_account()

    async def test_late_postings_do_not_change_gross_fill_or_protective_plan(self):
        original = self.e.place

        async def hold(pair, side, *args, **kw):
            if side == "sell":
                raise SafetyError("fixture hold")
            return await original(pair, side, *args, **kw)

        with (
            patch.object(self.e, "place", side_effect=hold),
            patch("kairos.alpaca_engine.asyncio.sleep", new=AsyncMock()),
            self.assertRaises(SafetyError),
        ):
            await self.e.qualify_paper("ONE ALPACA PAPER ROUND TRIP")
        plan = copy.deepcopy(htf.snapshot(self.e)["position"])
        order = copy.deepcopy(self.e.orders()[0])
        self.b.activities.extend(self.fees)
        await self.e.reconcile()
        self.assertEqual(htf.snapshot(self.e)["position"], plan)
        self.assertEqual(self.e.orders()[0], order)
        self.assertEqual(htf.owned(self.e, plan), self.b.holdings["BTC/USD"])
        self.assertEqual(
            self.e.store.get("paper-qualification")["status"],
            "interrupted; manual reconciliation required",
        )
