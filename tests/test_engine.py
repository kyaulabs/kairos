import asyncio
import time
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from kairos import margin
from kairos.domain import SafetyError, dec
from kairos.engine import Engine
from kairos.store import Store
from tests.helpers import BTC, CROSS, ETH, book, fake_jev, fake_kraken


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.kraken = fake_kraken()
        self.jev = fake_jev()
        self.events = []
        self.engine = Engine(
            self.store, self.kraken, self.jev, lambda kind, data: self.events.append((kind, data))
        )
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
        self.store.close()

    async def buy(self, maker=False):
        self.engine.running = True
        return await self.engine.place(BTC, "buy", dec("0.002"), dec("10000"), book(), maker)

    async def arm(self):
        self.kraken.allow_live = True
        await self.engine.configure({**self.engine.settings, "live_budget": "100"})
        await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        self.engine.running = True

    async def test_dry_run_uses_jev_but_never_exchange_writes(self):
        await self.engine.start()
        await self.engine.tick()
        self.jev.decide.assert_awaited_once()
        self.kraken.add.assert_not_awaited()
        self.kraken.cancel.assert_not_awaited()
        self.assertTrue(self.engine.orders())
        self.assertEqual(self.engine.orders()[0]["mode"], "dry-run")

    async def test_shared_daily_baselines_remain_separate_and_roll_over_in_utc(self):
        now = datetime(2026, 9, 22, 23, 59, tzinfo=UTC)
        keys = (
            "day:dry-run",
            "day:trading",
            "day:margin",
            "day:futures:dry-run",
            "day:futures:trading",
        )
        with patch("kairos.engine.datetime") as clock:
            clock.now.return_value = now
            for index, key in enumerate(keys):
                self.engine.record_valuation(dec(100 + index), dec(5), key)
                self.assertEqual(dec(self.engine.daily_pnl), 0)
            self.engine.record_valuation(dec(90), dec(7), keys[0])
            self.assertEqual(dec(self.engine.daily_pnl), -10)
            self.assertEqual(self.engine.exposure, "7")
            for index, key in enumerate(keys):
                self.assertEqual(dec(self.store.get(key)["equity"]), dec(100 + index))
            clock.now.return_value = now + timedelta(minutes=2)
            self.engine.record_valuation(dec(90), dec(7), keys[0])
            self.assertEqual(dec(self.engine.daily_pnl), 0)
            self.assertEqual(self.store.get(keys[0])["date"], "2026-09-23")
            self.assertEqual(self.store.get(keys[1])["date"], "2026-09-22")

    async def test_buy_planning_values_once_and_submission_still_rechecks(self):
        self.engine.running = True
        self.engine.valuation = AsyncMock(wraps=self.engine.valuation)
        await self.engine.directional(BTC)
        self.assertEqual(self.engine.valuation.await_count, 2)  # Planning, then guarded placement.
        self.assertEqual(self.store.orders()[-1]["status"], "closed")
        self.kraken.add.assert_not_awaited()

    async def test_htf_evaluates_each_completed_candle_once(self):
        self.jev.decide.return_value["action"] = "hold"
        for minutes in (1, 5, 15):
            with self.subTest(minutes=minutes):
                await self.engine.stop()
                await self.engine.configure({**self.engine.settings, "candle_minutes": minutes})
                self.jev.decide.reset_mock()
                rows = self.kraken.candles.return_value
                end = int(time.time()) // (minutes * 60) * (minutes * 60)
                for i, row in enumerate(rows):
                    row[0] = end - (len(rows) - i) * minutes * 60
                await self.engine.start()
                await self.engine.tick()
                await self.engine.tick()
                self.jev.decide.assert_awaited_once()
                self.kraken.candles.assert_awaited_with(BTC, minutes)
                self.assertEqual(self.jev.decide.call_args.args[0]["candle_close_time"], end)
                rows.append([end, *rows[-1][1:]])
                await self.engine.tick()
                self.assertEqual(self.jev.decide.await_count, 2)

    async def test_stop_during_inference_prevents_later_order(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_decision(state):
            entered.set()
            await release.wait()
            return self.jev.decide.return_value

        self.jev.decide.side_effect = delayed_decision
        await self.engine.start()
        tick = asyncio.create_task(self.engine.tick())
        await entered.wait()
        stop = asyncio.create_task(self.engine.stop())
        await asyncio.sleep(0)
        self.assertFalse(self.engine.running)
        release.set()
        await tick
        await stop
        self.assertEqual(self.engine.orders(), [])
        self.kraken.add.assert_not_awaited()

    async def test_high_confidence_hold_keeps_cash_without_creating_a_position(self):
        self.jev.decide.return_value.update(action="hold", confidence=0.99)
        cash = self.engine.balance(BTC.quote)
        await self.engine.start()
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertEqual(self.engine.balance(BTC.quote), cash)
        self.assertEqual(self.engine.balance(BTC.base), 0)
        self.assertEqual(self.engine.orders(), [])
        self.assertEqual(self.engine.latest_decision["action"], "hold")
        self.kraken.add.assert_not_awaited()

    async def test_htf_cost_filter_blocks_even_a_confident_buy(self):
        rows = self.kraken.candles.return_value
        for row in rows:
            row[4] = "10000"
        rows[-1][4] = "10020.3"
        await self.engine.configure({**self.engine.settings, "candle_minutes": 1})
        await self.engine.start()
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertEqual(self.engine.orders(), [])
        self.assertFalse(self.engine.latest_decision["state"]["entry_eligible"])
        self.assertIn(
            {"reason": "Deterministic trend/cost filter vetoed the Jev action"},
            [event["data"] for kind, event in self.events if kind == "skip"],
        )
        self.kraken.add.assert_not_awaited()

    async def test_low_confidence_abstains(self):
        self.jev.decide.return_value["confidence"] = 0.1
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])

    async def test_model_failure_stops_without_trade(self):
        self.jev.decide.side_effect = SafetyError("Model timeout")
        await self.engine.start()
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.orders(), [])
        self.assertIn("timeout", self.engine.last_error)

    async def test_paper_partial_fill_accounting_is_idempotent(self):
        order = await self.buy(maker=True)
        self.engine.apply(order, dec("0.001"), dec(10), dec("0.025"), "open")
        self.engine.apply(order, dec("0.001"), dec(10), dec("0.025"), "open")
        self.assertEqual(self.engine.balance("ZUSD"), dec("989.975"))
        self.assertEqual(self.engine.balance("XXBT"), dec("0.001"))
        self.engine.apply(order, dec("0.002"), dec(20), dec("0.05"), "closed")
        self.assertEqual(self.engine.balance("ZUSD"), dec("979.95"))

    async def test_spot_cannot_sell_unowned_inventory(self):
        self.engine.running = True
        with self.assertRaisesRegex(SafetyError, "cannot go short"):
            await self.engine.place(BTC, "sell", dec("0.002"), dec(9990), book())
        self.assertEqual(self.engine.orders(), [])

    async def test_order_size_enforced_in_code(self):
        self.engine.running = True
        with self.assertRaisesRegex(SafetyError, "per-order"):
            await self.engine.place(BTC, "buy", dec("0.003"), dec(10000), book())

    async def test_total_exposure_across_existing_holdings(self):
        ledger = self.engine.ledger()
        ledger["balances"]["XETH"] = "0.01"
        self.store.put("ledger:dry-run", ledger)
        with self.assertRaisesRegex(SafetyError, "exposure"):
            await self.buy()

    async def test_daily_loss_survives_restart(self):
        await self.engine.valuation()
        ledger = self.engine.ledger()
        ledger["balances"]["ZUSD"] = "970"
        self.store.put("ledger:dry-run", ledger)
        restarted = Engine(self.store, self.kraken, self.jev, lambda *_: None)
        await restarted.initialize()
        with self.assertRaisesRegex(SafetyError, "loss limit"):
            await restarted.start()
        self.assertFalse(restarted.running)

    async def test_stale_snapshot_blocks_order(self):
        self.engine.running = True
        snapshot = book()
        snapshot.received -= 60
        with self.assertRaisesRegex(SafetyError, "stale"):
            await self.engine.place(BTC, "buy", dec("0.002"), dec(10000), snapshot)
        self.assertEqual(self.engine.orders(), [])

    async def test_config_changes_require_paused_engine(self):
        await self.engine.start()
        with self.assertRaisesRegex(SafetyError, "Stop"):
            await self.engine.configure(self.engine.settings)

    async def test_configurable_limits_persist(self):
        await self.engine.configure({**self.engine.settings, "order_size": "13", "daily_loss": "7"})
        other = Engine(self.store, self.kraken, self.jev, lambda *_: None)
        self.assertEqual(other.settings["order_size"], "13")
        self.assertEqual(other.settings["daily_loss"], "7")

    async def test_live_requires_server_gate_and_confirmation(self):
        with self.assertRaises(SafetyError):
            await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        self.kraken.allow_live = True
        with self.assertRaisesRegex(SafetyError, "confirmation"):
            await self.engine.set_mode("trading", "yes")
        self.assertEqual(self.engine.mode, "dry-run")

    async def test_live_requires_fee_assumptions_cover_actual_fees(self):
        self.kraken.allow_live = True
        self.kraken.fees.return_value = ({BTC.id: dec(30)}, {BTC.id: dec(50)})
        await self.engine.configure({**self.engine.settings, "live_budget": "100"})
        with self.assertRaisesRegex(SafetyError, "underestimate"):
            await self.engine.set_mode("trading", "ENABLE LIVE TRADING")

    async def test_live_submits_limit_without_leverage_and_accounts_fill(self):
        await self.arm()
        await self.buy()
        payload = self.kraken.add.call_args.args[0]
        self.assertEqual(payload["timeinforce"], "IOC")
        self.assertEqual(payload["oflags"], "fciq")
        self.assertNotIn("leverage", payload)
        self.assertEqual(self.engine.balance("ZUSD"), dec("79.92"))
        self.assertEqual(self.engine.balance("ZUSD", "dry-run"), dec(1000))
        self.kraken.query.assert_awaited_once()

    async def test_maker_has_exchange_enforced_expiry(self):
        await self.arm()
        await self.buy(maker=True)
        payload = self.kraken.add.call_args.args[0]
        self.assertEqual(payload["timeinforce"], "GTD")
        self.assertEqual(payload["expiretm"], "+30")
        self.assertIn("post", payload["oflags"])

    async def test_uncertain_submit_is_persisted_and_never_retried(self):
        await self.arm()
        self.kraken.add.side_effect = TimeoutError()
        with self.assertRaisesRegex(SafetyError, "uncertain"):
            await self.buy()
        order = self.engine.orders(active=True)[0]
        self.assertEqual(order["status"], "uncertain")
        self.assertIsNone(order["txid"])
        self.kraken.add.assert_awaited_once()
        with self.assertRaisesRegex(SafetyError, "Reconcile"):
            await self.engine.start()

    async def test_restart_never_auto_resumes_live(self):
        await self.arm()
        await self.buy(maker=True)
        restarted = Engine(self.store, self.kraken, self.jev, lambda *_: None)
        await restarted.initialize()
        self.assertEqual(restarted.mode, "dry-run")
        self.assertFalse(restarted.running)
        self.assertTrue(restarted.orders("trading", True))
        with self.assertRaises(SafetyError):
            await restarted.start()

    async def test_cancel_fill_race_records_actual_fill(self):
        await self.arm()
        order = await self.buy(maker=True)
        self.kraken.query.side_effect = [
            {"vol_exec": "0.001", "cost": "10", "fee": "0.025", "status": "open"},
            {"vol_exec": "0.002", "cost": "20", "fee": "0.05", "status": "closed"},
        ]
        await self.engine.set_mode("dry-run", None)
        self.kraken.cancel.assert_awaited_once_with(order["txid"])
        self.assertEqual(self.engine.balance("ZUSD", "trading"), dec("79.95"))
        self.assertFalse(self.engine.orders(active=True))

    async def test_pending_cancel_blocks_mode_switch(self):
        await self.arm()
        await self.buy(maker=True)
        self.kraken.query.return_value = {
            "vol_exec": "0",
            "cost": "0",
            "fee": "0",
            "status": "open",
        }
        self.kraken.cancel.side_effect = SafetyError("Cancellation uncertain")
        with self.assertRaises(SafetyError):
            await self.engine.set_mode("dry-run", None)
        self.assertEqual(self.engine.mode, "trading")
        self.assertFalse(self.engine.running)

    async def test_paper_maker_requires_later_crossing_trade_not_touch(self):
        order = await self.buy(maker=True)
        ts = order["created"] + 1
        self.kraken.trades.return_value = (
            [
                ["9990", "1", order["created"] - 1, "s"],
                ["10000", "1", ts, "s"],
                ["9999", "0.01", ts, "s"],
            ],
            str(time.time_ns()),
        )
        await self.engine.paper_makers()
        self.assertEqual(self.engine.balance("XXBT"), dec("0.001"))
        self.kraken.add.assert_not_awaited()

    async def test_interrupted_arbitrage_blocks_restart_until_acknowledged(self):
        self.store.put("cycle", {"id": "interrupted", "mode": "dry-run", "completed_legs": 1})
        engine = Engine(self.store, self.kraken, self.jev, lambda *_: None)
        await engine.initialize()
        with self.assertRaises(SafetyError):
            await engine.start()
        with self.assertRaisesRegex(SafetyError, "acknowledge"):
            await engine.reconcile()
        await engine.reconcile(acknowledge=True)
        self.assertIsNone(self.store.get("cycle"))
        self.assertFalse(engine.running)

    async def test_margin_cannot_reach_live_exchange(self):
        await self.engine.configure({**self.engine.settings, "product": "margin"})
        self.kraken.allow_live = True
        with self.assertRaisesRegex(SafetyError, "paper-only"):
            await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        await self.buy()
        self.kraken.add.assert_not_awaited()
        self.assertEqual(self.engine.balance("ZUSD"), dec(1000))
        self.assertGreater(dec(self.store.get("margin")["positions"][BTC.id]["quantity"]), 0)

    async def test_margin_open_short_and_close_long_only_no_reversal(self):
        await self.engine.configure({**self.engine.settings, "product": "margin"})
        self.engine.running = True
        await self.engine.place(BTC, "sell", dec("0.002"), dec(9990), book())
        self.assertEqual(
            dec(self.store.get("margin")["positions"][BTC.id]["quantity"]), dec("-0.002")
        )
        await self.buy()
        self.assertEqual(dec(self.store.get("margin")["positions"][BTC.id]["quantity"]), 0)

    async def test_margin_liquidation_remains_paper_only(self):
        await self.engine.configure({**self.engine.settings, "product": "margin"})
        ledger = margin.new_ledger(1)
        margin.apply_fill(
            ledger, BTC.id, "buy", dec("0.01"), dec(100), dec(0), self.engine.settings
        )
        self.store.put("margin", ledger)
        with self.assertRaisesRegex(SafetyError, "liquidation"):
            await self.engine.valuation()
        self.assertEqual(dec(self.store.get("margin")["positions"][BTC.id]["quantity"]), 0)
        self.kraken.add.assert_not_awaited()
        self.assertFalse(self.engine.running)

    async def test_margin_requires_free_collateral(self):
        await self.engine.configure({**self.engine.settings, "product": "margin"})
        self.store.put("margin", margin.new_ledger(1))
        with self.assertRaisesRegex(SafetyError, "collateral"):
            await self.buy()

    async def prepare_arbitrage(self):
        await self.engine.configure(
            {
                **self.engine.settings,
                "pair": ETH.id,
                "strategy": "arbitrage",
                "maker_fee_bps": "0",
                "taker_fee_bps": "0",
                "slippage_bps": "0",
            }
        )

        def snapshots(pair):
            return {
                BTC.id: book(BTC, "10000", "10001"),
                ETH.id: book(ETH, "999", "1000"),
                CROSS.id: book(CROSS, "0.101", "0.10101"),
            }[pair.id]

        self.kraken.book.side_effect = snapshots
        self.kraken.marks.side_effect = lambda pairs: {p.id: snapshots(p).bids[0][0] for p in pairs}
        await self.engine.start()

    async def test_complete_arbitrage_cycle_reuses_proceeds(self):
        await self.prepare_arbitrage()
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        self.assertEqual(len(self.engine.orders()), 3)
        self.assertGreater(self.engine.balance("ZUSD"), dec(1000))
        self.assertFalse(self.engine.recovery_required)
        self.assertIsNone(self.store.get("cycle"))
        self.kraken.add.assert_not_awaited()

    async def test_partial_arbitrage_leg_stops_before_third_leg(self):
        await self.prepare_arbitrage()
        original_place = self.engine.place

        async def partial_place(pair, side, volume, price, snapshot, maker=False):
            if pair.id == CROSS.id:
                snapshot.bids[0][1] = volume / 2
            return await original_place(pair, side, volume, price, snapshot, maker)

        self.engine.place = partial_place
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertEqual(len(self.engine.orders()), 2)
        self.assertIn("partially filled", self.engine.last_error)
        self.assertTrue(self.engine.recovery_required)
        self.assertIsNotNone(self.store.get("cycle"))

    async def test_compounding_scales_both_limits_with_equity(self):
        await self.engine.configure(
            {
                **self.engine.settings,
                "order_size": "1000",
                "max_exposure": "1000",
                "reinvest_profits": True,
            }
        )
        ledger = self.engine.ledger()
        ledger["balances"]["ZUSD"] = "1500"
        self.store.put("ledger:dry-run", ledger)
        await self.engine.valuation()
        self.assertEqual(self.engine.limits(), (dec(1500), dec(1500)))
        ledger["balances"]["ZUSD"] = "800"
        self.store.put("ledger:dry-run", ledger)
        await self.engine.valuation()
        self.assertEqual(self.engine.limits(), (dec(800), dec(800)))

    async def test_recovery_waits_for_strictly_more_than_double(self):
        await self.engine.configure({**self.engine.settings, "recover_initial": True})
        ledger = self.engine.ledger()
        ledger["balances"]["ZUSD"] = "2000"
        self.store.put("ledger:dry-run", ledger)
        await self.engine.valuation()
        self.assertFalse(await self.engine.recover_capital())
        self.assertFalse(self.engine.ledger()["recovery"]["recovered"])

    async def test_recovery_once_removes_cash_without_false_loss(self):
        await self.engine.configure({**self.engine.settings, "recover_initial": True})
        ledger = self.engine.ledger()
        ledger["balances"]["ZUSD"] = "2100"
        self.store.put("ledger:dry-run", ledger)
        await self.engine.valuation()
        self.assertTrue(await self.engine.recover_capital())
        self.assertEqual(self.engine.balance("ZUSD"), dec(1100))
        self.assertEqual(dec(self.engine.ledger()["recovery"]["reserved"]), dec(1000))
        self.assertEqual(dec(self.engine.daily_pnl), 0)
        self.assertFalse(await self.engine.recover_capital())
        restarted = Engine(self.store, self.kraken, self.jev, lambda *_: None)
        await restarted.initialize()
        self.assertTrue(restarted.ledger()["recovery"]["recovered"])
        self.kraken.add.assert_not_awaited()

    async def test_recovery_reserve_is_not_spendable(self):
        await self.engine.configure(
            {
                **self.engine.settings,
                "recover_initial": True,
                "order_size": "2000",
                "max_exposure": "2000",
            }
        )
        ledger = self.engine.ledger()
        ledger["balances"]["ZUSD"] = "2100"
        self.store.put("ledger:dry-run", ledger)
        await self.engine.valuation()
        await self.engine.recover_capital()
        self.engine.running = True
        with self.assertRaisesRegex(SafetyError, "Insufficient allocated"):
            await self.engine.place(BTC, "buy", dec("0.12"), dec(10000), book())

    async def test_recovery_sells_bot_inventory_and_respects_cooldown(self):
        await self.engine.configure({**self.engine.settings, "recover_initial": True})
        ledger = self.engine.ledger()
        ledger["balances"] = {"ZUSD": "0", "XXBT": "0.25"}
        self.store.put("ledger:dry-run", ledger)
        self.engine.running = True
        await self.engine.valuation()
        self.assertTrue(await self.engine.recover_capital())
        self.assertTrue(self.engine.ledger()["recovery"]["pending"])
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertTrue(await self.engine.recover_capital())
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertGreater(self.engine.balance("ZUSD"), 0)
        self.kraken.add.assert_not_awaited()

    async def test_margin_position_blocks_product_switch(self):
        await self.engine.configure({**self.engine.settings, "product": "margin"})
        await self.buy()
        await self.engine.stop()
        with self.assertRaisesRegex(SafetyError, "Close or reset"):
            await self.engine.configure({**self.engine.settings, "product": "spot"})
