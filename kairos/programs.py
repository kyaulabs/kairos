"""Persisted deterministic programs; product-specific guards stay in Engine.place."""

import time
import uuid

from kairos.domain import BPS, ZERO, SafetyError, dec, floor
from kairos.settings import SCHEDULED_STRATEGIES as STRATEGIES
from kairos.strategies import limit_price

FIELDS = {
    "dca": ("pair", "quote", "dca_amount", "dca_count", "dca_period_seconds"),
    "twap": (
        "pair",
        "quote",
        "twap_side",
        "twap_quantity",
        "twap_limit",
        "twap_slices",
        "twap_duration_seconds",
    ),
    "rebalance": (
        "quote",
        "rebalance_targets",
        "rebalance_band_pct",
        "rebalance_min_trade",
        "rebalance_daily_turnover",
        "rebalance_cooldown_seconds",
    ),
}


def key(engine):
    product = "futures:" if engine.settings["product"] == "futures" else ""
    return f"program:{product}{engine.mode}:{engine.settings['strategy']}"


def configuration(settings):
    result = {name: settings[name] for name in FIELDS[settings["strategy"]]}
    if settings["product"] == "futures":
        result.update(
            {
                key: settings[key]
                for key in (
                    "product",
                    "futures_leverage",
                    "futures_reduce_only",
                    "futures_dca_side"
                    if settings["strategy"] == "dca"
                    else "futures_parent_notional",
                )
            }
        )
    return result


def targets(settings, resolve):
    """Explicit same-quote basket; cash is a weight, never a conversion route."""
    result = {}
    bases = set()
    entries = settings["rebalance_targets"].split(",")
    if not 2 <= len(entries) <= 11:
        raise SafetyError("Specify 1–10 basket markets plus CASH, with weights totaling 100")
    for entry in entries:
        parts = entry.strip().split("=")
        if len(parts) != 2:
            raise SafetyError("Use MARKET=percent,CASH=percent for basket targets")
        name, weight = parts[0].strip(), dec(parts[1].strip())
        pair = None if name.upper() == "CASH" else resolve(name)
        if pair and (pair.quote != settings["quote"] or pair.base in bases):
            raise SafetyError(
                "Basket markets must have distinct assets and the fixed quote currency"
            )
        identifier = pair.id if pair else "CASH"
        if identifier in result or not ZERO <= weight <= 100 or (pair and weight == 0):
            raise SafetyError("Basket targets must be unique and market weights positive")
        result[identifier] = (pair, weight)
        if pair:
            bases.add(pair.base)
    if "CASH" not in result or sum(weight for _, weight in result.values()) != 100:
        raise SafetyError(
            "Basket weights must total exactly 100, including an explicit CASH weight"
        )
    return result


def validate_markets(settings, resolve):
    if settings["strategy"] == "rebalance":
        return [pair for pair, _ in targets(settings, resolve).values() if pair]
    pair = resolve(settings["pair"])
    if settings["strategy"] == "twap":
        price = pair.price(dec(settings["twap_limit"]), settings["twap_side"])
        quantity = dec(settings["twap_quantity"])
        pair.validate(quantity, price)
        pair.validate(floor(quantity / settings["twap_slices"], pair.lot), price)
    return [pair]


def orders(engine, program):
    return [
        order for order in engine.orders(engine.mode) if order.get("program_id") == program["id"]
    ]


def snapshot(engine):
    if engine.settings["strategy"] not in STRATEGIES:
        return None
    program = engine.store.get(key(engine))
    if not program:
        return None
    children = orders(engine, program)
    return {
        **program,
        "configuration_changed": program["config"] != configuration(engine.settings),
        "filled_by_market": {
            pair: str(
                sum((dec(order["filled"]) for order in children if order["pair"] == pair), ZERO)
            )
            for pair in {order["pair"] for order in children}
        },
        "spent_including_fees": str(
            sum((dec(order["cost"]) + dec(order["fee"]) for order in children), ZERO)
        ),
        "orders": len(children),
    }


