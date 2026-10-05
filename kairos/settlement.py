"""BTC paper fee suspense: observed debits are not posted fees or entry permission."""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import ROUND_CEILING

from kairos import htf
from kairos.domain import SafetyError, dec

KEY = "alpaca-settlement"
CENT = dec(".01")
REVIEW_AFTER = 48 * 3600


def supported(engine):
    return (
        engine.settings["pair"] == "alpaca:BTC/USD"
        and engine.settings["strategy"] == "htf"
        and engine.settings["product"] == "spot"
    )


def pending(engine):
    return (engine.store.get(KEY) or {}).get("status") == "pending"


def reserve_usd(engine, price):
    state = engine.store.get(KEY) or {}
    if state.get("status") != "pending":
        return dec(0)
    reserve = state.get("unposted_reserve")
    if reserve is None:
        raise SafetyError("Automatic fee check required before using pending funds")
    return dec(reserve.get("USD", 0)) + dec(reserve.get("BTC", 0)) * dec(price)


def retained_base(engine):
    return sum(
        (
            dec(v["cap"])
            for v in (engine.store.get(KEY) or {}).get("unwitnessed", {}).values()
            if v["asset"] == "BTC"
        ),
        dec(0),
    )


def entry_budget(engine, price):
    state = engine.store.get(KEY) or {}
    if state.get("status") != "pending":
        return engine.balance("USD")
    reserve = reserve_usd(engine, price)
    outstanding = (
        reserve
        + dec(state["debits"].get("USD", 0))
        + dec(state["debits"].get("BTC", 0)) * dec(price)
    )
    limit = min(dec("12.50"), dec(engine.settings["daily_loss"]))
    if outstanding > limit:
        raise SafetyError("Outstanding crypto fee allowance exceeds the pending-fee risk limit")
    # Leave room for both sides' planning costs and native/cash precision, rather
    # than admitting an entry that immediately exhausts the pending-fee limit.
    precision = engine.resolve(engine.settings["pair"]).lot * dec(price) + 3 * CENT
    fee_room = max(dec(0), limit - outstanding - precision)
    round_trip_budget = fee_room / dec(".005") * dec("1.0025")
    return max(dec(0), min(engine.balance("USD") - reserve, round_trip_budget))


def signature(orders):
    return {o["id"]: [o["filled"], o["cost"]] for o in orders}


def rounded(value, step):
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def native_day(value):
    if value is None:
        return "undated"
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise SafetyError("Invalid native fee period timestamp") from None
    if len(value) > 10:
        if stamp.tzinfo is None:
            raise SafetyError("Fee period requires an explicit native timezone")
        stamp = stamp.astimezone(UTC)
    return stamp.date().isoformat()


