"""Canonical bot settings. Public metadata drives the form, never execution permissions."""

from kairos.domain import CANDLE_INTERVALS, SafetyError, dec

STRATEGIES = {
    "htf": {
        "label": "Higher-timeframe trend",
        "products": ("spot", "margin", "futures"),
        "scheduled": False,
    },
    "maker": {
        "label": "Rate-limited market making",
        "products": ("spot", "margin", "futures"),
        "scheduled": False,
    },
    "arbitrage": {"label": "Triangular arbitrage", "products": ("spot",), "scheduled": False},
    "dca": {
        "label": "DCA · scheduled accumulation",
        "products": ("spot", "futures"),
        "scheduled": True,
    },
    "twap": {
        "label": "TWAP · bounded order slicing",
        "products": ("spot", "futures"),
        "scheduled": True,
    },
    "rebalance": {"label": "Threshold rebalancing", "products": ("spot",), "scheduled": True},
}
SCHEDULED_STRATEGIES = frozenset(key for key, row in STRATEGIES.items() if row["scheduled"])


def setting(default, bounds=None, *, choices=None, **metadata):
    kind = (
        "boolean"
        if type(default) is bool
        else "integer"
        if type(default) is int
        else "decimal"
        if bounds
        else "string"
    )
    result = {"default": default, "type": kind, **metadata}
    if bounds:
        result.update(min=bounds[0], max=bounds[1])
    if choices:
        result["choices"] = choices
    return result


FIELDS = {
    "pair": setting("XBTUSD"),
    "strategy": setting("htf", choices={key: row["label"] for key, row in STRATEGIES.items()}),
    "quote": setting("ZUSD", editable=False),
    "paper_balance": setting("1000", ("1", "1000000000")),
    "live_budget": setting("0", ("0", "1000000000"), products=("spot",)),
    "order_size": setting("1000", ("0.01", "1000000000")),
    "max_exposure": setting("1000", ("0.01", "1000000000")),
    "daily_loss": setting("1000", ("0.01", "1000000000")),
    "slippage_bps": setting("10", ("0", "100")),
    "max_spread_bps": setting("30", ("0.01", "1000")),
    "min_confidence": setting("0.7", ("0", "1"), strategies=("htf", "maker", "arbitrage")),
    "arb_min_profit_bps": setting("10", ("0", "1000"), strategies=("arbitrage",)),
    "interval_seconds": setting(15, (10, 3600)),
    "candle_minutes": setting(
        15,
        choices={
            n: f"{n // 1440}d" if n >= 1440 else f"{n // 60}h" if n >= 60 else f"{n}m"
            for n in CANDLE_INTERVALS
        },
        strategies=("htf",),
    ),
    "stale_seconds": setting(10, (2, 30)),
    "reinvest_profits": setting(True),
    "recover_initial": setting(False, products=("spot",), must_be_off_when_inactive=True),
    "recovery_check_seconds": setting(60, (10, 86400), products=("spot",)),
    "product": setting(
        "spot",
        choices={
            "spot": "Spot · crypto / FX",
            "margin": "Margin · paper only",
            "futures": "Futures · USD linear crypto perpetuals",
        },
    ),
    "leverage": setting(2, (2, 5), choices={n: f"{n}×" for n in range(2, 6)}, products=("margin",)),
    "margin_open_fee_bps": setting("2", ("0", "100"), products=("margin",)),
    "margin_rollover_bps": setting("2", ("0", "100"), products=("margin",)),
    "maintenance_ratio": setting("0.4", ("0.1", "0.9"), products=("margin",)),
    "dca_amount": setting("100", ("0.01", "1000000000"), strategies=("dca",), migrate=True),
    "dca_count": setting(10, (1, 10000), strategies=("dca",), migrate=True),
    "dca_period_seconds": setting(86400, (10, 31536000), strategies=("dca",), migrate=True),
    "twap_side": setting(
        "buy",
        choices={"buy": "Buy", "sell": "Sell · spot inventory / Futures short or reduce"},
        strategies=("twap",),
        migrate=True,
    ),
    "twap_quantity": setting(
        "0.001", ("0.00000001", "1000000000"), strategies=("twap",), migrate=True
    ),
    "twap_limit": setting(
        "0", ("0", "1000000000"), strategies=("twap",), positive=True, migrate=True
    ),
    "twap_slices": setting(12, (2, 10000), strategies=("twap",), migrate=True),
    "twap_duration_seconds": setting(3600, (20, 31536000), strategies=("twap",), migrate=True),
    "rebalance_targets": setting(
        "BTC/USD=50,ETH/USD=30,CASH=20", max_length=1000, strategies=("rebalance",), migrate=True
    ),
    "rebalance_band_pct": setting("5", ("0.1", "50"), strategies=("rebalance",), migrate=True),
    "rebalance_min_trade": setting(
        "10", ("0.01", "1000000000"), strategies=("rebalance",), migrate=True
    ),
    "rebalance_daily_turnover": setting(
        "100", ("0.01", "1000000000"), strategies=("rebalance",), migrate=True
    ),
    "rebalance_cooldown_seconds": setting(
        3600, (10, 604800), strategies=("rebalance",), migrate=True
    ),
    "futures_live_budget": setting("0", ("0", "1000000000"), products=("futures",), migrate=True),
    "futures_leverage": setting(
        1, (1, 5), choices={n: f"{n}×" for n in range(1, 6)}, products=("futures",), migrate=True
    ),
    "futures_dca_side": setting(
        "buy",
        choices={"buy": "Buy / long", "sell": "Sell / short"},
        products=("futures",),
        strategies=("dca",),
        migrate=True,
    ),
    "futures_parent_notional": setting(
        "1000", ("0.01", "1000000000"), products=("futures",), strategies=("twap",), migrate=True
    ),
    "futures_reduce_only": setting(
        False, products=("futures",), strategies=("dca", "twap"), migrate=True
    ),
}
DEFAULTS = {key: field["default"] for key, field in FIELDS.items()}


