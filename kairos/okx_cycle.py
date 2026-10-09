"""One-use finite OKX previews and execution_cycle; TWAP retains the shared scheduler."""

import asyncio
import time
import uuid
from decimal import ROUND_UP

from kairos import okx_account as native
from kairos import programs
from kairos.domain import BPS, TERMINAL, ZERO, SafetyError, dec, floor
from kairos.fees import FeeUnavailable
from kairos.okx_engine import fingerprint
from kairos.strategies import limit_price

FIELDS = {
    "kind",
    "pair",
    "allocation",
    "budget",
    "buy_ceiling",
    "sell_floor",
    "max_exit_attempts",
    "duration_seconds",
    "demo_fee_allowance_bps",
}


def confirmation(e, kind):
    environment = "DEMO" if e.client.environment == "demo" else "LIVE REAL MONEY"
    return f"AUTHORIZE OKX {environment} {kind.upper()}"


def idle(e):
    if e.running or e.armed or e.orders(active=True) or e.shutting_down:
        raise SafetyError("Stop and reconcile OKX before preparing another finite authorization")


async def fees(e, pair, allowance):
    try:
        await e.refresh_fees(force=True)
        e.planning_source = e.client.fee_source
    except FeeUnavailable as exc:
        if e.client.environment != "demo" or allowance is None or not ZERO < dec(allowance) <= 1000:
            reason = str(exc.__cause__) if isinstance(exc.__cause__, SafetyError) else str(exc)
            raise SafetyError(
                f"OKX account/instrument planning fee capability unavailable: {reason}; nothing submitted. Demo diagnostics alone may use an explicitly approved positive conservative allowance; live/TWAP cannot."
            ) from None
        e.fees.rates[pair.id] = {
            "pair": pair.id,
            "symbol": pair.symbol,
            "maker_bps": str(dec(allowance)),
            "taker_bps": str(dec(allowance)),
            "received": time.time(),
        }
        e.fees.errors.pop(pair.id, None)
        e.fees.failures.pop(pair.id, None)
        e.planning_source = "Operator-approved demo diagnostic planning allowance; account fee discovery unavailable; actual execution fees still required"
    return e.fees.reserve(pair)


