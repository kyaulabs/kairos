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
                    self.assertEqual(kw["payload"]["side"], "sell")
                    for changed in (
                        {"side": "buy"},
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
                self.fees.append({"id": "fee-" + row["id"], "status": "executed", **fee})
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

    async def test_pending_entries_rejected_but_owned_hard_exit_needs_no_model_or_bars(self):
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
        with self.assertRaisesRegex(SafetyError, "only tracked reductions"):
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
        self.assertEqual(self.e.operations.status, "waiting-fees")
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
