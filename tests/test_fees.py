import unittest
from unittest.mock import patch

from kairos.domain import SafetyError, dec
from kairos.engine import Engine
from kairos.fees import AccountFees
from kairos.settings import DEFAULTS, load_settings, validate_settings
from kairos.store import Store
from tests.helpers import BTC, ETH, book, fake_jev, fake_kraken
from tests.test_futures import PAIR, fake_futures


class FeeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.spot, self.future = fake_kraken(), fake_futures()
        self.fees = AccountFees(self.spot, self.future)

    async def test_account_rates_are_market_specific_and_cached(self):
        self.spot.fees.return_value = (
            {BTC.id: dec(40), ETH.id: dec(10)},
            {BTC.id: dec(80), ETH.id: dec(20)},
        )
        await self.fees.refresh([BTC, ETH])
        await self.fees.refresh([BTC, ETH])
        self.spot.fees.assert_awaited_once_with([BTC, ETH])
        self.assertEqual(self.fees.rate(BTC), 80)
        self.assertEqual(self.fees.rate(ETH), 20)
        self.assertEqual(self.fees.rate(BTC, True), 40)
        self.assertFalse(any(r["stale"] for r in self.fees.snapshot([BTC, ETH])["markets"]))

    async def test_successful_rates_refresh_early_but_remain_cached_between_checks(self):
        with patch("kairos.fees.time.time", return_value=1000) as clock:
            await self.fees.refresh([BTC])
            self.spot.fees.return_value = ({BTC.id: dec(10)}, {BTC.id: dec(20)})
            clock.return_value = 1029
            await self.fees.refresh([BTC])
            self.assertEqual(self.spot.fees.await_count, 1)
            self.assertEqual(self.fees.rate(BTC), 40)
            clock.return_value = 1030
            await self.fees.refresh([BTC])
            self.assertEqual(self.spot.fees.await_count, 2)
            self.assertEqual(self.fees.rate(BTC), 20)
            self.assertEqual(self.fees.rates[BTC.id]["received"], 1030)

    async def test_failed_early_refresh_still_invalidates_and_paces_retries_for_a_minute(self):
        with patch("kairos.fees.time.time", return_value=1000) as clock:
            await self.fees.refresh([BTC])
            self.spot.fees.side_effect = SafetyError("Fee lookup unavailable")
            clock.return_value = 1030
            with self.assertRaisesRegex(SafetyError, "fees unavailable"):
                await self.fees.refresh([BTC])
            self.assertTrue(self.fees.snapshot([BTC])["markets"][0]["stale"])
            self.assertEqual(self.fees.rates[BTC.id]["received"], 1000)
            clock.return_value = 1089
            with self.assertRaisesRegex(SafetyError, "missing or stale"):
                await self.fees.refresh([BTC])
            self.assertEqual(self.spot.fees.await_count, 2)
            self.spot.fees.side_effect = None
            clock.return_value = 1090
            await self.fees.refresh([BTC])
            self.assertEqual(self.spot.fees.await_count, 3)
            self.assertEqual(self.fees.rate(BTC), 40)

    async def test_expired_and_clock_reversed_snapshots_cannot_be_used(self):
        with patch("kairos.fees.time.time", return_value=1000) as clock:
            await self.fees.refresh([BTC])
            clock.return_value = 1060
            with self.assertRaisesRegex(SafetyError, "stale"):
                self.fees.rate(BTC)
            self.spot.fees.return_value = ({BTC.id: dec(10)}, {BTC.id: dec(20)})
            await self.fees.refresh([BTC])
            self.assertEqual(self.fees.rate(BTC), 20)
            clock.return_value = 1059
            with self.assertRaises(SafetyError):
                self.fees.rate(BTC)

    async def test_failed_forced_refresh_invalidates_fresh_cache_without_remote_body_leak(self):
        await self.fees.refresh([BTC])
        self.spot.fees.side_effect = SafetyError("credential-bearing remote body")
        with self.assertRaisesRegex(SafetyError, "fees unavailable"):
            await self.fees.refresh([BTC], force=True)
        row = self.fees.snapshot([BTC])["markets"][0]
        self.assertTrue(row["stale"])
        self.assertEqual(row["taker_bps"], "40")
        self.assertNotIn("credential-bearing", row["error"])
        with self.assertRaises(SafetyError):
            await self.fees.refresh([BTC])
        self.assertEqual(self.spot.fees.await_count, 2)  # Failed reads are paced too.
        with self.assertRaises(SafetyError):
            self.fees.rate(BTC)

    async def test_incomplete_nonfinite_or_invalid_rates_fail_closed(self):
        for values in (
            ({}, {}),
            ({BTC.id: dec(90)}, {BTC.id: dec(80)}),
            ({BTC.id: "NaN"}, {BTC.id: dec(40)}),
            ({BTC.id: dec(-1)}, {BTC.id: dec(-1)}),
        ):
            self.spot.fees.return_value = values
            with self.subTest(values=values), self.assertRaises(SafetyError):
                await self.fees.refresh([BTC], force=True)
        self.assertEqual(self.fees.rates, {})

    async def test_single_schedule_and_maker_rebates_never_reduce_required_cash(self):
        self.spot.fees.return_value = ({}, {BTC.id: dec(20)})
        await self.fees.refresh([BTC])
        self.assertEqual(self.fees.rate(BTC, True), 20)
        self.spot.fees.return_value = ({BTC.id: dec(-2)}, {BTC.id: dec(20)})
        await self.fees.refresh([BTC], force=True)
        self.assertEqual(self.fees.rate(BTC, True), -2)
        self.assertEqual(self.fees.reserve(BTC, True), 0)

    async def test_futures_rates_use_separate_contract_lookup(self):
        self.future.fees.return_value = dec(2), dec(5)
        await self.fees.refresh([BTC, PAIR])
        self.spot.fees.assert_awaited_once_with([BTC])
        self.future.fees.assert_awaited_once_with(self.spot, PAIR)
        self.assertEqual(self.fees.rate(PAIR), 5)
        self.assertEqual(self.fees.rate(BTC), 40)
        self.future.request.assert_not_awaited()

    async def test_live_recheck_accepts_lower_rates_but_rejects_increase(self):
        await self.fees.refresh([BTC])
        self.spot.fees.return_value = ({BTC.id: dec(10)}, {BTC.id: dec(20)})
        self.assertEqual(await self.fees.recheck(BTC, False, dec(40)), 20)
        self.spot.fees.return_value = ({BTC.id: dec(10)}, {BTC.id: dec(80)})
        with self.assertRaisesRegex(SafetyError, "fee reserve"):
            await self.fees.recheck(BTC, False, dec(20))
        self.assertEqual(self.fees.rate(BTC), 80)


class FeeEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.spot, self.jev = fake_kraken(), fake_jev()
        self.engine = Engine(self.store, self.spot, self.jev, lambda *_: None)

    async def test_paper_charges_account_fees_and_htf_uses_them_in_cost_filter(self):
        self.spot.fees.return_value = ({BTC.id: dec(40)}, {BTC.id: dec(80)})
        await self.engine.initialize()
        await self.engine.start()
        order = await self.engine.place(BTC, "buy", dec(".002"), dec(10000), book())
        self.assertEqual(dec(order["fee"]), dec(".16"))
        self.assertEqual(self.engine.balance("ZUSD"), dec("979.84"))
        await self.engine.directional(BTC)
        self.assertEqual(self.engine.latest_decision["state"]["round_trip_cost_bps"], "180")
        self.spot.add.assert_not_awaited()

    async def test_rebalance_uses_traded_market_rate_not_anchor_rate(self):
        self.spot.fees.return_value = (
            {BTC.id: dec(10), ETH.id: dec(40)},
            {BTC.id: dec(20), ETH.id: dec(80)},
        )
        await self.engine.initialize()
        await self.engine.configure(
            {
                **self.engine.settings,
                "strategy": "rebalance",
                "rebalance_targets": "BTC/USD=10,ETH/USD=90,CASH=0",
            }
        )
        await self.engine.start()
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        order = self.store.orders()[0]
        self.assertEqual(order["pair"], ETH.id)
        self.assertEqual(dec(order["fee_bps"]), dec(80))
        self.assertLessEqual(dec(order["cost"]) + dec(order["fee"]), dec(100))
        self.jev.decide.assert_not_awaited()

    async def test_unavailable_account_fees_allow_setup_but_block_paper_start(self):
        self.spot.fees.side_effect = SafetyError("Missing read key")
        await self.engine.initialize()
        self.assertTrue(self.engine.ready)
        await self.engine.configure({**self.engine.settings, "daily_loss": "10"})
        with self.assertRaisesRegex(SafetyError, "fees unavailable"):
            await self.engine.start()
        self.assertFalse(self.engine.running)
        self.assertTrue(self.engine.snapshot()["fees"]["markets"][0]["stale"])
        self.jev.decide.assert_not_awaited()
        self.spot.add.assert_not_awaited()

    async def test_stale_planned_fees_cannot_be_silently_replaced_at_submission(self):
        await self.engine.initialize()
        self.engine.running = True
        self.engine.fees.rates[BTC.id]["received"] -= 61
        before = self.spot.fees.await_count
        with self.assertRaisesRegex(SafetyError, "stale"):
            await self.engine.place(BTC, "buy", dec(".002"), dec(10000), book())
        self.assertEqual(self.spot.fees.await_count, before)
        self.assertEqual(self.store.orders(), [])

    async def test_confirmed_maker_rebate_is_applied_once_not_reserved_in_advance(self):
        self.spot.fees.return_value = ({BTC.id: dec(-2)}, {BTC.id: dec(40)})
        await self.engine.initialize()
        self.engine.running = True
        order = await self.engine.place(BTC, "buy", dec(".002"), dec(10000), book(), maker=True)
        self.assertEqual(order["fee_bps"], "-2")
        for _ in range(2):
            self.engine.apply(order, dec(".002"), dec(20), dec("-.004"), "closed")
        self.assertEqual(self.engine.balance("ZUSD"), dec("980.004"))
        self.assertEqual(dec(self.engine.ledger()["fees"]["ZUSD"]), dec("-.004"))

    async def test_unexpected_taker_fee_reversal_still_requires_reconciliation(self):
        await self.engine.initialize()
        self.engine.running = True
        order = await self.engine.place(BTC, "buy", dec(".002"), dec(10000), book())
        before = self.engine.balance("ZUSD")
        with self.assertRaisesRegex(SafetyError, "backwards"):
            self.engine.apply(order, dec(".002"), dec(20), dec("-.004"), "closed")
        self.assertEqual(self.engine.balance("ZUSD"), before)

    async def test_non_scheduled_live_orders_also_recheck_fees_before_submission(self):
        await self.engine.initialize()
        self.spot.allow_live = True
        await self.engine.configure({**self.engine.settings, "live_budget": "1000"})
        await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        self.engine.running = True
        self.spot.fees.return_value = ({BTC.id: dec(60)}, {BTC.id: dec(100)})
        with self.assertRaisesRegex(SafetyError, "fee reserve"):
            await self.engine.place(BTC, "buy", dec(".002"), dec(10000), book(), maker=True)
        self.spot.add.assert_not_awaited()
        self.assertEqual(self.store.orders(), [])

    async def test_live_preparation_cannot_outlive_its_fee_snapshot(self):
        await self.engine.initialize()
        self.spot.allow_live = True
        await self.engine.configure({**self.engine.settings, "live_budget": "1000"})
        await self.engine.set_mode("trading", "ENABLE LIVE TRADING")
        self.engine.running = True
        balances = self.spot.balances.return_value

        async def expire_during_balance_read():
            self.engine.fees.rates[BTC.id]["received"] -= 61
            return balances

        self.spot.balances.side_effect = expire_during_balance_read
        with self.assertRaisesRegex(SafetyError, "stale"):
            await self.engine.place(BTC, "buy", dec(".002"), dec(10000), book())
        self.spot.add.assert_not_awaited()
        self.assertEqual(self.store.orders(), [])

    def test_migration_discards_manual_rates_without_weakening_other_risk_fields(self):
        self.assertEqual(
            load_settings({**DEFAULTS, "maker_fee_bps": "25", "taker_fee_bps": "40"}), DEFAULTS
        )
        with self.assertRaises(SafetyError):
            validate_settings({**DEFAULTS, "taker_fee_bps": "0"})
        with self.assertRaises(SafetyError):
            load_settings({k: v for k, v in DEFAULTS.items() if k != "daily_loss"})