async def preview(e, values):
    idle(e)
    if not isinstance(values, dict) or set(values) - FIELDS:
        raise SafetyError("Unknown OKX finite-run preview fields")
    async with e.lock:
        idle(e)
        generation = e.stop_generation
        kind = values.get("kind", "execution_cycle")
        if kind not in {"execution_cycle", "twap"}:
            raise SafetyError("Only execution_cycle and finite TWAP are available for OKX")
        if not e.client.configured:
            raise SafetyError(
                f"OKX {e.client.environment} credentials missing; install this environment's credentials privately"
            )
        await e.client.catalog()
        pair = e.resolve(values.get("pair", e.settings["pair"]))
        binding = e.store.get("okx-account")
        if binding and pair.quote != binding["quote"]:
            raise SafetyError(
                "OKX allocation currency is already bound; USD/stablecoins are not interchangeable"
            )
        if (
            e.ledger()
            and any(dec(v) for a, v in e.ledger()["balances"].items() if a != pair.quote)
            and e.settings["pair"] != pair.id
        ):
            raise SafetyError("Existing owned inventory prevents changing the execution instrument")
        # This is an explicit execution-market selection, never chart browsing.
        e.settings.update(pair=pair.id, quote=pair.quote)
        e.store.put("settings", e.settings)
        e.fee_scope = [pair]
        await e.client.market_data.configure([pair], max_age=e.settings["stale_seconds"])
        e.emit_state()  # Selection persists even if a later read-only preflight gate fails.
        allowance = values.get("demo_fee_allowance_bps")
        allowance = None if allowance in (None, "") else str(dec(allowance))
        if allowance is not None and (kind != "execution_cycle" or e.client.environment != "demo"):
            raise SafetyError(
                "A manual planning allowance is available only for the demo diagnostic, never live/TWAP"
            )
        fee = await fees(e, pair, allowance)
        proof = await e.probe()
        if binding:
            await e.settle()
        config = proof["config"]
        if not config["can_trade"] or config["fee_type"] not in {"0", "1"}:
            raise SafetyError(
                "OKX preflight requires native Trade permission and feeType 0/1; nothing submitted; inspect key permissions/account fee policy"
            )
        clock_rows = await e.client.request("GET", "/api/v5/public/time")
        if len(clock_rows) != 1:
            raise SafetyError("OKX server clock unavailable; nothing submitted")
        skew = abs(e.clock() - native.native_time(clock_rows[0]["ts"]) / 1000)
        if skew > 2:
            raise SafetyError(
                f"OKX server/local clock difference {skew:.3f}s exceeds 2s; nothing submitted; synchronize the server clock"
            )
        # Account reads can outlast an index update. Wait after those reads,
        # immediately before deriving and checking the preview's current prices.
        await e.client.wait_limit_reference(
            pair,
            e.settings["stale_seconds"],
            lambda: e.stop_generation == generation and not e.shutting_down,
        )
        book = await e.client.book(pair)
        book.fresh(e.settings["stale_seconds"])
        if book.spread_bps > dec(e.settings["max_spread_bps"]):
            raise SafetyError(
                f"OKX spread {book.spread_bps}bps exceeds {e.settings['max_spread_bps']}bps; nothing submitted; wait for acceptable native liquidity"
            )
        allocation = dec(
            values.get(
                "allocation",
                binding["allocation"]
                if binding
                else e.settings["paper_balance"]
                if e.mode == "paper"
                else e.settings["live_budget"],
            )
        )
        available = dec(proof["balance"]["assets"].get(pair.quote, {}).get("available", 0))
        if (
            allocation <= 0
            or (not binding and allocation > available)
            or binding
            and allocation != dec(binding["allocation"])
        ):
            raise SafetyError(
                f"OKX requested allocation {allocation} {pair.quote}, available {available}, existing allocation {binding['allocation'] if binding else 'unbound'}; nothing submitted; choose an affordable initial allocation without changing a bound ledger"
            )
        duration = values.get(
            "duration_seconds",
            180 if kind == "execution_cycle" else e.settings["twap_duration_seconds"],
        )
        attempts = values.get("max_exit_attempts", 1)
        if (
            type(duration) is not int
            or not 30 <= duration <= 86400
            or kind == "execution_cycle"
            and duration > 900
        ):
            raise SafetyError(
                "OKX duration must be 30–900 seconds for a cycle or at most one day for TWAP"
            )
        if type(attempts) is not int or not 1 <= attempts <= 3:
            raise SafetyError("OKX cycle requires 1–3 explicitly disclosed maximum exit attempts")
        buy_ceiling = (
            pair.price(dec(values["buy_ceiling"]), "buy")
            if values.get("buy_ceiling") not in (None, "")
            else limit_price(book, "buy", e.settings["slippage_bps"])
        )
        sell_floor = (
            pair.price(dec(values["sell_floor"]), "sell")
            if values.get("sell_floor") not in (None, "")
            else limit_price(book, "sell", e.settings["slippage_bps"])
        )
        budget = dec(values.get("budget", e.settings["order_size"]))
        quantity = ZERO
        terms = None
        if kind == "execution_cycle":
            if budget <= 0 or budget > min(
                allocation, dec(e.settings["order_size"]), dec(e.settings["max_exposure"])
            ):
                raise SafetyError(
                    "OKX cycle budget exceeds the allocation/order/exposure cap or is not positive; nothing submitted; reduce the explicit budget"
                )
            if (
                buy_ceiling < book.asks[0][0]
                or sell_floor > book.bids[0][0]
                or min(buy_ceiling, sell_floor) <= 0
            ):
                raise SafetyError(
                    "OKX absolute buy/sell bounds are not currently marketable; nothing submitted; review the disclosed bounds"
                )
            rate = fee / BPS
            minimum = (
                (pair.minimum * (1 + rate) / (1 - rate) + pair.lot) / pair.lot
            ).to_integral_value(rounding=ROUND_UP) * pair.lot
            required = minimum * buy_ceiling * (1 + rate)
            if budget < required:
                raise SafetyError(
                    f"OKX minimum expected sellable cycle requires {required} {pair.quote}; authorized budget {budget}; nothing submitted; explicitly choose a sufficient budget within risk caps"
                )
            quantity = floor(budget / (buy_ceiling * (1 + rate)), pair.lot)
            expected_exit = floor(quantity * (1 - rate) / (1 + rate), pair.lot)
            if (
                book.fill("buy", quantity, buy_ceiling)[0] < quantity
                or book.fill("sell", expected_exit, sell_floor)[0] < expected_exit
            ):
                raise SafetyError(
                    "OKX visible native book lacks liquidity for the proposed entry/exit; reduce budget or wait; nothing submitted"
                )
            pair.validate(quantity, buy_ceiling)
            expected = floor(quantity * (1 - rate), pair.lot)
            pair.validate(expected, sell_floor)
            buy_attempts, sell_attempts = 1, attempts
        else:
            e.validate_capabilities(e.settings)
            programs.validate_markets(e.settings, e.resolve)
            if allowance is not None:
                raise SafetyError("TWAP requires authenticated account fee rates")
            quantity = dec(e.settings["twap_quantity"])
            side = e.settings["twap_side"]
            parent = pair.price(dec(e.settings["twap_limit"]), side)
            if (side == "buy" and parent < book.asks[0][0]) or (
                side == "sell" and parent > book.bids[0][0]
            ):
                raise SafetyError(
                    f"OKX saved TWAP parent {parent} is not currently marketable against bid {book.bids[0][0]} / ask {book.asks[0][0]}; nothing submitted; save explicit marketable limits or wait"
                )
            saved_program = e.store.get(programs.key(e))
            if saved_program and (
                saved_program["status"] == "complete"
                or saved_program["config"] != programs.configuration(e.settings)
            ):
                raise SafetyError(
                    "OKX saved TWAP is complete or its settings changed; explicitly create a New strategy run before requesting another authorization"
                )
            buy_ceiling = parent if side == "buy" else buy_ceiling
            sell_floor = parent if side == "sell" else sell_floor
            budget = quantity * buy_ceiling * (1 + fee / BPS) if side == "buy" else ZERO
            if (
                side == "buy"
                and budget > (e.balance(pair.quote) if binding else allocation)
                or side == "sell"
                and quantity > e.balance(pair.base)
            ):
                raise SafetyError(
                    "OKX TWAP parent exceeds allocated cash or bot-owned inventory; pre-existing account assets are not owned"
                )
            buy_attempts, sell_attempts = (
                (e.settings["twap_slices"], 0) if side == "buy" else (0, e.settings["twap_slices"])
            )
            duration = e.settings["twap_duration_seconds"]
            if duration > 86400:
                raise SafetyError("Initial OKX TWAP authorization is limited to one day")
            terms = programs.configuration(e.settings)
        limit_quantity, reference_price = quantity, buy_ceiling
        if kind == "twap":
            slices = e.settings["twap_slices"]
            per_slice = floor(quantity / slices, pair.lot)
            limit_quantity = floor(quantity - per_slice * (slices - 1), pair.lot)
            reference_price = parent
        venue_limits = await e.client.limit_order_check(
            pair, limit_quantity, reference_price, e.settings["stale_seconds"]
        )
        if e.stop_generation != generation or e.shutting_down:
            raise SafetyError("OKX preview canceled by Stop")
        proposal = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "environment": e.client.environment,
            "account": "••••" + config["uid"][-4:],
            "account_scope": fingerprint(
                {"uid": config["uid"], "environment": e.client.environment}
            ),
            "pair": pair.id,
            "instrument": e.client.instruments[pair.id]["instId"],
            "base": pair.base,
            "spending_currency": pair.quote,
            "allocation": str(allocation),
            "budget": str(budget),
            "quantity": str(quantity),
            "fee_bps": str(fee),
            "fee_source": e.planning_source,
            "venue_limit_preview": venue_limits,
            "demo_fee_allowance_bps": allowance,
            "buy_ceiling": str(buy_ceiling),
            "sell_floor": str(sell_floor),
            "slippage_bps": e.settings["slippage_bps"],
            "attempts": {"buy": buy_attempts, "sell": sell_attempts},
            "duration_seconds": duration,
            "created_at": e.clock(),
            "expires_at": e.clock() + 120,
            "generation": generation,
            "settings_id": e.settings_id(),
            "market_rules": fingerprint(e.client.instruments[pair.id]),
            "program_terms": terms,
            "price_check": {
                **programs.price_check(book, side, parent),
                "price_currency": venue_limits["price_currency"],
            }
            if kind == "twap"
            else None,
            "write_gate": e.client.allow_writes,
            "confirmation": confirmation(e, kind),
            "claimed": False,
            "dust_policy": "Sell only actual acquired/owned quantity rounded down to the venue lot. Report every residual and its native-book value estimate. Never buy extra or erase dust; holdings can continue to block venue switching.",
        }
        e.store.put("okx-preview:" + proposal["id"], proposal)
        e.emit_state()
        return proposal