def period_coverage(e, state, orders, activities, observed, expected, actual, posted):
    """Do not let a newer period's reserve or debit cover an older fee."""
    previous = state.get("periods", {})
    periods, fresh, local_days = {}, {"BTC": set(), "USD": set()}, set()
    by_txid = {o["txid"]: o for o in orders if o["txid"]}
    for fill in activities:
        reserve = state["reserves"].get(fill["id"])
        if fill["activity_type"] != "FILL" or reserve is None:
            continue
        day = native_day(fill.get("transaction_time"))
        stamp = fill.get("transaction_time")
        if "transaction_time" in reserve and reserve["transaction_time"] != stamp:
            raise SafetyError("Recorded fill period was corrected")
        reserve["transaction_time"] = stamp
        reserve["period"] = day
        period = periods.setdefault(
            day,
            {
                "caps": {},
                "posted": {},
                "observed": dict(previous.get(day, {}).get("observed", {})),
                "rounding_bound": dec(0),
            },
        )
        asset = reserve["currency"]
        period["caps"][asset] = period["caps"].get(asset, dec(0)) + dec(reserve["cap"])
        period["rounding_bound"] += CENT
        order = by_txid[fill["order_id"]]
        local_days.add(datetime.fromtimestamp(order["created"], UTC).date().isoformat())
        if dec(order["filled"]) > dec(observed.get(order["id"], [0, 0])[0]):
            fresh[asset].add(day)
    if "undated" in periods and (len(periods) > 1 or len(local_days) > 1):
        raise SafetyError("Native fill dates required to isolate overlapping fee periods")
    baseline = state.get("baseline_fee_ids")
    if baseline is None:
        if any(dec(v) for v in state["baseline_fees"].values()):
            raise SafetyError(
                "Historical fee baseline lacks period linkage; investigation required"
            )
        baseline = state["baseline_fee_ids"] = []
    for fee in activities:
        if fee["activity_type"] not in {"FEE", "CFEE"} or fee["id"] in baseline:
            continue
        asset = "BTC" if fee["activity_type"] == "CFEE" and dec(fee.get("qty", 0)) else "USD"
        amount = -dec(fee.get("qty", 0) if asset == "BTC" else fee["net_amount"])
        eligible = [d for d, p in periods.items() if asset in p["caps"]]
        day = native_day(fee.get("date"))
        if eligible == ["undated"] or (day == "undated" and len(eligible) == 1):
            day = eligible[0]
        if day not in eligible:
            raise SafetyError("Posted fee has no unambiguous native settlement period")
        period = periods[day]
        period["posted"][asset] = period["posted"].get(asset, dec(0)) + amount
    for asset in ("BTC", "USD"):
        eligible = [d for d, p in periods.items() if asset in p["caps"] or asset == "USD"]
        total = (
            expected[asset]
            + dec(state["debits"].get(asset, 0))
            + posted.get(asset, dec(0))
            - actual[asset]
        )
        prior = sum((dec(p["observed"].get(asset, 0)) for p in periods.values()), dec(0))
        delta = total - prior
        if delta:
            candidates = set(fresh[asset])
            for day in eligible:
                paid = periods[day]["posted"].get(asset, dec(0))
                if paid > dec(previous.get(day, {}).get("posted", {}).get(asset, 0)):
                    candidates.add(day)
            if not previous:
                candidates.update(eligible)
            # Cash precision can move on a buy, even though its fee is in BTC.
            if asset == "USD":
                candidates.update(fresh["BTC"])
            if len(candidates) != 1:
                raise SafetyError("Balance debit cannot be isolated to one fee period")
            day = candidates.pop()
            if delta < 0 and (asset != "USD" or abs(delta) > periods[day]["rounding_bound"]):
                raise SafetyError("Unexpected credit exceeds settlement bounds")
            period = periods[day]
            period["observed"][asset] = str(dec(period["observed"].get(asset, 0)) + delta)
    covered = bool(periods)
    for period in periods.values():
        complete = True
        for asset in ("BTC", "USD"):
            cap = period["caps"].get(asset, dec(0))
            paid = period["posted"].get(asset, dec(0))
            debit = dec(period["observed"].get(asset, 0))
            rounding = period["rounding_bound"] if asset == "USD" else dec(0)
            if paid > cap or debit > cap + rounding or paid - debit > rounding:
                raise SafetyError("Fee period exceeds fill-linked settlement bounds")
            if asset in period["caps"] and (paid <= 0 or abs(debit - paid) > rounding):
                complete = False
        period["status"] = "matched" if complete else "pending"
        covered &= complete
        period["caps"] = {a: str(v) for a, v in period["caps"].items()}
        period["posted"] = {a: str(v) for a, v in period["posted"].items()}
        period["rounding_bound"] = str(period["rounding_bound"])
    state["periods"] = periods
    return covered


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
    day = datetime.fromtimestamp(e.clock(), UTC).date().isoformat()
    if state.get("utc_day") and state["utc_day"] != day:
        e.event(
            "fee-day",
            {
                "message": "UTC accounting snapshot; unresolved fees carried forward, not assumed settled",
                "utc_day": state["utc_day"],
                "checked_at": state.get("checked_at"),
                "debits": state["debits"],
                "unposted_reserve": state.get("unposted_reserve"),
                "status": state["status"],
                "deadline": state.get("deadline"),
                "posted_fees": state.get("posted_fees"),
                "periods": state.get("periods"),
                "settlement_id": state.get("id"),
            },
        )
    state["utc_day"] = day
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
            + REVIEW_AFTER,
            "debits": {},
            "reserves": {},
            "periods": {},
            "baseline_fee_ids": [
                r["id"]
                for r in activities
                if r["activity_type"] in {"FEE", "CFEE"}
                and (
                    r["id"] in identity["baseline_activities"]
                    or e.store.get("alpaca-activity:" + r["id"])
                )
            ],
        }
        e.store.put(KEY, state)
        e.htf_review.cancel()
        e.event(
            "fee-settlement",
            {"message": "Awaiting actual fee activities; conservative reserves retained", **state},
        )
    # A zero debit is not evidence of a zero fee. Track receipts separately from
    # activity posting, so an old fee cannot release a new fill's reserve.
    unseen = state.setdefault("unwitnessed", {})
    first_receipts = "observed" not in state
    witnessed = state.setdefault("witnessed", [])
    for order in orders:
        if (
            dec(order["filled"]) > dec(checkpoint.get(order["id"], [0, 0])[0])
            and order["id"] not in witnessed
            and order["id"] not in unseen
        ):
            asset = "BTC" if order["side"] == "buy" else "USD"
            unseen[order["id"]] = {"asset": asset, "cap": "0"}
    balances = e.ledger()["balances"]
    for asset in ("BTC", "USD"):
        candidates = [k for k, v in unseen.items() if v["asset"] == asset]
        actual_balance = (
            positions.get("BTC", dec(0))
            if asset == "BTC"
            else dec(account["cash"]) - dec(identity["unallocated_cash"])
        )
        delta = dec(balances.get(asset, 0)) - actual_balance
        # A posted fee can be booked before the broker's balance catches up.
        # Until a successful period snapshot includes it, keep that known debit
        # in the receipt comparison; do not require another fee or a manual reset.
        unrecorded_paid = max(
            dec(0),
            dec(e.ledger()["fees"].get(asset, 0))
            - dec(state["baseline_fees"].get(asset, 0))
            - sum(
                (dec(p.get("posted", {}).get(asset, 0)) for p in state.get("periods", {}).values()),
                dec(0),
            ),
        )
        delta += unrecorded_paid
        prior_debit = dec(state["debits"].get(asset, 0)) if first_receipts else dec(0)
        new_actual_fee = any(
            r["activity_type"] in {"FEE", "CFEE"}
            and r["id"] not in identity["baseline_activities"]
            and not e.store.get("alpaca-activity:" + r["id"])
            and (bool(dec(r.get("qty", 0))) == (asset == "BTC"))
            for r in activities
        )
        threshold = dec(0) if asset == "BTC" or new_actual_fee or unrecorded_paid else CENT
        if len(candidates) == 1 and delta + prior_debit > threshold:
            witnessed.append(candidates[0])
            del unseen[candidates[0]]
    # Positive fees from an older receipt cannot settle newly observed fills.
    observed = state.get("observed")
    if observed is None:
        # Existing v0.7.3 reserves are already witnessed, not new executions.
        observed = {
            o["id"]: [
                str(
                    sum(
                        (
                            dec(r["qty"])
                            for r in activities
                            if r["activity_type"] == "FILL"
                            and r["order_id"] == o["txid"]
                            and r["id"] in state.get("reserves", {})
                        ),
                        dec(0),
                    )
                ),
                "0",
            ]
            for o in orders
        }
        observed = {**state["checkpoint"], **{k: v for k, v in observed.items() if dec(v[0])}}
    floors = state.setdefault("coverage_floor", dict(state["baseline_fees"]))
    for order in orders:
        if dec(order["filled"]) > dec(observed.get(order["id"], [0, 0])[0]):
            asset = "BTC" if order["side"] == "buy" else "USD"
            floors[asset] = e.ledger()["fees"].get(asset, "0")
    state["observed"] = signature(orders)
    for row in activities:
        if row["id"] not in identity["baseline_activities"] and row["activity_type"] in {
            "FEE",
            "CFEE",
        }:
            e.apply_fee(row)
    # Preserve tentative receipt evidence only after validation; fee application
    # itself may already have consumed durable suspense atomically.
    state["debits"] = (e.store.get(KEY) or state)["debits"]
    state["utc_day"] = day
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
        state.update(checked_at=e.clock(), posted_fees=dict(ledger["fees"]), unposted_reserve={})
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
        if order["id"] in state["unwitnessed"]:
            state["unwitnessed"][order["id"]]["cap"] = str(cap)
        budgets[currency] = budgets.get(currency, dec(0)) + cap
        rounding_limit += CENT * len(fills)
        currencies.add(currency)
    posted = {
        a: dec(ledger["fees"].get(a, 0)) - dec(state["baseline_fees"].get(a, 0)) for a in currencies
    }
    if any(not 0 <= posted[a] <= budgets[a] for a in currencies):
        raise SafetyError("Actual crypto fees exceed the captured settlement reserve")
    periods_covered = period_coverage(
        e, state, orders, activities, observed, expected, actual, posted
    )
    fees_seen = (
        periods_covered
        and bool(currencies)
        and not state["unwitnessed"]
        and all(
            dec(ledger["fees"].get(a, 0)) > dec(state["coverage_floor"].get(a, 0))
            for a in currencies
        )
    )
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
    before_overdue = state.get("overdue", False)
    state.update(
        status="settled" if settled else "pending",
        # Retain the original deadline as an investigation reminder, not proof
        # that the paper broker will ever publish an activity for this debit.
        overdue=not settled and e.clock() > state["deadline"],
        cash_rounding_bound=str(rounding_limit),
        posted_fees=dict(ledger["fees"]),
        unposted_reserve={}
        if settled
        else {
            a: str(
                max(
                    dec(0),
                    budgets[a] - posted[a] - debits.get(a, dec(0)),
                    sum(
                        (dec(v["cap"]) for v in state["unwitnessed"].values() if v["asset"] == a),
                        dec(0),
                    ),
                )
            )
            for a in currencies
        },
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
    if state["overdue"] and not before_overdue:
        message = "Paper fee records overdue; unclassified debits and bounded reserves retained"
        e.event("fee-settlement", {"message": message, **state})
        if e.operations.alerts:
            e.operations.alerts.send(message)
    # Routine fee publication does not stop trading or retire otherwise valid signals.
    e.account_read_status["last_success_at"] = e.clock()
    return account
