import asyncio
import copy
import io
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from kairos.backtest import (
    ASSUMPTIONS,
    SCENARIOS,
    HistoricalEngine,
    HistoricalMarket,
    archive_rows,
    main,
    simulate,
    write_reports,
)
from kairos.backtest_report import render, yearly
from kairos.domain import Pair, SafetyError, dec
from kairos.settings import DEFAULTS, validate_settings
from kairos.store import Store
from tests.test_alpaca_htf_history import bars

PAIR = Pair(
    "okx-research:BTCUSDT",
    "BTC/USDT",
    "BTC",
    "USDT",
    dec(".01"),
    dec(".00000001"),
    dec(".00001"),
    dec(0),
)
START = 30 * 3600
END = START + 2 * 3600


def fixture(start=START):
    native = bars(start)
    native[-1][3] = "95.5"
    minutes = []
    for ts in range(start - 60, start + 2 * 3600, 60):
        price = 100 if ts <= start + 3600 else 110
        minutes.append([ts, str(price), str(price + 1), str(price - 1), str(price), "0", "100", 1])
    for ts in range(start, start + 2 * 3600, 3600):
        group = [r for r in minutes if ts <= r[0] < ts + 3600]
        native.append(
            [
                ts,
                group[0][1],
                str(max(dec(r[2]) for r in group)),
                str(min(dec(r[3]) for r in group)),
                group[-1][4],
                "0",
                "6000",
                60,
            ]
        )
    return minutes, native


def settings(policy):
    return validate_settings(
        {
            **DEFAULTS,
            "pair": PAIR.id,
            "quote": PAIR.quote,
            "htf_policy": policy,
            "strategy": "htf",
            "candle_minutes": 60,
            "paper_balance": "500",
            "order_size": "25",
            "max_exposure": "100",
            "daily_loss": "12.50",
            "reinvest_profits": False,
            "interval_seconds": 15,
        }
    )


def run(policy="multibar-v2", *, data=None, scenario=None, fast=True):
    minutes, native = data or fixture()
    return asyncio.run(
        simulate(
            minutes,
            native,
            PAIR,
            settings(policy),
            scenario or {**SCENARIOS["base"], "name": "base"},
            START,
            END,
            20,
            35,
            fast_forward=fast,
        )
    )


