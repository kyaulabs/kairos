import time
from dataclasses import asdict, dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal, InvalidOperation

ZERO = Decimal(0)
BPS = Decimal(10000)
TERMINAL = {"closed", "canceled", "expired", "rejected"}
CANDLE_INTERVALS = (1, 5, 15, 30, 60, 240, 1440)


class SafetyError(Exception):
    """A condition requiring the operator's attention, safe to show in the UI."""


def dec(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SafetyError("Invalid decimal value") from exc
    if not result.is_finite():
        raise SafetyError("Values must be finite")
    return result


def floor(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


@dataclass(frozen=True)
class Pair:
    id: str
    symbol: str
    base: str
    quote: str
    tick: Decimal
    lot: Decimal
    minimum: Decimal
    cost_minimum: Decimal
    leverage_buy: tuple = ()
    leverage_sell: tuple = ()

    @classmethod
    def parse(cls, key, data):
        return cls(
            key,
            data["wsname"].replace("XBT", "BTC"),
            data["base"],
            data["quote"],
            dec(data.get("tick_size", 10 ** -data["pair_decimals"])),
            Decimal(10) ** -data["lot_decimals"],
            dec(data["ordermin"]),
            dec(data.get("costmin", "0")),
            tuple(data.get("leverage_buy", [])),
            tuple(data.get("leverage_sell", [])),
        )

    def price(self, value, side):
        rounding = ROUND_DOWN if side == "buy" else ROUND_UP
        return (value / self.tick).to_integral_value(rounding=rounding) * self.tick

    def validate(self, volume, price):
        if volume <= 0 or price <= 0 or volume < self.minimum or volume * price < self.cost_minimum:
            raise SafetyError(f"Order below {self.symbol} minimum size or cost")
        if floor(volume, self.lot) != volume or floor(price, self.tick) != price:
            raise SafetyError("Order precision does not match exchange rules")

    def public(self):
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }


@dataclass
class Book:
    pair: Pair
    bids: list
    asks: list
    received: float

    @property
    def mid(self):
        return (self.bids[0][0] + self.asks[0][0]) / 2

    @property
    def spread_bps(self):
        return (self.asks[0][0] - self.bids[0][0]) / self.mid * BPS

    def fresh(self, seconds):
        if time.time() - self.received > seconds:
            raise SafetyError("Market snapshot is stale")
        if not self.bids or not self.asks or self.bids[0][0] >= self.asks[0][0]:
            raise SafetyError("Invalid or crossed order book")

    def fill(self, side, volume, limit):
        """Walk displayed liquidity, never fill beyond the limit or visible depth."""
        remaining, cost = volume, ZERO
        for price, size in self.asks if side == "buy" else self.bids:
            if (side == "buy" and price > limit) or (side == "sell" and price < limit):
                break
            take = min(remaining, size)
            cost += take * price
            remaining -= take
            if remaining <= 0:
                break
        return volume - remaining, cost


DEFAULTS = {
    "pair": "XBTUSD",
    "strategy": "htf",
    "quote": "ZUSD",
    "paper_balance": "1000",
    "live_budget": "0",
    "order_size": "1000",
    "max_exposure": "1000",
    "daily_loss": "1000",
    "maker_fee_bps": "25",
    "taker_fee_bps": "40",
    "slippage_bps": "10",
    "max_spread_bps": "30",
    "min_confidence": "0.7",
    "arb_min_profit_bps": "10",
    "interval_seconds": 15,
    "candle_minutes": 15,
    "stale_seconds": 10,
    "reinvest_profits": True,
    "recover_initial": False,
    "recovery_check_seconds": 60,
    "product": "spot",
    "leverage": 2,
    "margin_open_fee_bps": "2",
    "margin_rollover_bps": "2",
    "maintenance_ratio": "0.4",
}
DECIMAL_BOUNDS = {
    "paper_balance": ("1", "1000000000"),
    "live_budget": ("0", "1000000000"),
    "order_size": ("0.01", "1000000000"),
    "max_exposure": ("0.01", "1000000000"),
    "daily_loss": ("0.01", "1000000000"),
    "maker_fee_bps": ("0", "1000"),
    "taker_fee_bps": ("0", "1000"),
    "slippage_bps": ("0", "100"),
    "max_spread_bps": ("0.01", "1000"),
    "min_confidence": ("0", "1"),
    "arb_min_profit_bps": ("0", "1000"),
    "margin_open_fee_bps": ("0", "100"),
    "margin_rollover_bps": ("0", "100"),
    "maintenance_ratio": ("0.1", "0.9"),
}


def validate_settings(values):
    if set(values) != set(DEFAULTS):
        raise SafetyError("Settings must contain exactly the documented fields")
    result = dict(values)
    for key, (low, high) in DECIMAL_BOUNDS.items():
        number = dec(values[key])
        if not dec(low) <= number <= dec(high):
            raise SafetyError(f"{key} must be between {low} and {high}")
        result[key] = str(number)
    for key, low, high in (
        ("interval_seconds", 10, 3600),
        ("stale_seconds", 2, 30),
        ("recovery_check_seconds", 10, 86400),
    ):
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise SafetyError(f"{key} must be an integer between {low} and {high}")
    if (
        type(values["candle_minutes"]) is not int
        or values["candle_minutes"] not in CANDLE_INTERVALS
    ):
        raise SafetyError("Unsupported candle interval")
    if any(type(values[key]) is not bool for key in ("reinvest_profits", "recover_initial")):
        raise SafetyError("Reinvestment and recovery switches must be boolean")
    if values["product"] == "margin" and values["recover_initial"]:
        raise SafetyError("One-time capital recovery is supported for spot portfolios only")
    if values["product"] not in ("spot", "margin"):
        raise SafetyError("Unknown trading product")
    if type(values["leverage"]) is not int or not 2 <= values["leverage"] <= 5:
        raise SafetyError("Paper leverage must be an integer from 2 to 5")
    if values["product"] == "margin" and values["strategy"] == "arbitrage":
        raise SafetyError("Triangular arbitrage uses spot balances, not margin positions")
    if values["strategy"] not in ("htf", "maker", "arbitrage"):
        raise SafetyError("Unknown strategy")
    if not isinstance(values["pair"], str) or not isinstance(values["quote"], str):
        raise SafetyError("Pair and quote must be strings")
    if dec(result["order_size"]) > dec(result["max_exposure"]):
        raise SafetyError("Order size exceeds maximum exposure")
    if dec(result["maker_fee_bps"]) > dec(result["taker_fee_bps"]):
        raise SafetyError("Maker fee assumption must not exceed taker fee assumption")
    return result
