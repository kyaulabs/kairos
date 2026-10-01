import asyncio
import copy
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp

from kairos import htf
from kairos.alpaca import Alpaca, iso
from kairos.alpaca_data import STREAM_URL
from kairos.alpaca_transport import AlpacaRequests, PendingAlpacaData, RejectedAlpacaData
from kairos.clients import ExchangeRejected
from kairos.domain import SafetyError, dec
from tests import test_alpaca as fixtures
from tests.test_alpaca import PaperBroker
from tests.test_clients_web import Session


def message(**changes):
    return {
        "T": "o",
        "S": "BTC/USD",
        "t": iso(time.time()),
        "r": True,
        "b": [{"p": "99", "s": "1"}, {"p": "98", "s": "2"}],
        "a": [{"p": "101", "s": "1"}],
        **changes,
    }


class Socket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent, self.closed = [], False
        self._response = SimpleNamespace(url=STREAM_URL)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def send_json(self, value):
        self.sent.append(value)

    async def receive(self, **kwargs):
        value = await asyncio.wait_for(self.queue.get(), kwargs["timeout"])
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(value))


class BookStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.broker = PaperBroker()
        self.client = self.broker.client
        await self.client.catalog()
        self.pair = self.client.pairs["alpaca:BTC/USD"]
        self.feed = self.client.market_data
        await self.feed.configure([self.pair])
        self.addAsyncCleanup(self.feed.close)

    def subscribe(self):
        self.feed.message({"T": "success", "msg": "authenticated"})
        self.feed.message({"T": "subscription", "orderbooks": ["BTC/USD"]})

    async def test_reset_deltas_deletions_and_disconnect_invalidate_execution(self):
        self.subscribe()
        with self.assertRaisesRegex(SafetyError, "before full reset"):
            self.feed.message(message(r=False))
        self.feed.message(message())
        first = await self.client.book(self.pair)
        self.assertEqual(first.source, "WebSocket")
        first.fresh(10)
        self.feed.message(message(r=False, b=[{"p": "99", "s": "0"}], a=[]))
        current = await self.client.book(self.pair)
        self.assertEqual(current.bids[0][0], dec(98))
        with self.assertRaises(SafetyError):
            self.feed.message(message(t=iso(time.time() - 60)))
        with self.assertRaises(SafetyError):
            self.feed.message(message(b=[{"p": "102", "s": "1"}]))
        self.feed.invalidate()
        with self.assertRaises(PendingAlpacaData):
            first.fresh(10)
        self.assertIsNone(self.feed.fresh_book(self.pair))

    async def test_old_source_is_not_refreshed_by_receipt_and_rest_is_bounded(self):
        old = iso(time.time() - 48)
        self.client.request = AsyncMock(return_value={"orderbooks": {"BTC/USD": message(t=old)}})
        for _ in range(2):
            with self.assertRaises(PendingAlpacaData):
                await self.client.book(self.pair)
        self.assertEqual(self.client.request.await_count, 1)
        details = self.feed.snapshot()["books"][0]
        self.assertFalse(details["fresh"])
        self.assertGreater(details["market_age_seconds"], 47)
        self.assertGreater(details["received_at"], details["market_at"] + 47)
        self.feed.attempts.clear()
        self.client.request.return_value = {"orderbooks": {"BTC/USD": message()}}
        (await self.client.book(self.pair)).fresh(10)
        self.assertEqual(self.client.request.await_count, 2)

    async def test_one_connection_targeted_subscription_and_clean_shutdown(self):
        socket = Socket()
        calls = []

        def connect(url, **kwargs):
            calls.append(url)
            return socket

        self.client.session = SimpleNamespace(ws_connect=connect)
        await self.feed.configure([self.pair])
        await self.feed.configure([self.pair])
        await socket.queue.put([{"T": "success", "msg": "authenticated"}])
        await socket.queue.put([{"T": "subscription", "orderbooks": ["BTC/USD"]}])
        await socket.queue.put([message()])
        for _ in range(20):
            if self.feed.books:
                break
            await asyncio.sleep(0.001)
        self.assertEqual(calls, [STREAM_URL])
        self.assertEqual(socket.sent[1], {"action": "subscribe", "orderbooks": ["BTC/USD"]})
        self.assertEqual(self.feed.snapshot()["books_ready"], 1)
        cached = self.feed.books[self.pair.id]
        await self.feed.close()
        self.assertTrue(socket.closed)
        with self.assertRaises(PendingAlpacaData):
            cached.fresh(10)

    async def test_disconnect_while_order_queued_prevents_http_submission(self):
        self.subscribe()
        self.feed.message(message())
        self.client.session = Session({})
        self.client.request = Alpaca.request.__get__(self.client)
        self.client.requests.last_request = time.monotonic()
        task = asyncio.create_task(
            self.client.add(
                {
                    "pair": self.pair.id,
                    "type": "buy",
                    "ordertype": "limit",
                    "volume": ".1",
                    "price": "99",
                    "cl_ord_id": "queued-test",
                    "oflags": "post,fciq",
                    "deadline": iso(time.time() + 5),
                }
            )
        )
        await asyncio.sleep(0.01)
        self.feed.invalidate()
        with self.assertRaises(ExchangeRejected):
            await task
        self.assertFalse(self.client.session.calls)

    async def test_redirect_cannot_receive_authentication(self):
        socket = Socket()
        socket._response.url = "wss://untrusted.invalid/crypto/us"
        self.client.session = SimpleNamespace(ws_connect=lambda *a, **k: socket)
        await self.feed.configure([self.pair])
        await asyncio.wait_for(self.feed.task, 1)
        self.assertTrue(self.feed.blocked)
        self.assertFalse(socket.sent)
        self.assertTrue(socket.closed)

    async def test_stream_error_blocks_reconnect_without_trusting_cached_books(self):
        socket = Socket()
        self.client.session = SimpleNamespace(ws_connect=lambda *a, **k: socket)
        await socket.queue.put([{"T": "error", "code": 406, "msg": "not retained"}])
        await self.feed.configure([self.pair])
        await asyncio.wait_for(self.feed.task, 1)
        self.assertEqual(self.feed.status, "blocked")
        self.assertFalse(self.feed.connected)
        self.assertFalse(self.feed.books)
        self.assertNotIn("not retained", self.feed.error)
        task = self.feed.task
        await self.feed.configure([self.pair])
        self.assertIs(self.feed.task, task)

    async def test_future_bad_levels_and_clock_changes_are_not_transient_freshness(self):
        self.subscribe()
        for value in (
            message(t=iso(time.time() + 20)),
            message(b=[{"p": "99", "s": "-1"}]),
            message(r="true"),
        ):
            with self.assertRaises(SafetyError) as caught:
                self.feed.message(value)
            self.assertNotIsInstance(caught.exception, PendingAlpacaData)
        self.feed.message(message())
        book = self.feed.books[self.pair.id]
        with patch("kairos.alpaca_data.time.time", return_value=book.arrived_at - 20):
            with self.assertRaises(SafetyError) as caught:
                book.fresh(10)
            self.assertNotIsInstance(caught.exception, PendingAlpacaData)


class RequestBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_priority_and_canceled_waiters(self):
        gate = AlpacaRequests()
        order = []

        async def take(priority):
            async with gate.slot(priority, "data"):
                order.append(priority)
                gate.last_request = 0

        async with gate.slot(1, "trading"):
            jobs = [asyncio.create_task(take(p)) for p in (3, 2, 0)]
            canceled = asyncio.create_task(take(1))
            await asyncio.sleep(0)
            canceled.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await canceled
            gate.last_request = 0
        await asyncio.gather(*jobs)
        self.assertEqual(order, [0, 2, 3])
        self.assertFalse(gate.waiters)
        self.assertFalse(gate.busy)

    async def test_background_and_critical_reserves_and_remote_limit(self):
        gate = AlpacaRequests()
        now = time.monotonic()
        gate.history.extend((now, "data", 3) for _ in range(30))
        self.assertGreater(gate.delay(3, "data", now), 59)
        self.assertEqual(gate.delay(0, "trading", now), 0)
        gate.history.extend((now, "data", 2) for _ in range(100))
        self.assertGreater(gate.delay(2, "data", now), 59)
        self.assertEqual(gate.delay(0, "trading", now), 0)
        gate.buckets["trading"]["limit"] = 1
        gate.history.append((now, "trading", 0))
        self.assertGreater(gate.delay(0, "trading", now), 59)

    async def test_429_headers_defer_data_without_retrying_write_or_blocking_other_host(self):
        class Limited(Session):
            def request(self, *a, **k):
                result = super().request(*a, **k)
                result.headers = {
                    "X-RateLimit-Limit": "200",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time() + 30)),
                    "Retry-After": "30",
                }
                return result

        session = Limited({}, 429)
        client = Alpaca(session, "paper-test-key", "paper-test-secret", allow_paper=True)
        with self.assertRaises(PendingAlpacaData):
            await client.request("GET", "/v1beta3/crypto/us/latest/orderbooks", data=True)
        self.assertEqual(len(session.calls), 1)
        state = client.requests.snapshot()
        self.assertGreater(state["quotas"]["data"]["retry_in_seconds"], 29)
        self.assertEqual(state["quotas"]["trading"]["retry_in_seconds"], 0)
        with self.assertRaises(SafetyError) as caught:
            await client.request("POST", "/v2/orders", deadline=time.time() + 3)
        self.assertNotIsInstance(caught.exception, PendingAlpacaData)
        self.assertEqual(len(session.calls), 2)
        with self.assertRaises(ExchangeRejected):
            await client.request("POST", "/v2/orders", deadline=time.time())
        self.assertEqual(len(session.calls), 2)

    async def test_stop_and_freshness_guards_run_after_admission_before_http(self):
        client = Alpaca(Session({}), "paper-test-key", "paper-test-secret", allow_paper=True)
        client.order_guard = lambda: False
        with self.assertRaises(ExchangeRejected):
            await client.request("POST", "/v2/orders")
        self.assertFalse(client.session.calls)
        client.order_guard = lambda: True

        def reject():
            raise ExchangeRejected("Quote expired while queued")

        with self.assertRaises(ExchangeRejected):
            await client.request("POST", "/v2/orders", before_send=reject)
        self.assertFalse(client.session.calls)


class DataWaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = fixtures.AlpacaEngineTests()
        await self.case.asyncSetUp()
        self.addAsyncCleanup(self.case.asyncTearDown)
        self.engine = self.case.engine
        await self.engine.configure({**self.engine.settings, "strategy": "htf"})
        await self.case.start()
        self.engine.htf_review.refresh = AsyncMock(return_value=None)

    async def test_data_wait_keeps_no_orders_and_recovers_with_new_baseline(self):
        real_book = self.engine.kraken.book
        self.engine.kraken.book = AsyncMock(side_effect=PendingAlpacaData("Old source"))
        before = copy.deepcopy(self.engine.ledger())
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertIsNotNone(self.engine.market_wait)
        self.assertFalse(self.engine.recovery_required)
        self.assertFalse(self.engine.orders())
        self.assertEqual(self.engine.ledger(), before)
        state = htf.snapshot(self.engine)
        state.update(entry_signal="buy", pending_signal="buy")
        self.engine.store.put(htf.key(self.engine), state)
        self.engine.kraken.book = real_book
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertIsNone(self.engine.market_wait)
        self.assertIsNone(htf.snapshot(self.engine)["entry_signal"])
        self.assertFalse(self.engine.orders())

    async def test_explicit_stop_and_loss_halt_cannot_auto_resume(self):
        self.engine.kraken.book = AsyncMock(side_effect=PendingAlpacaData("Old source"))
        await self.engine.tick()
        await self.engine.stop()
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.engine.market_wait)
        self.engine.running = True
        self.engine.daily_pnl = "-12.50"
        self.assertFalse(self.engine.wait_for_market_data(PendingAlpacaData("Old")))
        self.assertFalse(self.engine.orders())

    async def test_stop_during_recovery_read_wins(self):
        real_book = self.engine.kraken.book
        self.engine.kraken.book = AsyncMock(side_effect=PendingAlpacaData("Old"))
        await self.engine.tick()
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(pair):
            entered.set()
            await release.wait()
            return await real_book(pair)

        self.engine.kraken.book = delayed
        tick = asyncio.create_task(self.engine.tick())
        await entered.wait()
        stop = asyncio.create_task(self.engine.stop())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(tick, stop)
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.engine.market_wait)
        self.assertFalse(self.engine.orders())

    async def test_pre_post_freshness_rejection_is_not_an_uncertain_write(self):
        plan = {
            "id": "no-send",
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
        self.engine.kraken.add = AsyncMock(
            side_effect=RejectedAlpacaData("Quote aged while queued")
        )
        with self.assertRaises(RejectedAlpacaData):
            await self.case.buy(maker=True)
        self.assertEqual(self.engine.orders()[0]["status"], "rejected")
        self.assertFalse(any(method == "POST" for method, _, _ in self.case.broker.calls))
        self.assertTrue(
            self.engine.wait_for_market_data(RejectedAlpacaData("Quote aged while queued"))
        )

    async def test_owned_protection_and_lineage_survive_data_wait(self):
        plan = {
            "id": "held-plan",
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
        self.case.broker.fill = False
        order = await self.case.buy(maker=True)
        self.case.broker.fill_order(order["txid"], dec(".1"))
        await self.engine.reconcile_account()
        htf.prepare(self.engine)
        before = copy.deepcopy(htf.snapshot(self.engine)["position"])
        real_book = self.engine.kraken.book
        self.engine.kraken.book = AsyncMock(side_effect=PendingAlpacaData("Old"))
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertEqual(htf.snapshot(self.engine)["position"], before)
        self.engine.kraken.book = real_book
        await self.engine.tick()
        self.assertEqual(htf.snapshot(self.engine)["position"], before)
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertEqual(self.engine.balance("BTC"), dec(".1"))

    async def test_loss_failure_during_wait_remains_halted_after_data_recovers(self):
        self.engine.market_wait = (time.time(), time.monotonic())
        self.engine.valuation = AsyncMock(
            side_effect=SafetyError("Daily marked-to-market loss limit reached")
        )
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertIn("loss limit", self.engine.last_error)
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.orders())

    async def test_timeout_and_uncertain_orders_fail_closed(self):
        self.engine.market_wait = (time.time() - 301, time.monotonic() - 301)
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertIn("exhausted", self.engine.last_error)
        self.engine.running = True
        self.engine.recovery_required = False
        with patch.object(self.engine, "orders", return_value=[{"status": "uncertain"}]):
            self.assertFalse(self.engine.wait_for_market_data(PendingAlpacaData("Old")))

    async def test_malformed_data_still_halts(self):
        self.engine.kraken.book = AsyncMock(side_effect=SafetyError("Invalid execution depth"))
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertIsNone(self.engine.market_wait)
        self.assertFalse(self.engine.orders())
