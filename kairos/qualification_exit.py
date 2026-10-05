"""Explicit one-use reduction of the interrupted qualification, never normal trading."""

import asyncio
import time
import uuid
from datetime import UTC, datetime

from kairos import htf
from kairos.clients import ExchangeRejected
from kairos.domain import SafetyError, dec, floor
from kairos.strategies import limit_price

KEY = "paper-qualification-exit"
CONFIRMATION = "SELL CONFIRMED QUALIFICATION HOLDING"


async def recover(engine, qualification_id, confirmation):
    if confirmation != CONFIRMATION:
        raise SafetyError("Explicit sell-only qualification recovery confirmation required")
    e = engine
    generation = e.stop_generation
    async with e.lock:

        def guard():
            if (
                not e.ready
                or e.running
                or e.paper_armed
                or e.shutting_down
                or e.stop_generation != generation
                or e.mode != "paper"
                or not e.kraken.allow_paper
                or e.account_wait
                or e.long_retry
                or e.orders(active=True)
                or e.store.get("cycle")
            ):
                raise SafetyError(
                    "Sell-only recovery requires paused, unarmed, known terminal orders"
                )

        guard()
        q = e.store.get("paper-qualification") or {}
        orders = e.orders()
        position = htf.snapshot(e).get("position")
        if (
            q.get("id") != qualification_id
            or not q.get("retry_of")
            or q.get("status") != "interrupted; manual reconciliation required"
            or e.store.get(KEY)
            or len(orders) != 2
            or e.settings["pair"] != "alpaca:BTC/USD"
            or e.settings["strategy"] != "htf"
            or e.settings["product"] != "spot"
            or not position
            or position["id"] != q["id"]
        ):
            raise SafetyError("Recovery is limited to the interrupted additional qualification")
        first, buy = orders
        prior = e.store.get("paper-qualification:" + q["retry_of"]) or {}
        if (
            first["run_id"] != prior.get("run_id")
            or first["status"] != "canceled"
            or dec(first["filled"]) != 0
            or buy["run_id"] != q.get("run_id")
            or buy["status"] != "closed"
            or buy["side"] != "buy"
            or dec(buy["filled"]) <= 0
            or any(o["side"] != "buy" for o in orders)
        ):
            raise SafetyError(
                "Recovery requires the original cancellation and one confirmed owned buy"
            )
        pair = e.resolve(e.settings["pair"])
        identity, ledger = e.store.get("alpaca-account"), e.ledger()
        owned = htf.owned(e, position)
        if (
            not identity
            or owned != e.balance("BTC")
            or owned > dec(buy["filled"])
            or any(dec(v) for a, v in ledger["balances"].items() if a not in {"USD", "BTC"})
        ):
            raise SafetyError("Qualification ownership does not match the allocated ledger")
        observed = None
        for _ in range(2):
            account = await e.kraken.account()
            positions = await e.kraken.positions()
            rows = await e.kraken.request(
                "GET", "/v2/orders", params={"status": "all", "limit": 100}
            )
            activities = await e.kraken.activities(identity["activities_after"])
            if (
                account["id"] != identity["id"]
                or account.get("crypto_status") != "ACTIVE"
                or set(positions) != {"BTC"}
                or not 0 < positions["BTC"] <= owned
                or owned - positions["BTC"] > dec(buy["filled"]) * dec(".0025") + pair.lot
                or abs(dec(account["cash"]) - dec(identity["unallocated_cash"]) - e.balance("USD"))
                > dec(".01")
                or len(rows) != len(orders)
            ):
                raise SafetyError(
                    "Broker evidence exceeds the narrow pending-base-debit recovery scope"
                )
            local = {o["txid"]: o for o in orders}
            if {row["id"] for row in rows} != set(local):
                raise SafetyError("Broker order identities are incomplete or duplicated")
            for row in rows:
                order = local.get(row["id"])
                if (
                    not order
                    or row["client_order_id"] != order["id"]
                    or row["symbol"] != e.kraken.symbol(pair)
                    or row["side"] != order["side"]
                    or row["status"] != ("filled" if order["status"] == "closed" else "canceled")
                    or dec(row["qty"]) != dec(order["volume"])
                    or dec(row["filled_qty"]) != dec(order["filled"])
                    or dec(row["filled_qty"]) * dec(row.get("filled_avg_price") or 0)
                    != dec(order["cost"])
                ):
                    raise SafetyError(
                        "Broker order history does not match confirmed qualification orders"
                    )
            for row in activities:
                baseline = identity["baseline_activities"].get(row["id"])
                if baseline is not None:
                    if baseline != row:
                        raise SafetyError("Initial funding activity changed")
                elif row["activity_type"] == "FILL" and row["order_id"] in local:
                    continue
                elif (
                    row["activity_type"] in {"FEE", "CFEE"}
                    and e.store.get("alpaca-activity:" + row["id"]) == row
                ):
                    continue
                else:
                    raise SafetyError(
                        "New or external activity requires normal reconciliation first"
                    )
            current = (account["id"], account["cash"], positions, rows, activities)
            if observed is not None and observed != current:
                raise SafetyError("Broker recovery evidence changed between read passes")
            observed = current
        guard()
        if e.ledger() != ledger or e.store.get("alpaca-account") != identity:
            raise SafetyError("Saved ownership changed during recovery reads")
        await e.refresh_fees()
        book = await e.kraken.book(pair)
        book.fresh(min(10, e.settings["stale_seconds"]))
        if book.spread_bps > min(dec(30), dec(e.settings["max_spread_bps"])):
            raise SafetyError("Recovery spread exceeds the existing limit")
        price = limit_price(book, "sell", min(dec(10), dec(e.settings["slippage_bps"])))
        volume = floor(min(positions["BTC"], dec(25) / price, e.limits()[0] / price), pair.lot)
        pair.validate(volume, price)
        guard()
        now, order_id = e.clock(), str(uuid.uuid4())
        record = {
            "qualification_id": q["id"],
            "at": now,
            "status": "claimed",
            "order_id": order_id,
            "broker_quantity": str(positions["BTC"]),
            "unclassified_base_debit": str(owned - positions["BTC"]),
            "volume": str(volume),
            "price": str(price),
        }
        # No balance adjustment, inferred fee, baseline reset or strategy permission.
        e.recovery_required = True
        e.last_error = (
            "Sell-only qualification recovery; actual fee reconciliation remains required"
        )
        with e.store.db:
            e.store._put("paper-qualification:" + q["id"], q)
            e.store._put(KEY, record)
        order = {
            "id": order_id,
            "txid": None,
            "mode": "paper",
            "strategy": "htf",
            "product": "spot",
            "pair": pair.id,
            "base": pair.base,
            "quote": pair.quote,
            "side": "sell",
            "volume": str(volume),
            "price": str(price),
            "maker": False,
            "fee_bps": str(e.fees.rate(pair, False)),
            "created": now,
            "expires": now + 30,
            "cursor": str(int(now * 1_000_000_000)),
            "status": "submitting",
            "filled": "0",
            "cost": "0",
            "fee": "0",
        }
        expected = {
            "symbol": e.kraken.symbol(pair),
            "qty": str(volume),
            "side": "sell",
            "type": "limit",
            "limit_price": str(price),
            "client_order_id": order_id,
            "time_in_force": "ioc",
            "extended_hours": False,
        }
        deadline = time.time() + 5

        def permit(path, payload):
            try:
                current_book = e.kraken.market_data.fresh_book(pair)
                if current_book is None:
                    return False
                current_book.fresh(min(10, e.settings["stale_seconds"]))
                if current_book.spread_bps > min(dec(30), dec(e.settings["max_spread_bps"])):
                    return False
            except SafetyError:
                return False
            return (
                path == "/v2/orders"
                and payload == expected
                and time.time() + 1 < deadline
                and e.stop_generation == generation
                and not e.running
                and not e.paper_armed
                and not e.shutting_down
                and e.recovery_required
            )

        try:
            e.execution_purpose = "qualification sell-only recovery; excluded from strategy trial"
            htf.tag(e, order)
            e.tag_order(order)
            e.store.save_order(order)
            e.event("qualification-recovery", record)
            e.kraken.recovery_exit_guard = permit
            try:
                result = await e.kraken.add(
                    {
                        "pair": pair.id,
                        "type": "sell",
                        "ordertype": "limit",
                        "volume": str(volume),
                        "price": str(price),
                        "cl_ord_id": order_id,
                        "oflags": "fciq",
                        "timeinforce": "IOC",
                        "deadline": datetime.fromtimestamp(deadline, UTC).isoformat(
                            timespec="milliseconds"
                        ),
                    }
                )
                order.update(txid=result["txid"][0], status="open")
                e.store.save_order(order)
            except ExchangeRejected:
                order["status"] = "rejected"
                e.store.save_order(order)
                raise
            except (Exception, asyncio.CancelledError):
                order["status"] = "uncertain"
                e.store.save_order(order)
                raise
            finally:
                e.kraken.recovery_exit_guard = None
            for _ in range(5):
                await e.refresh_order(order)
                if order["status"] in {"closed", "canceled", "expired", "rejected"}:
                    break
                await asyncio.sleep(1)
            record.update(
                status="exit attempted; reconciliation pending",
                filled=order["filled"],
                order_status=order["status"],
            )
        finally:
            e.kraken.recovery_exit_guard = None
            e.execution_purpose = None
            e.running = e.paper_armed = False
            e.recovery_required = True
            record["ended_at"] = e.clock()
            e.store.put(KEY, record)
            # An exceptional manual exit is not a passed normal execution qualification.
            q["status"] = "manual exit recovery; normal qualification incomplete"
            q["recovery_order_id"] = order_id
            e.store.put("paper-qualification", q)
            e.event("qualification-recovery", record)
            e.update_operating_state()
            e.emit_state()
            if e.operations.alerts:
                e.operations.alerts.send(
                    "Kairos paper qualification recovery ended; review reconciliation"
                )
        return record
