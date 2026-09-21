"""Paper-only cross-collateral model; not a reproduction of Kraken liquidation rules."""

import time

from kairos.domain import BPS, ZERO, SafetyError, dec


def new_ledger(amount):
    return {
        "cash": str(amount),
        "initial": str(amount),
        "positions": {},
        "fees": "0",
        "funding": "0",
        "funding_ts": time.time(),
    }


def accrue(ledger, settings, now=None):
    now = time.time() if now is None else now
    elapsed = max(0, now - ledger["funding_ts"])
    notional = sum(abs(dec(p["quantity"])) * dec(p["entry"]) for p in ledger["positions"].values())
    fee = notional * dec(settings["margin_rollover_bps"]) / BPS * dec(elapsed) / 14400
    ledger["cash"] = str(dec(ledger["cash"]) - fee)
    ledger["funding"] = str(dec(ledger["funding"]) + fee)
    ledger["funding_ts"] = now


def metrics(ledger, marks):
    pnl, exposure, used = ZERO, ZERO, ZERO
    for pair, position in ledger["positions"].items():
        quantity, entry = dec(position["quantity"]), dec(position["entry"])
        if quantity == 0:
            continue
        mark = marks[pair]
        pnl += quantity * (mark - entry)
        exposure += abs(quantity) * mark
        used += abs(quantity) * entry / dec(position["leverage"])
    return {
        "equity": dec(ledger["cash"]) + pnl,
        "exposure": exposure,
        "used_margin": used,
        "unrealized": pnl,
    }


def apply_fill(ledger, pair, side, volume, cost, fee, settings):
    position = ledger["positions"].get(
        pair, {"quantity": "0", "entry": "0", "leverage": settings["leverage"]}
    )
    old, entry = dec(position["quantity"]), dec(position["entry"])
    signed = volume if side == "buy" else -volume
    opening_fee = ZERO
    if volume:
        price = cost / volume
        if old * signed < 0:
            if abs(signed) > abs(old):
                raise SafetyError("Paper margin orders cannot reverse a position in one fill")
            ledger["cash"] = str(
                dec(ledger["cash"]) + volume * (price - entry) * (1 if old > 0 else -1)
            )
        else:
            entry = (abs(old) * entry + cost) / (abs(old) + volume)
            opening_fee = cost * dec(settings["margin_open_fee_bps"]) / BPS
        position.update(quantity=str(old + signed), entry=str(entry))
        ledger["positions"][pair] = position
    ledger["cash"] = str(dec(ledger["cash"]) - fee - opening_fee)
    ledger["fees"] = str(dec(ledger["fees"]) + fee + opening_fee)
