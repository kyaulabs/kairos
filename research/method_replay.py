"""Offline production-method replay. Synthetic execution, NOT broker/Jev reconstruction."""

import asyncio
import hashlib
import itertools
import json
from collections import Counter, deque
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from kairos import htf, programs, scalping
from kairos.domain import BPS, Book, Pair, SafetyError, dec, floor
from kairos.engine import Engine
from kairos.htf_review import HTFReview
from kairos.settings import DEFAULTS, validate_settings
from kairos.store import Store


class History:
    """Rolling OHLCV windows identical to rolling_rows; no fabricated archive VWAP."""

    def __init__(self):
        self.minutes = deque(maxlen=1800)
        self.hours = deque(maxlen=1800)
        self.group = deque()
        self.highs, self.lows = deque(), deque()
        self.volume = self.weighted = dec(0)
        self.trades = self.count = self.end = 0

    def add(self, row):
        ts = row[0]
        if self.end and ts != self.end:
            self.__init__()  # Missing input invalidates history, never holdings or protection.
        self.end = ts + 60
        self.count += 1
        self.minutes.append(row)
        high, low, vwap, volume = map(dec, (row[2], row[3], row[5], row[6]))
        self.group.append((row, volume, vwap * volume))
        self.volume += volume
        self.weighted += vwap * volume
        self.trades += row[7]
        while self.highs and self.highs[-1][1] <= high:
            self.highs.pop()
        while self.lows and self.lows[-1][1] >= low:
            self.lows.pop()
        self.highs.append((ts, high))
        self.lows.append((ts, low))
        if len(self.group) > 60:
            old, old_volume, old_weighted = self.group.popleft()
            self.volume -= old_volume
            self.weighted -= old_weighted
            self.trades -= old[7]
        for queue in (self.highs, self.lows):
            while queue and queue[0][0] <= ts - 3600:
                queue.popleft()
        self.hours.append(
            None
            if len(self.group) < 60
            else [
                ts - 3540,
                self.group[0][0][1],
                str(self.highs[0][1]),
                str(self.lows[0][1]),
                row[4],
                str(self.weighted / self.volume if self.volume else dec(0)),
                str(self.volume),
                self.trades,
            ]
        )

    def rolling(self, now):
        cutoff = int(now) // 60 * 60
        if self.end != cutoff or self.end + 1 > now or self.count < 1800:
            return None
        return [self.hours[-1 - i * 60] for i in reversed(range(30))]


class ScenarioBook(Book):
    def fill(self, side, volume, limit):
        quote = self.asks[0][0] if side == "buy" else self.bids[0][0]
        if (side == "buy" and limit < quote) or (side == "sell" and limit > quote):
            return dec(0), dec(0)
        filled = self.market.consume(volume)
        return filled, filled * limit  # Adverse limit, never claimed historical execution.


class Market:
    allow_live = False
    hosted_paper = True  # Select passive-limit wording/cost reserve, not an Alpaca session.

    def __init__(self, pair, scenario, start):
        self.pair, self.pairs = pair, {pair.id: pair}
        self.scenario, self.now = scenario, start + 1
        self.mid, self.remaining = dec(1), dec(0)
        self.history = History()
        self.market_data = self

    def consume(self, quantity):
        amount = floor(min(quantity, self.remaining), self.pair.lot)
        self.remaining -= amount
        return amount

    async def catalog(self):
        return self.pairs

    async def fees(self, pairs):
        rates = {p.id: dec(25) for p in pairs}
        return rates, rates

    async def book(self, pair):
        half = dec(self.scenario["spread_bps"]) / (2 * BPS)
        book = ScenarioBook(
            pair,
            [(pair.price(self.mid * (1 - half), "buy"), self.remaining)],
            [(pair.price(self.mid * (1 + half), "sell"), self.remaining)],
            self.now,
        )
        book.market = self
        return book

    async def marks(self, pairs):
        return {p.id: (await self.book(p)).bids[0][0] for p in pairs}

    async def candles(self, pair, minutes):
        if minutes != 1:
            raise SafetyError("Scalp replay requires native minutes")
        return list(self.history.minutes)[-31:]

    def snapshot(self):
        return {"status": "offline synthetic quotes; Kraken candles, assumed Alpaca fees"}


