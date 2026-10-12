"""Restart recovery uses offline native-protocol fixtures, never hosted orders."""

import asyncio
import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kairos import frontend, htf, restart
from kairos import web as worker_web
from kairos.domain import SafetyError, dec
from kairos.okx_engine import OKXEngine
from tests import test_okx_strategies as fixtures


class ControlTests(unittest.TestCase):
    def test_private_stable_control_and_durable_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            first = restart.Control(directory)
            before = first.read()
            second = restart.Control(directory)
            self.assertEqual(before, second.read())
            self.assertEqual(first.revoke(), 1)
            self.assertFalse(second.matches(before))
            self.assertEqual(second.read()["csrf"], before["csrf"])
            self.assertEqual(first.path.stat().st_mode & 0o777, 0o600)
            # Replacing a missing control creates a new epoch, never revives a checkpoint.
            current = first.read()
            first.path.unlink()
            replacement = restart.Control(directory)
            self.assertFalse(replacement.matches(current))

    def test_control_rejects_public_permissions_symlinks_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            control = restart.Control(directory)
            control.path.chmod(0o644)
            with self.assertRaises(SafetyError):
                control.read()
            control.path.chmod(0o600)
            control.path.write_text('{"format":1,"generation":true}')
            with self.assertRaises(SafetyError):
                control.read()
            control.path.unlink()
            control.path.symlink_to(Path(directory) / "missing")
            with self.assertRaises(OSError):
                control.read()


