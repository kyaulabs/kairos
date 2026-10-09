"""Native OKX evidence and exact-once allocated accounting, never balance repairs."""

import copy
import hashlib
import re

from kairos.domain import TERMINAL, ZERO, SafetyError, dec
from kairos.okx import PendingOKX


def currency(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,32}", value):
        raise SafetyError("OKX currency identifier missing or malformed; units cannot be inferred")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,64}", value):
        raise SafetyError("OKX native identifier missing or malformed")
    return value


def native_time(value):
    result = dec(value)
    if result <= 0 or result != result.to_integral_value():
        raise SafetyError("OKX native timestamp missing or malformed")
    return int(result)


def account(rows):
    if len(rows) != 1:
        raise SafetyError("OKX account identity unavailable; no binding or submission")
    row = rows[0]
    uid = identifier(row["uid"])
    if row.get("acctLv") not in {"1", "2"} or row.get("autoLoan") not in (False, "", None):
        raise SafetyError(
            "OKX requires spot/futures account mode without auto-borrow; configure through the official interface, not this application"
        )
    if row.get("enableSpotBorrow") is True:
        raise SafetyError("OKX spot borrowing is enabled; no borrowed execution is supported")
    return {
        "uid": uid,
        "account_level": row["acctLv"],
        "fee_type": row.get("feeType"),
        "can_trade": "trade" in str(row.get("perm", "")).split(","),
    }


def balances(rows):
    if len(rows) != 1 or not isinstance(rows[0].get("details"), list):
        raise SafetyError("OKX complete account balances unavailable; missing data is not zero")
    row = rows[0]
    stamp = native_time(row["uTime"])
    result = {}
    for item in row["details"]:
        asset = currency(item["ccy"])
        if asset in result:
            raise SafetyError("Duplicate OKX balance currency; account evidence incomplete")
        total, available, frozen = (dec(item[key]) for key in ("cashBal", "availBal", "frozenBal"))
        if min(total, available, frozen) < 0 or available > total:
            raise SafetyError(
                f"OKX {asset} cash/available/frozen balances are unsupported; borrowing is prohibited"
            )
        for key in (
            "liab",
            "crossLiab",
            "isoLiab",
            "interest",
            "borrowFroz",
            "spotInUseAmt",
            "spotIsoBal",
            "stgyEq",
            "autoLendMtAmt",
        ):
            if item.get(key) not in (None, "") and dec(item[key]) != 0:
                raise SafetyError(
                    f"OKX {asset} has nonzero {key}; borrowed, bot, copy-trading or pledged funds are unsupported"
                )
        result[asset] = {"total": str(total), "available": str(available), "frozen": str(frozen)}
    # The complete endpoint documents omission only when cashBal AND equity are zero.
    return {"timestamp": stamp, "assets": result}


def totals(snapshot):
    return {asset: dec(row["total"]) for asset, row in snapshot["assets"].items()}


def mismatch(binding, ledger, snapshot):
    baseline = {k: dec(v) for k, v in binding["baseline"].items()}
    delta = {k: dec(v) for k, v in ledger["account_delta"].items()}
    actual = totals(snapshot)
    return {
        asset: {
            "expected": str(baseline.get(asset, ZERO) + delta.get(asset, ZERO)),
            "observed": str(actual.get(asset, ZERO)),
        }
        for asset in baseline.keys() | delta.keys() | actual.keys()
        if baseline.get(asset, ZERO) + delta.get(asset, ZERO) != actual.get(asset, ZERO)
    }


def execution(row, order, binding):
    if (
        row.get("instType") != "SPOT"
        or row.get("instId") != order["instrument"]
        or row.get("side") != order["side"]
    ):
        raise SafetyError("OKX fill instrument/product/side differs from its durable intent")
    if row.get("tradeQuoteCcy") != order["quote"]:
        raise SafetyError(
            "OKX fill spending/receiving currency differs from authorization; no currency substitution"
        )
    client_id = order.get("client_id", order["id"])
    if row.get("clOrdId") not in (None, "", client_id):
        raise SafetyError("OKX fill belongs to a different client intent")
    broker = identifier(row["ordId"])
    if order.get("txid") not in (None, broker):
        raise SafetyError("OKX fill broker order identity changed")
    if not order.get("txid") and row.get("clOrdId") != client_id:
        raise SafetyError("OKX fill before acknowledgement lacks exact client ownership")
    quantity, price = dec(row["fillSz"]), dec(row["fillPx"])
    if quantity <= 0 or price <= 0 or quantity > dec(order["volume"]):
        raise SafetyError("OKX execution has invalid price/quantity")
    bound = dec(order["price"])
    if (order["side"] == "buy" and price > bound) or (order["side"] == "sell" and price < bound):
        raise SafetyError(
            "OKX execution violated the authorized limit; retain evidence and investigate"
        )
    if row.get("fee") in (None, ""):
        raise PendingOKX(
            "OKX execution fee not yet reported; gross execution is not net ownership; no exit submitted"
        )
    fee = dec(row["fee"])
    fee_currency = currency(row["feeCcy"]) if fee != 0 or row.get("feeCcy") else None
    if row.get("execType") not in {"M", "T"}:
        raise SafetyError("OKX execution lacks a supported liquidity role")
    bill, trade = identifier(row["billId"]), identifier(row["tradeId"])
    return {
        "id": f"{binding['environment']}:{hashlib.sha256(binding['uid'].encode()).hexdigest()[:16]}:{order['instrument']}:{bill}",
        "bill_id": bill,
        "trade_id": trade,
        "order_id": broker,
        "quantity": str(quantity),
        "price": str(price),
        "cost": str(quantity * price),
        "fee_signed": str(fee),
        "fee_currency": fee_currency,
        "liquidity": row["execType"],
        "fill_time": native_time(row["fillTime"]),
        "record_time": native_time(row["ts"]),
        "quote": order["quote"],
    }


