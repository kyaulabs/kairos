import copy
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from kairos import programs
from kairos.domain import SafetyError
from kairos.engine import Engine
from kairos.store import Store
from kairos.web import create_app
from tests.helpers import BTC, fake_jev, fake_kraken


def order(identifier="paper-1", **changes):
    return {
        "id": identifier,
        "txid": None,
        "mode": "dry-run",
        "product": "spot",
        "strategy": "htf",
        "pair": BTC.id,
        "base": BTC.base,
        "quote": BTC.quote,
        "side": "buy",
        "volume": "0.01",
        "filled": "0.01",
        "price": "10000",
        "cost": "100",
        "fee": "0.4",
        "fee_bps": "40",
        "maker": False,
        "status": "closed",
        "created": time.time() - 86400,
        "expires": 0,
        **changes,
    }


class PaperHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.spot, self.jev = fake_kraken(), fake_jev()
        self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None)
        await self.engine.initialize()

    def record(self, row):
        self.store.save_order(row)
        self.store.event("order", row)
        self.store.event(
            "fill",
            {
                "order_id": row["id"],
                "mode": row["mode"],
                "product": row.get("product", "spot"),
                "pair": row["pair"],
                "side": row["side"],
                "volume": row["filled"],
                "cost": row["cost"],
                "fee": row["fee"],
            },
        )

    async def mutate(self, identifier="paper-1", operation="delete"):
        await self.engine.paper_order_history(identifier, operation, "DELETE PAPER ORDER")

    async def test_archive_and_restore_are_persistent_display_only_actions(self):
        paper, live = order(), order("live-1", mode="trading")
        self.record(paper)
        self.record(live)
        before_ledger = copy.deepcopy(self.engine.ledger())
        self.engine.running = True
        await self.mutate(operation="archive")
        state = self.engine.snapshot()
        self.assertEqual([o["id"] for o in state["orders"]], ["live-1"])
        self.assertEqual([o["id"] for o in state["archived_orders"]], ["paper-1"])
        self.assertEqual(state["order_history_revision"], 1)
        self.assertEqual(len(self.engine.orders("dry-run")), 1)
        self.assertFalse(any(e["data"].get("mode") == "dry-run" for e in self.store.history()))
        self.assertEqual(self.engine.ledger(), before_ledger)
        await self.mutate(operation="restore")
        self.assertEqual([o["id"] for o in self.store.orders()], ["paper-1", "live-1"])
        self.assertEqual(len(self.engine.snapshot()["orders"]), 2)
        self.assertEqual(
            len([e for e in self.store.history() if e["data"].get("mode") == "dry-run"]), 2
        )
        self.assertEqual(self.engine.snapshot()["order_history_revision"], 2)
        self.spot.add.assert_not_awaited()
        self.spot.cancel.assert_not_awaited()

    async def test_delete_removes_order_and_linked_paper_events_but_preserves_money_and_live_data(
        self,
    ):
        self.record(order())
        self.record(order("live-1", mode="trading"))
        self.record(order("paper-2"))
        # Even an anomalous live event with a matching ID must not be removed.
        self.store.event("fill", {"mode": "trading", "order_id": "paper-1"})
        self.store.put("day:dry-run", {"day": "test", "equity": "900"})
        self.store.put("ledger:trading", {"untouched": True})
        before = {
            key: self.store.get(key)
            for key in (
                "ledger:dry-run",
                "ledger:trading",
                "margin",
                "futures:dry-run",
                "day:dry-run",
                "settings",
            )
        }
        await self.mutate()
        self.assertEqual([o["id"] for o in self.store.orders()], ["live-1", "paper-2"])
        for event in self.store.history():
            if event["data"].get("mode") == "dry-run":
                self.assertNotEqual(
                    event["data"].get("order_id", event["data"].get("id")), "paper-1"
                )
        self.assertEqual(
            len([e for e in self.store.history() if e["data"].get("mode") == "trading"]), 3
        )
        self.assertEqual(before, {key: self.store.get(key) for key in before})
        self.spot.request.assert_not_awaited()
        self.spot.add.assert_not_awaited()
        self.spot.cancel.assert_not_awaited()
        self.jev.decide.assert_not_awaited()

    async def test_live_active_and_unknown_orders_cannot_be_changed(self):
        self.record(order("live-1", mode="trading"))
        self.record(order("active", status="open"))
        for identifier in ("live-1", "active", "missing"):
            for operation in ("archive", "restore", "delete"):
                with (
                    self.subTest(identifier=identifier, operation=operation),
                    self.assertRaises(SafetyError),
                ):
                    await self.mutate(identifier, operation)
        self.assertIsNone(self.store.get("order_history_revision"))

    async def test_delete_requires_confirmation_paused_dry_run_and_no_unsettled_paper_orders(self):
        self.record(order())
        with self.assertRaisesRegex(SafetyError, "confirmation"):
            await self.engine.paper_order_history("paper-1", "delete")
        self.engine.running = True
        with self.assertRaisesRegex(SafetyError, "Pause"):
            await self.mutate()
        self.engine.running = False
        self.engine.mode = "trading"
        with self.assertRaises(SafetyError):
            await self.mutate()
        self.engine.mode = "dry-run"
        self.record(order("unsettled", product="futures", status="uncertain"))
        with self.assertRaises(SafetyError):
            await self.mutate()
        self.assertEqual(len(self.store.orders()), 2)

    async def test_positions_in_each_paper_portfolio_block_deletion_not_archiving(self):
        for product, key, ledger in (
            ("spot", "ledger:dry-run", {"balances": {BTC.quote: "1000", BTC.base: "0.1"}}),
            ("margin", "margin", {"positions": {BTC.id: {"quantity": "-0.1"}}}),
            ("futures", "futures:dry-run", {"positions": {"futures:PF_XBTUSD": {"quantity": "1"}}}),
        ):
            row = order(product, product=product)
            self.record(row)
            old = self.store.get(key)
            self.store.put(key, ledger)
            with self.assertRaisesRegex(SafetyError, "positions"):
                await self.mutate(product)
            await self.mutate(product, "archive")
            self.assertEqual(self.store.get(key), ledger)
            self.store.put(key, old)
            await self.mutate(product)
        self.assertEqual(self.store.orders(), [])

    async def test_current_scalp_plan_is_protected_even_when_quantity_is_zero(self):
        self.record(order(scalp_id="owned", strategy="scalp"))
        key = f"scalp:dry-run:spot:{BTC.id}"
        self.store.put(key, {"position": {"id": "owned"}})
        with self.assertRaisesRegex(SafetyError, "scalp plan"):
            await self.mutate()
        await self.mutate(operation="archive")
        self.assertEqual(self.store.get(key), {"position": {"id": "owned"}})
        self.store.put(key, {"position": None})
        await self.mutate()

    async def test_archiving_retains_program_accounting_and_deletion_protects_saved_runs(self):
        self.engine.settings["strategy"] = "dca"
        programs.prepare(self.engine)
        program = self.store.get(programs.key(self.engine))
        self.record(order(strategy="dca", program_id=program["id"]))
        before = programs.snapshot(self.engine)
        await self.mutate(operation="archive")
        self.assertEqual(programs.snapshot(self.engine), before)
        with self.assertRaisesRegex(SafetyError, "saved strategy run"):
            await self.mutate()
        self.store.put(programs.key(self.engine), {**program, "status": "complete"})
        with self.assertRaises(SafetyError):
            await self.mutate()
        self.store.put(programs.key(self.engine), None)
        await self.mutate()

    async def test_futures_run_and_today_rebalance_turnover_remain_protected(self):
        self.record(order("future", product="futures", strategy="twap", program_id="parent"))
        self.store.put("program:futures:dry-run:twap", {"id": "parent"})
        with self.assertRaisesRegex(SafetyError, "saved strategy run"):
            await self.mutate("future")
        row = order("rebalance", strategy="rebalance", created=time.time())
        self.record(row)
        await self.mutate("rebalance", "archive")
        self.assertIn("rebalance", [o["id"] for o in self.engine.orders("dry-run")])
        with self.assertRaisesRegex(SafetyError, "turnover"):
            await self.mutate("rebalance")
        row["created"] -= 86400
        self.store.save_order(row)
        await self.mutate("rebalance")

    async def test_recovery_and_invalid_requests_do_not_mutate_history(self):
        self.record(order())
        for recovery in (True, False):
            self.engine.recovery_required = recovery
            self.store.put("cycle", None if recovery else {"mode": "dry-run"})
            with self.assertRaisesRegex(SafetyError, "recovery"):
                await self.mutate()
        self.engine.recovery_required = False
        self.store.put("cycle", None)
        for identifier, operation in ((None, "delete"), ("paper-1", {}), ("paper-1", "unknown")):
            with self.assertRaises(SafetyError):
                await self.engine.paper_order_history(identifier, operation)
        self.assertEqual(len(self.store.orders()), 1)

    async def test_store_transaction_rolls_back_event_deletion_and_revision_on_failure(self):
        self.record(order())
        before = self.store.history()
        self.store.db.execute(
            "CREATE TRIGGER reject_delete BEFORE DELETE ON orders BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            await self.mutate()
        self.assertEqual(self.store.history(), before)
        self.assertEqual(len(self.store.orders()), 1)
        self.assertIsNone(self.store.get("order_history_revision"))

    async def test_archive_filter_applies_before_display_limits_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "history.sqlite3")
            store = Store(path)
            try:
                store.save_order(order("live", mode="trading"))
                store.event("order", order("live", mode="trading"))
                for i in range(4):
                    row = order(str(i))
                    store.save_order(row)
                    store.event("order", row)
                    store.paper_order_history(str(i), "archive")
                self.assertEqual([e["data"]["id"] for e in store.history(limit=1)], ["live"])
                self.assertEqual([o["id"] for o in store.display_orders(limit=1)], ["live"])
            finally:
                store.close()
            reopened = Store(path)
            try:
                self.assertEqual(len(reopened.display_orders(archived=True)), 4)
                self.assertEqual(reopened.get("order_history_revision"), 4)
            finally:
                reopened.close()

    async def test_api_requires_csrf_and_rechecks_safety_instead_of_trusting_ui(self):
        self.record(order())
        self.record(order("live", mode="trading"))
        app = create_app(self.engine, "https://kairos.example.test")
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        body = {"order_id": "paper-1", "operation": "archive"}
        response = await client.post("/api/paper-order", json=body)
        self.assertEqual(response.status, 403)
        headers = {"Origin": "https://kairos.example.test", "X-CSRF-Token": app["csrf"]}
        response = await client.post("/api/paper-order", json=body, headers=headers)
        self.assertEqual(response.status, 200)
        state = await response.json()
        self.assertEqual([o["id"] for o in state["archived_orders"]], ["paper-1"])
        self.assertIsNone(state["archived_orders"][0]["history_actions"]["delete_reason"])
        self.engine.running = True
        response = await client.post(
            "/api/paper-order",
            json={**body, "operation": "delete", "confirmation": "DELETE PAPER ORDER"},
            headers=headers,
        )
        self.assertEqual(response.status, 409)
        response = await client.post(
            "/api/paper-order", json={**body, "order_id": "live"}, headers=headers
        )
        self.assertEqual(response.status, 409)
        self.engine.running = False
        response = await client.post(
            "/api/paper-order",
            json={**body, "operation": "delete", "confirmation": "DELETE PAPER ORDER"},
            headers=headers,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual([o["id"] for o in (await response.json())["orders"]], ["live"])
