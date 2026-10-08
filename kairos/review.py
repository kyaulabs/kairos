"""Read-only reporting from existing snapshots; no requests, signals or state writes."""

from kairos.domain import BPS, ZERO, dec
from kairos.htf_review import NATIVE_POLICY
from kairos.programs import fill_cost, order_totals


def snapshot(state, now):
    settings = state["settings"]
    strategy, product = settings["strategy"], settings["product"]
    run = state["execution_run"]
    orders = [
        o
        for o in state["orders"] + state["archived_orders"]
        if o["mode"] == state["mode"] and o.get("product", "spot") == product
    ]
    decision = state["decision"]
    if not (
        decision
        and decision.get("strategy") == strategy
        and decision.get("pair") == settings["pair"]
        and decision.get("mode") == state["mode"]
        and decision.get("state", {}).get("product", "spot") == product
    ):
        decision = None
    inputs = (decision or {}).get("state", {})
    observed = (decision or {}).get("ts")
    age = now - observed if observed is not None else None
    review = state.get("htf_review") or {}
    cutoff = review.get("window_end")
    native = state.get("exchange") == "alpaca" and review.get("history_policy") == NATIVE_POLICY
    required = 30 * settings["candle_minutes"] if strategy == "htf" and not native else None
    step = settings["candle_minutes"] * 60 if native else 60
    # A stopped/canceled review can retain old counts. Do not call these current data.
    current_window = bool(
        strategy == "htf"
        and state["running"]
        and cutoff == int(now) // step * step
        and (
            review.get("required_native_bars") == 30
            and review.get("bar_minutes") == settings["candle_minutes"]
            if native
            else review.get("required_minute_rows") == required
        )
    )
    available = review.get("minute_rows") if current_window and not native else None
    native_available = review.get("native_bars") if current_window and native else None
    holdings = (
        {
            asset: quantity
            for asset, quantity in (state.get("ledger") or {}).get("balances", {}).items()
            if asset != settings["quote"] and dec(quantity)
        }
        if product == "spot"
        else {
            pair: row["quantity"]
            for pair, row in (state.get(product) or {}).get("positions", {}).items()
            if dec(row["quantity"])
        }
    )
    position = (state.get(strategy) or {}).get("position") if strategy in {"htf", "scalp"} else None
    stop_distance = None
    if position and dec(position["entry_limit"]) > 0:
        stop_distance = str(
            abs(dec(position["entry_limit"]) - dec(position["stop"]))
            / dec(position["entry_limit"])
            * BPS
        )
    program = state.get("program")
    execution = None
    if program:
        children = [o for o in orders if o.get("program_id") == program["id"]]
        config = program["config"]  # Saved program terms, never unsaved/new settings.
        spent = sum((fill_cost(o) for o in children), ZERO)
        execution = {
            "id": program["id"],
            "status": program["status"],
            "configuration_changed": program["configuration_changed"],
            "claimed_slots": program["next_slot"],
            "missed_slots": program["skipped_slots"],
            "message": program["message"],
            "next_at": program["next_at"],
            "orders": order_totals(children),
            "execution": program.get("execution"),
            "turnover_with_fee_reserve": str(spent),
            "markets": {},
        }
        for pair in sorted({o["pair"] for o in children}):
            execution["markets"][pair] = {
                side: {
                    field: str(
                        sum(
                            (
                                dec(o[field])
                                for o in children
                                if o["pair"] == pair and o["side"] == side
                            ),
                            ZERO,
                        )
                    )
                    for field in ("volume", "filled")
                }
                for side in ("buy", "sell")
            }
        if strategy == "dca" and product == "spot":
            budget = dec(config["dca_amount"]) * config["dca_count"]
            execution.update(budget=str(budget), unspent_allowance=str(max(ZERO, budget - spent)))
        elif strategy == "twap":
            filled = sum((dec(o["filled"]) for o in children), ZERO)
            parent = dec(config["twap_quantity"])
            execution.update(
                parent_quantity=str(parent), unfilled_quantity=str(max(ZERO, parent - filled))
            )
    return {
        "generated_at": now,
        "run_id": run["id"] if run else None,
        "orders": order_totals([o for o in orders if run and o.get("run_id") == run["id"]]),
        "data": {
            "history_policy": review.get("history_policy"),
            "bar_minutes": settings["candle_minutes"] if native else None,
            "required_native_bars": 30 if strategy == "htf" and native else None,
            "consecutive_native_bars": native_available,
            "native_bar_shortfall": 30 - native_available if native_available is not None else None,
            "history_fetched_at": review.get("history_fetched_at") if current_window else None,
            "history_revision": review.get("history_revision") if current_window else None,
            "required_minutes": required,
            "consecutive_minutes": available,
            "consecutive_shortfall": required - available if available is not None else None,
            "window_end": cutoff,
            "window_current": current_window,
            "assessment_at": observed,
            "assessment_age_seconds": age,
            "assessment_current": bool(
                state["running"]
                and age is not None
                and 0 <= age <= 2 * settings["interval_seconds"]
            ),
            "status": review.get("status") if strategy == "htf" else None,
        },
        "entry": {
            key: inputs.get(key)
            for key in ("entry_signal", "entry_ready", "entry_blockers", "allowed_actions")
        },
        "costs": {
            key: inputs.get(key)
            for key in (
                "spread_bps",
                "maker_fee_bps",
                "taker_fee_bps",
                "round_trip_cost_bps",
                "target_net_room_bps",
                "required_net_room_bps",
                "net_room_bps",
            )
        },
        "position": position,
        "stop_distance_bps": stop_distance,
        "holdings": holdings,
        "working_orders": order_totals(orders)["working"],
        "program": execution,
    }
