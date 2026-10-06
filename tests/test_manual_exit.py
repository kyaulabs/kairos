import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from kairos import htf, manual_exit, settlement
from kairos.alpaca import iso
from kairos.alpaca_transport import PendingAlpacaData
from kairos.domain import SafetyError, dec
from kairos.multibar import PROTOCOL_HASH
from tests import test_settlement


class ManualExitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        helper = test_settlement.SettlementTests()
        self.e, self.b, previous = await helper.recovered_fee_check()
        for fn, args, kwargs in helper._cleanups:
            self.addAsyncCleanup(fn, *args, **kwargs)
        e = self.e
        place = e.place

        async def fail_exit(pair, side, *args, **kwargs):
            if side == "sell":
                raise PendingAlpacaData("fixture stale exit quote")
            return await place(pair, side, *args, **kwargs)

        with (
            patch("kairos.qualification_data.WAIT_SECONDS", 0.5),
            patch.object(e, "place", side_effect=fail_exit),
        ):
            with self.assertRaises(PendingAlpacaData):
                await e.qualify_paper("ONE FEE-FIX PAPER QUALIFICATION", previous["id"])
        self.failed = copy.deepcopy(e.store.get("paper-qualification"))
        await e.configure({**e.settings, "htf_policy": "multibar-v2"})
        # The operator liquidates through the broker, not through any Kairos method.
        # It happens on the next native day; original fee periods/deadlines survive.
        now = e.clock() + 86400
        e.clock = lambda: now
        self.b.clock = e.clock
        result = await self.b.request(
            "POST",
            "/v2/orders",
            payload={
                "symbol": "BTC/USD",
                "side": "sell",
                "type": "market",
                "qty": str(e.balance("BTC")),
                "limit_price": "100",
                "client_order_id": "operator-external-sale",
                "time_in_force": "gtc",
            },
        )
        self.row = self.b.orders[result["id"]]
        self.row.update(type="market", limit_price=None, submitted_at=iso(now))
        self.e.kraken.request.side_effect = self.b.request
        self.b.calls.clear()
        self.before = copy.deepcopy(e.ledger())
        self.orders = copy.deepcopy(e.orders())
        self.settlement = copy.deepcopy(e.store.get(settlement.KEY))
        self.protection = copy.deepcopy(htf.snapshot(e)["position"])

    async def confirm(self, view=None):
        v = view or await manual_exit.preview(self.e)
        return await manual_exit.reconcile(
            self.e,
            v["qualification_id"],
            v["broker_order_id"],
            v["evidence_hash"],
            manual_exit.CONFIRMATION,
        )

    async def test_market_sale_requires_confirmation_then_reconciles_without_orders_or_trial(self):
        e = self.e
        with self.assertRaises(SafetyError):
            await e.reconcile_account()  # External orders are never adopted automatically.
        view = await manual_exit.preview(e)
        self.assertEqual(e.ledger(), self.before)
        self.assertEqual(e.orders(), self.orders)
        self.assertEqual(view["quantity"], self.row["filled_qty"])
        with self.assertRaises(SafetyError):
            await manual_exit.reconcile(
                e, view["qualification_id"], view["broker_order_id"], view["evidence_hash"], ""
            )
        with patch.object(
            e.kraken, "book", side_effect=AssertionError("Accounting must not need a live quote")
        ):
            await self.confirm(view)
        self.assertTrue(all(method == "GET" for method, _, _ in self.b.calls))
        self.assertEqual(e.balance("BTC"), 0)
        self.assertEqual(e.balance("USD"), self.b.cash - dec(9500))
        self.assertEqual(e.ledger()["fees"], self.before["fees"])
        self.assertEqual(e.ledger()["initial"], self.before["initial"])
        self.assertEqual(e.orders()[:6], self.orders)
        imported = e.orders()[-1]
        self.assertTrue(imported["externally_executed"])
        self.assertEqual(imported["broker_order_type"], "market")
        self.assertIsNone(imported["maker"])
        self.assertNotIn("planning_fee_bps", imported)
        self.assertNotIn("risk_at_submission", imported)
        self.assertEqual(e.store.get("paper-qualification:" + self.failed["id"]), self.failed)
        self.assertFalse(e.qualification_execution_complete)
        self.assertFalse(e.running or e.paper_armed or e.recovery_required)
        self.assertIsNone(e.store.get("multibar-trial:" + PROTOCOL_HASH))
        self.assertIsNone(htf.snapshot(e)["position"])
        archived = [
            r["data"]["position"]
            for r in e.store.history()
            if r["kind"] == "htf-position-closed"
            and r["data"]["position"]["id"] == self.failed["id"]
        ]
        self.assertEqual(archived, [self.protection])
        after = e.store.get(settlement.KEY)
        for key in ("id", "since", "deadline"):
            self.assertEqual(after[key], self.settlement[key])
        for key, reserve in self.settlement["reserves"].items():
            self.assertEqual(after["reserves"][key], reserve)
        self.assertTrue(e.fee_settlement_pending)
        self.assertIn("separately authorized", e.start_block_reason)
        await e.reconcile_account()  # The acknowledged market order is now verified normally.
        ledger = e.ledger()
        with self.assertRaises(SafetyError):
            await self.confirm(view)
        self.assertEqual(e.ledger(), ledger)
        self.assertEqual(len(e.orders()), 7)

    async def test_filled_external_limit_sale_is_supported_without_claiming_bot_execution(self):
        self.row.update(type="limit", limit_price="99.9")
        await self.confirm()
        order = self.e.orders()[-1]
        self.assertEqual(order["broker_order_type"], "limit")
        self.assertEqual(order["price"], "99.9")
        self.assertEqual(self.e.balance("BTC"), 0)
        self.assertTrue(all(m == "GET" for m, _, _ in self.b.calls))

    async def test_changed_confirmation_or_cash_evidence_never_imports(self):
        view = await manual_exit.preview(self.e)
        with self.assertRaises(SafetyError):
            await self.confirm({**view, "broker_order_id": "wrong-order"})
        self.b.cash -= dec(".01")
        with self.assertRaisesRegex(SafetyError, "preview"):
            await self.confirm(view)
        self.assertEqual(self.e.ledger(), self.before)
        self.assertEqual(self.e.orders(), self.orders)
        self.b.cash -= dec(1)
        with self.assertRaisesRegex(SafetyError, "cash difference"):
            await manual_exit.preview(self.e)
        self.assertIsNone(self.e.store.get(manual_exit.PREFIX + self.row["id"]))

    async def test_partial_wrong_side_extra_activity_or_changed_fill_is_rejected(self):
        original = copy.deepcopy(self.row)
        for changes in (
            {"side": "buy"},
            {"status": "partially_filled"},
            {"qty": "1"},
            {"symbol": "ETH/USD"},
        ):
            self.row.update(changes)
            with self.assertRaises(SafetyError):
                await manual_exit.preview(self.e)
            self.row.clear()
            self.row.update(original)
        self.b.activities.append({"id": "external-transfer", "activity_type": "JNLC"})
        with self.assertRaises(SafetyError):
            await manual_exit.preview(self.e)
        self.b.activities.pop()
        fill = next(f for f in self.b.activities if f.get("order_id") == self.row["id"])
        fill["price"] = "101"
        with self.assertRaisesRegex(SafetyError, "exact proceeds"):
            await manual_exit.preview(self.e)
        self.assertEqual(self.e.ledger(), self.before)
        self.assertEqual(self.e.orders(), self.orders)

    async def test_web_preview_is_read_only_and_confirmed_reconcile_stays_paused(self):
        from aiohttp.test_utils import TestClient, TestServer

        from kairos.web import command, create_app

        client = TestClient(TestServer(create_app(engine=self.e, origin="https://kairos.test")))
        await client.start_server()
        try:
            state = await (await client.get("/api/state")).json()
            self.assertTrue(state["manual_exit_reconciliation_available"])
            response = await client.get("/api/reconcile")
            self.assertEqual(response.status, 200)
            view = await response.json()
            self.assertEqual(self.e.ledger(), self.before)
            body = {
                "external_exit_order_id": view["broker_order_id"],
                "qualification_id": view["qualification_id"],
                "evidence_hash": view["evidence_hash"],
                "confirmation": manual_exit.CONFIRMATION,
            }
            response = await client.post("/api/reconcile", json=body)
            self.assertEqual(response.status, 403)
            response = await client.post(
                "/api/reconcile",
                json=body,
                headers={"Origin": "https://kairos.test", "X-CSRF-Token": state["csrf"]},
            )
            self.assertEqual(response.status, 200, await response.text())
            result = await response.json()
            self.assertFalse(result["manual_exit_reconciliation_available"])
            self.assertFalse(
                result["running"] or result["paper_armed"] or result["recovery_required"]
            )
            self.assertEqual(result["operations"]["status"], "paused")
            self.assertTrue(all(method == "GET" for method, _, _ in self.b.calls))
            other = SimpleNamespace(reconcile=AsyncMock())
            request = SimpleNamespace(
                app={"engine": other},
                match_info={"action": "reconcile"},
                json=AsyncMock(return_value=body),
            )
            with self.assertRaisesRegex(SafetyError, "Alpaca paper only"):
                await command(request)
            other.reconcile.assert_not_awaited()
        finally:
            await client.close()

    async def test_late_posted_fee_consumes_manual_suspense_once(self):
        await self.confirm()
        cash = self.e.balance("USD")
        fee = settlement.rounded(
            dec(self.row["filled_qty"]) * dec(self.row["filled_avg_price"]) * dec(".0025"),
            settlement.CENT,
        )
        self.b.activities.append(
            {
                "id": "manual-sale-fee",
                "activity_type": "FEE",
                "status": "executed",
                "date": settlement.native_day(iso(self.e.clock())),
                "net_amount": str(-fee),
            }
        )
        await self.e.reconcile_account()
        await self.e.reconcile_account()
        self.assertEqual(self.e.balance("USD"), cash)
        self.assertEqual(dec(self.e.ledger()["fees"]["USD"]), fee)
        self.assertFalse(self.e.qualification_execution_complete)
        self.assertEqual(len(self.e.orders()), 7)

    async def test_usd_labeled_crypto_fee_recovers_after_manual_sale_was_already_imported(self):
        buys = [f for f in self.b.activities if f.get("side") == "buy"]
        fee = {
            "id": "published-quantity-fee",
            "activity_type": "CFEE",
            "status": "executed",
            "currency": "USD",
            "symbol": "BTCUSD",
            "qty": self.protection["inventory_adjustment"],
            "net_amount": "0",
            "date": settlement.native_day(buys[-1]["transaction_time"]),
        }
        self.b.activities.append(fee)
        with patch.object(
            self.e, "apply_fee", side_effect=SafetyError("Unexpected Alpaca fee credit/currency")
        ):
            with self.assertRaises(SafetyError):
                await self.confirm()
            await self.e.check_account_automatically()
        self.assertEqual(len(self.e.orders()), 7)
        receipt = copy.deepcopy(self.e.store.get(manual_exit.PREFIX + self.row["id"]))
        self.assertIsNotNone(receipt)
        self.assertEqual(self.e.balance("BTC"), 0)
        self.e.next_account_check = 0
        await self.e.check_account_automatically()
        self.assertFalse(self.e.recovery_required or self.e.running or self.e.paper_armed)
        self.assertEqual(self.e.balance("USD"), self.b.cash - dec(9500))
        self.assertEqual(dec(self.e.ledger()["fees"]["BTC"]), -dec(fee["qty"]))
        ledger = self.e.ledger()
        await self.e.reconcile_account()
        self.assertEqual(self.e.ledger(), ledger)
        self.assertEqual(len(self.e.orders()), 7)
        self.assertEqual(self.e.store.get(manual_exit.PREFIX + self.row["id"]), receipt)
        self.assertEqual(self.e.store.get("paper-qualification:" + self.failed["id"]), self.failed)
        self.assertFalse(self.e.qualification_execution_complete)
        self.assertIsNone(self.e.store.get("multibar-trial:" + PROTOCOL_HASH))
        self.assertTrue(all(method == "GET" for method, _, _ in self.b.calls))

    async def test_old_witnessed_fee_publication_does_not_obscure_new_manual_sale_debit(self):
        # The fixture's first historical recovery had a fixed BTC debit and no
        # USD fee; subsequent fixture fills use the native rounded fee schedule.
        fills = list(self.b.activities)
        counts = {"buy": 0, "sell": 0}
        for fill in fills:
            side = fill["side"]
            counts[side] += 1
            qty, price = dec(fill["qty"]), dec(fill["price"])
            if side == "buy":
                amount = (
                    dec(".000000433")
                    if counts[side] == 1
                    else settlement.rounded(qty * dec(".0015"), dec("1e-9"))
                )
                debit = {"symbol": "BTCUSD", "qty": str(-amount), "net_amount": "0"}
            else:
                amount = (
                    dec(0)
                    if counts[side] == 1
                    else settlement.rounded(qty * price * dec(".0025"), settlement.CENT)
                )
                debit = {"net_amount": str(-amount)}
            self.b.activities.append(
                {
                    "id": "late-" + fill["id"],
                    "activity_type": "CFEE",
                    "status": "executed",
                    "currency": "USD",
                    "date": settlement.native_day(fill["transaction_time"]),
                    **debit,
                }
            )
        with patch(
            "kairos.settlement.period_coverage",
            side_effect=SafetyError("Balance debit cannot be isolated to one fee period"),
        ):
            with self.assertRaises(SafetyError):
                await self.confirm()
        paid = copy.deepcopy(self.e.ledger()["fees"])
        self.assertGreater(dec(paid["USD"]), 0)
        self.assertGreater(dec(paid["BTC"]), 0)
        receipt = self.e.store.get(manual_exit.PREFIX + self.row["id"])
        await self.e.check_account_automatically()
        self.assertFalse(self.e.recovery_required or self.e.running or self.e.paper_armed)
        self.assertIsNone(self.e.last_error)
        self.assertEqual(self.e.balance("USD"), self.b.cash - dec(9500))
        self.assertEqual(self.e.balance("BTC"), 0)
        self.assertEqual(self.e.ledger()["fees"], paid)
        for day, period in self.settlement["periods"].items():
            self.assertEqual(
                self.e.store.get(settlement.KEY)["periods"][day]["observed"], period["observed"]
            )
        for key in ("id", "since", "deadline"):
            self.assertEqual(self.e.store.get(settlement.KEY)[key], self.settlement[key])
        self.assertEqual(self.e.store.get(manual_exit.PREFIX + self.row["id"]), receipt)
        self.assertEqual(len(self.e.orders()), 7)
        self.assertFalse(self.e.qualification_execution_complete)
        self.assertIsNone(self.e.store.get("multibar-trial:" + PROTOCOL_HASH))
        ledger = self.e.ledger()
        await self.e.reconcile_account()
        self.assertEqual(self.e.ledger(), ledger)
        self.assertTrue(all(method == "GET" for method, _, _ in self.b.calls))

    async def test_failed_post_import_read_recovers_without_reimport_or_broker_writes(self):
        with patch.object(
            self.e, "reconcile_account", side_effect=SafetyError("fixture account read failed")
        ):
            with self.assertRaises(SafetyError):
                await self.confirm()
        self.assertEqual(len(self.e.orders()), 7)
        self.assertEqual(self.e.balance("BTC"), 0)
        self.assertIsNotNone(self.e.store.get(manual_exit.PREFIX + self.row["id"]))
        self.assertTrue(self.e.recovery_required)
        await self.e.check_account_automatically()
        self.assertFalse(self.e.recovery_required)
        self.assertIsNone(self.e.last_error)
        self.assertEqual(self.e.balance("USD"), self.b.cash - dec(9500))
        self.assertEqual(len(self.e.orders()), 7)
        self.assertTrue(all(m == "GET" for m, _, _ in self.b.calls))

    async def test_import_storage_failure_rolls_back_receipt_order_and_ledger_together(self):
        put = self.e.store._put

        def fail(key, value):
            if key == "ledger:paper":
                raise RuntimeError("fixture storage failure")
            put(key, value)

        with patch.object(self.e.store, "_put", side_effect=fail):
            with self.assertRaises(RuntimeError):
                await self.confirm()
        self.assertEqual(self.e.orders(), self.orders)
        self.assertEqual(self.e.ledger(), self.before)
        self.assertIsNone(self.e.store.get(manual_exit.PREFIX + self.row["id"]))
        self.assertEqual(self.e.store.get("paper-qualification"), self.failed)

    async def test_stop_and_inconsistent_reads_do_not_mutate_the_ledger(self):
        request = self.b.request
        count = 0

        async def change(method, path, **kw):
            nonlocal count
            if path == "/v2/account":
                count += 1
                if count == 2:
                    self.b.cash -= dec(".01")
            return await request(method, path, **kw)

        self.e.kraken.request.side_effect = change
        with self.assertRaisesRegex(SafetyError, "changed between reads"):
            await manual_exit.preview(self.e)
        lookups = 0
        submitted = self.row["submitted_at"]

        async def corrected_time(method, path, **kw):
            nonlocal lookups
            if path == "/v2/orders":
                lookups += 1
                if lookups == 2:
                    self.row["submitted_at"] = iso(self.failed["at"] - 1)
            return await request(method, path, **kw)

        self.e.kraken.request.side_effect = corrected_time
        with self.assertRaisesRegex(SafetyError, "changed between reads"):
            await manual_exit.preview(self.e)
        self.row["submitted_at"] = submitted
        self.e.kraken.request.side_effect = request
        view = await manual_exit.preview(self.e)

        async def stopped(method, path, **kw):
            self.e.stop_generation += 1
            return await request(method, path, **kw)

        self.e.kraken.request.side_effect = stopped
        with self.assertRaises(SafetyError):
            await self.confirm(view)
        self.assertEqual(self.e.ledger(), self.before)
        self.assertEqual(self.e.orders(), self.orders)
        self.assertTrue(all(m == "GET" for m, _, _ in self.b.calls))