class PermissionAblation(HTFReview):
    """Reuse production entry_view; replace unavailable Jev with explicit permission only."""

    async def refresh(self, pair, data):
        self.rows = self.engine.kraken.history.rolling(self.engine.clock())
        self.engine.policy_counts["rolling_ready" if self.rows else "rolling_incomplete"] += 1
        self.status = (
            "Offline rolling input ready" if self.rows else "Offline rolling input incomplete"
        )
        self.available = min(1800, self.engine.kraken.history.count)
        self.required = 1800
        return self.rows

    def entry_view(self, rows, pair, book):
        self.view = super().entry_view(rows, pair, book)
        for key in ("pullback_long", "entry_eligible"):
            self.engine.policy_counts[key] += int(self.view[key])
        self.engine.policy_counts["range_observation_eligible"] += int(
            self.view["range_observation"]["eligible"]
        )
        return self.view

    def take(self):
        now = self.engine.clock()
        if now < self.next_at:
            return None
        self.next_at = now + 2 * self.engine.settings["interval_seconds"]
        return {
            "action": htf.entry_side(self.view, "spot"),
            "confidence": 1,
            "probabilities": {},
            "deterministic": True,
            "model": "Offline eligible-entry permission ablation; NOT Jev",
            "allowed_actions": ["hold", "buy", "sell"],
            "observed_at": now,
            "expires_at": now + 2 * self.engine.settings["interval_seconds"],
            "latency_ms": 0,
        }


class ReplayEngine(Engine):
    """Only UI/log compression and hypothetical passive settlement differ from Engine."""

    def __init__(self, *args, **kwargs):
        self.reasons = Counter()
        self.policy_counts = Counter()
        self.examples = {}
        self.trace = hashlib.sha256()
        super().__init__(*args, **kwargs)

    def emit_state(self):
        pass  # No browser subscribers; does not affect execution state.

    def event(self, kind, data):
        if kind == "decision":
            reason = data.get("reason", "unknown")
            self.reasons[reason] += 1
            self.examples.setdefault(reason, data)
            self.trace.update(
                json.dumps(
                    [self.clock(), data.get("action"), reason, data.get("state", {}).get("mid")],
                    sort_keys=True,
                ).encode()
            )
            return None
        return super().event(kind, data)

    async def paper_makers(self):
        market = self.kraken
        for order in self.orders("dry-run", True):
            if not order["maker"]:
                raise SafetyError("Unexpected pending taker")
            if (
                market.scenario["passive"] == "strict_path_cross"
                and order["created"] < market.now <= order["expires"]
                and (
                    (order["side"] == "buy" and market.mid < dec(order["price"]))
                    or (order["side"] == "sell" and market.mid > dec(order["price"]))
                )
            ):
                size = market.consume(dec(order["volume"]) - dec(order["filled"]))
                filled = dec(order["filled"]) + size
                cost = dec(order["cost"]) + size * dec(order["price"])
                self.apply(
                    order,
                    filled,
                    cost,
                    cost * dec(order["fee_bps"]) / BPS,
                    "closed" if filled == dec(order["volume"]) else "open",
                )
        # Engine.tick next cancels remaining open orders. No fabricated trade prints.


def settings_for(method, pair, opening, scenario):
    if method not in {"htf", "htf_no_fill", "scalp", "dca", "twap", "rebalance", "passive80"}:
        raise SafetyError("Method unsupported by the offline archive study")
    strategy = "dca" if method == "passive80" else "htf" if method == "htf_no_fill" else method
    limit = pair.price(
        dec(opening)
        * (1 + dec(scenario["spread_bps"]) / 20000)
        * (1 + dec(scenario["slippage_bps"]) / 10000),
        "buy",
    )
    quantity = floor(dec(80) / (limit * dec("1.0025")), pair.lot)
    return validate_settings(
        {
            **DEFAULTS,
            "strategy": strategy,
            "product": "spot",
            "pair": pair.id,
            "quote": pair.quote,
            "paper_balance": "500",
            "order_size": "100",
            "max_exposure": "100",
            "daily_loss": "12.50",
            "recover_initial": False,
            "reinvest_profits": True,
            "slippage_bps": str(scenario["slippage_bps"]),
            "interval_seconds": 15,
            "dca_count": 1 if method == "passive80" else 7,
            "dca_amount": "80" if method == "passive80" else "10",
            "dca_period_seconds": 86400,
            "twap_quantity": str(quantity),
            "twap_limit": str(limit),
            "twap_slices": 12,
            "twap_duration_seconds": 3600,
            "rebalance_targets": f"{pair.id}=16,CASH=84",
            "rebalance_band_pct": "5",
        }
    )


