import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime, timedelta

from kairos import margin, programs, scalping
from kairos.clients import ExchangeRejected
from kairos.domain import BPS, TERMINAL, ZERO, SafetyError, dec, floor
from kairos.fees import AccountFees
from kairos.futures import FuturesDesk
from kairos.futures import new_ledger as futures_ledger
from kairos.settings import DEFAULTS, load_settings, validate_settings
from kairos.strategies import limit_price, plan_cycle, plan_leg, trend_state, triangle


class Engine:
    def __init__(self, store, kraken, jev, publish, futures=None):
        self.store, self.kraken, self.jev, self.publish = store, kraken, jev, publish
        self.futures = FuturesDesk(self, futures)
        self.settings = load_settings(store.get("settings", DEFAULTS))
        self.fees = AccountFees(kraken, futures)
        self.fee_scope = []
        self.mode = "dry-run"  # Never auto-resume live trading after a restart.
        self.running = False
        self.stop_generation = 0
        self.ready = False
        self.lock = asyncio.Lock()
        self.last_error = None
        self.latest_decision = None
        self.equity = None
        self.exposure = None
        self.daily_pnl = None
        self.valuation_ts = None
        self.task = None
        self.recovery_required = store.get("cycle", None) is not None

    def event(self, kind, data):
        self.publish("engine-error" if kind == "error" else kind, self.store.event(kind, data))

    def orders(self, mode=None, active=False):
        return [
            o
            for o in self.store.orders()
            if (mode is None or o["mode"] == mode) and (not active or o["status"] not in TERMINAL)
        ]

    def ledger(self, mode=None):
        mode = mode or self.mode
        return self.store.get("ledger:" + mode)

    def balance(self, asset, mode=None):
        return dec((self.ledger(mode) or {"balances": {}})["balances"].get(asset, "0"))

    def limits(self):
        scale = dec(1)
        ledger = (
            self.store.get("margin")
            if self.settings["product"] == "margin"
            else self.futures.ledger()
            if self.settings["product"] == "futures"
            else self.ledger()
        )
        if self.settings["reinvest_profits"] and ledger and self.equity is not None:
            initial = dec(ledger["initial"])
            if initial > 0:
                scale = max(ZERO, dec(self.equity)) / initial
        return dec(self.settings["order_size"]) * scale, dec(self.settings["max_exposure"]) * scale

    def snapshot(self):
        return {
            "ready": self.ready,
            "running": self.running,
            "mode": self.mode,
            "live_enabled": self.kraken.allow_live,
            "futures_live_enabled": bool(self.futures.client and self.futures.client.allow_live),
            "settings": self.settings,
            "fees": self.fees.snapshot(self.fee_scope),
            "market_data": self.kraken.market_data.snapshot(),
            "error": self.last_error
            or (
                "Live Futures positions remain. Dry-run does not close them; arm Trading to reconcile/manage them."
                if self.mode == "dry-run" and self.futures.has_live_position()
                else None
            ),
            "effective_order_cap": str(self.limits()[0]),
            "effective_exposure_cap": str(self.limits()[1]),
            "decision": self.latest_decision,
            "ledger": self.ledger(),
            "equity": self.equity,
            "exposure": self.exposure,
            "daily_pnl": self.daily_pnl,
            "valuation_ts": self.valuation_ts,
            "recovery_required": self.recovery_required or bool(self.orders("trading", True)),
            "orders": self.history_orders(),
            "archived_orders": self.history_orders(archived=True),
            "order_history_revision": self.store.get("order_history_revision", 0),
            "cycle": self.store.get("cycle"),
            "margin": self.store.get("margin"),
            "futures": self.futures.ledger(),
            "program": programs.snapshot(self),
            "scalp": scalping.snapshot(self) if self.settings["strategy"] == "scalp" else None,
            "capabilities": {
                "crypto_spot": "paper-and-live",
                "crypto_margin": "paper-only",
                "retail_market_data": "spot-fx-xstocks-futures",
                "exchange_accounts": "read-only; separate from bot allocations",
                "xstocks_execution": "blocked",
                "futures_execution": "USD linear crypto perpetuals: paper and explicitly gated live",
                "funding_policy": "pre-funded allocations; no automatic conversions or transfers",
                "us_stocks": "not integrated: reviewed CLI offers xStocks, not brokerage stock orders",
            },
        }

    def emit_state(self):
        self.publish("state", self.snapshot())

    def resolve(self, name):
        if name.startswith("futures:"):
            pair = self.futures.client.pairs.get(name) if self.futures.client else None
            if not pair:
                raise SafetyError("Not a qualified USD linear crypto perpetual")
            return pair
        pair = self.kraken.pairs.get(name)
        if pair:
            return pair
        compact = name.replace("XBT", "BTC").replace("/", "")
        pair = next(
            (p for p in self.kraken.pairs.values() if p.symbol.replace("/", "") == compact), None
        )
        if not pair:
            raise SafetyError("Pair is not an online Kraken crypto spot market")
        return pair

    def fee_pairs(self):
        pair = self.resolve(self.settings["pair"])
        pairs = [pair]
        if self.settings["strategy"] == "arbitrage":
            pairs = [leg.pair for leg in triangle(pair, self.kraken.pairs)[0]]
        elif self.settings["strategy"] == "rebalance":
            pairs = programs.validate_markets(self.settings, self.resolve)
        if self.settings["product"] == "spot" and self.settings["recover_initial"]:
            pairs += [
                p
                for p in self.kraken.pairs.values()
                if p.quote == self.settings["quote"] and self.balance(p.base) > 0
            ]
        return list({p.id: p for p in pairs}.values())

    async def refresh_fees(self, *, force=False, required=True):
        self.fee_scope = []
        try:
            self.fee_scope = self.fee_pairs()
            await self.fees.refresh(self.fee_scope, force=force)
        except SafetyError:
            if required:
                raise

    async def initialize(self):
        await self.kraken.catalog()
        self.futures.ensure_paper()
        if self.settings["product"] == "futures":
            if not self.futures.client:
                raise SafetyError("Futures adapter unavailable")
            await self.futures.client.catalog()
        self.settings["pair"] = self.resolve(self.settings["pair"]).id
        self.store.put("settings", self.settings)
        if not self.ledger("dry-run"):
            self.reset_ledger("dry-run", self.settings["paper_balance"])
        if not self.store.get("margin"):
            self.store.put("margin", margin.new_ledger(self.settings["paper_balance"]))
        for order in self.orders("dry-run", True):
            order["status"] = "canceled"
            self.store.save_order(order)
        if self.orders("trading", True) or self.recovery_required:
            self.last_error = (
                "Restart recovery: reconcile tracked live orders/cycle before starting"
            )
        if self.futures.has_live_position():
            self.last_error = "Live Futures positions remain after restart; arm Trading to reconcile/manage them. Dry-run does not close them."
        await self.refresh_fees(required=False)
        self.ready = True
        self.event("system", {"message": "Ready; paused in dry-run mode"})
        self.emit_state()

    def reset_ledger(self, mode, amount):
        self.store.put(
            "ledger:" + mode,
            {
                "balances": {self.settings["quote"]: amount},
                "initial": amount,
                "fees": {},
                "recovery": {
                    "original": amount,
                    "reserved": "0",
                    "recovered": False,
                    "pending": False,
                    "last_check": 0,
                },
            },
        )
        self.store.put("day:" + mode, None)

    async def configure(self, values):
        async with self.lock:
            if self.running or self.orders(active=True):
                raise SafetyError("Stop and reconcile outstanding orders before changing settings")
            new = validate_settings(values)
            if new["strategy"] == "scalp" and self.mode != "dry-run":
                raise SafetyError("Bollinger scalping is paper-only")
            if (
                self.settings["strategy"] == "scalp"
                and scalping.snapshot(self)["position"]
                and scalping.quantity(self, self.resolve(self.settings["pair"]))
            ):
                if any(new[k] != self.settings[k] for k in ("strategy", "product", "pair")):
                    raise SafetyError(
                        "Close or reset the paper scalp position before changing strategy or market"
                    )
            if new["strategy"] == "scalp" and self.settings["strategy"] != "scalp":
                if new["product"] == "spot" and any(
                    dec(v)
                    for a, v in self.ledger("dry-run")["balances"].items()
                    if a != new["quote"]
                ):
                    raise SafetyError("Scalping requires a flat paper portfolio")
                if new["product"] == "futures" and any(
                    dec(p["quantity"]) for p in self.futures.ledger("dry-run")["positions"].values()
                ):
                    raise SafetyError("Scalping requires a flat paper portfolio")
            if new["product"] != self.settings["product"] and self.mode == "trading":
                raise SafetyError("Switch to Dry-run before changing trading product")
            if new["product"] == "futures":
                if not self.futures.client:
                    raise SafetyError("Futures adapter unavailable")
                await self.futures.client.catalog()
            pair = self.resolve(new["pair"])
            new["pair"] = pair.id
            if pair.id.startswith("futures:") != (new["product"] == "futures"):
                raise SafetyError("Select the matching Futures or spot product and market")
            for mode in ("dry-run", "trading"):
                ledger = self.futures.ledger(mode)
                if (
                    ledger
                    and any(dec(p["quantity"]) for p in ledger["positions"].values())
                    and any(
                        new[k] != self.settings[k] for k in ("pair", "product", "futures_leverage")
                    )
                ):
                    raise SafetyError(
                        "Close Futures positions before changing market, product or leverage"
                    )
            live = self.futures.ledger("trading")
            if live and dec(new["futures_live_budget"]) != dec(live["initial"]):
                raise SafetyError("Live Futures allocation cannot be silently resized")
            if new["product"] != "futures" and pair.quote != new["quote"]:
                raise SafetyError("Selected pair must use the configured quote currency")
            if new["quote"] != self.settings["quote"]:
                raise SafetyError(
                    "Quote currency is fixed for this database; use a separate data directory"
                )
            if new["product"] == "margin":
                if self.mode == "trading":
                    raise SafetyError("Margin is paper-only; switch to Dry-run first")
                if (
                    new["leverage"] not in pair.leverage_buy
                    or new["leverage"] not in pair.leverage_sell
                ):
                    raise SafetyError("Selected market does not advertise this margin leverage")
            positions = (self.store.get("margin") or {}).get("positions", {})
            if any(dec(p["quantity"]) for p in positions.values()) and any(
                new[k] != self.settings[k]
                for k in (
                    "product",
                    "pair",
                    "leverage",
                    "margin_rollover_bps",
                    "margin_open_fee_bps",
                    "maintenance_ratio",
                )
            ):
                raise SafetyError(
                    "Close or reset paper margin positions before changing market, product, or margin assumptions"
                )
            if new["strategy"] == "arbitrage":
                triangle(pair, self.kraken.pairs)
            if new["strategy"] in programs.STRATEGIES:
                programs.validate_markets(new, self.resolve)
            ledger = self.ledger("trading")
            delta = dec(new["live_budget"]) - dec(self.settings["live_budget"])
            if ledger and delta:
                cash = dec(ledger["balances"].get(new["quote"], 0)) + delta
                if cash < 0:
                    raise SafetyError("Cannot remove live allocation currently held in crypto")
                ledger["balances"][new["quote"]] = str(cash)
                ledger["initial"] = str(dec(ledger["initial"]) + delta)
                day = self.store.get("day:trading")
                with self.store.db:
                    self.store._put("ledger:trading", ledger)
                    if day:
                        day["equity"] = str(dec(day["equity"]) + delta)
                        self.store._put("day:trading", day)
                    self.store._put("settings", new)
            else:
                self.store.put("settings", new)
            self.settings = new
            self.latest_decision = None
            await self.refresh_fees(required=False)
            self.equity = self.exposure = self.daily_pnl = self.valuation_ts = None
            self.event("settings", {"message": "Settings saved", "settings": new})
            self.emit_state()

    def paper_history_restriction(self, order, operation, *, active_paper=None):
        if order.get("mode") != "dry-run":
            return "Live order history cannot be changed"
        if order.get("status") not in TERMINAL:
            return "Only completed paper orders can be archived or deleted"
        if operation != "delete":
            return None
        if active_paper is None:
            active_paper = bool(self.orders("dry-run", True))
        if self.running or self.mode != "dry-run" or active_paper:
            return "Pause in Dry-run and reconcile paper orders before deleting history"
        if self.recovery_required or self.store.get("cycle"):
            return "Reconcile recovery before deleting paper history"
        if not order.get("pair") or not order.get("strategy"):
            return "Incomplete paper record; archive instead"
        product = order.get("product", "spot")
        if product == "spot":
            ledger = self.ledger("dry-run")
            held = not ledger or any(
                dec(value)
                for asset, value in ledger["balances"].items()
                if asset != self.settings["quote"]
            )
        elif product in {"margin", "futures"}:
            ledger = self.store.get("margin" if product == "margin" else "futures:dry-run")
            held = not ledger or any(dec(p["quantity"]) for p in ledger["positions"].values())
        else:
            return "Unknown paper portfolio; archive instead"
        if held:
            return "Close or reset this paper portfolio's positions before deleting history"
        plan = self.store.get(f"scalp:dry-run:{product}:{order['pair']}") or {}
        if order.get("scalp_id") and (plan.get("position") or {}).get("id") == order["scalp_id"]:
            return "This order supports a saved scalp plan; archive or reset that paper portfolio first"
        prefix = "futures:" if product == "futures" else ""
        program = self.store.get(f"program:{prefix}dry-run:{order['strategy']}") or {}
        if order.get("program_id") and program.get("id") == order["program_id"]:
            return "This order supports a saved strategy run; archive or explicitly reset/rearm that run first"
        if (
            order["strategy"] == "rebalance"
            and order["created"] >= int(time.time() // 86400) * 86400
        ):
            return "Today's rebalance orders protect the daily turnover limit; archive instead"
        return None

    def history_orders(self, archived=False):
        result = []
        active_paper = bool(self.orders("dry-run", True))
        for order in self.store.display_orders(archived):
            row = dict(order)
            if order.get("mode") == "dry-run":
                row["history_actions"] = {
                    "archive_reason": self.paper_history_restriction(order, "archive"),
                    "delete_reason": self.paper_history_restriction(
                        order, "delete", active_paper=active_paper
                    ),
                }
            result.append(row)
        return result

    async def paper_order_history(self, order_id, operation, confirmation=""):
        async with self.lock:
            if (
                not isinstance(order_id, str)
                or not 1 <= len(order_id) <= 128
                or not isinstance(operation, str)
                or operation not in {"archive", "restore", "delete"}
            ):
                raise SafetyError("Invalid paper history action")
            order = next((o for o in self.orders() if o["id"] == order_id), None)
            if not order:
                raise SafetyError("Order not found; refresh the order history")
            reason = self.paper_history_restriction(order, operation)
            if reason:
                raise SafetyError(reason)
            if operation == "delete" and confirmation != "DELETE PAPER ORDER":
                raise SafetyError(
                    "Explicit permanent paper-order deletion confirmation is required"
                )
            self.store.paper_order_history(order_id, operation)
            verb = {"archive": "archived", "restore": "restored", "delete": "permanently deleted"}[
                operation
            ]
            self.event(
                "system", {"message": f"Paper order {verb}; balances and live history unchanged"}
            )
            self.emit_state()

    async def reset_paper(self):
        async with self.lock:
            if self.running or self.orders("dry-run", True):
                raise SafetyError("Stop paper trading before resetting")
            if self.settings["product"] == "futures":
                self.store.put("futures:dry-run", futures_ledger(self.settings["paper_balance"]))
                self.store.put("day:futures:dry-run", None)
                self.store.put("candle:futures:dry-run", None)
                for strategy in ("dca", "twap"):
                    self.store.put(f"program:futures:dry-run:{strategy}", None)
            elif self.settings["product"] == "margin":
                self.store.put("margin", margin.new_ledger(self.settings["paper_balance"]))
                self.store.put("day:margin", None)
            else:
                self.reset_ledger("dry-run", self.settings["paper_balance"])
                for strategy in programs.STRATEGIES:
                    self.store.put(f"program:dry-run:{strategy}", None)
            self.store.put("candle:dry-run", None)
            self.store.put(
                scalping.key(self), {"position": None, "last_candle": None, "cooldown_until": 0}
            )
            self.equity = self.exposure = self.daily_pnl = self.valuation_ts = None
            if self.mode == "dry-run":
                self.latest_decision = None
            self.event("system", {"message": "Paper ledger reset; historical events retained"})
            self.emit_state()

    async def reset_program(self, confirmation):
        async with self.lock:
            if self.running or self.orders(active=True) or self.recovery_required:
                raise SafetyError("Stop and reconcile all orders before rearming a strategy run")
            if (
                self.settings["strategy"] not in programs.STRATEGIES
                or confirmation != "NEW STRATEGY RUN"
            ):
                raise SafetyError("Explicit new strategy run confirmation is required")
            self.store.put(programs.key(self), None)
            self.event(
                "program",
                {"message": "New run armed for next Start; holdings unchanged", "mode": self.mode},
            )
            self.emit_state()

    async def live_preflight(self):
        if self.settings["strategy"] == "scalp":
            raise SafetyError("Bollinger scalping is paper-only")
        if self.settings["product"] == "futures":
            return await self.futures.preflight()
        if self.settings["product"] == "margin":
            raise SafetyError("Real margin trading is not enabled; margin is paper-only")
        if not self.kraken.allow_live:
            raise SafetyError("Set ALLOW_LIVE_TRADING=true on the server before using Trading mode")
        if dec(self.settings["live_budget"]) <= 0:
            raise SafetyError("Configure a positive live capital allocation first")
        if dec(self.settings["order_size"]) > dec(self.settings["live_budget"]):
            raise SafetyError("Order size exceeds live capital allocation")
        status = await self.kraken.request("SystemStatus")
        if status.get("status") != "online":
            raise SafetyError("Kraken is not fully online")
        await self.refresh_fees(force=True)
        available = await self.kraken.balances()
        if not self.ledger("trading"):
            if available.get(self.settings["quote"], ZERO) < dec(self.settings["live_budget"]):
                raise SafetyError("Insufficient free quote balance for the live allocation")
            self.reset_ledger("trading", self.settings["live_budget"])
        for asset, quantity in self.ledger("trading")["balances"].items():
            if available.get(asset, ZERO) < dec(quantity):
                raise SafetyError(
                    "Exchange balances are below the bot ledger; reconcile account changes"
                )

    async def set_mode(self, mode, confirmation):
        if mode not in ("dry-run", "trading"):
            raise SafetyError("Unknown mode")
        async with self.lock:
            was_running = self.running
            self.running = False
            try:
                await self.cancel_active()
                if self.orders("trading", True) or self.recovery_required:
                    raise SafetyError("Reconcile live orders/cycle before changing mode")
                if mode == "trading":
                    expected = (
                        "ENABLE LIVE FUTURES"
                        if self.settings["product"] == "futures"
                        else "ENABLE LIVE TRADING"
                    )
                    if confirmation != expected:
                        raise SafetyError("Explicit live-trading confirmation is required")
                    await self.live_preflight()
                self.mode = mode
                self.equity = self.exposure = self.daily_pnl = self.valuation_ts = None
                self.last_error = None
                if was_running and self.settings["product"] != "futures":
                    await self.valuation(enforce=True)
                    programs.prepare(self)
                    self.running = True
                self.event("mode", {"mode": mode, "running": self.running})
            finally:
                self.emit_state()

    async def start(self):
        async with self.lock:
            if not self.ready:
                raise SafetyError("Market catalog is not ready")
            if self.orders(active=True) or self.recovery_required:
                raise SafetyError("Reconcile outstanding orders/cycle before starting")
            if (
                self.settings["product"] == "futures"
                and self.mode == "dry-run"
                and self.futures.has_live_position()
            ):
                raise SafetyError(
                    "Live Futures positions remain; arm Trading to reconcile or reduce them before running paper"
                )
            if self.mode == "trading":
                await self.live_preflight()
            else:
                await self.refresh_fees(force=True)
            scalp = self.settings["strategy"] == "scalp"
            if scalp:
                scalping.prepare(self)
            if (
                not scalp
                and self.settings["strategy"] not in programs.STRATEGIES
                and not self.jev.key
            ):
                raise SafetyError("JEV_API_KEY is not configured")
            await self.valuation(enforce=not (scalp and scalping.snapshot(self)["position"]))
            programs.prepare(self)
            self.running, self.last_error = True, None
            self.event("system", {"message": "Started", "mode": self.mode})
            self.emit_state()

    async def stop(self):
        # Latch the stop before waiting for an in-flight data/model request.
        self.stop_generation += 1
        self.running = False
        async with self.lock:
            try:
                await self.cancel_active()
                self.event(
                    "system", {"message": "Stopped; tracked orders reconciled. Holdings retained."}
                )
            finally:
                self.emit_state()

    async def close_futures(self, confirmation):
        async with self.lock:
            if (
                self.running
                or self.settings["product"] != "futures"
                or confirmation != "REDUCE FUTURES POSITION"
            ):
                raise SafetyError("Pause Futures and explicitly confirm a reduce-only exit")
            await self.cancel_active()
            if self.recovery_required:
                raise SafetyError("Reconcile recovery before reducing a Futures position")
            pair = self.resolve(self.settings["pair"])
            await self.fees.refresh([pair])
            await self.futures.valuation(False, exit_only=True)
            quantity = self.futures.position(pair)
            if not quantity:
                raise SafetyError("No bot Futures position to reduce")
            side = "sell" if quantity > 0 else "buy"
            book = await self.futures.client.book(pair)
            price = limit_price(book, side, self.settings["slippage_bps"])
            volume = floor(min(abs(quantity), self.limits()[0] / price), pair.lot)
            await self.futures.place(pair, side, volume, price, book, close=True)
            await self.futures.valuation(False)
            self.emit_state()

    async def reconcile(self, acknowledge=False):
        async with self.lock:
            self.running = False
            await self.cancel_active()
            if self.orders(active=True):
                raise SafetyError("Orders still unresolved; remain stopped")
            if self.store.get("cycle"):
                if not acknowledge:
                    raise SafetyError(
                        "Interrupted cycle left inventory. Review balances, then acknowledge recovery"
                    )
                self.store.put("cycle", None)
            if self.settings["product"] == "futures" and self.futures.ledger("trading"):
                await self.futures.live_sync()
            self.recovery_required = False
            self.last_error = None
            self.event("system", {"message": "Reconciliation completed; holdings retained"})
            self.emit_state()

    def record_valuation(self, equity, exposure, day_key):
        """Shared display/UTC baseline bookkeeping; callers retain product-specific risk checks."""
        date = datetime.now(UTC).date().isoformat()
        day = self.store.get(day_key)
        if not day or day["date"] != date:
            day = {"date": date, "equity": str(equity)}
            self.store.put(day_key, day)
        self.equity, self.exposure = str(equity), str(exposure)
        self.daily_pnl = str(equity - dec(day["equity"]))
        self.valuation_ts = time.time()

    async def valuation(self, enforce=False):
        if self.settings["product"] == "futures":
            return await self.futures.valuation(enforce)
        if self.settings["product"] == "margin":
            return await self.margin_valuation(enforce)
        ledger = self.ledger()
        if not ledger:
            raise SafetyError("No portfolio initialized")
        quote = self.settings["quote"]
        pairs = []
        for asset, amount in ledger["balances"].items():
            if asset == quote or dec(amount) == 0:
                continue
            pair = next(
                (p for p in self.kraken.pairs.values() if p.base == asset and p.quote == quote),
                None,
            )
            if not pair:
                raise SafetyError(
                    "Cannot value an existing holding in the configured quote currency"
                )
            pairs.append(pair)
        marks = await self.kraken.marks(pairs)
        prices = {quote: dec(1)}
        for pair in pairs:
            if pair.id not in marks or marks[pair.id] <= 0:
                raise SafetyError("Missing portfolio valuation price")
            prices[pair.base] = marks[pair.id]
        equity = sum(
            dec(qty) * prices[asset] for asset, qty in ledger["balances"].items() if dec(qty) != 0
        )
        exposure = equity - dec(ledger["balances"].get(quote, 0))
        self.record_valuation(equity, exposure, "day:" + self.mode)
        if enforce and -dec(self.daily_pnl) >= dec(self.settings["daily_loss"]):
            raise SafetyError("Daily marked-to-market loss limit reached; no further orders")
        return prices, exposure

    def apply(self, order, total_volume, total_cost, total_fee, status):
        """Apply cumulative Kraken/paper fills exactly once, including partial fills."""
        volume = total_volume - dec(order["filled"])
        cost = total_cost - dec(order["cost"])
        fee = total_fee - dec(order["fee"])
        if min(volume, cost) < 0 or (fee < 0 and not order["maker"]):
            raise SafetyError("Order accounting moved backwards; reconciliation required")
        if order.get("product") == "margin":
            ledger = self.store.get("margin")
            margin.apply_fill(
                ledger, order["pair"], order["side"], volume, cost, fee, self.settings
            )
            order.update(
                filled=str(total_volume), cost=str(total_cost), fee=str(total_fee), status=status
            )
            self.store.save_order(order, ledger)
            if volume or fee:
                self.event(
                    "fill",
                    {
                        "mode": "dry-run",
                        "product": "margin",
                        "pair": order["pair"],
                        "side": order["side"],
                        "volume": str(volume),
                        "cost": str(cost),
                        "fee": str(fee),
                        "order_id": order["id"],
                        "simulated": True,
                    },
                )
            return
        ledger = self.ledger(order["mode"])
        balances = {k: dec(v) for k, v in ledger["balances"].items()}
        base, quote = order["base"], order["quote"]
        balances[base] = balances.get(base, ZERO) + (volume if order["side"] == "buy" else -volume)
        balances[quote] = balances.get(quote, ZERO) + (
            -cost - fee if order["side"] == "buy" else cost - fee
        )
        if any(q < 0 for q in balances.values()):
            raise SafetyError("Fill exceeds allocated balance; manual reconciliation required")
        ledger["balances"] = {k: str(v) for k, v in balances.items()}
        ledger["fees"][quote] = str(dec(ledger["fees"].get(quote, 0)) + fee)
        order.update(
            filled=str(total_volume), cost=str(total_cost), fee=str(total_fee), status=status
        )
        self.store.save_order(order, ledger)
        if volume or fee:
            self.event(
                "fill",
                {
                    "mode": order["mode"],
                    "pair": order["pair"],
                    "side": order["side"],
                    "volume": str(volume),
                    "cost": str(cost),
                    "fee": str(fee),
                    "fee_currency": quote,
                    "order_id": order["id"],
                    "simulated": order["mode"] == "dry-run",
                },
            )

    async def refresh_order(self, order):
        if order["mode"] == "dry-run":
            return order
        if order.get("product") == "futures":
            await self.futures.refresh(order)
            return order
        if not order["txid"]:
            order["txid"], row = await self.kraken.find_order(order["id"], order["created"])
            self.store.save_order(order)
        else:
            row = await self.kraken.query(order["txid"])
        self.apply(order, dec(row["vol_exec"]), dec(row["cost"]), dec(row["fee"]), row["status"])
        return order

    async def cancel_active(self):
        for order in self.orders(active=True):
            if order["mode"] == "dry-run":
                order["status"] = "canceled"
                self.store.save_order(order)
                continue
            if order.get("product") == "futures":
                await self.futures.cancel(order)
                continue
            await self.refresh_order(order)
            if order["status"] in TERMINAL:
                continue
            try:
                await self.kraken.cancel(order["txid"])
            except ExchangeRejected:
                pass  # It may have filled just before cancellation; query is authoritative.
            for _ in range(5):
                await self.refresh_order(order)
                if order["status"] in TERMINAL:
                    break
                await asyncio.sleep(1)
            if order["status"] not in TERMINAL:
                raise SafetyError("Cancellation not confirmed; no further orders")

    async def paper_makers(self):
        if self.futures.client:
            await self.futures.paper_makers()
        for order in self.orders("dry-run", True):
            if order.get("product") == "futures":
                continue
            if not order["maker"]:
                raise SafetyError("Unexpected unfinished paper taker order")
            pair = self.resolve(order["pair"])
            trades, cursor = await self.kraken.trades(pair, order["cursor"])
            volume = ZERO
            for price, qty, ts, side, *_ in trades:
                # Crossing prints, not touches; assume only 10% participation. Queue unknown.
                if not order["created"] < float(ts) <= order["expires"]:
                    continue
                crosses = (
                    order["side"] == "buy" and side == "s" and dec(price) < dec(order["price"])
                ) or (order["side"] == "sell" and side == "b" and dec(price) > dec(order["price"]))
                if crosses:
                    volume += dec(qty) * dec("0.1")
            volume = floor(min(volume, dec(order["volume"]) - dec(order["filled"])), pair.lot)
            filled = dec(order["filled"]) + volume
            cost = dec(order["cost"]) + volume * dec(order["price"])
            fee = cost * dec(order["fee_bps"]) / BPS
            order["cursor"] = cursor
            status = "closed" if filled == dec(order["volume"]) else "open"
            if time.time() >= order["expires"] and status == "open":
                status = "expired"
            self.apply(order, filled, cost, fee, status)

    async def margin_valuation(self, enforce):
        ledger = self.store.get("margin")
        if ledger is None:
            raise SafetyError("Paper margin ledger is not initialized")
        margin.accrue(ledger, self.settings)
        self.store.put("margin", ledger)
        books, marks = {}, {}
        for key, position in ledger["positions"].items():
            if dec(position["quantity"]) == 0:
                continue
            book = await self.kraken.book(self.resolve(key))
            books[key] = book
            marks[key] = book.bids[0][0] if dec(position["quantity"]) > 0 else book.asks[0][0]
        values = margin.metrics(ledger, marks)
        self.record_valuation(values["equity"], values["exposure"], "day:margin")
        if values["used_margin"] and values["equity"] <= values["used_margin"] * dec(
            self.settings["maintenance_ratio"]
        ):
            self.running = False
            await self.cancel_active()
            for key, position in ledger["positions"].items():
                quantity = dec(position["quantity"])
                if not quantity:
                    continue
                side = "sell" if quantity > 0 else "buy"
                book = books[key]
                price = book.bids[-1][0] if side == "sell" else book.asks[-1][0]
                filled, cost = book.fill(side, abs(quantity), price)
                order = self.margin_order(self.resolve(key), side, abs(quantity), price, False)
                order["liquidation"] = True
                self.store.save_order(order)
                self.apply(
                    order,
                    filled,
                    cost,
                    cost * self.fees.rate(self.resolve(key)) / BPS,
                    "closed" if filled == abs(quantity) else "canceled",
                )
            self.event(
                "liquidation",
                {
                    "message": "Simulated maintenance threshold breached; visible-depth liquidation attempted"
                },
            )
            raise SafetyError(
                "Paper margin liquidation triggered; review remaining positions or reset"
            )
        if enforce and -dec(self.daily_pnl) >= dec(self.settings["daily_loss"]):
            raise SafetyError("Daily paper margin loss limit reached")
        return values, values["exposure"]

    def margin_order(self, pair, side, volume, price, maker):
        now = time.time()
        return {
            "id": str(uuid.uuid4()),
            "txid": None,
            "mode": "dry-run",
            "product": "margin",
            "strategy": self.settings["strategy"],
            "pair": pair.id,
            "base": pair.base,
            "quote": pair.quote,
            "side": side,
            "volume": str(volume),
            "price": str(price),
            "maker": maker,
            "fee_bps": str(self.fees.rate(pair, maker)),
            "created": now,
            "expires": now + 30,
            "cursor": str(int(now * 1_000_000_000)),
            "status": "open",
            "filled": "0",
            "cost": "0",
            "fee": "0",
        }

    async def place_margin(self, pair, side, volume, price, book, maker):
        if self.mode != "dry-run":
            raise SafetyError("Live margin execution is prohibited")
        values, exposure = await self.margin_valuation(True)
        ledger = self.store.get("margin")
        old = dec(ledger["positions"].get(pair.id, {}).get("quantity", 0))
        reducing = old * (1 if side == "buy" else -1) < 0
        if reducing and volume > abs(old):
            raise SafetyError("Margin order must close before reversing a position")
        order_cap, exposure_cap = self.limits()
        if volume * price > order_cap:
            raise SafetyError("Margin order exceeds notional size limit")
        if not reducing:
            if exposure + volume * price > exposure_cap:
                raise SafetyError("Margin gross exposure limit would be exceeded")
            fee = (
                volume
                * price
                * (self.fees.reserve(pair) + dec(self.settings["margin_open_fee_bps"]))
                / BPS
            )
            required = volume * price / self.settings["leverage"] + fee
            if values["equity"] - values["used_margin"] < required:
                raise SafetyError("Insufficient simulated free collateral")
        book.fresh(self.settings["stale_seconds"])
        if book.spread_bps > dec(self.settings["max_spread_bps"]):
            raise SafetyError("Margin spread exceeds maximum")
        if not self.running:
            raise SafetyError("Engine stopped before paper margin submission")
        order = self.margin_order(pair, side, volume, price, maker)
        self.store.save_order(order)
        if not maker:
            filled, cost = book.fill(side, volume, price)
            self.apply(
                order,
                filled,
                cost,
                cost * dec(order["fee_bps"]) / BPS,
                "closed" if filled == volume else "canceled",
            )
        self.event("order", order)
        return order

    async def place(
        self, pair, side, volume, price, book, maker=False, *, program=None, exit_only=False
    ):
        if self.settings["strategy"] == "scalp" and self.mode != "dry-run":
            raise SafetyError("Bollinger scalping is paper-only")
        if exit_only and (
            self.settings["strategy"] != "scalp"
            or maker
            or not scalping.valid_exit(self, pair, side, volume)
        ):
            raise SafetyError("Only a tracked paper scalp position may use an exit-only order")
        if not self.running:
            raise SafetyError("Engine is stopped")
        if self.orders(self.mode, True):
            raise SafetyError("Previous order must settle before placing another")
        pair.validate(volume, price)
        # Do not refresh away the planner's fee snapshot here; stale plans must fail closed.
        self.fees.rate(pair, maker)
        if self.settings["product"] == "futures":
            return await self.futures.place(
                pair, side, volume, price, book, maker, program=program, close=exit_only
            )
        if self.settings["product"] == "margin":
            return await self.place_margin(pair, side, volume, price, book, maker)
        prices, exposure = await self.valuation(enforce=not exit_only)
        quote = self.settings["quote"]
        # Also price intermediate arbitrage currencies in the configured quote.
        for asset in (pair.base, pair.quote):
            if asset in prices:
                continue
            direct = next(
                (p for p in self.kraken.pairs.values() if p.base == asset and p.quote == quote),
                None,
            )
            if not direct:
                raise SafetyError("Cannot value order currency")
            marks = await self.kraken.marks([direct])
            if direct.id not in marks or marks[direct.id] <= 0:
                raise SafetyError("Missing order valuation price")
            prices[asset] = marks[direct.id]
        fee_bps = self.fees.rate(pair, maker)
        notional = volume * price * prices[pair.quote]
        # Every order, including an intermediate leg, has a quote-denominated size cap.
        order_cap, exposure_cap = self.limits()
        if notional > order_cap:
            raise SafetyError("Order exceeds configured per-order size")
        if side == "buy" and pair.quote == quote and exposure + notional > exposure_cap:
            raise SafetyError("Maximum total crypto exposure would be exceeded")
        if side == "sell" and pair.base == quote and exposure + notional > exposure_cap:
            raise SafetyError("Maximum total crypto exposure would be exceeded")
        needed = (
            {pair.quote: volume * price * (1 + max(ZERO, fee_bps) / BPS)}
            if side == "buy"
            # A spot sell's fee is paid from its proceeds, not a separate quote reserve.
            else {pair.base: volume}
        )
        for asset, amount in needed.items():
            if self.balance(asset) < amount:
                raise SafetyError("Insufficient allocated funds; spot inventory cannot go short")
        if self.mode == "trading":
            if not self.kraken.allow_live:
                raise SafetyError("Live writes disabled")
            status = await self.kraken.request("SystemStatus")
            if status.get("status") != "online":
                raise SafetyError("Kraken trading is not online")
            fee_bps = await self.fees.recheck(pair, maker, fee_bps)
            balances = await self.kraken.balances()
            if any(balances.get(asset, ZERO) < amount for asset, amount in needed.items()):
                raise SafetyError("Insufficient exchange funds after holds")
        book.fresh(self.settings["stale_seconds"])
        if book.spread_bps > dec(self.settings["max_spread_bps"]):
            raise SafetyError("Spread exceeds configured maximum")
        if not self.running:
            raise SafetyError("Engine stopped before order submission")
        now = time.time()
        if program and now + 1 >= program["deadline"]:
            raise SafetyError("Scheduled slot expired before submission; no late order sent")
        self.fees.rate(pair, maker)
        order = {
            "id": str(uuid.uuid4()),
            "txid": None,
            "mode": self.mode,
            "strategy": self.settings["strategy"],
            "product": "spot",
            "pair": pair.id,
            "base": pair.base,
            "quote": pair.quote,
            "side": side,
            "volume": str(volume),
            "price": str(price),
            "maker": maker,
            "fee_bps": str(fee_bps),
            "created": now,
            "expires": now + 30,
            "cursor": str(int(now * 1_000_000_000)),
            "status": "submitting",
            "filled": "0",
            "cost": "0",
            "fee": "0",
        }
        if program:
            order.update(program_id=program["id"], program_slot=program["slot"])
        scalping.tag(self, order)
        self.store.save_order(order)  # Durable intent and run identity before the network write.
        if self.mode == "dry-run":
            if maker:
                order["status"] = "open"
                self.store.save_order(order)
            else:
                filled, cost = book.fill(side, volume, price)
                self.apply(
                    order,
                    filled,
                    cost,
                    cost * fee_bps / BPS,
                    "closed" if filled == volume else "canceled",
                )
        else:
            params = {
                "pair": pair.id,
                "type": side,
                "ordertype": "limit",
                "volume": str(volume),
                "price": str(price),
                "cl_ord_id": order["id"],
                "oflags": "post,fciq" if maker else "fciq",
                "timeinforce": "GTD" if maker else "IOC",
                "deadline": (datetime.now(UTC) + timedelta(seconds=5)).isoformat(
                    timespec="milliseconds"
                ),
            }
            if program:
                params["deadline"] = datetime.fromtimestamp(
                    min(time.time() + 5, program["deadline"]), UTC
                ).isoformat(timespec="milliseconds")
            if maker:
                params["expiretm"] = "+30"
            try:
                result = await self.kraken.add(params)
                order["txid"] = result["txid"][0]
                order["status"] = "open"
                self.store.save_order(order)
            except ExchangeRejected:
                order["status"] = "rejected"
                self.store.save_order(order)
                raise
            except Exception:
                order["status"] = "uncertain"
                self.store.save_order(order)
                raise SafetyError(
                    "Order submission outcome uncertain; reconcile before continuing"
                ) from None
            if not maker:
                for _ in range(5):
                    await self.refresh_order(order)
                    if order["status"] in TERMINAL:
                        break
                    await asyncio.sleep(1)
                if order["status"] not in TERMINAL:
                    raise SafetyError("IOC settlement not confirmed; engine stopped")
        self.event("order", order)
        return order

    async def decision(self, state):
        decision = await self.jev.decide(state)
        decision.update(
            strategy=self.settings["strategy"],
            pair=self.settings["pair"],
            mode=self.mode,
            ts=time.time(),
            state=state,
        )
        self.latest_decision = decision
        self.event("decision", decision)
        return (
            decision["action"]
            if dec(decision["confidence"]) >= dec(self.settings["min_confidence"])
            else "hold"
        )

    async def directional(self, pair):
        if self.settings["product"] == "futures":
            return await self.futures.directional(pair)
        maker = self.settings["strategy"] == "maker"
        is_margin = self.settings["product"] == "margin"
        position = dec(
            (self.store.get("margin") or {})
            .get("positions", {})
            .get(pair.id, {})
            .get("quantity", 0)
        )
        state = {
            "strategy": self.settings["strategy"],
            "symbol": pair.symbol,
            "inventory": str(position if is_margin else self.balance(pair.base)),
            "long_only": not is_margin,
            "product": self.settings["product"],
            "note": "Paper margin: buy opens/increases long or closes short; sell opens/increases short or closes long"
            if is_margin
            else "Unleveraged spot",
        }
        if not maker:
            rows = await self.kraken.candles(pair, self.settings["candle_minutes"])
            state.update(trend_state(rows, self.settings, self.fees.reserve(pair)))
            candle = f"{pair.id}:{self.settings['candle_minutes']}:{state['candle_close_time']}"
            if self.store.get("candle:" + self.mode) == candle:
                return
            self.store.put("candle:" + self.mode, candle)
        book = await self.kraken.book(pair)
        bid_depth = sum(v for _, v in book.bids[:10])
        ask_depth = sum(v for _, v in book.asks[:10])
        state.update(
            mid=str(book.mid),
            spread_bps=str(book.spread_bps),
            liquidity="bid-heavy" if bid_depth > ask_depth else "ask-heavy",
            taker_fee_bps=str(self.fees.rate(pair)),
            maker_fee_bps=str(self.fees.rate(pair, True)),
        )
        side = await self.decision(state)
        if not self.running or side == "hold":
            return
        if not maker and not state["entry_eligible" if side == "buy" else "exit_eligible"]:
            self.event("skip", {"reason": "Deterministic trend/cost filter vetoed the Jev action"})
            return
        if not is_margin and side == "sell" and self.balance(pair.base) <= 0:
            self.event("skip", {"reason": "No bot-owned inventory to sell"})
            return
        # Re-read after inference; do not execute on an aged pre-model snapshot.
        book = await self.kraken.book(pair)
        fee = self.fees.reserve(pair, maker) / BPS
        price = limit_price(
            book,
            side,
            self.settings["slippage_bps"],
            maker_fee_bps=self.fees.reserve(pair, True) if maker else None,
        )
        _, exposure = await self.valuation(enforce=True)
        budget, exposure_cap = self.limits()
        if is_margin:
            volume = floor(budget / price, pair.lot)
            if (side == "sell" and position > 0) or (side == "buy" and position < 0):
                volume = min(volume, abs(position))
        elif side == "buy":
            budget = min(budget, self.balance(pair.quote) / (1 + fee), exposure_cap - exposure)
            volume = floor(max(ZERO, budget) / price, pair.lot)
        else:
            volume = floor(min(budget / price, self.balance(pair.base)), pair.lot)
        if volume < pair.minimum or volume * price < pair.cost_minimum:
            self.event(
                "skip", {"reason": "Available allocation/inventory is below market minimums"}
            )
            return
        await self.place(pair, side, volume, price, book, maker)

    async def arbitrage(self, pair):
        routes = triangle(pair, self.kraken.pairs)
        books = {leg.pair.id: await self.kraken.book(leg.pair) for leg in routes[0]}
        _, exposure = await self.valuation(enforce=True)
        order_cap, exposure_cap = self.limits()
        amount = min(
            order_cap / (1 + max(self.fees.reserve(leg.pair) for leg in routes[0]) / BPS),
            self.balance(pair.quote),
            exposure_cap - exposure,
        )
        if amount <= 0:
            self.event("skip", {"reason": "No free arbitrage allocation"})
            return
        candidates = []
        for route in routes:
            try:
                candidates.append(
                    (plan_cycle(route, books, amount, self.settings, self.fees), route)
                )
            except SafetyError:
                continue
        if not candidates:
            self.event("skip", {"reason": "Cycle fails minimum-size or depth requirements"})
            return
        plan, route = max(candidates, key=lambda item: dec(item[0]["edge_bps"]))
        # Reserve room for the profitable final leg: the per-order cap applies to
        # every leg's notional, not only the first leg's starting capital.
        quote_prices = {pair.quote: dec(1)}
        for snapshot in books.values():
            if snapshot.pair.quote == pair.quote:
                quote_prices[snapshot.pair.base] = snapshot.asks[0][0]
        peak_notional = max(
            dec(p["volume"]) * dec(p["limit"]) * quote_prices[leg.pair.quote]
            for p, leg in zip(plan["legs"], route, strict=True)
        )
        if peak_notional > order_cap:
            amount *= order_cap / peak_notional * dec("0.999")
            plan = plan_cycle(route, books, amount, self.settings, self.fees)
        eligible = dec(plan["edge_bps"]) >= dec(self.settings["arb_min_profit_bps"])
        action = await self.decision(
            {
                "strategy": "arbitrage",
                "symbol": pair.symbol,
                "plan": plan,
                "eligible_after_costs": eligible,
                "note": "Three sequential spot orders, not an atomic transaction",
            }
        )
        if not eligible or action != "buy":
            self.event("skip", {"reason": "No approved positive-net cycle after fees and slippage"})
            return
        # Recheck the whole opportunity after Jev, then refresh each leg before sending.
        books = {leg.pair.id: await self.kraken.book(leg.pair) for leg in route}
        for book in books.values():
            book.fresh(self.settings["stale_seconds"])
        plan = plan_cycle(route, books, amount, self.settings, self.fees)
        if dec(plan["edge_bps"]) < dec(self.settings["arb_min_profit_bps"]):
            self.event("skip", {"reason": "Arbitrage opportunity disappeared during assessment"})
            return
        cycle = {"id": str(uuid.uuid4()), "mode": self.mode, "plan": plan, "completed_legs": 0}
        self.store.put("cycle", cycle)
        self.recovery_required = True
        output = amount
        for index, leg in enumerate(route):
            book = await self.kraken.book(leg.pair)
            volume, price, _ = plan_leg(
                leg,
                book,
                output,
                self.fees.reserve(leg.pair),
                dec(self.settings["slippage_bps"]),
            )
            # Do not let a deteriorated leg escape the original cycle's price bounds.
            bound = dec(plan["legs"][index]["limit"])
            if (leg.side == "buy" and price > bound) or (leg.side == "sell" and price < bound):
                raise SafetyError(
                    "Arbitrage leg moved beyond its bound; inspect intermediate inventory"
                )
            order = await self.place(leg.pair, leg.side, volume, price, book)
            cycle["completed_legs"] = index + 1
            self.store.put("cycle", cycle)
            if dec(order["filled"]) != volume:
                raise SafetyError("Arbitrage leg partially filled; inspect intermediate inventory")
            output = (
                dec(order["filled"])
                if leg.side == "buy"
                else dec(order["cost"]) - dec(order["fee"])
            )
        self.store.put("cycle", None)
        self.recovery_required = False
        self.event(
            "cycle",
            {
                "id": cycle["id"],
                "mode": self.mode,
                "input": str(amount),
                "output": str(output),
                "note": "Residual rounding dust remains in the ledger",
            },
        )

    async def recover_capital(self):
        if not self.settings["recover_initial"] or self.settings["product"] != "spot":
            return False
        ledger = self.ledger()
        recovery = ledger["recovery"]
        if recovery["recovered"]:
            return False
        if time.time() - recovery["last_check"] < self.settings["recovery_check_seconds"]:
            return recovery["pending"]
        recovery["last_check"] = time.time()
        original = dec(recovery["original"])
        recovery["pending"] = dec(self.equity) > original * 2
        self.store.put("ledger:" + self.mode, ledger)
        if not recovery["pending"]:
            return False
        quote = self.settings["quote"]
        cash = self.balance(quote)
        if cash < original:
            # Recover only bot-owned inventory, with the same limits and IOC price bounds
            # as ordinary orders. One sale per check; no unbounded liquidation loop.
            for asset, quantity in ledger["balances"].items():
                if asset == quote or dec(quantity) <= 0:
                    continue
                pair = next(
                    (p for p in self.kraken.pairs.values() if p.base == asset and p.quote == quote),
                    None,
                )
                if pair is None:
                    continue
                snapshot = await self.kraken.book(pair)
                price = limit_price(snapshot, "sell", self.settings["slippage_bps"])
                fee = self.fees.reserve(pair) / BPS
                needed = (original - cash) / (price * (1 - fee))
                # Round up for proceeds, then down to the available inventory/order cap.
                desired = floor(needed, pair.lot) + pair.lot
                volume = floor(min(desired, dec(quantity), self.limits()[0] / price), pair.lot)
                if volume < pair.minimum or volume * price < pair.cost_minimum:
                    continue
                await self.place(pair, "sell", volume, price, snapshot)
                await self.valuation(enforce=True)
                ledger = self.ledger()
                recovery = ledger["recovery"]
                cash = self.balance(quote)
                break
        if cash >= original and dec(self.equity) > original * 2:
            ledger["balances"][quote] = str(cash - original)
            recovery.update(reserved=str(original), recovered=True, pending=False)
            day = self.store.get("day:" + self.mode)
            with self.store.db:
                self.store._put("ledger:" + self.mode, ledger)
                if day:
                    # Moving cash out of the trading allocation is not a trading loss.
                    day["equity"] = str(dec(day["equity"]) - original)
                    self.store._put("day:" + self.mode, day)
            self.event(
                "recovery",
                {
                    "mode": self.mode,
                    "amount": str(original),
                    "currency": quote,
                    "message": "Original allocation reserved once; no external withdrawal made",
                },
            )
            await self.valuation(enforce=True)
        else:
            self.event(
                "recovery",
                {
                    "mode": self.mode,
                    "message": "Capital recovery pending sufficient realized cash and equity",
                },
            )
        return True

    async def tick(self):
        async with self.lock:
            if not self.running:
                await self.refresh_fees(required=False)
                if self.settings["product"] in {"margin", "futures"}:
                    try:
                        await self.valuation(False)
                    except Exception as exc:
                        self.last_error = (
                            str(exc)
                            if isinstance(exc, SafetyError)
                            else "Portfolio valuation unavailable; remain stopped"
                        )
                self.emit_state()
                return
            try:
                await self.refresh_fees()
                if self.mode == "dry-run":
                    await self.paper_makers()
                await self.cancel_active()
                scalp = self.settings["strategy"] == "scalp"
                if scalp:
                    await scalping.run(self)
                else:
                    await self.valuation(enforce=True)
                pair = self.resolve(self.settings["pair"])
                recovered = False if scalp else await self.recover_capital()
                if not recovered and not scalp:
                    if self.settings["strategy"] in programs.STRATEGIES:
                        await programs.run(self)
                    elif self.settings["strategy"] == "arbitrage":
                        await self.arbitrage(pair)
                    else:
                        await self.directional(pair)
                await self.valuation(enforce=not scalp)
            except Exception as exc:
                self.running = False
                self.last_error = (
                    str(exc) if isinstance(exc, SafetyError) else "Internal failure; engine stopped"
                )
                self.event("error", {"message": self.last_error})
                try:
                    await self.cancel_active()
                except Exception:
                    self.event(
                        "error",
                        {
                            "message": "Cleanup could not confirm cancellation. Use Reconcile; inspect Kraken."
                        },
                    )
            finally:
                self.emit_state()

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(self.settings["interval_seconds"])

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        # Restart remains paused. GTD maker orders expire even if this cleanup fails.
        with contextlib.suppress(Exception):
            await self.stop()