class BacktestTests(unittest.TestCase):
    def test_both_current_policies_enter_passively_then_use_owned_target_exit(self):
        for policy in ("pullback-v1", "multibar-v2"):
            with self.subTest(policy=policy):
                result = run(policy)
                self.assertFalse(result["halted"], result["halt_reason"])
                self.assertEqual(result["completed_trades"], 1)
                buy, *sells = result["orders"]
                self.assertTrue(buy["maker"])
                self.assertTrue(all(not sell["maker"] for sell in sells))
                self.assertEqual(buy["created"], START + 3601)
                self.assertTrue(all("target" in sell["reason"] for sell in sells))
                self.assertEqual(dec(buy["fee_bps"]), 20)
                self.assertTrue(all(dec(sell["fee_bps"]) == 35 for sell in sells))
                for order in result["orders"]:
                    self.assertLessEqual(dec(order["volume"]) * dec(order["price"]), 25)
                expected = (
                    sum(dec(sell["cost"]) - dec(sell["fee"]) for sell in sells)
                    - dec(buy["cost"])
                    - dec(buy["fee"])
                )
                self.assertEqual(dec(result["trades"][0]["net_pnl"]), expected)
                self.assertEqual(dec(result["ending_liquidation_equity"]), 500 + expected)

    def test_flat_fast_forward_matches_full_executor_orders_and_accounting(self):
        keys = (
            "side",
            "volume",
            "filled",
            "cost",
            "fee",
            "price",
            "created",
            "expires",
            "status",
            "reason",
        )
        for policy in ("pullback-v1", "multibar-v2"):
            with self.subTest(policy=policy):
                fast, slow = run(policy), run(policy, fast=False)
                self.assertEqual(
                    [{k: o[k] for k in keys} for o in fast["orders"]],
                    [{k: o[k] for k in keys} for o in slow["orders"]],
                )
                for key in (
                    "net_return_pct",
                    "path_drawdown_pct",
                    "paid_fees",
                    "retained_quantity",
                    "trades",
                    "distinct_candidates",
                    "gate_counts",
                ):
                    self.assertEqual(fast[key], slow[key], key)

    def test_future_native_bars_and_future_volume_cannot_rewrite_an_entry(self):
        original = fixture()
        changed = copy.deepcopy(original)
        # The second native bar is forming when the entry is submitted.
        changed[1][-1][1:5] = ["1", "2", ".5", "1"]
        changed[0][61][6] = "1000000000"
        a, b = run(data=original), run(data=changed)
        for key in ("created", "volume", "price", "filled", "cost", "fee"):
            self.assertEqual(a["orders"][0][key], b["orders"][0][key])

    def test_missing_native_bar_blocks_entries_without_filling_history(self):
        minutes, native = fixture()
        del native[10]
        result = run(data=(minutes, native))
        self.assertEqual(result["entry_attempts"], 0)
        self.assertEqual(result["completed_trades"], 0)
        self.assertFalse(result["halted"])

    def test_no_fill_scenario_consumes_attempt_and_does_not_invent_a_position(self):
        result = run(scenario={**SCENARIOS["base"], "name": "no_fill", "passive": "none"})
        self.assertEqual(result["entry_attempts"], 1)
        self.assertEqual(result["filled_entry_orders"], 0)
        self.assertEqual(dec(result["retained_quantity"]), 0)
        self.assertEqual(dec(result["paid_fees"]), 0)
        self.assertEqual(dec(result["net_return_pct"]), 0)

    def test_stop_and_latched_daily_loss_are_not_bypassed_by_research(self):
        data = fixture()
        for row in data[0]:
            if row[0] > START + 3600:
                row[1:5] = ["90", "91", "89", "90"]
        result = run(data=data)
        self.assertIn("stop", result["trades"][0]["reason"])
        self.assertLess(dec(result["net_return_pct"]), 0)
        tight = {**settings("multibar-v2"), "daily_loss": ".01"}
        result = asyncio.run(
            simulate(
                *fixture(), PAIR, tight, {**SCENARIOS["base"], "name": "base"}, START, END, 20, 35
            )
        )
        # Independently protected exit remains possible; no unlimited risk/reset is added.
        self.assertTrue(any(o["side"] == "sell" for o in result["orders"]))
        self.assertLessEqual(result["entry_attempts"], 1)

    def test_passive_touch_expiry_and_shared_liquidity(self):
        async def check(mid, now, passive):
            market = HistoricalMarket(
                PAIR, {**SCENARIOS["base"], "passive": passive}, dec(20), dec(35), 60, []
            )
            market.mid, market.now, market.remaining = dec(mid), now, dec(".1")
            store = Store(":memory:")
            store.put("settings", settings("multibar-v2"))
            engine = HistoricalEngine(
                store,
                market,
                SimpleNamespace(key="offline"),
                lambda *_: None,
                clock=lambda: market.now,
                end=END,
            )
            engine.reset_ledger("dry-run", "500")
            order = {
                "id": "o",
                "mode": "dry-run",
                "product": "spot",
                "strategy": "htf",
                "pair": PAIR.id,
                "base": PAIR.base,
                "quote": PAIR.quote,
                "side": "buy",
                "maker": True,
                "price": "100",
                "volume": "1",
                "filled": "0",
                "cost": "0",
                "fee": "0",
                "fee_bps": "20",
                "created": 1,
                "expires": 31,
                "status": "open",
            }
            store.save_order(order)
            try:
                await engine.paper_makers()
                return engine.orders()[0], market.remaining
            finally:
                store.close()

        for mid, now, passive in ((100, 20, "cross"), (99, 32, "cross"), (99, 20, "none")):
            self.assertEqual(asyncio.run(check(mid, now, passive))[0]["filled"], "0")
        filled, remaining = asyncio.run(check(99, 20, "cross"))
        self.assertEqual(dec(filled["filled"]), dec(".1"))
        self.assertEqual(remaining, 0)

    def test_archive_validation_rejects_duplicates_malformed_prices_and_nonfinite(self):
        for content in (
            "0,100,101,99,100,1,1\n0,100,101,99,100,1,1\n",
            "0,100,1,99,100,1,1\n",
            "0,NaN,101,99,100,1,1\n",
            "header\n",
        ):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "data.zip"
                with ZipFile(path, "w") as archive:
                    archive.writestr("XBTUSDT_1.csv", content)
                with ZipFile(path) as archive, self.assertRaises(SafetyError):
                    list(archive_rows(archive, "XBTUSDT_1.csv", 1))

    def test_cli_generates_both_reports_and_refuses_to_overwrite(self):
        start = 1577836800
        minutes, native = fixture(start)
        with tempfile.TemporaryDirectory() as folder:
            archive_path, output = Path(folder) / "data.zip", Path(folder) / "report.json"
            with ZipFile(archive_path, "w") as archive:
                for name, rows in (("XBTUSDT_1.csv", minutes), ("XBTUSDT_60.csv", native)):
                    archive.writestr(
                        name,
                        "".join(
                            ",".join(map(str, [r[0], *r[1:5], r[6], r[7]])) + "\n" for r in rows
                        ),
                    )
            args = [
                "kairos-backtest",
                "--archive",
                str(archive_path),
                "--start",
                "2020-01-01",
                "--end",
                "2020-01-02",
                "--minutes",
                "60",
                "--policies",
                "multibar-v2",
                "--scenarios",
                "base",
                "--output",
                str(output),
            ]
            with patch("sys.argv", args), patch("sys.stdout", io.StringIO()):
                main()
            self.assertEqual(json.loads(output.read_text())["status"], "complete")
            self.assertIn("HTF comparison", output.with_suffix(".html").read_text())
            original = output.read_bytes()
            with (
                patch("sys.argv", args),
                patch("sys.stderr", io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                main()
            self.assertEqual(error.exception.code, 1)
            self.assertEqual(output.read_bytes(), original)


class ReportTests(unittest.TestCase):
    def test_branded_report_is_offline_accessible_and_escapes_untrusted_text(self):
        case = run()
        case["decision_reasons"]["<script>alert(1)</script>"] = 1
        report = {
            "source": "<img src=x onerror=alert(1)>",
            "symbol": "BTC/USDT",
            "assumptions": ASSUMPTIONS,
            "cases": [case],
        }
        html = render(report, "results.json")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("#C1A0FF", html)
        self.assertIn("data:image/svg+xml;base64,", html)
        self.assertIn('aria-labelledby="chart-1"', html)
        self.assertIn("1970", html)

        class Resources(HTMLParser):
            def handle_starttag(self, tag, attrs):
                for key, value in attrs:
                    if key == "src":
                        self_outer.assertTrue(value.startswith("data:"))

        self_outer = self
        Resources().feed(html)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "report.json"
            write_reports(report, output, create=True)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                write_reports(report, output, create=True)

    def test_yearly_returns_carry_equity_instead_of_resetting_each_year(self):
        case = {
            "settings": {"paper_balance": "500"},
            "daily_equity": {"1609372800": "550", "1640908800": "495"},
            "trades": [],
        }
        years = yearly(case)
        self.assertEqual([y["year"] for y in years], [2020, 2021])
        self.assertEqual([dec(y["return_pct"]) for y in years], [dec(10), dec(-10)])
