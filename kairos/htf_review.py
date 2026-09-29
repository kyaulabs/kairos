"""Rolling, confirmed-minute HTF history and non-blocking Jev reviews.

Model IO never holds the execution lock. Only the HTF executor may place orders.
"""

import asyncio
import contextlib
import json

from kairos import diagnostics
from kairos.domain import SafetyError, dec
from kairos.htf_policy import signal
from kairos.market_data import CandleHistory


class ExpiredReview(SafetyError):
    """A discretionary intent expired before any order was recorded or sent."""


def check_intent(engine, review=None):
    expiry = (review or {}).get("expires_at")
    if expiry is not None and (
        engine.clock() + 1 >= expiry or engine.clock() < review["observed_at"]
    ):
        raise ExpiredReview("Jev review expired before submission; no order sent")


def intent_deadline(default, review=None):
    expiry = (review or {}).get("expires_at")
    return min(default, expiry) if expiry is not None else default


def rolling_rows(store, pair, rows, cutoff, minutes):
    """Archive confirmed minutes and aggregate 30 adjacent windows ending at cutoff.

    No interpolation, forward filling, forming bars or clock-only candle promotion.
    An incomplete contiguous suffix is warm-up, never an executable signal.
    """
    history = CandleHistory(pair, 1, 1)
    # Futures OHLC has no VWAP; zero means unavailable, never a synthetic price.
    rows = [history.validate(row, vwap_optional=pair.id.startswith("futures:")) for row in rows]
    times = [r[0] for r in rows]
    if times != sorted(set(times)) or any(t >= cutoff for t in times):
        raise SafetyError("Invalid completed minute history")
    needed = minutes * 30
    with store.db:
        store.db.executemany(
            "INSERT OR REPLACE INTO htf_minutes VALUES (?, ?, ?)",
            [(pair.id, r[0], json.dumps(r)) for r in rows],
        )
        store.db.execute(
            "DELETE FROM htf_minutes WHERE pair=? AND ts<?",
            (pair.id, cutoff - max(needed, 1800) * 60),
        )
    saved = {
        t: json.loads(r)
        for t, r in store.db.execute(
            "SELECT ts,data FROM htf_minutes WHERE pair=? AND ts>=? AND ts<? ORDER BY ts",
            (pair.id, cutoff - needed * 60, cutoff),
        )
    }
    count = 0
    while count < needed and cutoff - (count + 1) * 60 in saved:
        count += 1
    if count != needed:
        return None, count, needed
    result = []
    for start in range(cutoff - needed * 60, cutoff, minutes * 60):
        group = [saved[t] for t in range(start, start + minutes * 60, 60)]
        volume = sum(dec(r[6]) for r in group)
        vwap = sum(dec(r[5]) * dec(r[6]) for r in group) / volume if volume else dec(0)
        result.append(
            [
                start,
                group[0][1],
                str(max(dec(r[2]) for r in group)),
                str(min(dec(r[3]) for r in group)),
                group[-1][4],
                str(vwap),
                str(volume),
                sum(r[7] for r in group),
            ]
        )
    return result, count, needed


