"""Paper/demo cash execution of deterministic VWAP and Fibonacci metas.

Protection is bot-managed, only while running. No native attached stop is assumed.
"""

import asyncio
import math
import uuid

from kairos.domain import BPS, CANDLE_INTERVALS, TERMINAL, ZERO, SafetyError, dec, floor
from kairos.market_data import CandleHistory
from kairos.meta_signals import FAMILIES, MetaSignals
from kairos.research_diagnostics import execution_estimate
from kairos.strategies import limit_price

MAX_HOLD = 1_209_600
FIB_FIELDS = (
    "fib_context_minutes",
    "fib_confirmation_minutes",
    "fib_max_hold_seconds",
    "fib_stop_pct",
    "fib_target_pct",
)


def configuration(e):
    fields = ("meta_structure_minutes",) + (
        FIB_FIELDS if e.settings["strategy"] == "fib-meta" else ()
    )
    return {k: e.settings[k] for k in fields}


def confirmation_minutes(e):
    return e.settings["fib_confirmation_minutes"] if e.settings["strategy"] == "fib-meta" else 15


def signals_for(e):
    return MetaSignals(
        e.settings["strategy"],
        e.settings["meta_structure_minutes"],
        **(
            {
                "confirmation_minutes": e.settings["fib_confirmation_minutes"],
                "context_minutes": e.settings["fib_context_minutes"],
            }
            if e.settings["strategy"] == "fib-meta"
            else {}
        ),
    )


def require_percentages(e):
    if e.settings["strategy"] == "fib-meta" and any(
        dec(e.settings[k]) <= 0 for k in ("fib_stop_pct", "fib_target_pct")
    ):
        raise SafetyError(
            "Set explicit positive Fibonacci stop-loss and take-profit percentages before arming"
        )


def context_admission(e, state):
    background = (state.get("signals") or {}).get("background") or {}
    status = background.get("status") or {}
    return bool(
        status.get("bullish")
        and status.get("closed_at") is not None
        and status["closed_at"]
        <= e.clock()
        < status["closed_at"] + e.settings["fib_context_minutes"] * 60
    )


def key(e, mode=None, strategy=None, pair=None):
    return (
        f"meta:{mode or e.mode}:{strategy or e.settings['strategy']}:{pair or e.settings['pair']}"
    )


def snapshot(e):
    return e.store.get(key(e)) or {
        "position": None,
        "epoch": None,
        "signals": None,
        "entries_halted": False,
    }


def view(e):
    state = snapshot(e)
    signals = state.get("signals") or {}
    if (
        signals.get("frame") != e.settings["meta_structure_minutes"]
        or signals.get("confirmation_minutes", 15) != confirmation_minutes(e)
        or signals.get("context_minutes")
        != (e.settings["fib_context_minutes"] if e.settings["strategy"] == "fib-meta" else None)
    ):
        signals = {}
    return {
        "configuration": configuration(e),
        "background_context": (signals.get("background") or {}).get("status"),
        "context_rejections": signals.get("context_rejections", {}),
        "position": state["position"],
        "entries_halted": state["entries_halted"],
        "structure_minutes": e.settings["meta_structure_minutes"],
        "confirmation_minutes": confirmation_minutes(e),
        "structure_observed_bars": signals.get("structure", {}).get("count", 0),
        "confirmation_observed_bars": next(iter(signals.get("children", {}).values()), {}).get(
            "count", 0
        ),
        "raw_confirmations": signals.get("raw_counts", {}),
        "selected_families": signals.get("selected_counts", {}),
        "history_unavailable": state.get("history_unavailable", False),
    }


def validate(e, values):
    if values["strategy"] not in FAMILIES:
        return
    if values["product"] != "spot" or not (
        e.exchange == "kraken"
        and e.mode == "dry-run"
        or e.exchange == "okx-demo"
        and e.mode == "paper"
    ):
        raise SafetyError("VWAP/Fibonacci metas support Kraken dry-run and OKX Demo cash spot only")


