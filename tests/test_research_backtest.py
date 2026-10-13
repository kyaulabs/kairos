import asyncio
import copy
import random
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from kairos.backtest import SCENARIOS, HistoricalMarket, archive_rows
from kairos.backtest_report import render
from kairos.domain import ZERO, SafetyError, dec
from kairos.research_backtest import (
    ResearchEngine,
    instrument,
    net_quantity,
    pilot_settings,
    prepare_data,
    simulate,
)
from kairos.research_signals import VARIANTS, Setup, Signals, WeightedPrices
from kairos.store import Store

START = 3 * 86400
PAIR = instrument()
SCENARIO = {**SCENARIOS["base"], "name": "base"}


def candle(ts, close, *, opening=None, low=None, high=None, volume=100):
    return [
        ts,
        str(close if opening is None else opening),
        str(close + 1 if high is None else high),
        str(close - 1 if low is None else low),
        str(close),
        "0",
        str(volume),
        1,
    ]


class ScheduledSignals:
    def push(self, row, now):
        closed_at = row[0] + 300
        setup = (
            Setup("vwap-pullback", closed_at, dec(100), dec(95), dec(105), {})
            if closed_at in (START, START + 300)
            else None
        )
        return {"fixture_permitted": True}, setup


def fixture(*, price_after=110, prior_volume=100, current_volume=100):
    minutes = [
        candle(
            ts,
            100 if ts < START + 60 else price_after,
            volume=prior_volume if ts < START else current_volume,
        )
        for ts in range(START - 60, START + 600, 60)
    ]
    bars = [candle(ts, 100) for ts in range(START - 300, START + 600, 300)]
    return minutes, bars


def run(data=None, *, daily_loss="12.50"):
    minutes, bars = data or fixture()
    settings = pilot_settings(PAIR, 5, SCENARIO, 500, 25, 100, dec(daily_loss))
    return asyncio.run(
        simulate(
            minutes,
            bars,
            PAIR,
            settings,
            SCENARIO,
            "vwap-pullback",
            START,
            START + 600,
            signals=ScheduledSignals(),
        )
    )


class SignalTests(unittest.TestCase):
    def test_volume_weighted_mean_and_population_sigma_not_unweighted_close(self):
        prices = WeightedPrices()
        prices.add(candle(0, 10, low=10, high=10, volume=1))
        prices.add(candle(300, 14, low=14, high=14, volume=3))
        mean, sigma = prices.bands()
        self.assertEqual(mean, 13)
        self.assertEqual(sigma, dec(3).sqrt())
        self.assertEqual(WeightedPrices().bands(), (None, None))

    def test_daily_UTC_reset_and_future_candle_rejection(self):
        signals = Signals(5, "vwap-reversion")
        for ts in range(86400 - 60 * 300, 86400, 300):
            signals.push(candle(ts, 100), ts + 300)
        checks, setup = signals.push(candle(86400, 200), 86700)
        self.assertEqual(signals.daily.bands(), (dec(200), ZERO))
        self.assertFalse(checks["same_UTC_session"])
        self.assertFalse(checks["vwap_session_warmup"])
        self.assertIsNone(setup)
        with self.assertRaises(SafetyError):
            signals.push(candle(86700, 200), 86701)

    def test_pivot_anchor_is_unavailable_until_two_right_candles_close(self):
        signals = Signals(5, "vwap-anchored")
        rows = [candle(i * 300, 100 + i) for i in range(50)]
        rows += [candle(15000, 110, low=90), candle(15300, 120), candle(15600, 130)]
        for row in rows[:-1]:
            signals.push(row, row[0] + 300)
        self.assertIsNone(signals.anchor)
        signals.push(rows[-1], 15900)
        self.assertEqual(signals.anchor, 15000)
        self.assertEqual(signals.low[2], 15900)

    def test_all_eight_setups_generate_valid_closed_bar_plans_without_backdating(self):
        rng = random.Random(18)
        price = 100
        rows = []
        for i in range(1800):
            opening = price
            price = max(50, price + rng.gauss(0, 0.7))
            rows.append(
                candle(
                    START + i * 300,
                    price,
                    opening=opening,
                    low=min(opening, price) - rng.uniform(0.05, 0.8),
                    high=max(opening, price) + rng.uniform(0.05, 0.8),
                    volume=rng.uniform(1, 6),
                )
            )
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                signals, emitted = Signals(5, variant), []
                for row in rows:
                    _, setup = signals.push(row, row[0] + 300)
                    if setup:
                        self.assertLess(setup.stop, setup.reference)
                        self.assertGreater(setup.target, setup.reference)
                        self.assertEqual(setup.closed_at, row[0] + 300)
                        self.assertLessEqual(
                            setup.facts.get("swing_known_at", setup.closed_at), setup.closed_at
                        )
                        self.assertLessEqual(
                            setup.facts.get("anchor_known_at", setup.closed_at), setup.closed_at
                        )
                        emitted.append(setup)
                self.assertTrue(emitted, variant)

    @staticmethod
    def armed_anchor():
        signals = Signals(5, "vwap-anchored")
        rows = [candle(i * 300, 100 + i) for i in range(50)]
        rows += [
            candle(15000, 110, low=90),
            candle(15300, 120),
            candle(15600, 130),
            candle(15900, 112),
            candle(16200, 120),
        ]
        for row in rows:
            signals.push(row, row[0] + 300)
        return signals

    def test_armed_anchor_survives_new_confirmed_lows_until_retest(self):
        signals = self.armed_anchor()
        self.assertEqual(signals.pending["armed_at"], 16500)
        rows = [
            candle(16500, 121, low=120),
            candle(16800, 119, low=118),
            candle(17100, 121, low=120),
            candle(17400, 121.5, low=120.5),
        ]
        for row in rows:
            _, setup = signals.push(row, row[0] + 300)
            self.assertIsNone(setup)
        self.assertEqual(signals.low[0], 16800)  # Another low really was confirmed.
        self.assertEqual(signals.anchor, 15000)
        _, setup = signals.push(candle(17700, 122, opening=121, low=121), 18000)
        self.assertIsNotNone(setup)
        self.assertEqual(setup.facts["anchor_at"], 15000)
        self.assertEqual(setup.facts["anchor_known_at"], 15900)
        self.assertEqual(setup.facts["armed_at"], 16500)
        self.assertEqual(signals.events["confirmed"], 1)
        self.assertIsNone(signals.pending)

    def test_pending_setup_cannot_confirm_after_missing_native_bar_or_expiry(self):
        signals = self.armed_anchor()
        _, setup = signals.push(candle(16800, 122, opening=121), 17100)
        self.assertIsNone(setup)
        self.assertEqual(signals.events["gap_expired"], 1)
        self.assertIsNone(signals.pending)
        signals = self.armed_anchor()
        for ts in range(16500, 18600, 300):
            _, setup = signals.push(candle(ts, 123), ts + 300)
            self.assertIsNone(setup)
        self.assertEqual(signals.events["expired"], 1)

    def test_daily_VWAP_candidate_expires_at_UTC_reset(self):
        signals = Signals(5, "vwap-reversion")
        for ts in range(71400, 86100, 300):
            signals.push(candle(ts, 100), ts + 300)
        signals.push(candle(86100, 80, opening=90, low=70, high=91), 86400)
        self.assertIsNotNone(signals.pending)
        _, setup = signals.push(candle(86400, 82), 86700)
        self.assertIsNone(setup)
        self.assertEqual(signals.events["session_expired"], 1)
        self.assertIsNone(signals.pending)


