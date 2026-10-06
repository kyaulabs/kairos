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

    async def test_usd_currency_metadata_does_not_convert_native_crypto_fee_to_cash(self):
        await self.round_trip()
        before = copy.deepcopy(self.e.ledger()["balances"])
        for fee in self.fees:
            self.b.activities.append({**fee, "activity_type": "CFEE", "currency": "USD"})
        self.b.calls.clear()
        await self.e.reconcile()
        self.assertEqual(self.e.ledger()["balances"], before)
        self.assertEqual(dec(self.e.ledger()["fees"]["BTC"]), -dec(self.fees[0]["qty"]))
        self.assertEqual(dec(self.e.ledger()["fees"]["USD"]), -dec(self.fees[1]["net_amount"]))
        self.assertFalse(self.e.fee_settlement_pending)
        ledger = copy.deepcopy(self.e.ledger())
        await self.e.reconcile_account()
        self.assertEqual(self.e.ledger(), ledger)
        self.assertTrue(all(method == "GET" for method, _, _ in self.b.calls))
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_fee_currency_fix_retains_credit_foreign_currency_and_dual_debit_guards(self):
        await self.round_trip()
        before = copy.deepcopy(self.e.ledger())
        suspense = copy.deepcopy(self.e.store.get(settlement.KEY))
        base = {**self.fees[0], "currency": "USD"}
        for changes in (
            {"qty": "0.000000001"},
            {"currency": "EUR"},
            {"currency": "BTCUSD"},
            {"net_amount": "-.01"},
            {"symbol": "UNKNOWN"},
            {"status": "pending"},
        ):
            with self.subTest(changes=changes), self.assertRaises(SafetyError):
                self.e.apply_fee({**base, **changes})
        for changes in ({"net_amount": ".01"}, {"currency": "BTC"}, {"currency": "EUR"}):
            with self.subTest(changes=changes), self.assertRaises(SafetyError):
                self.e.apply_fee({**self.fees[1], "activity_type": "CFEE", **changes})
        self.assertEqual(self.e.ledger(), before)
        self.assertEqual(self.e.store.get(settlement.KEY), suspense)
        self.assertIsNone(self.e.store.get("alpaca-activity:" + base["id"]))

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

    async def test_restart_and_overdue_records_warn_without_granting_permission(self):
        await self.round_trip()
        state = self.e.store.get(settlement.KEY)
        await self.e.initialize()
        self.assertTrue(self.e.recovery_required)
        self.assertFalse(self.e.running or self.e.paper_armed)
        await self.e.reconcile_account()
        self.assertEqual(self.e.store.get(settlement.KEY)["reserves"], state["reserves"])
        self.e.clock = lambda: state["deadline"] + 1
        self.b.calls.clear()
        await self.e.tick()
        overdue = self.e.store.get(settlement.KEY)
        self.assertTrue(overdue["overdue"])
        self.assertEqual(overdue["deadline"], state["deadline"])
        self.assertEqual(overdue["reserves"], state["reserves"])
        self.assertGreater(settlement.entry_budget(self.e, dec(100)), 25)
        self.assertEqual(self.e.ledger()["fees"], {"USD": "0"})
        self.assertFalse(self.e.running or self.e.paper_armed or self.e.recovery_required)
        self.assertTrue(all(method == "GET" for method, _, _ in self.b.calls))
        await self.e.reconcile_account()
        reminders = [
            row
            for row in self.e.store.history()
            if row["kind"] == "fee-settlement" and "overdue" in row["data"].get("message", "")
        ]
        self.assertEqual(len(reminders), 1)
        self.b.activities.extend(self.fees)
        await self.e.reconcile()  # Actual records, not a timeout reset, settle the epoch.
        self.b.cash -= dec(".001")
        with self.assertRaisesRegex(SafetyError, "without a new confirmed fill"):
            await self.e.reconcile_account()
        self.assertEqual(len(self.e.orders()), 2)

    async def test_post_fix_authorization_is_bound_once_with_pending_or_settled_recovery(self):
        from kairos.multibar import PROTOCOL_HASH
        from kairos.qualification_exit import CONFIRMATION, recover
        from tests.test_qualification_exit import RecoveryTests

        for posted in (False, True):
            with self.subTest(posted=posted):
                old = RecoveryTests()
                await old.asyncSetUp()
                self.addAsyncCleanup(old.case.asyncTearDown)
                e, b = old.e, old.broker
                await recover(e, old.q["id"], CONFIRMATION)
                b.cash -= dec(".04")
                if posted:
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
                                "net_amount": "-.04",
                                "status": "executed",
                            },
                        ]
                    )
                await e.reconcile()
                self.assertEqual(e.fee_settlement_pending, not posted)
                self.assertFalse(e.recovery_required)
                archived = copy.deepcopy(e.store.get("paper-qualification:" + old.q["id"]))
                recovery = copy.deepcopy(e.store.get("paper-qualification-exit"))
                with self.assertRaises(SafetyError):
                    await e.qualify_paper("ONE POST-FIX PAPER QUALIFICATION", "wrong-id")
                self.assertIsNone(e.store.get("paper-qualification-recheck"))
                e.kraken.request.side_effect = old.request
                b.charge_crypto_fees = True
                with patch("kairos.alpaca_engine.asyncio.sleep", new=AsyncMock()):
                    await e.qualify_paper("ONE POST-FIX PAPER QUALIFICATION", old.q["id"])
                new = e.store.get("paper-qualification")
                self.assertEqual(new["after_recovery_of"], old.q["id"])
                self.assertEqual(new["fees_pending_before"], not posted)
                self.assertNotEqual(new["run_id"], old.q["run_id"])
                self.assertEqual(new["entry_filled"], e.orders()[-2]["filled"])
                self.assertEqual(e.store.get("paper-qualification:" + old.q["id"]), archived)
                self.assertEqual(e.store.get("paper-qualification-exit"), recovery)
                self.assertEqual(new["status"], "execution complete; fee settlement pending")
                self.assertTrue(e.qualification_execution_complete)
                self.assertFalse(e.running or e.paper_armed or e.recovery_required)
                self.assertIsNone(e.store.get("multibar-trial:" + PROTOCOL_HASH))
                self.assertEqual(len(e.orders()), 5)
                with self.assertRaises(SafetyError):
                    await e.qualify_paper("ONE POST-FIX PAPER QUALIFICATION", old.q["id"])
                self.assertEqual(len(e.orders()), 5)

    async def test_unsubmitted_postfix_failure_is_paused_and_new_authorization_is_one_use(self):
        from kairos.alpaca_transport import PendingAlpacaData
        from kairos.qualification_exit import CONFIRMATION, recover
        from tests.test_qualification_exit import RecoveryTests

        old = RecoveryTests()
        await old.asyncSetUp()
        self.addAsyncCleanup(old.case.asyncTearDown)
        e, b = old.e, old.broker
        await recover(e, old.q["id"], CONFIRMATION)
        await e.reconcile()
        e.kraken.request.side_effect = old.request
        b.charge_crypto_fees = True
        with patch.object(
            e,
            "execution_book",
            side_effect=PendingAlpacaData("Alpaca market data is stale (11.0s; limit 10s)"),
        ):
            with self.assertRaises(PendingAlpacaData):
                await e.qualify_paper("ONE POST-FIX PAPER QUALIFICATION", old.q["id"])
        failed = copy.deepcopy(e.store.get("paper-qualification"))
        first_claim = copy.deepcopy(e.store.get("paper-qualification-recheck"))
        self.assertEqual(len(e.orders()), 3)
        b.calls.clear()
        await e.tick()
        self.assertFalse(e.recovery_required or e.running or e.paper_armed)
        self.assertIsNone(e.last_error)
        self.assertTrue(all(method == "GET" for method, _, _ in b.calls))
        await e.configure({**e.settings, "htf_policy": "multibar-v2", "reinvest_profits": False})
        self.assertEqual(e.operations.status, "paused")
        self.assertIn("separately authorized", e.start_block_reason)
        await e.stop()
        self.assertIn("separately authorized", e.start_block_reason)
        with self.assertRaisesRegex(SafetyError, "separately authorized"):
            await e.start(restart=True, confirmation="RESTART ALPACA PAPER")
        self.assertEqual(e.store.get("paper-qualification"), failed)
        self.assertIn("11.0s", failed["failure_reason"])
        await e.configure({**e.settings, "htf_policy": "pullback-v1"})
        with self.assertRaises(SafetyError):
            await e.qualify_paper("ONE NO-SUBMISSION PAPER QUALIFICATION", "wrong-id")
        order = e.orders()[-1]
        changed = {**order, "run_id": failed["run_id"]}
        e.store.save_order(changed)
        with self.assertRaises(SafetyError):
            await e.qualify_paper("ONE NO-SUBMISSION PAPER QUALIFICATION", failed["id"])
        e.store.save_order(order)
        self.assertIsNone(e.store.get("paper-qualification-no-submit-recheck"))
        with patch("kairos.alpaca_engine.asyncio.sleep", new=AsyncMock()):
            await e.qualify_paper("ONE NO-SUBMISSION PAPER QUALIFICATION", failed["id"])
        self.assertEqual(len(e.orders()), 5)
        self.assertTrue(e.qualification_execution_complete)
        self.assertFalse(e.running or e.paper_armed)
        self.assertEqual(e.store.get("paper-qualification:" + failed["id"]), failed)
        self.assertEqual(e.store.get("paper-qualification-recheck"), first_claim)
        with self.assertRaises(SafetyError):
            await e.qualify_paper("ONE NO-SUBMISSION PAPER QUALIFICATION", failed["id"])
        self.assertEqual(len(e.orders()), 5)

    async def test_passive_quote_refresh_after_risk_reads_keeps_original_limit(self):
        valuation = self.e.valuation
        refreshed = False

        async def age_planner(enforce=False):
            nonlocal refreshed
            result = await valuation(enforce)
            if self.e.running and not self.e.orders() and not refreshed:
                old = self.e.kraken.market_data.rest_books["alpaca:BTC/USD"]
                fresh = copy.copy(old)
                old.received -= 11
                self.e.kraken.market_data.rest_books["alpaca:BTC/USD"] = fresh
                refreshed = True
            return result

        with patch.object(self.e, "valuation", side_effect=age_planner):
            await self.round_trip()
        self.assertTrue(refreshed)
        self.assertTrue(self.e.qualification_execution_complete)
        self.assertEqual(dec(self.e.orders()[0]["price"]), 100)
        self.assertEqual(len(self.e.orders()), 2)

    async def test_final_passive_quote_still_rejects_crossing_and_stop(self):
        for fault in ("crossing", "stop", "stale"):
            with self.subTest(fault=fault):
                case = SettlementTests()
                await case.asyncSetUp()
                self.addAsyncCleanup(case.case.asyncTearDown)
                e = case.e
                valuation = e.valuation
                changed = False

                async def late_change(enforce=False, valuation=valuation, e=e, fault=fault):
                    nonlocal changed
                    result = await valuation(enforce)
                    if e.running and not e.orders() and not changed:
                        old = e.kraken.market_data.rest_books["alpaca:BTC/USD"]
                        fresh = copy.copy(old)
                        old.received -= 11
                        if fault == "crossing":
                            fresh.bids = [[dec("99.8"), dec(100)]]
                            fresh.asks = [[dec("99.9"), dec(100)]]
                        elif fault == "stop":
                            e.running = False
                            e.stop_generation += 1
                        else:
                            fresh.received -= 11
                        e.kraken.market_data.rest_books["alpaca:BTC/USD"] = fresh
                        changed = True
                    return result

                with patch.object(e, "valuation", side_effect=late_change):
                    with self.assertRaises(SafetyError):
                        await case.round_trip()
                self.assertTrue(changed)
                self.assertFalse(e.orders())
                self.assertFalse(any(method == "POST" for method, _, _ in case.b.calls))
                self.assertFalse(e.running or e.paper_armed)

    async def test_qualification_refreshes_expired_planning_fees_before_its_exit(self):
        from kairos.fees import FeeUnavailable

        request = self.e.kraken.request.side_effect
        expired = False

        async def delayed_fill(method, path, **kw):
            nonlocal expired
            result = await request(method, path, **kw)
            if method == "POST" and kw["payload"]["side"] == "buy":
                pair = self.e.resolve("alpaca:BTC/USD")
                self.e.fees.rates[pair.id]["received"] -= 61
                self.e.fees.attempts[pair.id] -= 61
                with self.assertRaises(FeeUnavailable):
                    self.e.fees.rate(pair)
                expired = True
            return result

        self.e.kraken.request.side_effect = delayed_fill
        await self.round_trip()
        self.assertTrue(expired)
        self.assertTrue(self.e.qualification_execution_complete)
        self.assertEqual(len(self.e.orders()), 2)
        self.assertTrue(self.e.orders()[-1]["exit_only"])
        self.assertEqual(self.e.balance("BTC"), 0)
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertEqual(self.e.ledger()["fees"], {"USD": "0"})

    async def fee_interrupted_check(self, *, witnessed=True):
        from kairos.alpaca_transport import PendingAlpacaData
        from kairos.fees import FeeUnavailable
        from kairos.qualification_exit import CONFIRMATION, recover
        from tests.test_qualification_exit import RecoveryTests

        old = RecoveryTests()
        await old.asyncSetUp()
        self.addAsyncCleanup(old.case.asyncTearDown)
        e, b = old.e, old.broker
        await recover(e, old.q["id"], CONFIRMATION)
        await e.reconcile()
        original_recovery = copy.deepcopy(e.store.get("paper-qualification-exit"))
        e.kraken.request.side_effect = old.request
        b.charge_crypto_fees = witnessed
        with patch.object(
            e, "execution_book", side_effect=PendingAlpacaData("fixture expired quote")
        ):
            with self.assertRaises(PendingAlpacaData):
                await e.qualify_paper("ONE POST-FIX PAPER QUALIFICATION", old.q["id"])
        previous = e.store.get("paper-qualification")
        await e.tick()
        place = e.place

        async def fail_exit(pair, side, *args, **kw):
            if side == "sell":
                raise FeeUnavailable(
                    "Alpaca planning fees missing or stale; refresh before trading"
                )
            return await place(pair, side, *args, **kw)

        with patch.object(e, "place", side_effect=fail_exit):
            with self.assertRaises(FeeUnavailable):
                await e.qualify_paper("ONE NO-SUBMISSION PAPER QUALIFICATION", previous["id"])
        failed = copy.deepcopy(e.store.get("paper-qualification"))
        await e.configure({**e.settings, "htf_policy": "multibar-v2", "reinvest_profits": False})
        return e, b, original_recovery, failed

    async def test_fee_interrupted_recovery_is_one_sell_and_never_starts_or_passes_trial(self):
        from kairos.multibar import PROTOCOL_HASH
        from kairos.qualification_exit import FEE_CONFIRMATION, FEE_KEY, recover

        e, b, previous_recovery, failed = await self.fee_interrupted_check()
        self.assertEqual(len(e.orders()), 4)
        request = b.request

        async def guarded(method, path, **kw):
            if method == "POST":
                self.assertFalse(e.running or e.paper_armed)
                self.assertTrue(e.kraken.recovery_exit_guard(path, kw["payload"]))
                self.assertFalse(e.kraken.recovery_exit_guard(path, {**kw["payload"], "qty": "1"}))
            return await request(method, path, **kw)

        e.kraken.request.side_effect = guarded
        b.calls.clear()
        result = await recover(e, failed["id"], FEE_CONFIRMATION)
        self.assertEqual(sum(m == "POST" for m, _, _ in b.calls), 1)
        self.assertEqual(result["order_status"], "closed")
        self.assertEqual(b.holdings["BTC/USD"], 0)
        self.assertEqual(e.store.get("paper-qualification-exit"), previous_recovery)
        self.assertEqual(e.store.get("paper-qualification:" + failed["id"]), failed)
        self.assertIsNotNone(e.store.get(FEE_KEY))
        self.assertIsNone(e.store.get("multibar-trial:" + PROTOCOL_HASH))
        e.next_account_check = 0
        await e.tick()
        self.assertEqual(e.balance("BTC"), 0)
        self.assertFalse(e.running or e.paper_armed or e.recovery_required)
        self.assertFalse(e.qualification_execution_complete)
        self.assertEqual(e.operations.status, "paused")
        self.assertIn("separately authorized", e.start_block_reason)
        self.assertEqual(
            e.snapshot()["paper_qualification_recovery"]["qualification_id"], failed["id"]
        )
        with self.assertRaises(SafetyError):
            await recover(e, failed["id"], FEE_CONFIRMATION)
        self.assertEqual(len(e.orders()), 5)

    async def recovered_fee_check(self):
        from kairos.qualification_exit import FEE_CONFIRMATION, recover

        e, b, _, failed = await self.fee_interrupted_check()
        await recover(e, failed["id"], FEE_CONFIRMATION)
        e.next_account_check = 0
        await e.tick()
        await e.configure({**e.settings, "htf_policy": "pullback-v1"})
        return e, b, failed

    async def test_fee_fix_qualification_is_once_preserves_failures_and_does_not_start_trial(self):
        from kairos.multibar import PROTOCOL_HASH

        e, b, failed = await self.recovered_fee_check()
        previous = copy.deepcopy(e.store.get("paper-qualification"))
        retained = {
            key: copy.deepcopy(e.store.get(key))
            for key in (
                "paper-qualification-exit",
                "paper-qualification-fee-exit",
                "paper-qualification-recheck",
                "paper-qualification-no-submit-recheck",
                "paper-qualification:" + failed["id"],
            )
        }
        before = e.orders()
        request = b.request
        expired = False

        async def delayed_fill(method, path, **kw):
            nonlocal expired
            result = await request(method, path, **kw)
            if method == "POST" and kw["payload"]["side"] == "buy":
                e.fees.rates["alpaca:BTC/USD"]["received"] -= 61
                e.fees.attempts["alpaca:BTC/USD"] -= 61
                expired = True
            return result

        e.kraken.request.side_effect = delayed_fill
        await e.qualify_paper("ONE FEE-FIX PAPER QUALIFICATION", failed["id"])
        q = e.store.get("paper-qualification")
        self.assertTrue(expired and e.qualification_execution_complete)
        self.assertEqual(q["status"], "execution complete; fee settlement pending")
        self.assertEqual(q["after_recovery_of"], failed["id"])
        self.assertEqual(
            e.store.get("paper-qualification-fee-recheck"),
            {"id": q["id"], "previous_id": failed["id"]},
        )
        self.assertEqual(
            e.store.get("paper-qualification-before-fee-recheck:" + failed["id"]), previous
        )
        for key, value in retained.items():
            self.assertEqual(e.store.get(key), value, key)
        self.assertEqual(e.orders()[:5], before)
        self.assertEqual(len(e.orders()), 7)
        buy, sell = e.orders()[-2:]
        self.assertTrue(buy["maker"] and sell["exit_only"])
        reserve = next(
            r
            for r in e.store.get(settlement.KEY)["reserves"].values()
            if r["qty"] == buy["filled"] and r["currency"] == "BTC"
        )
        self.assertLessEqual(
            dec(buy["cost"]) + dec(reserve["cap"]) * dec(buy["price"]), dec(q["budget"])
        )
        self.assertEqual(e.balance("BTC"), 0)
        self.assertFalse(e.running or e.paper_armed)
        await e.configure({**e.settings, "htf_policy": "multibar-v2"})
        self.assertIsNone(e.start_block_reason)
        self.assertIsNone(e.store.get("multibar-trial:" + PROTOCOL_HASH))
        with self.assertRaises(SafetyError):
            await e.qualify_paper("ONE FEE-FIX PAPER QUALIFICATION", failed["id"])
        self.assertEqual(len(e.orders()), 7)

    async def test_fee_fix_qualification_rejects_wrong_lineage_and_consumes_interruption(self):
        from kairos.alpaca_transport import PendingAlpacaData

        e, b, failed = await self.recovered_fee_check()
        with self.assertRaises(SafetyError):
            await e.qualify_paper("ONE FEE-FIX PAPER QUALIFICATION", "wrong-id")
        original = e.store.get("paper-qualification-fee-exit")
        e.store.put("paper-qualification-fee-exit", {**original, "filled": "0"})
        with self.assertRaises(SafetyError):
            await e.qualify_paper("ONE FEE-FIX PAPER QUALIFICATION", failed["id"])
        self.assertIsNone(e.store.get("paper-qualification-fee-recheck"))
        e.store.put("paper-qualification-fee-exit", original)
        b.calls.clear()
        with patch.object(
            e, "execution_book", side_effect=PendingAlpacaData("fixture stale quote")
        ):
            with self.assertRaises(PendingAlpacaData):
                await e.qualify_paper("ONE FEE-FIX PAPER QUALIFICATION", failed["id"])
        self.assertFalse(any(method == "POST" for method, _, _ in b.calls))
        self.assertIsNotNone(e.store.get("paper-qualification-fee-recheck"))
        self.assertEqual(e.store.get("paper-qualification:" + failed["id"]), failed)
        self.assertFalse(e.running or e.paper_armed or e.qualification_execution_complete)
        with self.assertRaises(SafetyError):
            await e.qualify_paper("ONE FEE-FIX PAPER QUALIFICATION", failed["id"])
        self.assertEqual(len(e.orders()), 5)

    async def test_fee_recovery_keeps_unwitnessed_native_allowance_unsold(self):
        from kairos.qualification_exit import FEE_CONFIRMATION, recover

        e, b, _, failed = await self.fee_interrupted_check(witnessed=False)
        retained = settlement.retained_base(e)
        self.assertGreater(retained, 0)
        await recover(e, failed["id"], FEE_CONFIRMATION)
        self.assertEqual(b.holdings["BTC/USD"], retained)
        self.assertEqual(e.balance("BTC"), retained)
        self.assertFalse(e.running or e.paper_armed or e.qualification_execution_complete)
        self.assertTrue(e.recovery_required)

    async def test_fee_recovery_rejects_wrong_lineage_and_stop_without_another_sell(self):
        from kairos.clients import ExchangeRejected
        from kairos.qualification_exit import FEE_CONFIRMATION, FEE_KEY, recover

        e, b, _, failed = await self.fee_interrupted_check()
        with self.assertRaises(SafetyError):
            await recover(e, "wrong-id", FEE_CONFIRMATION)
        e.store.put("paper-qualification", {**failed, "failure_type": "TimeoutError"})
        with self.assertRaises(SafetyError):
            await recover(e, failed["id"], FEE_CONFIRMATION)
        self.assertIsNone(e.store.get(FEE_KEY))
        e.store.put("paper-qualification", failed)
        b.holdings["BTC/USD"] -= dec("1e-9")
        with self.assertRaisesRegex(SafetyError, "Broker evidence"):
            await recover(e, failed["id"], FEE_CONFIRMATION)
        b.holdings["BTC/USD"] += dec("1e-9")
        self.assertIsNone(e.store.get(FEE_KEY))
        request = b.request

        async def stopped(method, path, **kw):
            if method == "POST":
                e.stop_generation += 1
                self.assertFalse(e.kraken.recovery_exit_guard(path, kw["payload"]))
                raise ExchangeRejected("Stopped before submission")
            return await request(method, path, **kw)

        e.kraken.request.side_effect = stopped
        b.calls.clear()
        with self.assertRaises(ExchangeRejected):
            await recover(e, failed["id"], FEE_CONFIRMATION)
        self.assertFalse(any(m == "POST" for m, _, _ in b.calls))
        self.assertGreater(e.balance("BTC"), 0)
        self.assertIsNotNone(e.store.get(FEE_KEY))
        self.assertFalse(e.running or e.paper_armed)
        self.assertIsNone(e.kraken.recovery_exit_guard)
        with self.assertRaises(SafetyError):
            await recover(e, failed["id"], FEE_CONFIRMATION)

    async def select_multibar(self):
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

    async def test_completed_execution_permits_only_explicit_start_with_unposted_fees(self):
        from kairos.multibar import PROTOCOL_HASH

        await self.round_trip()
        q = self.e.store.get("paper-qualification")
        await self.e.tick()
        await self.select_multibar()
        self.assertIsNone(self.e.store.get("multibar-trial:" + PROTOCOL_HASH))
        self.assertFalse(self.e.running or self.e.paper_armed or self.e.recovery_required)
        self.assertIsNone(self.e.start_block_reason)
        for status, complete in (
            ("manual exit recovery; normal qualification incomplete", True),
            ("residual retained", True),
            ("no entry fill; round trip untested", False),
            ("execution complete; fee settlement pending", False),
        ):
            self.e.store.put(
                "paper-qualification", {**q, "status": status, "execution_complete": complete}
            )
            with self.assertRaisesRegex(SafetyError, "separately authorized"):
                await self.e.start(confirmation="START ALPACA PAPER")
            self.assertIsNone(self.e.store.get("multibar-trial:" + PROTOCOL_HASH))
        self.e.store.put("paper-qualification", q)
        with self.assertRaisesRegex(SafetyError, "Explicit START"):
            await self.e.start()
        await self.e.start(confirmation="START ALPACA PAPER")
        self.assertTrue(self.e.running and self.e.paper_armed)
        self.assertTrue(self.e.fee_settlement_pending)
        self.assertEqual(self.e.store.get("paper-qualification"), q)
        self.assertIsNotNone(self.e.store.get("multibar-trial:" + PROTOCOL_HASH))
        self.assertEqual(len(self.e.orders()), 2)
        await self.e.stop()

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
        self.e.clock = lambda: initial["since"] + 3 * 86400
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
        qualification = self.e.store.get("paper-qualification")
        self.assertNotEqual(qualification["cash_after"], str(self.e.balance("USD")))
        self.assertEqual(qualification["posted_fees_after"], {"USD": "0"})
        self.assertEqual(
            qualification["account_posted_fees_at_settlement"], self.e.ledger()["fees"]
        )
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

    async def test_verified_unposted_fees_clear_legacy_latch_without_writes_or_resume(self):
        await self.round_trip()
        self.e.recovery_required = True
        self.e.last_error = (
            "Actual crypto fee records pending; new entries blocked; engine remains paused"
        )
        self.b.calls.clear()
        await self.e.tick()
        state = self.e.snapshot()
        self.assertEqual(state["operations"]["status"], "paused")
        self.assertIsNone(state["start_block_reason"])
        self.assertIsNone(self.e.last_error)
        self.assertFalse(state["recovery_required"])
        self.assertFalse(self.e.running or self.e.paper_armed)
        self.assertTrue(all(method == "GET" for method, _, _ in self.b.calls))
        self.assertTrue(self.e.fee_settlement_pending)

    async def test_unposted_fees_cannot_hide_mismatch_loss_or_uncertain_orders(self):
        await self.round_trip()
        await self.e.tick()
        for error in ("Daily marked-to-market loss limit reached", "Audit/storage failure"):
            self.e.last_error = error
            self.e.update_operating_state()
            self.assertEqual(self.e.operations.status, "halted")
            self.assertIsNone(self.e.start_block_reason)
        self.e.last_error = None
        self.b.cash -= dec(1)
        self.e.next_account_check = 0
        await self.e.tick()
        self.assertEqual(self.e.operations.status, "halted")
        self.assertTrue(self.e.recovery_required)
        self.b.cash += dec(1)
        self.e.next_account_check = 0
        await self.e.tick()
        self.assertEqual(self.e.operations.status, "paused")
        order = self.e.orders()[-1]
        order["status"] = "uncertain"
        self.e.store.save_order(order)
        self.e.update_operating_state()
        self.assertEqual(self.e.operations.status, "halted")

    async def test_historical_recovery_wait_explains_remaining_post_fix_qualification(self):
        await self.round_trip()
        q = self.e.store.get("paper-qualification")
        q.update(
            status="manual exit recovery; normal qualification incomplete", execution_complete=False
        )
        self.e.store.put("paper-qualification", q)
        self.e.store.put(
            "paper-qualification-exit",
            {"status": "exit attempted; reconciliation pending", "qualification_id": q["id"]},
        )
        await self.e.tick()
        await self.select_multibar()
        self.assertEqual(self.e.operations.status, "paused")
        self.assertIn(
            "separately authorized paper round trip", self.e.snapshot()["start_block_reason"]
        )
        self.assertIn("do not block qualification", self.e.snapshot()["start_block_reason"])
        with self.assertRaisesRegex(SafetyError, "separately authorized"):
            await self.e.start(confirmation="START ALPACA PAPER")
        self.assertEqual(self.e.store.get("paper-qualification"), q)
        self.assertIsNone(self.e.store.get("paper-qualification-recheck"))
        self.assertFalse(self.e.running or self.e.paper_armed)

    async def test_automatic_exceptions_and_matches_notify_once_while_already_halted(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        await self.round_trip()
        await self.e.tick()
        self.e.operations.set("halted", "fixture prior halt")
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