class HTFReview:
    def __init__(self, engine):
        self.engine = engine
        self.task = None
        self.rows = None
        self.answer = None
        self.context = None
        self.next_at = 0
        self.cutoff = None
        self.status = "Rolling history not loaded"
        self.available = 0
        self.required = 30 * engine.settings["candle_minutes"]
        self.observed_window = None
        self.permissions = None
        self.range_observation = None

    def entry_view(self, rows, pair, book):
        return signal(
            rows,
            self.engine.settings,
            book,
            self.engine.fees.reserve(pair, True),
            self.engine.fees.reserve(pair),
        )

    def signature(self):
        from kairos import htf

        engine = self.engine
        pair = engine.resolve(engine.settings["pair"])
        return (
            engine.stop_generation,
            engine.mode,
            engine.settings_id(),
            str(htf.quantity(engine, pair)),
            json.dumps(
                {k: htf.snapshot(engine).get(k) for k in ("position", "entry_attempt")},
                sort_keys=True,
            ),
        )

    def cancel(self):
        if self.task:
            self.task.cancel()
        self.answer = None
        self.context = None
        self.rows = None
        self.cutoff = None
        self.next_at = 0
        self.status = "Rolling review paused"
        self.permissions = None
        self.range_observation = None

    async def close(self):
        task = self.task
        self.cancel()
        if task:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def snapshot(self):
        return {
            "status": self.status,
            "interval_seconds": 2 * self.engine.settings["interval_seconds"],
            "minute_rows": self.available,
            "required_minute_rows": self.required,
            "window_end": self.cutoff,
            "in_flight": bool(self.task and not self.task.done()),
            "permissions": self.permissions,
            "range_observation": self.range_observation,
        }

    async def refresh(self, pair, data):
        engine = self.engine
        signature = self.signature()
        if self.context is not None and self.context != signature:
            next_at = self.next_at
            self.cancel()
            self.next_at = next_at
        if (
            (not self.task or self.task.done())
            and engine.clock() >= self.next_at
            and engine.running
            and not engine.shutting_down
        ):
            self.context = signature
            self.next_at = engine.clock() + 2 * engine.settings["interval_seconds"]
            self.task = asyncio.create_task(self.review(pair, signature))
        return self.rows if self.cutoff == int(engine.clock()) // 60 * 60 else None

    async def review(self, pair, signature):
        engine = self.engine
        try:
            client = (
                engine.futures.client if engine.settings["product"] == "futures" else engine.kraken
            )
            started = engine.clock()
            cutoff = int(started) // 60 * 60
            if self.cutoff != cutoff:
                if engine.settings["product"] == "futures":
                    rows = await client.completed_candles(pair, 1)
                    rows = [r for r in rows if r[0] < cutoff]
                else:
                    raw = await client.ohlc(pair, 1)
                    rows = list(CandleHistory(pair, 1, 1).rest_rows(raw, started).values())
                if self.context != signature or self.signature() != signature or not engine.running:
                    return
                self.rows, self.available, self.required = rolling_rows(
                    engine.store, pair, rows, cutoff, engine.settings["candle_minutes"]
                )
                # A boundary response may still end with the previous forming minute.
                # Retry that unconfirmed latest bucket next review, not next minute.
                self.cutoff = cutoff if self.available else None
            if not self.rows:
                self.status = f"Rolling warm-up: {self.available}/{self.required} consecutive confirmed minutes; protection active"
                return
            book = await client.book(pair)
            book.fresh(engine.settings["stale_seconds"])
            observed = engine.clock()
            view = self.entry_view(self.rows, pair, book)
            if int(observed) // 60 * 60 != cutoff or self.signature() != signature:
                self.status = "Rolling window changed during data read; waiting for a fresh review"
                return
            from kairos import htf

            window = self.rows[-1]
            permissions = htf.entry_context(engine, pair, view, book)
            self.permissions = permissions
            self.range_observation = view["range_observation"]
            if self.observed_window != cutoff:
                engine.event(
                    "htf-observation",
                    {
                        "pair": pair.id,
                        "product": engine.settings["product"],
                        "window_end": cutoff,
                        "mid": str(book.mid),
                        "entry_policy": view["entry_policy"],
                        "pullback_signal": permissions["entry_signal"],
                        "pullback_targets": view["entry_targets"],
                        "pullback_net_room_bps": view["target_net_room_bps"],
                        "maker_fee_bps": view["maker_fee_bps"],
                        "taker_fee_bps": view["taker_fee_bps"],
                        "spread_bps": str(book.spread_bps),
                        "range": self.range_observation,
                        "note": "Observations only, not orders, fills or strategy profits",
                        "message": (
                            f"{pair.symbol}: pullback {permissions['entry_signal']}; range observation "
                            + (
                                self.range_observation["signal"]
                                if self.range_observation["eligible"]
                                else "hold"
                            )
                            + "; observations only, no fills or P&L"
                        ),
                    },
                )
                self.observed_window = cutoff
            payload = {
                "rolling_window": dict(
                    zip(
                        ("start", "open", "high", "low", "close", "vwap", "volume", "trades"),
                        window,
                        strict=True,
                    )
                ),
                "window_end": cutoff,
                **permissions,
                "strategy": "htf",
                "product": engine.settings["product"],
                "mode": engine.mode,
                "symbol": pair.symbol,
                "inventory": str(htf.quantity(engine, pair)),
                "position": htf.snapshot(engine)["position"],
                "risk": engine.risk_snapshot(),
                "mid": str(book.mid),
                "bid": str(book.bids[0][0]),
                "ask": str(book.asks[0][0]),
                "spread_bps": str(book.spread_bps),
                "observed_at": observed,
                "review_interval_seconds": 2 * engine.settings["interval_seconds"],
                "rules": "Choose only from allowed_actions. entry_ready and pending_signal describe executable setup freshness; a rising trend alone does not authorize buying. Entries are passive-only pullback recoveries with cost-qualified target room, not momentum chasing. Targets are historical structure, not forecasts. No pyramiding or taker fallback. range_observation is research only and cannot authorize any order. You may hold or reduce owned inventory early. Hard protective exits cannot be vetoed. All actions still require final execution risk checks.",
                **view,
            }
            # Detach mutable protection/risk state before yielding to the model.
            payload = json.loads(json.dumps(payload))
            if permissions["allowed_actions"] == ["hold"]:
                self.status = (
                    "HTF waiting: " + "; ".join(permissions["entry_blockers"]) + "; Jev skipped"
                )
                answer = {
                    "action": "hold",
                    "confidence": None,
                    "probabilities": {},
                    "model": "HTF entry gates",
                    "deterministic": True,
                    "latency_ms": 0,
                }
            else:
                self.status = "Jev reviewing permitted HTF actions; protection active"
                answer = await engine.jev.decide(payload)
            if (
                self.context == signature
                and self.signature() == signature
                and engine.running
                and not engine.shutting_down
            ):
                self.answer = (
                    signature,
                    cutoff,
                    observed,
                    {
                        **answer,
                        "rolling_window": payload["rolling_window"],
                        "input_spread_bps": str(book.spread_bps),
                        "allowed_actions": permissions["allowed_actions"],
                    },
                )
                if not answer.get("deterministic"):
                    self.status = "Jev review ready; execution will recheck market and risk gates"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_id = diagnostics.capture(exc, "htf-review")
            self.answer = None
            self.status = (
                "Rolling data/Jev unavailable; discretionary orders blocked; protection active"
            )
            if engine.running and self.context == signature:
                engine.event(
                    "review-error",
                    {
                        "message": self.status,
                        "exception_type": type(exc).__name__,
                        "error_id": error_id,
                        "reason": str(exc)
                        if isinstance(exc, SafetyError)
                        else "Unexpected review failure",
                    },
                )

    def take(self):
        result, self.answer = self.answer, None
        if result is None:
            return None
        signature, cutoff, observed, answer = result
        now = self.engine.clock()
        if (
            signature != self.signature()
            or cutoff != int(now) // 60 * 60
            or not 0 <= now - observed <= 2 * self.engine.settings["interval_seconds"]
        ):
            self.status = "Stale Jev review discarded; protection active"
            return None
        if not answer.get("deterministic"):
            self.status = "Jev review received; waiting for the next scheduled assessment"
        return {
            **answer,
            "observed_at": observed,
            "window_end": cutoff,
            "expires_at": min(observed + 2 * self.engine.settings["interval_seconds"], cutoff + 60),
        }
