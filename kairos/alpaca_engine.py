"""Hosted-paper reconciliation reusing Kairos's existing execution/risk engine."""

from kairos import diagnostics, htf, programs, scalping
from kairos.alpaca import iso
from kairos.domain import ZERO, SafetyError, dec
from kairos.engine import Engine
from kairos.settings import DEFAULTS


class AlpacaEngine(Engine):
    exchange = "alpaca"
    defaults = {
        **DEFAULTS,
        "quote": "USD",
        "pair": "alpaca:BTC/USD",
        "paper_balance": "500",
        "order_size": "100",
        "max_exposure": "100",
        "daily_loss": "12.50",
        "live_budget": "0",
        "recover_initial": False,
    }

    def __init__(self, store, client, jev, publish, *, clock=None):
        if store.get("settings") is None:
            store.put("settings", self.defaults)
        super().__init__(store, client, jev, publish, clock=clock)
        self.mode, self.paper_armed = "paper", False
        self.market_session = None

    async def initialize(self):
        await self.kraken.catalog()
        self.resolve(self.settings["pair"])
        if not self.ledger():
            self.reset_ledger(self.mode, self.settings["paper_balance"])
        self.recovery_required = bool(self.orders(active=True))
        if self.recovery_required:
            self.last_error = "Alpaca paper orders remain after restart; Reconcile before Start"
        await self.refresh_fees(required=False)
        self.ready = True
        self.event("system", {"message": "Alpaca hosted paper ready; paused, never live"})
        self.emit_state()

    def snapshot(self):
        return {
            **super().snapshot(),
            "exchange": "alpaca",
            "paper_armed": self.paper_armed,
            "paper_enabled": self.kraken.allow_paper,
            "recovery_required": self.recovery_required or bool(self.orders(active=True)),
            "market_session": self.market_session,
            "fee_note": self.kraken.fee_note,
            "broker_account": self.store.get("alpaca-account", {}).get("id"),
            "capabilities": {
                "crypto_spot": "Alpaca hosted paper; passive limits, no post-only guarantee",
                "us_stocks": "Alpaca hosted paper; listed stocks/ETFs, whole-share scheduled limits, regular sessions",
                "market_data": "Free crypto data and IEX-only equities; not consolidated equity quotes",
                "live_execution": "unavailable; paper host is fixed in code",
                "funding_policy": "Dedicated unused paper account, fixed local allocation, no transfers or account resets",
                "order_expiry": "Passive GTC orders need Kairos to cancel; a crash can leave them working until reconciliation",
            },
        }

    def validate_capabilities(self, values):
        if (
            values["product"] != "spot"
            or values["strategy"] == "arbitrage"
            or values["recover_initial"]
        ):
            raise SafetyError(
                "Alpaca supports unleveraged USD paper spot only; no arbitrage or capital recovery"
            )
        pair = self.resolve(values["pair"])
        if self.kraken.is_equity(pair) and values["strategy"] not in programs.STRATEGIES:
            raise SafetyError("Alpaca equities support DCA, TWAP and rebalancing only")
        if self.store.get("alpaca-account") and dec(values["paper_balance"]) != dec(
            self.ledger()["initial"]
        ):
            raise SafetyError("The hosted paper allocation cannot be silently resized or reset")

    def save_settings(self, settings):
        ledger = self.ledger()
        if not self.store.get("alpaca-account"):
            if self.orders() or any(dec(q) for a, q in ledger["balances"].items() if a != "USD"):
                raise SafetyError("Cannot resize an unreconciled Alpaca allocation")
            ledger["balances"]["USD"] = settings["paper_balance"]
            ledger["initial"] = settings["paper_balance"]
            ledger["recovery"]["original"] = settings["paper_balance"]
        with self.store.db:
            self.store._put("settings", settings)
            self.store._put("ledger:paper", ledger)

    async def set_mode(self, mode, confirmation):
        raise SafetyError(
            "Alpaca is hosted-paper only; live and local dry-run modes are unavailable"
        )

    def paper_history_restriction(self, order, operation, *, active_paper=None):
        return "Hosted-paper order history is retained for broker reconciliation"

    async def reset_paper(self):
        raise SafetyError(
            "Kairos never resets an Alpaca account or discards its reconciliation ledger"
        )

    async def live_preflight(self):
        raise SafetyError("Live Alpaca trading is not implemented")

    async def start(self, *, restart=False, confirmation=None):
        generation = self.stop_generation
        if not self.kraken.allow_paper:
            raise SafetyError(
                "Set ALLOW_ALPACA_PAPER_TRADING=true before submitting hosted paper orders"
            )
        expected = "RESTART ALPACA PAPER" if restart else "START ALPACA PAPER"
        if not self.paper_armed and confirmation != expected:
            raise SafetyError(f"Explicit {expected} confirmation is required")
        async with self.lock:
            self.validate_capabilities(self.settings)
            await self.reconcile_account(adopt=True)
            if self.stop_generation != generation:
                raise SafetyError("Start canceled by Stop")
            self.paper_armed = True
        await super().start(restart=restart, confirmation="RESTART ENGINE" if restart else None)

    def tag_order(self, order):
        super().tag_order(order)
        order.update(
            exchange="alpaca",
            fee_reported=False,
            planning_fee_bps=str(self.fees.reserve(self.resolve(order["pair"]), order["maker"])),
            fee_note="Order API omits fees; posted account activities are reconciled separately",
            execution="passive-limit" if order["maker"] else "ioc-limit",
            exchange_post_only=False,
            expiry_source="local cancellation; GTC can remain working while Kairos is offline"
            if order["maker"]
            else "broker IOC",
        )

    async def refresh_order(self, order):
        if order["mode"] != "paper":
            raise SafetyError("Unexpected order mode in Alpaca ledger")
        if not order["txid"]:
            order["txid"], row = await self.kraken.find_order(order["id"], order["created"])
            self.store.save_order(order)
        else:
            row = await self.kraken.query(order["txid"])
        raw = row["raw"]
        pair = self.resolve(order["pair"])
        if (
            raw["id"] != order["txid"]
            or raw["client_order_id"] != order["id"]
            or raw["symbol"].replace("/", "") != self.kraken.symbol(pair).replace("/", "")
            or raw["side"] != order["side"]
            or dec(raw["qty"]) != dec(order["volume"])
            or raw["type"] != "limit"
            or dec(raw["limit_price"]) != dec(order["price"])
        ):
            raise SafetyError("Alpaca order identity/terms changed; manual reconciliation required")
        self.apply(order, dec(row["vol_exec"]), dec(row["cost"]), ZERO, row["status"])
        return order

    def apply_fee(self, row):
        marker = "alpaca-activity:" + row["id"]
        prior = self.store.get(marker)
        if prior is not None:
            if prior != row:
                raise SafetyError("Alpaca activity was corrected; manual reconciliation required")
            return
        kind = row["activity_type"]
        if row.get("status") != "executed":
            raise SafetyError("Alpaca fee is not an executed activity")
        currency, amount = "USD", dec(row["net_amount"])
        if kind == "CFEE" and dec(row.get("qty", 0)):
            symbol = row.get("symbol", "").replace("/", "")
            pair = next(
                (
                    p
                    for p in self.kraken.pairs.values()
                    if self.kraken.symbol(p).replace("/", "") == symbol
                ),
                None,
            )
            if pair is None or self.kraken.is_equity(pair) or amount:
                raise SafetyError("Unsupported Alpaca crypto fee denomination")
            currency, amount = pair.base, dec(row["qty"])
        if amount > 0 or row.get("currency", currency) != currency:
            raise SafetyError("Unexpected Alpaca fee credit/currency")
        ledger = self.ledger()
        balance = dec(ledger["balances"].get(currency, 0)) + amount
        if balance < 0:
            raise SafetyError("Alpaca fee exceeds owned allocation; reconciliation required")
        ledger["balances"][currency] = str(balance)
        ledger["fees"][currency] = str(dec(ledger["fees"].get(currency, 0)) - amount)
        plans = []
        if currency != "USD":
            # Fee debits reduce owned inventory, not the gross broker fill quantities.
            for key in (htf.key(self), scalping.key(self)):
                state = self.store.get(key)
                if state and self.resolve(self.settings["pair"]).base == currency:
                    positions = [
                        state.get("position"),
                        state.get("entry_attempt", {}).get("position"),
                    ]
                    for position in positions:
                        if position:
                            position["inventory_adjustment"] = str(
                                dec(position.get("inventory_adjustment", 0)) + amount
                            )
                    plans.append((key, state))
        with self.store.db:
            self.store._put("ledger:paper", ledger)
            for key, value in plans:
                self.store._put(key, value)
            self.store._put(marker, row)
        self.event(
            "account-fee",
            {
                "activity_id": row["id"],
                "currency": currency,
                "amount": str(-amount),
                "attribution": "Account activity; not attributed to a particular order/run",
            },
        )

    async def reconcile_account(self, *, adopt=False):
        identity = self.store.get("alpaca-account")
        if not identity and not adopt:
            raise SafetyError("Press Start to validate and bind the dedicated Alpaca paper account")
        account = await self.kraken.account()
        if not identity:
            positions = await self.kraken.positions()
            previous = await self.kraken.request(
                "GET", "/v2/orders", params={"status": "all", "limit": 1}
            )
            if positions or previous:
                raise SafetyError(
                    "First connection requires a dedicated unused Alpaca paper account; Kairos will not adopt existing orders/holdings"
                )
            if self.orders():
                raise SafetyError("Alpaca account identity missing for existing local orders")
            allocation = dec(self.settings["paper_balance"])
            available = min(dec(account["cash"]), dec(account["non_marginable_buying_power"]))
            if allocation > available:
                raise SafetyError("Alpaca paper cash is below the configured allocation")
            after = iso(int(self.clock()) // 86400 * 86400)
            baseline = await self.kraken.activities(after)
            if any(r["activity_type"] != "CSD" for r in baseline):
                raise SafetyError("First Alpaca connection requires an unused paper account")
            identity = {
                "id": account["id"],
                "unallocated_cash": str(dec(account["cash"]) - allocation),
                "activities_after": after,
                "baseline_activities": {r["id"]: r for r in baseline},
            }
            self.reset_ledger("paper", str(allocation))
            self.store.put("alpaca-account", identity)
        if identity["id"] != account["id"]:
            raise SafetyError(
                "Alpaca paper account changed; existing ledger must not be reassigned"
            )
        for order in self.orders(active=True):
            await self.refresh_order(order)
        if self.settings["strategy"] == "htf" and (
            htf.snapshot(self).get("entry_attempt", {}).get("order_id")
        ):
            htf.reconcile_entry(self)
        local = {o["txid"]: o for o in self.orders() if o["txid"]}
        for row in await self.kraken.open_orders():
            if row["id"] not in local or row["client_order_id"] != local[row["id"]]["id"]:
                raise SafetyError("Untracked Alpaca order; manual reconciliation required")
        for row in await self.kraken.activities(identity["activities_after"]):
            baseline = identity["baseline_activities"].get(row["id"])
            if baseline is not None:
                if baseline != row:
                    raise SafetyError("Alpaca baseline activity was corrected")
                continue
            if row["activity_type"] == "FILL":
                if row["order_id"] not in local:
                    raise SafetyError(
                        "External Alpaca fill detected; account must be exclusive to Kairos"
                    )
            elif row["activity_type"] in {"FEE", "CFEE"}:
                self.apply_fee(row)
            else:
                raise SafetyError(
                    "External account activity or corporate action; manual reconciliation required"
                )
        # Re-read after fee activities; no assumptions about settlement timing or external edits.
        account = await self.kraken.account()
        positions = await self.kraken.positions()
        expected = {
            a: dec(q) for a, q in self.ledger()["balances"].items() if a != "USD" and dec(q)
        }
        if positions != expected:
            raise SafetyError(
                "Alpaca positions differ from confirmed fills/fees; reconcile before continuing"
            )
        cash = dec(account["cash"]) - dec(identity["unallocated_cash"])
        orders = self.orders()
        latest = orders[-1] if orders else None
        checkpoint = [latest[k] for k in ("id", "filled", "cost")] if latest else None
        delta = cash - self.balance("USD")
        if delta and not (
            latest
            and dec(latest["filled"]) > 0
            and checkpoint != self.store.get("alpaca-cash-checkpoint")
            and abs(delta) <= dec(".01")
        ):
            raise SafetyError(
                "Alpaca cash differs from confirmed fills/fees; reconcile before continuing"
            )
        ledger = self.ledger()
        ledger["balances"]["USD"] = str(cash)
        ledger["broker_rounding"] = str(dec(ledger.get("broker_rounding", 0)) + delta)
        with self.store.db:
            self.store._put("ledger:paper", ledger)
            self.store._put("alpaca-cash-checkpoint", checkpoint)
        return account

    async def valuation(self, enforce=False):
        if not self.store.get("alpaca-account"):
            return {"USD": dec(1)}, ZERO
        await self.reconcile_account()
        prices = {"USD": dec(1), **self.kraken.position_prices}
        ledger = self.ledger()
        equity = sum(dec(q) * prices[a] for a, q in ledger["balances"].items() if dec(q))
        exposure = equity - self.balance("USD")
        self.record_valuation(equity, exposure, "day:paper")
        if enforce and -dec(self.daily_pnl) >= dec(self.settings["daily_loss"]):
            raise SafetyError("Daily marked-to-market loss limit reached; no further orders")
        return prices, exposure

    async def place(self, pair, side, volume, price, book, maker=False, **kwargs):
        if not self.paper_armed or not self.kraken.allow_paper or self.mode != "paper":
            raise SafetyError("Alpaca hosted paper is not armed")
        self.validate_capabilities(self.settings)
        account = await self.reconcile_account()
        if not self.kraken.is_equity(pair) and account.get("crypto_status") != "ACTIVE":
            raise SafetyError("Alpaca crypto trading is not active for this account")
        if volume * price > dec("200000"):
            raise SafetyError("Alpaca paper order exceeds the documented crypto notional ceiling")
        available = await self.kraken.balances()
        required = (
            volume * price * (1 + self.fees.reserve(pair) / 10000) if side == "buy" else volume
        )
        if available.get("USD" if side == "buy" else pair.base, ZERO) < required:
            raise SafetyError("Alpaca available funds after holds are insufficient")
        if maker:
            current = await self.kraken.book(pair)
            current.fresh(self.settings["stale_seconds"])
            if (side == "buy" and price >= current.asks[0][0]) or (
                side == "sell" and price <= current.bids[0][0]
            ):
                raise SafetyError("Passive Alpaca limit now crosses; skip rather than chase")
            book = current
        return await super().place(pair, side, volume, price, book, maker, **kwargs)

    async def reconcile_portfolio(self):
        try:
            await self.reconcile_account()
        except Exception:
            self.recovery_required = True
            raise

    async def tick(self):
        if not self.running:
            return await super().tick()
        try:
            async with self.lock:
                await self.cancel_active()
                await self.reconcile_account()
                equities = any(self.kraken.is_equity(p) for p in self.fee_pairs())
                self.market_session = await self.kraken.clock() if equities else None
                if equities and not self.market_session["is_open"]:
                    await self.valuation(False)
                    self.emit_state()
                    return  # Scheduled slots are skipped normally on the next open cycle.
            await super().tick()
        except Exception as exc:
            error_id = diagnostics.capture(exc, "alpaca-reconciliation")
            self.running = False
            self.last_error = (
                str(exc) if isinstance(exc, SafetyError) else "Alpaca reconciliation failed"
            )
            self.recovery_required = True
            self.event("error", {"message": self.last_error, "error_id": error_id})
            try:
                await self.stop()
            except Exception as cleanup:
                diagnostics.capture(cleanup, "alpaca-cleanup")
            self.emit_state()