class NativeRestartTests(unittest.IsolatedAsyncioTestCase):
    configure = fixtures.DemoStrategyTests.configure
    book = fixtures.DemoStrategyTests.book
    preview = fixtures.DemoStrategyTests.preview
    authorize = fixtures.DemoStrategyTests.authorize
    posts = fixtures.DemoStrategyTests.posts

    async def asyncSetUp(self):
        await fixtures.DemoStrategyTests.asyncSetUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.control = restart.Control(self.directory.name)
        self.engine.restart_control = self.control
        self.engine.settings.update(strategy="htf", htf_policy="multibar-v2")
        self.store.put("settings", self.engine.settings)
        await self.authorize(duration_seconds=3600)

    def new_engine(self):
        engine = OKXEngine(
            self.store, self.client, AsyncMock(), lambda *_: None, restart_control=self.control
        )
        self.addAsyncCleanup(engine.htf_review.close)
        return engine

    async def test_resume_retains_both_clocks_run_ownership_attempts_and_exit_plan(self):
        scope = copy.deepcopy(self.engine.authorization)
        deadline = self.engine.authorization_monotonic_deadline
        run = copy.deepcopy(self.engine.active_run())
        await self.engine.place(self.pair, "buy", dec(".0001"), dec("50000"), self.book())
        state = htf.snapshot(self.engine)
        state.update(
            position={
                "id": "owned",
                "entry_limit": "50000",
                "stop": "49000",
                "deadline": self.engine.clock() + 100,
                "exit_reason": "existing stop",
            },
            candidate={"id": "stale-entry"},
        )
        self.store.put(htf.key(self.engine), state)
        ledger, orders, posts = self.engine.ledger(), self.engine.orders(), self.posts()
        await self.engine.suspend()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.store.get("okx-operation")["status"], "running")
        resumed = self.new_engine()
        await resumed.initialize()
        self.assertTrue(resumed.running and resumed.armed, resumed.last_error)
        self.assertEqual(resumed.authorization, scope)
        self.assertEqual(resumed.authorization_monotonic_deadline, deadline)
        self.assertEqual(resumed.active_run(), run)
        self.assertEqual(resumed.ledger(), ledger)
        self.assertEqual(resumed.orders(), orders)
        self.assertEqual(self.posts(), posts)
        self.assertEqual(htf.snapshot(resumed)["position"], state["position"])
        self.assertIn("stale-entry", htf.snapshot(resumed)["consumed_candidates"])
        self.assertEqual(resumed.permission()["max_orders"], 4)

    async def test_recovery_reconciles_fill_during_owned_quote_cancellation_without_rebuying(self):
        self.venue.defer = True
        task = asyncio.create_task(
            self.engine.place(
                self.pair, "buy", dec(".0001"), dec("49999.9"), self.book(), maker=True
            )
        )
        try:
            for _ in range(100):
                if self.venue.orders:
                    break
                await asyncio.sleep(0.001)
            self.assertEqual(len(self.venue.orders), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.venue.fill_on_cancel = True
        resumed = self.new_engine()
        await resumed.initialize()
        self.assertTrue(resumed.running, resumed.last_error)
        buys = [call for call in self.posts() if call[1].endswith("/trade/order")]
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(resumed.orders()), 1)
        self.assertEqual(dec(resumed.orders()[0]["filled"]), dec(".0001"))
        self.assertEqual(resumed.balance("BTC"), dec(".0000999"))

    async def test_recovery_keeps_original_execution_run_provenance_across_code_upgrade(self):
        checkpoint = self.store.get(restart.CHECKPOINT)
        run = self.store.get(self.engine.run_key())
        checkpoint["execution_revision"] = run["execution_revision"] = "older-execution"
        self.store.put(self.engine.run_key(), run)
        self.store.put(restart.CHECKPOINT, checkpoint)
        resumed = self.new_engine()
        await resumed.initialize()
        self.assertTrue(resumed.running, resumed.last_error)
        resumed.ensure_run()
        self.assertEqual(self.store.get(resumed.run_key()), run)
        self.assertEqual(self.posts(), [])
        await resumed.stop()
        self.assertEqual(resumed.active_run()["id"], run["id"])
        self.engine = resumed
        await self.authorize(duration_seconds=3600)
        self.assertNotEqual(resumed.active_run()["id"], run["id"])
        self.assertNotEqual(resumed.active_run()["execution_revision"], "older-execution")

    async def test_gateway_stop_seen_before_watcher_does_not_create_a_failure_report(self):
        self.control.revoke()
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.store.get("okx-operation")["status"], "running")
        await self.engine.stop()
        self.assertEqual(self.store.get("okx-operation")["status"], "STOPPED")
        self.assertEqual(self.posts(), [])

    async def test_new_account_or_instrument_rules_block_recovery(self):
        checkpoint = self.store.get(restart.CHECKPOINT)
        operation = self.store.get("okx-operation")
        for reason in ("account", "rules"):
            self.store.put(restart.CHECKPOINT, checkpoint)
            self.store.put("okx-operation", operation)
            if reason == "account":
                self.venue.uid = "different-account"
            else:
                self.venue.uid = "123456"
                self.venue.instrument["tickSz"] = ".2"
            resumed = self.new_engine()
            await resumed.initialize()
            self.assertFalse(resumed.running or resumed.armed)
            self.assertEqual(self.posts(), [])

    async def test_stop_during_suspension_revokes_recovery_and_retention_message(self):
        async def interrupted():
            try:
                await asyncio.sleep(60)
            finally:
                await self.engine.stop()

        self.engine.task = asyncio.create_task(interrupted())
        await asyncio.sleep(0)
        with patch.object(self.engine, "event", wraps=self.engine.event) as events:
            await self.engine.suspend()
            self.assertFalse(any("worker suspended" in str(call) for call in events.call_args_list))
        self.assertIsNone(self.store.get(restart.CHECKPOINT))
        resumed = self.new_engine()
        await resumed.initialize()
        self.assertFalse(resumed.running or resumed.armed)
        self.assertEqual(self.store.get("okx-operation")["status"], "STOPPED")

    async def test_expired_permission_shutdown_stops_instead_of_retaining_checkpoint(self):
        checkpoint = self.store.get(restart.CHECKPOINT)
        checkpoint["monotonic_deadline"] = time.monotonic() - 1
        self.store.put(restart.CHECKPOINT, checkpoint)
        await self.engine.suspend()
        self.assertFalse(self.engine.running or self.engine.armed)
        self.assertIsNone(self.store.get(restart.CHECKPOINT))
        self.assertEqual(self.store.get("okx-operation")["status"], "STOPPED")

    async def test_abrupt_loss_uses_checkpoint_without_replaying_an_order(self):
        checkpoint = self.store.get(restart.CHECKPOINT)
        original = self.engine.active_run()
        # The old instance is not executed again; no graceful shutdown hook ran.
        resumed = self.new_engine()
        await resumed.initialize()
        self.assertTrue(resumed.running, resumed.last_error)
        self.assertEqual(resumed.authorization["id"], checkpoint["scope"]["id"])
        self.assertEqual(resumed.active_run()["id"], original["id"])
        self.assertEqual(self.posts(), [])

    async def test_watcher_observation_does_not_generate_an_endless_stop_loop(self):
        marker = self.control.read()
        await self.engine.stop()
        generation = self.control.read()["generation"]
        app = {"restart_control": self.control, "stop_marker": marker, "engine": self.engine}
        task = asyncio.create_task(worker_web.watch_stop(app))
        try:
            await asyncio.sleep(0.35)
            self.assertEqual(self.control.read()["generation"], generation)
            self.assertEqual(self.store.get("okx-operation")["status"], "STOPPED")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_stop_during_worker_downtime_revokes_checkpoint_and_transport(self):
        self.control.revoke()
        with self.assertRaisesRegex(SafetyError, "Durable Stop"):
            self.engine.permission()
        resumed = self.new_engine()
        await resumed.initialize()
        self.assertFalse(resumed.running or resumed.armed)
        self.assertIn("Stop", resumed.last_error)
        self.assertIsNone(self.store.get(restart.CHECKPOINT))
        self.assertEqual(self.posts(), [])

    async def test_expiry_settings_boot_and_terminal_reports_cannot_resume(self):
        checkpoint = self.store.get(restart.CHECKPOINT)
        operation = self.store.get("okx-operation")
        for reason in (
            "monotonic",
            "wall",
            "boot",
            "settings",
            "terminal",
            "budget",
            "account",
        ):
            with self.subTest(reason=reason):
                self.store.put("okx-operation", operation)
                value = copy.deepcopy(checkpoint)
                if reason == "monotonic":
                    value["monotonic_deadline"] = time.monotonic() - 1
                elif reason == "wall":
                    value["scope"]["deadline"] = time.time() - 1
                elif reason == "boot":
                    value["boot_id"] = "different boot"
                elif reason == "budget":
                    value["scope"]["budget"] = "2500"
                elif reason == "terminal":
                    self.store.put(
                        "okx-operation",
                        {**operation, "status": "STOPPED", "message": "original Stop report"},
                    )
                self.store.put(restart.CHECKPOINT, value)
                resumed = self.new_engine()
                if reason == "settings":
                    resumed.settings["order_size"] = "26"
                if reason == "account":
                    value["scope"]["account_scope"] = "different"
                    self.store.put(restart.CHECKPOINT, value)
                await resumed.initialize()
                self.assertFalse(resumed.running or resumed.armed, reason)
                if reason == "terminal":
                    self.assertEqual(
                        self.store.get("okx-operation")["message"], "original Stop report"
                    )
                self.assertEqual(self.posts(), [])

    async def test_exhausted_durable_attempt_limit_cannot_be_reset_by_restart(self):
        await self.engine.stop()
        await self.authorize(duration_seconds=3600, max_orders=1)
        await self.engine.place(self.pair, "buy", dec(".0001"), dec("50000"), self.book())
        ledger, orders, posts = self.engine.ledger(), self.engine.orders(), self.posts()
        resumed = self.new_engine()
        await resumed.initialize()
        self.assertFalse(resumed.running or resumed.armed)
        self.assertIn("attempt limit", resumed.last_error)
        self.assertEqual(resumed.orders(), orders)
        self.assertEqual(resumed.ledger(), ledger)
        self.assertEqual(self.posts(), posts)

    async def test_unresolved_account_blocks_resume_without_new_intents(self):
        resumed = self.new_engine()
        with patch.object(
            resumed, "reconcile_account", side_effect=SafetyError("fixture unresolved bills")
        ):
            await resumed.initialize()
        self.assertFalse(resumed.running)
        self.assertEqual(self.posts(), [])
        self.assertIn("unresolved bills", resumed.last_error)

    async def test_stop_during_recovery_read_wins(self):
        resumed = self.new_engine()
        original = resumed.wait_htf_book

        async def stopped():
            self.control.revoke()
            await original()

        with patch.object(resumed, "wait_htf_book", stopped):
            await resumed.initialize()
        self.assertFalse(resumed.running or resumed.armed)
        self.assertEqual(self.posts(), [])

    async def test_stop_is_durable_before_waiting_for_the_engine_lock(self):
        await self.engine.lock.acquire()
        stopped = asyncio.create_task(self.engine.stop())
        await asyncio.sleep(0)
        self.assertIsNone(self.store.get(restart.CHECKPOINT))
        self.assertFalse(self.engine.running)
        self.engine.lock.release()
        await stopped
        resumed = self.new_engine()
        await resumed.initialize()
        self.assertFalse(resumed.running)
        self.assertEqual(self.store.get("okx-operation")["status"], "STOPPED")

    async def test_stop_during_authorization_cannot_be_adopted_as_a_new_generation(self):
        await self.engine.stop()
        proposal = await self.preview(duration_seconds=3600)
        original = self.engine.valuation

        async def stopped(*args, **kwargs):
            self.control.revoke()
            return await original(*args, **kwargs)

        from kairos import okx_cycle

        with patch.object(self.engine, "valuation", stopped):
            with self.assertRaises(SafetyError):
                await okx_cycle.authorize(self.engine, proposal["id"], proposal["confirmation"])
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.store.get(restart.CHECKPOINT))
        self.assertEqual(self.posts(), [])


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.control = restart.Control(self.directory.name)
        self.worker = web.Application()
        self.received = []
        self.stops = 0

        async def state(request):
            return web.json_response(
                {"version": "worker-fixture", "running": True, "csrf": self.control.read()["csrf"]}
            )

        async def action(request):
            self.received.append((request.path, await request.json()))
            return web.json_response({"running": False})

        async def closed(app):
            self.stops += 1

        self.worker.router.add_get("/api/state", state)
        self.worker.router.add_post("/api/{action}", action)
        self.worker.on_shutdown.append(closed)
        self.runner = web.AppRunner(self.worker)
        await self.runner.setup()
        await web.UnixSite(self.runner, str(Path(self.directory.name) / "engine.sock")).start()
        self.addAsyncCleanup(self.runner.cleanup)
        self.client = await self.gateway()
        self.headers = {
            "Origin": "https://kairos.test",
            "X-CSRF-Token": self.control.read()["csrf"],
        }

    async def gateway(self):
        client = TestClient(
            TestServer(frontend.create_app(self.directory.name, "https://kairos.test"))
        )
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return client

    async def test_gateway_restart_does_not_stop_worker_or_revoke_permission(self):
        before = self.control.read()
        state = await (await self.client.get("/api/state")).json()
        self.assertTrue(state["running"] and state["separate_worker"])
        self.assertEqual(state["worker_version"], "worker-fixture")
        await self.client.close()
        self.assertEqual(self.stops, 0)
        again = await self.gateway()
        self.assertTrue((await (await again.get("/api/state")).json())["running"])
        self.assertEqual(self.control.read(), before)
        self.assertEqual(self.received, [])

    async def test_origin_csrf_json_and_body_limits_preserve_stop_security(self):
        before = self.control.read()
        for headers in (
            {},
            {"Origin": "https://evil.test", "X-CSRF-Token": self.headers["X-CSRF-Token"]},
            {"Origin": "https://kairos.test", "X-CSRF-Token": "bad"},
        ):
            response = await self.client.post("/api/stop", json={}, headers=headers)
            self.assertEqual(response.status, 403)
        response = await self.client.post(
            "/api/stop",
            data="not JSON",
            headers={**self.headers, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 400)
        response = await self.client.post(
            "/api/stop", json={"big": "x" * 20000}, headers=self.headers
        )
        self.assertEqual(response.status, 413)
        self.assertEqual(self.control.read(), before)
        self.assertEqual(self.received, [])

    async def test_stop_is_durable_when_worker_is_offline_and_requests_are_not_retried(self):
        await self.runner.cleanup()
        before = self.control.read()["generation"]
        response = await self.client.post("/api/stop", json={}, headers=self.headers)
        self.assertEqual(response.status, 202)
        self.assertTrue((await response.json())["stop_recorded"])
        self.assertEqual(self.control.read()["generation"], before + 1)
        response = await self.client.post("/api/start", json={}, headers=self.headers)
        self.assertEqual(response.status, 503)
        self.assertEqual(self.received, [])
        response = await self.client.get("/api/control")
        self.assertTrue((await response.json())["stop_available"])
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    async def test_worker_watcher_keeps_startup_stop_and_stop_during_settlement(self):
        marker = self.control.read()
        self.control.revoke()  # Accepted before the watcher starts.
        settled = asyncio.Event()
        calls = []

        async def stop():
            calls.append(self.control.read()["generation"])
            if len(calls) == 1:
                self.control.revoke()  # Another Stop arrives while the first is settling.
            else:
                settled.set()

        app = {
            "restart_control": self.control,
            "stop_marker": marker,
            "engine": SimpleNamespace(stop=stop),
        }
        task = asyncio.create_task(worker_web.watch_stop(app))
        try:
            await asyncio.wait_for(settled.wait(), 2)
            self.assertEqual(calls, [1, 2])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_watcher_does_not_repeat_failed_bounded_settlement(self):
        marker = self.control.read()
        self.control.revoke()
        engine = SimpleNamespace(
            stop=AsyncMock(side_effect=SafetyError("fixture unresolved settlement")),
            running=True,
            emit_state=lambda: None,
        )
        app = {"restart_control": self.control, "stop_marker": marker, "engine": engine}
        with patch.object(worker_web.diagnostics, "capture"):
            task = asyncio.create_task(worker_web.watch_stop(app))
            try:
                await asyncio.sleep(0.35)
                engine.stop.assert_awaited_once()
                self.assertFalse(engine.running)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_gateway_forwards_an_authorized_action_once(self):
        response = await self.client.post("/api/stop", json={}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.received, [("/api/stop", {})])
        self.assertEqual(self.control.read()["generation"], 1)
