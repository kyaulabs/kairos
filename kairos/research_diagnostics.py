"""Offline cost/timing evidence. These calculations never grant entry permission."""

from collections import Counter
from datetime import UTC, datetime
from statistics import median

from kairos.domain import BPS, ZERO, Book, dec, floor
from kairos.strategies import limit_price


def clock_facts(timestamp):
    value = datetime.fromtimestamp(timestamp, UTC)
    return {"hour_UTC": value.hour, "weekday_UTC": value.weekday(), "minute_of_hour": value.minute}


def execution_estimate(pair, mid, side, scenario):
    """The same synthetic quote and tick-rounded IOC bound as HistoricalMarket."""
    half = dec(scenario["spread_bps"]) / (2 * BPS)
    book = Book(
        pair,
        [(pair.price(mid * (1 - half), "buy"), ZERO)],
        [(pair.price(mid * (1 + half), "sell"), ZERO)],
        0,
    )
    return limit_price(book, side, scenario["slippage_bps"])


def entry_economics(pair, entry, stop, target, scenario, fee_bps, *, gross=None, owned=None):
    """Illustrative all-sold unit, plus lot-rounded actual-fill projection when supplied.

    No probability/expectancy is implied. Unexecutable residual BTC stays owned;
    a below-minimum projection is unavailable, never an invented full liquidation.
    """
    fee = dec(fee_bps) / BPS
    stop_exit = execution_estimate(pair, stop, "sell", scenario)
    target_exit = execution_estimate(pair, target, "sell", scenario)
    factor = (1 - fee) ** 2
    loss, reward = entry - factor * stop_exit, factor * target_exit - entry
    room = target - entry
    result = {
        "entry_price": str(entry),
        "stop": str(stop),
        "target": str(target),
        "stop_exit_estimate": str(stop_exit),
        "target_exit_estimate": str(target_exit),
        "gross_target_room_per_gross_unit": str(room),
        "net_target_reward_per_gross_unit": str(reward),
        "net_stop_loss_per_gross_unit": str(loss),
        "net_planned_R": str(reward / loss) if loss > 0 else None,
        "target_cost_per_gross_unit": str(room - reward),
        "cost_share_of_gross_target_room": str((room - reward) / room) if room > 0 else None,
        "break_even_entry_fill_price": str(factor * target_exit),
        "definition": "Received-BTC buy fee and quote sell fee; illustrative all-sold unit, not expected profit.",
    }
    if gross is not None and owned is not None:
        rounded = floor(owned, pair.lot)
        executable = rounded >= pair.minimum and rounded * target_exit >= pair.cost_minimum
        sold = rounded if executable else ZERO
        basis = gross * entry * sold / owned if owned else ZERO
        result["filled_projection"] = {
            "gross_bought": str(gross),
            "net_owned": str(owned),
            "lot_rounded_exit_quantity": str(rounded),
            "lot_rounded_sold": str(sold),
            "retained_quantity": str(owned - sold),
            "retained_cost_basis": str(gross * entry - basis),
            "meets_target_exit_minimum": executable,
            "sold_share_cost_basis": str(basis),
            "net_target_pnl": str(sold * target_exit * (1 - fee) - basis) if executable else None,
            "net_stop_loss": str(basis - sold * stop_exit * (1 - fee))
            if sold >= pair.minimum and sold * stop_exit >= pair.cost_minimum
            else None,
        }
    return result


