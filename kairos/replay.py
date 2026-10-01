"""Offline HTF rule benchmark, not a rolling/Jev simulation. Never loads credentials."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from kairos import htf
from kairos.domain import BPS, CANDLE_INTERVALS, ZERO, Book, Pair, SafetyError, dec
from kairos.engine import EXECUTION_REVISION, Engine
from kairos.market_data import CandleHistory
from kairos.settings import DEFAULTS, validate_settings
from kairos.store import Store
from kairos.strategies import trend_state


class RuleReview:
    """Explicit offline rule benchmark, NOT a reconstruction of Jev decisions.

    Also used by order/risk unit tests to isolate execution from inference IO.
    Production always uses HTFReview, never this adapter.
    """

    native = False
    policy = "offline-fixed-candle-rules"

    def __init__(self, engine):
        self.engine = engine
        self.view = None
        self.last = None

    def entry_view(self, rows, pair, book):
        # Retained legacy taker benchmark: explicitly not the passive pullback policy.
        return trend_state(
            rows, self.engine.settings, self.engine.fees.reserve(pair), book.spread_bps
        )

    def cancel(self):
        self.last = None

    async def close(self):
        self.cancel()

    def snapshot(self):
        return {"status": "Offline rule benchmark; not Jev", "interval_seconds": None}

    async def refresh(self, pair, data):
        engine = self.engine
        client = engine.futures.client if engine.settings["product"] == "futures" else engine.kraken
        rows = await (
            client.completed_candles(pair, engine.settings["candle_minutes"])
            if engine.settings["product"] == "futures"
            else client.candles(pair, engine.settings["candle_minutes"])
        )
        self.view = trend_state(
            rows, engine.settings, engine.fees.reserve(pair), data["spread_bps"]
        )
        return rows

    def take(self):
        if self.last == self.view["candle_close_time"]:
            return None
        self.last = self.view["candle_close_time"]
        return {
            "action": htf.entry_side(self.view, self.engine.settings["product"]),
            "confidence": 1,
            "probabilities": {},
            "model": "Offline rule benchmark",
            "latency_ms": 0,
            "deterministic": True,
        }


class ReplayBook(Book):
    """Synthetic unlimited depth; every fill pays the adverse limit price."""

    def fresh(self, seconds):
        # Zero spread is a valid offline assumption, unlike a real crossed book.
        if self.bids[0][0] == self.asks[0][0] and time.time() - self.received <= seconds:
            return
        super().fresh(seconds)

    def fill(self, side, volume, limit):
        return volume, volume * limit


class ReplayMarket:
    """Read-only generated market. No exchange session, credentials or write methods."""

    allow_live = False

    def __init__(self, rows, pair, fee_bps, spread_bps):
        self.rows, self.pair = rows, pair
        self.pairs = {pair.id: pair}
        self.fee_bps, self.half_spread = dec(fee_bps), dec(spread_bps) / (2 * BPS)
        self.index = 30
        self.now, self.mid = rows[30][0], dec(rows[30][1])
        self.market_data = self

    async def catalog(self):
        return self.pairs

    async def fees(self, pairs):
        rates = {p.id: self.fee_bps for p in pairs}
        return rates, rates

    async def book(self, pair):
        bid = pair.price(self.mid * (1 - self.half_spread), "buy")
        ask = pair.price(self.mid * (1 + self.half_spread), "sell")
        if bid <= 0:
            raise SafetyError("Replay price below market tick precision")
        return ReplayBook(pair, [(bid, dec("1e30"))], [(ask, dec("1e30"))], time.time())

    async def marks(self, pairs):
        return {p.id: (await self.book(p)).bids[0][0] for p in pairs}

    async def candles(self, pair, minutes):
        return self.rows[self.index - 30 : self.index]

    def snapshot(self):
        return {"status": "offline", "last_candle_source": "Historical completed bars"}


async def replay_async(rows, settings, *, fee_bps, spread_bps, initial=1000, budget=100, pair=None):
    """Run the shared paper executor with rule decisions, not historical Jev predictions.

    The path is assumed, not reconstructed. Four observations per bar cannot model
    ten-second protection checks, gaps between ticks, queueing, outages or actual
    liquidity. All fills pay the adverse limit, with unlimited synthetic depth.
    Supply a Pair to apply venue precision/minimums; defaults are synthetic.
    """
    initial, budget, fee_bps, spread_bps = map(dec, (initial, budget, fee_bps, spread_bps))
    if not 0 <= fee_bps <= 1000 or not 0 <= spread_bps < 2 * BPS or min(initial, budget) <= 0:
        raise SafetyError("Invalid replay costs or allocation")
    if (
        settings["product"] != "spot"
        or settings["strategy"] != "htf"
        or settings["recover_initial"]
    ):
        raise SafetyError("Replay supports spot HTF without capital recovery only")
    minutes = settings["candle_minutes"]
    if minutes not in CANDLE_INTERVALS:
        raise SafetyError("Unsupported candle interval")
    history = CandleHistory(None, minutes, 30)
    rows = [history.validate(row) for row in rows]
    if len(rows) < 31 or any(
        b[0] - a[0] != minutes * 60 for a, b in zip(rows[:-1], rows[1:], strict=True)
    ):
        raise SafetyError("Replay needs at least 31 consecutive completed candles")
    pair = pair or Pair(
        "REPLAYUSD", "REPLAY/USD", "REPLAY", "ZUSD", dec("1e-12"), dec("1e-12"), dec("1e-12"), ZERO
    )
    configured = validate_settings(
        {
            **settings,
            "pair": pair.id,
            "quote": pair.quote,
            "paper_balance": str(initial),
            "order_size": str(budget),
        }
    )
    market = ReplayMarket(rows, pair, fee_bps, spread_bps)

    def clock():
        return market.now

    store = Store(":memory:", clock=clock)
    store.put("settings", configured)
    engine = Engine(
        store, market, SimpleNamespace(key="offline-rule-benchmark"), lambda *_: None, clock=clock
    )
    engine.htf_review = RuleReview(engine)
    peak, drawdown, equity = initial, ZERO, initial
    processed = 0
    try:
        await engine.initialize()
        await engine.start()
        for i in range(30, len(rows)):
            market.index = i
            ts, opening, high, low, close, *_ = rows[i]
            # Adverse-first path: if both stop and trim levels occur in a bar,
            # losses are observed first. Only PREVIOUS completed bars reach HTF.
            for offset, mid in zip(
                (0, minutes * 20, minutes * 40, minutes * 60 - 1),
                (opening, low, high, close),
                strict=True,
            ):
                market.now, market.mid = ts + offset, dec(mid)
                await engine.tick()
                if not engine.running:
                    break
            held = engine.balance(pair.base)
            bid = (await market.book(pair)).bids[0][0]
            equity = engine.balance(pair.quote) + held * bid * (
                1 - dec(settings["slippage_bps"]) / BPS
            ) * (1 - fee_bps / BPS)
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak * 100)
            processed += 1
            if not engine.running:
                break  # Same fail-closed halt as paper; never auto-rearm tomorrow.
        orders = engine.orders()
        positions = {}
        for order in orders:
            if dec(order["filled"]):
                positions.setdefault(order["htf_id"], []).append(order)
        trades = []
        for fills in positions.values():
            if sum((dec(o["filled"]) * (1 if o["side"] == "buy" else -1) for o in fills), ZERO):
                continue
            trades.append(
                {
                    "opened_at": fills[0]["created"],
                    "closed_bar": int(fills[-1]["created"] // (minutes * 60)) * minutes * 60,
                    "reason": fills[-1].get("reason"),
                    "net_pnl": str(
                        sum(
                            (
                                dec(o["cost"]) * (1 if o["side"] == "sell" else -1) - dec(o["fee"])
                                for o in fills
                            ),
                            ZERO,
                        )
                    ),
                }
            )
        return {
            "bars": len(rows),
            "processed_bars": processed,
            "execution_revision": EXECUTION_REVISION,
            "policy": "Legacy taker rule benchmark; does not reproduce passive pullback entries, rolling-minute inputs or Jev decisions",
            "completed_trades": len(trades),
            "wins": sum(dec(t["net_pnl"]) > 0 for t in trades),
            "net_return_pct": str((equity / initial - 1) * 100),
            "bar_close_max_drawdown_pct": str(drawdown),
            "paid_fees": str(dec(engine.ledger()["fees"].get(pair.quote, 0))),
            "open_quantity": str(engine.balance(pair.base)),
            "ending_liquidation_equity": str(equity),
            "halted": not engine.running,
            "halt_reason": engine.last_error,
            "trades": trades,
            "orders": orders,
            "pair_rules": pair.public(),
        }
    finally:
        store.close()


def replay(rows, settings, **kwargs):
    return asyncio.run(replay_async(rows, settings, **kwargs))


def evaluate(rows, settings, *, fee_bps, spread_bps, initial=1000, budget=100, pair=None):
    split = len(rows) * 7 // 10
    if split < 31 or len(rows) - split < 31:
        raise SafetyError(
            "Need enough data for independent 70/30 chronological segments (31 bars each)"
        )
    options = dict(
        fee_bps=fee_bps, spread_bps=spread_bps, initial=initial, budget=budget, pair=pair
    )
    return {
        "assumptions": "Offline RULE BENCHMARK, not the deployed rolling/Jev strategy. Historical hourly bars cannot reconstruct minute-shifted windows or model decisions. Uses the shared spot HTF paper executor and risk checks; independent flat-start 70/30 segments with 30 warm-up bars. Fresh entry transitions, spread-aware costs, 80% target, bounded trims, reinvestment and UTC daily-loss limits are shared with execution. Assumed open-low-high-close path with four checks per bar, not real engine cadence. Full fills at adverse limits; no real depth, downtime, borrowing or funding. Default precision/minimums are synthetic unless pair rules are supplied. No automatic resume after a safety halt. Drawdown uses observed bar ends (or the halt sample); open holdings valued at estimated net liquidation. No profitability claim.",
        "settings": {**settings, "paper_balance": str(initial), "order_size": str(budget)},
        "taker_fee_bps": str(fee_bps),
        "spread_bps": str(spread_bps),
        "train": replay(rows[:split], settings, **options),
        "holdout": replay(rows[split:], settings, **options),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "candles", type=Path, help="JSON completed Kraken OHLC rows; omit the forming row"
    )
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--taker-fee-bps", required=True, type=dec)
    parser.add_argument("--spread-bps", required=True, type=dec)
    parser.add_argument("--initial", type=dec, default=dec(1000))
    parser.add_argument("--order-cap", type=dec, default=dec(100))
    parser.add_argument("--exposure-cap", type=dec, default=dec(DEFAULTS["max_exposure"]))
    parser.add_argument("--daily-loss", type=dec, default=dec(DEFAULTS["daily_loss"]))
    parser.add_argument(
        "--reinvest", action=argparse.BooleanOptionalAction, default=DEFAULTS["reinvest_profits"]
    )
    args = parser.parse_args()
    settings = {
        **DEFAULTS,
        "candle_minutes": args.minutes,
        "max_exposure": str(args.exposure_cap),
        "daily_loss": str(args.daily_loss),
        "reinvest_profits": args.reinvest,
    }
    try:
        result = evaluate(
            json.loads(args.candles.read_text()),
            settings,
            fee_bps=args.taker_fee_bps,
            spread_bps=args.spread_bps,
            initial=args.initial,
            budget=args.order_cap,
        )
    except (SafetyError, ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
