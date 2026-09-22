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
