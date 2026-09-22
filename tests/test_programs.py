import asyncio
import unittest
from unittest.mock import patch

from kairos import programs
from kairos.domain import SafetyError, dec
from kairos.engine import Engine
from kairos.settings import DEFAULTS
from kairos.store import Store
from tests.helpers import BTC, ETH, book, fake_jev, fake_kraken


class ProgramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock_patch = patch("kairos.programs.time.time", return_value=1790035200.0)
        self.clock = self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.kraken, self.jev = fake_kraken(), fake_jev()
        self.jev.key = ""  # None of these strategies require inference credentials.
        self.kraken.book.side_effect = lambda pair: book(pair, "99.9", "100")
        self.kraken.marks.side_effect = lambda pairs: {pair.id: dec("99.95") for pair in pairs}
        self.engine = Engine(self.store, self.kraken, self.jev, lambda *_: None)
        await self.engine.initialize()
        await self.configure("dca")

    async def configure(self, strategy, **values):
        await self.engine.configure(
            {
                **self.engine.settings,
                "strategy": strategy,
                "interval_seconds": 10,
                "reinvest_profits": False,
                "dca_amount": "100",
                "dca_count": 3,
                "dca_period_seconds": 60,
                "twap_quantity": "3",
                "twap_limit": "101",
                "twap_slices": 3,
                "twap_duration_seconds": 180,
                "rebalance_cooldown_seconds": 60,
                "rebalance_daily_turnover": "1000",
                **values,
            }
        )

    def advance(self, seconds):
        self.clock.return_value += seconds

    def program(self):
        return programs.snapshot(self.engine)

    async def restart(self):
        self.engine.running = False
        self.engine = Engine(self.store, self.kraken, self.jev, lambda *_: None)
        await self.engine.initialize()

    async def live(self, strategy="dca", **values):
        await self.configure(strategy, live_budget="1000", **values)
        self.kraken.allow_live = True
        self.kraken.fees.return_value = (
            {pair.id: dec(25) for pair in (BTC, ETH)},
            {pair.id: dec(40) for pair in (BTC, ETH)},
        )

        async def query(_):
            params = self.kraken.add.call_args.args[0]
            volume = dec(params["volume"])
            cost = volume * dec("100")
            return {
                "vol_exec": str(volume),
                "cost": str(cost),
                "fee": str(cost * dec("0.004")),
                "status": "closed",
            }

        self.kraken.query.side_effect = query
        await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        await self.engine.start()

    async def test_dca_has_no_inference_and_never_repeats_a_claimed_slot(self):
        await self.engine.start()
        identifier = self.program()["id"]
        await self.engine.tick()
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertEqual(self.engine.orders()[0]["program_id"], identifier)
        self.assertLessEqual(dec(self.program()["spent_including_fees"]), 100)
        self.jev.decide.assert_not_awaited()
        self.kraken.add.assert_not_awaited()
        self.kraken.cancel.assert_not_awaited()
        await self.restart()
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.program()["id"], identifier)
        self.assertEqual(len(self.engine.orders()), 1)
        self.advance(120)  # The intervening slot is missed, not backfilled.
        await self.engine.tick()
        self.assertEqual([order["program_slot"] for order in self.engine.orders()], [0, 2])
        self.assertEqual(self.program()["skipped_slots"], 1)
        self.assertEqual(self.program()["status"], "complete")
        self.assertFalse(self.engine.running)
        with self.assertRaisesRegex(SafetyError, "complete"):
            await self.engine.start()

    async def test_completed_program_requires_explicit_rearm_without_resetting_holdings(self):
        await self.configure("dca", dca_count=1)
        await self.engine.start()
        await self.engine.tick()
        old = self.program()["id"]
        balances = self.engine.ledger()
        with self.assertRaises(SafetyError):
            await self.engine.reset_program(None)
        await self.engine.reset_program("NEW STRATEGY RUN")
        self.assertEqual(self.engine.ledger(), balances)
        await self.engine.start()
        self.assertNotEqual(self.program()["id"], old)
        self.assertEqual(self.program()["orders"], 0)
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 2)

    async def test_plan_changes_require_rearm_but_risk_edits_do_not_reset_progress(self):
        await self.engine.start()
        await self.engine.tick()
        await self.engine.stop()
        await self.engine.configure({**self.engine.settings, "max_spread_bps": "40"})
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        await self.engine.stop()
        await self.engine.configure({**self.engine.settings, "dca_count": 4})
        with self.assertRaisesRegex(SafetyError, "settings changed"):
            await self.engine.start()
        self.assertTrue(self.program()["configuration_changed"])

    async def test_entire_parents_must_be_pre_funded_with_bot_allocations(self):
        for strategy, values in (
            ("dca", {"dca_count": 11}),
            ("twap", {"twap_quantity": "11"}),
            ("twap", {"twap_side": "sell"}),
        ):
            with self.subTest(strategy=strategy, values=values):
                await self.configure(strategy, **values)
                with self.assertRaisesRegex(SafetyError, "Pre-fund"):
                    await self.engine.start()
                self.assertIsNone(self.program())
        self.kraken.balances.assert_not_awaited()
        self.assertEqual(self.engine.balance(BTC.base), 0)

    async def test_partial_dca_fill_does_not_increase_next_purchase(self):
        self.kraken.book.side_effect = lambda pair: book(pair, "99.9", "100", ".2")
        await self.engine.start()
        await self.engine.tick()
        first = self.engine.orders()[0]
        self.assertEqual(first["status"], "canceled")
        self.assertEqual(dec(first["filled"]), dec(".2"))
        self.advance(60)
        self.kraken.book.side_effect = lambda pair: book(pair, "99.9", "100")
        await self.engine.tick()
        self.assertEqual(self.engine.orders()[1]["volume"], first["volume"])

    async def test_risk_or_spread_skip_consumes_slot_without_stopping_or_bypassing_caps(self):
        await self.configure("dca", order_size="10", max_exposure="100")
        await self.engine.start()
        await self.engine.tick()
        self.assertTrue(self.engine.running)
        self.assertEqual(self.engine.orders(), [])
        self.assertIn("order cap", self.program()["message"])
        self.advance(60)
        self.kraken.book.side_effect = lambda pair: book(pair, "90", "100")
        await self.engine.tick()
        self.assertEqual(self.program()["next_slot"], 2)
        self.assertIn("spread", self.program()["message"])
        self.assertEqual(self.engine.orders(), [])

    async def test_twap_parent_quantity_limit_and_duration_are_enforced(self):
        await self.configure("twap")
        await self.engine.start()
        await self.engine.tick()
        self.advance(120)
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        orders = self.engine.orders()
        self.assertEqual(len(orders), 2)
        self.assertTrue(
            all(dec(order["volume"]) == 1 and dec(order["price"]) <= 101 for order in orders)
        )
        self.assertEqual(sum(dec(order["filled"]) for order in orders), 2)
        self.assertEqual(self.program()["filled_by_market"], {BTC.id: "2"})
        self.jev.decide.assert_not_awaited()

    async def test_twap_unmarketable_limit_and_expiry_never_chase_or_catch_up(self):
        await self.configure("twap", twap_limit="99")
        await self.engine.start()
        await self.engine.tick()
        self.assertIn("not marketable", self.program()["message"])
        self.advance(180)
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.assertFalse(self.engine.running)
        self.assertEqual(self.program()["status"], "complete")

    async def test_twap_sell_never_uses_unallocated_exchange_inventory(self):
        ledger = self.engine.ledger()
        ledger["balances"][BTC.base] = "3"
        self.store.put("ledger:dry-run", ledger)
        await self.configure("twap", twap_side="sell", twap_limit="99")
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.engine.balance(BTC.base), 2)
        self.assertEqual(self.engine.orders()[0]["side"], "sell")
        self.assertGreaterEqual(dec(self.engine.orders()[0]["price"]), 99)
        self.kraken.balances.assert_not_awaited()

    async def test_live_dca_uses_persisted_intents_and_existing_ioc_guarded_path(self):
        await self.live()
        await self.engine.tick()
        order = self.engine.orders()[0]
        params = self.kraken.add.call_args.args[0]
        self.assertEqual(params["timeinforce"], "IOC")
        self.assertEqual(params["cl_ord_id"], order["id"])
        self.assertNotIn("leverage", params)
        self.assertEqual(order["program_id"], self.program()["id"])
        self.assertLessEqual(dec(order["cost"]) + dec(order["fee"]), 100)
        self.assertEqual(order["status"], "closed")
        self.jev.decide.assert_not_awaited()

    async def test_live_twap_and_basket_use_the_same_guarded_order_path(self):
        for strategy in ("twap", "rebalance"):
            with self.subTest(strategy=strategy):
                await self.engine.stop()
                await self.live(strategy)
                await self.engine.tick()
                order = self.engine.orders()[-1]
                self.assertEqual(order["strategy"], strategy)
                self.assertEqual(order["mode"], "trading")
                self.assertEqual(order["program_id"], self.program()["id"])
                self.assertEqual(self.kraken.add.call_args.args[0]["timeinforce"], "IOC")
        self.kraken.fees.assert_any_await([BTC, ETH])
        self.jev.decide.assert_not_awaited()

    async def test_live_fee_increase_after_start_blocks_submission(self):
        await self.live()
        self.kraken.fees.return_value = ({BTC.id: dec(25)}, {BTC.id: dec(50)})
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertIn("fee reserve", self.engine.last_error)
        self.kraken.add.assert_not_awaited()
        self.assertEqual(self.program()["next_slot"], 1)

    async def test_uncertain_live_submission_blocks_rearm_and_cannot_repeat_after_recovery(self):
        await self.live()
        self.kraken.add.side_effect = TimeoutError()
        self.kraken.find_order.side_effect = SafetyError("Order still unknown")
        await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.kraken.add.await_count, 1)
        order = self.engine.orders()[0]
        self.assertEqual(order["status"], "uncertain")
        with self.assertRaises(SafetyError):
            await self.engine.reset_program("NEW STRATEGY RUN")
        with self.assertRaises(SafetyError):
            await self.engine.start()
        await self.restart()
        self.assertEqual(self.engine.mode, "dry-run")
        self.kraken.find_order.side_effect = None
        self.kraken.find_order.return_value = (
            "RECOVERED",
            {"vol_exec": order["volume"], "cost": "99", "fee": ".396", "status": "closed"},
        )
        await self.engine.reconcile()
        self.kraken.add.side_effect = None
        await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.kraken.add.await_count, 1)
        self.assertEqual(self.program()["next_slot"], 1)
        self.assertEqual(dec(self.program()["spent_including_fees"]), dec("99.396"))

    async def test_modes_keep_separate_run_identity_and_paper_reset_keeps_live_run(self):
        await self.engine.start()
        await self.engine.tick()
        paper = self.program()["id"]
        await self.engine.stop()
        await self.live()
        live = self.program()["id"]
        self.assertNotEqual(live, paper)
        await self.engine.stop()
        await self.engine.set_mode("dry-run", None)
        self.assertEqual(self.program()["id"], paper)
        await self.engine.reset_paper()
        self.assertIsNone(self.program())
        self.assertEqual(self.store.get("program:trading:dca")["id"], live)

    async def test_stop_during_program_data_request_prevents_submission(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(pair):
            entered.set()
            await release.wait()
            return book(pair, "99.9", "100")

        self.kraken.book.side_effect = delayed
        await self.engine.start()
        tick = asyncio.create_task(self.engine.tick())
        await entered.wait()
        stop = asyncio.create_task(self.engine.stop())
        await asyncio.sleep(0)
        release.set()
        await tick
        await stop
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.orders(), [])
        self.assertEqual(self.program()["next_slot"], 1)

    async def test_slot_deadline_is_rechecked_at_the_actual_order_boundary(self):
        await self.engine.start()
        with self.assertRaisesRegex(SafetyError, "expired"):
            await self.engine.place(
                BTC,
                "buy",
                dec(".1"),
                dec("100"),
                book(BTC, "99.9", "100"),
                program={
                    "id": self.program()["id"],
                    "slot": 0,
                    "deadline": self.clock.return_value + 0.5,
                },
            )
        self.assertEqual(self.engine.orders(), [])
        self.kraken.add.assert_not_awaited()

    async def test_rebalance_converges_with_cash_weight_and_does_not_churn_inside_band(self):
        await self.configure("rebalance")
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.engine.orders()[0]["pair"], BTC.id)
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        self.advance(60)
        await self.engine.tick()
        self.assertEqual(self.engine.orders()[1]["pair"], ETH.id)
        self.advance(60)
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 2)
        self.assertIn("inside the drift band", self.program()["message"])
        self.assertEqual(set(self.program()["filled_by_market"]), {BTC.id, ETH.id})
        self.jev.decide.assert_not_awaited()

    async def test_rebalance_sells_overweights_before_spending_confirmed_proceeds(self):
        ledger = self.engine.ledger()
        ledger["balances"] = {"ZUSD": "0", BTC.base: "10"}
        self.store.put("ledger:dry-run", ledger)
        await self.configure("rebalance")
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.engine.orders()[0]["side"], "sell")
        self.assertEqual(self.engine.balance(ETH.base), 0)
        self.assertGreater(self.engine.balance("ZUSD"), 0)
        self.advance(60)
        await self.engine.tick()
        self.assertEqual(self.engine.orders()[1]["side"], "buy")
        self.assertGreater(self.engine.balance(ETH.base), 0)

    async def test_rebalance_turnover_survives_rearm_and_resets_at_utc_day(self):
        await self.configure("rebalance", rebalance_daily_turnover="100")
        await self.engine.start()
        await self.engine.tick()
        await self.engine.stop()
        await self.engine.reset_program("NEW STRATEGY RUN")
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertIn("turnover", self.program()["message"])
        self.advance(86400)
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 2)

    async def test_rebalance_does_not_value_or_sell_unlisted_holdings_as_basket_capital(self):
        ledger = self.engine.ledger()
        ledger["balances"][ETH.base] = "10"
        self.store.put("ledger:dry-run", ledger)
        await self.configure(
            "rebalance", rebalance_targets="BTC/USD=50,CASH=50", max_exposure="10000"
        )
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.engine.balance(ETH.base), 10)
        self.assertLessEqual(dec(self.engine.orders()[0]["cost"]), 500)
        self.assertTrue(all(order["pair"] == BTC.id for order in self.engine.orders()))

    async def test_invalid_baskets_products_and_slice_precision_fail_before_any_write(self):
        for target in (
            "BTC/USD=50,CASH=40",
            "BTC/USD=100",
            "BTC/USD=40,XBTUSD=40,CASH=20",
            "ETH/BTC=50,CASH=50",
            "xstocks:AAPLxUSD=50,CASH=50",
            "BTC/USD=NaN,CASH=50",
        ):
            with self.subTest(target=target), self.assertRaises(SafetyError):
                await self.configure("rebalance", rebalance_targets=target)
        for strategy, values in (
            ("twap", {"twap_quantity": ".00001"}),
            ("twap", {"twap_limit": "0"}),
            ("twap", {"twap_duration_seconds": 20}),
            ("dca", {"product": "margin"}),
            ("rebalance", {"product": "margin"}),
            ("dca", {"dca_period_seconds": True}),
        ):
            with self.subTest(strategy=strategy, values=values), self.assertRaises(SafetyError):
                await self.configure(strategy, **values)
        self.assertEqual(self.engine.orders(), [])
        self.kraken.add.assert_not_awaited()

    async def test_legacy_settings_gain_defaults_without_rearming_existing_strategies(self):
        legacy = {
            key: value
            for key, value in DEFAULTS.items()
            if not key.startswith(("dca_", "twap_", "rebalance_"))
        }
        self.store.put("settings", legacy)
        await self.restart()
        self.assertEqual(self.engine.settings["strategy"], "htf")
        self.assertEqual(self.engine.settings["dca_count"], DEFAULTS["dca_count"])
        self.assertFalse(self.engine.running)
        self.assertIsNone(programs.snapshot(self.engine))
        with self.assertRaisesRegex(SafetyError, "JEV_API_KEY"):
            await self.engine.start()
        del legacy["daily_loss"]
        self.store.put("settings", legacy)
        with self.assertRaisesRegex(SafetyError, "documented fields"):
            Engine(self.store, self.kraken, self.jev, lambda *_: None)