def owned(e, state, position=None, mode=None):
    return sum(
        (
            dec(o["filled"]) * (1 if o["side"] == "buy" else -1)
            + sum(
                (
                    dec(x["fee_signed"])
                    for x in o.get("executions", [])
                    if x["fee_currency"] == o["base"]
                ),
                ZERO,
            )
            for o in e.orders(mode or e.mode)
            if state.get("epoch")
            and o.get("meta_epoch") == state["epoch"]
            and (position is None or o.get("meta_id") == position["id"])
        ),
        ZERO,
    )


def prepare(e):
    validate(e, e.settings)
    pair, state = e.resolve(e.settings["pair"]), snapshot(e)
    if any(
        dec(q)
        for asset, q in e.ledger()["balances"].items()
        if asset not in {pair.base, pair.quote}
    ):
        raise SafetyError("A meta requires one cash portfolio without unrelated assets")
    if e.balance(pair.base) != owned(e, state):
        raise SafetyError("A meta cannot adopt unrelated inventory or missing ownership history")
    if not state["epoch"]:
        state["epoch"] = uuid.uuid4().hex
        e.store.put(key(e), state)
    seal_protection(e, state)


def guard_configuration(e, values):
    if e.settings["strategy"] not in FAMILIES:
        return
    if any(
        values[k] != e.settings[k]
        for k in ("strategy", "product", "pair", "meta_structure_minutes")
        + (
            ("fib_context_minutes", "fib_confirmation_minutes")
            if e.settings["strategy"] == "fib-meta"
            else ()
        )
    ):
        for mode in ("dry-run", "paper", "trading"):
            state = e.store.get(key(e, mode)) or {}
            if owned(e, state, mode=mode):
                raise SafetyError(
                    "Retained meta ownership prevents changing strategy, market or structure frame"
                )


def arm(e):
    prepare(e)
    state = snapshot(e)
    if not state["position"]:
        require_percentages(e)
    step = confirmation_minutes(e) * 60
    state["baseline_after"] = int(e.clock()) // step * step
    if state["signals"]:
        signals = MetaSignals.restore(state["signals"])
        signals.cancel()
        state["signals"] = signals.checkpoint()
    e.store.put(key(e), state)


def tag(e, order):
    if e.settings["strategy"] not in FAMILIES:
        return
    state = snapshot(e)
    position = state["position"]
    if not position:
        raise SafetyError("Meta order has no durable ownership/protection plan")
    plan = position["risk_plan"]
    volume, price = dec(order["volume"]), dec(order["price"])
    if order["maker"] or order["pair"] != e.settings["pair"] or volume * price > 1000:
        raise SafetyError("Meta intent exceeds its cash IOC market/order scope")
    if order["side"] == "buy":
        if e.settings["strategy"] == "fib-meta":
            require_percentages(e)
            if not position.get("percentages") or not context_admission(e, state):
                raise SafetyError(
                    "Fibonacci entry requires percentage protection and current bullish context"
                )
        duplicate = any(
            o.get("meta_id") == position["id"]
            and o.get("meta_epoch") == state["epoch"]
            and o["side"] == "buy"
            for o in e.orders(e.mode)
        )
        if (
            duplicate
            or volume != dec(plan["requested_quantity"])
            or price != dec(plan["entry_price"])
            or dec(order["fee_bps"]) > dec(plan["planning_fee_bps"])
        ):
            raise SafetyError("Meta entry changed or already consumed; no resubmission")
        if volume * dec(plan["loss_per_gross_unit"]) + dec(plan["dust_risk_reserved"]) + dec(
            plan["potential_unprotected_dust_cost"]
        ) > dec(plan["capital_budget"]):
            raise SafetyError("Meta entry exceeds its fixed planned-risk allowance")
    elif order["side"] != "sell" or not order["exit_only"]:
        raise SafetyError("Meta sales must reduce their tracked position")
    order.update(
        meta_epoch=state["epoch"],
        meta_id=position["id"],
        signal_facts=position["signal"],
        protection={k: position[k] for k in ("stop", "target", "deadline")},
        risk_plan=position["risk_plan"],
    )