def prepare(engine):
    if engine.settings["strategy"] not in STRATEGIES:
        return
    settings = engine.settings
    pairs = validate_markets(settings, engine.resolve)
    program = engine.store.get(key(engine))
    if program:
        if program["config"] != configuration(settings):
            raise SafetyError("Program settings changed; explicitly create a new strategy run")
        if program["status"] == "complete":
            raise SafetyError(
                "Strategy run is complete; explicitly create a new run to trade again"
            )
        return
    fee = dec(settings["taker_fee_bps"]) / BPS
    if settings["product"] == "futures":
        engine.futures.prepare_program(pairs[0])
    elif settings["strategy"] == "dca":
        if engine.balance(settings["quote"]) < dec(settings["dca_amount"]) * settings["dca_count"]:
            raise SafetyError("Pre-fund the entire DCA run in the bot's allocated quote balance")
    elif settings["strategy"] == "twap":
        pair = pairs[0]
        quantity = dec(settings["twap_quantity"])
        asset, needed = (
            (pair.quote, quantity * dec(settings["twap_limit"]) * (1 + fee))
            if settings["twap_side"] == "buy"
            else (pair.base, quantity)
        )
        if engine.balance(asset) < needed:
            raise SafetyError(
                "Pre-fund the entire TWAP parent with allocated cash or bot-owned inventory"
            )
    now = time.time()
    engine.store.put(
        key(engine),
        {
            "id": str(uuid.uuid4()),
            "strategy": settings["strategy"],
            "mode": engine.mode,
            "config": configuration(settings),
            "started": now,
            "next_slot": 0,
            "next_at": now,
            "skipped_slots": 0,
            "status": "active",
            "message": "Ready; first check is due immediately",
        },
    )


def report(engine, program, message):
    program["message"] = message
    engine.store.put(key(engine), program)
    engine.event("program", {"message": message, "program_id": program["id"], "mode": engine.mode})


def finish(engine, program):
    program["status"] = "complete"
    program["next_at"] = None
    report(
        engine,
        program,
        "Run complete; unfilled or skipped amounts remain unspent. New run requires explicit rearming.",
    )
    engine.running = False


async def run(engine):
    settings = engine.settings
    program = engine.store.get(key(engine))
    if not program or program["config"] != configuration(settings):
        raise SafetyError("Start or rearm this strategy run before execution")
    if program["status"] == "complete":
        engine.running = False
        return
    now = time.time()
    if now < program["next_at"]:
        return
    if settings["strategy"] == "rebalance":
        # Persist the cooldown before any data request; a crash cannot repeat this check.
        program["next_slot"] += 1
        program["next_at"] = now + settings["rebalance_cooldown_seconds"]
        engine.store.put(key(engine), program)
        await rebalance(engine, program)
        return
    count = settings["dca_count"] if settings["strategy"] == "dca" else settings["twap_slices"]
    period = (
        settings["dca_period_seconds"]
        if settings["strategy"] == "dca"
        else settings["twap_duration_seconds"] / count
    )
    slot = int((now - program["started"]) / period)
    if slot >= count:
        program["skipped_slots"] += count - program["next_slot"]
        program["next_slot"] = count
        finish(engine, program)
        return
    if slot < program["next_slot"]:
        return
    program["skipped_slots"] += slot - program["next_slot"]
    program["next_slot"] = slot + 1
    program["next_at"] = program["started"] + (slot + 1) * period
    # Claim before I/O. A crash between claim and intent skips a slice rather than duplicating it.
    engine.store.put(key(engine), program)
    await scheduled_order(engine, program, slot)
    if program["next_slot"] == count:
        finish(engine, program)


async def scheduled_order(engine, program, slot):
    settings = engine.settings
    pair = engine.resolve(settings["pair"])
    derivative = settings["product"] == "futures"
    side = (
        (settings["futures_dca_side"] if derivative else "buy")
        if settings["strategy"] == "dca"
        else settings["twap_side"]
    )
    client = engine.futures.client if derivative else engine.kraken
    book = await client.book(pair)
    book.fresh(settings["stale_seconds"])
    if book.spread_bps > dec(settings["max_spread_bps"]):
        report(engine, program, "Skipped scheduled slot: spread exceeds maximum")
        return
    price = limit_price(
        book,
        side,
        settings["slippage_bps"],
        parent=dec(settings["twap_limit"]) if settings["strategy"] == "twap" else None,
    )
    if (side == "buy" and price < book.asks[0][0]) or (side == "sell" and price > book.bids[0][0]):
        report(engine, program, "Skipped TWAP slot: parent limit is not marketable")
        return
    fee = dec(settings["taker_fee_bps"]) / BPS
    children = orders(engine, program)
    if settings["strategy"] == "dca":
        spent = sum((dec(order["cost"]) + dec(order["fee"]) for order in children), ZERO)
        budget = min(
            dec(settings["dca_amount"]), dec(settings["dca_amount"]) * settings["dca_count"] - spent
        )
        volume = floor(max(ZERO, budget) / (price * (1 + fee)), pair.lot)
    else:
        quantity = dec(settings["twap_quantity"])
        per_slice = floor(quantity / settings["twap_slices"], pair.lot)
        desired = (
            per_slice
            if slot < settings["twap_slices"] - 1
            else quantity - per_slice * (settings["twap_slices"] - 1)
        )
        remaining = quantity - sum((dec(order["filled"]) for order in children), ZERO)
        volume = floor(max(ZERO, min(desired, remaining)), pair.lot)
    _, exposure = await engine.valuation(enforce=True)
    order_cap, exposure_cap = engine.limits()
    notional = volume * price
    if derivative:
        if settings["strategy"] == "twap" and sum(
            (dec(o["cost"]) for o in children), ZERO
        ) + notional > dec(settings["futures_parent_notional"]):
            report(engine, program, "Skipped Futures TWAP slice: parent notional cap")
            return
    if (
        volume < pair.minimum
        or notional < pair.cost_minimum
        or notional > order_cap
        or (
            not derivative
            and side == "buy"
            and (
                notional * (1 + fee) > engine.balance(pair.quote)
                or exposure + notional > exposure_cap
            )
        )
        or (not derivative and side == "sell" and volume > engine.balance(pair.base))
    ):
        report(
            engine,
            program,
            "Skipped scheduled slot: minimum size, allocated funds, order cap or exposure limit",
        )
        return
    order = await engine.place(
        pair,
        side,
        volume,
        price,
        book,
        program={"id": program["id"], "slot": slot, "deadline": program["next_at"]},
    )
    if (
        derivative
        and settings["strategy"] == "twap"
        and sum((dec(o["cost"]) for o in orders(engine, program)), ZERO)
        > dec(settings["futures_parent_notional"])
    ):
        raise SafetyError(
            "Futures fill notional exceeded parent cap after price improvement; inspect position"
        )
    report(
        engine,
        program,
        f"Slot {slot + 1}: {side} {order['filled']} {pair.symbol}; {order['status']}. No catch-up of missed or unfilled slices.",
    )