def record(e, operation):
    e.store.put("okx-operation", operation)
    e.store.put("okx-operation:" + operation["id"], operation)
    e.event("okx-operation", operation)
    e.emit_state()


async def authorize(e, preview_id, phrase):
    idle(e)
    async with e.lock:
        idle(e)
        proposal = e.store.get("okx-preview:" + str(preview_id))
        if not proposal or proposal["claimed"] or phrase != confirmation(e, proposal["kind"]):
            raise SafetyError(
                "OKX requires the exact unused finite preview and its explicit confirmation"
            )
        if (
            proposal["environment"] != e.client.environment
            or e.clock() >= proposal["expires_at"]
            or proposal["generation"] != e.stop_generation
            or proposal["settings_id"] != e.settings_id()
            or not e.client.allow_writes
        ):
            raise SafetyError(
                "OKX preview expired, changed, revoked, or server write gate disabled; prepare a new preview"
            )
        proposal["claimed"] = True
        e.store.put("okx-preview:" + proposal["id"], proposal)
        operation = {
            **proposal,
            "status": "authorized",
            "started_at": e.clock(),
            "phase": "preflight",
            "submitted": False,
        }
        record(e, operation)
        try:
            await e.client.catalog()
            pair = e.resolve(proposal["pair"])
            if fingerprint(e.client.instruments[pair.id]) != proposal["market_rules"]:
                raise SafetyError(
                    "OKX instrument rules changed after preview; no submission; prepare a new authorization"
                )
            fee = await fees(e, pair, proposal["demo_fee_allowance_bps"])
            if fee > dec(proposal["fee_bps"]):
                raise SafetyError(
                    "OKX planning fees increased after authorization; no submission; re-preview the cost"
                )
            proof = await e.probe()
            if (
                fingerprint({"uid": proof["config"]["uid"], "environment": e.client.environment})
                != proposal["account_scope"]
            ):
                raise SafetyError("OKX preview belongs to a different account/environment")
            if e.store.get("okx-account"):
                await e.settle()
            if e.stop_generation != proposal["generation"] or e.shutting_down:
                raise SafetyError("OKX authorization canceled by Stop before execution")
            e.bind(proof, pair, proposal["allocation"])
            operation.update(
                status="running",
                phase="entry" if proposal["kind"] == "execution_cycle" else "scheduled",
                opening_ledger=e.ledger(),
                deadline=e.clock() + proposal["duration_seconds"],
            )
            e.authorization = {**operation, "deadline": operation["deadline"]}
            e.authorization_monotonic_deadline = time.monotonic() + proposal["duration_seconds"]
            e.armed = e.running = True
            e.execution_purpose = (
                "execution_cycle" if proposal["kind"] == "execution_cycle" else "finite_twap"
            )
            await e.valuation(enforce=True)
            e.ensure_run()
            if proposal["kind"] == "twap":
                programs.prepare(e)
            record(e, operation)
            if proposal["kind"] == "execution_cycle":
                e.operation_task = asyncio.create_task(run(e, operation))
        except Exception as exc:
            e.running, e.armed, e.authorization = False, False, None
            operation.update(
                status="PREFLIGHT_BLOCKED",
                message=str(exc)
                if isinstance(exc, SafetyError)
                else "OKX preflight failed; no new order authorized",
                ended_at=e.clock(),
            )
            record(e, operation)
            raise
        return operation


