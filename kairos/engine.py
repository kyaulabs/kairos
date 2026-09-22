import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime, timedelta

from kairos import margin
from kairos.clients import ExchangeRejected
from kairos.domain import BPS, DEFAULTS, TERMINAL, ZERO, SafetyError, dec, floor, validate_settings
from kairos.strategies import plan_cycle, plan_leg, trend_state, triangle


class Engine:
    def __init__(self, store, kraken, jev, publish):
        self.store, self.kraken, self.jev, self.publish = store, kraken, jev, publish
        self.settings = validate_settings(store.get("settings", DEFAULTS))
        self.mode = "dry-run"  # Never auto-resume live trading after a restart.
        self.running = False
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
        ledger = self.store.get("margin") if self.settings["product"] == "margin" else self.ledger()
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
            "settings": self.settings,
            "error": self.last_error,
            "effective_order_cap": str(self.limits()[0]),
            "effective_exposure_cap": str(self.limits()[1]),
            "decision": self.latest_decision,
            "ledger": self.ledger(),
            "equity": self.equity,
            "exposure": self.exposure,
            "daily_pnl": self.daily_pnl,
            "valuation_ts": self.valuation_ts,
            "recovery_required": self.recovery_required or bool(self.orders("trading", True)),
            "orders": self.store.orders()[-100:],
            "cycle": self.store.get("cycle"),
            "margin": self.store.get("margin"),
            "capabilities": {
                "crypto_spot": "paper-and-live",
                "crypto_margin": "paper-only",
                "us_stocks": "not integrated: reviewed CLI offers xStocks, not brokerage stock orders",
            },
        }

    def emit_state(self):
        self.publish("state", self.snapshot())

    def resolve(self, name):
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

    async def initialize(self):
        await self.kraken.catalog()
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
            pair = self.resolve(new["pair"])
            new["pair"] = pair.id
            if pair.quote != new["quote"]:
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
            self.equity = self.exposure = self.daily_pnl = self.valuation_ts = None
            self.event("settings", {"message": "Settings saved", "settings": new})
            self.emit_state()

    async def reset_paper(self):
        async with self.lock:
            if self.running or self.orders("dry-run", True):
                raise SafetyError("Stop paper trading before resetting")
            if self.settings["product"] == "margin":
                self.store.put("margin", margin.new_ledger(self.settings["paper_balance"]))
                self.store.put("day:margin", None)
            else:
                self.reset_ledger("dry-run", self.settings["paper_balance"])
            self.store.put("candle:dry-run", None)
            self.equity = self.exposure = self.daily_pnl = self.valuation_ts = None
            self.event("system", {"message": "Paper ledger reset; historical events retained"})
            self.emit_state()

    async def live_preflight(self):
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
        pair = self.resolve(self.settings["pair"])
        pairs = (
            [leg.pair for leg in triangle(pair, self.kraken.pairs)[0]]
            if self.settings["strategy"] == "arbitrage"
            else [pair]
        )
        maker, taker = await self.kraken.fees(pairs)
        if any(
            taker[p.id] > dec(self.settings["taker_fee_bps"])
            or maker.get(p.id, taker[p.id]) > dec(self.settings["maker_fee_bps"])
            for p in pairs
        ):
            raise SafetyError("Configured fees underestimate the account's Kraken fees")
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
                    if confirmation != "ENABLE LIVE TRADING":
                        raise SafetyError("Explicit live-trading confirmation is required")
                    await self.live_preflight()
                self.mode = mode
                self.equity = self.exposure = self.daily_pnl = self.valuation_ts = None
                self.last_error = None
                if was_running:
                    await self.valuation(enforce=True)
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
            if self.mode == "trading":
                await self.live_preflight()
            if not self.jev.key:
                raise SafetyError("JEV_API_KEY is not configured")
            await self.valuation(enforce=True)
            self.running, self.last_error = True, None
            self.event("system", {"message": "Started", "mode": self.mode})
            self.emit_state()

    async def stop(self):
        # Latch the stop before waiting for an in-flight data/model request.
        self.running = False
        async with self.lock:
            try:
                await self.cancel_active()
                self.event(
                    "system", {"message": "Stopped; tracked orders reconciled. Holdings retained."}
                )
            finally:
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
            self.recovery_required = False
            self.last_error = None
            self.event("system", {"message": "Reconciliation completed; holdings retained"})
            self.emit_state()

    async def valuation(self, enforce=False):
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
        day_key = datetime.now(UTC).date().isoformat()
        day = self.store.get("day:" + self.mode)
        if not day or day["date"] != day_key:
            day = {"date": day_key, "equity": str(equity)}
            self.store.put("day:" + self.mode, day)
        self.equity, self.exposure = str(equity), str(exposure)
        self.daily_pnl = str(equity - dec(day["equity"]))
        self.valuation_ts = time.time()
        if enforce and -dec(self.daily_pnl) >= dec(self.settings["daily_loss"]):
            raise SafetyError("Daily marked-to-market loss limit reached; no further orders")
        return prices, exposure

    def apply(self, order, total_volume, total_cost, total_fee, status):
        """Apply cumulative Kraken/paper fills exactly once, including partial fills."""
        volume = total_volume - dec(order["filled"])
        cost = total_cost - dec(order["cost"])
        fee = total_fee - dec(order["fee"])
        if min(volume, cost, fee) < 0:
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
        for order in self.orders("dry-run", True):
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
        self.equity, self.exposure = str(values["equity"]), str(values["exposure"])
        self.valuation_ts = time.time()
        date = datetime.now(UTC).date().isoformat()
        day = self.store.get("day:margin")
        if not day or day["date"] != date:
            day = {"date": date, "equity": self.equity}
            self.store.put("day:margin", day)
        self.daily_pnl = str(values["equity"] - dec(day["equity"]))
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
                    cost * dec(self.settings["taker_fee_bps"]) / BPS,
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
            "fee_bps": self.settings["maker_fee_bps" if maker else "taker_fee_bps"],
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
                * (dec(self.settings["taker_fee_bps"]) + dec(self.settings["margin_open_fee_bps"]))
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

    async def place(self, pair, side, volume, price, book, maker=False):
        if not self.running:
            raise SafetyError("Engine is stopped")
        if self.orders(self.mode, True):
            raise SafetyError("Previous order must settle before placing another")
        pair.validate(volume, price)
        if self.settings["product"] == "margin":
            return await self.place_margin(pair, side, volume, price, book, maker)
        prices, exposure = await self.valuation(enforce=True)
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
        fee_bps = dec(self.settings["maker_fee_bps" if maker else "taker_fee_bps"])
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
            {pair.quote: volume * price * (1 + fee_bps / BPS)}
            if side == "buy"
            else {pair.base: volume, pair.quote: volume * price * fee_bps / BPS if maker else ZERO}
        )
        # A spot sell's fee is paid from its proceeds, not from a separate quote reserve.
        if side == "sell":
            needed = {pair.base: volume}
        for asset, amount in needed.items():
            if self.balance(asset) < amount:
                raise SafetyError("Insufficient allocated funds; spot inventory cannot go short")
        if self.mode == "trading":
            if not self.kraken.allow_live:
                raise SafetyError("Live writes disabled")
            status = await self.kraken.request("SystemStatus")
            if status.get("status") != "online":
                raise SafetyError("Kraken trading is not online")
            balances = await self.kraken.balances()
            if any(balances.get(asset, ZERO) < amount for asset, amount in needed.items()):
                raise SafetyError("Insufficient exchange funds after holds")
        book.fresh(self.settings["stale_seconds"])
        if book.spread_bps > dec(self.settings["max_spread_bps"]):
            raise SafetyError("Spread exceeds configured maximum")
        if not self.running:
            raise SafetyError("Engine stopped before order submission")
        now = time.time()
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
        self.store.save_order(order)  # Durable intent before the network write.
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
            state.update(trend_state(rows, self.settings))
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
            taker_fee_bps=self.settings["taker_fee_bps"],
            maker_fee_bps=self.settings["maker_fee_bps"],
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
        fee = dec(self.settings["maker_fee_bps" if maker else "taker_fee_bps"]) / BPS
        if maker:
            # Quote away from mid enough to include assumed fees, rather than claiming
            # the tiny touch spread covers Kraken's retail fee tier.
            width = fee + dec(self.settings["slippage_bps"]) / BPS
            raw = (
                min(book.bids[0][0], book.mid * (1 - width))
                if side == "buy"
                else max(book.asks[0][0], book.mid * (1 + width))
            )
        else:
            slip = dec(self.settings["slippage_bps"]) / BPS
            raw = book.asks[0][0] * (1 + slip) if side == "buy" else book.bids[0][0] * (1 - slip)
        price = pair.price(raw, side)
        await self.valuation(enforce=True)
        budget, exposure_cap = self.limits()
        if is_margin:
            volume = floor(budget / price, pair.lot)
            if (side == "sell" and position > 0) or (side == "buy" and position < 0):
                volume = min(volume, abs(position))
        elif side == "buy":
            _, exposure = await self.valuation(enforce=True)
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
            order_cap / (1 + dec(self.settings["taker_fee_bps"]) / BPS),
            self.balance(pair.quote),
            exposure_cap - exposure,
        )
        if amount <= 0:
            self.event("skip", {"reason": "No free arbitrage allocation"})
            return
        candidates = []
        for route in routes:
            try:
                candidates.append((plan_cycle(route, books, amount, self.settings), route))
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
            plan = plan_cycle(route, books, amount, self.settings)
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
        plan = plan_cycle(route, books, amount, self.settings)
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
                dec(self.settings["taker_fee_bps"]),
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
                price = pair.price(
                    snapshot.bids[0][0] * (1 - dec(self.settings["slippage_bps"]) / BPS), "sell"
                )
                fee = dec(self.settings["taker_fee_bps"]) / BPS
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
                if self.settings["product"] == "margin":
                    try:
                        await self.margin_valuation(False)
                    except SafetyError as exc:
                        self.last_error = str(exc)
                    self.emit_state()
                return
            try:
                if self.mode == "dry-run":
                    await self.paper_makers()
                await self.cancel_active()
                await self.valuation(enforce=True)
                pair = self.resolve(self.settings["pair"])
                recovered = await self.recover_capital()
                if not recovered:
                    if self.settings["strategy"] == "arbitrage":
                        await self.arbitrage(pair)
                    else:
                        await self.directional(pair)
                await self.valuation(enforce=True)
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
