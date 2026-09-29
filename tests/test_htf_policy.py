import unittest

from kairos.domain import dec
from kairos.htf_policy import signal, target_room
from kairos.htf_review import rolling_rows
from kairos.settings import DEFAULTS
from kairos.store import Store
from tests.helpers import BTC, book
from tests.test_htf_review import minute, pullback_minutes


class PullbackPolicyTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.end = 180000
        self.minutes = pullback_minutes(self.end)
        self.minutes.append(minute(self.end, 9995))
        self.rows, _, _ = rolling_rows(self.store, BTC, self.minutes, self.end + 60, 60)
        self.settings = {**DEFAULTS, "candle_minutes": 60}

    def test_recovery_has_cost_qualified_room_but_chasing_or_falling_quote_does_not(self):
        view = signal(self.rows, self.settings, book(), dec(40), dec(80))
        self.assertTrue(view["entry_eligible"])
        self.assertGreater(
            dec(view["target_net_room_bps"]["buy"]), dec(view["required_net_room_bps"])
        )
        for quote in (book(bid="10600", ask="10610"), book(bid="9700", ask="9710")):
            self.assertFalse(
                signal(self.rows, self.settings, quote, dec(40), dec(80))["entry_eligible"]
            )
        expensive = signal(self.rows, self.settings, book(), dec(400), dec(800))
        self.assertTrue(expensive["pullback_long"])
        self.assertFalse(expensive["entry_eligible"])

    def test_past_rally_is_not_an_entry_and_flat_history_has_no_setup(self):
        for prices in ([9000 + i * 40 for i in range(30)], [10000] * 30):
            rows = [minute(i * 3600, p) for i, p in enumerate(prices)]
            view = signal(rows, self.settings, book(), dec(40), dec(80))
            self.assertFalse(view["entry_eligible"])
            self.assertFalse(view["short_entry_eligible"])
        rising = [minute(i * 3600, 9000 + i * 40) for i in range(30)]
        self.assertTrue(
            signal(rising, self.settings, book(), dec(40), dec(80))[
                "historical_move_exceeds_round_trip_cost"
            ]
        )

    def test_mixed_fee_room_uses_distinct_notionals_and_adverse_exit_rounding(self):
        quote = book(bid="99.9", ask="100.1")
        price, room = target_room(quote, "buy", dec(104), self.settings, dec(40), dec(80))
        self.assertEqual(price, dec("99.9"))
        closing = BTC.price(dec(104) * (1 - quote.spread_bps / 20000) * dec(".999"), "buy")
        self.assertEqual(room, (closing * dec(".992") - price * dec("1.004")) / price * 10000)
        _, taker_entry_room = target_room(quote, "buy", dec(104), self.settings, dec(80), dec(80))
        self.assertEqual(room - taker_entry_room, dec(40))

    def test_range_reentry_is_observable_but_does_not_authorize_pullback(self):
        prices = [100] * 10 + [98, 102] * 9 + [94, 96]
        rows = [minute(i * 3600, p) for i, p in enumerate(prices)]
        view = signal(rows, self.settings, book(bid="95.9", ask="96.1"), dec(40), dec(80))
        self.assertFalse(view["entry_eligible"])
        observed = view["range_observation"]
        self.assertTrue(observed["observation_only"])
        self.assertTrue(observed["eligible"])
        self.assertEqual(observed["signal"], "buy")
        self.assertNotIn("profit", observed)
