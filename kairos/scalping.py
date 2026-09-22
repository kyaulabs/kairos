"""Paper-only rolling Bollinger mean reversion. No model calls or chart inputs."""

import time
import uuid

from kairos.domain import BPS, ZERO, SafetyError, dec, floor
from kairos.strategies import limit_price


def key(engine):
    return f"scalp:dry-run:{engine.settings['product']}:{engine.settings['pair']}"


def snapshot(engine):
    return engine.store.get(
        key(engine), {"position": None, "last_candle": None, "cooldown_until": 0}
    )


def quantity(engine, pair):
    return (
        engine.futures.position(pair)
        if engine.settings["product"] == "futures"
        else engine.balance(pair.base)
    )


def prepare(engine):
    if engine.mode != "dry-run" or engine.settings["product"] not in {"spot", "futures"}:
        raise SafetyError("Bollinger scalping is paper-only for spot and linear Futures")
    pair = engine.resolve(engine.settings["pair"])
    state = snapshot(engine)
    position = state["position"]
    if engine.settings["product"] == "spot":
        others = {
            asset: dec(value)
            for asset, value in engine.ledger()["balances"].items()
            if asset not in {pair.base, pair.quote}
        }
    else:
        others = {
            market: dec(row["quantity"])
            for market, row in engine.futures.ledger()["positions"].items()
            if market != pair.id
        }
    if any(others.values()):
        raise SafetyError("Scalping requires one position only; use a flat paper portfolio")
    owned = sum(
        (
            dec(order["filled"]) * (1 if order["side"] == "buy" else -1)
            for order in engine.orders("dry-run")
            if position and order.get("scalp_id") == position["id"]
        ),
        ZERO,
    )
    if quantity(engine, pair) != owned:
        raise SafetyError(
            "Scalping cannot adopt unrelated holdings; close or reset the paper portfolio"
        )


def tag(engine, order):
    if order["strategy"] == "scalp":
        position = snapshot(engine)["position"]
        if not position:
            raise SafetyError("Scalping order has no durable protection plan")
        order["scalp_id"] = position["id"]


def valid_exit(engine, pair, side, volume):
    prepare(engine)
    held = quantity(engine, pair)
    return bool(
        snapshot(engine)["position"]
        and pair.id == engine.settings["pair"]
        and ZERO < volume <= abs(held)
        and side == ("sell" if held > 0 else "buy")
    )


def signal(rows, settings, now=None):
    now = time.time() if now is None else now
    window = settings["scalp_window"]
    rows = rows[-(window + 1) :]
    cutoff = int(now) // 60 * 60
    if len(rows) != window + 1:
        raise SafetyError("Scalping needs a full rolling window of completed 1-minute candles")
    closes = []
    for index, row in enumerate(rows):
        ts = dec(row[0])
        opening, high, low, close = map(dec, row[1:5])
        if (
            ts != cutoff - (len(rows) - index) * 60
            or not ZERO < low <= min(opening, close) <= max(opening, close) <= high
        ):
            raise SafetyError("Scalping candle history is stale, incomplete or malformed")
        closes.append(close)

    def bands(values):
        mean = sum(values) / len(values)
        sd = (sum((value - mean) ** 2 for value in values) / len(values)).sqrt()
        width = sd * dec(settings["scalp_sigma"])
        return mean, mean - width, mean + width, sd

    _, previous_lower, previous_upper, _ = bands(closes[:-1])
    mean, lower, upper, sd = bands(closes[1:])
    travel = sum(abs(b - a) for a, b in zip(closes[1:-1], closes[2:], strict=True))
    efficiency = abs(closes[-1] - closes[1]) / travel if travel else dec(1)
    side = (
        "buy"
        if closes[-2] < previous_lower and lower <= closes[-1] < mean
        else "sell"
        if closes[-2] > previous_upper and mean < closes[-1] <= upper
        else "hold"
    )
    return {
        "candle_close_time": cutoff,
        "candle_minutes": 1,
        "window": window,
        "middle": str(mean),
        "lower": str(lower),
        "upper": str(upper),
        "z_score": str((closes[-1] - mean) / sd) if sd else "0",
        "efficiency": str(efficiency),
        "range_eligible": bool(
            sd and lower > 0 and efficiency <= dec(settings["scalp_max_efficiency"])
        ),
        "signal": side,
        "series": [
            {"time": int(row[0]), "close": str(close)}
            for row, close in zip(rows[1:], closes[1:], strict=True)
        ],
    }