def schema():
    return {
        "fields": {
            key: {k: v for k, v in field.items() if k != "migrate"} for key, field in FIELDS.items()
        },
        "strategies": STRATEGIES,
    }


def applies(field, values):
    return all(
        not field.get(scope) or values[key] in field[scope]
        for scope, key in (("products", "product"), ("strategies", "strategy"))
    )


def load_settings(saved):
    # Only named, already-introduced additions may migrate. Never invent a missing risk limit.
    additions = {key: field["default"] for key, field in FIELDS.items() if field.get("migrate")}
    # Remove only the retired manual trading-fee assumptions. Existing order snapshots remain intact.
    saved = {
        key: value for key, value in saved.items() if key not in ("maker_fee_bps", "taker_fee_bps")
    }
    return validate_settings({**additions, **saved})


def validate_settings(values):
    if set(values) != set(FIELDS):
        raise SafetyError("Settings must contain exactly the documented fields")
    result = dict(values)
    for key, field in FIELDS.items():
        value, kind = values[key], field["type"]
        if kind == "decimal":
            value = dec(value)
            result[key] = str(value)
        elif kind == "integer" and type(value) is not int:
            raise SafetyError(f"{key} must be an integer")
        elif kind == "boolean" and type(value) is not bool:
            raise SafetyError(f"{key} must be boolean")
        elif kind == "string" and not isinstance(value, str):
            raise SafetyError(f"{key} must be a string")
        if "min" in field and not dec(field["min"]) <= value <= dec(field["max"]):
            raise SafetyError(f"{key} must be between {field['min']} and {field['max']}")
        if "choices" in field and value not in field["choices"]:
            raise SafetyError(f"Unsupported {key}")
        if "max_length" in field and len(value) > field["max_length"]:
            raise SafetyError(f"{key} must contain at most {field['max_length']} characters")
        if field.get("positive") and applies(field, values) and value <= 0:
            raise SafetyError("TWAP requires an explicit positive limit price")
        if field.get("must_be_off_when_inactive") and value and not applies(field, values):
            raise SafetyError("One-time capital recovery is supported for spot portfolios only")
    if values["product"] not in STRATEGIES[values["strategy"]]["products"]:
        raise SafetyError(f"Strategy {values['strategy']} does not support {values['product']}")
    if values["strategy"] == "dca" and values["dca_period_seconds"] < values["interval_seconds"]:
        raise SafetyError("DCA period must be at least the engine interval")
    if (
        values["strategy"] == "twap"
        and values["twap_duration_seconds"] < values["twap_slices"] * values["interval_seconds"]
    ):
        raise SafetyError("Each TWAP slice must span at least one engine interval")
    if values["strategy"] == "rebalance" and dec(values["rebalance_min_trade"]) > dec(
        values["rebalance_daily_turnover"]
    ):
        raise SafetyError("Rebalance minimum trade exceeds daily turnover allowance")
    if dec(result["order_size"]) > dec(result["max_exposure"]):
        raise SafetyError("Order size exceeds maximum exposure")
    return result
