"""Paper/demo cash execution of the frozen VWAP and Fibonacci metas.

Protection is bot-managed, only while running. No native attached stop is assumed.
"""

import asyncio
import uuid

from kairos.domain import BPS, ZERO, SafetyError, dec, floor
from kairos.market_data import CandleHistory
from kairos.meta_signals import FAMILIES, MetaSignals
from kairos.research_diagnostics import execution_estimate
from kairos.strategies import limit_price

MAX_HOLD = 1_209_600


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
    if signals.get("frame") != e.settings["meta_structure_minutes"]:
        signals = {}
    return {
        "position": state["position"],
        "entries_halted": state["entries_halted"],
        "structure_minutes": e.settings["meta_structure_minutes"],
        "confirmation_minutes": 15,
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


def guard_configuration(e, values):
    if e.settings["strategy"] not in FAMILIES:
        return
    if any(
        values[k] != e.settings[k]
        for k in ("strategy", "product", "pair", "meta_structure_minutes")
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
    state["baseline_after"] = int(e.clock()) // 900 * 900
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
        gross * dec(position["risk_plan"]["loss_per_gross_unit"])
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
            "structure_minutes": e.settings["meta_structure_minutes"],
            "confirmation_minutes": 15,
            "inventory": str(e.balance(pair.base)),
            "position": state["position"],
            "entries_halted": state["entries_halted"],
            **data,
        },
    }
    e.latest_decision = decision
    e.event("decision", decision)


def risk_plan(e, pair, book, setup):
    price = limit_price(book, "buy", e.settings["slippage_bps"])
    if not setup.stop < price < setup.target:
        raise SafetyError("Current entry is outside the fixed stop/target plan")
    fee = max(ZERO, e.fees.reserve(pair)) / BPS
    if fee >= 1:
        raise SafetyError("Invalid meta fee allowance")
    scenario = {"spread_bps": str(book.spread_bps), "slippage_bps": e.settings["slippage_bps"]}
    stop_exit = execution_estimate(pair, setup.stop, "sell", scenario)
    target_exit = execution_estimate(pair, setup.target, "sell", scenario)
    # Kraken paper charges quote fees. OKX may charge entry fees in base or quote:
    # reserve the worse documented case, without assuming a particular fill currency.
    losses = [price * (1 + fee) - stop_exit * (1 - fee)]
    rewards = [target_exit * (1 - fee) - price * (1 + fee)]
    if e.exchange == "okx-demo":
        losses.append(price - (1 - fee) * stop_exit * (1 - fee))
        rewards.append((1 - fee) * target_exit * (1 - fee) - price)
    loss, reward = max(losses), min(rewards)
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


async def history(e, state):
    cutoff = int(e.clock()) // 900 * 900
    if state.get("history_checked_at") == cutoff and not state.get("history_unavailable"):
        return None
    pair = e.resolve(e.settings["pair"])
    async with asyncio.timeout(8):
        rows = (
            await e.kraken.bars(pair, 15, count=299)
            if e.exchange == "okx-demo"
            else await e.kraken.candles(pair, 15)
        )
    validator = CandleHistory(pair, 15, 1)
    if any(not isinstance(r, (list, tuple)) or len(r) != 8 for r in rows):
        raise SafetyError("Malformed meta execution history")
    rows = [validator.validate(r, vwap_optional=e.exchange == "okx-demo") for r in rows]
    times = [r[0] for r in rows]
    if (
        not times
        or times != sorted(set(times))
        or any(t + 900 > cutoff for t in times)
        or times[-1] + 900 != cutoff
    ):
        raise SafetyError("Meta history requires ordered, current, completed native 15m bars")
    signals = (
        MetaSignals.restore(state["signals"])
        if state["signals"]
        else MetaSignals(e.settings["strategy"], e.settings["meta_structure_minutes"])
    )
    if signals.structure_minutes != e.settings["meta_structure_minutes"]:
        signals = MetaSignals(e.settings["strategy"], e.settings["meta_structure_minutes"])
    last = signals.context.last_now
    setup = None
    for row in rows:
        if last is None or row[0] + 900 > last:
            setup = signals.push(row, e.clock())
    if state.get("history_unavailable") or last is not None and cutoff - last > 900:
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
    try:
        volume, price, plan = risk_plan(e, pair, book, setup)
    except SafetyError as exc:
        report(e, "hold", "Selected setup rejected; no family fallback: " + str(exc), state)
        return
    state["position"] = {
        "id": uuid.uuid4().hex,
        "stop": str(setup.stop),
        "target": str(setup.target),
        "deadline": e.clock() + MAX_HOLD,
        "signal": setup.facts,
        "risk_plan": plan,
    }
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
