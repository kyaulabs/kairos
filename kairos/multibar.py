"""Frozen multibar-v2 experiment. Structural candidates are not order permissions."""

import hashlib
from pathlib import Path

from kairos.domain import BPS, dec
from kairos.htf_policy import signal as legacy_signal
from kairos.htf_policy import target_room

POLICY = "multibar-v2"
PROTOCOL_HASH = hashlib.sha256(Path(__file__).with_name("trial.json").read_bytes()).hexdigest()


def signal(rows, settings, book, maker_fee, taker_fee):
    rows = rows[-30:]
    view = legacy_signal(rows, settings, book, maker_fee, taker_fee)
    view["legacy_control"] = {
        k: view[k]
        for k in ("entry_policy", "pullback_long", "entry_eligible", "target_net_room_bps")
    }
    closes = [dec(r[4]) for r in rows[-30:]]
    highs, lows = [dec(r[2]) for r in rows[-30:]], [dec(r[3]) for r in rows[-30:]]
    atr, fast, slow = dec(view["atr"]), dec(view["fast_average"]), dec(view["slow_average"])
    prior_slow = dec(view["previous_slow_average"])
    pullback = None
    values = {}
    for i in (28, 27, 26):
        prior_fast = sum(closes[i - 7 : i + 1]) / 8
        high = max(highs[i - 8 : i])
        if (
            atr > 0
            and closes[i] < closes[i - 1]
            and lows[i] <= prior_fast
            and dec(".5") * atr <= high - lows[i] <= 2 * atr
        ):
            pullback = {
                "id": f"{POLICY}:{rows[i][0]}",
                "pullback_at": rows[i][0],
                "target": str(high),
                "lower": str(closes[i] + dec(".1") * atr),
                "upper": str(fast + dec(".5") * atr),
                "atr": str(atr),
            }
            values = {
                "pullback_at": rows[i][0],
                "pullback_low": str(lows[i]),
                "prior_fast": str(prior_fast),
                "retracement": str(high - lows[i]),
                "recovery_floor": pullback["lower"],
                "chase_ceiling": pullback["upper"],
            }
            break
    gates = {
        "rising_trend": fast > slow > prior_slow,
        "pullback_in_last_three": pullback is not None,
        "closed_recovery": bool(pullback and closes[-1] >= dec(pullback["lower"])),
        "closed_no_chase": bool(pullback and closes[-1] <= dec(pullback["upper"])),
    }
    setup = all(gates.values())
    view.update(
        entry_policy=POLICY,
        structural_gates=gates,
        gate_values=values,
        structural_ready=setup,
        candidate_spec=pullback if setup else None,
        pullback_long=setup,
        pullback_short=False,
        short_entry_eligible=False,
        entry_eligible=False,
    )
    return view


def candidate_view(engine, view, book):
    """Pure projection shared by review/executor. Only executor persists a candidate."""
    from kairos import htf

    state = htf.snapshot(engine)
    cutoff = view["candle_close_time"]
    candidate = state.get("candidate")
    consumed = set(state.get("consumed_candidates", []))
    baseline = state.get("baseline_after", cutoff)
    if candidate and (
        engine.clock() >= candidate["expires_at"]
        or candidate["id"] in consumed
        or not view["structural_gates"]["rising_trend"]
    ):
        consumed.add(candidate["id"])
        candidate = None
    spec = view.get("candidate_spec")
    if (
        candidate is None
        and spec
        and spec["id"] not in consumed
        and cutoff > baseline
        and cutoff > state.get("candidate_window", baseline)
    ):
        candidate = {**spec, "born_at": cutoff, "expires_at": cutoff + 3 * 3600}
    # Do not extend or replace an unconsumed candidate just because quotes improved.
    if candidate:
        price, room = target_room(
            book,
            "buy",
            dec(candidate["target"]),
            engine.settings,
            dec(view["maker_fee_bps"]),
            dec(view["taker_fee_bps"]),
        )
        required = max(dec(10), dec(candidate["atr"]) / book.mid * BPS * dec(".25"))
        quote_ok = dec(candidate["lower"]) <= book.mid <= dec(candidate["upper"])
        view.update(
            entry_prices={**view["entry_prices"], "buy": str(price)},
            entry_targets={**view["entry_targets"], "buy": candidate["target"]},
            target_net_room_bps={**view["target_net_room_bps"], "buy": str(room)},
            required_net_room_bps=str(required),
            entry_eligible=quote_ok and room >= required,
        )
        view["execution_gates"] = {
            "quote_in_candidate_bounds": quote_ok,
            "net_room": room >= required,
        }
    else:
        view["execution_gates"] = {"candidate_available": False}
    view["candidate"] = candidate
    return view
