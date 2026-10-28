"""Native-protocol fixtures for segregated legacy dust, never hosted executions."""

import copy
import unittest
from unittest.mock import patch

from kairos import meta_strategy as meta
from kairos import programs
from kairos.domain import SafetyError, dec, floor
from kairos.okx_cycle import authorize, preview
from kairos.research_signals import Setup
from tests import test_meta_okx as meta_fixtures
from tests import test_okx_strategies as fixtures


class LegacyDustTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.DemoStrategyTests.asyncSetUp
    configure = fixtures.DemoStrategyTests.configure
    book = fixtures.DemoStrategyTests.book
    posts = fixtures.DemoStrategyTests.posts
    authorize_meta = meta_fixtures.MetaDemoTests.authorize_meta
    bullish_context = meta_fixtures.MetaDemoTests.bullish_context

    async def legacy_twap(self, *, sell=True):
        self.engine.settings.update(
            strategy="twap",
            interval_seconds=10,
            twap_quantity=".0003016",
            twap_limit="50000",
            twap_slices=2,
            twap_duration_seconds=30,
        )
        for side in ("buy", "sell") if sell else ("buy",):
            if side == "sell":
                await self.engine.reset_program("NEW STRATEGY RUN")
                self.engine.settings.update(
                    twap_side="sell",
                    twap_limit="49999.9",
                    twap_quantity=str(floor(self.engine.balance(self.pair.base), self.pair.lot)),
                )
            proposal = await preview(
                self.engine,
                {
                    "kind": "twap",
                    "pair": self.pair.id,
                    "allocation": "500",
                },
            )
            await authorize(self.engine, proposal["id"], proposal["confirmation"])
            for slot in range(2):
                if slot:
                    plan = self.store.get(programs.key(self.engine))
                    plan["started"] -= 16
                    plan["next_at"] -= 16
                    self.store.put(programs.key(self.engine), plan)
                self.book()
                await self.engine.tick()
            await self.engine.stop()
        self.engine.settings.update(strategy="fib-meta", fib_stop_pct="2", fib_target_pct="4")
        quantity = self.engine.balance(self.pair.base)
        self.assertGreater(quantity, 0)
        if sell:
            self.assertLess(quantity, self.pair.lot)
        return quantity

    async def proposal(self):
        return await preview(
            self.engine,
            {
                "kind": "strategy",
                "pair": self.pair.id,
                "allocation": "500",
                "budget": "25",
                "duration_seconds": 3600,
                "max_orders": 4,
            },
        )

    async def test_native_legacy_dust_is_disclosed_reserved_and_never_adopted_or_sold(self):
        dust = await self.legacy_twap()
        ledger, legacy_orders = self.engine.ledger(), self.engine.orders()
        writes = len(self.posts())
        proposal = await self.proposal()
        self.assertEqual(dec(proposal["legacy_dust"]["quantity"]), dust)
        self.assertEqual(proposal["legacy_dust"]["pair"], self.pair.id)
        self.assertEqual(self.engine.ledger(), ledger)
        self.assertIsNone(meta.snapshot(self.engine)["epoch"])
        self.assertNotIn("legacy_dust", meta.snapshot(self.engine))
        self.assertEqual(len(self.posts()), writes)
        await authorize(self.engine, proposal["id"], proposal["confirmation"])
        state = meta.snapshot(self.engine)
        self.assertEqual(state["legacy_dust"], proposal["legacy_dust"])
        self.assertEqual(meta.owned(self.engine, state), 0)
        self.assertEqual(self.engine.balance(self.pair.base), dust)
        self.assertEqual(self.engine.ledger(), ledger)
        self.assertLess(self.engine.balance(self.pair.quote), dec(ledger["initial"]))
        setup = Setup("fib-meta", self.engine.clock(), dec(50000), dec(49000), dec(54000), {})
        quantity, price, risk = meta.risk_plan(self.engine, self.pair, self.book(), setup)
        self.assertEqual(dec(risk["dust_risk_reserved"]), dust * self.book().bids[0][0])
        self.assertLessEqual(
            quantity * dec(risk["loss_per_gross_unit"])
            + dec(risk["dust_risk_reserved"])
            + dec(risk["potential_unprotected_dust_cost"]),
            dec(risk["capital_budget"]),
        )
        self.bullish_context(state)
        state["position"] = {
            "id": "new-meta-only",
            "stop": risk["stop"],
            "target": risk["target"],
            "deadline": self.engine.clock() + 28800,
            "signal": {},
            "risk_plan": risk,
            "percentages": {"stop": "2", "target": "4"},
            "protection_sealed": False,
        }
        self.store.put(meta.key(self.engine), state)
        await self.engine.place(self.pair, "buy", quantity, price, self.book())
        meta.prepare(self.engine)
        state = meta.snapshot(self.engine)
        acquired = meta.owned(self.engine, state)
        self.assertEqual(self.engine.balance(self.pair.base), dust + acquired)
        self.client.market_data.message(
            {
                "arg": self.client.market_data.argument(self.pair),
                "data": [
                    {
                        "ts": str(int(self.engine.clock() * 1000)),
                        "bids": [["60000", "1"]],
                        "asks": [["60000.1", "1"]],
                    }
                ],
            }
        )
        await meta.run(self.engine)
        orders = self.engine.orders()
        sell_order = orders[-1]
        self.assertEqual(sell_order["side"], "sell")
        self.assertGreater(dec(sell_order["volume"]), 0)
        self.assertLessEqual(dec(sell_order["volume"]), floor(acquired, self.pair.lot))
        state = meta.snapshot(self.engine)
        self.assertEqual(state["legacy_dust"], proposal["legacy_dust"])
        self.assertGreaterEqual(meta.owned(self.engine, state), 0)
        self.assertEqual(self.engine.balance(self.pair.base), dust + meta.owned(self.engine, state))
        self.assertEqual(
            [o["executions"] for o in orders[: len(legacy_orders)]],
            [o["executions"] for o in legacy_orders],
        )
        await self.engine.stop()
        meta.prepare(self.engine)
        self.assertEqual(meta.snapshot(self.engine)["legacy_dust"], proposal["legacy_dust"])

    async def test_executable_legacy_inventory_is_rejected_by_preview_not_authorization(self):
        await self.legacy_twap(sell=False)
        ledger, writes = self.engine.ledger(), len(self.posts())
        with self.assertRaisesRegex(SafetyError, "cannot adopt"):
            await self.proposal()
        self.assertEqual(self.engine.ledger(), ledger)
        self.assertEqual(len(self.posts()), writes)
        self.assertFalse(self.engine.running or self.engine.armed)
        self.assertIsNone(meta.snapshot(self.engine)["epoch"])

    async def test_unknown_incomplete_or_changed_dust_evidence_stays_blocked(self):
        await self.legacy_twap()
        orders, ledger = self.engine.orders(), self.engine.ledger()
        self.assertIsNotNone(meta.ownership(self.engine))
        for changes in (
            {"strategy": "htf"},
            {"status": "open"},
            {"fee_reported": False},
            {"txid": None},
            {"executions": []},
            {"payload": {"tdMode": "cross"}},
        ):
            with self.subTest(changes=changes):
                damaged = copy.deepcopy(orders)
                damaged[0].update(changes)
                with patch.object(self.engine, "orders", return_value=damaged):
                    with self.assertRaises(SafetyError):
                        meta.ownership(self.engine)
        for balances in (
            {**ledger["balances"], self.pair.base: str(self.pair.lot)},
            {**ledger["balances"], self.pair.base: "0.000000001"},
            {**ledger["balances"], "ETH": "0.1"},
        ):
            with (
                self.subTest(balances=balances),
                patch.object(self.engine, "ledger", return_value={**ledger, "balances": balances}),
            ):
                with self.assertRaises(SafetyError):
                    meta.ownership(self.engine)
        await self.authorize_meta("fib-meta")
        revised = copy.deepcopy(orders)
        revised[0]["executions"][0]["record_time"] += 1
        with patch.object(self.engine, "orders", return_value=revised):
            with self.assertRaises(SafetyError):
                meta.ownership(self.engine)
        state = meta.snapshot(self.engine)
        state["legacy_dust"]["quantity"] = "0"
        self.store.put(meta.key(self.engine), state)
        with self.assertRaises(SafetyError):
            meta.prepare(self.engine)
        self.assertEqual(self.engine.ledger(), ledger)

    async def test_undisclosed_dust_is_rejected_before_binding_or_arming(self):
        await self.legacy_twap()
        proposal = await self.proposal()
        proposal.pop("legacy_dust", None)
        self.store.put("okx-preview:" + proposal["id"], proposal)
        ledger, writes = self.engine.ledger(), len(self.posts())
        with patch.object(self.engine, "bind", wraps=self.engine.bind) as bind:
            with self.assertRaisesRegex(SafetyError, "dust.*preview"):
                await authorize(self.engine, proposal["id"], proposal["confirmation"])
            bind.assert_not_called()
        self.assertFalse(self.engine.running or self.engine.armed)
        self.assertEqual(self.engine.ledger(), ledger)
        self.assertEqual(len(self.posts()), writes)
