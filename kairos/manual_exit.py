"""Explicit accounting of an already-filled external qualification sale. No broker writes."""

import asyncio
import hashlib
import json

from kairos import htf, settlement
from kairos.domain import SafetyError, dec
from kairos.market_data import timestamp

CONFIRMATION = "ACKNOWLEDGE MANUAL QUALIFICATION EXIT"
PREFIX = "alpaca-manual-exit:"


def available(e):
    q = e.store.get("paper-qualification") or {}
    position = htf.snapshot(e).get("position")
    return bool(
        e.ready
        and not e.running
        and not e.paper_armed
        and not e.shutting_down
        and e.mode == "paper"
        and e.kraken.allow_paper
        and e.settings["pair"] == "alpaca:BTC/USD"
        and e.settings["strategy"] == "htf"
        and e.settings["product"] == "spot"
        and not e.account_wait
        and not e.long_retry
        and not e.orders(active=True)
        and not e.store.get("cycle")
        and q.get("status") == "interrupted; manual reconciliation required"
        and position
        and position["id"] == q.get("id")
        and e.balance("BTC") > 0
    )


def guard(e, generation):
    if not available(e) or e.stop_generation != generation:
        raise SafetyError(
            "Manual-sale reconciliation requires the stopped, owned qualification holding"
        )


async def inspect(e, generation):
    guard(e, generation)
    q, ledger = e.store.get("paper-qualification"), e.ledger()
    identity = e.store.get("alpaca-account")
    position = htf.snapshot(e)["position"]
    local = {o["txid"]: o for o in e.orders() if o.get("txid")}
    owned = htf.owned(e, position)
    buys = [o for o in local.values() if o.get("htf_id") == q["id"] and o["side"] == "buy"]
    if (
        not identity
        or owned != e.balance("BTC")
        or len(local) != len(e.orders())
        or len(buys) != 1
        or buys[0]["status"] != "closed"
        or buys[0].get("run_id") != q.get("run_id")
        or dec(buys[0].get("planning_fee_bps", 0)) != 25
    ):
        raise SafetyError("Cannot attribute the manual sale to one confirmed qualification buy")
    pair = e.resolve(e.settings["pair"])
    rows = await e.kraken.request("GET", "/v2/orders", params={"status": "all", "limit": 500})
    extra = [r for r in rows if r["id"] not in local]
    if len(rows) >= 500 or len(rows) != len(local) + 1 or len(extra) != 1:
        raise SafetyError(
            "Reconcile requires exactly one external completed liquidation, not other account activity"
        )
    row = extra[0]
    client_id = row.get("client_order_id")
    if (
        row.get("symbol", "").replace("/", "") != "BTCUSD"
        or row.get("side") != "sell"
        or row.get("type") not in {"limit", "market"}
        or row.get("status") != "filled"
        or row["type"] == "limit"
        and dec(row.get("limit_price") or 0) <= 0
        or dec(row.get("qty", 0)) != owned
        or dec(row.get("filled_qty", 0)) != owned
        or not isinstance(client_id, str)
        or not client_id
        or any(o["id"] == client_id for o in local.values())
        or e.store.get(PREFIX + row["id"])
    ):
        raise SafetyError(
            "Only the fully filled external sale of this exact owned BTC quantity can be reconciled"
        )
    price = dec(row.get("filled_avg_price", 0))
    created = timestamp(row.get("submitted_at") or row["created_at"])
    if price <= 0 or not q["at"] <= created <= e.clock() + 2:
        raise SafetyError("Manual sale price/time does not match the qualification holding")
    cost = owned * price
    order = {
        "id": client_id,
        "txid": row["id"],
        "mode": "paper",
        "exchange": "alpaca",
        "strategy": "htf",
        "product": "spot",
        "pair": pair.id,
        "base": "BTC",
        "quote": "USD",
        "side": "sell",
        "volume": str(owned),
        "price": str(dec(row["limit_price"]) if row["type"] == "limit" else price),
        "maker": None,
        "created": created,
        "status": "closed",
        "filled": str(owned),
        "cost": str(cost),
        "fee": "0",
        "fee_reported": False,
        "htf_id": q["id"],
        "externally_executed": True,
        "broker_order_type": row["type"],
        "reason": "Operator-confirmed manual liquidation; not submitted by Kairos",
        "reconciliation_fee_bound_bps": buys[0]["planning_fee_bps"],
        "fee_note": "Reconciliation bound inherited from qualification planning; not a historical fee quote or posted fee",
        "fee_bound_source_order_id": buys[0]["id"],
    }
    local[row["id"]] = order
    # These are complete, adjacent matching reads, not cached dashboard evidence.
    async with asyncio.timeout(60):
        first = await settlement.read_pass(e, identity, local, pair)
        second = await settlement.read_pass(e, identity, local, pair)

    def stable(view):
        a, positions, orders, activities = view
        return (a["id"], a["cash"], a["non_marginable_buying_power"], positions, orders, activities)

    if stable(first) != stable(second) or row != next(r for r in second[2] if r["id"] == row["id"]):
        raise SafetyError("Manual-sale evidence changed between reads; review a fresh preview")
    account, positions, _, activities = second
    fills = [
        r for r in activities if r.get("order_id") == row["id"] and r["activity_type"] == "FILL"
    ]
    if positions or not fills or sum(dec(f["qty"]) * dec(f["price"]) for f in fills) != cost:
        raise SafetyError("Manual-sale fills do not prove a flat account and exact proceeds")
    for fill in fills:
        if not created <= timestamp(fill["transaction_time"]) <= e.clock() + 2:
            raise SafetyError("Manual-sale fill has an invalid native execution time")
    cash = dec(account["cash"]) - dec(identity["unallocated_cash"])
    difference = dec(ledger["balances"]["USD"]) + cost - cash
    allowance = sum(
        settlement.rounded(dec(f["qty"]) * dec(f["price"]) * dec(".0025"), settlement.CENT)
        for f in fills
    )
    if not 0 <= difference <= allowance + settlement.CENT * len(fills):
        raise SafetyError("Manual-sale cash difference exceeds its native fee and rounding bounds")
    guard(e, generation)
    if (
        e.ledger() != ledger
        or e.store.get("alpaca-account") != identity
        or e.store.get("paper-qualification") != q
        or htf.snapshot(e).get("position") != position
    ):
        raise SafetyError("Saved ownership changed during manual-sale reads")
    evidence = hashlib.sha256(
        json.dumps([stable(second), ledger, q, position, generation], sort_keys=True).encode()
    ).hexdigest()
    preview = {
        "qualification_id": q["id"],
        "broker_order_id": row["id"],
        "evidence_hash": evidence,
        "quantity": str(owned),
        "average_price": str(price),
        "gross_proceeds": str(cost),
        "allocated_cash_after": str(cash),
        "unclassified_cash_difference": str(difference),
    }
    return preview, order, ledger, q, row, fills


