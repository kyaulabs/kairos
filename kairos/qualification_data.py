"""Bounded qualification preflight waits; never replay a durable order intent."""

import asyncio
import time

from kairos.alpaca_transport import PendingAlpacaData
from kairos.domain import SafetyError

WAIT_SECONDS = 300
RETRY_SECONDS = 5


def allowed(e):
    scope = e.qualification_data_scope
    return scope is None or (
        scope["generation"] == e.stop_generation and time.monotonic() < scope["deadline"]
    )


def guard(e, scope):
    if (
        e.shutting_down
        or e.stop_generation != scope["generation"]
        or (scope["armed"] and (not e.running or not e.paper_armed))
    ):
        raise SafetyError("Qualification fresh-data wait canceled by Stop")
    if time.monotonic() >= scope["deadline"]:
        reason = scope.get("last_error", "no fresh preflight completed")
        raise PendingAlpacaData(
            f"Qualification fresh-data deadline exhausted; no repeat authorized: {reason}"
        )


async def run(e, operation, *, generation, deadline, leg, armed):
    if e.qualification_data_scope is not None:
        raise SafetyError("Qualification data wait already active")
    scope = {"generation": generation, "deadline": deadline, "armed": armed, "leg": leg}
    orders = e.orders()
    e.qualification_data_scope = scope
    waiting, completed = False, False
    try:
        while True:
            guard(e, scope)
            try:
                if waiting:
                    # Do not repeat full account/risk reads while the source is
                    # still old. The client retains its five-second REST throttle.
                    book = await e.kraken.book(e.resolve(e.settings["pair"]))
                    book.fresh(e.settings["stale_seconds"])
                    guard(e, scope)
                result = await operation()
                completed = True
                return result
            except PendingAlpacaData as exc:
                # Engine.place persists intent before any network write. Even a
                # definitive pre-POST rejection is consumed, never retried here.
                if e.orders() != orders:
                    raise
                scope["last_error"] = str(exc)
                guard(e, scope)
                if not waiting:
                    waiting = True
                    e.event(
                        "qualification-data",
                        {
                            "message": "Waiting for fresh Alpaca data; no order intent retried",
                            "leg": leg,
                            "reason": str(exc),
                            "remaining_seconds": max(0, deadline - time.monotonic()),
                        },
                    )
                    e.emit_state()
                until = min(deadline, time.monotonic() + RETRY_SECONDS)
                while time.monotonic() < until:
                    guard(e, scope)
                    await asyncio.sleep(min(0.25, until - time.monotonic()))
    finally:
        e.qualification_data_scope = None
        if waiting:
            e.event(
                "qualification-data",
                {
                    "message": "Qualification data wait completed"
                    if completed
                    else "Qualification data wait ended without recovery",
                    "leg": leg,
                },
            )
            e.emit_state()
