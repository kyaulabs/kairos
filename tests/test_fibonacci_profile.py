import time
import unittest
from unittest.mock import AsyncMock, patch

from kairos import meta_strategy as meta
from kairos.domain import Book, SafetyError, dec
from kairos.engine import Engine
from kairos.meta_signals import MetaSignals
from kairos.research_signals import Setup
from kairos.settings import DEFAULTS, load_settings, validate_settings
from kairos.store import Store
from tests.helpers import BTC, book, fake_jev, fake_kraken


class FibonacciProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = patch("time.time", return_value=30 * 86400 + 3)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.client, self.jev = fake_kraken(), fake_jev()
        self.jev.key = ""
        self.bid, self.ask, self.depth = "9999", "10000", "10"
        self.client.book = AsyncMock(side_effect=lambda p: book(p, self.bid, self.ask, self.depth))
        self.client.marks = AsyncMock(side_effect=lambda ps: {p.id: dec(self.bid) for p in ps})
        self.engine = Engine(self.store, self.client, self.jev, lambda *_: None)
        self.addAsyncCleanup(self.engine.htf_review.close)
        await self.engine.initialize()
        await self.engine.configure(
            {
                **self.engine.settings,
                "strategy": "fib-meta",
                "fib_stop_pct": "2",
                "fib_target_pct": "4",
            }
        )
        self.bullish = True
        self.history = patch.object(meta, "history", AsyncMock(side_effect=self.confirmation))
        self.history.start()
        self.addCleanup(self.history.stop)

    async def confirmation(self, engine, state):
        step = meta.confirmation_minutes(engine) * 60
        end = int(time.time()) // step * step
        if state.get("history_checked_at") == end:
            return None
        signals = MetaSignals(
            "fib-meta",
            60,
            confirmation_minutes=meta.confirmation_minutes(engine),
            context_minutes=240,
        ).checkpoint()
        signals["background"]["status"] = {
            "bullish": self.bullish,
            "closed_at": end // 14400 * 14400,
        }
        state.update(signals=signals, history_checked_at=end)
        return Setup(
            "fib-meta",
            end,
            dec(10000),
            dec(9900),
            dec(10300),
            {"selected_strategy": "fib-ema", "candidate_id": str(end)},
        )

    async def enter(self):
        await self.engine.start()
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])
        self.now.return_value += meta.confirmation_minutes(self.engine) * 60
        await self.engine.tick()
        self.assertTrue(self.engine.running, self.engine.last_error)
        return meta.snapshot(self.engine)["position"]

    async def test_fill_vwap_percentage_prices_replace_structural_exits_and_size_costs(self):
        position = await self.enter()
        order = self.engine.orders()[0]
        self.assertTrue(position["protection_sealed"])
        self.assertEqual(dec(position["entry_price"]), 10000)
        self.assertGreater(dec(order["price"]), dec(position["entry_price"]))
        self.assertEqual(dec(position["stop"]), 9800)
        self.assertEqual(dec(position["target"]), 10400)
        self.assertEqual(position["deadline"], time.time() + 28800)
        self.assertLessEqual(dec(position["risk_plan"]["filled_stop_risk"]), 10)
        self.assertLessEqual(dec(order["risk_plan"]["requested_stop_risk"]), 10)
        self.assertLess(
            dec(position["risk_plan"]["filled_loss_per_gross_unit"]),
            dec(order["risk_plan"]["loss_per_gross_unit"]),
        )
        # Crossing the old 9900 structural stop is not a fourth exit.
        self.bid, self.ask = "9899", "9900"
        self.bullish = False
        self.now.return_value += 900
        await self.engine.tick()
        self.assertEqual(len(self.engine.orders()), 1)
        self.assertIsNone(meta.snapshot(self.engine)["position"].get("exit_reason"))
        self.bid, self.ask = "9799", "9800"
        await self.engine.tick()
        self.assertEqual(meta.snapshot(self.engine)["position"]["exit_reason"], "Stop")
        self.jev.decide.assert_not_awaited()
        self.client.add.assert_not_awaited()

    async def test_multiple_fill_prices_use_gross_vwap_not_limit_or_first_fill(self):
        self.client.book.side_effect = lambda p: Book(
            p,
            [[dec(9999), dec(10)]],
            [[dec(10000), dec(".01")], [dec(10005), dec(10)]],
            time.time(),
        )
        position = await self.enter()
        order = self.engine.orders()[0]
        average = dec(order["cost"]) / dec(order["filled"])
        self.assertGreater(average, 10000)
        self.assertLess(average, 10005)
        self.assertEqual(dec(position["entry_price"]), average)
        self.assertEqual(dec(position["stop"]), average * dec(".98"))
        self.assertEqual(dec(position["target"]), average * dec("1.04"))
        # A claimed fill outside the saved IOC must not be sealed or adopted.
        state = meta.snapshot(self.engine)
        state["position"]["protection_sealed"] = False
        order["cost"] = str(dec(order["filled"]) * (dec(order["price"]) + 1))
        self.store.save_order(order)
        with self.assertRaisesRegex(SafetyError, "frozen cash IOC"):
            meta.seal_protection(self.engine, state)

    async def test_context_rejection_and_unset_percentages_never_buy(self):
        self.bullish = False
        self.assertIsNone(await self.enter())
        self.assertEqual(self.engine.orders(), [])
        await self.engine.stop()
        await self.engine.configure({**self.engine.settings, "fib_stop_pct": "0"})
        with self.assertRaisesRegex(SafetyError, "explicit positive"):
            await self.engine.start()
        self.assertFalse(self.engine.running)

    async def test_prices_and_deadline_survive_configuration_changes_and_restart(self):
        position = await self.enter()
        original = {k: position[k] for k in ("stop", "target", "deadline", "entry_price")}
        await self.engine.stop()
        for change in (
            {"fib_context_minutes": 720},
            {"fib_confirmation_minutes": 5},
            {"meta_structure_minutes": 240},
        ):
            with self.subTest(change=change), self.assertRaises(SafetyError):
                await self.engine.configure({**self.engine.settings, **change})
        await self.engine.configure(
            {
                **self.engine.settings,
                "fib_stop_pct": "5",
                "fib_target_pct": "10",
                "fib_max_hold_seconds": 86400,
            }
        )
        await self.engine.start()
        self.assertEqual({k: meta.snapshot(self.engine)["position"][k] for k in original}, original)
        self.now.return_value = original["deadline"] + 1
        self.depth = "0.00001"
        await self.engine.tick()
        self.assertEqual(meta.snapshot(self.engine)["position"]["exit_reason"], "Deadline")
        self.assertGreater(self.engine.balance(BTC.base), 0)

    async def test_partial_entry_and_target_exit_keep_first_reason_and_fill_basis(self):
        self.depth = "0.001"
        position = await self.enter()
        self.assertEqual(self.engine.orders()[0]["status"], "canceled")
        self.assertTrue(position["protection_sealed"])
        self.assertEqual(dec(position["entry_price"]), 10000)
        self.depth, self.bid, self.ask = "0.0005", "10401", "10402"
        self.now.return_value += 900
        await self.engine.tick()
        self.assertEqual(meta.snapshot(self.engine)["position"]["exit_reason"], "Target")
        self.bid, self.ask = "9700", "9701"
        self.now.return_value = position["deadline"] + 1
        await self.engine.tick()
        self.assertEqual(meta.snapshot(self.engine)["position"]["exit_reason"], "Target")
        self.assertEqual(len([o for o in self.engine.orders() if o["side"] == "buy"]), 1)

    async def test_zero_fill_retires_pending_position_without_retrying_same_confirmation(self):
        with patch.object(Book, "fill", return_value=(dec(0), dec(0))):
            self.assertIsNone(await self.enter())
        await self.engine.tick()
        self.assertIsNone(meta.snapshot(self.engine)["position"])
        self.assertEqual(len(self.engine.orders()), 1)

    async def test_stopped_before_durable_intent_does_not_leave_a_phantom_position(self):
        await self.engine.start()
        await self.engine.tick()
        self.now.return_value += 900
        with patch.object(
            self.engine, "place", AsyncMock(side_effect=SafetyError("fixture Stop before intent"))
        ):
            await self.engine.tick()
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.orders(), [])
        await self.engine.start()
        self.assertIsNone(meta.snapshot(self.engine)["position"])
        await self.engine.tick()
        self.assertEqual(self.engine.orders(), [])

    async def test_unknown_entry_stays_owned_until_terminal_fill_proof(self):
        await self.enter()
        state = meta.snapshot(self.engine)
        state["position"]["protection_sealed"] = False
        self.store.put(meta.key(self.engine), state)
        order = self.engine.orders()[0]
        order["status"] = "uncertain"
        self.store.save_order(order)
        meta.seal_protection(self.engine, meta.snapshot(self.engine))
        self.assertFalse(meta.snapshot(self.engine)["position"]["protection_sealed"])
        order["status"] = "canceled"
        self.store.save_order(order)
        meta.seal_protection(self.engine, meta.snapshot(self.engine))
        self.assertTrue(meta.snapshot(self.engine)["position"]["protection_sealed"])

    async def test_legacy_owned_plan_keeps_its_original_protection_without_percentage_defaults(
        self,
    ):
        await self.enter()
        state = meta.snapshot(self.engine)
        position = state["position"]
        for k in ("percentages", "protection_sealed", "entry_price"):
            position.pop(k)
        position.update(stop="9900", target="10300", deadline=time.time() + meta.MAX_HOLD)
        self.store.put(meta.key(self.engine), state)
        await self.engine.stop()
        await self.engine.configure(
            {**self.engine.settings, "fib_stop_pct": "0", "fib_target_pct": "0"}
        )
        await self.engine.start()
        saved = meta.snapshot(self.engine)["position"]
        self.assertEqual(
            (saved["stop"], saved["target"], saved["deadline"]),
            ("9900", "10300", position["deadline"]),
        )

    async def test_five_minute_confirmation_and_custom_duration_are_used(self):
        await self.engine.configure(
            {**self.engine.settings, "fib_confirmation_minutes": 5, "fib_max_hold_seconds": 3600}
        )
        position = await self.enter()
        self.assertEqual(position["deadline"], time.time() + 3600)
        self.assertEqual(meta.view(self.engine)["confirmation_minutes"], 5)

    async def test_uneconomic_target_is_rejected_without_stretching_or_more_risk(self):
        await self.engine.configure({**self.engine.settings, "fib_target_pct": "0.001"})
        self.assertIsNone(await self.enter())
        self.assertEqual(self.engine.orders(), [])
        self.assertEqual(self.engine.settings["fib_target_pct"], "0.001")
        self.assertIn("Nonpositive cost-adjusted", self.engine.latest_decision["reason"])


