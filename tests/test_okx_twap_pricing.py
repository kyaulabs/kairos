import unittest
from unittest.mock import patch

from kairos import okx_cycle, programs
from kairos.domain import BPS, dec
from kairos.strategies import limit_price
from tests import test_okx_cycle as cycles
from tests import test_okx_engine as fixtures


class TWAPPricingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.OKXEngineTests.asyncSetUp
    book = fixtures.OKXEngineTests.book
    proposal = cycles.CycleTests.proposal

    async def prepare(self, side="buy"):
        if side == "sell":
            await self.engine.place(self.pair, "buy", dec(".0005"), dec(50000), self.book())
        self.engine.running = self.engine.armed = False
        self.engine.authorization = None
        self.engine.settings.update(
            twap_side=side,
            twap_quantity=".0004",
            twap_limit="51000" if side == "buy" else "49000",
            twap_slices=2,
            twap_duration_seconds=600,
        )
        preview = await self.proposal(kind="twap")
        await okx_cycle.authorize(self.engine, preview["id"], preview["confirmation"])
        self.before_orders = len(self.store.orders())
        self.before_writes = len([c for c in self.venue.calls if c[0] == "POST"])
        self.before_ledger = self.engine.ledger()
        return preview

    def move(self, ask):
        # A new native snapshot, not a mutation of the scheduler's earlier book.
        book = self.book()
        book.asks = [[dec(ask), dec(1)]]
        book.bids = [[dec(ask) - dec(".1"), dec(1)]]
        return book

    async def tick_after_reads(self, action):
        request = self.client.request

        async def changed(method, path, **kwargs):
            rows = await request(method, path, **kwargs)
            if path.endswith("/price-limit"):
                self.assertEqual(len(self.store.orders()), self.before_orders)
                action(rows[0])
            return rows

        with patch.object(self.client, "request", changed):
            await self.engine.tick()

    def assert_blocked(self, message):
        self.assertEqual(len(self.store.orders()), self.before_orders)
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), self.before_writes)
        self.assertEqual(self.engine.ledger(), self.before_ledger)
        result = self.store.get("okx-operation")
        self.assertFalse(result["submitted"])
        self.assertEqual(result["program_execution"]["outcome"], "no_fills")
        self.assertIn(message, result["message"])
        self.assertFalse(self.engine.running or self.engine.armed)
        self.assertIsNone(self.engine.authorization)

    async def test_buy_uses_falling_book_after_reads_without_changing_quantity_or_ceiling(self):
        preview = await self.prepare()
        old = limit_price(self.book(), "buy", "10", parent=dec(preview["buy_ceiling"]))
        await self.tick_after_reads(lambda _: self.move("49990"))
        self.assertIsNone(self.engine.last_error)
        self.assertEqual(len(self.store.orders()), self.before_orders + 1)
        order = self.store.orders()[-1]
        self.assertLess(dec(order["price"]), old)
        self.assertEqual(dec(order["price"]), dec("50039.9"))
        self.assertEqual(order["volume"], "0.0002")
        self.assertLessEqual(dec(order["price"]), dec(preview["buy_ceiling"]))
        self.assertEqual(order["payload"]["px"], order["price"])
        self.assertEqual(order["venue_limits"]["price"], order["price"])
        self.assertEqual(order["status"], "closed")
        await self.engine.tick()  # Claimed slot is never replayed.
        self.assertEqual(len(self.store.orders()), 1)
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 1)

    async def test_sell_uses_rising_book_after_reads_and_preserves_owned_inventory(self):
        preview = await self.prepare("sell")
        old = limit_price(self.book(), "sell", "10", parent=dec(preview["sell_floor"]))
        await self.tick_after_reads(lambda _: self.move("50010"))
        self.assertIsNone(self.engine.last_error)
        self.assertEqual(len(self.store.orders()), self.before_orders + 1)
        order = self.store.orders()[-1]
        price, bid = dec(order["price"]), dec("50009.9")
        self.assertGreater(price, old)
        self.assertGreater((bid - old) / bid * BPS, 10)
        self.assertLessEqual((bid - price) / bid * BPS, 10)
        self.assertGreaterEqual(price, dec(preview["sell_floor"]))
        self.assertEqual(order["volume"], "0.0002")
        self.assertEqual(order["payload"]["px"], order["price"])
        self.assertEqual(order["venue_limits"]["price"], order["price"])
        self.assertEqual(self.engine.balance("BTC"), dec(".0002995"))
        self.assertEqual(self.venue.cash["BTC"], dec("1.0002995"))  # Baseline 1 BTC untouched.
        await self.engine.tick()
        self.assertEqual(len(self.store.orders()), 2)  # Owned-inventory entry plus one sell.
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 2)

    async def test_fixed_sell_floor_still_blocks_a_drop_during_reads(self):
        await self.prepare("sell")
        await self.tick_after_reads(lambda _: self.move("48900"))
        self.assert_blocked("not marketable")
        self.assertIn("limit 49000", self.store.get("okx-operation")["message"])

    async def test_fixed_buy_ceiling_still_blocks_a_rise_during_reads(self):
        await self.prepare()
        await self.tick_after_reads(lambda _: self.move("51100"))
        self.assert_blocked("not marketable")
        self.assertIn("limit 51000", self.store.get("okx-operation")["message"])

    async def test_new_buy_price_is_rechecked_against_finite_budget(self):
        await self.prepare()
        # Remaining finite budget admits the earlier child, not the updated one.
        self.engine.authorization["budget"] = "10.021"
        await self.tick_after_reads(lambda _: self.move("50020"))
        self.assert_blocked("budget exhausted")

    async def test_new_buy_price_is_rechecked_against_available_funds(self):
        await self.prepare()

        def held(_):
            self.move("50020")
            self.engine.account_snapshot["assets"]["USDC"]["available"] = "10.021"

        await self.tick_after_reads(held)
        self.assert_blocked("available after holds")

    async def test_new_buy_price_is_rechecked_against_order_cap(self):
        await self.prepare()
        with patch.object(self.engine, "limits", return_value=(dec("10.012"), dec(500))):
            await self.tick_after_reads(lambda _: self.move("50020"))
        self.assert_blocked("per-order size")

    async def test_new_price_is_rechecked_against_native_band(self):
        await self.prepare()

        def band(rows):
            self.move("50020")
            rows["buyLmt"] = "50055"

        await self.tick_after_reads(band)
        self.assert_blocked("native venue band")

    async def test_stop_during_reads_cannot_construct_or_send_a_child(self):
        await self.prepare()

        def stopped(_):
            self.move("50020")
            self.engine.stop_generation += 1

        await self.tick_after_reads(stopped)
        await self.engine.stop()  # Complete the Stop whose revocation was injected above.
        self.assert_blocked("Stop revoked")

    async def test_expired_slot_during_reads_is_not_replayed_or_extended(self):
        await self.prepare()

        def expired(_):
            program = self.store.get(programs.key(self.engine))
            self.engine.clock = lambda: program["next_at"] + 1

        await self.tick_after_reads(expired)
        self.assert_blocked("slot expired")

    async def test_later_price_move_still_fails_final_guard_without_repricing_loop(self):
        await self.prepare()
        execution_book = self.engine.execution_book

        async def moved_again(*args):
            self.move("49900")
            return await execution_book(*args)

        with patch.object(self.engine, "execution_book", moved_again):
            await self.tick_after_reads(lambda _: self.move("49990"))
        self.assert_blocked("fresh-book slippage")
        await self.engine.tick()
        self.assertEqual(len(self.store.orders()), 0)