def valid_exit(e, pair, side, volume):
    prepare(e)
    state = snapshot(e)
    return bool(
        state["position"]
        and pair.id == e.settings["pair"]
        and side == "sell"
        and ZERO < volume <= owned(e, state, state["position"])
    )


def observe_risk(e, state, pair, book):
    position = state["position"]
    orders = [
        o
        for o in e.orders(e.mode)
        if o.get("meta_id") == position["id"] and o.get("meta_epoch") == state["epoch"]
    ]
    spent = proceeds = gross = ZERO
    for order in orders:
        fee = dec(
            order.get("fees", {}).get(pair.quote, order.get("fee", 0) if "fees" not in order else 0)
        )
        if order["side"] == "buy":
            spent += dec(order["cost"]) + fee
            gross += dec(order["filled"])
        else:
            proceeds += dec(order["cost"]) - fee
    position["risk_plan"]["filled_stop_risk"] = str(
        gross
        * dec(
            position["risk_plan"].get(
                "filled_loss_per_gross_unit", position["risk_plan"]["loss_per_gross_unit"]
            )
        )
    )
    mark = (
        owned(e, state, position)
        * book.bids[0][0]
        * (1 - dec(e.settings["slippage_bps"]) / BPS)
        * (1 - max(ZERO, e.fees.reserve(pair)) / BPS)
    )
    loss = max(ZERO, spent - proceeds - mark)
    position["maximum_observed_loss"] = str(
        max(dec(position.get("maximum_observed_loss", 0)), loss)
    )
    allowance = dec(position["risk_plan"]["capital_budget"]) - dec(
        position["risk_plan"]["dust_risk_reserved"]
    )
    if loss > allowance and not position.get("risk_overrun_reported"):
        position["risk_overrun_reported"] = True
        e.event(
            "meta-risk-overrun",
            {
                "meta_id": position["id"],
                "loss": str(loss),
                "planned_allowance": str(allowance),
                "guaranteed_loss_cap": False,
            },
        )


def report(e, action, reason, state, **data):
    pair = e.resolve(e.settings["pair"])
    decision = {
        "action": action,
        "confidence": None,
        "probabilities": {},
        "deterministic": True,
        "reason": reason,
        "strategy": e.settings["strategy"],
        "pair": pair.id,
        "mode": e.mode,
        "model": "Fixed-priority meta rules",
        "latency_ms": 0,
        "ts": e.clock(),
        "state": {
            "strategy": e.settings["strategy"],
            "product": "spot",
            "symbol": pair.symbol,
            "configuration": configuration(e),
            "structure_minutes": e.settings["meta_structure_minutes"],
            "confirmation_minutes": confirmation_minutes(e),
            "inventory": str(e.balance(pair.base)),
            "position": state["position"],
            "entries_halted": state["entries_halted"],
            **data,
        },
    }
    e.latest_decision = decision
    e.event("decision", decision)


def economics(pair, price, stop, target, fee, scenario, base_fees):
    stop_exit = execution_estimate(pair, stop, "sell", scenario)
    target_exit = execution_estimate(pair, target, "sell", scenario)
    # Kraken paper charges quote fees. OKX may charge entry fees in base or quote:
    # reserve the worse documented case, without assuming a particular fill currency.
    losses = [price * (1 + fee) - stop_exit * (1 - fee)]
    rewards = [target_exit * (1 - fee) - price * (1 + fee)]
    if base_fees:
        losses.append(price - (1 - fee) * stop_exit * (1 - fee))
        rewards.append((1 - fee) * target_exit * (1 - fee) - price)
    return max(losses), min(rewards), stop_exit