class FibonacciSettingsTests(unittest.TestCase):
    def test_defaults_and_old_settings_migrate_without_percentage_guesses(self):
        self.assertEqual(DEFAULTS["strategy"], "htf")
        self.assertEqual(DEFAULTS["fib_context_minutes"], 240)
        self.assertEqual(DEFAULTS["fib_confirmation_minutes"], 15)
        self.assertEqual(DEFAULTS["fib_max_hold_seconds"], 28800)
        saved = {k: v for k, v in DEFAULTS.items() if not k.startswith("fib_")}
        saved.update(strategy="fib-meta", meta_structure_minutes=720)
        restored = load_settings(saved)
        self.assertEqual(restored["fib_context_minutes"], 720)
        self.assertEqual(restored["fib_stop_pct"], "0")
        self.assertEqual(restored["fib_target_pct"], "0")

    def test_invalid_profiles_and_percentages_are_rejected(self):
        for update in (
            {"fib_context_minutes": 60, "meta_structure_minutes": 240},
            {"meta_structure_minutes": 30, "fib_confirmation_minutes": 60},
            {"fib_confirmation_minutes": 10},
            {"fib_stop_pct": "100"},
            {"fib_stop_pct": "-1"},
            {"fib_target_pct": "NaN"},
            {"fib_target_pct": "Infinity"},
            {"fib_target_pct": "-1"},
            {"fib_max_hold_seconds": 0},
        ):
            with self.subTest(update=update), self.assertRaises(SafetyError):
                validate_settings({**DEFAULTS, "strategy": "fib-meta", **update})
