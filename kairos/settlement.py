"""BTC paper fee suspense: observed debits are not posted fees or entry permission."""

import asyncio
import hashlib
import json
import uuid
from decimal import ROUND_CEILING

from kairos import htf
from kairos.domain import SafetyError, dec

KEY = "alpaca-settlement"
CENT = dec(".01")
MAX_WAIT = 48 * 3600


def supported(engine):
    return (
        engine.settings["pair"] == "alpaca:BTC/USD"
        and engine.settings["strategy"] == "htf"
        and engine.settings["product"] == "spot"
    )


def pending(engine):
    return (engine.store.get(KEY) or {}).get("status") == "pending"


def signature(orders):
    return {o["id"]: [o["filled"], o["cost"]] for o in orders}


def rounded(value, step):
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


async def read_pass(e, identity, local, pair):
    account = await e.kraken.account()
    positions = await e.kraken.positions()
    rows = await e.kraken.request("GET", "/v2/orders", params={"status": "all", "limit": 500})
    activities = await e.kraken.activities(identity["activities_after"])
    if account["id"] != identity["id"] or account.get("crypto_status") != "ACTIVE":
        raise SafetyError("Alpaca account identity/status changed")
    if set(positions) - {"BTC"} or len(rows) >= 500 or len(rows) != len(local):
        raise SafetyError("Cannot prove exclusive, complete BTC settlement ownership")
    if {r["id"] for r in rows} != set(local):
        raise SafetyError("External or duplicate Alpaca order; manual reconciliation required")
    totals = {k: dec(0) for k in local}
    for row in rows:
        order = local[row["id"]]
        if (
            row["client_order_id"] != order["id"]
            or row["symbol"].replace("/", "") != e.kraken.symbol(pair).replace("/", "")
            or row["side"] != order["side"]
            or row["type"] != "limit"
            or dec(row["qty"]) != dec(order["volume"])
            or dec(row["limit_price"]) != dec(order["price"])
            or row["status"] not in {"filled", "canceled", "expired", "rejected"}
            or e.kraken.normalize_order(row)["status"] != order["status"]
            or dec(row["filled_qty"]) != dec(order["filled"])
            or dec(row["filled_qty"]) * dec(row.get("filled_avg_price") or 0) != dec(order["cost"])
        ):
            raise SafetyError("Alpaca settlement order terms/fills changed or remain active")
    for row in activities:
        baseline = identity["baseline_activities"].get(row["id"])
        if baseline is not None:
            if baseline != row:
                raise SafetyError("Alpaca baseline activity was corrected")
        elif row["activity_type"] == "FILL":
            order = local.get(row["order_id"])
            if (
                not order
                or row.get("side") != order["side"]
                or row.get("symbol", "").replace("/", "") != "BTCUSD"
                or dec(row.get("qty")) <= 0
                or dec(row.get("price")) <= 0
            ):
                raise SafetyError("External or malformed Alpaca fill activity")
            totals[row["order_id"]] += dec(row["qty"])
        elif row["activity_type"] not in {"FEE", "CFEE"}:
            raise SafetyError("External account activity; manual reconciliation required")
    if any(totals[k] != dec(o["filled"]) for k, o in local.items()):
        raise SafetyError("Alpaca fill activities do not cover confirmed order quantities")
    return account, positions, rows, activities


