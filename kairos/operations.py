"""Session-local operating time and bounded, non-blocking Discord notifications."""

import asyncio
import contextlib
import re
import time
import uuid

import aiohttp

from kairos import diagnostics


class Operations:
    def __init__(self, engine):
        self.engine = engine
        self.session_id = str(uuid.uuid4())
        self.status = "paused"
        self.since = engine.clock()
        self.monotonic = time.monotonic()
        self.totals = {}
        self.last_evaluation = None
        self.alerts = None

    def set(self, status, reason=None):
        if status == self.status:
            return
        now = time.monotonic()
        self.totals[self.status] = self.totals.get(self.status, 0) + max(0, now - self.monotonic)
        old = self.status
        self.status, self.since, self.monotonic = status, self.engine.clock(), now
        data = {
            "message": f"Trading state: {status}",
            "session_id": self.session_id,
            "previous": old,
            "status": status,
            "since": self.since,
            "reason": reason,
            "session_seconds": self.totals.copy(),
        }
        try:
            self.engine.event("operating-state", data)
        except Exception:
            self.engine.running = False
            self.engine.recovery_required = True
            raise
        if self.alerts and (
            status in {"halted", "retry-wait", "waiting-fees"}
            or old in {"retry-wait", "waiting-fees"}
        ):
            self.alerts.send(status)

    def failed(self):
        # Recording failure must still revoke permission and label in-memory state honestly.
        now = time.monotonic()
        self.totals[self.status] = self.totals.get(self.status, 0) + max(0, now - self.monotonic)
        self.status, self.since, self.monotonic = "halted", self.engine.clock(), now
        if self.alerts:
            self.alerts.send("halted; audit/storage failure")

    def snapshot(self):
        totals = self.totals.copy()
        totals[self.status] = totals.get(self.status, 0) + max(0, time.monotonic() - self.monotonic)
        return {
            "session_id": self.session_id,
            "status": self.status,
            "since": self.since,
            "session_seconds": totals,
            "last_evaluation_at": self.last_evaluation,
            "notifications": self.alerts.snapshot() if self.alerts else {"configured": False},
        }


class DiscordAlerts:
    def __init__(self, session, url):
        self.session, self.url = session, url
        diagnostics.register_secrets(url, url.rsplit("/", 1)[-1] if url else "")
        self.valid = bool(
            re.fullmatch(r"https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+", url)
        )
        self.queue = asyncio.Queue(maxsize=16)
        self.task = None
        self.last_status = (
            "disabled" if not url else "ready" if self.valid else "invalid configuration"
        )
        self.last_at = None

    def snapshot(self):
        return {"configured": self.valid, "status": self.last_status, "last_at": self.last_at}

    def send(self, status):
        if not self.valid:
            return
        if self.queue.full():
            self.last_status = "queue full; notification dropped"
            return
        self.queue.put_nowait(status)
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def run(self):
        while not self.queue.empty():
            status = self.queue.get_nowait()
            try:
                async with self.session.post(
                    self.url,
                    json={
                        "content": f"Kairos paper trading: {status}. Check the authenticated dashboard. No trading action was taken by this notification.",
                        "allowed_mentions": {"parse": []},
                    },
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    self.last_status = (
                        "delivered"
                        if 200 <= response.status < 300
                        else f"delivery failed (HTTP {response.status}); not retried"
                    )
            except (aiohttp.ClientError, TimeoutError):
                self.last_status = "delivery transport failure; not retried"
            self.last_at = time.time()

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
