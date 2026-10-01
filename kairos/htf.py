"""Rolling HTF/Jev execution with durable, bot-owned exit plans.

Pullback targets and cost room are hypotheses, NOT forecasts of net returns.
Stops and deadlines are local and operate only while the engine is running.
"""

import uuid
from decimal import ROUND_UP

from kairos.domain import BPS, TERMINAL, ZERO, SafetyError, dec, floor
from kairos.htf_review import ExpiredReview
from kairos.strategies import limit_price

EXPOSURE_TARGET = dec("0.8")


def key(engine, mode=None):
    return f"htf:{mode or engine.mode}:{engine.settings['product']}:{engine.settings['pair']}"


def snapshot(engine):
    return engine.store.get(key(engine)) or {"position": None, "last_candle": None}


def arm(engine):
    """Re-baseline entries on Start without changing an existing exit plan."""
    state = snapshot(engine)
    state["entry_signal"] = None
    state["pending_signal"] = None
    engine.htf_review.cancel()
    engine.store.put(key(engine), state)


def quantity(engine, pair):
    if engine.settings["product"] == "futures":
        return engine.futures.position(pair)
    if engine.settings["product"] == "margin":
        return dec(engine.store.get("margin")["positions"].get(pair.id, {}).get("quantity", 0))
    return engine.balance(pair.base)


def owned(engine, position, mode=None):
    return sum(
        (
            dec(o["filled"]) * (1 if o["side"] == "buy" else -1)
            for o in engine.orders(mode or engine.mode)
            if position
            and o.get("htf_id") == position["id"]
            and o["pair"] == engine.settings["pair"]
        ),
        dec((position or {}).get("inventory_adjustment", 0)),
    )


def reconcile_entry(engine):
    """An unfilled passive quote never replaces a retained residual's protection."""
    state = snapshot(engine)
    attempt = state.get("entry_attempt")
    if not attempt:
        return
    order = next((o for o in engine.orders() if o["id"] == attempt["order_id"]), None)
    if order and dec(order["filled"]) > 0:
        state["position"] = attempt["position"]
    if not order or order["status"] in TERMINAL:
        state.pop("entry_attempt")
    engine.store.put(key(engine), state)


def prepare(engine):
    reconcile_entry(engine)
    pair = engine.resolve(engine.settings["pair"])
    if quantity(engine, pair) != owned(engine, snapshot(engine)["position"]):
        raise SafetyError("HTF cannot adopt unrelated holdings; review/close them before starting")


def tag(engine, order):
    if order["strategy"] == "htf" and order["pair"] == engine.settings["pair"]:
        state = snapshot(engine)
        attempt = state.get("entry_attempt")
        position = attempt["position"] if attempt else state["position"]
        if position:
            order["htf_id"] = position["id"]
        if attempt:
            attempt["order_id"] = order["id"]
            engine.store.put(key(engine), state)


def valid_exit(engine, pair, side, volume):
    prepare(engine)
    held = quantity(engine, pair)
    return bool(
        snapshot(engine)["position"]
        and pair.id == engine.settings["pair"]
        and ZERO < volume <= abs(held)
        and side == ("sell" if held > 0 else "buy")
    )


def below_minimum(pair, volume, price):
    return volume <= 0 or volume < pair.minimum or volume * price < pair.cost_minimum


def minimum_volume(pair, price):
    minimum = max(pair.minimum, pair.cost_minimum / price, pair.lot)
    return (minimum / pair.lot).to_integral_value(rounding=ROUND_UP) * pair.lot


def entry_side(view, product):
    if view["entry_eligible"]:
        return "buy"
    if product != "spot" and view["short_entry_eligible"]:
        return "sell"
    return "hold"