def percentage_prices(price, percentages):
    return (
        price * (1 - dec(percentages["stop"]) / 100),
        price * (1 + dec(percentages["target"]) / 100),
    )


def seal_protection(e, state):
    position = state["position"]
    if not position or not position.get("percentages") or position.get("protection_sealed"):
        return
    buys = [
        o
        for o in e.orders()
        if o.get("meta_id") == position["id"]
        and o.get("meta_epoch") == state["epoch"]
        and o["side"] == "buy"
    ]
    if len(buys) > 1:
        raise SafetyError("Fibonacci position has multiple entry intents; no averaging or adoption")
    if not buys:
        # Every transport write requires a durable order first. No intent means
        # Stop or admission failed before submission, not an unknown live order.
        state["position"] = None
        e.store.put(key(e), state)
        return
    if buys[0]["status"] not in TERMINAL:
        return
    order, plan = buys[0], position["risk_plan"]
    gross, cost = dec(order["filled"]), dec(order["cost"])
    if gross == 0:
        if cost != 0:
            raise SafetyError("Zero-fill Fibonacci IOC reports nonzero cost")
        state["position"] = None
        e.store.put(key(e), state)
        return
    if not order.get("fee_reported", True):
        return
    price = cost / gross
    if (
        cost <= 0
        or gross < 0
        or gross > dec(plan["requested_quantity"])
        or price > dec(plan["entry_price"])
        or order["pair"] != e.settings["pair"]
        or dec(order["price"]) != dec(plan["entry_price"])
    ):
        raise SafetyError("Fibonacci fill differs from its frozen cash IOC limits")
    stop, target = percentage_prices(price, position["percentages"])
    loss, _, _ = economics(
        e.resolve(order["pair"]),
        price,
        stop,
        target,
        dec(plan["planning_fee_bps"]) / BPS,
        plan["execution_scenario"],
        e.exchange == "okx-demo",
    )
    position.update(
        stop=str(stop), target=str(target), entry_price=str(price), protection_sealed=True
    )
    plan["filled_loss_per_gross_unit"] = str(loss)
    plan["filled_stop_risk"] = str(gross * loss)
    e.store.put(key(e), state)


def risk_plan(e, pair, book, setup):
    price = limit_price(book, "buy", e.settings["slippage_bps"])
    stop, target = setup.stop, setup.target
    if e.settings["strategy"] == "fib-meta":
        require_percentages(e)
        stop, target = percentage_prices(
            price, {"stop": e.settings["fib_stop_pct"], "target": e.settings["fib_target_pct"]}
        )
    if not ZERO < stop < price < target:
        raise SafetyError("Current entry is outside the fixed stop/target plan")
    fee = max(ZERO, e.fees.reserve(pair)) / BPS
    if fee >= 1:
        raise SafetyError("Invalid meta fee allowance")
    scenario = {"spread_bps": str(book.spread_bps), "slippage_bps": e.settings["slippage_bps"]}
    loss, reward, stop_exit = economics(
        pair, price, stop, target, fee, scenario, e.exchange == "okx-demo"
    )
    if loss <= 0 or reward <= 0 or stop_exit <= 0:
        raise SafetyError("Nonpositive cost-adjusted target reward or invalid stop estimate")
    capital = max(ZERO, min(dec(e.ledger()["initial"]), dec(e.equity), dec(1000)))
    budget = capital / 100
    dust = e.balance(pair.base) * book.bids[0][0]
    potential_dust = (
        max(pair.minimum, pair.lot, pair.cost_minimum / stop_exit) * price * (1 + fee) / (1 - fee)
    )
    cap, maximum = (min(dec(1000), x) for x in e.limits())
    volume = floor(
        max(
            ZERO,
            min(
                (budget - dust - potential_dust) / loss,
                cap / price,
                (maximum - dec(e.exposure)) / price,
                e.balance(pair.quote) / (price * (1 + fee)),
            ),
        ),
        pair.lot,
    )
    pair.validate(volume, price)
    return (
        volume,
        price,
        {
            "entry_price": str(price),
            "stop": str(stop),
            "target": str(target),
            "execution_scenario": scenario,
            "requested_quantity": str(volume),
            "planning_fee_bps": str(fee * BPS),
            "capital_budget": str(budget),
            "percent": "1",
            "dust_risk_reserved": str(dust),
            "potential_unprotected_dust_cost": str(potential_dust),
            "loss_per_gross_unit": str(loss),
            "requested_stop_risk": str(volume * loss),
            "stop_execution_estimate": str(stop_exit),
            "net_target_reward_per_gross_unit": str(reward),
            "net_planned_R": str(reward / loss),
            "guaranteed_loss_cap": False,
        },
    )