def children(e, operation):
    return [o for o in e.orders() if o.get("authorization_id") == operation["id"]]


def owned(e, operation):
    return sum(
        (
            dec(x["quantity"]) * (1 if order["side"] == "buy" else -1)
            + (dec(x["fee_signed"]) if x["fee_currency"] == order["base"] else ZERO)
            for order in children(e, operation)
            for x in order.get("executions", [])
        ),
        ZERO,
    )


def execution_evidence(e, operation):
    """Current owned-order facts; reading them never rewrites the original report."""
    orders = children(e, operation)
    fees_by_currency = {}
    for order in orders:
        for asset, amount in order.get("fees", {}).items():
            fees_by_currency[asset] = str(dec(fees_by_currency.get(asset, 0)) + dec(amount))
    entry_debit = sum(
        (
            dec(o["cost"]) + dec(o.get("fees", {}).get(o["quote"], 0))
            for o in orders
            if o["side"] == "buy"
        ),
        ZERO,
    )
    return dict(
        entry_debit=str(entry_debit),
        counts=programs.order_totals(orders),
        submitted=(
            True
            if any(
                o.get("txid") or o.get("write_outcome") in {"accepted", "rejected"} for o in orders
            )
            else "unknown"
            if any(o.get("write_outcome") == "unknown" for o in orders)
            else False
        ),
        orders=[
            {
                "id": o["id"],
                "broker_id": o["txid"],
                "side": o["side"],
                "status": o["status"],
                "write_outcome": o.get("write_outcome"),
                "venue_limits": o.get("venue_limits"),
                "filled": o["filled"],
                "executions": o.get("executions", []),
            }
            for o in orders
        ],
        fees=fees_by_currency,
    )