def entry_context(engine, pair, view, book):
    """Project the fresh-edge state without mutating it in the model worker."""
    state = snapshot(engine)
    side = entry_side(view, engine.settings["product"])
    previous = state.get("entry_signal")
    pending = state.get("pending_signal")
    if previous is None:
        pending = None
    elif side != previous:
        new_window = (
            not engine.htf_review.native
            or state.get("last_candle")
            != f"{engine.settings['candle_minutes']}:{view['candle_close_time']}"
        )
        pending = side if side != "hold" and new_window else None
    ready = pending == side and side != "hold"
    held = quantity(engine, pair)
    exit_side = "sell" if held > 0 else "buy"
    exit_price = limit_price(book, exit_side, engine.settings["slippage_bps"])
    residual = bool(held and below_minimum(pair, floor(abs(held), pair.lot), exit_price))
    blockers = []
    if view.get("entry_execution") in {"post-only", "passive-limit"} and engine.mode not in {
        "dry-run",
        "paper",
    }:
        blockers.append("passive entry experiment is paper-only")
    if previous is None:
        blockers.append("startup baseline")
    elif engine.htf_review.native and side != previous and not new_window:
        blockers.append("native HTF setup transitions require a new completed bar")
    if side == "hold":
        blockers.append("no cost-qualified pullback")
    elif not ready:
        blockers.append("no fresh unconsumed setup")
    if held and not residual:
        blockers.append("owned position; no pyramiding")
    if residual and side != ("buy" if held > 0 else "sell"):
        blockers.append("residual requires same-direction setup")
    if state.get("entry_attempt") or engine.orders(active=True):
        blockers.append("entry/order awaiting settlement")
    if -dec(engine.daily_pnl) >= dec(engine.settings["daily_loss"]):
        blockers.append("daily loss limit")
    if book.spread_bps > dec(engine.settings["max_spread_bps"]):
        blockers.append("spread limit")
    cap, maximum = engine.limits()
    if min(cap, maximum * EXPOSURE_TARGET - dec(engine.exposure)) < max(
        pair.minimum * book.mid, pair.cost_minimum, pair.lot * book.mid
    ):
        blockers.append("allocation below market minimum")
    if engine.settings["product"] == "spot" and engine.balance(pair.quote) < max(
        pair.minimum * book.mid, pair.cost_minimum, pair.lot * book.mid
    ) * (1 + engine.fees.reserve(pair, True) / BPS):
        blockers.append("insufficient allocated cash")
    allowed = ["hold"]
    if not blockers:
        allowed.append(side)
    elif (
        held and not residual and not state.get("entry_attempt") and not engine.orders(active=True)
    ):
        allowed.append(exit_side)
    return {
        "entry_signal": side,
        "previous_entry_signal": previous,
        "pending_signal": pending,
        "entry_ready": ready,
        "startup_baseline_pending": previous is None,
        "allowed_actions": allowed,
        "entry_blockers": blockers,
    }


def exit_reason(position, held, mark, now, view=None):
    if position.get("exit_reason"):
        return position["exit_reason"]
    if (held > 0 and mark <= dec(position["stop"])) or (held < 0 and mark >= dec(position["stop"])):
        return "HTF protective stop reached"
    if now >= position["deadline"]:
        return "HTF holding deadline reached"
    target = position.get("target")
    if target and ((held > 0 and mark >= dec(target)) or (held < 0 and mark <= dec(target))):
        return "HTF pullback target reached"
    if view and ((held > 0 and view["exit_eligible"]) or (held < 0 and view["trend"] == "rising")):
        return "HTF trend reversal"
    return None


def report(engine, pair, action, reason, data):
    decision = {
        "action": action,
        "confidence": None,
        "probabilities": {},
        "deterministic": True,
        "reason": reason,
        "strategy": "htf",
        "pair": pair.id,
        "mode": engine.mode,
        "model": "HTF rules",
        "latency_ms": 0,
        "ts": engine.clock(),
        "state": {
            "strategy": "htf",
            "product": engine.settings["product"],
            "symbol": pair.symbol,
            "inventory": str(quantity(engine, pair)),
            **data,
        },
    }
    if data.get("jev"):
        assessment = data["jev"]
        decision.update(
            {
                k: assessment[k]
                for k in ("confidence", "probabilities", "model", "latency_ms", "usage")
                if k in assessment
            }
        )
        decision.update(
            deterministic=assessment.get("deterministic", False), model_action=assessment["action"]
        )
    engine.latest_decision = decision
    engine.event("decision", decision)