async def replay_async(rows, pair, method, scenario, start, end):
    if not rows or end > 1767225600 or start >= end:
        raise SafetyError("Invalid offline development window")
    if any(a[0] >= b[0] for a, b in zip(rows[:-1], rows[1:], strict=True)):
        raise SafetyError("Duplicate or unordered input")
    current = next((r for r in rows if start <= r[0] < end), None)
    if current is None:
        raise SafetyError("No evaluation observations")
    market = Market(pair, scenario, start)
    market.mid = dec(current[1])
    market.now = current[0] + 1
    settings = settings_for(method, pair, current[1], scenario)
    store = Store(":memory:", clock=lambda: market.now)
    store.put("settings", settings)
    engine = ReplayEngine(
        store,
        market,
        SimpleNamespace(key="offline-no-model"),
        lambda *_: None,
        clock=lambda: market.now,
    )
    engine.htf_review = PermissionAblation(engine)
    engine.htf_review.view = None
    ids = itertools.count(1)
    marks, processed, halt_at, high, drawdown = {}, 0, None, dec(500), dec(0)
    cached_balances = None
    try:
        with (
            patch("time.time", lambda: market.now),
            patch("uuid.uuid4", lambda: UUID(int=next(ids))),
        ):
            await engine.initialize()
            await engine.start()
            for row in rows:
                if row[0] < start:
                    market.history.add(row)
                    continue
                if row[0] >= end:
                    break
                market.remaining = (
                    dec(market.history.minutes[-1][6]) * dec(scenario["participation"])
                    if market.history.minutes and market.history.end == row[0]
                    else dec(0)
                )
                points = (
                    (row[1], row[3], row[2], row[4])
                    if scenario["path"] == "OLHC"
                    else (row[1], row[2], row[3], row[4])
                )
                for offset, price in zip((1, 20, 40, 59), points, strict=True):
                    market.now, market.mid = row[0] + offset, dec(price)
                    if engine.running:
                        await engine.tick()
                        if not engine.running and halt_at is None:
                            halt_at = market.now
                    # Mark retained holdings after completion/halt; never resume or liquidate.
                    bid = (await market.book(pair)).bids[0][0]
                    if cached_balances is None:
                        cash, held = engine.balance(pair.quote), engine.balance(pair.base)
                        if not engine.running:
                            cached_balances = (cash, held)
                    else:
                        cash, held = cached_balances
                    net = cash + held * bid * (1 - dec(settings["slippage_bps"]) / BPS) * dec(
                        ".9975"
                    )
                    high = max(high, net)
                    drawdown = max(drawdown, (high - net) / high * 100)
                processed += 1
                marks[str(row[0] // 86400 * 86400)] = {"at": market.now, "equity": str(net)}
                market.history.add(row)
            await engine.cancel_active()
            orders = engine.orders()
            return {
                "method": method,
                "settings": settings,
                "scenario": scenario,
                "start": start,
                "end": end,
                "observed_minutes": processed,
                "expected_minutes": (end - start) // 60,
                "missing_minutes": (end - start) // 60 - processed,
                "net_liquidation_change_pct": str((net / 500 - 1) * 100),
                "end_cash": str(engine.balance(pair.quote)),
                "retained_quantity": str(engine.balance(pair.base)),
                "net_liquidation_equity": str(net),
                "path_drawdown_pct": str(drawdown),
                "simulated_fees": str(dec(engine.ledger()["fees"].get(pair.quote, 0))),
                "halt_or_completion_at": halt_at,
                "halt_reason": engine.last_error,
                "running_at_boundary": engine.running,
                "program": programs.snapshot(engine),
                "htf": htf.snapshot(engine) if settings["strategy"] == "htf" else None,
                "scalp": scalping.snapshot(engine) if settings["strategy"] == "scalp" else None,
                "orders": orders,
                "filled_orders": sum(dec(o["filled"]) > 0 for o in orders),
                "submitted_orders": len(orders),
                "decision_reasons": dict(engine.reasons),
                "policy_evaluation_counts": dict(engine.policy_counts),
                "decision_examples": engine.examples,
                "decision_trace_sha256": engine.trace.hexdigest(),
                "daily_marks": marks,
                "pair_rules": pair.public(),
                "broker_verified": False,
                "jev_reconstructed": False,
                "source": "Kraken minute archive",
                "network_calls": 0,
            }
    finally:
        await engine.htf_review.close()
        store.close()


def replay(rows, rule, method, scenario, start, end):
    pair = Pair(
        rule["id"],
        rule["symbol"],
        rule["base"],
        "ZUSD",
        dec(rule["tick"]),
        dec(rule["lot"]),
        dec(rule["minimum"]),
        dec(rule["cost_minimum"]),
    )
    return asyncio.run(replay_async(rows, pair, method, scenario, start, end))
