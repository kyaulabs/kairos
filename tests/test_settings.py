import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from kairos.domain import CANDLE_INTERVALS, SafetyError
from kairos.settings import DEFAULTS, FIELDS, load_settings, schema, validate_settings


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = []
        self.section = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") in ("strategy-settings", "capital-settings", "execution-settings"):
            self.section = attrs["id"]
        if tag in ("input", "select") and "name" in attrs:
            self.controls.append((self.section, tag, attrs))


class SettingsTests(unittest.TestCase):
    def test_each_editable_setting_has_one_control_in_its_section(self):
        parser = FormParser()
        parser.feed(Path("kairos/static/index.html").read_text())
        names = Counter(attrs["name"] for _, _, attrs in parser.controls)
        self.assertEqual(set(names), {k for k, f in FIELDS.items() if f.get("editable", True)})
        self.assertTrue(all(count == 1 for count in names.values()))
        sections = {attrs["name"]: section for section, _, attrs in parser.controls}
        for name in (
            "paper_balance",
            "live_budget",
            "futures_live_budget",
            "leverage",
            "futures_leverage",
            "order_size",
            "max_exposure",
        ):
            self.assertEqual(sections[name], "capital-settings")
        for name in (
            "product",
            "strategy",
            "candle_minutes",
            "futures_dca_side",
            "futures_parent_notional",
            "arb_min_profit_bps",
        ):
            self.assertEqual(sections[name], "strategy-settings")
        for _, _, attrs in parser.controls:
            self.assertFalse(
                {"min", "max", "step", "maxlength"} & attrs.keys(),
                "Form constraints belong to the server contract",
            )

    def test_migration_preserves_saved_values_and_requires_legacy_risk_fields(self):
        saved = {
            **DEFAULTS,
            "daily_loss": "17.25",
            "leverage": 4,
            "futures_leverage": 3,
            "live_budget": "19",
            "futures_live_budget": "23",
        }
        self.assertEqual(load_settings(saved), saved)
        legacy = {
            k: v
            for k, v in saved.items()
            if not k.startswith(("dca_", "twap_", "rebalance_", "futures_"))
        }
        self.assertEqual(load_settings(legacy)["daily_loss"], "17.25")
        for name in ("daily_loss", "order_size", "slippage_bps", "recover_initial", "leverage"):
            with self.subTest(name=name), self.assertRaises(SafetyError):
                load_settings({k: v for k, v in legacy.items() if k != name})
        with self.assertRaises(SafetyError):
            load_settings({**saved, "allow_live": True})

    def test_product_strategy_permissions_are_not_changed_by_form_applicability(self):
        supported = {
            "spot": {"htf", "maker", "arbitrage", "dca", "twap", "rebalance"},
            "margin": {"htf", "maker"},
            "futures": {"htf", "maker", "dca", "twap"},
        }
        for product, allowed in supported.items():
            for strategy in supported["spot"]:
                values = {**DEFAULTS, "product": product, "strategy": strategy, "twap_limit": "100"}
                with self.subTest(product=product, strategy=strategy):
                    if strategy in allowed:
                        self.assertEqual(validate_settings(values), values)
                    else:
                        with self.assertRaises(SafetyError):
                            validate_settings(values)
        for product in ("margin", "futures"):
            with self.assertRaisesRegex(SafetyError, "capital recovery is unavailable"):
                validate_settings({**DEFAULTS, "product": product, "recover_initial": True})

    def test_public_contract_has_no_runtime_permissions_and_keeps_native_intervals(self):
        public = schema()
        self.assertEqual(set(public["fields"]), set(DEFAULTS))
        self.assertEqual(tuple(public["fields"]["candle_minutes"]["choices"]), CANDLE_INTERVALS)
        self.assertFalse(public["fields"]["quote"]["editable"])
        self.assertEqual(public["fields"]["order_size"]["max"], "1000000000")
        self.assertFalse(any("migrate" in field for field in public["fields"].values()))
        self.assertNotIn("ALLOW_LIVE_TRADING", str(public))

    def test_inactive_fields_still_validate_and_twap_zero_remains_unconfigured(self):
        self.assertEqual(validate_settings(DEFAULTS)["twap_limit"], "0")
        with self.assertRaisesRegex(SafetyError, "positive"):
            validate_settings({**DEFAULTS, "strategy": "twap"})
        for name, invalid in (
            ("futures_leverage", True),
            ("futures_live_budget", "NaN"),
            ("dca_count", 1.5),
            ("futures_reduce_only", 1),
        ):
            with self.subTest(name=name), self.assertRaises(SafetyError):
                validate_settings({**DEFAULTS, name: invalid})
