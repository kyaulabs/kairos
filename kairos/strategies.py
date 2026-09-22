from dataclasses import dataclass

from kairos.domain import BPS, SafetyError, dec, floor


def limit_price(book, side, slippage_bps, *, maker_fee_bps=None, parent=None):
    """One tick-rounded price policy; callers still check freshness, funding and parent size."""
    if side not in ("buy", "sell"):
        raise SafetyError("Invalid order side")
    slip = dec(slippage_bps) / BPS
    if maker_fee_bps is not None:
        width = dec(maker_fee_bps) / BPS + slip
        raw = (
            min(book.bids[0][0], book.mid * (1 - width))
            if side == "buy"
            else max(book.asks[0][0], book.mid * (1 + width))
        )
    else:
        raw = book.asks[0][0] * (1 + slip) if side == "buy" else book.bids[0][0] * (1 - slip)
    if parent is not None:
        raw = min(raw, parent) if side == "buy" else max(raw, parent)
    return book.pair.price(raw, side)


def trend_state(rows, settings, taker_fee_bps):
    if len(rows) < 30:
        raise SafetyError("Need at least 30 completed candles")
    closes = [dec(row[4]) for row in rows[-30:]]
    fast, slow = sum(closes[-8:]) / 8, sum(closes[-21:]) / 21
    move = (closes[-1] / closes[-9] - 1) * BPS
    costs = taker_fee_bps * 2 + dec(settings["slippage_bps"]) * 2
    return {
        "candle_close_time": int(rows[-1][0]) + settings["candle_minutes"] * 60,
        "candle_minutes": settings["candle_minutes"],
        "trend": "rising" if fast > slow else "falling",
        "eight_candle_return_bps": str(move),
        "round_trip_cost_bps": str(costs),
        "historical_move_exceeds_round_trip_cost": abs(move) > costs,
        "entry_eligible": fast > slow and move > costs,
        "exit_eligible": fast < slow,
        "note": "Historical momentum is not a forecast of future returns.",
    }


@dataclass(frozen=True)
class Leg:
    pair: object
    side: str

    @property
    def source(self):
        return self.pair.quote if self.side == "buy" else self.pair.base

    @property
    def target(self):
        return self.pair.base if self.side == "buy" else self.pair.quote


def triangle(primary, pairs):
    """Use one liquid BTC/ETH bridge, in both directions; no unbounded market scan."""
    for bridge_asset in ("XXBT", "XETH"):
        if bridge_asset == primary.base:
            continue
        bridge = next(
            (p for p in pairs.values() if p.base == bridge_asset and p.quote == primary.quote), None
        )
        cross = next(
            (p for p in pairs.values() if {p.base, p.quote} == {primary.base, bridge_asset}), None
        )
        if bridge and cross:
            middle = Leg(cross, "sell" if cross.base == primary.base else "buy")
            forward = [Leg(primary, "buy"), middle, Leg(bridge, "sell")]
            reverse = [Leg(x.pair, "sell" if x.side == "buy" else "buy") for x in reversed(forward)]
            return forward, reverse
    raise SafetyError("No BTC/ETH triangular route exists for this pair and quote currency")


def plan_leg(leg, book, amount, fee_bps, slippage_bps):
    fee = fee_bps / BPS
    limit = limit_price(book, leg.side, slippage_bps)
    volume = floor(amount / (limit * (1 + fee)) if leg.side == "buy" else amount, leg.pair.lot)
    leg.pair.validate(volume, limit)
    filled, _ = book.fill(leg.side, volume, limit)
    if filled < volume:
        raise SafetyError("Insufficient displayed depth for the proposed order")
    # Profitability assumes every leg executes at its worst permitted price.
    output = volume if leg.side == "buy" else volume * limit * (1 - fee)
    return volume, limit, output


def plan_cycle(legs, books, amount, settings, fees):
    output = amount
    planned = []
    for leg in legs:
        volume, limit, output = plan_leg(
            leg,
            books[leg.pair.id],
            output,
            fees.reserve(leg.pair),
            dec(settings["slippage_bps"]),
        )
        planned.append(
            {"pair": leg.pair.id, "side": leg.side, "volume": str(volume), "limit": str(limit)}
        )
    return {
        "legs": planned,
        "input": str(amount),
        "minimum_output": str(output),
        "edge_bps": str((output / amount - 1) * BPS),
    }