async def native_rows(e, pair, minutes):
    rows = (
        await e.kraken.bars(pair, minutes, count=299)
        if e.exchange == "okx-demo"
        else await e.kraken.candles(pair, minutes)
    )
    validator = CandleHistory(pair, minutes, 1)
    if any(not isinstance(r, (list, tuple)) or len(r) != 8 for r in rows):
        raise SafetyError("Malformed meta execution history")
    rows = [validator.validate(r, vwap_optional=e.exchange == "okx-demo") for r in rows]
    times, step = [r[0] for r in rows], minutes * 60
    cutoff = int(e.clock()) // step * step
    if (
        not times
        or times != sorted(set(times))
        or any(t + step > cutoff for t in times)
        or times[-1] + step != cutoff
    ):
        raise SafetyError("Meta history requires ordered, current, completed native bars")
    return rows


async def history(e, state):
    step = confirmation_minutes(e) * 60
    cutoff = int(e.clock()) // step * step
    if state.get("history_checked_at") == cutoff and not state.get("history_unavailable"):
        return None
    pair = e.resolve(e.settings["pair"])
    desired = signals_for(e)
    signals = MetaSignals.restore(state["signals"]) if state["signals"] else desired
    if (signals.structure_minutes, signals.confirmation_minutes, signals.context_minutes) != (
        desired.structure_minutes,
        desired.confirmation_minutes,
        desired.context_minutes,
    ):
        signals = desired
    last = signals.context.last_now
    async with asyncio.timeout(8):
        rows = await native_rows(e, pair, confirmation_minutes(e))
        if last is None and signals.background:
            # Seed only history already known at the first replayed confirmation.
            # Starting on a shared boundary avoids inventing partial higher bars.
            boundary = math.lcm(signals.structure_minutes, signals.context_minutes) * 60
            start = (rows[0][0] + boundary - 1) // boundary * boundary
            rows = [r for r in rows if r[0] >= start]
            if not rows:
                raise SafetyError("Insufficient confirmation history for aligned Fibonacci clocks")
            higher_rows = {}
            for context in (signals.context, signals.background):
                if context.minutes in CANDLE_INTERVALS:
                    if context.minutes not in higher_rows:
                        higher_rows[context.minutes] = await native_rows(e, pair, context.minutes)
                    context.seed(higher_rows[context.minutes], start)
    setup = None
    for row in rows:
        if last is None or row[0] + step > last:
            setup = signals.push(row, e.clock())
    if state.get("history_unavailable") or last is not None and cutoff - last > step:
        state["baseline_after"] = cutoff
        signals.cancel()
        setup = None
    state.update(signals=signals.checkpoint(), history_checked_at=cutoff, history_unavailable=False)
    return setup