def exit_economics(position, orders, pair, scenario, fee_bps):
    """Reconcile sold-share P&L and show liquidation after the first latched trigger."""
    sells = [o for o in orders if o["side"] == "sell" and o["position_id"] == position["id"]]
    filled = [o for o in sells if dec(o["filled"])]
    sold, cost, fees = (
        sum((dec(o[field]) for o in sells), ZERO) for field in ("filled", "cost", "fee")
    )
    basis = position["entry_cost"] * sold / position["owned"]
    target = position["target"]
    target_exit = execution_estimate(pair, target, "sell", scenario)
    projected = sold * target_exit * (1 - dec(fee_bps) / BPS) - basis
    pnl = cost - fees - basis
    observed_mid_value = sum(
        (dec(o["filled"]) * dec(o["execution_observation"]["mid"]) for o in sells), ZERO
    )
    observed_bid_value = sum(
        (dec(o["filled"]) * dec(o["execution_observation"]["bid"]) for o in sells), ZERO
    )
    trigger = position.get("exit_trigger")
    return {
        "first_trigger": trigger,
        "stop_crossed_after_target_at": position.get("stop_after_target_at"),
        "IOC_attempts": len(sells),
        "zero_fill_attempts": len(sells) - len(filled),
        "partial_fill_attempts": sum(dec(o["filled"]) < dec(o["volume"]) for o in filled),
        "first_fill_at": filled[0]["created"] if filled else None,
        "last_fill_at": filled[-1]["created"] if filled else None,
        "first_fill_delay_seconds": filled[0]["created"] - trigger["observed_at"]
        if filled and trigger
        else None,
        "last_fill_delay_seconds": filled[-1]["created"] - trigger["observed_at"]
        if filled and trigger
        else None,
        "net_owned": str(position["owned"]),
        "sold_quantity": str(sold),
        "retained_quantity": str(position["quantity"]),
        "sold_share_cost_basis": str(basis),
        "retained_cost_basis": str(position["entry_cost"] - basis),
        "average_exit_fill_price": str(cost / sold) if sold else None,
        "gross_target_pnl_before_sell_costs": str(sold * target - basis),
        "exit_price_shortfall_from_target": str(sold * target - cost),
        "target_to_observed_mid_shortfall": str(sold * target - observed_mid_value),
        "exit_spread_and_quote_tick_cost": str(observed_mid_value - observed_bid_value),
        "exit_slippage_and_bound_tick_cost": str(observed_bid_value - cost),
        "sell_quote_fees": str(fees),
        "modeled_at_target_net_pnl_on_sold_share": str(projected),
        "execution_delta_from_modeled_target": str(pnl - projected),
        "net_sold_pnl": str(pnl),
        "remainder_below_quantity_minimum": position["quantity"] < pair.minimum,
    }


def economics_summary(candidates, exits):
    filled = [c for c in candidates if c.get("outcome") == "filled"]
    plans = [c["entry_economics"] for c in filled]
    targets = [e for e in exits if e["first_trigger"] and e["first_trigger"]["reason"] == "Target"]
    closed_targets = [e for e in targets if e["episode_retired"] and dec(e["sold_quantity"])]
    losing = [e for e in closed_targets if dec(e["net_sold_pnl"]) < 0]
    ratios = [dec(p["net_planned_R"]) for p in plans if p["net_planned_R"] is not None]
    return {
        "candidate_outcomes": dict(Counter(c["outcome"] for c in candidates)),
        "filled_plans": len(plans),
        "nonpositive_target_reward": sum(
            dec(p["net_target_reward_per_gross_unit"]) <= 0 for p in plans
        ),
        "below_one_net_R": sum(r < 1 for r in ratios),
        "median_net_planned_R": str(median(ratios)) if ratios else None,
        "closed_target_episodes": len(closed_targets),
        "losing_target_episodes": len(losing),
        "losing_targets_cost_negative_at_entry": sum(e["cost_negative_at_entry"] for e in losing),
        "losing_targets_negative_on_actual_sold_share_at_target": sum(
            dec(e["modeled_at_target_net_pnl_on_sold_share"]) <= 0 for e in losing
        ),
        "losing_targets_with_multiple_IOCs": sum(e["IOC_attempts"] > 1 for e in losing),
        "losing_targets_crossed_stop": sum(
            e["stop_crossed_after_target_at"] is not None for e in losing
        ),
        "target_episodes_still_open": sum(not e["episode_retired"] for e in targets),
        "note": "Attribution counts overlap. First trigger is latched; Target is not a profitable-exit label.",
    }