class ExecutionTests(unittest.TestCase):
    def test_next_open_native_base_fees_bounded_exits_and_retained_dust(self):
        result = run()
        self.assertEqual(result["activity"]["signals"], 2)
        self.assertEqual(result["completed_trades"], 1)
        buy, *sells = result["orders"]
        self.assertEqual(buy["created"], START + 1)
        self.assertEqual(buy["signal_closed_at"], START)
        self.assertTrue(all(o["simulated"] and not o["maker"] for o in result["orders"]))
        fee = dec(buy["filled"]) / 1000
        self.assertEqual(dec(buy["fees"]["BTC"]), fee)
        self.assertEqual(net_quantity(buy), dec(buy["filled"]) - fee)
        self.assertEqual(
            dec(buy["fee"]), 0
        )  # No second quote debit for a base-denominated buy fee.
        sold = sum((dec(s["filled"]) for s in sells), ZERO)
        retained = net_quantity(buy) - sold
        self.assertEqual(dec(result["retained_quantity"]), retained)
        self.assertGreaterEqual(retained, 0)
        self.assertLess(retained, PAIR.minimum)
        self.assertTrue(all(dec(o["volume"]) * dec(o["price"]) <= 25 for o in result["orders"]))
        self.assertTrue(all(s["reason"] == "Target" for s in sells))
        self.assertFalse(result["halted"])
        self.assertEqual(result["activity"]["days_with_fills"], 1)

    def test_current_and_future_volume_cannot_finance_first_entry(self):
        data = fixture()
        changed = copy.deepcopy(data)
        changed[0][1][6] = "999999999"
        changed[0][-1][6] = "999999999"
        original, mutated = run(data), run(changed)
        for key in ("volume", "filled", "cost", "fees", "created", "signal_facts"):
            self.assertEqual(original["orders"][0][key], mutated["orders"][0][key])

    def test_missing_next_minute_expires_signal_without_late_entry(self):
        minutes, bars = fixture()
        result = run(([r for r in minutes if r[0] != START], bars))
        self.assertEqual(result["activity"]["missed_next_open"], 1)
        self.assertEqual(result["entry_attempts"], 0)  # Second setup gaps beyond its target.
        self.assertEqual(result["retained_quantity"], "0")

    def test_no_liquidity_never_uses_current_volume_or_invents_fees(self):
        result = run(fixture(prior_volume=0))
        self.assertEqual(result["orders"][0]["filled"], "0")
        self.assertEqual(result["orders"][0]["status"], "canceled")
        self.assertEqual(result["paid_fees"], "0")
        self.assertEqual(result["activity"]["filled_entries_per_day"], 0)

    def test_stop_and_daily_loss_latch_are_preserved(self):
        stopped = run(fixture(price_after=90))
        self.assertTrue(
            any(o["side"] == "sell" and o["reason"] == "Stop" for o in stopped["orders"])
        )
        halted = run(fixture(price_after=90), daily_loss=".01")
        self.assertTrue(halted["halted"])
        self.assertEqual(sum(o["side"] == "buy" for o in halted["orders"]), 1)
        self.assertTrue(
            any(o["side"] == "sell" and o["reason"] == "Stop" for o in halted["orders"])
        )
        self.assertLess(dec(halted["retained_quantity"]), PAIR.minimum)
        self.assertGreater(
            halted["decision_reasons"].get("Signal blocked: latched loss halt", 0), 0
        )

    def test_simulator_rejects_live_mode_and_passive_orders(self):
        market = HistoricalMarket(PAIR, SCENARIO, dec(8), dec(10), 5, ())
        store = Store(":memory:")
        store.put("settings", pilot_settings(PAIR, 5, SCENARIO, 500, 25, 100, 12.5))
        engine = ResearchEngine(store, market, "vwap-pullback")

        async def check():
            market.now, market.mid = START, dec(100)
            book = await market.book(PAIR)
            engine.mode = "trading"
            with self.assertRaises(SafetyError):
                await engine.place(PAIR, "buy", dec(".1"), dec(100), book)
            engine.mode = "dry-run"
            with self.assertRaises(SafetyError):
                await engine.place(PAIR, "buy", dec(".1"), dec(100), book, True)
            await engine.htf_review.close()

        try:
            asyncio.run(check())
        finally:
            store.close()

    def test_activity_report_keeps_branding_and_includes_zero_trade_days(self):
        result = run()
        html = render(
            {
                "title": "VWAP + Fibonacci pilot",
                "status": "complete",
                "cases": [result],
                "assumptions": [],
            },
            "pilot.json",
        )
        self.assertIn("KAIROS", html)
        self.assertIn("Activity first", html)
        self.assertIn("Longest idle", html)
        self.assertIn("Daily activity; includes no-trade days", html)
        self.assertIn('href="pilot.json"', html)

    def test_matched_report_identifies_versions_and_handles_no_closed_R(self):
        original = run(fixture(prior_volume=0, current_volume=0))
        refined = run()
        original["signal_version"], refined["signal_version"] = "v1", "v2<script>"

        def summary(case, risk):
            return {
                "signals": case["activity"]["signals"],
                "filled_entries": case["filled_entry_orders"],
                "active_days": case["activity"]["days_with_fills"],
                "net_return_pct": case["net_return_pct"],
                "mean_closed_R": risk,
            }

        html = render(
            {
                "cases": [original, refined],
                "assumptions": [],
                "paired_comparison": [
                    {
                        "policy": original["policy"],
                        "minutes": 5,
                        "v1": summary(original, None),
                        "v2": summary(refined, "-0.25"),
                    }
                ],
            },
            "fair.json",
        )
        self.assertIn("Matched v1 / v2 comparison", html)
        self.assertIn("Mean closed R v1", html)
        self.assertIn("v1 · vwap-pullback", html)
        self.assertIn("v2&lt;script&gt; · vwap-pullback", html)
        self.assertNotIn("<script>", html)
        self.assertIn("<td>—</td>", html)
        self.assertIn("-0.25", html)
        self.assertIn("sold-share", html)
        self.assertIn('href="fair.json"', html)

    def test_selected_archive_is_bounded_and_preserves_native_prices(self):
        minutes, bars = fixture()
        with tempfile.TemporaryDirectory() as directory:
            source, selected = Path(directory) / "full.zip", Path(directory) / "pilot.zip"
            with ZipFile(source, "w") as archive:
                for interval, rows in ((1, minutes), (5, bars)):
                    archive.writestr(
                        f"XBTUSDT_{interval}.csv",
                        "\n".join(",".join(map(str, [*r[:5], r[6], r[7]])) for r in rows),
                    )
            receipt = prepare_data(source, selected, START, START + 600, [5])
            self.assertTrue(selected.with_suffix(".receipt.json").exists())
            with ZipFile(selected) as archive:
                self.assertEqual(list(archive_rows(archive, "XBTUSDT_1.csv", 1)), minutes)
            self.assertEqual(receipt["members"]["XBTUSDT_1.csv"]["selected_rows"], len(minutes))
            with self.assertRaises(SafetyError):
                prepare_data(source, selected, START, START + 600, [5])