async def run(e):
    prepare(e)
    pair, state = e.resolve(e.settings["pair"]), snapshot(e)
    await e.valuation(False)
    if -dec(e.daily_pnl) >= min(dec(e.settings["daily_loss"]), dec("12.50")):
        state["entries_halted"] = True
    book = await e.kraken.book(pair)
    book.fresh(e.settings["stale_seconds"])
    position = state["position"]
    exited = False
    if position and position.get("percentages") and not position.get("protection_sealed"):
        report(e, "hold", "Awaiting terminal native entry fills; no duplicate buy", state)
        return
    if position:
        observe_risk(e, state, pair, book)
        held = owned(e, state, position)
        if held < 0:
            raise SafetyError("Meta owned quantity is negative")
        volume = floor(held, pair.lot)
        if volume < pair.minimum or volume <= 0:
            e.event(
                "meta-position-retired",
                {
                    "meta_id": position["id"],
                    "retained_quantity": str(held),
                    "maximum_observed_loss": position["maximum_observed_loss"],
                    "risk_plan": position["risk_plan"],
                },
            )
            state["position"] = None  # Residual stays owned, marked and risk-reserved.
        else:
            reason = position.get("exit_reason") or (
                "Stop"
                if book.mid <= dec(position["stop"])
                else "Deadline"
                if e.clock() >= position["deadline"]
                else "Target"
                if book.mid >= dec(position["target"])
                else None
            )
            if reason:
                position["exit_reason"] = reason
                e.store.put(key(e), state)
                price = limit_price(book, "sell", e.settings["slippage_bps"])
                cap = min(dec(1000), e.limits()[0])
                volume = floor(min(held, cap / price), pair.lot)
                try:
                    pair.validate(volume, price)
                except SafetyError as exc:
                    report(
                        e,
                        "hold",
                        "Protective reduction below market/cap minimum: " + str(exc),
                        state,
                    )
                else:
                    report(e, "sell", "Latched meta exit: " + reason, state)
                    await e.place(pair, "sell", volume, price, book, exit_only=True)
                    observe_risk(e, state, pair, book)
                exited = True
    # Every child advances regardless of ownership or the entry latch. A failed
    # history read only blocks entries; existing protection was checked first.
    try:
        setup = await history(e, state)
    except (SafetyError, TimeoutError) as exc:
        state["history_unavailable"] = True
        e.store.put(key(e), state)
        report(e, "hold", "Waiting for meta history; no entry: " + str(exc), state)
        return
    e.store.put(key(e), state)  # Consume this confirmation before any order I/O.
    if exited or state["position"] or state["entries_halted"] or setup is None:
        if not exited:
            report(
                e,
                "hold",
                "Entry loss latch active"
                if state["entries_halted"]
                else "Managing owned meta plan"
                if state["position"]
                else "Waiting for a fresh confirmed meta setup",
                state,
            )
        return
    if (
        setup.closed_at <= state.get("baseline_after", setup.closed_at)
        or not 0 <= e.clock() - setup.closed_at < 60
    ):
        report(e, "hold", "Startup/recovery baseline or missed first execution minute", state)
        return
    if e.settings["strategy"] == "fib-meta" and not context_admission(e, state):
        report(e, "hold", "Fibonacci requires current bullish background context", state)
        return
    try:
        volume, price, plan = risk_plan(e, pair, book, setup)
    except SafetyError as exc:
        report(e, "hold", "Selected setup rejected; no family fallback: " + str(exc), state)
        return
    state["position"] = {
        "id": uuid.uuid4().hex,
        "stop": plan["stop"],
        "target": plan["target"],
        "deadline": e.clock()
        + (
            e.settings["fib_max_hold_seconds"] if e.settings["strategy"] == "fib-meta" else MAX_HOLD
        ),
        "signal": setup.facts,
        "risk_plan": plan,
    }
    if e.settings["strategy"] == "fib-meta":
        state["position"].update(
            percentages={
                "stop": e.settings["fib_stop_pct"],
                "target": e.settings["fib_target_pct"],
            },
            protection_sealed=False,
        )
    e.store.put(key(e), state)
    report(e, "buy", "Fixed-priority, cost-qualified meta setup", state)
    await e.place(
        pair,
        "buy",
        volume,
        price,
        book,
        review={"observed_at": e.clock(), "expires_at": setup.closed_at + 60},
    )
    seal_protection(e, snapshot(e))