async def trim(engine, pair, state, book, data, mark):
    """Reduce toward the target, not zero; preserve the original protective plan."""
    held = quantity(engine, pair)
    side = "sell" if held > 0 else "buy"
    if engine.settings["product"] == "futures":
        mark = dec((await engine.futures.client.market(pair))["markPrice"])
    price = limit_price(book, side, engine.settings["slippage_bps"])
    cap, maximum = engine.limits()
    target = maximum * EXPOSURE_TARGET
    # Exit costs also shrink a reinvested target. Reserve their effect so a full
    # fill does not leave a fee-sized excess that triggers another minimum order.
    fraction = (
        target / dec(engine.equity)
        if engine.settings["reinvest_profits"] and dec(engine.equity) > 0
        else ZERO
    )
    cost_per_unit = abs(mark - price) + price * engine.fees.reserve(pair) / BPS
    reduction = mark - fraction * cost_per_unit
    needed = (dec(engine.exposure) - target) / reduction if reduction > 0 else abs(held)
    wanted = max(
        minimum_volume(pair, price),
        (needed / pair.lot).to_integral_value(rounding=ROUND_UP) * pair.lot,
    )
    volume = min(wanted, floor(min(abs(held), cap / price), pair.lot))
    if below_minimum(pair, volume, price):
        report(engine, pair, "hold", "HTF trim pending: order cap below market minimum", data)
        return
    pair.validate(volume, price)
    report(
        engine,
        pair,
        side,
        "HTF exposure trim toward 80% of cap; stop and deadline unchanged",
        {**data, "exposure_target": str(target), "exposure_cap": str(maximum)},
    )
    await engine.place(pair, side, volume, price, book, exit_only=True)
    if not quantity(engine, pair):
        state["position"] = None
        engine.store.put(key(engine), state)


