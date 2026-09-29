import asyncio
import time
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from kairos import htf
from kairos.domain import Pair, SafetyError, dec
from kairos.market_data import PendingCandle
from kairos.store import Store
from tests.helpers import BTC, book, fake_jev, fake_kraken, htf_baseline
from tests.helpers import RuleEngine as Engine
from tests.test_futures import PAIR, fake_futures


class HTFTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.spot, self.future, self.jev = fake_kraken(), fake_futures(), fake_jev()
        self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None, self.future)
        await self.engine.initialize()
        await self.engine.configure(
            {
                **self.engine.settings,
                "order_size": "25",
                "max_exposure": "100",
                "daily_loss": "20",
                "reinvest_profits": False,
            }
        )

    async def asyncTearDown(self):
        await self.spot.market_data.close()
        self.store.close()

    async def enter(self, product="spot", short=False):
        pair = PAIR if product == "futures" else BTC
        await self.engine.configure({**self.engine.settings, "product": product, "pair": pair.id})
        if short:
            rows = (
                self.future.completed_candles.return_value
                if product == "futures"
                else self.spot.candles.return_value
            )
            for i, row in enumerate(rows):
                row[4] = str(11000 - i * 40)
        await self.engine.start()
        await htf_baseline(
            self.engine,
            self.future.completed_candles if product == "futures" else self.spot.candles,
        )
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertIsNotNone(htf.snapshot(self.engine)["position"])
        self.assertTrue(htf.quantity(self.engine, pair))
        self.jev.decide.assert_not_awaited()
        return pair

    async def xrp_market(self, minimum="1.65"):
        pair = Pair(
            "XXRPZUSD",
            "XRP/USD",
            "XXRP",
            "ZUSD",
            dec(".00001"),
            dec(".00000001"),
            dec(minimum),
            dec(".5"),
        )
        self.spot.pairs[pair.id] = pair
        self.spot.fees.return_value = ({pair.id: dec(80)}, {pair.id: dec(80)})
        self.spot.book.side_effect = lambda p: book(p, "1.55668", "1.55670", "1000")
        self.spot.marks.side_effect = lambda pairs: {p.id: dec("1.55668") for p in pairs}
        await self.engine.configure(
            {
                **self.engine.settings,
                "pair": pair.id,
                "paper_balance": "250",
                "order_size": "100",
                "max_exposure": "100",
                "reinvest_profits": True,
            }
        )
        return pair

    async def enter_xrp(self, minimum="1.65"):
        pair = await self.xrp_market(minimum)
        await self.engine.start()
        await htf_baseline(self.engine, self.spot.candles)
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        return pair

    def xrp_quote(self, bid, quantity="1000"):
        self.spot.book.side_effect = lambda p: book(p, bid, str(dec(bid) + dec(".00002")), quantity)
        self.spot.marks.side_effect = lambda pairs: {p.id: dec(bid) for p in pairs}

    async def xrp_residual(self, minimum="1.65"):
        pair = await self.enter_xrp(minimum)
        held = htf.quantity(self.engine, pair)
        state = htf.snapshot(self.engine)
        state["position"]["deadline"] = time.time() - 1
        self.store.put(htf.key(self.engine), state)
        self.spot.book.side_effect = lambda p: book(
            p, "1.55668", "1.55670", str(held - dec(".063055"))
        )
        await self.engine.tick()
        self.assertEqual(htf.quantity(self.engine, pair), dec(".063055"))
        self.spot.book.side_effect = lambda p: book(p, "1.55668", "1.55670", "1000")
        return pair

    def next_trend(self, rising=True):
        for i, row in enumerate(self.spot.candles.return_value):
            row[0] += 3600
            row[4] = str(9000 + i * 40 if rising else 11000 - i * 40)

    async def test_start_skips_existing_signal_until_a_later_fresh_transition(self):
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.assertIn("startup baseline", self.engine.latest_decision["reason"])
        self.assertEqual(htf.snapshot(self.engine)["entry_signal"], "buy")
        self.next_trend()
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.assertIn("already active", self.engine.latest_decision["reason"])
        self.next_trend(rising=False)
        await self.engine.tick()
        # An intrabar change on the same completed-candle timestamp is not a fresh signal.
        for i, row in enumerate(self.spot.candles.return_value):
            row[4] = str(9000 + i * 40)
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.next_trend()
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertEqual(self.engine.orders()[0]["side"], "buy")

    async def test_resume_and_recreated_engine_rebaseline_even_an_already_seen_candle(self):
        await self.engine.start()
        await htf_baseline(self.engine, self.spot.candles)
        for restart in (False, True):
            await self.engine.stop()
            if restart:
                self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None, self.future)
                await self.engine.initialize()
            await self.engine.start(
                restart=restart, confirmation="RESTART ENGINE" if restart else None
            )
            await self.engine.tick()
            self.assertEqual(self.engine.orders(), [])
            self.assertIn("startup baseline", self.engine.latest_decision["reason"])
            self.assertEqual(htf.snapshot(self.engine)["entry_signal"], "buy")
        self.next_trend()
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.next_trend(rising=False)
        await self.engine.tick()
        self.next_trend()
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)

    async def test_rebaseline_never_delays_an_existing_trend_reversal_exit(self):
        await self.enter()
        await self.engine.stop()
        self.next_trend(rising=False)
        await self.engine.start()
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(htf.quantity(self.engine, BTC), 0)
        self.assertEqual(self.engine.orders()[-1]["side"], "sell")
        self.assertIn("trend reversal", self.engine.latest_decision["reason"])

    async def test_running_mode_change_cannot_use_a_previous_mode_session_signal(self):
        await self.engine.configure({**self.engine.settings, "live_budget": "100"})
        self.spot.allow_live = True
        self.store.put(
            htf.key(self.engine, "trading"),
            {"position": None, "last_candle": None, "entry_signal": "hold"},
        )
        await self.engine.start()
        await self.engine.tick()
        await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        self.assertTrue(self.engine.running)
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.spot.add.assert_not_awaited()
        self.assertIn("startup baseline", self.engine.latest_decision["reason"])

    async def test_existing_short_signal_also_requires_a_new_transition(self):
        await self.engine.configure({**self.engine.settings, "product": "futures", "pair": PAIR.id})
        rows = self.future.completed_candles.return_value
        for i, row in enumerate(rows):
            row[4] = str(11000 - i * 40)
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.assertEqual(htf.snapshot(self.engine)["entry_signal"], "sell")
        for row in rows:
            row[0] += 3600
            row[4] = "10000"
        await self.engine.tick()
        for i, row in enumerate(rows):
            row[0] += 3600
            row[4] = str(11000 - i * 40)
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertLess(htf.quantity(self.engine, PAIR), 0)
        self.assertEqual(len(self.engine.orders()), 1)

    async def test_entry_leaves_growth_headroom_and_does_not_sell_a_small_gain(self):
        pair = await self.enter_xrp()
        self.assertGreater(htf.quantity(self.engine, pair), 0)
        self.assertLessEqual(dec(self.engine.orders()[0]["cost"]), dec(80))
        self.assertLessEqual(dec(self.engine.exposure), self.engine.limits()[1] * dec(".8"))
        original = htf.snapshot(self.engine)["position"]
        self.xrp_quote("1.56292")  # Approximately +0.4%, larger than the failed LINK trade's gain.
        await self.engine.tick()
        self.assertGreater(dec(self.engine.exposure), self.engine.limits()[1] * dec(".8"))
        self.assertLess(dec(self.engine.exposure), self.engine.limits()[1])
        self.next_trend()
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertEqual(htf.snapshot(self.engine)["position"], original)

    async def test_recorded_link_moves_keep_small_gain_open_but_still_stop_the_loss(self):
        # Recorded average fills from the two September 26 paper round trips.
        # Isolated price-move regressions, not a reconstruction of intervening candles.
        pair = Pair(
            "LINKUSD",
            "LINK/USD",
            "LINK",
            "ZUSD",
            dec(".00001"),
            dec(".00000001"),
            dec(".1"),
            dec(".5"),
        )
        self.spot.pairs[pair.id] = pair
        self.spot.fees.return_value = ({pair.id: dec(80)}, {pair.id: dec(80)})
        for entry, later, stopped in (
            ("14.26481", "14.31509", False),
            ("14.33723", "13.88164", True),
        ):
            with self.subTest(entry=entry):
                await self.engine.stop()
                await self.engine.configure(
                    {
                        **self.engine.settings,
                        "pair": pair.id,
                        "paper_balance": "250",
                        "order_size": "100",
                        "max_exposure": "100",
                        "reinvest_profits": True,
                    }
                )
                await self.engine.reset_paper()
                self.spot.book.side_effect = lambda p, entry=entry: book(
                    p, str(dec(entry) - dec(".00002")), entry
                )
                self.spot.marks.side_effect = lambda pairs, entry=entry: {
                    p.id: dec(entry) - dec(".00002") for p in pairs
                }
                await self.engine.start()
                await htf_baseline(self.engine, self.spot.candles)
                await self.engine.tick()
                original = htf.snapshot(self.engine)["position"]
                held = htf.quantity(self.engine, pair)
                self.assertGreater(held, 0)
                self.assertLessEqual(dec(self.engine.orders()[-1]["cost"]), 80)
                count = len(self.engine.orders())
                self.spot.book.side_effect = lambda p, later=later: book(
                    p, later, str(dec(later) + dec(".00002"))
                )
                self.spot.marks.side_effect = lambda pairs, later=later: {
                    p.id: dec(later) for p in pairs
                }
                await self.engine.tick()
                self.assertTrue(self.engine.running, self.engine.last_error)
                if stopped:
                    self.assertEqual(htf.quantity(self.engine, pair), 0)
                    self.assertIn("protective stop", self.engine.latest_decision["reason"])
                    self.assertEqual(len(self.engine.orders()), count + 1)
                else:
                    self.assertEqual(htf.quantity(self.engine, pair), held)
                    self.assertEqual(htf.snapshot(self.engine)["position"], original)
                    self.assertEqual(len(self.engine.orders()), count)

    async def test_exposure_breach_trims_to_target_once_without_resetting_protection(self):
        pair = await self.enter_xrp()
        held = htf.quantity(self.engine, pair)
        original = htf.snapshot(self.engine)["position"]
        self.xrp_quote("2.30")
        await self.engine.tick()  # Same completed candle: risk management cannot wait an hour.
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertGreater(htf.quantity(self.engine, pair), 0)
        self.assertLess(htf.quantity(self.engine, pair), held)
        self.assertIn("exposure trim", self.engine.latest_decision["reason"])
        self.assertLessEqual(dec(self.engine.exposure), self.engine.limits()[1] * dec(".8"))
        plan = htf.snapshot(self.engine)["position"]
        self.assertTrue(plan["trim_pending"])
        self.assertEqual({k: v for k, v in plan.items() if k != "trim_pending"}, original)
        await self.engine.tick()
        self.assertEqual(htf.snapshot(self.engine)["position"], original)
        self.xrp_quote("2.32")
        await self.engine.tick()
        self.assertGreater(dec(self.engine.exposure), self.engine.limits()[1] * dec(".8"))
        self.assertLess(dec(self.engine.exposure), self.engine.limits()[1])
        self.assertEqual(len(self.engine.orders()), 2)
        htf.prepare(self.engine)

    async def test_partial_trim_continues_below_hard_cap_and_survives_restart(self):
        pair = await self.enter_xrp()
        original = htf.snapshot(self.engine)["position"]
        await self.engine.stop()
        await self.engine.configure(
            {**self.engine.settings, "order_size": "5", "reinvest_profits": False}
        )
        await self.engine.start()
        self.xrp_quote("2.00", "2")
        await self.engine.tick()
        self.assertEqual(self.engine.orders()[-1]["status"], "canceled")
        self.assertLess(dec(self.engine.exposure), 100)
        self.assertGreater(dec(self.engine.exposure), 80)
        self.assertTrue(htf.snapshot(self.engine)["position"]["trim_pending"])
        await self.engine.stop()
        self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None, self.future)
        await self.engine.initialize()
        await self.engine.start()
        for _ in range(10):
            await self.engine.tick()
            if not htf.snapshot(self.engine)["position"].get("trim_pending"):
                break
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertGreater(htf.quantity(self.engine, pair), 0)
        self.assertLessEqual(dec(self.engine.exposure), 80)
        self.assertEqual(htf.snapshot(self.engine)["position"], original)
        for order in self.engine.orders()[1:]:
            self.assertEqual(order["side"], "sell")
            self.assertLessEqual(dec(order["volume"]) * dec(order["price"]), 5)
        count = len(self.engine.orders())
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), count)

    async def test_protective_exits_take_priority_over_pending_trims(self):
        for trigger in ("stop", "deadline", "daily loss", "trend reversal"):
            with self.subTest(trigger=trigger):
                await self.engine.stop()
                await self.engine.reset_paper()
                pair = await self.enter_xrp()
                self.xrp_quote("2.30", "2")
                await self.engine.tick()
                self.assertTrue(htf.snapshot(self.engine)["position"]["trim_pending"])
                self.xrp_quote("2.30")
                if trigger == "stop":
                    self.xrp_quote("1.40")
                elif trigger == "deadline":
                    state = htf.snapshot(self.engine)
                    state["position"]["deadline"] = time.time() - 1
                    self.store.put(htf.key(self.engine), state)
                elif trigger == "daily loss":
                    day = self.store.get("day:dry-run")
                    day["equity"] = "1000"
                    self.store.put("day:dry-run", day)
                else:
                    self.next_trend(rising=False)
                if trigger != "trend reversal":
                    self.spot.candles.side_effect = SafetyError("outage")
                await self.engine.tick()
                self.spot.candles.side_effect = None
                self.assertTrue(self.engine.running, self.engine.last_error)
                self.assertEqual(htf.quantity(self.engine, pair), 0)
                self.assertIsNone(htf.snapshot(self.engine)["position"])
                self.assertIn(trigger, self.engine.latest_decision["reason"])

    async def test_trim_respects_minimum_quantity_and_does_not_force_a_subminimum_order(self):
        pair = await self.enter_xrp(minimum="20")
        self.xrp_quote("2.30")
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(dec(self.engine.orders()[-1]["volume"]), 20)
        self.assertGreater(htf.quantity(self.engine, pair), 0)
        self.assertLessEqual(dec(self.engine.exposure), self.engine.limits()[1] * dec(".8"))

    async def test_trim_rounds_up_to_cost_minimum_without_flattening(self):
        pair = await self.xrp_market()
        pair = replace(pair, cost_minimum=dec(40))
        self.spot.pairs[pair.id] = pair
        await self.engine.start()
        await htf_baseline(self.engine, self.spot.candles)
        await self.engine.tick()
        self.xrp_quote("2.30")
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        order = self.engine.orders()[-1]
        self.assertEqual(order["side"], "sell")
        self.assertGreaterEqual(dec(order["volume"]) * dec(order["price"]), 40)
        self.assertGreater(htf.quantity(self.engine, pair), 0)
        self.assertLessEqual(dec(self.engine.exposure), self.engine.limits()[1] * dec(".8"))

    async def test_unfilled_trim_keeps_ownership_and_retries_only_as_a_reduction(self):
        pair = await self.enter_xrp()
        original = htf.snapshot(self.engine)["position"]
        held = htf.quantity(self.engine, pair)
        self.xrp_quote("2.30")

        def unfilled(pair):
            snapshot = book(pair, "2.30", "2.30002")
            snapshot.fill = lambda *_: (dec(0), dec(0))
            return snapshot

        self.spot.book.side_effect = unfilled
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(htf.quantity(self.engine, pair), held)
        self.assertEqual(dec(self.engine.orders()[-1]["filled"]), 0)
        self.assertEqual(htf.snapshot(self.engine)["position"], {**original, "trim_pending": True})
        self.xrp_quote("2.30")
        await self.engine.tick()
        self.assertEqual([o["side"] for o in self.engine.orders()], ["buy", "sell", "sell"])
        self.assertGreater(htf.quantity(self.engine, pair), 0)
        self.assertLess(htf.quantity(self.engine, pair), held)
        htf.prepare(self.engine)

    async def test_trim_waits_when_order_cap_cannot_meet_market_minimum(self):
        await self.enter_xrp()
        await self.engine.stop()
        await self.engine.configure({**self.engine.settings, "order_size": "1"})
        await self.engine.start()
        self.xrp_quote("2.30")
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertIn(
            "trim pending: order cap below market minimum", self.engine.latest_decision["reason"]
        )
        self.assertTrue(htf.snapshot(self.engine)["position"]["trim_pending"])
        self.assertIsNone(htf.snapshot(self.engine)["position"]["exit_reason"])

    async def test_derivative_entries_bound_marked_exposure_not_just_limit_notional(self):
        self.future.market.return_value["markPrice"] = "105"
        for product in ("margin", "futures"):
            for short in (False, True):
                with self.subTest(product=product, short=short):
                    await self.engine.stop()
                    await self.engine.reset_paper()
                    await self.engine.configure({**self.engine.settings, "order_size": "100"})
                    await self.enter(product, short=short)
                    self.assertLessEqual(dec(self.engine.exposure), 80)
                    self.assertEqual(self.engine.orders()[-1]["side"], "sell" if short else "buy")

    async def test_derivative_trims_reduce_longs_and_shorts_without_reversing(self):
        self.future.market.return_value["markPrice"] = "98"
        for product in ("margin", "futures"):
            for short in (False, True):
                with self.subTest(product=product, short=short):
                    await self.engine.stop()
                    await self.engine.reset_paper()
                    await self.engine.configure(
                        {**self.engine.settings, "order_size": "25", "max_exposure": "100"}
                    )
                    pair = await self.enter(product, short=short)
                    original = htf.snapshot(self.engine)["position"]
                    held = htf.quantity(self.engine, pair)
                    await self.engine.stop()
                    await self.engine.configure(
                        {**self.engine.settings, "order_size": "10", "max_exposure": "20"}
                    )
                    await self.engine.start()
                    self.engine.place = AsyncMock(wraps=self.engine.place)
                    await self.engine.tick()
                    self.assertTrue(self.engine.running, self.engine.last_error)
                    self.assertTrue(self.engine.place.call_args.kwargs["exit_only"])
                    remaining = htf.quantity(self.engine, pair)
                    self.assertGreater(held * remaining, 0)
                    self.assertLess(abs(remaining), abs(held))
                    self.assertEqual(self.engine.orders()[-1]["side"], "buy" if short else "sell")
                    self.assertLessEqual(dec(self.engine.exposure), 16)
                    await self.engine.tick()
                    self.assertEqual(htf.snapshot(self.engine)["position"], original)
                    htf.prepare(self.engine)

    async def test_residual_survives_restart_joins_eligible_entry_and_eventually_closes(self):
        pair = await self.xrp_residual()
        original = htf.snapshot(self.engine)["position"]
        await self.engine.stop()
        self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None, self.future)
        await self.engine.initialize()
        await self.engine.start(restart=True, confirmation="RESTART ENGINE")
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(len(self.engine.orders()), 2)
        self.assertEqual(htf.quantity(self.engine, pair), dec(".063055"))
        self.assertIn("residual", self.engine.latest_decision["reason"])
        self.next_trend(rising=False)
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 2)  # No purchase just to clear dust.
        self.next_trend()
        await self.engine.tick()
        plan = htf.snapshot(self.engine)["position"]
        self.assertEqual(len(self.engine.orders()), 3)
        self.assertEqual(plan["id"], original["id"])
        self.assertIsNone(plan["exit_reason"])
        self.assertGreater(plan["deadline"], original["deadline"])
        self.assertGreater(htf.quantity(self.engine, pair), dec(".063055"))
        htf.prepare(self.engine)
        self.next_trend()
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 3)  # No ordinary pyramiding.
        state = htf.snapshot(self.engine)
        state["position"]["deadline"] = time.time() - 1
        self.store.put(htf.key(self.engine), state)
        for _ in range(4):
            if htf.quantity(self.engine, pair):
                await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(htf.quantity(self.engine, pair), 0)
        self.assertIsNone(htf.snapshot(self.engine)["position"])
        self.assertEqual(
            sum(dec(o["filled"]) * (1 if o["side"] == "buy" else -1) for o in self.engine.orders()),
            0,
        )

    async def test_unfilled_residual_entry_keeps_old_plan_and_never_retries_same_candle(self):
        pair = await self.xrp_residual()
        original = htf.snapshot(self.engine)["position"]
        self.next_trend(rising=False)
        await self.engine.tick()
        self.next_trend()
        self.spot.book.side_effect = lambda p: book(p, "1.55668", "1.55670", "0")
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(htf.snapshot(self.engine)["position"], original)
        self.assertEqual(htf.quantity(self.engine, pair), dec(".063055"))
        self.assertEqual(len(self.engine.orders()), 3)
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 3)
        await self.engine.stop()
        with self.assertRaises(SafetyError):
            await self.engine.paper_order_history(
                self.engine.orders()[0]["id"], "delete", "DELETE PAPER ORDER"
            )

    async def test_cost_only_residual_exits_without_candles_when_tradeable_again(self):
        pair = await self.xrp_residual(minimum=".00001")
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(len(self.engine.orders()), 2)
        self.spot.book.side_effect = lambda p: book(p, "10", "10.00001", "1000")
        self.spot.marks.side_effect = lambda pairs: {p.id: dec(10) for p in pairs}
        self.spot.candles.side_effect = SafetyError("outage")
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(htf.quantity(self.engine, pair), 0)
        self.assertEqual(self.engine.orders()[-1]["side"], "sell")

    async def test_small_exit_cap_waits_without_mistaking_tradeable_holdings_for_dust(self):
        await self.enter()
        await self.engine.stop()
        await self.engine.configure({**self.engine.settings, "order_size": ".01"})
        state = htf.snapshot(self.engine)
        state["position"]["deadline"] = time.time() - 1
        self.store.put(htf.key(self.engine), state)
        await self.engine.start()
        self.next_trend()
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertIn("order cap below market minimum", self.engine.latest_decision["reason"])
        self.assertGreater(htf.quantity(self.engine, BTC), BTC.minimum)

    async def test_short_residual_never_reverses_and_only_joins_a_new_short(self):
        await self.enter("margin", short=True)
        state = htf.snapshot(self.engine)
        state["position"]["deadline"] = time.time() - 1
        self.store.put(htf.key(self.engine), state)
        self.spot.book.side_effect = lambda p: book(
            p, qty=str(abs(htf.quantity(self.engine, p)) - p.lot)
        )
        for _ in range(2):
            await self.engine.tick()
        self.assertEqual(htf.quantity(self.engine, BTC), -BTC.lot)
        self.spot.book.side_effect = lambda p: book(p)
        self.next_trend()
        count = len(self.engine.orders())
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(len(self.engine.orders()), count)
        self.assertEqual(htf.quantity(self.engine, BTC), -BTC.lot)
        self.next_trend(rising=False)
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(self.engine.orders()[-1]["side"], "sell")
        self.assertLess(htf.quantity(self.engine, BTC), -BTC.minimum)
        htf.prepare(self.engine)

    async def test_live_spot_uses_persisted_plan_and_guarded_real_order_path(self):
        await self.engine.configure({**self.engine.settings, "live_budget": "100"})
        self.spot.allow_live = True
        await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        await self.engine.start()
        await htf_baseline(self.engine, self.spot.candles)
        self.spot.add.assert_not_awaited()
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.spot.add.assert_awaited_once()
        self.jev.decide.assert_not_awaited()
        order = self.engine.orders()[0]
        plan = htf.snapshot(self.engine)["position"]
        self.assertEqual(order["htf_id"], plan["id"])
        self.assertEqual(order["mode"], "trading")
        self.assertEqual(self.spot.add.call_args.args[0]["timeinforce"], "IOC")
        htf.prepare(self.engine)

    async def test_no_pyramiding_and_plan_survives_restart_without_model(self):
        await self.enter()
        original = htf.snapshot(self.engine)["position"]
        for row in self.spot.candles.return_value:
            row[0] += 3600
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        await self.engine.stop()
        self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None, self.future)
        await self.engine.initialize()
        self.assertFalse(self.engine.running)
        self.assertEqual(htf.snapshot(self.engine)["position"], original)
        await self.engine.start()
        htf.prepare(self.engine)

    async def test_deadline_exit_uses_no_candles_and_retains_original_plan_settings(self):
        await self.enter()
        await self.engine.stop()
        plan = htf.snapshot(self.engine)
        plan["position"]["deadline"] = time.time() - 1
        self.store.put(htf.key(self.engine), plan)
        await self.engine.configure({**self.engine.settings, "htf_stop_bps": "1000"})
        self.assertEqual(htf.snapshot(self.engine)["position"]["stop"], plan["position"]["stop"])
        self.spot.candles.side_effect = SafetyError("outage")
        self.spot.candles.reset_mock()
        await self.engine.start()
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.spot.candles.assert_not_awaited()
        self.assertIn("deadline", self.engine.latest_decision["reason"])

    async def test_stop_exit_partial_fills_remain_owned_and_never_reverse(self):
        await self.enter()
        held = self.engine.balance(BTC.base)
        self.spot.book.side_effect = lambda pair: book(pair, "9500", "9501", "0.001")
        self.spot.candles.side_effect = SafetyError("outage")
        await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), held - dec(".001"))
        self.assertIsNotNone(htf.snapshot(self.engine)["position"]["exit_reason"])
        self.spot.book.side_effect = lambda pair: book(pair)
        for _ in range(4):
            if self.engine.balance(BTC.base):
                await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual({o["side"] for o in self.engine.orders()[1:]}, {"sell"})

    async def test_trend_reversal_closes_even_when_move_is_below_costs(self):
        await self.enter()
        for i, row in enumerate(self.spot.candles.return_value):
            row[0] += 3600
            row[4] = str(10000 - i)
        await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertIn("trend reversal", self.engine.latest_decision["reason"])

    async def test_daily_loss_still_allows_only_owned_reduction(self):
        await self.enter()
        day = self.store.get("day:dry-run")
        day["equity"] = "1100"
        self.store.put("day:dry-run", day)
        await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertIn("daily loss limit", self.engine.latest_decision["reason"])
        for row in self.spot.candles.return_value:
            row[0] += 3600
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertEqual(len(self.engine.orders()), 2)

    async def test_product_short_positions_reduce_at_deadline_without_reversal(self):
        for product in ("margin", "futures"):
            with self.subTest(product=product):
                await self.engine.stop()
                await self.engine.reset_paper()
                pair = await self.enter(product, short=True)
                self.assertLess(htf.quantity(self.engine, pair), 0)
                await self.engine.stop()
                await self.engine.configure({**self.engine.settings, "order_size": "50"})
                await self.engine.start()
                state = htf.snapshot(self.engine)
                state["position"]["deadline"] = time.time() - 1
                self.store.put(htf.key(self.engine), state)
                for _ in range(10):
                    if htf.quantity(self.engine, pair):
                        await self.engine.tick()
                self.assertEqual(htf.quantity(self.engine, pair), 0)
                self.assertTrue(self.engine.running, self.engine.last_error)

    async def test_bounded_exits_leave_a_tradeable_tranche_instead_of_avoidable_dust(self):
        await self.enter("margin", short=True)
        state = htf.snapshot(self.engine)
        state["position"]["deadline"] = time.time() - 1
        self.store.put(htf.key(self.engine), state)
        await self.engine.tick()
        self.assertLessEqual(htf.quantity(self.engine, BTC), -BTC.minimum)
        await self.engine.tick()
        self.assertEqual(htf.quantity(self.engine, BTC), 0)
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertIsNone(htf.snapshot(self.engine)["position"])
        self.assertTrue(all(dec(o["volume"]) * dec(o["price"]) <= 25 for o in self.engine.orders()))

    async def test_unrelated_inventory_cannot_be_adopted_or_exit_only_sold(self):
        ledger = self.engine.ledger()
        ledger["balances"][BTC.base] = "0.002"
        self.store.put("ledger:dry-run", ledger)
        with self.assertRaisesRegex(SafetyError, "unrelated"):
            await self.engine.start()
        self.engine.running = True
        with self.assertRaises(SafetyError):
            await self.engine.place(BTC, "sell", dec(".002"), dec(9990), book(), exit_only=True)
        self.assertEqual(self.engine.orders(), [])

    async def test_strategy_switch_and_history_deletion_cannot_remove_protection(self):
        await self.enter()
        await self.engine.stop()
        with self.assertRaisesRegex(SafetyError, "HTF position"):
            await self.engine.configure({**self.engine.settings, "strategy": "dca"})
        with self.assertRaises(SafetyError):
            await self.engine.paper_order_history(
                self.engine.orders()[0]["id"], "delete", "DELETE PAPER ORDER"
            )
        await self.engine.reset_paper()
        self.assertIsNone(htf.snapshot(self.engine)["position"])
        await self.engine.configure({**self.engine.settings, "strategy": "dca"})

    async def test_unfilled_entry_does_not_lock_strategy_switch_or_repeat_candle(self):
        def unfilled(pair):
            snapshot = book(pair)
            snapshot.fill = lambda *_: (dec(0), dec(0))
            return snapshot

        self.spot.book.side_effect = unfilled
        await self.engine.start()
        await htf_baseline(self.engine, self.spot.candles)
        await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertIsNone(htf.snapshot(self.engine)["position"])
        await self.engine.tick()
        self.next_trend()
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        await self.engine.stop()
        await self.engine.configure({**self.engine.settings, "strategy": "dca"})

    async def test_stop_during_read_cannot_submit_and_does_not_duplicate_entry(self):
        async def interrupted(*_):
            self.engine.running = False
            return self.spot.candles.return_value

        await self.engine.start()
        await htf_baseline(self.engine, self.spot.candles)
        self.spot.candles.side_effect = interrupted
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.spot.candles.side_effect = None
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])

    async def test_boundary_errors_recover_but_exhausted_budget_stops(self):
        pending = PendingCandle(int(time.time()))
        self.spot.candles.side_effect = pending
        await self.engine.start()
        for i in (1, 2):
            await self.engine.tick()
            self.assertTrue(self.engine.running)
            self.assertEqual(self.engine.candle_retries, i)
            self.assertEqual(self.engine.orders(), [])
        self.spot.candles.side_effect = None
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertIsNone(self.engine.last_error)
        self.assertEqual(self.engine.candle_retries, 0)
        self.spot.candles.side_effect = pending
        for _ in range(3):
            await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertIn("recovery exhausted", self.engine.last_error)

    async def test_malformed_data_and_uncertain_orders_never_auto_resume(self):
        self.spot.candles.side_effect = SafetyError("Invalid execution candle")
        await self.engine.start()
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.candle_retries, 0)
        self.engine.recovery_required = True
        with self.assertRaisesRegex(SafetyError, "Reconcile"):
            await self.engine.start(restart=True, confirmation="RESTART ENGINE")
        self.assertFalse(self.engine.running)

    async def test_restart_checks_confirmation_permissions_and_preserves_state(self):
        self.engine.last_error = "Execution candles are stale or incomplete"
        balances = self.engine.ledger()
        with self.assertRaises(SafetyError):
            await self.engine.start(restart=True)
        await self.engine.start(restart=True, confirmation="RESTART ENGINE")
        self.assertTrue(self.engine.running)
        self.assertIsNone(self.engine.last_error)
        self.assertEqual(self.engine.ledger(), balances)
        self.assertEqual(self.engine.orders(), [])
        await self.engine.stop()
        self.engine.mode = "trading"
        with self.assertRaisesRegex(SafetyError, "ALLOW_LIVE_TRADING"):
            await self.engine.start(restart=True, confirmation="RESTART ENGINE")
        self.assertFalse(self.engine.running)

    async def test_stop_during_restart_preflight_wins(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(*_, **kwargs):
            entered.set()
            await release.wait()

        self.engine.refresh_fees = AsyncMock(side_effect=delayed)
        task = asyncio.create_task(self.engine.start(restart=True, confirmation="RESTART ENGINE"))
        await entered.wait()
        stop = asyncio.create_task(self.engine.stop())
        await asyncio.sleep(0)
        release.set()
        with self.assertRaisesRegex(SafetyError, "canceled by Stop"):
            await task
        await stop
        self.assertFalse(self.engine.running)

    async def test_decision_counts_include_persisted_history_not_only_browser_window(self):
        await self.engine.start()
        for _ in range(230):
            self.engine.event(
                "decision",
                {
                    "action": "hold",
                    "reason": "Costs",
                    "strategy": "htf",
                    "pair": BTC.id,
                    "mode": "dry-run",
                    "state": {"product": "spot"},
                },
            )
        summary = self.engine.snapshot()["decision_summary"]
        self.assertEqual(summary["assessments"], 230)
        self.assertEqual(summary["hold_reasons"], [{"reason": "Costs", "count": 230}])
        self.engine.mode = "trading"
        self.assertEqual(self.engine.decision_summary()["assessments"], 0)
