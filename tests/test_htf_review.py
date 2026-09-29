import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from kairos import htf
from kairos.domain import SafetyError, dec
from kairos.engine import Engine
from kairos.htf_review import rolling_rows
from kairos.store import Store
from kairos.strategies import trend_state
from tests.helpers import BTC, book, fake_jev, fake_kraken


def minute(ts, price=10000):
    return [ts, str(price), str(price + 1), str(price - 1), str(price), str(price), "10", 1]


def pullback_minutes(end):
    closes = [9000 + i * 40 for i in range(27)] + [10400, 9750, 9750]
    rows = []
    for i, ts in enumerate(range(end - 1800 * 60, end, 60)):
        index = i // 60
        price = closes[index - 1] if i % 60 == 0 and index else closes[index]
        row = minute(ts, price)
        row[2], row[3] = str(price + 300), str(price - 300)
        rows.append(row)
    return rows


class RollingHistoryTests(unittest.TestCase):
    def test_shifted_hour_windows_preserve_indicator_math_and_survive_restart(self):
        cutoff = 180000
        rows = [minute(t, 10000 + i) for i, t in enumerate(range(cutoff - 1800 * 60, cutoff, 60))]
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "history.sqlite3")
            store = Store(path)
            bars, count, needed = rolling_rows(store, BTC, rows, cutoff, 60)
            self.assertEqual((count, needed, len(bars)), (1800, 1800, 30))
            self.assertEqual(bars[-1][0], cutoff - 3600)
            self.assertEqual(bars[-1][1], rows[-60][1])
            self.assertEqual(bars[-1][4], rows[-1][4])
            settings = {"candle_minutes": 60, "slippage_bps": "10"}
            view = trend_state(bars, settings, 40, 10)
            closes = [dec(r[4]) for r in rows[59::60]]
            self.assertEqual(dec(view["fast_average"]), sum(closes[-8:]) / 8)
            self.assertEqual(dec(view["slow_average"]), sum(closes[-21:]) / 21)
            self.assertEqual(
                dec(view["eight_candle_return_bps"]), (closes[-1] / closes[-9] - 1) * 10000
            )
            store.close()
            store = Store(path)
            self.addCleanup(store.close)
            shifted, count, _ = rolling_rows(store, BTC, [minute(cutoff, 12000)], cutoff + 60, 60)
            self.assertEqual(count, 1800)
            self.assertEqual(shifted[-1][0], cutoff + 60 - 3600)
            self.assertEqual(shifted[-1][4], "12000")
            self.assertEqual(shifted[-2][4], rows[-60][4])

    def test_no_forward_fill_forming_bars_or_false_warmup_completion(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        cutoff = 180000
        rows = [minute(t) for t in range(cutoff - 1800 * 60, cutoff, 60)]
        del rows[-10]
        bars, count, _ = rolling_rows(store, BTC, rows, cutoff, 60)
        self.assertIsNone(bars)
        self.assertEqual(count, 9)
        for invalid in ([minute(cutoff)], [minute(cutoff - 60)] * 2):
            with self.assertRaises(SafetyError):
                rolling_rows(store, BTC, invalid, cutoff, 60)
        bars, count, _ = rolling_rows(store, BTC, [minute(cutoff - 10 * 60)], cutoff, 60)
        self.assertEqual((len(bars), count), (30, 1800))


class RollingReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = int(time.time()) // 60 * 60 + 1
        self.store = Store(":memory:", clock=lambda: self.now)
        self.spot, self.jev = fake_kraken(), fake_jev()
        self.engine = Engine(
            self.store, self.spot, self.jev, lambda *_: None, clock=lambda: self.now
        )
        await self.engine.initialize()
        await self.engine.configure(
            {
                **self.engine.settings,
                "candle_minutes": 60,
                "interval_seconds": 10,
                "order_size": "25",
                "max_exposure": "100",
                "daily_loss": "20",
            }
        )
        self.end = int(self.now) // 60 * 60
        self.rows = pullback_minutes(self.end)
        rolling_rows(self.store, BTC, self.rows, self.end, 60)
        self.spot.ohlc = AsyncMock(
            side_effect=lambda *_: self.rows[-719:] + [minute(self.end, 99999)]
        )
        await self.engine.start()

    async def asyncTearDown(self):
        await self.engine.close()
        await self.spot.market_data.close()
        self.store.close()

    async def review(self):
        await self.engine.tick()
        task = self.engine.htf_review.task
        if task:
            await task
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)

    async def signal(self, action="buy", *, fill=True):
        await self.review()  # Flat startup baseline, even if Jev recommends buying.
        self.now += 60
        self.end += 60
        self.rows.append(minute(self.end - 60, 9995))
        self.jev.decide.return_value["action"] = action
        await self.review()
        if fill and self.engine.orders(active=True):
            await self.fill_entry()

    async def fill_entry(self, fraction=1):
        order = self.engine.orders()[-1]
        self.now += 1
        self.spot.trades.return_value = (
            [
                [
                    str(dec(order["price"]) - BTC.tick),
                    str(dec(order["volume"]) * 10 * dec(fraction)),
                    self.now,
                    "s",
                    "l",
                ]
            ],
            str(int(self.now * 1e9)),
        )
        await self.engine.tick()
        self.spot.trades.return_value = ([], str(int(self.now * 1e9)))
        self.assertTrue(self.engine.running, self.engine.last_error)

    async def test_model_cannot_buy_at_start_or_override_entry_gate(self):
        await self.review()
        self.assertEqual(self.engine.orders(), [])
        payload = self.engine.latest_decision["state"]
        self.assertFalse(payload["entry_eligible"])
        self.assertEqual(payload["candle_minutes"], 60)
        self.assertEqual(payload["review"]["interval_seconds"], 20)
        self.assertEqual(payload["candle_close_time"], self.end)
        self.assertEqual(payload["allowed_actions"], ["hold"])
        self.assertNotIn("99999", payload["fast_average"])
        self.jev.decide.assert_not_awaited()
        self.spot.add.assert_not_awaited()
        calls = self.jev.decide.await_count
        self.now += 19
        await self.engine.tick()
        self.assertEqual(self.jev.decide.await_count, calls)
        self.now += 1
        await self.review()
        self.assertEqual(self.jev.decide.await_count, calls)
        self.assertIn("Jev skipped", self.engine.htf_review.status)

    async def test_hold_then_buy_in_same_rolling_window_no_pyramiding(self):
        await self.signal("hold")
        self.assertEqual(self.engine.orders(), [])
        self.assertEqual(htf.snapshot(self.engine)["pending_signal"], "buy")
        payload = self.jev.decide.call_args.args[0]
        self.assertTrue(payload["entry_ready"])
        self.assertEqual(payload["pending_signal"], "buy")
        self.assertEqual(payload["allowed_actions"], ["hold", "buy"])
        self.now += 20
        self.jev.decide.return_value["action"] = "buy"
        await self.review()
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertFalse(self.engine.latest_decision["deterministic"])
        self.assertEqual(self.engine.latest_decision["model"], "test")
        self.assertIsNone(htf.snapshot(self.engine)["pending_signal"])
        await self.fill_entry()
        self.now += 20
        await self.review()
        self.assertEqual(len(self.engine.orders()), 1)
        self.spot.add.assert_not_awaited()

    async def test_low_confidence_does_not_consume_setup(self):
        self.jev.decide.return_value["confidence"] = 0.2
        await self.signal()
        self.assertEqual(self.engine.orders(), [])
        self.assertEqual(htf.snapshot(self.engine)["pending_signal"], "buy")
        self.now += 20
        self.jev.decide.return_value["confidence"] = 0.9
        await self.review()
        self.assertEqual(len(self.engine.orders()), 1)

    async def test_jev_can_exit_between_hour_boundaries(self):
        await self.signal()
        self.now += 20
        self.jev.decide.return_value["action"] = "sell"
        await self.review()
        self.assertEqual(len(self.engine.orders()), 2)
        self.assertIn("Jev discretionary exit", self.engine.orders()[-1]["reason"])
        self.assertEqual(self.engine.balance(BTC.base), 0)

    async def test_slow_jev_does_not_delay_protective_stop_or_overlap_calls(self):
        await self.signal()
        self.now += 20
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(_):
            entered.set()
            await release.wait()
            return self.jev.decide.return_value

        self.jev.decide.side_effect = slow
        await self.engine.tick()
        await asyncio.wait_for(entered.wait(), 1)
        calls = self.jev.decide.await_count
        self.now += 20
        await asyncio.wait_for(self.engine.tick(), 1)
        self.assertEqual(self.jev.decide.await_count, calls)
        self.spot.book.side_effect = lambda pair: book(pair, "9500", "9510")
        await asyncio.wait_for(self.engine.tick(), 1)
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertIn("protective stop", self.engine.orders()[-1]["reason"])
        self.assertTrue(self.engine.running)
        release.set()

    async def test_model_error_keeps_protection_running_without_discretionary_trades(self):
        await self.signal("hold")
        self.now += 20
        self.jev.decide.side_effect = SafetyError("simulated model timeout")
        await self.review()
        self.assertEqual(self.engine.orders(), [])
        self.assertIn("unavailable", self.engine.htf_review.status)
        self.assertIsNone(self.engine.last_error)
        self.assertEqual(len([e for e in self.store.history() if e["kind"] == "review-error"]), 1)

    async def test_expired_response_and_expiry_during_submission_cannot_trade(self):
        await self.signal("hold")
        self.now += 20
        self.jev.decide.return_value["action"] = "buy"
        await self.engine.tick()
        await self.engine.htf_review.task
        self.now += 21
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        # Generate a fresh recommendation, then expire it during final valuation.
        await self.engine.htf_review.task
        original = self.engine.valuation
        calls = 0

        async def delay(enforce=True):
            nonlocal calls
            calls += 1
            if calls == 3:  # protection, planning, submission
                self.now += 21
            return await original(enforce)

        self.engine.valuation = delay
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.assertIsNone(htf.snapshot(self.engine)["position"])
        self.assertTrue(self.engine.running)
        self.assertEqual(htf.snapshot(self.engine)["pending_signal"], "buy")

    async def test_stop_discards_even_a_response_that_ignores_cancellation(self):
        await self.signal("hold")
        self.now += 20
        entered = asyncio.Event()

        async def late(_):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {**self.jev.decide.return_value, "action": "buy"}

        self.jev.decide.side_effect = late
        await self.engine.tick()
        await asyncio.wait_for(entered.wait(), 1)
        task = self.engine.htf_review.task
        await asyncio.wait_for(self.engine.stop(), 1)
        await task
        self.assertIsNone(self.engine.htf_review.answer)
        self.assertEqual(self.engine.orders(), [])
        self.assertFalse(self.engine.running)

    async def test_incomplete_history_warms_up_without_paying_for_model_calls(self):
        await self.engine.stop()
        with self.store.db:
            self.store.db.execute("DELETE FROM htf_minutes")
        await self.engine.start()
        await self.review()
        self.assertEqual(self.engine.htf_review.available, 719)
        self.assertEqual(self.engine.htf_review.required, 1800)
        self.assertIn("warm-up", self.engine.htf_review.status)
        self.jev.decide.assert_not_awaited()
        self.assertEqual(self.engine.orders(), [])

    async def test_delayed_minute_boundary_is_retried_without_waiting_for_next_minute(self):
        with self.store.db:
            self.store.db.execute("DELETE FROM htf_minutes")
        self.spot.ohlc.side_effect = [self.rows[-720:], self.rows[-719:] + [minute(self.end)]]
        await self.review()
        self.assertEqual(self.engine.htf_review.available, 0)
        self.assertIsNone(self.engine.htf_review.cutoff)
        self.now += 20
        await self.review()
        self.assertEqual(
            self.engine.htf_review.available, 720
        )  # Includes the older archived minute.
        self.assertEqual(self.spot.ohlc.await_count, 2)
        self.jev.decide.assert_not_awaited()

    async def test_futures_rolling_review_accepts_unavailable_vwap_and_opens_qualified_short(self):
        from tests.test_futures import PAIR, fake_futures

        await self.engine.close()
        client = fake_futures()
        self.engine = Engine(
            self.store, self.spot, self.jev, lambda *_: None, client, clock=lambda: self.now
        )
        await self.engine.initialize()
        await self.engine.configure({**self.engine.settings, "product": "futures", "pair": PAIR.id})
        self.rows = [
            [
                r[0],
                str(200 - dec(r[1]) / 100),
                str(200 - dec(r[3]) / 100),
                str(200 - dec(r[2]) / 100),
                str(200 - dec(r[4]) / 100),
                "0",
                "10",
                1,
            ]
            for r in self.rows
        ]  # Futures publishes no VWAP; do not invent one.
        rolling_rows(self.store, PAIR, self.rows, self.end, 60)
        client.completed_candles = AsyncMock(side_effect=lambda *_: self.rows[-720:])
        await self.engine.start()
        await self.review()
        self.now += 60
        self.end += 60
        row = minute(self.end - 60, dec("100.05"))
        row[5] = "0"
        self.rows.append(row)
        self.jev.decide.return_value["action"] = "sell"
        await self.review()
        self.assertEqual(self.engine.futures.position(PAIR), 0)
        order = self.engine.orders()[-1]
        self.now += 1
        crossing = book(
            PAIR, str(dec(order["price"]) + PAIR.tick), str(dec(order["price"]) + 2 * PAIR.tick)
        )
        crossing.received = self.now
        client.book.side_effect = lambda _: crossing
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertLess(self.engine.futures.position(PAIR), 0)
        self.assertEqual(self.engine.orders()[0]["side"], "sell")
        self.assertEqual(self.engine.orders()[0]["mode"], "dry-run")
        client.request.assert_not_awaited()
        self.now += 200  # Manual reduction must not inherit an expired model approval.
        before = self.engine.futures.position(PAIR)
        await self.engine.stop()
        await self.engine.close_futures("REDUCE FUTURES POSITION")
        self.assertLess(abs(self.engine.futures.position(PAIR)), abs(before))
        self.assertLessEqual(self.engine.futures.position(PAIR), 0)
        self.assertTrue(self.engine.orders()[-1]["reduce_only"])

    async def test_expired_exit_restores_original_plan_and_does_not_stop_protection(self):
        await self.signal()
        original_plan = htf.snapshot(self.engine)["position"]
        self.now += 20
        self.jev.decide.return_value["action"] = "sell"
        await self.engine.tick()
        await self.engine.htf_review.task
        original = self.engine.valuation
        calls = 0

        async def delay(enforce=True):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.now += 21
            return await original(enforce)

        self.engine.valuation = delay
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertEqual(htf.snapshot(self.engine)["position"], original_plan)
        self.assertTrue(self.engine.running)

    async def test_passive_miss_is_consumed_without_taker_fallback_or_phantom_position(self):
        await self.signal(fill=False)
        order = self.engine.orders()[-1]
        self.assertTrue(order["maker"])
        self.assertEqual(order["price"], "9990")
        self.assertEqual(order["fee_bps"], "25")
        self.assertIsNone(htf.snapshot(self.engine)["position"])
        self.assertIn("entry_attempt", htf.snapshot(self.engine))
        self.assertLessEqual(order["expires"], order["created"] + 20)
        self.now += 10
        await self.review()
        self.assertEqual(self.engine.orders()[-1]["status"], "canceled")
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertNotIn("entry_attempt", htf.snapshot(self.engine))
        self.now += 20
        await self.review()
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertIsNone(htf.snapshot(self.engine)["pending_signal"])
        self.spot.add.assert_not_awaited()

    async def test_expired_quote_and_touch_cannot_fill(self):
        await self.signal(fill=False)
        order = self.engine.orders()[-1]
        self.now = order["expires"] + 1
        self.spot.trades.return_value = (
            [
                [order["price"], "100", order["created"] + 1, "s"],
                ["9980", "100", order["created"] - 1, "s"],
                ["9980", "100", order["expires"] + 0.5, "s"],
            ],
            "later",
        )
        await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertEqual(self.engine.orders()[0]["status"], "expired")
        self.assertIsNone(htf.snapshot(self.engine)["position"])

    async def test_partial_passive_fill_gets_protection_and_target_exit_without_jev(self):
        await self.signal(fill=False)
        order = self.engine.orders()[-1]
        await self.fill_entry(".5")
        held = self.engine.balance(BTC.base)
        self.assertGreater(held, 0)
        self.assertLess(held, dec(order["volume"]))
        self.assertEqual(self.engine.orders()[0]["status"], "canceled")
        plan = htf.snapshot(self.engine)["position"]
        self.assertEqual(htf.owned(self.engine, plan), held)
        self.assertEqual(
            dec(self.engine.orders()[0]["fee"]), held * dec(order["price"]) * dec(".0025")
        )
        calls = self.jev.decide.await_count
        target = dec(plan["target"])
        self.spot.book.side_effect = lambda p: book(p, str(target), str(target + p.tick))
        self.spot.marks.side_effect = lambda pairs: {p.id: target for p in pairs}
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertEqual(self.jev.decide.await_count, calls)
        self.assertFalse(self.engine.orders()[-1]["maker"])
        self.assertIn("target reached", self.engine.orders()[-1]["reason"])

    async def test_restart_recovers_fill_lineage_before_resuming_protection(self):
        await self.signal(fill=False)
        order = self.engine.orders()[-1]
        self.now += 1
        self.spot.trades.return_value = ([["9980", ".001", self.now, "s"]], "filled")
        await self.engine.paper_makers()  # Crash between settlement and the next HTF run.
        self.assertGreater(self.engine.balance(BTC.base), 0)
        self.assertIn("entry_attempt", htf.snapshot(self.engine))
        await self.engine.htf_review.close()
        self.engine = Engine(
            self.store, self.spot, self.jev, lambda *_: None, clock=lambda: self.now
        )
        await self.engine.initialize()
        await self.engine.start()
        plan = htf.snapshot(self.engine)["position"]
        self.assertEqual(plan["id"], order["htf_id"])
        self.assertEqual(htf.owned(self.engine, plan), self.engine.balance(BTC.base))
        self.assertNotIn("entry_attempt", htf.snapshot(self.engine))

    async def test_unfilled_residual_topup_keeps_original_plan(self):
        await self.signal(fill=False)
        await self.fill_entry(".01")  # Less than the exchange's minimum order volume.
        previous = htf.snapshot(self.engine)["position"]
        self.assertIsNotNone(previous)
        self.assertLess(self.engine.balance(BTC.base), BTC.minimum)
        self.now += 20
        state = htf.snapshot(self.engine)
        state["entry_signal"] = "hold"  # A fresh edge; setup arithmetic is exercised elsewhere.
        self.store.put(htf.key(self.engine), state)
        await self.review()
        self.assertEqual(len(self.engine.orders()), 2)
        self.assertEqual(htf.snapshot(self.engine)["position"], previous)
        self.assertEqual(self.engine.orders()[-1]["htf_id"], previous["id"])
        self.now += 5
        await self.engine.stop()
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(htf.snapshot(self.engine)["position"], previous)
        self.assertNotIn("entry_attempt", htf.snapshot(self.engine))

    async def test_approval_cannot_chase_a_quote_that_loses_pullback_eligibility(self):
        await self.signal("hold")
        self.now += 20
        self.jev.decide.return_value["action"] = "buy"
        await self.engine.tick()
        await self.engine.htf_review.task
        self.assertIsNotNone(self.engine.htf_review.answer)
        self.spot.book.side_effect = lambda p: book(p, "10600", "10610")
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.assertIsNone(htf.snapshot(self.engine)["pending_signal"])
        self.assertTrue(self.engine.running)

    async def test_range_candidate_is_logged_only_and_never_calls_order_api(self):
        prices = [100] * 10 + [98, 102] * 9 + [94, 96]
        self.rows = [minute(self.end - (1800 - i) * 60, prices[i // 60]) for i in range(1800)]
        rolling_rows(self.store, BTC, self.rows, self.end, 60)
        self.spot.book.side_effect = lambda p: book(p, "95.9", "96.1")
        before = self.engine.ledger()
        await self.review()
        observations = [e for e in self.store.history() if e["kind"] == "htf-observation"]
        self.assertEqual(len(observations), 1)
        self.assertTrue(observations[0]["data"]["range"]["eligible"])
        self.assertEqual(self.engine.ledger(), before)
        self.assertEqual(self.engine.orders(), [])
        self.jev.decide.assert_not_awaited()
        self.now += 20
        await self.review()
        self.assertEqual(
            len([e for e in self.store.history() if e["kind"] == "htf-observation"]), 1
        )
        self.spot.add.assert_not_awaited()

    async def test_model_cannot_override_permissions_or_enable_live_passive_entry(self):
        await self.signal("sell")
        self.assertEqual(self.engine.orders(), [])
        self.assertEqual(self.jev.decide.call_args.args[0]["allowed_actions"], ["hold", "buy"])
        self.engine.mode = "trading"
        with self.assertRaisesRegex(SafetyError, "paper-only"):
            await self.engine.place(
                BTC,
                "buy",
                dec(".002"),
                dec(9990),
                book(),
                maker=True,
                review={"observed_at": self.now, "expires_at": self.now + 20},
            )
        self.spot.add.assert_not_awaited()
        self.engine.mode = "dry-run"

    async def test_missing_jev_key_rejects_start(self):
        await self.engine.stop()
        self.jev.key = ""
        with self.assertRaisesRegex(SafetyError, "JEV_API_KEY"):
            await self.engine.start()
