"""Event-ordered bar scenarios, NOT an Alpaca queue or production-engine reproduction."""

import hashlib
from decimal import ROUND_DOWN

from kairos.domain import SafetyError, dec
from kairos.research.signals import allocation

ZERO = dec(0)


def down(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def draw(seed, symbol, timestamp, side, purpose):
    text = f"{seed}:{symbol}:{timestamp}:{side}:{purpose}".encode()
    return int.from_bytes(hashlib.sha256(text).digest()[:8], "big") / 2**64


class Scenario:
    def __init__(self, plan, symbol, arm, scenario, model, scale):
        self.plan, self.symbol, self.arm, self.model = plan, symbol, arm, model
        self.risk, self.cost = plan["risk"], plan["scenarios"][scenario]
        self.scenario, self.scale = scenario, dec(scale)
        if arm not in plan["arms"] + plan["benchmarks"] or not 0 <= self.scale <= 1:
            raise SafetyError("Unregistered arm or leveraged risk matching")
        self.reference = arm == "buy_hold_reference"
        self.benchmark = arm in plan["benchmarks"]
        self.initial = self.cash = dec(self.risk["initial_usd"])
        self.cap, self.order_cap = (
            dec(self.risk["exposure_cap_usd"]),
            dec(self.risk["order_cap_usd"]),
        )
        self.target = self.cap * dec(self.risk["exposure_target_fraction"]) * self.scale
        self.tick, self.lot, self.minimum = (
            dec(plan["assumed_market_rules"][k]) for k in ("tick", "lot", "minimum_usd")
        )
        self.fee = dec(self.cost["fee_bps"]) / 10000
        self.half = dec(self.cost["spread_bps"]) / 20000
        self.slip = dec(self.cost["slippage_bps"]) / 10000
        self.qty = self.fees = self.turnover = self.spread = self.slippage = ZERO
        self.peak = self.equity = self.day_open = self.initial
        self.drawdown = self.low_drawdown = ZERO
        self.pending = self.working = self.opened = self.stop = self.exit_reason = None
        self.previous_signal = self.day = self.last_entry_day = None
        self.halted, self.rounds, self.missing = False, 0, 0
        self.orders, self.decisions, self.path = [], [], []

    def liquidation(self, price):
        return self.cash + self.qty * price * (1 - self.half) * (1 - self.slip) * (1 - self.fee)

    def fill(self, order, amount, at, opening):
        price = dec(order["price"])
        amount = down(amount, self.lot)
        if amount <= 0:
            order["status"] = "canceled"
            return
        cost, fee = amount * price, amount * price * self.fee
        if not self.reference and cost > self.order_cap:
            raise SafetyError("Research order cap violated")
        if order["side"] == "buy":
            self.cash -= cost + fee
            self.qty += amount
            self.opened = at
            self.stop = price * (1 - dec(self.risk["stop_bps"]) / 10000)
            if self.reference:
                self.spread += amount * opening * self.half
                self.slippage += amount * opening * (1 + self.half) * self.slip
            else:
                self.spread -= amount * opening * self.half
            self.last_entry_day = at // 86400
        else:
            self.cash += cost - fee
            self.qty -= amount
            self.spread += amount * opening * self.half
            self.slippage += amount * opening * (1 - self.half) * self.slip
            if self.qty == 0:
                self.rounds += 1
                self.opened = self.stop = self.exit_reason = None
        if self.cash < 0 or self.qty < 0:
            raise SafetyError("Research accounting invariant violated")
        self.fees += fee
        self.turnover += cost
        order.update(
            filled=str(amount),
            fee=str(fee),
            confirmed=at,
            status="filled" if amount == dec(order["requested"]) else "partial_canceled",
        )

    def fill_amount(self, order, liquidity):
        amount = min(dec(order["requested"]), liquidity * dec(self.cost["participation"]))
        if (
            draw(self.plan["seed"], self.symbol, order["submitted"], order["side"], "partial")
            < self.cost["partial_probability"]
        ):
            amount *= dec(self.cost["partial_fraction"])
        return amount

    def opening(self, bar, previous):
        if self.day != bar.start // 86400:
            self.day, self.day_open = bar.start // 86400, self.equity
        if not self.pending or bar.start < self.pending["due"]:
            return
        intent, self.pending = self.pending, None
        side, opening = intent["side"], dec(bar.open)
        price = down(
            opening * (1 - self.half if side == "buy" else (1 - self.half) * (1 - self.slip)),
            self.tick,
        )
        if self.reference and side == "buy":
            price = down(opening * (1 + self.half) * (1 + self.slip), self.tick)
        if price <= 0:
            raise SafetyError("Scenario price below tick")
        if side == "buy":
            budget = min(
                self.cash, intent["budget"], self.initial if self.reference else self.order_cap
            )
            requested = down(budget / (price * (1 + self.fee)), self.lot)
        else:
            desired = (
                max(ZERO, self.qty - self.target / opening)
                if intent["reason"] == "exposure trim"
                else self.qty
            )
            requested = down(
                min(desired, self.qty if self.reference else self.order_cap / price), self.lot
            )
        order = {
            "created": intent["created"],
            "submitted": bar.start,
            "side": side,
            "reason": intent["reason"],
            "requested": str(requested),
            "price": str(price),
            "filled": "0",
            "fee": "0",
            "status": "canceled",
        }
        self.orders.append(order)
        if bar.start != intent["due"]:
            order["reason"] = "expired before observed execution bar"
        elif self.reference:
            # Deliberately untradeable full-exposure PRICE reference. It has costs,
            # but no queue, latency, participation, rejection, caps or protective exits.
            self.fill(order, requested, bar.start, opening)
        elif requested * price < self.minimum:
            order.update(status="rejected", reason="retained dust or below synthetic minimum")
        elif (
            draw(self.plan["seed"], self.symbol, bar.start, side, "reject")
            < self.cost["reject_probability"]
        ):
            order.update(status="rejected", reason="scenario rejection")
        elif side == "buy":
            order["status"] = "working"
            self.working = order
        else:
            # IOC liquidity proxy uses an already COMPLETED bar, never future volume.
            liquidity = (
                dec(previous.volume)
                if (
                    previous
                    and previous.available <= bar.start
                    and bar.start - previous.available < 3600
                )
                else ZERO
            )
            self.fill(order, self.fill_amount(order, liquidity), bar.start, opening)

    def closing(self, bar):
        # Conditional passive scenario: all fills are confirmed at expiry/bar end.
        # This does not reconstruct actual intrabar queue, fill or cancellation timing.
        if self.working and self.working["submitted"] == bar.start:
            order, self.working = self.working, None
            amount = (
                self.fill_amount(order, dec(bar.volume))
                if dec(bar.low) < dec(order["price"])
                else ZERO
            )
            self.fill(order, amount, bar.end, dec(bar.open))
        self.equity = self.liquidation(dec(bar.close))
        low = self.liquidation(dec(bar.low))
        self.low_drawdown = max(self.low_drawdown, (self.peak - low) / self.peak * 100)
        self.peak = max(self.peak, self.equity)
        self.drawdown = max(self.drawdown, (self.peak - self.equity) / self.peak * 100)
        self.path.append(
            {
                "at": bar.end,
                "equity": float(self.equity),
                "exposure": float(self.qty * dec(bar.close)),
            }
        )

    def cancel_entry(self):
        if self.pending and self.pending["side"] == "buy":
            self.pending = None
        if self.working:
            # Bars cannot prove cancellation beat an intrabar fill. Keep potential
            # fills until the predeclared expiry; never assume an early cancel erased them.
            self.working["cancel_requested"] = True

    def observe(self, bar, view):
        # Only this event can use completed OHLC features/stop observations.
        # It follows the publication delay and cannot cancel a previous opening fill.
        signal = bool(view and view["momentum"] > 0)
        price = dec(bar.close)
        equity = self.liquidation(price)
        if not self.reference and self.day_open - equity >= dec(self.risk["daily_loss_usd"]):
            self.halted = True
            self.exit_reason = "daily loss halt"
        if self.qty and not self.reference:
            if self.stop and bar.end >= self.opened and dec(bar.low) <= self.stop:
                self.exit_reason = self.exit_reason or "stop observed after publication"
            if self.opened and bar.available - self.opened >= self.risk["max_hold_hours"] * 3600:
                self.exit_reason = self.exit_reason or "deadline"
            if self.qty * price > self.cap:
                self.exit_reason = self.exit_reason or "exposure trim"
            if not self.benchmark and view and not signal:
                self.exit_reason = self.exit_reason or "momentum exit"
        if self.exit_reason == "exposure trim" and self.qty * price <= self.target:
            self.exit_reason = None
        action = "hold"
        if self.halted:
            self.cancel_entry()
        elif not self.benchmark and (
            self.working or (self.pending and self.pending["side"] == "buy")
        ):
            if view is None or allocation(self.arm, view, self.plan, self.cost, self.model) == 0:
                self.cancel_entry()
        if self.qty and self.exit_reason:
            self.cancel_entry()
            if self.qty * price < self.minimum:
                action = "retained_dust"
            elif self.pending is None:
                self.pending = {
                    "created": bar.available,
                    "due": bar.end + self.cost["delay_bars"] * 3600,
                    "side": "sell",
                    "reason": self.exit_reason,
                }
                action = "protective_sell"
        elif (
            not self.qty
            and not self.halted
            and not self.pending
            and not self.working
            and self.arm != "cash"
        ):
            fraction = (
                self.scale
                if self.benchmark
                else dec(allocation(self.arm, view, self.plan, self.cost, self.model))
            )
            eligible = (
                self.last_entry_day != bar.available // 86400
                if self.benchmark
                else view is not None and self.previous_signal is False and signal
            )
            if self.reference:
                eligible = self.last_entry_day is None
            if fraction > 0 and eligible:
                budget = (
                    self.initial
                    if self.reference
                    else self.cap * dec(self.risk["exposure_target_fraction"]) * fraction
                )
                self.pending = {
                    "created": bar.available,
                    "due": bar.end + self.cost["delay_bars"] * 3600,
                    "side": "buy",
                    "budget": budget,
                    "reason": self.arm,
                }
                action = "passive_buy"
        if view is None:
            self.missing += 1
        self.previous_signal = signal if view else None
        self.decisions.append(
            {
                "at": bar.available,
                "information_end": view["information_end"] if view else None,
                "action": action,
                "quantity": str(self.qty),
                "halted": self.halted,
            }
        )

    def result(self):
        return {
            "arm": self.arm,
            "scenario": self.scenario,
            "symbol": self.symbol,
            "net_return_pct": float((self.equity / self.initial - 1) * 100),
            "ending_liquidation_equity": str(self.equity),
            "cash": str(self.cash),
            "open_quantity": str(self.qty),
            "fees": str(self.fees),
            "turnover_usd": str(self.turnover),
            "turnover_over_initial": float(self.turnover / self.initial),
            "spread_cost_estimate": str(self.spread),
            "slippage_cost_estimate": str(self.slippage),
            "bar_close_drawdown_pct": float(self.drawdown),
            "adverse_bar_low_drawdown_pct": float(self.low_drawdown),
            "completed_round_trips": self.rounds,
            "halted": self.halted,
            "missing_feature_bars": self.missing,
            "rejections": sum(o["status"] == "rejected" for o in self.orders),
            "partial_fills": sum(o["status"] == "partial_canceled" for o in self.orders),
            "unfilled": sum(o["filled"] == "0" for o in self.orders),
            "orders": self.orders,
            "decisions": self.decisions,
            "path": self.path,
            "limitations": "Retrospective hourly bar scenario; assumed liquidity, quotes, delays and expiry-time passive fill confirmation. Not broker execution. Buy-hold reference exempts caps/stops and is not deployable.",
        }


def simulate(
    bars, views, plan, symbol, arm, scenario_name, start, end, model=None, passive_scale=1.0
):
    if len(bars) != len(views) or any(
        a.available >= b.available for a, b in zip(bars[:-1], bars[1:], strict=True)
    ):
        raise SafetyError("Unaligned features or unsupported out-of-order availability")
    state = Scenario(plan, symbol, arm, scenario_name, model, passive_scale)
    events = []
    for i, (bar, view) in enumerate(zip(bars, views, strict=True)):
        if bar.available < start:
            state.previous_signal = bool(view and view["momentum"] > 0) if view else None
        if start <= bar.start < end:
            if state.reference and not events:
                state.pending = {
                    "created": bar.start,
                    "due": bar.start,
                    "side": "buy",
                    "budget": state.initial,
                    "reason": "unconstrained price reference",
                }
            # Close precedes next open at the same boundary; publication is later.
            events.extend([(bar.start, 1, i), (bar.end, 0, i), (bar.available, 2, i)])
    for at, kind, i in sorted(events):
        if at > end:
            continue
        bar = bars[i]
        if kind == 0:
            state.closing(bar)
        elif kind == 1:
            prior = [b for b in bars[max(0, i - 2) : i] if b.available <= at]
            state.opening(bar, prior[-1] if prior else None)
        else:
            state.observe(bar, views[i])
    return state.result()
