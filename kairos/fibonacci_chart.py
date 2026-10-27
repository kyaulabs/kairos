"""Read-only projections of existing Fibonacci signal state, never trading inputs."""

from kairos.domain import SafetyError, dec
from kairos.meta_signals import FAMILIES, unpack
from kairos.research_signals import RULES

RATIOS = ("0", "0.236", "0.382", "0.5", "0.618", "0.786", "1", "1.618", "2.618", "3.618", "4.236")


def timestamp(value):
    if type(value) not in (int, float) or value < 0 or not dec(value).is_finite():
        raise ValueError("Invalid geometry timestamp")
    return value


def positive(value):
    value = dec(value)
    if value <= 0:
        raise ValueError("Invalid geometry price")
    return value


def geometry(facts, family, phase, observed, scope, position=None):
    low, high = positive(facts["swing_low"]), positive(facts["swing_high"])
    low_at, high_at = timestamp(facts["swing_low_at"]), timestamp(facts["swing_high_at"])
    known = timestamp(facts["swing_known_at"])
    if not low < high or not low_at < high_at <= known <= observed:
        raise ValueError("Unconfirmed Fibonacci geometry")
    if family is not None and family not in FAMILIES["fib-meta"]:
        raise ValueError("Unknown Fibonacci family")
    kind = "extension" if family == "fib-extension" else "retracement"
    width = high - low
    levels = []
    for ratio in RATIOS:
        price = low + dec(ratio) * width if kind == "extension" else high - dec(ratio) * width
        if price > 0:
            levels.append({"ratio": ratio, "price": str(price)})
    result = {
        "status": "available",
        "scope": scope,
        "kind": kind,
        "family": family,
        "phase": phase,
        "observed_at": observed,
        "known_at": known,
        "low": {"at": low_at, "price": str(low)},
        "high": {"at": high_at, "price": str(high)},
        "levels": levels,
        "trade": None,
    }
    if position:
        plan = position.get("risk_plan") or {}
        entry = position.get("entry_price") or plan.get("entry_price")
        stop, target = positive(position["stop"]), positive(position["target"])
        if stop >= target:
            raise ValueError("Invalid saved protection")
        result["trade"] = {
            "entry": str(positive(entry)) if entry is not None else None,
            "stop": str(stop),
            "target": str(target),
            "deadline": timestamp(position["deadline"]),
            "sealed": bool(position.get("protection_sealed")),
            "percentage_based": bool(position.get("percentages")),
            "exit_reason": position.get("exit_reason"),
        }
    return result


def view(e, state, signals):
    if e.settings["strategy"] != "fib-meta":
        return None
    scope = {
        "exchange": e.exchange,
        "mode": e.mode,
        "pair": e.settings["pair"],
        "strategy": "fib-meta",
        "context_minutes": e.settings["fib_context_minutes"],
        "structure_minutes": e.settings["meta_structure_minutes"],
        "confirmation_minutes": e.settings["fib_confirmation_minutes"],
    }
    waiting = {
        "status": "waiting",
        "scope": scope,
        "reason": "Waiting for a confirmed Fibonacci swing",
    }
    if (
        signals.get("frame") != scope["structure_minutes"]
        or signals.get("confirmation_minutes", 15) != scope["confirmation_minutes"]
        or signals.get("context_minutes") != scope["context_minutes"]
    ):
        signals = {}
    try:
        now = timestamp(e.clock())
        observed = (signals.get("context") or {}).get("last_now")
        position = state.get("position")
        if position:
            facts = position["signal"]
            observed = timestamp(
                observed
                if observed is not None
                else facts.get("confirmed_at", facts["swing_known_at"])
            )
            phase = (
                "entry-plan"
                if position.get("percentages") and not position.get("protection_sealed")
                else "owned"
            )
            result = geometry(
                facts, facts.get("selected_strategy"), phase, observed, scope, position
            )
        else:
            if observed is None:
                return waiting
            observed = timestamp(observed)
            selected = state.get("chart_setup")
            if selected and selected["closed_at"] == observed:
                result = geometry(
                    selected["facts"],
                    selected["facts"]["selected_strategy"],
                    "confirmed",
                    observed,
                    scope,
                )
            else:
                result = None
                for family in FAMILIES["fib-meta"]:
                    pending = unpack((signals.get("children", {}).get(family) or {}).get("pending"))
                    if pending and pending["armed_at"] <= observed <= pending["expires"]:
                        result = geometry(pending["facts"], family, "armed", observed, scope)
                        break
                if result is None:
                    parent = unpack((signals.get("parent") or {}).get("parent"))
                    if not parent:
                        return waiting
                    low_at, low, low_known, high_at, high, high_known = parent
                    known = max(low_known, high_known)
                    if (
                        observed
                        > high_known + RULES["swing_max_age_bars"] * scope["structure_minutes"] * 60
                    ):
                        return waiting
                    result = geometry(
                        {
                            "swing_low_at": low_at,
                            "swing_low": low,
                            "swing_high_at": high_at,
                            "swing_high": high,
                            "swing_known_at": known,
                        },
                        None,
                        "reference",
                        observed,
                        scope,
                    )
        if observed > now:
            raise ValueError("Future Fibonacci observation")
        result.update(
            running=bool(e.running),
            context_bullish=bool(
                ((signals.get("background") or {}).get("status") or {}).get("bullish")
            ),
        )
        return result
    except (KeyError, IndexError, TypeError, ValueError, ArithmeticError, SafetyError):
        # Invalid display metadata must neither invent anchors nor interrupt protection.
        return {
            **waiting,
            "status": "unavailable",
            "reason": "Stored Fibonacci geometry is unavailable",
        }
