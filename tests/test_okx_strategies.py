"""Offline native-protocol fixtures, not evidence of hosted strategy fills."""

import asyncio
import copy
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from kairos import htf, okx_cycle, programs, scalping
from kairos.domain import SafetyError, dec
from kairos.okx_engine import OKXEngine
from kairos.store import Store
from tests.test_okx import instrument
from tests.test_okx_engine import Venue, VenueResponse


class DemoStrategyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        market = {
            **instrument(),
            "instId": "BTC-USDC",
            "quoteCcy": "USDC",
            "tradeQuoteCcyList": ["USDC"],
        }
        self.venue = Venue(market)
        self.client = self.venue.client
        await self.client.catalog()
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.engine = OKXEngine(self.store, self.client, AsyncMock(), lambda *_: None)
        self.pair = self.client.resolve("okx-demo:BTC-USDC:USDC")
        self.engine.settings.update(
            pair=self.pair.id,
            quote="USDC",
            strategy="dca",
            dca_amount="10",
            dca_count=2,
            dca_period_seconds=15,
        )
        self.original_configure = self.client.market_data.configure
        self.client.market_data.configure = self.configure
        await self.configure([self.pair])
        self.addAsyncCleanup(self.client.market_data.close)
        self.addAsyncCleanup(self.engine.htf_review.close)

    async def configure(self, pairs, **kwargs):
        session, self.client.session = self.client.session, None
        try:
            await self.original_configure(pairs, **kwargs)
        finally:
            self.client.session = session
        feed = self.client.market_data
        if not feed.task:
            feed.task = asyncio.create_task(asyncio.sleep(1000))
        for pair in pairs:
            self.book(pair)

    def book(self, pair=None):
        pair = pair or self.pair
        feed = self.client.market_data
        arg = feed.argument(pair)
        feed.message({"event": "subscribe", "arg": arg})
        feed.message(
            {
                "arg": arg,
                "data": [
                    {
                        "ts": str(int(time.time() * 1000)),
                        "bids": [["49999.9", "1", "0", "1"]],
                        "asks": [["50000", "1", "0", "1"]],
                    }
                ],
            }
        )
        return feed.books[pair.id]

    async def preview(self, **values):
        return await okx_cycle.preview(
            self.engine,
            {
                "kind": "strategy",
                "pair": self.pair.id,
                "allocation": "500",
                "budget": "25",
                "duration_seconds": 60,
                "max_orders": 4,
                **values,
            },
        )

    async def authorize(self, **values):
        proposal = await self.preview(**values)
        await okx_cycle.authorize(self.engine, proposal["id"], proposal["confirmation"])
        return proposal

    def posts(self):
        return [call for call in self.venue.calls if call[0] == "POST"]

    async def test_dca_real_engine_scheduler_native_fees_and_one_use_consent(self):
        proposal = await self.preview()
        self.assertEqual(self.posts(), [])
        self.assertFalse(self.engine.running)
        self.assertEqual(proposal["markets"][self.pair.id]["quote"], "USDC")
        with self.assertRaises(SafetyError):
            await okx_cycle.authorize(self.engine, proposal["id"], "START")
        await okx_cycle.authorize(self.engine, proposal["id"], proposal["confirmation"])
        for index in range(2):
            if index:
                program = self.store.get(programs.key(self.engine))
                program["started"] -= 16
                program["next_at"] -= 16
                self.store.put(programs.key(self.engine), program)
            self.book()
            await self.engine.tick()
        self.assertEqual(len(self.posts()), 2)
        self.assertEqual(self.store.get("okx-operation")["status"], "STRATEGY_COMPLETE")
        self.assertFalse(self.engine.running or self.engine.armed)
        self.assertGreater(self.engine.balance("BTC"), 0)
        self.assertEqual(self.venue.cash["BTC"], 1 + self.engine.balance("BTC"))
        self.assertEqual(
            self.engine.balance("BTC"),
            sum((dec(o["filled"]) * dec(".999") for o in self.engine.orders()), dec(0)),
        )
        with self.assertRaises(SafetyError):
            await okx_cycle.authorize(self.engine, proposal["id"], proposal["confirmation"])
        self.assertEqual(len(self.posts()), 2)

    async def test_stop_expiry_and_restart_never_replay_a_consumed_slot(self):
        await self.authorize()
        await self.engine.tick()
        await self.engine.stop()
        self.assertEqual(self.store.get("okx-operation")["status"], "STOPPED")
        program, ledger = self.store.get(programs.key(self.engine)), self.engine.ledger()
        self.store.put("settings", self.engine.settings)
        replacement = OKXEngine(self.store, self.client, None, lambda *_: None)
        await replacement.initialize()
        await replacement.tick()
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(replacement.ledger(), ledger)
        self.assertEqual(self.store.get(programs.key(replacement)), program)
        self.assertFalse(replacement.running or replacement.armed)
        self.client.write_guard = self.engine.write_guard
        await self.authorize()
        self.engine.authorization["deadline"] = self.engine.clock() - 1
        await self.engine.tick()
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.store.get("okx-operation")["status"], "STRATEGY_COMPLETE")

    async def test_native_candles_no_forming_bar_promotion_or_parity_fallback(self):
        rows = await self.client.bars(self.pair, 60)
        cutoff = int(time.time()) // 3600 * 3600
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[-1][0], cutoff - 3600)
        self.assertEqual(self.engine.htf_review.policy, "okx-us-confirmed-native-bars-v1")
        self.client.instruments[self.pair.id]["quoteCcy"] = "USD"
        with self.assertRaisesRegex(SafetyError, "parity"):
            await self.client.bars(self.pair, 60)
        self.assertEqual(self.posts(), [])

    async def test_native_candle_duplicates_false_completion_and_gaps_block(self):
        original = await self.client.request(
            "GET",
            "/api/v5/market/candles",
            params={"instId": "BTC-USDC", "bar": "1H", "limit": "31"},
        )
        for transform in (
            lambda rows: rows + [rows[-1]],
            lambda rows: [[*rows[0][:-1], "1"], *rows[1:]],
        ):
            with patch.object(
                self.client, "request", AsyncMock(return_value=transform(copy.deepcopy(original)))
            ):
                with self.assertRaises(SafetyError):
                    await self.client.bars(self.pair, 60)
        self.engine.settings.update(strategy="htf", htf_policy="multibar-v2")
        raw = self.client.request

        async def missing_latest(method, path, **kwargs):
            rows = await raw(method, path, **kwargs)
            return [rows[0], *rows[2:]] if path.endswith("/candles") else rows

        with patch.object(self.client, "request", missing_latest):
            with self.assertRaisesRegex(SafetyError, "30 adjacent"):
                await self.preview()
        self.assertEqual(self.posts(), [])

    async def test_htf_is_separate_protocol_with_baseline_and_native_net_ownership(self):
        self.engine.settings.update(strategy="htf", htf_policy="multibar-v2")
        await self.authorize()
        self.assertFalse(
            self.store.db.execute(
                "SELECT key FROM state WHERE key LIKE 'multibar-trial:%'"
            ).fetchall()
        )
        self.assertEqual(
            self.engine.active_run()["trial_ends_at"], self.engine.authorization["deadline"]
        )
        self.assertIsNotNone(htf.snapshot(self.engine)["baseline_after"])
        state = htf.snapshot(self.engine)
        state["position"] = {"id": "owned-lineage", "entry_limit": "50000", "stop": "49000"}
        self.store.put(htf.key(self.engine), state)
        order = await self.engine.place(self.pair, "buy", dec(".0001"), dec("50000"), self.book())
        self.assertEqual(order["htf_id"], "owned-lineage")
        htf.prepare(self.engine)
        self.assertEqual(htf.owned(self.engine, state["position"]), dec(".0000999"))
        self.assertEqual(self.venue.cash["BTC"], 1 + dec(".0000999"))
        await self.engine.stop()

    async def test_scalp_net_owned_quantity_excludes_unrelated_dust(self):
        await self.authorize()
        await self.engine.tick()
        await self.engine.stop()
        prior = self.engine.balance("BTC")
        self.engine.settings["strategy"] = "scalp"
        await self.authorize()
        self.assertEqual(scalping.quantity(self.engine, self.pair), 0)
        state = scalping.snapshot(self.engine)
        state["position"] = {"id": "scalp-lineage", "entry_limit": "50000", "stop": "49000"}
        self.store.put(scalping.key(self.engine), state)
        await self.engine.place(self.pair, "buy", dec(".0001"), dec("50000"), self.book())
        scalping.prepare(self.engine)
        self.assertEqual(scalping.quantity(self.engine, self.pair), dec(".0000999"))
        self.assertEqual(self.engine.balance("BTC"), prior + dec(".0000999"))
        await self.engine.stop()

    async def test_native_post_only_cancel_fill_and_stop_race(self):
        self.engine.settings["strategy"] = "maker"
        await self.authorize()
        self.venue.defer = True
        self.venue.fill_on_cancel = True
        submitted = asyncio.Event()
        request = self.client.request

        async def observe(method, path, **kwargs):
            result = await request(method, path, **kwargs)
            if method == "POST" and path == "/api/v5/trade/order":
                submitted.set()
            return result

        self.client.request = observe
        placing = asyncio.create_task(
            self.engine.place(
                self.pair, "buy", dec(".0001"), dec("49999.9"), self.book(), maker=True
            )
        )
        await asyncio.wait_for(submitted.wait(), 2)
        # Stop's latch interrupts observation; cancellation may race a real fill.
        self.engine.running = self.engine.armed = False
        await placing
        order = self.engine.orders()[0]
        self.assertEqual(order["payload"]["ordType"], "post_only")
        self.assertEqual(order["status"], "closed")
        self.assertEqual(self.engine.balance("BTC"), dec(".0000999"))
        self.assertEqual(len(self.posts()), 2)
        await self.engine.stop()
        self.assertEqual(len(self.posts()), 2)

    async def test_multibar_native_signal_to_post_only_fill_and_protected_exit(self):
        from tests.test_alpaca_htf_history import bars

        now = time.time()
        end = int(now) // 3600 * 3600
        rows = bars(end)
        for row in rows:
            row[1:6] = [str(dec(value) * 500) for value in row[1:6]]
        self.engine.settings.update(strategy="htf", htf_policy="multibar-v2")
        request = self.client.request

        async def native_history(method, path, **kwargs):
            if path.endswith("/candles"):
                return [
                    [str(r[0] * 1000), *r[1:5], "10", "10", "500000", "1"] for r in reversed(rows)
                ]
            return await request(method, path, **kwargs)

        async def cycle():
            self.book()
            await self.engine.tick()
            if self.engine.htf_review.task:
                await self.engine.htf_review.task
            self.book()
            await self.engine.tick()
            self.assertTrue(self.engine.running, self.engine.last_error)

        self.engine.clock = lambda: now
        with (
            patch("time.time", side_effect=lambda: now),
            patch.object(self.client, "request", native_history),
        ):
            await self.authorize(duration_seconds=7200)
            await cycle()
            self.assertEqual(self.posts(), [])
            data = self.engine.snapshot()["review"]["data"]
            self.assertEqual(data["required_native_bars"], 30)
            self.assertEqual(data["consecutive_native_bars"], 30)
            self.assertIsNone(data["required_minutes"])
            now += 3600
            rows = rows[1:] + [[end, "49975", "51475", "48475", "49975", "49975", "10", 1]]
            await cycle()
            self.assertEqual(len(self.posts()), 1, self.engine.latest_decision)
            self.assertEqual(self.engine.orders()[0]["payload"]["ordType"], "post_only")
            self.engine.jev.decide.assert_not_awaited()
            position = htf.snapshot(self.engine)["position"]
            self.assertTrue(position)
            self.assertLessEqual(position["deadline"], self.engine.authorization["deadline"])
            position["deadline"] = now - 1
            state = htf.snapshot(self.engine)
            state["position"] = position
            self.store.put(htf.key(self.engine), state)
            self.engine.htf_review.cancel()
            self.book()
            with patch.object(
                self.client, "bars", AsyncMock(side_effect=SafetyError("unavailable"))
            ) as missing:
                await self.engine.tick()
                missing.assert_not_awaited()
            self.assertEqual(len(self.posts()), 2, self.engine.last_error)
            self.assertEqual(sum(o["side"] == "sell" for o in self.engine.orders()), 1)
            self.engine.jev.decide.assert_not_awaited()
            await self.engine.stop()

    async def test_scalp_native_band_reentry_and_deadline_exit_skip_model(self):
        from tests.test_scalping import candles

        self.engine.settings["strategy"] = "scalp"
        book = self.book

        def quote(pair=None):
            result = book(pair)
            result.bids[0][0], result.asks[0][0] = dec("49200"), dec("49250")
            return result

        self.book = quote
        request = self.client.request

        async def native_history(method, path, **kwargs):
            if path.endswith("/candles"):
                return [
                    [
                        str(r[0] * 1000),
                        *[str(dec(v) * 500) for v in r[1:5]],
                        "10",
                        "10",
                        "500000",
                        "1",
                    ]
                    for r in reversed(candles())
                ]
            return await request(method, path, **kwargs)

        with patch.object(self.client, "request", native_history):
            await self.authorize(max_orders=2)
            await self.engine.tick()
            self.assertEqual(len(self.posts()), 1, self.engine.last_error)
            state = scalping.snapshot(self.engine)
            self.assertTrue(state["position"])
            state["position"]["deadline"] = time.time() - 1
            self.store.put(scalping.key(self.engine), state)
            with patch.object(
                self.client, "candles", AsyncMock(side_effect=SafetyError("unavailable"))
            ) as missing:
                await self.engine.tick()
                missing.assert_not_awaited()
            self.assertEqual(len(self.posts()), 2, self.engine.last_error)
            self.assertFalse(self.engine.armed)
            self.assertEqual(self.venue.cash["BTC"], 1 + self.engine.balance("BTC"))
            self.engine.jev.decide.assert_not_awaited()

    async def test_maker_model_decisions_use_native_post_only_and_bounded_owned_sales(self):
        self.engine.settings["strategy"] = "maker"
        self.engine.jev.decide.return_value = {
            "action": "buy",
            "confidence": "1",
            "model": "Offline test",
            "probabilities": {},
            "latency_ms": 0,
        }
        await self.authorize(max_orders=2)
        await self.engine.tick()
        self.assertEqual(len(self.posts()), 1, self.engine.last_error)
        held = self.engine.balance("BTC")
        self.engine.jev.decide.return_value["action"] = "sell"
        await self.engine.tick()
        self.assertEqual(len(self.posts()), 2, self.engine.last_error)
        self.assertTrue(all(o["payload"]["ordType"] == "post_only" for o in self.engine.orders()))
        self.assertLessEqual(
            sum(dec(o["filled"]) for o in self.engine.orders() if o["side"] == "sell"), held
        )
        self.assertFalse(self.engine.armed)
        self.assertEqual(self.engine.jev.decide.await_count, 2)

    async def test_bounds_count_fee_increase_and_market_mismatch_never_submit(self):
        self.engine.settings["strategy"] = "maker"
        await self.authorize(max_orders=1)
        for side, price in (("buy", "60000"), ("sell", "40000")):
            with self.assertRaisesRegex(SafetyError, "absolute"):
                await self.engine.place(self.pair, side, dec(".0001"), dec(price), self.book())
        self.engine.fees.rates[self.pair.id]["taker_bps"] = "11"
        with self.assertRaisesRegex(SafetyError, "fee allowance"):
            await self.engine.place(self.pair, "buy", dec(".0001"), dec("50000"), self.book())
        self.engine.fees.rates[self.pair.id]["taker_bps"] = "10"
        self.assertEqual(self.posts(), [])
        await self.engine.place(self.pair, "buy", dec(".0001"), dec("50000"), self.book())
        with self.assertRaisesRegex(SafetyError, "attempt count"):
            await self.engine.place(self.pair, "buy", dec(".0001"), dec("50000"), self.book())
        self.assertEqual(len(self.posts()), 1)

    async def basket(self):
        originals = [
            {**self.venue.instrument, "instId": "BTC-USDC"},
            {**self.venue.instrument, "instId": "ETH-USDC", "baseCcy": "ETH"},
            {
                **self.venue.instrument,
                "instId": "ETH-BTC",
                "baseCcy": "ETH",
                "quoteCcy": "BTC",
                "tradeQuoteCcyList": ["BTC"],
                "tickSz": ".00001",
            },
        ]
        self.venue.cash["ETH"] = dec(0)
        self.venue.initial_cash = dict(self.venue.cash)
        request = self.venue.request

        def native_request(method, url, **kwargs):
            path = urlsplit(str(url)).path
            query = {k: v[0] for k, v in parse_qs(urlsplit(str(url)).query).items()}
            if path.endswith("/instruments"):
                rows = originals
            elif path.endswith("/price-limit"):
                rows = [
                    {
                        "instId": query["instId"],
                        "enabled": False,
                        "ts": str(int(time.time() * 1000)),
                    }
                ]
            else:
                return request(method, url, **kwargs)
            self.venue.calls.append((method, str(url), kwargs))
            response = VenueResponse({"code": "0", "data": rows})
            response.venue = self.venue
            return response

        self.venue.request = native_request

        def fill(oid, quantity, stamp):
            order = self.venue.orders[oid]
            base = order["instId"].split("-")[0]
            quote, price = order["tradeQuoteCcy"], dec(order["px"])
            order.update(
                accFillSz=str(quantity),
                state="filled" if quantity == dec(order["sz"]) else "canceled",
                uTime=stamp,
            )
            currency = base if order["side"] == "buy" else quote
            fee = -quantity * (1 if order["side"] == "buy" else price) * dec(".001")
            self.venue.fills.append(
                {
                    **order,
                    "fillSz": str(quantity),
                    "fillPx": str(price),
                    "fee": str(fee),
                    "feeCcy": currency,
                    "execType": "T",
                    "billId": str(200 + len(self.venue.fills)),
                    "tradeId": str(300 + len(self.venue.fills)),
                    "fillTime": stamp,
                    "ts": stamp,
                }
            )
            direction = 1 if order["side"] == "buy" else -1
            self.venue.cash[base] += direction * quantity
            self.venue.cash[quote] -= direction * quantity * price
            self.venue.cash[currency] += fee

        self.venue.fill = fill
        await self.client.catalog()
        self.pair = self.client.resolve("okx-demo:ETH-USDC:USDC")
        self.engine.settings["pair"] = self.pair.id
        prices = {
            "BTC-USDC": ("99.99", "100"),
            "ETH-USDC": ("9.99", "10"),
            "ETH-BTC": (".11", ".11001"),
        }

        def book(pair=None):
            pair = pair or self.pair
            feed = self.client.market_data
            arg = feed.argument(pair)
            bid, ask = prices[arg["instId"]]
            feed.message({"event": "subscribe", "arg": arg})
            feed.message(
                {
                    "arg": arg,
                    "data": [
                        {
                            "ts": str(int(time.time() * 1000)),
                            "bids": [[bid, "100", "0", "1"]],
                            "asks": [[ask, "100", "0", "1"]],
                        }
                    ],
                }
            )
            return feed.books[pair.id]

        self.book = book
        await self.configure(list(self.client.pairs.values()))
        self.engine.jev.decide.return_value = {
            "action": "buy",
            "confidence": "1",
            "model": "Offline test",
            "probabilities": {},
            "latency_ms": 0,
        }

    async def test_multi_market_rebalance_orders_keep_all_asset_ledgers_separate(self):
        await self.basket()
        self.engine.settings.update(
            strategy="rebalance",
            rebalance_targets="okx-demo:ETH-USDC:USDC=25,okx-demo:BTC-USDC:USDC=25,CASH=50",
            rebalance_band_pct="1",
            rebalance_min_trade="1",
            rebalance_cooldown_seconds=10,
        )
        proposal = await self.authorize(budget="60")
        self.assertEqual(len(proposal["markets"]), 2)
        await self.engine.tick()
        self.assertEqual(len(self.posts()), 1)
        program = self.store.get(programs.key(self.engine))
        program["next_at"] = time.time() - 1
        self.store.put(programs.key(self.engine), program)
        await self.engine.tick()
        self.assertEqual(len(self.posts()), 2)
        self.assertGreater(self.engine.balance("ETH"), 0)
        self.assertGreater(self.engine.balance("BTC"), 0)
        self.assertEqual(self.venue.cash["BTC"], 1 + self.engine.balance("BTC"))
        self.assertEqual(self.venue.cash["ETH"], self.engine.balance("ETH"))
        self.assertEqual(self.store.get("okx-reconciliation")["status"], "matched")
        await self.engine.stop()

    async def test_arbitrage_consumes_only_actual_fee_adjusted_intermediate_proceeds(self):
        await self.basket()
        self.engine.settings["strategy"] = "arbitrage"
        proposal = await self.authorize(max_orders=3)
        self.assertEqual({row["quote"] for row in proposal["markets"].values()}, {"BTC", "USDC"})
        await self.engine.tick()
        self.assertEqual(len(self.posts()), 3, self.engine.last_error)
        orders = self.engine.orders()
        acquired_eth = sum(
            dec(o["filled"]) - dec(o.get("fees", {}).get("ETH", 0))
            for o in orders
            if o["base"] == "ETH" and o["side"] == "buy"
        )
        sold_eth = sum(
            dec(o["filled"]) for o in orders if o["base"] == "ETH" and o["side"] == "sell"
        )
        self.assertLessEqual(sold_eth, acquired_eth)
        self.assertEqual(self.venue.cash["BTC"], 1 + self.engine.balance("BTC"))
        self.assertIsNone(self.store.get("cycle"))
        self.assertFalse(self.engine.armed)
        self.assertEqual(self.store.get("okx-operation")["status"], "STRATEGY_COMPLETE")

    async def test_partial_arbitrage_stops_without_next_leg_or_replay(self):
        await self.basket()
        self.engine.settings["strategy"] = "arbitrage"
        await self.authorize(max_orders=3)
        self.venue.fraction = dec(".5")
        await self.engine.tick()
        self.assertEqual(len(self.posts()), 1)
        self.assertIsNotNone(self.store.get("cycle"))
        self.assertFalse(self.engine.armed)
        await self.engine.tick()
        with self.assertRaisesRegex(SafetyError, "incomplete arbitrage"):
            await self.preview()
        self.assertEqual(len(self.posts()), 1)
        cycle, ledger, report = (
            self.store.get("cycle"),
            self.engine.ledger(),
            self.store.get("okx-operation"),
        )
        with self.assertRaisesRegex(SafetyError, "Review the interrupted"):
            await self.engine.reconcile()
        await self.engine.reconcile(acknowledge=True)
        self.assertIsNone(self.store.get("cycle"))
        archived = self.store.get("okx-cycle:" + cycle["id"])
        self.assertEqual(archived["plan"], cycle["plan"])
        self.assertEqual(archived["retained_ledger"], ledger)
        self.assertEqual(self.store.get("okx-operation"), report)
        self.assertEqual(self.engine.ledger(), ledger)
        await self.engine.tick()
        self.assertEqual(len(self.posts()), 1)
        self.assertFalse(self.engine.running or self.engine.armed)

    async def test_dca_final_price_refresh_retains_quantity_and_slot_budget(self):
        await self.authorize()
        request = self.client.request

        async def moved_book(method, path, **kwargs):
            result = await request(method, path, **kwargs)
            if path.endswith("/price-limit"):
                current = self.book()
                current.bids[0][0], current.asks[0][0] = dec("49980"), dec("49980.1")
            return result

        with patch.object(self.client, "request", moved_book):
            await self.engine.tick()
        self.assertEqual(len(self.posts()), 1, self.engine.last_error)
        order = self.engine.orders()[0]
        self.assertLess(dec(order["price"]), dec("50050"))
        self.assertLessEqual(dec(order["cost"]) * dec("1.001"), dec("10"))
        self.assertEqual(order["program_slot"], 0)
        await self.engine.stop()

    async def test_restart_cancels_original_resting_order_without_new_submission(self):
        self.engine.settings["strategy"] = "maker"
        await self.authorize()
        self.store.put("settings", self.engine.settings)
        self.venue.defer = True
        observed = asyncio.Event()
        refresh = self.engine.refresh_order

        async def watch(order):
            result = await refresh(order)
            observed.set()
            return result

        with patch.object(self.engine, "refresh_order", watch):
            placing = asyncio.create_task(
                self.engine.place(
                    self.pair, "buy", dec(".0001"), dec("49999.9"), self.book(), maker=True
                )
            )
            await asyncio.wait_for(observed.wait(), 2)
            placing.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await placing
        replacement = OKXEngine(self.store, self.client, None, lambda *_: None)
        await replacement.initialize()
        await replacement.tick()
        self.assertEqual(len(self.posts()), 2)
        self.assertEqual(replacement.orders()[0]["status"], "canceled")
        self.assertEqual(replacement.balance("BTC"), 0)
        self.assertEqual(self.store.get("okx-operation")["status"], "interrupted")
        self.assertFalse(replacement.running or replacement.armed)
        await replacement.stop()
        self.assertEqual(len(self.posts()), 2)

    async def test_invalid_basket_never_persists_settings_or_retargets_books(self):
        before = copy.deepcopy(self.engine.settings)
        saved = self.store.get("settings")
        selected = dict(self.client.market_data.pairs)
        with self.assertRaises(SafetyError):
            await self.engine.configure(
                {**before, "strategy": "rebalance", "rebalance_targets": "BTC/USD=50,CASH=50"}
            )
        self.assertEqual(self.engine.settings, before)
        self.assertEqual(self.store.get("settings"), saved)
        self.assertEqual(self.client.market_data.pairs, selected)
        self.assertEqual(self.posts(), [])

    async def test_demo_budget_and_live_capability_fail_closed(self):
        with self.assertRaisesRegex(SafetyError, "DCA schedule"):
            await self.preview(budget="10")
        self.client._environment = "live"
        with self.assertRaisesRegex(SafetyError, "Demo only"):
            await self.preview()
        with self.assertRaises(SafetyError):
            self.engine.validate_capabilities(self.engine.settings)
        self.assertEqual(self.posts(), [])
