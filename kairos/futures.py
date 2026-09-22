"""Separate USD linear-perpetual accounting and guarded execution."""

import asyncio
import time
import uuid
from datetime import UTC, datetime

from kairos.domain import BPS, TERMINAL, ZERO, SafetyError, dec, floor
from kairos.futures_client import UnsettledFutures, timestamp
from kairos.strategies import limit_price, trend_state


def new_ledger(amount):
    return {
        "initial": str(amount),
        "cash": str(amount),
        "positions": {},
        "fees": "0",
        "funding": "0",
        "funding_ts": time.time(),
        "halted": False,
    }


def apply_fill(ledger, order, filled, cost, fee):
    volume = filled - dec(order["filled"])
    delta_cost, delta_fee = cost - dec(order["cost"]), fee - dec(order["fee"])
    if (
        volume < 0
        or delta_cost < 0
        or filled > dec(order["volume"])
        or (volume == 0 and delta_cost != 0)
        or (volume > 0 and delta_cost <= 0)
    ):
        raise SafetyError("Futures cumulative accounting moved backwards or exceeded intent")
    position = ledger["positions"].get(order["pair"], {"quantity": "0", "entry": "0"})
    old, entry = dec(position["quantity"]), dec(position["entry"])
    change = volume * (1 if order["side"] == "buy" else -1)
    reducing = old * change < 0
    if order["reduce_only"] and volume and (not reducing or volume > abs(old)):
        raise SafetyError("Futures reduce-only fill would increase or reverse exposure")
    realized = ZERO
    if volume:
        price = delta_cost / volume
        if (order["side"] == "buy" and price > dec(order["price"])) or (
            order["side"] == "sell" and price < dec(order["price"])
        ):
            raise SafetyError("Futures fill exceeded its price limit")
        if reducing:
            if volume > abs(old):
                raise SafetyError("Close a Futures position before reversing it")
            realized = volume * (price - entry) * (1 if old > 0 else -1)
        else:
            entry = (abs(old) * entry + delta_cost) / (abs(old) + volume)
    quantity = old + change
    ledger["positions"][order["pair"]] = {
        "quantity": str(quantity),
        "entry": str(entry if quantity else ZERO),
    }
    ledger["cash"] = str(dec(ledger["cash"]) + realized - delta_fee)
    ledger["fees"] = str(dec(ledger["fees"]) + delta_fee)