async def rebalance(engine, program):
    settings = engine.settings
    basket = targets(settings, engine.resolve)
    books = {}
    for identifier, (pair, _) in basket.items():
        if pair:
            books[identifier] = await engine.kraken.book(pair)
    for book in books.values():
        book.fresh(settings["stale_seconds"])
        if book.spread_bps > dec(settings["max_spread_bps"]):
            report(engine, program, "Rebalance skipped: a basket spread exceeds maximum")
            return
    values = {
        identifier: engine.balance(pair.base) * books[identifier].mid
        if pair
        else engine.balance(settings["quote"])
        for identifier, (pair, _) in basket.items()
    }
    equity = sum(values.values())
    if equity <= 0:
        report(engine, program, "Rebalance skipped: no allocated basket funds")
        return
    if all(
        abs(values[identifier] / equity * 100 - weight) <= dec(settings["rebalance_band_pct"])
        for identifier, (_, weight) in basket.items()
    ):
        report(engine, program, "Basket is inside the drift band; no trade")
        return
    day_start = int(time.time() // 86400) * 86400
    # Across run IDs: rearming or editing a basket cannot erase today's turnover.
    turnover = sum(
        (
            dec(order["cost"]) + dec(order["fee"])
            for order in engine.orders(engine.mode)
            if order["strategy"] == "rebalance" and order["created"] >= day_start
        ),
        ZERO,
    )
    allowance = max(ZERO, dec(settings["rebalance_daily_turnover"]) - turnover)
    fee = dec(settings["taker_fee_bps"]) / BPS
    _, exposure = await engine.valuation(enforce=True)
    order_cap, exposure_cap = engine.limits()
    deltas = [
        (identifier, values[identifier] - equity * weight / 100)
        for identifier, (pair, weight) in basket.items()
        if pair
    ]
    # Sell excess holdings before buying deficits. At most one settled order per cooldown.
    for identifier, delta in sorted(deltas, key=lambda item: (item[1] <= 0, -abs(item[1]))):
        if delta == 0:
            continue
        pair, _ = basket[identifier]
        book = books[identifier]
        side = "sell" if delta > 0 else "buy"
        price = limit_price(book, side, settings["slippage_bps"])
        budget = min(abs(delta), order_cap, allowance / (1 + fee))
        if side == "buy":
            budget = min(
                budget, engine.balance(pair.quote) / (1 + fee), max(ZERO, exposure_cap - exposure)
            )
        volume = floor(max(ZERO, budget) / price, pair.lot)
        if side == "sell":
            volume = min(volume, floor(engine.balance(pair.base), pair.lot))
        if volume < pair.minimum or volume * price < max(
            pair.cost_minimum, dec(settings["rebalance_min_trade"])
        ):
            continue
        order = await engine.place(
            pair,
            side,
            volume,
            price,
            book,
            program={
                "id": program["id"],
                "slot": program["next_slot"] - 1,
                "deadline": program["next_at"],
            },
        )
        report(
            engine,
            program,
            f"Rebalance {side}: {order['filled']} {pair.symbol}; {order['status']}. Revalue confirmed fills at the next check.",
        )
        return
    report(
        engine, program, "Rebalance skipped: funds, minimum trade, exposure or daily turnover limit"
    )