def apply_executions(store, order, rows, binding):
    """Commit each native execution checkpoint together with every asset delta."""
    order = copy.deepcopy(order)
    ledger = store.get("ledger:" + order["mode"])
    if ledger is None:
        raise SafetyError(
            "OKX execution has no bound allocated ledger; do not import account holdings"
        )
    saved = {row["id"]: row for row in order.get("executions", [])}
    trades = {row["trade_id"]: row["id"] for row in saved.values()}
    added = []
    for row in rows:
        record = execution(row, order, binding)
        old = saved.get(record["id"])
        if old:
            if old != record:
                raise SafetyError(
                    "OKX already-accounted execution/fee was revised; no automatic correction"
                )
            continue
        if record["trade_id"] in trades:
            raise SafetyError(
                "OKX trade reappeared under a different bill identity; deduplication is ambiguous"
            )
        order["txid"] = record["order_id"]
        saved[record["id"]], trades[record["trade_id"]] = record, record["id"]
        volume, cost, fee = dec(record["quantity"]), dec(record["cost"]), dec(record["fee_signed"])
        sign = 1 if order["side"] == "buy" else -1
        changes = {order["base"]: volume * sign, order["quote"]: -cost * sign}
        fee_currency = record["fee_currency"]
        if fee_currency:
            changes[fee_currency] = changes.get(fee_currency, ZERO) + fee
            ledger["fees"][fee_currency] = str(dec(ledger["fees"].get(fee_currency, 0)) - fee)
            order.setdefault("fees", {})[fee_currency] = str(
                dec(order.get("fees", {}).get(fee_currency, 0)) - fee
            )
        for asset, delta in changes.items():
            ledger["account_delta"][asset] = str(dec(ledger["account_delta"].get(asset, 0)) + delta)
            if asset in {order["base"], order["quote"]}:
                ledger["balances"][asset] = str(dec(ledger["balances"].get(asset, 0)) + delta)
            else:
                # Actual third-currency fees/rebates remain native and visible. They
                # do not authorize taking ownership of pre-existing account assets.
                ledger.setdefault("unallocated_fee_effects", {})[asset] = str(
                    dec(ledger.get("unallocated_fee_effects", {}).get(asset, 0)) + delta
                )
        added.append(record)
    quantity = sum((dec(r["quantity"]) for r in saved.values()), ZERO)
    if quantity > dec(order["volume"]):
        raise SafetyError("OKX aggregate executions exceed the authorized quantity")
    order.update(
        executions=list(saved.values()),
        filled=str(quantity),
        cost=str(sum((dec(r["cost"]) for r in saved.values()), ZERO)),
        fee=str(dec(order.get("fees", {}).get(order["quote"], 0))),
        fee_reported=bool(saved) and quantity >= dec(order.get("broker_filled", 0)),
    )
    # Negative ownership is retained as a factual breach, not clamped away. It
    # blocks further writes and successful reconciliation in the engine.
    store.save_order(order, ledger)
    return order, added


def order_observation(order, row):
    """REST cumulative state confirms completion; it never generates a fill."""
    payload = order["payload"]
    expected = {
        "instType": "SPOT",
        "tdMode": "cash",
        "ordType": payload["ordType"],
        "instId": order["instrument"],
        "side": order["side"],
        "clOrdId": order.get("client_id", order["id"]),
        "tradeQuoteCcy": order["quote"],
    }
    if any(row.get(key) != value for key, value in expected.items()):
        raise SafetyError(
            "OKX order identity/environment/parameters differ from its durable intent"
        )
    broker = identifier(row["ordId"])
    if order.get("txid") not in (None, broker):
        raise SafetyError("Duplicate OKX broker orders share one client intent")
    if dec(row["sz"]) != dec(payload["sz"]) or dec(row["px"]) != dec(payload["px"]):
        raise SafetyError(
            "OKX changed the approved quantity or price; no amended order was authorized"
        )
    stamp, filled = native_time(row["uTime"]), dec(row["accFillSz"])
    if filled < 0 or filled > dec(order["volume"]):
        raise SafetyError("OKX cumulative executed quantity violates order bounds")
    states = {
        "live": "open",
        "partially_filled": "open",
        "filled": "closed",
        "canceled": "canceled",
    }
    if row.get("state") not in states:
        raise SafetyError("Unsupported OKX order state; execution unresolved")
    if row["state"] == "filled" and filled != dec(order["volume"]):
        raise SafetyError("OKX filled state disagrees with requested quantity")
    if stamp < order.get("broker_updated", 0):
        return order  # Late observation cannot undo a newer terminal checkpoint.
    previous = order.get("exchange_status")
    status = states[row["state"]]
    if previous in TERMINAL and status not in TERMINAL:
        raise SafetyError("OKX terminal order regressed in a newer response")
    if filled < dec(order.get("broker_filled", 0)):
        raise SafetyError("OKX cumulative execution regressed in a newer response")
    result = {
        **order,
        "txid": broker,
        "broker_updated": stamp,
        "broker_filled": str(filled),
        "exchange_status": status,
    }
    result["status"] = status if filled == dec(order["filled"]) else "settling"
    result["fee_reported"] = filled == dec(order["filled"]) and (filled > 0 or status in TERMINAL)
    return result
