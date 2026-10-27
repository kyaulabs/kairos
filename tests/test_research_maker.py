import asyncio
import copy
import unittest

from kairos.backtest import SCENARIOS, HistoricalMarket
from kairos.domain import BPS, ZERO, SafetyError, dec
from kairos.research_backtest import (
    ResearchEngine,
    instrument,
    net_quantity,
    pilot_settings,
    simulate,
)
from kairos.research_diagnostics import entry_economics
from kairos.research_signals import Setup
from kairos.store import Store
from tests.test_research_backtest import START, candle

PAIR = instrument()


class OneSignal:
    def push(self, row, now):
        closed = row[0] + 300
        return {}, Setup(
            "vwap-pullback", closed, dec(100), dec(95), dec(105), {}
        ) if closed == START else None


def run(*, low=98, high=101, prior_volume=100, scenario="base", data=None, daily_loss="12.50"):
    scenario = {**SCENARIOS[scenario], "name": scenario}
    rows = [candle(START - 60, 100, volume=prior_volume), candle(START, 100, low=low, high=high)]
    rows.extend(candle(ts, 110, low=109) for ts in range(START + 60, START + 600, 60))
    bars = [candle(START - 300, 100)]
    return asyncio.run(
        simulate(
            data or rows,
            bars,
            PAIR,
            pilot_settings(PAIR, 5, scenario, 1000, 1000, 1000, dec(daily_loss)),
            scenario,
            "vwap-pullback",
            START,
            START + 600,
            signals=OneSignal(),
            economic_policy="positive-target",
            entry_execution="maker-best-bid-30s",
        )
    )


class MakerTests(unittest.TestCase):
    def test_later_strict_crossing_native_maker_buy_and_taker_exits(self):
        c = run()
        buy, *sells = c["orders"]
        self.assertTrue(buy["maker"])
        self.assertEqual(buy["payload"]["ordType"], "post_only")
        self.assertEqual(buy["created"], START + 1)
        self.assertEqual(buy["expires"], START + 31)
        self.assertEqual(buy["first_fill_at"], START + 20)
        self.assertEqual(buy["executions"][0]["liquidity"], "M")
        self.assertEqual(dec(buy["fees"]["BTC"]), dec(buy["filled"]) * dec(8) / BPS)
        self.assertEqual(net_quantity(buy), dec(buy["filled"]) * dec(".9992"))
        self.assertTrue(sells)
        self.assertTrue(all(not s["maker"] and s["payload"]["ordType"] == "ioc" for s in sells))
        self.assertTrue(all(e["liquidity"] == "T" for s in sells for e in s.get("executions", [])))
        self.assertLessEqual(dec(buy["risk_plan"]["requested_stop_risk"]), 10)
        self.assertEqual(c["trades"][0]["opened_at"], START + 20)

    def test_touch_never_fills_or_pays_fees(self):
        c = run(low=dec("99.9"))
        self.assertEqual(c["filled_entry_orders"], 0)
        self.assertEqual(c["paid_fees"], "0")
        self.assertEqual(c["orders"][0]["passive_outcome"], "passive_expired")

    def test_crossing_after_expiry_does_not_fill_or_fallback(self):
        c = run(scenario="stress", low=90)
        self.assertEqual(c["filled_entry_orders"], 0)
        self.assertEqual(c["entry_attempts"], 1)
        self.assertTrue(all(o["maker"] for o in c["orders"]))
        self.assertEqual(c["orders"][0]["status"], "canceled")

    def test_partial_entry_cancels_remainder_and_preserves_ownership(self):
        c = run(prior_volume=1)
        buy = c["orders"][0]
        self.assertEqual(dec(buy["filled"]), dec(".01"))
        self.assertEqual(buy["status"], "canceled")
        self.assertTrue(buy["passive_observation"]["partial_remainder_canceled"])
        self.assertEqual(len(buy["executions"]), 1)
        sold = sum((dec(o["filled"]) for o in c["orders"] if o["side"] == "sell"), ZERO)
        self.assertEqual(net_quantity(buy) - sold, dec(c["retained_quantity"]))

    def test_current_volume_and_later_prices_cannot_fund_first_fill(self):
        rows = [candle(START - 60, 100, volume=1), candle(START, 100, low=98)]
        rows.extend(candle(ts, 110) for ts in range(START + 60, START + 600, 60))
        changed = copy.deepcopy(rows)
        changed[1][6] = "999999999"
        a, b = run(data=rows)["orders"][0], run(data=changed)["orders"][0]
        for key in ("volume", "filled", "cost", "fees", "first_fill_at"):
            self.assertEqual(a[key], b[key])
        self.assertEqual(run(prior_volume=0)["filled_entry_orders"], 0)

    def test_crossing_through_stop_fills_before_protection_not_optimistic_cancel(self):
        c = run(low=80, daily_loss=".01")
        buy, *sells = c["orders"]
        self.assertTrue(buy["passive_observation"]["crossed_through_stop"])
        self.assertTrue(sells)
        self.assertEqual(sells[0]["reason"], "Stop")
        self.assertEqual(sells[0]["created"], START + 20)
        self.assertGreater(c["risk"]["budget_breaches"], 0)
        self.assertTrue(c["halted"])
        self.assertTrue(any(dec(s["filled"]) for s in sells))

    def test_received_entry_and_exit_fees_are_not_both_taker(self):
        c = run()
        p = c["orders"][0]
        e = p["entry_economics"]
        factor = dec(".9992") * dec(".999")
        self.assertEqual(
            dec(e["net_target_reward_per_gross_unit"]),
            factor * dec(e["target_exit_estimate"]) - dec(p["price"]),
        )
        self.assertEqual(
            dec(p["risk_plan"]["loss_per_gross_unit"]),
            dec(p["price"]) - factor * dec(e["stop_exit_estimate"]),
        )
        legacy = entry_economics(PAIR, dec(p["price"]), dec(95), dec(105), SCENARIOS["base"], 10)
        self.assertGreater(
            dec(e["net_target_reward_per_gross_unit"]),
            dec(legacy["net_target_reward_per_gross_unit"]),
        )

    def test_passive_not_allowed_by_default_and_only_best_bid_buy_is_supported(self):
        async def check():
            for mode in ("ioc", "maker-best-bid-30s"):
                market = HistoricalMarket(PAIR, SCENARIOS["base"], dec(8), dec(10), 5, ())
                market.now, market.mid = START + 1, dec(100)
                store = Store(":memory:", clock=lambda market=market: market.now)
                store.put(
                    "settings",
                    pilot_settings(PAIR, 5, SCENARIOS["base"], 1000, 1000, 1000, dec("12.50")),
                )
                engine = ResearchEngine(store, market, "vwap-pullback", mode)
                book = await market.book(PAIR)
                with self.assertRaises(SafetyError):
                    await engine.place(PAIR, "buy", dec(1), book.asks[0][0], book, maker=True)
                with self.assertRaises(SafetyError):
                    await engine.place(PAIR, "sell", dec(1), book.bids[0][0], book, maker=True)
                if mode == "ioc":
                    with self.assertRaises(SafetyError):
                        await engine.place(PAIR, "buy", dec(1), book.bids[0][0], book, maker=True)
                await engine.htf_review.close()
                store.close()

        asyncio.run(check())