async def report(e, operation, outcome, message):
    e.running, e.armed, e.authorization = False, False, None
    evidence = execution_evidence(e, operation)
    residual = (
        owned(e, operation)
        if operation["kind"] == "execution_cycle"
        else e.balance(operation["base"])
    )
    book = e.client.market_data.books.get(operation["pair"])
    try:
        if book:
            book.fresh(e.settings["stale_seconds"])
        estimate = str(residual * book.bids[0][0]) if book else None
    except SafetyError:
        estimate = None
    entry_debit = dec(evidence["entry_debit"])
    if outcome in {"PASSED", "PASSED_WITH_DUST", "TWAP_COMPLETE"} and entry_debit > dec(
        operation["budget"]
    ):
        outcome = "PARTIAL"
        message = f"Actual entry debit {entry_debit} {operation['spending_currency']} exceeded authorized budget {operation['budget']}; native fees retained, accounting reconciled but operational bounds failed; no repeat authorized"
    operation.update(
        **evidence,
        status=outcome,
        phase="stopped",
        message=message,
        ended_at=e.clock(),
        residual=str(residual),
        residual_value_estimate=estimate,
        value_currency=operation["spending_currency"],
        closing_ledger=e.ledger(),
        reconciliation=e.store.get("okx-reconciliation"),
    )
    if operation["kind"] == "twap":
        program = programs.snapshot(e)
        operation["program_execution"] = program["execution"] if program else None
    e.last_error = (
        None
        if outcome in {"PASSED", "PASSED_WITH_DUST", "NO_FILL", "TWAP_COMPLETE", "STOPPED"}
        else message
    )
    record(e, operation)


