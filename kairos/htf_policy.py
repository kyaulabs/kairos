"""Experimental pullback entries and observation-only range signals.

Targets describe historical price structure, not forecasts. Constants are explicit
paper-experiment hypotheses, not parameters fitted to the recent losing trades.
"""

from kairos.domain import BPS, ZERO, dec
from kairos.strategies import trend_state

POLICY = "pullback-v1"


def target_room(book, side, target, settings, maker_fee, taker_fee):
    """Passive entry / adverse marketable exit, rounded to venue ticks."""
    long = side == "buy"
    price = book.pair.price(book.bids[0][0] if long else book.asks[0][0], side)
    half_spread = book.spread_bps / (2 * BPS)
    slip = dec(settings["slippage_bps"]) / BPS
    closing = book.pair.price(
        target * (1 - half_spread) * (1 - slip)
        if long
        else target * (1 + half_spread) * (1 + slip),
        side,
    )
    opening_fee = maker_fee + (
        dec(settings["margin_open_fee_bps"]) if settings["product"] == "margin" else ZERO
    )
    net = (
        closing * (1 - taker_fee / BPS) - price * (1 + opening_fee / BPS)
        if long
        else price * (1 - opening_fee / BPS) - closing * (1 + taker_fee / BPS)
    )
    return price, net / price * BPS


def signal(rows, settings, book, maker_fee, taker_fee):
    view = trend_state(rows, settings, taker_fee, book.spread_bps)
    closes = [dec(r[4]) for r in rows[-30:]]
    highs, lows = [dec(r[2]) for r in rows[-30:]], [dec(r[3]) for r in rows[-30:]]
    fast, slow = dec(view["fast_average"]), dec(view["slow_average"])
    prior_fast, prior_slow = sum(closes[-9:-1]) / 8, sum(closes[-22:-1]) / 21
    atr = (
        sum(
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(len(closes) - 14, len(closes))
        )
        / 14
    )
    # A 0.5–2 ATR retracement to the preceding fast mean, then a 0.1 ATR
    # recovery. Do not chase a quote/close more than 0.5 ATR beyond that mean.
    long_target, short_target = max(highs[-9:-1]), min(lows[-9:-1])
    gates = {
        "positive_atr": atr > 0,
        "rising_trend": fast > slow > prior_slow,
        "prior_close_falling": closes[-2] < closes[-3],
        "touch_prior_fast": lows[-2] <= prior_fast,
        "retracement_0_5_to_2_atr": dec("0.5") * atr <= long_target - lows[-2] <= 2 * atr,
        "recovery_close_and_quote": min(closes[-1], book.mid) >= closes[-2] + dec("0.1") * atr,
        "no_chase_close_and_quote": max(closes[-1], book.mid) <= fast + dec("0.5") * atr,
    }
    long_setup = all(gates.values())
    short_setup = bool(
        atr > 0
        and fast < slow < prior_slow
        and closes[-2] > closes[-3]
        and highs[-2] >= prior_fast
        and dec("0.5") * atr <= highs[-2] - short_target <= 2 * atr
        and max(closes[-1], book.mid) <= closes[-2] - dec("0.1") * atr
        and min(closes[-1], book.mid) >= fast - dec("0.5") * atr
    )
    long_price, long_room = target_room(book, "buy", long_target, settings, maker_fee, taker_fee)
    short_price, short_room = target_room(
        book, "sell", short_target, settings, maker_fee, taker_fee
    )
    buffer = max(dec(10), atr / book.mid * BPS * dec("0.25"))
    view.update(
        entry_policy=POLICY,
        structural_gates=gates,
        execution_gates={"net_room": long_room >= buffer},
        gate_values={
            "prior_fast": str(prior_fast),
            "pullback_low": str(lows[-2]),
            "retracement": str(long_target - lows[-2]),
            "recovery_floor": str(closes[-2] + dec(".1") * atr),
            "chase_ceiling": str(fast + dec(".5") * atr),
        },
        entry_execution="post-only",
        entry_eligible=long_setup and long_room >= buffer,
        short_entry_eligible=short_setup and short_room >= buffer and settings["product"] != "spot",
        pullback_long=long_setup,
        pullback_short=short_setup,
        atr=str(atr),
        previous_slow_average=str(prior_slow),
        entry_prices={"buy": str(long_price), "sell": str(short_price)},
        entry_targets={"buy": str(long_target), "sell": str(short_target)},
        target_net_room_bps={"buy": str(long_room), "sell": str(short_room)},
        required_net_room_bps=str(buffer),
        maker_fee_bps=str(maker_fee),
        taker_fee_bps=str(taker_fee),
        target_cost_assumption="Passive entry at current same-side best quote, maker entry fee, taker exit fee, current spread and adverse exit slippage. Excludes future funding/borrowing. Target is not a forecast.",
    )

    # Independent 20-window, 2-sigma band re-entry with low directional efficiency.
    # This records opportunities only: never orders, inventory or simulated profits.
    def bands(values):
        mean = sum(values) / len(values)
        sd = (sum((v - mean) ** 2 for v in values) / len(values)).sqrt()
        return mean, mean - 2 * sd, mean + 2 * sd

    _, old_lower, old_upper = bands(closes[-21:-1])
    middle, lower, upper = bands(closes[-20:])
    travel = sum(abs(b - a) for a, b in zip(closes[-20:-1], closes[-19:], strict=True))
    efficiency = abs(closes[-1] - closes[-20]) / travel if travel else dec(1)
    side = (
        "buy"
        if closes[-2] < old_lower and lower <= closes[-1] < middle
        else "sell"
        if closes[-2] > old_upper and middle < closes[-1] <= upper and settings["product"] != "spot"
        else "hold"
    )
    room = (
        target_room(book, side, middle, settings, maker_fee, taker_fee)[1]
        if side != "hold"
        else None
    )
    view["range_observation"] = {
        "policy": "range-observation-v1",
        "observation_only": True,
        "signal": side,
        "eligible": bool(side != "hold" and efficiency <= dec("0.3") and room >= buffer),
        "target": str(middle),
        "efficiency": str(efficiency),
        "net_room_bps": str(room) if room is not None else None,
        "required_net_room_bps": str(buffer),
    }
    return view