def cost_room(book, side, target, settings, fee_bps):
    """Worst-side tick rounding and fees on each distinct notional, not just 2 × entry fee."""
    half_spread = book.spread_bps / (2 * BPS)
    slip, fee = dec(settings["slippage_bps"]) / BPS, fee_bps / BPS
    long = side == "buy"
    opening = book.pair.price(
        book.asks[0][0] * (1 + slip) if long else book.bids[0][0] * (1 - slip),
        "sell" if long else "buy",
    )
    closing = book.pair.price(
        target * (1 - half_spread) * (1 - slip)
        if long
        else target * (1 + half_spread) * (1 + slip),
        "buy" if long else "sell",
    )
    net = (
        closing * (1 - fee) - opening * (1 + fee)
        if long
        else opening * (1 - fee) - closing * (1 + fee)
    )
    room = (target - book.mid if long else book.mid - target) / book.mid * BPS
    return room, room - net / book.mid * BPS + dec(settings["scalp_margin_bps"])


def report(engine, pair, action, reason, data):
    decision = {
        "action": action,
        "confidence": None,
        "probabilities": {},
        "deterministic": True,
        "reason": reason,
        "strategy": "scalp",
        "pair": pair.id,
        "mode": engine.mode,
        "model": "Bollinger rules",
        "latency_ms": 0,
        "ts": time.time(),
        "state": {
            "strategy": "scalp",
            "product": engine.settings["product"],
            "symbol": pair.symbol,
            "inventory": str(quantity(engine, pair)),
            **data,
        },
    }
    engine.latest_decision = decision
    # Keep chart arrays in the current snapshot/entry plan, not repeated in the event log.
    event_state = {k: v for k, v in decision["state"].items() if k not in {"series", "position"}}
    if data.get("position"):
        event_state["position"] = {k: v for k, v in data["position"].items() if k != "signal"}
    engine.event("decision", {**decision, "state": event_state})