async def reconcile(e, identity):
    # Broker writes never run here. Two complete matching reads are mandatory even
    # when previously observed suspense already makes the net balance match.
    for order in e.orders(active=True):
        await e.refresh_order(order)
    if htf.snapshot(e).get("entry_attempt", {}).get("order_id"):
        htf.reconcile_entry(e)
    orders = e.orders()
    if e.orders(active=True):
        raise SafetyError("Working Alpaca orders must terminate before settled-account checks")
    if any(
        o["pair"] != "alpaca:BTC/USD"
        or (not o["txid"] and (o["status"] != "rejected" or dec(o["filled"]) or dec(o["cost"])))
        for o in orders
    ):
        raise SafetyError("BTC fee settlement requires exclusively tracked BTC orders")
    pair = e.resolve(e.settings["pair"])
    # Retain known no-submit rejections locally. Any unexpected broker row still
    # fails the complete-history comparison; uncertain writes are never excluded.
    local = {o["txid"]: o for o in orders if o["txid"]}

    def stable(view):
        a, p, r, acts = view
        return (a["id"], a["cash"], a["non_marginable_buying_power"], p, r, acts)

    # A fee journal may arrive between successful reads. Require two adjacent
    # matching snapshots within three passes/60s; never retry a failed HTTP request.
    async with asyncio.timeout(60):
        previous = await read_pass(e, identity, local, pair)
        for _ in range(2):
            current = await read_pass(e, identity, local, pair)
            if stable(previous) == stable(current):
                break
            previous = current
        else:
            raise SafetyError("Alpaca settlement evidence changed between read passes")
    account, positions, rows, activities = current
    if e.store.get("alpaca-account") != identity:
        raise SafetyError("Saved Alpaca binding changed during settlement reads")
    state = e.store.get(KEY) or {
        "status": "settled",
        "checkpoint": {},
        "baseline_fees": {},
        "debits": {},
    }
    checkpoint = state["checkpoint"]
    changed = any(dec(o["filled"]) > dec(checkpoint.get(o["id"], [0, 0])[0]) for o in orders)
    if state["status"] == "settled" and changed:
        state = {
            **state,
            "id": str(uuid.uuid4()),
            "status": "pending",
            "since": e.clock(),
            "deadline": min(
                o["created"]
                for o in orders
                if dec(o["filled"]) > dec(checkpoint.get(o["id"], [0, 0])[0])
            )
            + MAX_WAIT,
            "debits": {},
            "reserves": {},
        }
        e.store.put(KEY, state)
        e.htf_review.cancel()
        e.event(
            "fee-settlement",
            {"message": "Awaiting actual fee activities; entries blocked", **state},
        )
    for row in activities:
        if row["id"] not in identity["baseline_activities"] and row["activity_type"] in {
            "FEE",
            "CFEE",
        }:
            e.apply_fee(row)
    state = e.store.get(KEY) or state  # apply_fee may have consumed suspense atomically.
    ledger = e.ledger()
    if any(dec(v) for a, v in ledger["balances"].items() if a not in {"USD", "BTC"}):
        raise SafetyError("Unrelated local inventory blocks BTC settlement")
    expected = {"USD": dec(ledger["balances"]["USD"]), "BTC": dec(ledger["balances"].get("BTC", 0))}
    actual = {
        "USD": dec(account["cash"]) - dec(identity["unallocated_cash"]),
        "BTC": positions.get("BTC", dec(0)),
    }
    if min(actual.values()) < 0:
        raise SafetyError("Alpaca settlement exceeds the allocated account")
    if state["status"] != "pending":
        if actual != expected:
            raise SafetyError("Alpaca cash/positions differ without a new confirmed fill")
        e.store.put(KEY, state)
        e.account_read_status["last_success_at"] = e.clock()
        return account
    budgets, currencies, rounding_limit = {}, set(), dec(0)
    for order in orders:
        before = checkpoint.get(order["id"], [0, 0])
        qty, cost = dec(order["filled"]) - dec(before[0]), dec(order["cost"]) - dec(before[1])
        if min(qty, cost) < 0 or bool(qty) != bool(cost):
            raise SafetyError("Alpaca settlement cumulative fills moved backwards")
        if not qty:
            continue
        currency, step = ("BTC", pair.lot) if order["side"] == "buy" else ("USD", CENT)
        rate = dec(order["planning_fee_bps"])
        if not 0 < rate <= 25:
            raise SafetyError("Unsupported captured crypto fee reserve")
        # Each partial fill can incur native-currency rounding; never reuse a settled
        # order's unused maker discount as authority for a later unexplained debit.
        fills = [
            r for r in activities if r["activity_type"] == "FILL" and r["order_id"] == order["txid"]
        ]
        if dec(before[0]):
            # Pending batches never advance their checkpoint mid-order.
            raise SafetyError("Partial historical settlement checkpoint needs manual review")
        cap = dec(0)
        for fill in fills:
            terms = {
                "currency": currency,
                "qty": fill["qty"],
                "price": fill["price"],
                "rate": str(rate),
            }
            reserve = state["reserves"].get(fill["id"])
            if reserve is not None and any(reserve[k] != v for k, v in terms.items()):
                raise SafetyError("Recorded fill/fee reserve was corrected")
            if reserve is None:
                basis = dec(fill["qty"]) * (dec(fill["price"]) if currency == "USD" else 1)
                reserve = {
                    **terms,
                    "step": str(step),
                    "cap": str(rounded(basis * rate / 10000, step)),
                }
                state["reserves"][fill["id"]] = reserve
            cap += dec(reserve["cap"])
        budgets[currency] = budgets.get(currency, dec(0)) + cap
        rounding_limit += CENT * len(fills)
        currencies.add(currency)
    posted = {
        a: dec(ledger["fees"].get(a, 0)) - dec(state["baseline_fees"].get(a, 0)) for a in currencies
    }
    if any(not 0 <= posted[a] <= budgets[a] for a in currencies):
        raise SafetyError("Actual crypto fees exceed the captured settlement reserve")
    fees_seen = bool(currencies) and all(posted[a] > 0 for a in currencies)
    old_debits = {a: dec(state["debits"].get(a, 0)) for a in expected}
    debits = {a: expected[a] + old_debits[a] - actual[a] for a in expected}
    # A final USD rounding residual is classified only after actual activities cover
    # the batch. No BTC rounding or fee amount is inferred from the position gap.
    cash_rounding = dec(0)
    if fees_seen and debits["BTC"] == 0 and abs(debits["USD"]) <= rounding_limit:
        cash_rounding = -debits["USD"]
        debits["USD"] = dec(0)
    for asset, value in debits.items():
        allowance = budgets.get(asset, dec(0)) - posted.get(asset, dec(0))
        if asset == "USD" and currencies:
            allowance += rounding_limit
        if value < 0 or value > allowance:
            raise SafetyError("Alpaca balance difference exceeds fill-linked settlement bounds")
    if (not fees_seen or any(debits.values())) and e.clock() > state["deadline"]:
        raise SafetyError("Crypto fee settlement exceeded 48 hours; manual reconciliation required")
    position_state = htf.snapshot(e)
    position = position_state.get("position")
    adjustment = old_debits["BTC"] - debits["BTC"]
    if adjustment and (not position or htf.owned(e, position) != expected["BTC"]):
        raise SafetyError("Pending crypto debit does not match tracked HTF ownership")
    if position and adjustment:
        position["inventory_adjustment"] = str(
            dec(position.get("inventory_adjustment", 0)) + adjustment
        )
    for asset in expected:
        ledger["balances"][asset] = str(
            expected[asset]
            + old_debits[asset]
            - debits[asset]
            + (cash_rounding if asset == "USD" else 0)
        )
        if dec(ledger["balances"][asset]) != actual[asset]:
            raise SafetyError("Settlement net balance does not match the broker")
    ledger["broker_rounding"] = str(dec(ledger.get("broker_rounding", 0)) + cash_rounding)
    settled = fees_seen and not any(debits.values())
    before_status = state["status"]
    state.update(
        status="settled" if settled else "pending",
        cash_rounding_bound=str(rounding_limit),
        debits={a: str(v) for a, v in debits.items() if v},
        checked_at=e.clock(),
        evidence_hash=hashlib.sha256(
            json.dumps(
                stable((account, positions, rows, activities)), sort_keys=True, default=str
            ).encode()
        ).hexdigest(),
    )
    if settled:
        state.update(
            checkpoint=signature(orders), baseline_fees=dict(ledger["fees"]), settled_at=e.clock()
        )
    with e.store.db:
        e.store._put("ledger:paper", ledger)
        e.store._put(htf.key(e), position_state)
        e.store._put(KEY, state)
    if before_status != state["status"] or old_debits != debits:
        e.event(
            "fee-settlement",
            {
                "message": "Actual fees reconciled"
                if settled
                else "Observed debits held in suspense, not booked as fees",
                **state,
            },
        )
    if settled:
        htf.arm(e)  # No pending-fee-era entry candidate may be caught up after settlement.
    e.account_read_status["last_success_at"] = e.clock()
    return account
