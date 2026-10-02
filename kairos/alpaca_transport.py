"""Bounded Alpaca request admission and response diagnostics; never retry writes."""

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from email.utils import parsedate_to_datetime

from kairos.clients import ExchangeRejected
from kairos.domain import SafetyError


class PendingAlpacaData(SafetyError):
    """A read-only market-data interruption, never an uncertain account/order write."""


ACCOUNT_READ_PATHS = frozenset(
    {"/v2/account", "/v2/positions", "/v2/orders", "/v2/account/activities", "/v2/clock"}
)


class PendingAlpacaAccount(SafetyError):
    """A transient failure of an allowlisted GET, never an order-write outcome."""

    def __init__(self, endpoint, reason, http_status=None):
        self.details = {
            "endpoint": endpoint,
            "method": "GET",
            "reason": reason,
            "http_status": http_status,
        }
        self.diagnostic_details = self.details
        super().__init__(
            f"Alpaca account read GET {endpoint} unavailable ({reason}); no write attempted by this request"
        )


class RejectedAlpacaData(ExchangeRejected, PendingAlpacaData):
    """Freshness failed before POST: definitively rejected, not an uncertain write."""


class AlpacaRequests:
    # Leave headroom below the documented 200/minute limit. Reserve 20 admissions
    # for orders/cancellations/lookups; dashboards can use at most 30/minute.
    def __init__(self):
        self.condition = asyncio.Condition()
        self.waiters, self.history = {}, deque()
        self.sequence, self.busy, self.last_request = 0, False, 0
        self.buckets = {name: {"blocked_until": 0} for name in ("data", "trading")}
        self.last = None

    def delay(self, priority, bucket, now):
        while self.history and now - self.history[0][0] >= 60:
            self.history.popleft()
        waits = [0, self.last_request + 0.35 - now, self.buckets[bucket]["blocked_until"] - now]
        cap = 150 if priority == 0 else 130
        if len(self.history) >= cap:
            waits.append(self.history[-cap][0] + 60 - now)
        limit = self.buckets[bucket].get("limit")
        if limit is not None:
            same_bucket = [at for at, b, _ in self.history if b == bucket]
            if limit == 0:
                waits.append(60)
            elif len(same_bucket) >= limit:
                waits.append(same_bucket[-limit] + 60 - now)
        background = [at for at, _, p in self.history if p == 3]
        if priority == 3 and len(background) >= 30:
            waits.append(background[-30] + 60 - now)
        return max(waits)

    @asynccontextmanager
    async def slot(self, priority, bucket, deadline=None, *, account_read=None):
        async with self.condition:
            ticket = self.sequence
            self.sequence += 1
            if (
                len(self.waiters) >= (64 if priority == 0 else 48)
                or priority == 3
                and sum(p == 3 for p, _ in self.waiters.values()) >= 16
            ):
                if account_read:
                    raise PendingAlpacaAccount(account_read, "queue full")
                raise SafetyError("Alpaca request queue full; no request sent")
            self.waiters[ticket] = (priority, bucket)
            started = time.monotonic()
            try:
                while True:
                    now = time.monotonic()
                    if deadline is not None and time.time() + 1 >= deadline:
                        raise ExchangeRejected("Alpaca intent expired before the network write")
                    if now - started >= 12:
                        if account_read:
                            raise PendingAlpacaAccount(account_read, "quota/queue wait")
                        error = PendingAlpacaData if bucket == "data" else SafetyError
                        raise error("Alpaca request deferred by quota/queue; no request sent")
                    eligible = [
                        (p, t) for t, (p, b) in self.waiters.items() if self.delay(p, b, now) <= 0
                    ]
                    if not self.busy and eligible and min(eligible)[1] == ticket:
                        self.busy = True
                        self.last_request = now
                        self.history.append((now, bucket, priority))
                        break
                    wait = min(0.35, max(0.01, self.delay(priority, bucket, now)))
                    try:
                        await asyncio.wait_for(self.condition.wait(), wait)
                    except TimeoutError:
                        pass
            finally:
                del self.waiters[ticket]
                self.condition.notify_all()
        try:
            yield
        finally:
            async with self.condition:
                self.busy = False
                self.condition.notify_all()

    def response(self, bucket, method, path, response, started):
        now, wall = time.monotonic(), time.time()
        values = {}
        for name in ("Limit", "Remaining", "Reset"):
            raw = response.headers.get("X-RateLimit-" + name)
            try:
                number = int(raw)
                if number >= 0:
                    values[name.lower()] = number
            except (TypeError, ValueError):
                pass
        retry = response.headers.get("Retry-After")
        try:
            retry_delay = max(0, float(retry))
        except (TypeError, ValueError):
            try:
                retry_delay = max(0, parsedate_to_datetime(retry).timestamp() - wall)
            except (TypeError, ValueError, OverflowError):
                retry_delay = 0
        state = self.buckets[bucket]
        state.update(values, observed_at=wall)
        if response.status == 429 or values.get("remaining") == 0:
            # Convert once to monotonic time. Do not shorten a server-directed wait.
            delay = max(retry_delay, values.get("reset", wall) - wall, 1)
            if response.status == 429 and not retry and "reset" not in values:
                delay = 60
            state["blocked_until"] = max(state["blocked_until"], now + delay)
        self.last = {
            "endpoint": path,
            "method": method,
            "http_status": response.status,
            "latency_seconds": round(now - started, 3),
            "received_at": wall,
        }

    def snapshot(self):
        now = time.monotonic()
        self.delay(0, "trading", now)
        return {
            "budget_per_minute": 150,
            "ordinary_budget": 130,
            "background_budget": 30,
            "admissions_last_minute": len(self.history),
            "queued": len(self.waiters),
            "last_response": self.last,
            "quotas": {
                key: {
                    **{k: v for k, v in value.items() if k != "blocked_until"},
                    "retry_in_seconds": max(0, round(value["blocked_until"] - now, 2)),
                }
                for key, value in self.buckets.items()
            },
        }
