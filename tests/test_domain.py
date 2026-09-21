import time
import unittest

from kairos import margin
from kairos.domain import DEFAULTS, SafetyError, dec, validate_settings
from kairos.strategies import plan_cycle, trend_state, triangle
from tests.helpers import BTC, CROSS, ETH, book


class DomainTests(unittest.TestCase):
    def test_settings_reject_nonfinite_and_invalid_limits(self):
        for field, value in [
            ("order_size", "NaN"),
            ("daily_loss", "Infinity"),
            ("max_exposure", "-1"),
            ("interval_seconds", 1),
            ("order_size", "1001"),
            ("leverage", True),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises(SafetyError):
                validate_settings({**DEFAULTS, field: value})

    def test_native_candle_intervals_and_strict_integer_validation(self):
        for minutes in (1, 5, 15, 30, 60, 240, 1440):
            with self.subTest(minutes=minutes):
                settings = validate_settings({**DEFAULTS, "candle_minutes": minutes})
                self.assertEqual(settings["candle_minutes"], minutes)
        for invalid in (True, False, 5.0, "5", 0, 10):
            with self.subTest(invalid=invalid), self.assertRaises(SafetyError):
                validate_settings({**DEFAULTS, "candle_minutes": invalid})

    def test_unknown_settings_rejected(self):
        with self.assertRaises(SafetyError):
            validate_settings({**DEFAULTS, "disable_safety": True})

    def test_margin_arbitrage_not_silently_treated_as_spot(self):
        with self.assertRaises(SafetyError):
            validate_settings({**DEFAULTS, "product": "margin", "strategy": "arbitrage"})

    def test_tick_rounding_stays_inside_limit(self):
        self.assertEqual(BTC.price(dec("100.19"), "buy"), dec("100.1"))
        self.assertEqual(BTC.price(dec("100.11"), "sell"), dec("100.2"))

    def test_order_minimum_cost_and_precision(self):
        for volume, price in [("0.000001", "10000"), ("0.00005", "1"), ("0.001", "10000.01")]:
            with self.subTest(volume=volume, price=price), self.assertRaises(SafetyError):
                BTC.validate(dec(volume), dec(price))

    def test_depth_walk_never_fills_invisible_volume(self):
        snapshot = book(qty="0.01")
        snapshot.asks.append([dec("10010"), dec("0.02")])
        self.assertEqual(
            snapshot.fill("buy", dec("0.025"), dec("10000")), (dec("0.01"), dec("100"))
        )
        self.assertEqual(
            snapshot.fill("buy", dec("0.025"), dec("10010")), (dec("0.025"), dec("250.15"))
        )

    def test_stale_book_rejected(self):
        snapshot = book()
        snapshot.received = time.time() - 60
        with self.assertRaises(SafetyError):
            snapshot.fresh(10)

    def test_triangle_has_connected_legs_in_both_directions(self):
        routes = triangle(BTC, {p.id: p for p in (BTC, ETH, CROSS)})
        for route in routes:
            self.assertEqual(route[0].source, "ZUSD")
            self.assertEqual(route[-1].target, "ZUSD")
            for left, right in zip(route, route[1:], strict=False):
                self.assertEqual(left.target, right.source)

    def test_fees_can_eliminate_apparent_arbitrage(self):
        pairs = {p.id: p for p in (BTC, ETH, CROSS)}
        route = triangle(ETH, pairs)[0]
        books = {
            ETH.id: book(ETH, "999", "1000"),
            BTC.id: book(BTC, "10000", "10001"),
            CROSS.id: book(CROSS, "0.101", "0.102"),
        }
        cheap = {**DEFAULTS, "taker_fee_bps": "0", "slippage_bps": "0"}
        expensive = {**cheap, "taker_fee_bps": "50"}
        self.assertGreater(dec(plan_cycle(route, books, dec(25), cheap)["edge_bps"]), 0)
        self.assertLess(dec(plan_cycle(route, books, dec(25), expensive)["edge_bps"]), 0)

    def test_shallow_arbitrage_rejected(self):
        route = triangle(ETH, {p.id: p for p in (BTC, ETH, CROSS)})[0]
        books = {p.id: book(p, "999", "1000", "0.000001") for p in (BTC, ETH, CROSS)}
        with self.assertRaises(SafetyError):
            plan_cycle(route, books, dec(25), DEFAULTS)

    def test_trend_requires_completed_history(self):
        with self.assertRaises(SafetyError):
            trend_state([], DEFAULTS)


class MarginTests(unittest.TestCase):
    def test_long_roundtrip_pnl_and_fees(self):
        ledger = margin.new_ledger(100)
        margin.apply_fill(ledger, BTC.id, "buy", dec("0.01"), dec(100), dec("0.4"), DEFAULTS)
        self.assertEqual(dec(ledger["cash"]), dec("99.58"))
        values = margin.metrics(ledger, {BTC.id: dec(11000)})
        self.assertEqual(values["used_margin"], dec(50))
        self.assertEqual(values["equity"], dec("109.58"))
        margin.apply_fill(ledger, BTC.id, "sell", dec("0.01"), dec(110), dec("0.44"), DEFAULTS)
        self.assertEqual(dec(ledger["cash"]), dec("109.14"))
        self.assertEqual(dec(ledger["positions"][BTC.id]["quantity"]), 0)

    def test_short_roundtrip(self):
        ledger = margin.new_ledger(100)
        margin.apply_fill(ledger, BTC.id, "sell", dec("0.01"), dec(100), dec("0.4"), DEFAULTS)
        margin.apply_fill(ledger, BTC.id, "buy", dec("0.01"), dec(90), dec("0.36"), DEFAULTS)
        self.assertEqual(dec(ledger["cash"]), dec("109.22"))

    def test_no_single_fill_position_reversal(self):
        ledger = margin.new_ledger(100)
        margin.apply_fill(ledger, BTC.id, "buy", dec("0.01"), dec(100), dec(0), DEFAULTS)
        with self.assertRaises(SafetyError):
            margin.apply_fill(ledger, BTC.id, "sell", dec("0.02"), dec(200), dec(0), DEFAULTS)

    def test_funding_accrues_once_across_restart_interval(self):
        ledger = margin.new_ledger(100)
        margin.apply_fill(ledger, BTC.id, "buy", dec("0.01"), dec(100), dec(0), DEFAULTS)
        ledger["funding_ts"] = 1000
        margin.accrue(ledger, DEFAULTS, 15400)
        margin.accrue(ledger, DEFAULTS, 15400)
        self.assertEqual(dec(ledger["funding"]), dec("0.02"))
