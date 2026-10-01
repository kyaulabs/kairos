import asyncio
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kairos import htf, programs, scalping
from kairos.domain import dec
from kairos.htf_review import rolling_rows
from kairos.settings import DEFAULTS
from kairos.store import Store
from research.method_replay import History, Market, ReplayEngine, replay
from tests.helpers import BTC

PLAN = json.loads(Path("research/METHODS_PLAN.json").read_text())


def minutes(count=1840):
    return [[i * 60, "100", "101", "99", "100", "100", "1000", 2] for i in range(count)]


class MethodReplayTests(unittest.TestCase):
    def test_rolling_aggregation_matches_production_and_never_bridges_a_gap(self):
        data = minutes(1870)
        for i, r in enumerate(data):
            r[1:6] = [
                str(100 + i % 7),
                str(110 + i % 7),
                str(90 + i % 7),
                str(101 + i % 7),
                str(100 + i % 7),
            ]
        h = History()
        for row in data:
            h.add(row)
        with_store = Store(":memory:")
        try:
            expected, _, _ = rolling_rows(with_store, BTC, data, 1870 * 60, 60)
            self.assertEqual(h.rolling(1870 * 60 + 1), expected)
            self.assertIsNone(h.rolling(1870 * 60))
            h.add([1871 * 60, *data[-1][1:]])
            self.assertIsNone(h.rolling(1872 * 60 + 1))
        finally:
            with_store.close()

    def test_strategy_dispatch_calls_real_methods_and_respects_order_caps(self):
        data = minutes(1803)
        for method, owner, name in [
            ("htf", htf, "run"),
            ("scalp", scalping, "run"),
            ("dca", programs, "run"),
            ("twap", programs, "run"),
            ("rebalance", programs, "run"),
            ("passive80", programs, "run"),
        ]:
            with (
                self.subTest(method=method),
                patch.object(owner, name, wraps=getattr(owner, name)) as call,
            ):
                result = replay(
                    data, PLAN["rules"][0], method, PLAN["scenarios"]["base"], 1800 * 60, 1803 * 60
                )
                self.assertGreater(call.call_count, 0)
                for order in result["orders"]:
                    self.assertLessEqual(
                        dec(order["volume"]) * dec(order["price"]),
                        dec(order["risk_at_submission"]["order_cap"]),
                    )
                self.assertFalse(result["broker_verified"])

    def test_future_changes_do_not_rewrite_submitted_orders_and_completion_keeps_inventory(self):
        data = minutes()
        a = replay(
            data, PLAN["rules"][0], "passive80", PLAN["scenarios"]["base"], 1800 * 60, 1840 * 60
        )
        changed = copy.deepcopy(data)
        for row in changed[1820:]:
            row[1:6] = ["50", "51", "49", "50", "50"]
        b = replay(
            changed, PLAN["rules"][0], "passive80", PLAN["scenarios"]["base"], 1800 * 60, 1840 * 60
        )
        self.assertEqual(a["orders"], b["orders"])
        self.assertEqual(a["program"]["status"], "complete")
        self.assertGreater(dec(a["retained_quantity"]), 0)
        self.assertEqual(a["retained_quantity"], b["retained_quantity"])
        self.assertLess(dec(b["net_liquidation_equity"]), dec(a["net_liquidation_equity"]))

    def test_native_volume_capacity_does_not_read_the_current_bar(self):
        data = minutes(1802)
        for row in data[:1800]:
            row[6] = "0.01"
        a = replay(
            data, PLAN["rules"][0], "passive80", PLAN["scenarios"]["base"], 1800 * 60, 1802 * 60
        )
        data[1800][6] = "1000000000"
        b = replay(
            data, PLAN["rules"][0], "passive80", PLAN["scenarios"]["base"], 1800 * 60, 1802 * 60
        )
        self.assertEqual(a["orders"][0]["filled"], b["orders"][0]["filled"])
        self.assertLess(dec(a["orders"][0]["filled"]), dec(a["orders"][0]["volume"]))

    def test_passive_touch_expiry_partial_and_no_fill_scenarios(self):
        async def check(mid, now, passive):
            market = Market(BTC, {**PLAN["scenarios"]["base"], "passive": passive}, 0)
            market.mid, market.now, market.remaining = dec(mid), now, dec(".25")
            store = Store(":memory:")
            store.put("settings", {**DEFAULTS, "pair": BTC.id, "quote": BTC.quote})
            engine = ReplayEngine(
                store,
                market,
                SimpleNamespace(key="offline"),
                lambda *_: None,
                clock=lambda: market.now,
            )
            engine.reset_ledger("dry-run", "500")
            order = {
                "id": "o",
                "mode": "dry-run",
                "product": "spot",
                "strategy": "htf",
                "pair": BTC.id,
                "base": BTC.base,
                "quote": BTC.quote,
                "side": "buy",
                "maker": True,
                "price": "100",
                "volume": "1",
                "filled": "0",
                "cost": "0",
                "fee": "0",
                "fee_bps": "25",
                "created": 1,
                "expires": 31,
                "status": "open",
            }
            store.save_order(order)
            try:
                await engine.paper_makers()
                return engine.orders()[0]
            finally:
                store.close()

        for mid, now, policy in [
            ("100", 20, "strict_path_cross"),
            ("99", 32, "strict_path_cross"),
            ("99", 20, "none"),
        ]:
            self.assertEqual(asyncio.run(check(mid, now, policy))["filled"], "0")
        filled = asyncio.run(check("99", 20, "strict_path_cross"))
        self.assertEqual(dec(filled["filled"]), dec(".25"))
        self.assertEqual(filled["status"], "open")