class FuturesDesk:
    def __init__(self, engine, client):
        self.engine, self.client = engine, client

    @property
    def settings(self):
        return self.engine.settings

    def ledger(self, mode=None):
        return self.engine.store.get("futures:" + (mode or self.engine.mode))

    def has_live_position(self):
        return any(
            dec(p["quantity"]) for p in (self.ledger("trading") or {}).get("positions", {}).values()
        )

    def ensure_paper(self):
        if not self.ledger("dry-run"):
            self.engine.store.put("futures:dry-run", new_ledger(self.settings["paper_balance"]))

    def position(self, pair, mode=None):
        return dec(
            (self.ledger(mode) or {}).get("positions", {}).get(pair.id, {}).get("quantity", 0)
        )

    def margin_rates(self, pair, notional):
        row = self.client.metadata[pair.id]
        schedules = [row["marginLevels"], row.get("retailMarginLevels", [])]
        schedules.extend(
            levels
            for region in row.get("marginSchedules", {}).values()
            for levels in region.values()
        )
        applicable = [
            level
            for levels in schedules
            for level in levels
            if dec(level["numNonContractUnits"]) <= notional
        ]
        initial = max(dec(level["initialMargin"]) for level in applicable)
        maintenance = max(dec(level["maintenanceMargin"]) for level in applicable)
        if not ZERO < maintenance < initial <= 1:
            raise SafetyError("Unsupported Futures margin schedule")
        # Conservative across published jurisdiction tiers, plus the user's own leverage cap.
        return max(initial, dec(1) / self.settings["futures_leverage"]), maintenance

    async def funding(self, ledger):
        now, since = time.time(), ledger["funding_ts"]
        if now < since:
            raise SafetyError("Clock moved backwards during Futures funding accounting")
        total = ZERO
        for key, position in ledger["positions"].items():
            quantity = dec(position["quantity"])
            if not quantity:
                continue
            pair = self.engine.resolve(key)
            data = await self.client.get("historical-funding-rates", {"symbol": pair.symbol})
            rates = sorted(
                (timestamp(r["timestamp"]), dec(r["fundingRate"])) for r in data["rates"]
            )
            cursor = since
            for start, rate in rates:
                end = min(now, start + 3600)
                if end <= cursor or start >= now:
                    continue
                if start > cursor:
                    raise SafetyError(
                        "Missing Futures funding history; cannot value paper position"
                    )
                total += quantity * rate * dec(end - cursor) / 3600
                cursor = end
            if cursor < now:
                # Current-hour rate from a fresh ticker; never extend it over historical gaps.
                hour = int(now // 3600) * 3600
                if cursor < hour:
                    raise SafetyError("Incomplete Futures funding history")
                market = await self.client.market(pair)
                total += quantity * dec(market["fundingRate"]) * dec(now - cursor) / 3600
        ledger["cash"] = str(dec(ledger["cash"]) - total)
        ledger["funding"] = str(dec(ledger["funding"]) + total)
        ledger["funding_ts"] = now

    async def live_sync(self):
        engine = self.engine
        ledger = self.ledger("trading")
        if not ledger:
            raise SafetyError("No allocated Futures wallet; explicitly arm live Futures first")
        # Read histories before comparing the independent account snapshot. Unknown activity halts.
        owned = {o["id"] for o in engine.orders("trading") if o.get("product") == "futures"}
        for element in await self.client.history("executions", ledger["armed_at"]):
            execution = element["event"]["execution"]["execution"]
            if execution["order"].get("clientId") not in owned:
                raise SafetyError("External Futures fill or liquidation detected; inspect account")
        if ledger.get("account_uid") != self.client.account_uid:
            raise SafetyError(
                "Futures allocation belongs to another wallet; refusing account crossover"
            )
        for row in (await self.client.get("openorders"))["openOrders"]:
            if row.get("cliOrdId") not in owned:
                raise SafetyError("Untracked Futures order; dedicated wallet required")
        preferences = (await self.client.get("pnlpreferences"))["preferences"]
        if any(row["pnlCurrency"] != "USD" for row in preferences):
            raise SafetyError("Set Futures PnL settlement to USD before trading")
        leverage = (await self.client.get("leveragepreferences"))["leveragePreferences"]
        symbol = self.engine.resolve(self.settings["pair"]).symbol
        if any(row["symbol"] == symbol and row.get("maxLeverage") is not None for row in leverage):
            raise SafetyError(
                "Select cross margin on Kraken for this Futures contract; isolated margin is not modeled"
            )
        logs = await self.client.history("account-log", ledger["armed_at"])
        log_cash = dec(ledger["initial"])
        for row in sorted(logs, key=lambda r: r["id"]):
            if row["margin_account"] != "flex":
                continue
            if row["info"] not in {"futures trade", "funding rate change"}:
                raise SafetyError(
                    "External Futures wallet change detected; no automatic allocation"
                )
            if row.get("collateral") not in (None, "USD"):
                raise SafetyError("Non-USD Futures settlement detected")
            if row["asset"] == "USD":
                if abs(dec(row["old_balance"]) - log_cash) > dec("0.00000001"):
                    raise SafetyError("Futures cash history has a gap; remain stopped")
                log_cash = dec(row["new_balance"])
        positions = (await self.client.get("openpositions"))["openPositions"]
        actual = {}
        for row in positions:
            if (
                row.get("pnlCurrency") not in (None, "USD")
                or row.get("maxFixedLeverage") is not None
                or row["side"] not in ("long", "short")
            ):
                raise SafetyError("Unsupported Futures position settlement")
            if "futures:" + row["symbol"] in actual or dec(row["size"]) < 0:
                raise SafetyError("Invalid or duplicate Futures position")
            actual["futures:" + row["symbol"]] = dec(row["size"]) * (
                1 if row["side"] == "long" else -1
            )
        expected = {
            k: dec(p["quantity"]) for k, p in ledger["positions"].items() if dec(p["quantity"])
        }
        if {k: q for k, q in actual.items() if q} != expected:
            raise SafetyError("Futures positions differ from confirmed bot fills; reconcile")
        account = await self.client.account()
        # Entire dedicated USD wallet was explicitly allocated at arming, never imported later.
        cash = dec(account["currencies"]["USD"]["quantity"])
        if abs(cash - log_cash) > dec("0.00000001"):
            raise SafetyError("Futures account and cash history disagree; reconcile again")
        ledger["funding"] = str(dec(ledger["funding"]) + dec(ledger["cash"]) - cash)
        ledger["cash"] = str(cash)
        ledger["unrealized_funding"] = str(dec(account["unrealizedFunding"]))
        ledger["account_available"] = str(dec(account["availableMargin"]))
        ledger["account_maintenance"] = str(dec(account["maintenanceMargin"]))
        engine.store.put("futures:trading", ledger)
        return account

    def prepare_program(self, pair):
        settings = self.settings
        side = (
            settings["futures_dca_side"] if settings["strategy"] == "dca" else settings["twap_side"]
        )
        quantity = self.position(pair)
        if settings["futures_reduce_only"]:
            if quantity * (1 if side == "buy" else -1) >= 0 or (
                settings["strategy"] == "twap" and dec(settings["twap_quantity"]) > abs(quantity)
            ):
                raise SafetyError(
                    "Pre-fund reduce-only Futures program with an opposing bot position"
                )
            return
        if quantity * (1 if side == "buy" else -1) < 0:
            raise SafetyError(
                "Close opposing Futures position or select reduce-only before a scheduled run"
            )
        cap = (
            dec(settings["dca_amount"]) * settings["dca_count"]
            if settings["strategy"] == "dca"
            else dec(settings["futures_parent_notional"])
        )
        if (
            settings["strategy"] == "twap"
            and dec(settings["twap_quantity"]) * dec(settings["twap_limit"]) > cap
        ):
            raise SafetyError("TWAP quantity at its limit exceeds Futures parent notional cap")
        exposure = dec(self.engine.exposure)
        im, _ = self.margin_rates(pair, exposure + cap)
        reserve = dec(self.ledger()["initial"]) * dec("0.2")
        if dec(self.engine.equity) - exposure * im - reserve < cap * (
            im + 2 * dec(settings["taker_fee_bps"]) / BPS
        ):
            raise SafetyError(
                "Pre-fund entire Futures parent margin, round-trip fees and 20% collateral reserve"
            )

    async def preflight(self):
        engine = self.engine
        if not self.client or not self.client.allow_live or not engine.kraken.allow_live:
            raise SafetyError(
                "Enable both ALLOW_LIVE_TRADING and ALLOW_FUTURES_TRADING before arming Futures"
            )
        await self.client.catalog()
        pair = engine.resolve(self.settings["pair"])
        await self.client.market(pair)
        maker, taker = await self.client.fees(engine.kraken, pair)
        if maker > dec(self.settings["maker_fee_bps"]) or taker > dec(
            self.settings["taker_fee_bps"]
        ):
            raise SafetyError("Configured Futures fees underestimate current account fees")
        budget = dec(self.settings["futures_live_budget"])
        if budget <= 0:
            raise SafetyError("Explicit positive Futures collateral allocation required")
        if not self.ledger("trading"):
            armed_at = time.time()
            if (await self.client.get("openorders"))["openOrders"] or (
                await self.client.get("openpositions")
            )["openPositions"]:
                raise SafetyError(
                    "First Futures arming requires an empty dedicated derivatives wallet"
                )
            account = await self.client.account()
            if (
                dec(account["currencies"]["USD"]["quantity"]) != budget
                or dec(account["availableMargin"]) < budget
            ):
                raise SafetyError(
                    "Pre-fund a dedicated USD-only Futures wallet with exactly the configured allocation"
                )
            await self.client.history("orders", armed_at)
            if not self.client.account_uid:
                raise SafetyError("Futures account identity is unavailable")
            ledger = new_ledger(budget)
            ledger["account_uid"] = self.client.account_uid
            ledger["armed_at"] = armed_at
            engine.store.put("futures:trading", ledger)
        if dec(self.ledger("trading")["initial"]) != budget:
            raise SafetyError(
                "Existing live Futures allocation is fixed; it cannot be silently resized"
            )
        await self.live_sync()

    async def valuation(self, enforce=False, *, exit_only=False):
        engine = self.engine
        self.ensure_paper()
        if engine.mode == "trading":
            for order in engine.orders("trading", True):
                if order.get("product") == "futures":
                    await self.settle(order)
            await self.live_sync()
        ledger = self.ledger()
        if engine.mode == "dry-run":
            await self.funding(ledger)
            engine.store.put("futures:dry-run", ledger)
        equity = dec(ledger["cash"]) + dec(ledger.get("unrealized_funding", 0))
        exposure = initial = maintenance = ZERO
        for key, position in ledger["positions"].items():
            quantity = dec(position["quantity"])
            if not quantity:
                continue
            pair = engine.resolve(key)
            mark = dec((await self.client.market(pair))["markPrice"])
            notional = abs(quantity) * mark
            im, mm = self.margin_rates(pair, notional)
            equity += quantity * (mark - dec(position["entry"]))
            exposure += notional
            initial += notional * im
            maintenance += notional * mm
        engine.record_valuation(equity, exposure, "day:futures:" + engine.mode)
        values = {
            "equity": equity,
            "exposure": exposure,
            "initial": initial,
            "maintenance": maintenance,
            "free": min(equity - initial, dec(ledger.get("account_available", equity - initial))),
        }
        if (
            not exit_only
            and exposure
            and equity <= max(maintenance, dec(ledger.get("account_maintenance", 0)))
        ):
            if engine.mode == "dry-run" and not ledger["halted"]:
                ledger["halted"] = True
                engine.store.put("futures:dry-run", ledger)
                await engine.cancel_active()
                for key, position in ledger["positions"].items():
                    quantity = dec(position["quantity"])
                    if not quantity:
                        continue
                    pair = engine.resolve(key)
                    book = await self.client.book(pair)
                    side = "sell" if quantity > 0 else "buy"
                    price = pair.price(
                        book.mid * (dec("0.95") if side == "sell" else dec("1.05")), side
                    )
                    order = self.intent(pair, side, abs(quantity), price, False, True)
                    order["liquidation"] = True
                    engine.store.save_order(order)
                    filled, cost = book.fill(side, abs(quantity), price)
                    self.apply(
                        order,
                        filled,
                        cost,
                        cost * (dec(self.settings["taker_fee_bps"]) + 50) / BPS,
                        "canceled",
                    )
                engine.event(
                    "liquidation",
                    {
                        "message": "Paper Futures maintenance breach; bounded depth liquidation attempted, residual positions may remain"
                    },
                )
            raise SafetyError("Futures maintenance threshold breached; inspect remaining positions")
        if enforce and exposure > engine.limits()[1]:
            raise SafetyError("Marked Futures exposure exceeds cap; pause and reduce position")
        if enforce and (
            ledger["halted"] or -dec(engine.daily_pnl) >= dec(self.settings["daily_loss"])
        ):
            raise SafetyError("Futures loss limit or liquidation halt reached")
        return values, exposure

    def intent(self, pair, side, volume, price, maker, reducing):
        now = time.time()
        return {
            "id": str(uuid.uuid4()),
            "txid": None,
            "mode": self.engine.mode,
            "product": "futures",
            "strategy": self.settings["strategy"],
            "pair": pair.id,
            "symbol": pair.symbol,
            "base": pair.base,
            "quote": "USD",
            "side": side,
            "volume": str(volume),
            "price": str(price),
            "maker": maker,
            "reduce_only": reducing,
            "created": now,
            "expires": now + 30,
            "fee_bps": self.settings["maker_fee_bps" if maker else "taker_fee_bps"],
            "status": "submitting",
            "filled": "0",
            "cost": "0",
            "fee": "0",
        }

    def apply(self, order, filled, cost, fee, status):
        ledger = self.ledger(order["mode"])
        delta, delta_cost = filled - dec(order["filled"]), cost - dec(order["cost"])
        apply_fill(ledger, order, filled, cost, fee)
        order.update(filled=str(filled), cost=str(cost), fee=str(fee), status=status)
        self.engine.store.save_order(order, ledger)
        if delta:
            self.engine.event(
                "fill",
                {
                    "mode": order["mode"],
                    "product": "futures",
                    "pair": order["pair"],
                    "side": order["side"],
                    "volume": str(delta),
                    "cost": str(delta_cost),
                    "fee": str(fee),
                    "order_id": order["id"],
                    "simulated": order["mode"] == "dry-run",
                },
            )

        if order["mode"] == "trading" and fee > cost * dec(order["fee_bps"]) / BPS + dec(
            "0.00000001"
        ):
            raise SafetyError(
                "Actual Futures fees exceeded reserve; fills recorded, remain stopped"
            )

    async def refresh(self, order):
        txid, filled, cost, fee, status = await self.client.order_state(order)
        order["txid"] = txid
        self.apply(order, filled, cost, fee, status)

    async def settle(self, order, *, terminal=False):
        for attempt in range(5):
            try:
                await self.refresh(order)
                if not terminal or order["status"] in TERMINAL:
                    return
            except UnsettledFutures:
                pass
            if attempt < 4:
                await asyncio.sleep(1)
        raise SafetyError("Futures history did not settle; remain stopped and reconcile")

    async def cancel(self, order):
        # Cancel by durable client ID even if an acknowledgement was lost. Never resubmit.
        if not self.client.allow_live:
            await self.refresh(order)
            if order["status"] in TERMINAL:
                return
            raise SafetyError("Futures writes disabled; cancel on Kraken and reconcile history")
        await self.client.request("cancelorder", {"cliOrdId": order["id"]})
        await self.settle(order, terminal=True)

    async def paper_makers(self):
        for order in self.engine.orders("dry-run", True):
            if order.get("product") != "futures":
                continue
            if time.time() >= order["expires"]:
                order["status"] = "expired"
                self.engine.store.save_order(order)
                continue
            pair = self.engine.resolve(order["pair"])
            book = await self.client.book(pair)
            price = dec(order["price"])
            levels = book.asks if order["side"] == "buy" else book.bids
            visible = sum(
                (q for p, q in levels if (p < price if order["side"] == "buy" else p > price)), ZERO
            )
            volume = floor(
                min(visible * dec("0.1"), dec(order["volume"]) - dec(order["filled"])), pair.lot
            )
            await self.valuation(False)
            cost = dec(order["cost"]) + volume * price
            filled = dec(order["filled"]) + volume
            self.apply(
                order,
                filled,
                cost,
                cost * dec(order["fee_bps"]) / BPS,
                "closed" if filled == dec(order["volume"]) else "open",
            )

    async def place(
        self, pair, side, volume, price, book, maker=False, *, program=None, close=False
    ):
        engine = self.engine
        stop_generation = engine.stop_generation
        if (not engine.running and not close) or engine.orders(active=True):
            raise SafetyError("Futures engine stopped or previous order unresolved")
        await self.client.catalog()
        if self.client.pairs.get(pair.id) != pair:
            raise SafetyError("Futures contract rules changed; reload settings before trading")
        pair.validate(volume, price)
        if side not in {"buy", "sell"}:
            raise SafetyError("Invalid Futures side")
        values, exposure = await self.valuation(enforce=not close, exit_only=close)
        old = self.position(pair)
        reducing = old * (1 if side == "buy" else -1) < 0
        if (close or self.settings["futures_reduce_only"] and program) and not reducing:
            raise SafetyError("Reduce-only Futures order requires an opposing position")
        if reducing and volume > abs(old):
            raise SafetyError("Close a Futures position before reversing it")
        notional = volume * price
        order_cap, exposure_cap = engine.limits()
        if notional > order_cap:
            raise SafetyError("Futures order exceeds notional cap")
        market = await self.client.market(pair)
        mark = dec(market["markPrice"])
        if not maker and (market.get("postOnly") or self.client.metadata[pair.id].get("postOnly")):
            raise SafetyError("Futures market currently permits post-only orders")
        fee_bps = dec(self.settings["maker_fee_bps" if maker else "taker_fee_bps"])
        if not reducing:
            risk_notional = volume * max(price, mark)
            im, _ = self.margin_rates(pair, exposure + risk_notional)
            # Reserve 20% of original collateral and closing fees. Never borrow/consolidate cash.
            required = (
                max(ZERO, (exposure + risk_notional) * im - values["initial"])
                + notional * (fee_bps + dec(self.settings["taker_fee_bps"])) / BPS
            )
            reserve = dec(self.ledger()["initial"]) * dec("0.2")
            if exposure + risk_notional > exposure_cap or values["free"] - reserve < required:
                raise SafetyError("Futures exposure or reserved collateral limit exceeded")
            if abs(old) + volume > dec(self.client.metadata[pair.id]["maxPositionSize"]):
                raise SafetyError("Futures contract position limit exceeded")
        if engine.mode == "trading":
            if not self.client.allow_live or not engine.kraken.allow_live:
                raise SafetyError("Futures live writes disabled")
            maker_fee, taker_fee = await self.client.fees(engine.kraken, pair)
            if (maker_fee if maker else taker_fee) > fee_bps:
                raise SafetyError("Current Futures fees exceed the configured reserve")
            # Account-wide switch is permitted only for the dedicated wallet validated above.
            switch = await self.client.request("cancelallordersafter", {"timeout": 60})
            trigger = switch.get("status", {}).get("triggerTime")
            if not trigger or not time.time() + 10 < timestamp(trigger) <= time.time() + 90:
                raise SafetyError("Futures dead-man switch was not confirmed")
        book.fresh(self.settings["stale_seconds"])
        if book.spread_bps > dec(self.settings["max_spread_bps"]):
            raise SafetyError("Futures spread exceeds maximum")
        if maker and (
            (side == "buy" and price >= book.asks[0][0])
            or (side == "sell" and price <= book.bids[0][0])
        ):
            raise SafetyError("Futures post-only price crosses the book")
        if (not engine.running and not close) or engine.stop_generation != stop_generation:
            raise SafetyError("Stopped before Futures order submission")
        deadline = min(time.time() + 5, program["deadline"] if program else time.time() + 5)
        if time.time() + 1 >= deadline:
            raise SafetyError("Futures scheduled slot expired before submission")
        order = self.intent(pair, side, volume, price, maker, reducing)
        if program:
            order.update(program_id=program["id"], program_slot=program["slot"])
        engine.store.save_order(order)
        if engine.mode == "dry-run":
            if maker:
                order["status"] = "open"
                engine.store.save_order(order)
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
            try:
                result = await self.client.request(
                    "sendorder",
                    {
                        "symbol": pair.symbol,
                        "side": side,
                        "size": str(volume),
                        "limitPrice": str(price),
                        "orderType": "post" if maker else "ioc",
                        "reduceOnly": str(reducing).lower(),
                        "cliOrdId": order["id"],
                        "processBefore": datetime.fromtimestamp(deadline, UTC).isoformat(
                            timespec="milliseconds"
                        ),
                    },
                )
                status = result["sendStatus"]
                # Only explicit documented rejections establish that no order was accepted.
                if status["status"] in {
                    "invalidPrice",
                    "invalidSize",
                    "insufficientAvailableFunds",
                    "wouldCauseLiquidation",
                    "postWouldExecute",
                    "iocWouldNotExecute",
                    "wouldNotReducePosition",
                    "marketSuspended",
                    "marketInactive",
                    "wouldProcessAfterSpecifiedTime",
                    "invalidDomainConfiguration",
                }:
                    order["status"] = "rejected"
                    engine.store.save_order(order)
                    raise SafetyError(
                        "Futures rejected the order; inspect configuration and permissions"
                    )
                if status["status"] not in {"placed", "partiallyFilled", "filled"}:
                    raise SafetyError("Unrecognized Futures order outcome")
                order["txid"], order["status"] = status["order_id"], "open"
                engine.store.save_order(order)
            except Exception:
                if order["status"] != "rejected":
                    order["status"] = "uncertain"
                    engine.store.save_order(order)
                raise SafetyError(
                    "Futures submission not settled; reconcile without resubmitting"
                ) from None
            if not maker:
                await self.settle(order, terminal=True)
        engine.event("order", order)
        return order

    async def directional(self, pair):
        engine = self.engine
        maker = self.settings["strategy"] == "maker"
        position = self.position(pair)
        state = {
            "strategy": self.settings["strategy"],
            "symbol": pair.symbol,
            "product": "futures",
            "inventory": str(position),
            "long_only": False,
            "note": "USD linear perpetual; buys close shorts before opening longs, sells close longs before opening shorts. Funding and liquidation risk apply.",
        }
        if not maker:
            state.update(
                trend_state(
                    await self.client.completed_candles(pair, self.settings["candle_minutes"]),
                    self.settings,
                )
            )
            state["short_entry_eligible"] = state["exit_eligible"] and dec(
                state["eight_candle_return_bps"]
            ) < -dec(state["round_trip_cost_bps"])
            key = "candle:futures:" + engine.mode
            candle = f"{pair.id}:{self.settings['candle_minutes']}:{state['candle_close_time']}"
            if engine.store.get(key) == candle:
                return
            engine.store.put(key, candle)
        book = await self.client.book(pair)
        state.update(
            mid=str(book.mid),
            spread_bps=str(book.spread_bps),
            maker_fee_bps=self.settings["maker_fee_bps"],
            taker_fee_bps=self.settings["taker_fee_bps"],
            equity=engine.equity,
            leverage_cap=self.settings["futures_leverage"],
        )
        side = await engine.decision(state)
        if not engine.running or side == "hold":
            return
        allowed = True
        if not maker:
            if side == "buy":
                allowed = state["trend"] == "rising" if position < 0 else state["entry_eligible"]
            else:
                allowed = state["exit_eligible"] if position > 0 else state["short_entry_eligible"]
        if not allowed:
            engine.event("skip", {"reason": "Futures trend/cost filter vetoed the model action"})
            return
        book = await self.client.book(pair)
        price = limit_price(
            book,
            side,
            self.settings["slippage_bps"],
            maker_fee_bps=self.settings["maker_fee_bps"] if maker else None,
        )
        values, exposure = await self.valuation(True)
        cap, maximum = engine.limits()
        reducing = position * (1 if side == "buy" else -1) < 0
        if reducing:
            volume = min(abs(position), floor(cap / price, pair.lot))
        else:
            im, _ = self.margin_rates(pair, exposure + cap)
            budget = min(
                cap,
                maximum - exposure,
                max(ZERO, values["free"] - dec(self.ledger()["initial"]) * dec("0.2"))
                / (im + 2 * dec(self.settings["taker_fee_bps"]) / BPS),
            )
            volume = floor(max(ZERO, budget) / price, pair.lot)
        if volume < pair.minimum:
            engine.event("skip", {"reason": "Futures collateral or notional cap below minimum"})
            return
        await self.place(pair, side, volume, price, book, maker)