async def run(e, operation):
    async with e.lock:
        outcome, message = "PREFLIGHT_BLOCKED", "No order submitted"
        try:
            pair = e.resolve(operation["pair"])
            async with asyncio.timeout(max(0.001, operation["deadline"] - e.clock())):
                e.permission()
                await e.client.wait_limit_reference(pair, e.settings["stale_seconds"], e.permission)
                book = await e.client.book(pair)
                price = limit_price(
                    book, "buy", e.settings["slippage_bps"], parent=dec(operation["buy_ceiling"])
                )
                buy = await e.place(pair, "buy", dec(operation["quantity"]), price, book)
                operation.update(phase="entry confirmed", submitted=True)
                record(e, operation)
                if dec(buy["filled"]) <= 0:
                    outcome, message = (
                        "NO_FILL",
                        "Buy was terminal with no execution; no sale or automatic repeat",
                    )
                else:
                    for attempt in range(operation["attempts"]["sell"]):
                        e.permission()
                        await fees(e, pair, operation["demo_fee_allowance_bps"])
                        await e.settle()
                        await e.client.wait_limit_reference(
                            pair, e.settings["stale_seconds"], e.permission
                        )
                        book = await e.client.book(pair)
                        price = limit_price(
                            book,
                            "sell",
                            e.settings["slippage_bps"],
                            parent=dec(operation["sell_floor"]),
                        )
                        quantity = floor(min(owned(e, operation), e.balance(pair.base)), pair.lot)
                        if quantity < pair.minimum or quantity * price < pair.cost_minimum:
                            break
                        operation.update(phase=f"exit attempt {attempt + 1}")
                        record(e, operation)
                        await e.place(pair, "sell", quantity, price, book)
                        if owned(e, operation) == 0:
                            break
                    operation["phase"] = "reconciliation"
                    record(e, operation)
                    await e.settle()
                    await e.valuation(enforce=False)
                    sells = sum(
                        (dec(o["filled"]) for o in children(e, operation) if o["side"] == "sell"),
                        ZERO,
                    )
                    residual = owned(e, operation)
                    if sells <= 0:
                        outcome, message = (
                            "PARTIAL",
                            "Buy executed but no positive sell execution; acquired inventory retained",
                        )
                    elif residual == 0:
                        outcome, message = (
                            "PASSED",
                            "Positive buy and sell, terminal orders, native fees and two matched accounting reads; stopped",
                        )
                    elif (
                        ZERO < floor(residual, pair.lot) < pair.minimum
                        or ZERO < residual < pair.lot
                    ):
                        outcome, message = (
                            "PASSED_WITH_DUST",
                            "Positive buy and sell reconciled; exact unsellable residual retained and disclosed; not complete liquidation",
                        )
                    else:
                        outcome, message = (
                            "PARTIAL",
                            "Exit attempt limit reached with residual inventory; no cleanup buy or automatic rearming",
                        )
        except asyncio.CancelledError:
            outcome = (
                "UNKNOWN"
                if any(o["status"] in {"submitting", "uncertain"} for o in children(e, operation))
                else "PARTIAL"
            )
            message = (
                "Stop/shutdown revoked permission; reconcile original intents and retain inventory"
            )
        except Exception as exc:
            orders = children(e, operation)
            if any(o["status"] in {"submitting", "uncertain"} for o in orders):
                outcome = "UNKNOWN"
            elif any(o["status"] not in TERMINAL for o in orders) or e.recovery_required:
                outcome = "RECONCILIATION_PENDING"
            elif any(dec(o["filled"]) > 0 for o in orders):
                outcome = "PARTIAL"
            message = (
                str(exc)
                if isinstance(exc, SafetyError)
                else "OKX finite execution interrupted/expired; inspect retained native evidence; no automatic retry"
            )
        finally:
            await report(e, operation, outcome, message)
            e.operation_task = None