async def run(engine):
    prepare(engine)
    settings = engine.settings
    pair = engine.resolve(settings["pair"])
    state = snapshot(engine)
    held = quantity(engine, pair)
    position = state["position"]
    if position and not held:
        state["position"] = position = None
        engine.store.put(key(engine), state)
    values, _ = await engine.valuation(False)
    # Valuation can liquidate derivative positions; never trade on the pre-valuation size.
    held = quantity(engine, pair)
    client = engine.futures.client if settings["product"] == "futures" else engine.kraken
    book = await client.book(pair)
    book.fresh(settings["stale_seconds"])
    fee = engine.fees.reserve(pair)
    data = {
        "mid": str(book.mid),
        "spread_bps": str(book.spread_bps),
        "taker_fee_bps": str(fee),
        "candle_minutes": settings["candle_minutes"],
        "history_policy": engine.htf_review.policy,
        "position": position,
    }
    mark = book.bids[0][0] if held > 0 else book.asks[0][0]
    exit_side = "sell" if held > 0 else "buy"
    exit_price = limit_price(book, exit_side, settings["slippage_bps"])
    residual = bool(
        position and held and below_minimum(pair, floor(abs(held), pair.lot), exit_price)
    )
    data["residual_quantity"] = str(held) if residual else None
    reason = exit_reason(position, held, mark, engine.clock()) if position and held else None
    if position and held and not reason and -dec(engine.daily_pnl) >= dec(settings["daily_loss"]):
        reason = "HTF daily loss limit reached"
    if reason:
        position["exit_reason"] = reason
        position.pop("trim_pending", None)
        engine.store.put(key(engine), state)
    elif position and held:
        maximum = engine.limits()[1]
        if dec(engine.exposure) > maximum and not position.get("trim_pending"):
            position["trim_pending"] = True
            engine.store.put(key(engine), state)
        elif position.get("trim_pending") and dec(engine.exposure) <= maximum * EXPOSURE_TARGET:
            position.pop("trim_pending")
            engine.store.put(key(engine), state)
            report(
                engine,
                pair,
                "hold",
                "HTF exposure trim complete; stop and deadline unchanged",
                data,
            )
    trimming = bool(position and position.get("trim_pending"))
    # Stop, deadline and daily-loss exits do not need candles. Dust stays owned
    # and may join the next eligible entry; never buy solely to dispose of it.
    side, assessment, entry_ready, approved = "hold", None, False, False
    if not reason or residual:
        rows = await engine.htf_review.refresh(pair, data)
        if rows is None:
            if trimming and not residual:
                marked_price = values[pair.base] if settings["product"] == "spot" else mark
                await trim(engine, pair, state, book, data, marked_price)
            else:
                status = engine.htf_review.snapshot()["status"]
                if (engine.latest_decision or {}).get("reason") != status:
                    report(engine, pair, "hold", status, data)
            return
        # Refresh execution costs, never trust the model's earlier quote for orders.
        book = await client.book(pair)
        book.fresh(settings["stale_seconds"])
        view = engine.htf_review.entry_view(rows, pair, book)
        context = entry_context(engine, pair, view, book)
        side = context["entry_signal"]
        previous_signal = context["previous_entry_signal"]
        state["pending_signal"] = context["pending_signal"]
        entry_ready = context["entry_ready"]
        state.update(
            last_candle=f"{settings['candle_minutes']}:{view['candle_close_time']}",
            entry_signal=side,
        )
        engine.store.put(key(engine), state)
        data.update(view)
        data.update(
            mid=str(book.mid),
            spread_bps=str(book.spread_bps),
            **context,
            review=engine.htf_review.snapshot(),
        )
        mark = book.bids[0][0] if held > 0 else book.asks[0][0]
        exit_price = limit_price(book, exit_side, settings["slippage_bps"])
        residual = bool(
            position and held and below_minimum(pair, floor(abs(held), pair.lot), exit_price)
        )
        data["residual_quantity"] = str(held) if residual else None
        assessment = engine.htf_review.take()
        if assessment:
            data["jev"] = assessment
            approved = (
                dec(assessment.get("confidence") or 0) >= dec(settings["min_confidence"])
                and assessment["action"] in context["allowed_actions"]
                and assessment["action"]
                in assessment.get("allowed_actions", context["allowed_actions"])
            )
        if position and held and not residual:
            reason = exit_reason(position, held, mark, engine.clock(), view)
            if not reason and approved and assessment["action"] == exit_side:
                reason = "HTF Jev discretionary exit"
                data["review_required"] = True
            if not reason and not trimming:
                if assessment or previous_signal is None:
                    report(engine, pair, "hold", "HTF holding; stop and deadline active", data)
                return
    else:
        engine.htf_review.cancel()
    if reason and not residual:
        previous_plan = dict(position)
        position["exit_reason"] = reason
        position.pop("trim_pending", None)
        engine.store.put(key(engine), state)
        side, price = exit_side, exit_price
        volume = floor(min(abs(held), engine.limits()[0] / price), pair.lot)
        if below_minimum(pair, volume, price):
            report(engine, pair, "hold", "HTF exit pending: order cap below market minimum", data)
            return
        # When the cap requires several exits, leave a tradeable next tranche if
        # possible. Partial exchange fills can still leave dust, which stays owned.
        minimum = minimum_volume(pair, price)
        remainder = floor(abs(held) - volume, pair.lot)
        smaller = floor(abs(held) - minimum, pair.lot)
        if ZERO < remainder < minimum and smaller >= minimum:
            volume = min(volume, smaller)
        pair.validate(volume, price)
        report(engine, pair, side, reason + "; bounded reduction, residuals may remain", data)
        try:
            await engine.place(
                pair,
                side,
                volume,
                price,
                book,
                exit_only=True,
                review=assessment if data.get("review_required") else None,
            )
        except ExpiredReview as exc:
            state["position"] = previous_plan
            engine.store.put(key(engine), state)
            report(engine, pair, "hold", str(exc), data)
            raise
        if not quantity(engine, pair):
            state["position"] = None
            engine.store.put(key(engine), state)
        return
    if trimming and not residual:
        marked_price = values[pair.base] if settings["product"] == "spot" else mark
        await trim(engine, pair, state, book, data, marked_price)
        return
    if residual and side != ("buy" if held > 0 else "sell"):
        report(
            engine,
            pair,
            "hold",
            "HTF residual below market minimum; waiting for an eligible same-direction entry",
            data,
        )
        return
    if assessment is None:
        return
    if side == "hold":
        report(
            engine,
            pair,
            "hold",
            "HTF waiting: trend, pullback recovery or net target room not satisfied"
            if view.get("entry_execution") in {"post-only", "passive-limit"}
            else "HTF waiting: trend and historical momentum/cost screen not satisfied",
            data,
        )
        return
    if book.spread_bps > dec(settings["max_spread_bps"]):
        report(engine, pair, "hold", "HTF spread exceeds entry limit", data)
        return
    await engine.valuation(True)
    if not entry_ready:
        report(
            engine,
            pair,
            "hold",
            (
                "HTF startup baseline saved; waiting for a fresh entry signal"
                if previous_signal is None
                else "HTF waiting for a fresh entry signal; current signal already active"
            )
            + ("; residual retained" if residual else ""),
            data,
        )
        return
    if not approved or assessment["action"] != side:
        report(
            engine,
            pair,
            "hold",
            "HTF Jev did not approve the eligible entry; fresh setup remains pending",
            data,
        )
        return
    maker = view.get("entry_execution") in {"post-only", "passive-limit"}
    price = (
        dec(view["entry_prices"][side])
        if maker
        else limit_price(book, side, settings["slippage_bps"])
    )
    fee = engine.fees.reserve(pair, maker)
    cap, maximum = engine.limits()
    target = maximum * EXPOSURE_TARGET
    available = max(ZERO, target - dec(engine.exposure))
    entry_mark = book.bids[0][0] if side == "buy" else book.asks[0][0]
    if settings["product"] == "futures":
        entry_mark = dec((await client.market(pair))["markPrice"])
    # Shorts and Futures can be marked above the sell limit. Bound marked
    # exposure as well as order notional, even when reinvestment is disabled.
    divisor = max(dec(1), entry_mark / price)
    if settings["reinvest_profits"] and dec(engine.equity) > 0:
        # Entry fees and crossing the spread reduce equity and therefore the
        # scaled exposure target. Reserve that headroom before sizing the entry.
        entry_fee = fee + (
            dec(settings["margin_open_fee_bps"]) if settings["product"] == "margin" else ZERO
        )
        loss_rate = entry_fee / BPS + abs(price - entry_mark) / price
        divisor += target / dec(engine.equity) * loss_rate
    budget = min(cap, available / divisor)
    if settings["product"] == "spot":
        budget = min(budget, engine.balance(pair.quote) / (1 + fee / BPS))
    elif settings["product"] == "futures":
        values, exposure = await engine.futures.valuation(True)
        im, _ = engine.futures.margin_rates(pair, exposure + budget)
        budget = min(
            budget,
            max(ZERO, values["free"] - dec(engine.futures.ledger()["initial"]) * dec("0.2"))
            / (im + 2 * fee / BPS),
        )
    else:
        values, _ = await engine.margin_valuation(True)
        budget = min(
            budget,
            max(ZERO, values["equity"] - values["used_margin"])
            / (dec(1) / settings["leverage"] + (fee + dec(settings["margin_open_fee_bps"])) / BPS),
        )
    volume = floor(max(ZERO, budget) / price, pair.lot)
    if below_minimum(pair, volume, price):
        report(engine, pair, "hold", "HTF allocation below market minimum", data)
        return
    # Consume the setup only on an actual submission attempt, including unfilled orders.
    state["pending_signal"] = None
    data["review_required"] = True
    now = engine.clock()
    previous = position
    position = {
        # Keep the fill lineage when carrying dust so ownership, history guards
        # and the eventual exit include every retained unit, including after restart.
        "id": previous["id"] if residual else str(uuid.uuid4()),
        "side": side,
        "entry_limit": str(price),
        "stop": str(
            price * (1 + (-1 if side == "buy" else 1) * dec(settings["htf_stop_bps"]) / BPS)
        ),
        "opened_at": now,
        "deadline": now + settings["htf_max_hold_seconds"],
        "exit_reason": None,
    }
    if residual and "inventory_adjustment" in previous:
        position["inventory_adjustment"] = previous["inventory_adjustment"]
    if maker:
        position["target"] = view["entry_targets"][side]
        position["entry_policy"] = view["entry_policy"]
        state["entry_attempt"] = {"position": position, "order_id": None}
        # Keep the old plan until a passive fill is actually confirmed.
    else:
        state["position"] = position
    engine.store.put(key(engine), state)
    report(
        engine,
        pair,
        side,
        "HTF pullback and net target room passed; Jev approved; passive entry, no taker fallback"
        if maker
        else "Offline HTF rule benchmark momentum screen passed; not the deployed pullback policy",
        {
            **data,
            "position": position,
            "exposure_target": str(target),
            "exposure_cap": str(maximum),
        },
    )
    try:
        await engine.place(pair, side, volume, price, book, maker=maker, review=assessment)
    except ExpiredReview as exc:
        if maker:
            # Hosted-paper preflight may have reconciled a fee debit on the retained plan.
            state = snapshot(engine)
        else:
            state["position"] = previous
        state.pop("entry_attempt", None)
        state["pending_signal"] = side
        engine.store.put(key(engine), state)
        report(engine, pair, "hold", str(exc), data)
        raise
    if maker:
        reconcile_entry(engine)
    elif quantity(engine, pair) == held:
        state["position"] = previous  # An unfilled entry must not replace dust's exit plan.
        engine.store.put(key(engine), state)
