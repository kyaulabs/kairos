import unittest

from kairos import okx_cycle, programs
from kairos.domain import SafetyError, dec
from tests import test_okx_engine as fixtures


class CycleTests(unittest.IsolatedAsyncioTestCase):
    book = fixtures.OKXEngineTests.book

    async def asyncSetUp(self):
        await fixtures.OKXEngineTests.asyncSetUp(self)
        self.engine.running = self.engine.armed = False
        self.engine.authorization = None

    async def proposal(self, **values):
        self.book()
        return await okx_cycle.preview(
            self.engine,
            {
                "kind": "execution_cycle",
                "pair": self.pair.id,
                "allocation": "500",
                "budget": "25",
                "buy_ceiling": "50000",
                "sell_floor": "49900",
                **values,
            },
        )

    async def execute(self, proposal):
        await okx_cycle.authorize(self.engine, proposal["id"], proposal["confirmation"])
        if self.engine.operation_task:
            await self.engine.operation_task
        return self.store.get("okx-operation")

    async def test_explicit_preview_authorization_cycle_and_disclosed_dust_then_stop(self):
        proposal = await self.proposal()
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])
        self.assertEqual(proposal["account"], "••••3456")
        self.assertEqual(proposal["spending_currency"], "USDC")
        with self.assertRaises(SafetyError):
            await okx_cycle.authorize(self.engine, proposal["id"], "ENABLE LIVE TRADING")
        result = await self.execute(proposal)
        self.assertEqual(result["status"], "PASSED_WITH_DUST")
        self.assertGreater(dec(result["residual"]), 0)
        self.assertLess(dec(result["residual"]), self.pair.lot)
        self.assertIsNotNone(result["residual_value_estimate"])
        self.assertEqual(result["reconciliation"]["status"], "matched")
        self.assertEqual(len(result["orders"]), 2)
        self.assertTrue(all(dec(o["filled"]) > 0 for o in result["orders"]))
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.armed)
        self.assertIsNone(self.engine.authorization)
        with self.assertRaises(SafetyError):
            await self.execute(proposal)
        await self.engine.tick()
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 2)

    async def test_actual_quote_fee_overrun_is_recorded_and_cannot_pass_the_budget_bound(self):
        original = self.venue.fill

        def higher_quote_fee(oid, quantity, stamp):
            original(oid, quantity, stamp)
            fill = self.venue.fills[-1]
            if fill["side"] == "buy":
                self.venue.cash[fill["feeCcy"]] -= dec(fill["fee"])
                fill["feeCcy"] = fill["tradeQuoteCcy"]
                fill["fee"] = str(-quantity * dec(fill["fillPx"]) * dec(".01"))
                self.venue.cash[fill["feeCcy"]] += dec(fill["fee"])

        self.venue.fill = higher_quote_fee
        result = await self.execute(await self.proposal())
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIn("exceeded authorized budget", result["message"])
        self.assertGreater(dec(result["entry_debit"]), dec(result["budget"]))
        self.assertEqual(result["reconciliation"]["status"], "matched")
        self.assertEqual(dec(result["residual"]), 0)
        self.assertEqual(len(result["orders"]), 2)
        self.assertFalse(self.engine.armed)

    async def test_no_fill_and_partial_execution_are_not_passed(self):
        self.venue.fraction = dec(0)
        result = await self.execute(await self.proposal())
        self.assertEqual(result["status"], "NO_FILL")
        self.assertEqual(len(result["orders"]), 1)
        self.assertEqual(self.engine.balance("BTC"), 0)
        self.venue.fraction = dec(".5")
        result = await self.execute(await self.proposal())
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(len(result["orders"]), 2)
        self.assertGreater(dec(result["residual"]), self.pair.minimum)
        self.assertFalse(self.engine.running)
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), 3)

    async def test_minimum_budget_and_expired_changed_or_stopped_preview_do_not_submit(self):
        with self.assertRaisesRegex(SafetyError, "minimum expected sellable"):
            await self.proposal(budget=".01")
        proposal = await self.proposal()
        self.engine.stop_generation += 1
        with self.assertRaisesRegex(SafetyError, "revoked"):
            await self.execute(proposal)
        self.assertFalse([c for c in self.venue.calls if c[0] == "POST"])
        with self.assertRaises(SafetyError):
            await self.proposal(kind="twap", demo_fee_allowance_bps="20")

    async def test_sell_preview_can_pass_before_price_drops_and_both_windows_skip(self):
        self.engine.settings.update(
            twap_quantity=".0004", twap_limit="50000", twap_slices=2, twap_duration_seconds=30
        )
        proposal = await self.proposal(kind="twap")
        await okx_cycle.authorize(self.engine, proposal["id"], proposal["confirmation"])
        for slot in range(2):
            if slot:
                program = self.store.get(programs.key(self.engine))
                program["started"] -= 16
                program["next_at"] -= 16
                self.store.put(programs.key(self.engine), program)
            self.book()
            await self.engine.tick()
        await self.engine.reset_program("NEW STRATEGY RUN")
        self.engine.settings.update(twap_side="sell", twap_quantity=".0003996", twap_limit="49900")
        proposal = await self.proposal(kind="twap")
        self.assertEqual(proposal["price_check"]["bid"], "49999.9")
        self.assertEqual(proposal["price_check"]["price_currency"], "USD")
        self.assertEqual(proposal["spending_currency"], "USDC")
        self.assertEqual(proposal["price_check"]["parent_limit"], "49900")
        self.assertGreater(dec(proposal["price_check"]["limit_room_bps"]), 0)
        self.assertIn("not a price reservation", proposal["price_check"]["notice"])
        ledger = self.engine.ledger()
        writes = len([c for c in self.venue.calls if c[0] == "POST"])
        await okx_cycle.authorize(self.engine, proposal["id"], proposal["confirmation"])
        for slot in range(2):
            if slot:
                program = self.store.get(programs.key(self.engine))
                program["started"] -= 16
                program["next_at"] -= 16
                self.store.put(programs.key(self.engine), program)
            self.book()
            book = self.client.market_data.books[self.pair.id]
            book.bids = [[dec("49800"), dec("2")]]
            book.asks = [[dec("49800.1"), dec("2")]]
            await self.engine.tick()
        result = self.store.get("okx-operation")
        self.assertEqual(result["status"], "TWAP_COMPLETE")  # Schedule, not fills.
        self.assertFalse(result["submitted"])
        self.assertEqual(result["orders"], [])
        self.assertEqual(result["program_execution"]["outcome"], "no_fills")
        self.assertEqual(result["program_execution"]["guard_skipped_slots"], 2)
        self.assertIn("0 orders with fills", result["message"])
        self.assertEqual(self.engine.ledger(), ledger)
        self.assertFalse(self.engine.running or self.engine.armed)
        self.assertIsNone(self.engine.authorization)
        self.assertEqual(len([c for c in self.venue.calls if c[0] == "POST"]), writes)
        for row in result["program_execution"]["skip_evidence"]:
            self.assertEqual(row["price_check"]["bid"], "49800")
            self.assertEqual(row["price_check"]["parent_limit"], "49900")
        self.assertEqual(
            proposal["price_check"]["bid"], "49999.9"
        )  # Original preview timestamp/price retained.

    async def test_existing_twap_scheduler_uses_native_execution_for_buy_and_sell(self):
        self.engine.settings.update(
            twap_quantity=".0004", twap_limit="50000", twap_slices=2, twap_duration_seconds=30
        )
        proposal = await self.proposal(kind="twap")
        await okx_cycle.authorize(self.engine, proposal["id"], proposal["confirmation"])
        program = self.store.get(programs.key(self.engine))
        await self.engine.tick()
        program = self.store.get(programs.key(self.engine))
        program["started"] -= 16
        program["next_at"] -= 16
        self.store.put(programs.key(self.engine), program)
        self.book()
        await self.engine.tick()
        self.assertEqual(self.store.get(programs.key(self.engine))["status"], "complete")
        self.assertEqual(len(self.store.orders()), 2)
        self.assertEqual(self.engine.balance("BTC"), dec(".0003996"))
        self.assertFalse(self.engine.running)
        await self.engine.reset_program("NEW STRATEGY RUN")
        self.engine.settings.update(twap_side="sell", twap_quantity=".0003996", twap_limit="49900")
        proposal = await self.proposal(kind="twap")
        await okx_cycle.authorize(self.engine, proposal["id"], proposal["confirmation"])
        program = self.store.get(programs.key(self.engine))
        await self.engine.tick()
        program = self.store.get(programs.key(self.engine))
        program["started"] -= 16
        program["next_at"] -= 16
        self.store.put(programs.key(self.engine), program)
        self.book()
        await self.engine.tick()
        self.assertEqual(self.engine.balance("BTC"), 0)
        self.assertEqual(self.store.get("okx-operation")["program_execution"]["outcome"], "filled")
        self.assertEqual(len(self.store.orders()), 4)
        self.assertFalse(self.engine.running)
        self.assertFalse(self.engine.armed)
        self.assertTrue(all(o["payload"]["tdMode"] == "cash" for o in self.store.orders()))


if __name__ == "__main__":
    unittest.main()