async def run(engine):
    prepare(engine)
    settings = engine.settings
    pair = engine.resolve(settings["pair"])
    state = snapshot(engine)
    position = state["position"]
    held = quantity(engine, pair)
    if position and not held:
        state.update(position=None, cooldown_until=time.time() + settings["scalp_cooldown_seconds"])
        engine.store.put(key(engine), state)
        position = None
    # Existing protection does not depend on obtaining a new candle or profitable exit.
    await engine.valuation(False)
    client = engine.futures.client if settings["product"] == "futures" else engine.kraken
    book = await client.book(pair)
    book.fresh(settings["stale_seconds"])
    if any(price <= 0 or size <= 0 for price, size in book.bids + book.asks):
        raise SafetyError("Invalid scalping order-book depth")
    if book.spread_bps > dec(settings["max_spread_bps"]):
        raise SafetyError("Scalping spread exceeds maximum; position may remain")
    fee = engine.fees.reserve(pair)
    now = time.time()
    data = {
        "mid": str(book.mid),
        "taker_fee_bps": str(fee),
        "spread_bps": str(book.spread_bps),
        "position": position,
    }
    if position:
        mark = book.bids[0][0] if held > 0 else book.asks[0][0]
        hit_stop = mark <= dec(position["stop"]) if held > 0 else mark >= dec(position["stop"])
        hit_target = (
            mark >= dec(position["target"]) if held > 0 else mark <= dec(position["target"])
        )
        reason = position.get("exit_reason") or (
            "Stop reached"
            if hit_stop
            else "Holding deadline reached"
            if now >= position["deadline"]
            else "Risk limit reached"
            if -dec(engine.daily_pnl) >= dec(settings["daily_loss"])
            or dec(engine.exposure) > engine.limits()[1]
            else "Midpoint target reached"
            if hit_target
            else None
        )
        data.update(position=position, **position["signal"])
        if not reason:
            report(engine, pair, "hold", "Managing fixed target, stop and holding deadline", data)
            return
        position["exit_reason"] = reason
        engine.store.put(key(engine), state)
        side = "sell" if held > 0 else "buy"
        price = limit_price(book, side, settings["slippage_bps"])
        volume = floor(min(abs(held), engine.limits()[0] / price), pair.lot)
        pair.validate(volume, price)
        report(
            engine,
            pair,
            side,
            reason + " · bounded paper reduction; partial fills may remain",
            data,
        )
        await engine.place(pair, side, volume, price, book, exit_only=True)
        if not quantity(engine, pair):
            state.update(
                position=None, cooldown_until=time.time() + settings["scalp_cooldown_seconds"]
            )
            engine.store.put(key(engine), state)
        return
    await engine.valuation(True)
    if now < state["cooldown_until"]:
        report(
            engine,
            pair,
            "hold",
            "Post-exit cooldown",
            {**data, "cooldown_until": state["cooldown_until"]},
        )
        return
    rows = await (
        client.completed_candles(pair, 1)
        if settings["product"] == "futures"
        else client.candles(pair, 1)
    )
    view = signal(rows, settings)
    data.update(view)
    candle = view["candle_close_time"]
    if state["last_candle"] == candle:
        return
    state["last_candle"] = candle
    engine.store.put(
        key(engine), state
    )  # Claim before any order attempt; never repeat this candle.
    side = view["signal"]
    target = dec(view["middle"])
    room, costs = (
        cost_room(book, side, target, settings, fee)
        if side != "hold"
        else cost_room(book, "buy", book.mid, settings, fee)
    )
    data.update(
        round_trip_cost_bps=str(costs),
        target_room_bps=str(room),
        net_room_bps=str(room - costs),
        cost_eligible=room > costs,
    )
    if side == "hold" or side == "sell" and settings["product"] == "spot":
        report(
            engine,
            pair,
            "hold",
            "Waiting for lower-band re-entry"
            if settings["product"] == "spot"
            else "Waiting for band re-entry",
            data,
        )
        return
    if not view["range_eligible"]:
        report(engine, pair, "hold", "Trend/volatility filter blocks entry", data)
        return
    if room <= costs or not dec(view["lower"]) <= book.mid <= dec(view["upper"]):
        report(
            engine,
            pair,
            "hold",
            "Midpoint room does not cover costs and safety margin, or price left the range",
            data,
        )
        return
    price = limit_price(book, side, settings["slippage_bps"])
    cap, maximum = engine.limits()
    if settings["product"] == "spot":
        budget = min(
            cap, engine.balance(pair.quote) / (1 + fee / BPS), maximum - dec(engine.exposure)
        )
    else:
        values, exposure = await engine.futures.valuation(True)
        im, _ = engine.futures.margin_rates(pair, exposure + cap)
        budget = min(
            cap,
            maximum - exposure,
            max(ZERO, values["free"] - dec(engine.futures.ledger()["initial"]) * dec("0.2"))
            / (im + 2 * fee / BPS),
        )
    volume = floor(max(ZERO, budget) / price, pair.lot)
    pair.validate(volume, price)
    position = {
        "id": str(uuid.uuid4()),
        "side": side,
        "entry_limit": str(price),
        "target": str(target),
        "stop": str(
            price
            * (
                1 - dec(settings["scalp_stop_bps"]) / BPS
                if side == "buy"
                else 1 + dec(settings["scalp_stop_bps"]) / BPS
            )
        ),
        "opened_at": time.time(),
        "deadline": time.time() + settings["scalp_max_hold_seconds"],
        "signal": {
            **view,
            "round_trip_cost_bps": str(costs),
            "target_room_bps": str(room),
            "net_room_bps": str(room - costs),
            "entry_fee_bps": str(fee),
        },
        "exit_reason": None,
    }
    state["position"] = position
    engine.store.put(key(engine), state)  # Protection survives a crash between intent and fill.
    report(
        engine,
        pair,
        side,
        "Band re-entry, range and net-cost filters passed · paper IOC",
        {**data, "position": position},
    )
    await engine.place(pair, side, volume, price, book)
