"""Isolated, explicitly authorized OKX cash execution on the normal Engine path."""

import asyncio
import hashlib
import json
import time

from kairos import diagnostics, programs
from kairos import okx_account as native
from kairos.clients import ExchangeRejected
from kairos.domain import BPS, TERMINAL, ZERO, SafetyError, dec
from kairos.engine import Engine
from kairos.okx import OKXBeforeSend, OKXRejected, PendingOKX, PendingOKXBook
from kairos.okx_reconciliation import bill_evidence
from kairos.settings import DEFAULTS, validate_settings
from kairos.strategies import limit_price

HTF_BOOK_WAIT_SECONDS = 60


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def history_identity(row):
    return {
        k: row.get(k)
        for k in (
            "instId",
            "ordId",
            "clOrdId",
            "side",
            "state",
            "accFillSz",
            "fee",
            "feeCcy",
            "rebate",
            "rebateCcy",
            "tradeQuoteCcy",
        )
    }


class OKXEngine(Engine):
    def __init__(self, store, client, jev, publish, *, clock=None):
        if store.get("settings") is None:
            store.put(
                "settings",
                {
                    **DEFAULTS,
                    "strategy": "twap",
                    "twap_limit": "1",  # Inert default: no instrument or finite permission.
                    "product": "spot",
                    "pair": "",
                    "quote": "USD",
                    "paper_balance": "500",
                    "live_budget": "500",
                    "order_size": "25",
                    "max_exposure": "100",
                    "daily_loss": "12.50",
                    "reinvest_profits": False,
                    "recover_initial": False,
                },
            )
        super().__init__(store, client, jev, publish, clock=clock)
        self.exchange = client.exchange
        self.mode = "paper" if client.environment == "demo" else "trading"
        self.armed = False
        self.authorization = None
        self.authorization_monotonic_deadline = None
        self.operation_task = None
        self.account_info = None
        self.account_snapshot = None
        self.account_checked = 0
        self.recovery_required = bool(self.orders(active=True))
        self.client = client
        client.write_guard = self.write_guard

    def resolve(self, name):
        return self.client.resolve(name)  # No symbol-only/other-currency fallback.

    def validate_capabilities(self, values):
        if (
            values["product"] != "spot"
            or self.client.environment != "demo"
            and values["strategy"] != "twap"
            or values["recover_initial"]
            or values["reinvest_profits"]
            or values["api_auto_recovery"]
        ):
            raise SafetyError(
                "OKX supports cash spot only: demo finite strategies; live cycle/TWAP only. Compounding, automatic recovery and capital recovery are unavailable"
            )

    async def initialize(self):
        self.running, self.armed, self.authorization = False, False, None
        current = self.store.get("okx-operation")
        if current and current.get("status") in {"running", "authorized"}:
            current.update(
                status="interrupted",
                message="Restart revoked permission; reconcile original intents. No operation is replayed.",
            )
            self.store.put("okx-operation", current)
            self.store.put("okx-operation:" + current["id"], current)
        try:
            if not self.client.configured:
                raise SafetyError(
                    f"OKX {self.client.environment} credentials missing; configure this environment privately; no orders submitted"
                )
            # Retire owned resting quotes even if their former market was delisted
            # or the data service cannot restart. This grants no new-order permission.
            if self.store.get("okx-account"):
                await self.cancel_active()
            await self.client.catalog()
            if self.settings["pair"]:
                await self.client.market_data.configure(
                    self.fee_pairs(), max_age=self.settings["stale_seconds"]
                )
                await self.refresh_fees(required=False)
            if self.store.get("okx-account"):
                await self.reconcile_account()
        except Exception as exc:
            self.last_error = (
                str(exc)
                if isinstance(exc, SafetyError)
                else "OKX initialization evidence unavailable; remain stopped"
            )
            self.recovery_required = bool(self.store.get("okx-account"))
        self.ready = True
        self.event(
            "system",
            {
                "message": f"OKX {self.client.environment} ready for configuration; paused and unarmed"
            },
        )
        self.emit_state()

    async def read_account(self):
        config = native.account(await self.client.request("GET", "/api/v5/account/config"))
        binding = self.store.get("okx-account")
        if binding and (
            binding["uid"] != config["uid"]
            or binding["environment"] != self.client.environment
            or binding["venue"] != "okx-us"
        ):
            raise SafetyError(
                "OKX account/venue/environment differs from the persisted binding; no inheritance, reset, or submission permitted"
            )
        begin = str(int((binding["bound_at"] if binding else self.clock() - 7 * 86400) * 1000))
        pending = await self.client.pages("/api/v5/trade/orders-pending", cursor_key="ordId")
        history = await self.client.pages(
            "/api/v5/trade/orders-history", cursor_key="ordId", instType="SPOT", begin=begin
        )
        bills_path = (
            "/api/v5/account/bills-archive"
            if binding and self.clock() - binding["bound_at"] >= 6 * 86400
            else "/api/v5/account/bills"
        )
        bills = await self.client.pages(bills_path, cursor_key="billId", begin=begin)
        balance = native.balances(await self.client.request("GET", "/api/v5/account/balance"))
        return {
            "config": config,
            "balance": balance,
            "pending": pending,
            "history": history,
            "bills": bills,
            "received_at": self.clock(),
        }

    async def probe(self):
        try:
            async with asyncio.timeout(30):
                return await self._probe()
        except TimeoutError:
            raise PendingOKX(
                "OKX preflight exceeded 30s; nothing submitted; inspect endpoint latency and account history"
            ) from None

    async def _probe(self):
        first = await self.read_account()
        second = await self.read_account()
        if (
            first["config"] != second["config"]
            or first["balance"]["assets"] != second["balance"]["assets"]
            or first["pending"]
            or second["pending"]
        ):
            raise PendingOKX(
                "OKX preflight account changed or has working orders; required: two stable reads and no unrelated working orders; nothing submitted; inspect account activity"
            )
        if {r["ordId"]: history_identity(r) for r in first["history"]} != {
            r["ordId"]: history_identity(r) for r in second["history"]
        } or {r["billId"] for r in first["bills"]} != {r["billId"] for r in second["bills"]}:
            raise PendingOKX(
                "OKX preflight history changed between reads; nothing submitted; wait for account activity to settle"
            )
        self.account_info, self.account_snapshot = second["config"], second["balance"]
        self.account_checked = self.clock()
        return second

    def bind(self, proof, pair, allocation):
        allocation = dec(allocation)
        existing = self.store.get("okx-account")
        if existing:
            if (
                existing["uid"] != proof["config"]["uid"]
                or existing["quote"] != pair.quote
                or dec(existing["allocation"]) != allocation
            ):
                raise SafetyError(
                    "OKX allocation/account/currency is already bound; no automatic replacement or funding adjustment"
                )
            return
        available = dec(proof["balance"]["assets"].get(pair.quote, {}).get("available", 0))
        if allocation <= 0 or allocation > available:
            raise SafetyError(
                f"OKX allocation {allocation} {pair.quote} exceeds available {available}; nothing submitted; reduce the explicit allocation or use official account funding"
            )
        binding = {
            "venue": "okx-us",
            "environment": self.client.environment,
            "uid": proof["config"]["uid"],
            "quote": pair.quote,
            "allocation": str(allocation),
            "bound_at": self.clock(),
            "baseline": {a: str(v) for a, v in native.totals(proof["balance"]).items()},
            "history": {r["ordId"]: history_identity(r) for r in proof["history"]},
            "bills": [r["billId"] for r in proof["bills"]],
        }
        ledger = {
            "balances": {pair.quote: str(allocation)},
            "initial": str(allocation),
            "fees": {},
            "account_delta": {},
            "recovery": {
                "original": str(allocation),
                "reserved": "0",
                "recovered": False,
                "pending": False,
                "last_check": 0,
            },
        }
        with self.store.db:
            self.store._put("okx-account", binding)
            self.store._put("ledger:" + self.mode, ledger)
        self.account_checked = self.clock()

    async def refresh_order(self, order):
        params = {"instId": order["instrument"]}
        params.update(
            {"ordId": order["txid"]} if order.get("txid") else {"clOrdId": order["client_id"]}
        )
        row = None
        try:
            rows = await self.client.request("GET", "/api/v5/trade/order", params=params)
            if len(rows) != 1:
                raise PendingOKX(
                    "OKX individual order is not yet visible; original intent remains unresolved"
                )
            row = rows[0]
            updated = native.order_observation(order, row)
            order.clear()
            order.update(updated)
            self.store.save_order(order)
        except OKXRejected as exc:
            if exc.code != "51603":
                raise
        fill_params = {"instType": "SPOT", "instId": order["instrument"]}
        if order.get("txid"):
            fill_params["ordId"] = order["txid"]
        fills_path = (
            "/api/v5/trade/fills-history"
            if self.clock() - order["created"] >= 2 * 86400
            else "/api/v5/trade/fills"
        )
        fills = await self.client.pages(fills_path, cursor_key="billId", **fill_params)
        fills = [
            r
            for r in fills
            if (order.get("txid") and r.get("ordId") == order["txid"])
            or r.get("clOrdId") == order["client_id"]
        ]
        updated, added = native.apply_executions(
            self.store, order, fills, self.store.get("okx-account")
        )
        if row:
            updated = native.order_observation(updated, row)
        elif updated.get("exchange_status") in TERMINAL and dec(
            updated.get("broker_filled", -1)
        ) == dec(updated["filled"]):
            updated["status"] = updated["exchange_status"]
            updated["fee_reported"] = True
        order.clear()
        order.update(updated)
        self.store.save_order(order)
        for execution in added:
            self.event(
                "fill",
                {
                    "order_id": order["id"],
                    "side": order["side"],
                    "volume": execution["quantity"],
                    "cost": execution["cost"],
                    "fee": str(-dec(execution["fee_signed"])),
                    "fee_signed": execution["fee_signed"],
                    "fee_currency": execution["fee_currency"],
                    "broker_execution_id": execution["bill_id"],
                    "source_time": execution["fill_time"],
                    "simulated": False,
                    "hosted_demo": self.client.environment == "demo",
                },
            )
        return order

    async def reconcile_account(self):
        binding = self.store.get("okx-account")
        if binding is None:
            raise SafetyError("OKX account is not explicitly allocated/bound")
        proof = await self.read_account()
        by_client = {o.get("client_id"): o for o in self.orders()}
        by_broker = {o["txid"]: o for o in self.orders() if o.get("txid")}
        observed = {r["ordId"]: r for r in proof["history"] + proof["pending"]}
        owned_observations = {}
        for identifier, row in observed.items():
            owned = by_broker.get(identifier) or by_client.get(row.get("clOrdId"))
            if owned is not None:
                if (
                    owned.get("txid") not in (None, identifier)
                    or owned["id"] in owned_observations
                    and owned_observations[owned["id"]]["ordId"] != identifier
                ):
                    raise SafetyError(
                        "Duplicate OKX broker orders share one client intent; no new submissions"
                    )
                owned_observations[owned["id"]] = row
            if owned is None and binding["history"].get(identifier) != history_identity(row):
                raise SafetyError(
                    "External OKX order/activity differs from the account baseline; no automatic adoption or cancellation"
                )
        for order in self.orders():
            row = owned_observations.get(order["id"])
            changed = row and native.native_time(row["uTime"]) > order.get("broker_updated", 0)
            if row:
                order = native.order_observation(order, row)
                self.store.save_order(order)
            if order["status"] not in TERMINAL or changed:
                await self.refresh_order(order)
        owned_ids = {o["txid"] for o in self.orders() if o.get("txid")}
        old_bills = set(binding["bills"])
        for bill in proof["bills"]:
            if bill["billId"] not in old_bills and (
                bill.get("ordId") not in owned_ids or bill.get("type") != "2"
            ):
                raise SafetyError(
                    "External OKX bill/funding/account activity requires investigation; no fabricated funding or balance correction"
                )
        balance = native.balances(await self.client.request("GET", "/api/v5/account/balance"))
        differences = native.mismatch(binding, self.ledger(), balance)
        pending = self.orders(active=True)
        evidence = None
        if differences and not pending and not proof["pending"]:
            evidence = bill_evidence(
                binding, self.ledger(), self.orders(), proof["bills"], balance, differences
            )
            if evidence:
                # No cache: a second complete native account/bill read is required.
                second = await self.read_account()
                if (
                    proof["config"] != second["config"]
                    or second["pending"]
                    or proof["balance"]["assets"] != balance["assets"]
                    or balance["assets"] != second["balance"]["assets"]
                    or {r["ordId"]: r for r in proof["history"]}
                    != {r["ordId"]: r for r in second["history"]}
                    or {r["billId"]: r for r in proof["bills"]}
                    != {r["billId"]: r for r in second["bills"]}
                    or bill_evidence(
                        binding,
                        self.ledger(),
                        self.orders(),
                        second["bills"],
                        second["balance"],
                        differences,
                    )
                    != evidence
                ):
                    raise PendingOKX(
                        "OKX bill/summary proof changed between independent reads; no accounting approval or new order"
                    )
                previous = self.store.get("okx-reconciliation") or {}
                evidence["original_pending_since"] = previous.get(
                    "since", (previous.get("bill_proof") or {}).get("original_pending_since")
                )
                evidence["read_received_at"] = [proof["received_at"], second["received_at"]]
                proof, balance, differences = second, second["balance"], {}
        self.account_info, self.account_snapshot = proof["config"], balance
        issue = {
            "differences": differences,
            "unresolved_orders": [o["id"] for o in pending],
            "checked_at": self.clock(),
        }
        if evidence:
            issue["bill_proof"] = evidence
        if differences or pending:
            previous = self.store.get("okx-reconciliation") or {}
            issue["since"] = previous.get("since", self.clock())
            self.store.put("okx-reconciliation", issue)
            self.recovery_required = True
            detail = (
                f"balance differences {json.dumps(differences, sort_keys=True)}"
                if differences
                else "original order execution/fee/terminal evidence missing"
            )
            if self.clock() - issue["since"] >= 45:
                raise SafetyError(
                    f"OKX reconciliation exceeded 45s: {detail}; trading remains blocked; investigate native evidence, never reset balances"
                )
            raise PendingOKX(
                f"OKX reconciliation pending: {detail}; no new order until evidence agrees"
            )
        if any(dec(v) < 0 for v in self.ledger()["balances"].values()) or any(
            dec(v) for v in self.ledger().get("unallocated_fee_effects", {}).values()
        ):
            raise SafetyError(
                "OKX execution affected unallocated assets or exceeded owned inventory; exact native fees retained, no further trading"
            )
        self.store.put("okx-reconciliation", {**issue, "status": "matched"})
        self.account_checked = self.clock()
        self.recovery_required = False
        return proof

    async def settle(self, seconds=45):
        try:
            async with asyncio.timeout(seconds):
                return await self._settle(seconds)
        except TimeoutError:
            raise SafetyError(
                "OKX bounded reconciliation exhausted; retain holdings and original intents; no new submission"
            ) from None

    async def _settle(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            try:
                first = await self.reconcile_account()
                second = await self.reconcile_account()
                if first["config"] != second["config"]:
                    raise SafetyError("OKX identity changed across reconciliation reads")
                return second
            except PendingOKX:
                if time.monotonic() >= deadline:
                    raise SafetyError(
                        "OKX bounded reconciliation exhausted; retain holdings and original intents; no new submission"
                    ) from None
                await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))

    async def valuation(self, enforce=False):
        if not self.ledger():
            return {self.settings["quote"]: dec(1)}, ZERO
        quote = self.settings["quote"]
        prices = {quote: dec(1)}
        exposure = ZERO
        selected = self.fee_pairs()
        for asset, quantity in self.ledger()["balances"].items():
            if asset == quote or not dec(quantity):
                continue
            pair = next((p for p in selected if p.base == asset and p.quote == quote), None)
            if pair is None:
                raise SafetyError(
                    "Owned asset lacks a selected native valuation market; retain inventory"
                )
            book = await self.client.book(pair)
            book.fresh(self.settings["stale_seconds"])
            prices[asset] = book.mid
            exposure += dec(quantity) * book.mid
        self.record_valuation(self.balance(quote) + exposure, exposure, "day:" + self.mode)
        if enforce and -dec(self.daily_pnl) >= dec(self.settings["daily_loss"]):
            raise SafetyError("Daily marked-to-market loss limit reached; no further orders")
        return prices, exposure

    def limits(self):
        cap, exposure = super().limits()
        scope = getattr(self, "authorization", None)
        if scope and scope["kind"] == "strategy":
            fee = max(dec(row["fee_bps"]) for row in scope["markets"].values())
            cap = min(cap, dec(scope["budget"]) / (1 + fee / BPS))
        return cap, exposure

    def active_run(self):
        run = super().active_run()
        scope = getattr(self, "authorization", None)
        if run and scope and scope["kind"] == "strategy":
            # Reuse signal lifecycle bounds, not the frozen Alpaca trial or its state.
            return {**run, "trial_ends_at": scope["deadline"], "protocol": scope["protocol"]}
        return run

    def permission(self):
        if self.mode != ("paper" if self.client.environment == "demo" else "trading"):
            raise SafetyError(
                "OKX mode/environment mismatch; local simulated execution is prohibited"
            )
        scope = self.authorization
        if (
            not self.running
            or not self.armed
            or self.shutting_down
            or not scope
            or not self.client.allow_writes
            or self.clock() >= scope["deadline"]
            or self.authorization_monotonic_deadline is None
            or time.monotonic() >= self.authorization_monotonic_deadline
            or scope["generation"] != self.stop_generation
            or scope["settings_id"] != self.settings_id()
        ):
            raise SafetyError(
                "OKX finite permission absent, expired or revoked; nothing submitted; explicitly authorize a new finite run"
            )
        if (
            scope["environment"] != self.client.environment
            or scope["pair"] != self.settings["pair"]
        ):
            raise SafetyError("OKX authorization environment/instrument mismatch")
        return scope

    def write_guard(self, path, payload):
        binding = self.store.get("okx-account")
        if (
            not binding
            or not self.account_info
            or binding["uid"] != self.account_info["uid"]
            or not 0 <= self.clock() - self.account_checked < 30
        ):
            raise SafetyError(
                "OKX account verification missing/stale at transport boundary; no request sent"
            )
        if path.endswith("/cancel-order"):
            return any(
                o.get("txid") == payload["ordId"]
                and o.get("instrument") == payload["instId"]
                and o.get("exchange_status") == "open"
                and o.get("cancel_state") == "submitting"
                for o in self.orders(active=True)
            )
        scope = self.permission()
        matches = [o for o in self.orders() if o.get("client_id") == payload["clOrdId"]]
        if (
            len(matches) != 1
            or matches[0]["status"] != "submitting"
            or matches[0].get("payload") != payload
            or matches[0].get("authorization_id") != scope["id"]
        ):
            return False
        order = matches[0]
        evidence = order.get("venue_limits")
        if (
            not evidence
            or evidence["quantity"] != order["volume"]
            or evidence["price"] != order["price"]
        ):
            raise SafetyError("OKX order lacks matching native limit evidence; nothing submitted")
        self.client.check_limit_reference(evidence, self.settings["stale_seconds"])
        if any(o["id"] != order["id"] for o in self.orders(active=True)):
            raise SafetyError("OKX permits one unresolved intent at a time")
        pair = self.resolve(order["pair"])
        book = self.client.market_data.books.get(pair.id)
        if book is None:
            raise SafetyError(
                "OKX same-environment execution book unavailable at transport boundary"
            )
        book.fresh(self.settings["stale_seconds"])
        self.check_scope(
            order["side"],
            dec(order["volume"]),
            dec(order["price"]),
            include_intent=order["id"],
            pair=pair,
        )
        return True

    def check_scope(self, side, volume, price, *, include_intent=None, pair=None):
        scope = self.permission()
        if scope["kind"] == "strategy":
            from kairos.okx_strategy import check

            return check(
                self, pair or self.resolve(scope["pair"]), side, volume, price, include_intent
            )
        children = [
            o
            for o in self.orders()
            if o.get("authorization_id") == scope["id"] and o["id"] != include_intent
        ]
        limits = scope["attempts"]
        if side not in limits or sum(o["side"] == side for o in children) >= limits[side]:
            raise SafetyError(
                "OKX authorized submission count exhausted; no automatic retry/rearming"
            )
        if (
            side == "buy"
            and price > dec(scope["buy_ceiling"])
            or side == "sell"
            and price < dec(scope["sell_floor"])
        ):
            raise SafetyError("OKX price exceeds the absolute authorized bound")
        spent = sum(
            (
                max(
                    dec(o["volume"]) * dec(o["price"]) * (1 + dec(o["fee_bps"]) / BPS),
                    dec(o["cost"]) + max(ZERO, dec(o.get("fees", {}).get(o["quote"], 0))),
                )
                for o in children
                if o["side"] == "buy"
            ),
            ZERO,
        )
        fee = self.fees.reserve(self.resolve(scope["pair"]))
        if fee > dec(scope["fee_bps"]):
            raise SafetyError(
                "OKX planning fee exceeds the finite authorization allowance; no new submission"
            )
        if side == "buy" and spent + volume * price * (1 + fee / BPS) > dec(scope["budget"]):
            raise SafetyError("OKX finite authorization budget exhausted")
        if scope["kind"] == "execution_cycle" and side == "sell":
            owned = sum(
                (
                    dec(x["quantity"]) * (1 if o["side"] == "buy" else -1)
                    + (dec(x["fee_signed"]) if x["fee_currency"] == o["base"] else ZERO)
                    for o in children
                    for x in o.get("executions", [])
                ),
                ZERO,
            )
            if volume > owned:
                raise SafetyError("OKX diagnostic exit exceeds inventory acquired by this cycle")

    async def execution_book(self, pair, side, price, book, maker):
        self.permission()
        book = await self.client.book(pair)
        book.fresh(self.settings["stale_seconds"])
        best = book.asks[0][0] if side == "buy" else book.bids[0][0]
        if maker:
            if (
                self.client.environment != "demo"
                or (side == "buy" and price >= best)
                or (side == "sell" and price <= best)
            ):
                raise SafetyError("OKX demo post-only price crosses the fresh book; no chasing")
            return book
        slip = abs(price - best) / best * BPS
        if (
            (side == "buy" and price < best)
            or (side == "sell" and price > best)
            or slip > dec(self.settings["slippage_bps"])
        ):
            raise SafetyError(
                f"OKX IOC not marketable or beyond fresh-book slippage: limit {price}, best {best}, distance {slip}bps, permitted {self.settings['slippage_bps']}bps; nothing submitted"
            )
        return book

    async def live_spot_check(self, pair, maker, fee_bps, needed):
        self.permission()
        return fee_bps  # Native balances/fees are checked by place in BOTH environments.

    def tag_order(self, order):
        super().tag_order(order)
        scope = self.permission()
        order.update(
            client_id=order["id"].replace("-", ""),
            instrument=self.client.instruments[order["pair"]]["instId"],
            environment=self.client.environment,
            authorization_id=scope["id"],
            fees={},
            fee_reported=False,
            planning_fee_bps=order["fee_bps"],
            write_outcome="not_sent",
        )
        order["payload"] = {
            "instId": order["instrument"],
            "tdMode": "cash",
            "clOrdId": order["client_id"],
            "side": order["side"],
            "ordType": "post_only" if order["maker"] else "ioc",
            "sz": order["volume"],
            "px": order["price"],
            "tradeQuoteCcy": order["quote"],
            "stpMode": "cancel_taker",
            "pxAmendType": "0",
        }

    async def submit_spot(self, order, *, program=None, review=None):
        scope = self.permission()
        from kairos.htf_review import intent_deadline

        deadline = intent_deadline(
            min(scope["deadline"], program["deadline"] if program else scope["deadline"]), review
        )
        try:
            order["venue_limits"] = await self.client.limit_order_check(
                self.resolve(order["pair"]),
                dec(order["volume"]),
                dec(order["price"]),
                self.settings["stale_seconds"],
            )
        except SafetyError as exc:
            # The durable intent exists, but no HTTP order write has been attempted.
            raise ExchangeRejected(str(exc)) from None
        order["write_outcome"] = "unknown"
        self.store.save_order(order)
        self.emit_state()
        try:
            rows = await self.client.request(
                "POST", "/api/v5/trade/order", payload=order["payload"], deadline=deadline
            )
        except (OKXBeforeSend, OKXRejected) as exc:
            order["write_outcome"] = "not_sent" if isinstance(exc, OKXBeforeSend) else "rejected"
            raise ExchangeRejected(str(exc)) from None
        order["write_outcome"] = "accepted"
        return {"txid": [rows[0]["ordId"]]}

    async def place(self, pair, side, volume, price, book, maker=False, **kwargs):
        self.validate_capabilities(self.settings)
        if self.permission()["kind"] != "strategy" and (
            maker or kwargs.get("exit_only") or kwargs.get("review") is not None
        ):
            raise SafetyError("OKX accepts finite cash IOC intents only")
        self.check_scope(side, volume, price, pair=pair)

        # Finish account reads before waiting for the short-lived price reference.
        # No waiting or replay is permitted once submit_spot owns that intent.
        def wait_guard():
            self.permission()
            program = kwargs.get("program")
            if program and self.clock() >= program["deadline"]:
                raise SafetyError(
                    "OKX TWAP slot expired during data wait; no new intent or catch-up submission"
                )

        while True:
            wait_guard()
            await self.settle()
            self.permission()
            if not self.account_info["can_trade"] or self.account_info["fee_type"] not in {
                "0",
                "1",
            }:
                raise SafetyError(
                    "OKX account lacks Trade permission or documented fee-currency policy; nothing submitted"
                )
            await self.client.catalog()
            pair = self.resolve(pair.id)
            metadata = self.client.instruments[pair.id]
            scope = self.permission()
            expected_rules = (
                scope["markets"][pair.id]["market_rules"]
                if scope["kind"] == "strategy"
                else scope["market_rules"]
            )
            if fingerprint(metadata) != expected_rules:
                raise SafetyError(
                    "OKX account instrument rules changed after authorization; no new submission"
                )
            await self.client.wait_limit_reference(pair, self.settings["stale_seconds"], wait_guard)
            if 0 <= self.clock() - self.account_checked < 30:
                break
            # A long reference wait may age the account proof. Recheck it before
            # any intent; the same permission and child-slot deadlines still apply.
        bands = await self.client.request(
            "GET", "/api/v5/public/price-limit", params={"instId": metadata["instId"]}
        )
        if (
            len(bands) != 1
            or bands[0].get("instId") != metadata["instId"]
            or type(bands[0].get("enabled")) is not bool
        ):
            raise SafetyError("OKX native price-band evidence unavailable; nothing submitted")
        # The scheduler's price predates account/reference/band I/O. Construct
        # scheduled child once from a fresh native book, within unchanged
        # authorization and DCA-slot bounds. Never reprice a durable intent.
        scope = self.permission()
        if scope["kind"] in {"twap", "strategy"} and kwargs.get("program"):
            limits = scope["markets"][pair.id] if scope["kind"] == "strategy" else scope
            parent = dec(limits["buy_ceiling" if side == "buy" else "sell_floor"])
            if scope["kind"] == "strategy" and self.settings["strategy"] == "dca":
                parent = min(
                    parent,
                    dec(self.settings["dca_amount"])
                    / (volume * (1 + self.fees.reserve(pair) / BPS)),
                )
            book = await self.client.book(pair)
            book.fresh(self.settings["stale_seconds"])
            price = limit_price(book, side, self.settings["slippage_bps"], parent=parent)
        wait_guard()
        self.check_scope(side, volume, price, pair=pair)
        needed = volume * price * (1 + self.fees.reserve(pair) / BPS) if side == "buy" else volume
        asset = pair.quote if side == "buy" else pair.base
        available = dec(self.account_snapshot["assets"].get(asset, {}).get("available", 0))
        if needed > available:
            raise SafetyError(
                f"OKX requires {needed} {asset}, available after holds {available}; nothing submitted"
            )
        band = bands[0]
        age = self.clock() - native.native_time(band["ts"]) / 1000
        if not -2 <= age <= self.settings["stale_seconds"]:
            raise PendingOKX(
                f"OKX price-band age {age:.3f}s exceeds the current freshness bound; no new submission"
            )
        if band["enabled"]:
            bound = dec(band["buyLmt" if side == "buy" else "sellLmt"])
            if (
                bound <= 0
                or (side == "buy" and price > bound)
                or (side == "sell" and price < bound)
            ):
                raise SafetyError(
                    f"OKX {side} price {price} violates native venue band {bound}; no price amendment or submission"
                )
        result = await super().place(pair, side, volume, price, book, maker=maker, **kwargs)
        if maker:
            # Native post-only orders have no exchange-side GTD. Bound observation
            # and cancel owned remainder; never interpret cancel acceptance as a fill.
            end = min(result["expires"], scope["deadline"])
            monotonic_end = time.monotonic() + max(0, end - self.clock())
            while (
                self.running
                and self.armed
                and self.clock() < end
                and time.monotonic() < monotonic_end
            ):
                await self.refresh_order(result)
                if result["status"] in TERMINAL:
                    break
                await asyncio.sleep(min(1, max(0, end - self.clock())))
            await self.cancel_active()
        await self.settle()
        return next(o for o in self.orders() if o["id"] == result["id"])

    async def confirm_spot(self, order):
        try:
            async with asyncio.timeout(45):
                while True:
                    if not self.running or not self.armed:
                        raise SafetyError(
                            "OKX IOC confirmation interrupted by Stop; reconcile original intent, retain holdings"
                        )
                    try:
                        await self.refresh_order(order)
                        if order["status"] in TERMINAL:
                            return
                    except PendingOKX:
                        pass  # Read-only observation lag, never resubmission.
                    await asyncio.sleep(1)
        except TimeoutError:
            raise SafetyError(
                "OKX IOC execution/fee evidence incomplete after 45s; original intent retained, no repeat authorized"
            ) from None

    async def cancel_active(self):
        if not self.orders(active=True):
            return
        config = native.account(await self.client.request("GET", "/api/v5/account/config"))
        binding = self.store.get("okx-account")
        if (
            not binding
            or config["uid"] != binding["uid"]
            or binding["environment"] != self.client.environment
        ):
            raise SafetyError(
                "OKX cancellation account/environment does not match its durable ownership"
            )
        self.account_info, self.account_checked = config, self.clock()
        for order in self.orders(active=True):
            await self.refresh_order(order)
            if order.get("exchange_status") != "open" or order.get("cancel_state"):
                continue
            order["cancel_state"] = "submitting"
            self.store.save_order(order)
            try:
                await self.client.request(
                    "POST",
                    "/api/v5/trade/cancel-order",
                    payload={"instId": order["instrument"], "ordId": order["txid"]},
                )
                order["cancel_state"] = "accepted"
            except (OKXBeforeSend, OKXRejected):
                order["cancel_state"] = "rejected"
                self.store.save_order(order)
                await self.refresh_order(order)
                if order.get("exchange_status") not in TERMINAL:
                    raise
            except Exception:
                order["cancel_state"] = "uncertain"
                self.store.save_order(order)
                await self.refresh_order(order)
                if order.get("exchange_status") not in TERMINAL:
                    raise
            self.store.save_order(order)
            await self.refresh_order(order)  # Acceptance is not terminal cancellation.

    async def stop(self):
        self.running, self.armed, self.authorization = False, False, None
        self.stop_generation += 1
        self.htf_review.cancel()
        if self.operation_task and self.operation_task is not asyncio.current_task():
            self.operation_task.cancel()
        async with self.lock:
            settled = False
            try:
                await self.cancel_active()
                if self.store.get("okx-account"):
                    await self.settle(seconds=10)
                settled = True
            except SafetyError as exc:
                self.last_error = str(exc)
                self.recovery_required = True
                raise
            finally:
                operation = self.store.get("okx-operation")
                if (
                    operation
                    and operation.get("kind") in {"twap", "strategy"}
                    and operation.get("status") == "running"
                ):
                    from kairos.okx_cycle import children, report

                    if not settled:
                        self.recovery_required = True
                    pending = [o for o in children(self, operation) if o["status"] not in TERMINAL]
                    outcome = (
                        "UNKNOWN"
                        if any(o["status"] in {"submitting", "uncertain"} for o in pending)
                        else "RECONCILIATION_PENDING"
                        if pending or self.recovery_required
                        else "STOPPED"
                    )
                    await report(
                        self,
                        operation,
                        outcome,
                        "Stop revoked permission; actual fills and retained holdings are recorded. "
                        "Unsubmitted windows are not replayed; a new run requires explicit authorization."
                        if outcome == "STOPPED"
                        else self.last_error
                        or "Stop revoked permission; original orders/accounting remain unresolved.",
                    )
                self.emit_state()

    async def reconcile(self, acknowledge=False):
        async with self.lock:
            if self.running or self.armed:
                raise SafetyError("Stop OKX before reconciling original intents")
            cycle = self.store.get("cycle")
            operation = self.store.get(
                "okx-operation:" + str((cycle or {}).get("authorization_id"))
            )
            if acknowledge and not (
                self.client.environment == "demo"
                and cycle
                and operation
                and operation.get("kind") == "strategy"
                and operation.get("strategy") == "arbitrage"
            ):
                raise SafetyError("OKX automatic external-order adoption is unavailable")
            await self.settle()
            if cycle:
                if not acknowledge:
                    raise SafetyError(
                        "Review the interrupted demo route and retained inventory, then explicitly acknowledge recovery"
                    )
                # Preserve the original route and operation outcome. Acknowledgement
                # releases only the recovery latch, not inventory or trading permission.
                with self.store.db:
                    self.store._put(
                        "okx-cycle:" + cycle["id"],
                        {
                            **cycle,
                            "acknowledged_at": self.clock(),
                            "retained_ledger": self.ledger(),
                            "reconciliation": self.store.get("okx-reconciliation"),
                        },
                    )
                    self.store._put("cycle", None)
                self.event(
                    "system",
                    {
                        "message": "Interrupted demo route acknowledged; original evidence and all holdings retained; no permission rearmed"
                    },
                )
            self.last_error = None
            self.emit_state()

    async def configure(self, values):
        async with self.lock:
            if self.running or self.orders(active=True):
                raise SafetyError("Stop and reconcile OKX before changing settings")
            values = validate_settings(values)
            self.validate_capabilities(values)
            if self.settings["strategy"] in {"htf", "scalp"} and any(
                values[k] != self.settings[k] for k in ("strategy", "product", "pair")
            ):
                from kairos import htf, scalping

                module = htf if self.settings["strategy"] == "htf" else scalping
                if module is htf:
                    htf.reconcile_entry(self)
                if module.snapshot(self).get("position") and module.quantity(
                    self, self.resolve(self.settings["pair"])
                ):
                    raise SafetyError(
                        "Retained demo strategy position prevents changing strategy/market; no reset or silent adoption"
                    )
            pair = self.resolve(values["pair"])
            if values["quote"] != pair.quote:
                raise SafetyError(
                    "OKX spending currency must match the explicitly selected instrument variant"
                )
            binding = self.store.get("okx-account")
            if binding and binding["quote"] != pair.quote:
                raise SafetyError(
                    "OKX allocation is bound to a different currency; no implicit conversion"
                )
            if (
                self.ledger()
                and any(dec(q) for a, q in self.ledger()["balances"].items() if a != pair.quote)
                and values["pair"] != self.settings["pair"]
                and values["strategy"] not in {"rebalance", "arbitrage"}
            ):
                raise SafetyError("OKX owned inventory prevents changing execution instruments")
            if values["strategy"] == "arbitrage":
                from kairos.strategies import triangle

                selected = [leg.pair for leg in triangle(pair, self.client.pairs)[0]]
            elif values["strategy"] in programs.STRATEGIES:
                selected = programs.validate_markets(values, self.resolve)
            else:
                selected = [pair]
            if self.client.environment == "demo" and values["strategy"] != "twap":
                if pair.id not in {p.id for p in selected} or any(
                    p.quote != self.client.instruments[p.id]["quoteCcy"] for p in selected
                ):
                    raise SafetyError(
                        "Demo strategy markets must include the primary market and use native price currencies"
                    )
                valued_assets = {p.base for p in selected if p.quote == values["quote"]}
                if self.ledger() and any(
                    dec(q)
                    for asset, q in self.ledger()["balances"].items()
                    if asset != values["quote"] and asset not in valued_assets
                ):
                    raise SafetyError(
                        "Retained inventory requires its native valuation market in the strategy scope"
                    )
            self.settings = values
            self.store.put("settings", values)
            await self.client.market_data.configure(selected, max_age=values["stale_seconds"])
            await self.refresh_fees(required=False)
            self.emit_state()

    async def set_mode(self, *args, **kwargs):
        raise SafetyError(
            "OKX live and demo are isolated exchanges; authorize a finite run instead of switching modes"
        )

    def reset_ledger(self, *args, **kwargs):
        raise SafetyError(
            "OKX allocation is established only by explicit account binding, never a balance reset"
        )

    def apply(self, *args, **kwargs):
        raise SafetyError(
            "OKX accounting accepts native execution records only; local fills are prohibited"
        )

    async def reset_paper(self):
        raise SafetyError("Hosted OKX balances and recovery evidence cannot be reset locally")

    async def start(self, **kwargs):
        if kwargs.get("restart") and kwargs.get("confirmation") == "RESTART ENGINE":
            async with self.lock:
                if self.running:
                    raise SafetyError(
                        "Stop OKX before restarting its read-only data/reconciliation services"
                    )
                await self.client.market_data.close()
                await self.initialize()
                return
        raise SafetyError(
            "OKX Start requires a finite preview and explicit authorization; strategy tests are demo-only"
        )

    async def wait_htf_book(self):
        """Read-only preflight, never a retry of HTF evaluation or an order intent."""
        scope = self.permission()
        if (
            self.client.environment != "demo"
            or scope["kind"] != "strategy"
            or scope["strategy"] != "htf"
            or self.orders(active=True)
            or self.recovery_required
        ):
            raise SafetyError("HTF book recovery requires an active, settled demo authorization")
        pair = self.resolve(scope["pair"])
        deadline = min(
            time.monotonic() + HTF_BOOK_WAIT_SECONDS, self.authorization_monotonic_deadline
        )
        task, notice, reason = None, None, "book read still pending"
        try:
            while True:
                if self.permission() is not scope:
                    raise SafetyError("HTF book wait authorization changed; no evaluation")
                remaining = min(deadline - time.monotonic(), scope["deadline"] - self.clock())
                if remaining <= 0:
                    raise PendingOKXBook(
                        "HTF initial book recovery exhausted its bounded wait; no strategy "
                        f"evaluation or new intent; original permission is not extended. Last read: {reason}"
                    )
                if task is None:
                    task = asyncio.create_task(self.client.book(pair))
                done, _ = await asyncio.wait((task,), timeout=min(0.25, remaining))
                if not done or time.monotonic() >= deadline:
                    continue
                try:
                    book = task.result()
                except PendingOKXBook as exc:
                    task, reason = None, str(exc)
                    if notice is None:
                        self.htf_review.cancel()
                        notice = (
                            "Waiting for a fresh OKX demo HTF book before evaluation "
                            f"(at most {HTF_BOOK_WAIT_SECONDS}s total, within the original deadline). "
                            "No new orders; protective exits cannot run without fresh data."
                        )
                        self.last_error = notice
                        self.event("data-wait", {"message": notice, "reason": reason})
                        self.emit_state()
                    await asyncio.sleep(min(0.25, max(0, remaining)))
                    continue
                book.fresh(self.settings["stale_seconds"])
                if self.permission() is not scope:  # Stop/expiry still wins with the snapshot.
                    raise SafetyError("HTF book wait authorization changed; no evaluation")
                if notice is not None:
                    from kairos import htf

                    htf.arm(self)  # Retire entry candidates, not ownership or authorization.
                    self.last_error = None
                    self.event(
                        "data-wait",
                        {
                            "message": "Fresh HTF book recovered; old entry candidates retired. "
                            "Original permission, budgets and deadlines unchanged."
                        },
                    )
                return
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if self.stop_generation != scope["generation"] and self.last_error == notice:
                self.last_error = None  # A stopped run is no longer waiting for a book.

    async def tick(self):
        async with self.lock:
            if not self.running:
                self.emit_state()
                return
            operation = self.store.get("okx-operation")
            generation = self.stop_generation
            try:
                if (
                    self.authorization
                    and self.authorization["kind"] == "strategy"
                    and (
                        self.clock() >= self.authorization["deadline"]
                        or time.monotonic() >= self.authorization_monotonic_deadline
                    )
                ):
                    self.running, self.armed, self.authorization = False, False, None
                    await self.cancel_active()
                    await self.settle()
                    from kairos.okx_cycle import report

                    await report(
                        self,
                        operation,
                        "STRATEGY_COMPLETE",
                        "Finite demo period ended; retained holdings remain owned. No liquidation or replay authorized.",
                    )
                    return
                self.permission()
                if self.authorization["kind"] == "execution_cycle":
                    return  # execution_cycle owns its separately bounded task.
                await self.refresh_fees()
                await self.settle()
                if self.authorization["kind"] == "strategy":
                    from kairos.okx_strategy import run

                    await run(self)
                    scope = self.authorization
                    if (
                        scope
                        and len(
                            [o for o in self.orders() if o.get("authorization_id") == scope["id"]]
                        )
                        >= scope["max_orders"]
                    ):
                        self.running = False
                else:
                    await programs.run(self)
                if not self.running:
                    if self.stop_generation != generation:
                        return  # Stop owns final reporting after its settlement reads.
                    self.armed, self.authorization = False, None
                    if operation:
                        from kairos.okx_cycle import report

                        if operation["kind"] == "strategy":
                            await report(
                                self,
                                operation,
                                "STRATEGY_COMPLETE",
                                "Finite demo schedule or attempt limit reached; actual executions recorded, holdings retained. Not proof of profitability.",
                            )
                        else:
                            complete = self.store.get(programs.key(self))["status"] == "complete"
                            await report(
                                self,
                                operation,
                                "TWAP_COMPLETE" if complete else "PARTIAL",
                                self.store.get(programs.key(self))["message"]
                                + " Schedule completion is not proof of full execution or round-trip qualification.",
                            )
            except Exception as exc:
                if self.stop_generation != generation:
                    return  # Stop owns cancellation, diagnostics and the final report.
                self.running, self.armed, self.authorization = False, False, None
                self.last_error = (
                    str(exc)
                    if isinstance(exc, SafetyError)
                    else "OKX program failed; original intents retained; reconcile before another authorization"
                )
                diagnostics.capture(exc, "okx-program")
                try:
                    await self.cancel_active()
                    await self.settle(seconds=10)
                except Exception:
                    self.recovery_required = True
                if operation:
                    from kairos.okx_cycle import report

                    outcome = (
                        "UNKNOWN"
                        if any(
                            o["status"] in {"uncertain", "submitting"}
                            for o in self.orders(active=True)
                        )
                        else "RECONCILIATION_PENDING"
                        if self.recovery_required
                        else "PARTIAL"
                    )
                    await report(self, operation, outcome, self.last_error)
            finally:
                if not self.running:
                    self.htf_review.cancel()
                self.emit_state()

    def snapshot(self):
        from kairos.okx_cycle import execution_evidence

        state = super().snapshot()
        binding = self.store.get("okx-account")
        operation = self.store.get("okx-operation")
        if getattr(self, "planning_source", None):
            state["fees"]["source"] = self.planning_source
            state["fees"]["note"] = self.planning_source + ". " + self.client.fee_note
        state.update(
            recovery_required=self.recovery_required
            or bool(self.orders(active=True))
            or bool(self.store.get("cycle")),
            account_verified_at=self.account_checked or None,
            environment=self.client.environment,
            armed=self.armed,
            hosted_demo=self.client.environment == "demo",
            credentials_configured=self.client.configured,
            write_gate=self.client.allow_writes,
            account_identity="••••" + binding["uid"][-4:] if binding else None,
            account_balances=self.account_snapshot,
            reconciliation=self.store.get("okx-reconciliation"),
            execution_cycle=operation,
            execution_cycle_evidence=execution_evidence(self, operation) if operation else None,
            capabilities={
                "crypto_spot": "OKX U.S. cash spot; isolated live/demo",
                "programs": "finite demo strategies; live execution_cycle/TWAP only",
                "funding_policy": "explicit allocation; no conversions, transfers, borrowing or withdrawals",
                "unsupported": "live predictive strategies, margin, derivatives and local fill simulation",
            },
        )
        return state