async def preview(e):
    generation = e.stop_generation
    async with e.lock:
        return (await inspect(e, generation))[0]


async def reconcile(e, qualification_id, broker_order_id, evidence_hash, confirmation):
    if confirmation != CONFIRMATION:
        raise SafetyError("Explicit acknowledgement of the existing manual sale is required")
    generation = e.stop_generation
    async with e.lock:
        view, order, ledger, q, broker_order, fills = await inspect(e, generation)
        if (qualification_id, broker_order_id, evidence_hash) != (
            view["qualification_id"],
            view["broker_order_id"],
            view["evidence_hash"],
        ):
            raise SafetyError("Manual-sale confirmation does not match the current preview")
        guard(e, generation)
        ledger["balances"]["BTC"] = str(dec(ledger["balances"]["BTC"]) - dec(order["filled"]))
        ledger["balances"]["USD"] = str(dec(ledger["balances"]["USD"]) + dec(order["cost"]))
        receipt = {
            **view,
            "acknowledged_at": e.clock(),
            "broker_order": broker_order,
            "fills": fills,
            "local_order_id": order["id"],
        }
        e.store.import_manual_exit(order, ledger, receipt, q)
        # Only confirmed gross fills have been recorded. Existing settlement code
        # classifies actual journals or retains bounded suspense; no fee is invented.
        e.running = e.paper_armed = False
        e.recovery_required = True
        try:
            await e.reconcile_account()
            e.recovery_required = False
            e.last_error = None
            e.account_check_status = {
                "status": "fees pending" if e.fee_settlement_pending else "matched",
                "checked_at": e.clock(),
                "next_at": e.clock() + 60,
                "interval_seconds": 60,
            }
            e.next_account_check = e.clock() + 60
        except Exception:
            e.last_error = "Manual sale recorded; automatic account verification pending"
            e.account_check_status = {"status": "blocked", "error": e.last_error}
            e.next_account_check = 0
            raise
        finally:
            e.event(
                "manual-liquidation",
                {
                    "message": "External sale recorded; no broker order submitted; trading remains paused",
                    **view,
                },
            )
            e.update_operating_state()
            e.emit_state()
        return view
