"""Read-only diagnostic capture while execution is paused or blocked; no model calls."""

import hashlib
import json

from kairos.htf_review import native_rows


class Observations:
    def __init__(self, engine):
        self.engine = engine
        self.enabled = False
        self.next_at = 0
        self.window = None
        self.latest = None

    async def sample(self):
        e = self.engine
        if (
            not self.enabled
            or e.shutting_down
            or not e.ready
            or not e.store.get("alpaca-account")
            or e.settings["strategy"] != "htf"
            or e.clock() < self.next_at
        ):
            return
        self.next_at = e.clock() + 60
        e.event(
            "health-sample",
            {
                "message": "Read-only execution health sample",
                "operating_state": e.operations.status,
                "operations": e.operations.snapshot(),
                "market_data": e.kraken.market_data.snapshot(),
                "running": e.running,
                "recovery_required": e.recovery_required,
            },
        )
        cutoff = e.htf_review.window_end(e.clock())
        if e.running and not (e.market_wait or e.account_wait or e.long_retry):
            return  # Normal HTF observations already retain active-strategy input history.
        if self.window == (e.settings_id(), cutoff):
            return
        signature, minutes = e.settings_id(), e.settings["candle_minutes"]
        pair = e.resolve(e.settings["pair"])
        rows = await e.kraken.bars(pair, minutes, count=30, background=True)
        if (
            e.shutting_down
            or e.settings_id() != signature
            or e.htf_review.window_end(e.clock()) != cutoff
        ):
            return  # Do not attach a late response to another market, interval or window.
        rows, count, required = native_rows(pair, rows, cutoff, minutes)
        latest = {
            "window_end": cutoff,
            "observed_at": e.clock(),
            "pair": pair.id,
            "bar_minutes": minutes,
            "consecutive": count,
            "required": required,
        }
        e.event(
            "market-observation",
            {
                "message": "Read-only closed history; not evaluated trading opportunities",
                "window_end": cutoff,
                "history_policy": e.htf_review.policy,
                "history_fetched_at": e.clock(),
                "native_history": rows,
                "consecutive": count,
                "required": required,
                "history_revision": hashlib.sha256(
                    json.dumps(rows, separators=(",", ":")).encode()
                ).hexdigest()
                if rows
                else None,
            },
        )
        self.latest = latest
        if rows:
            self.window = (e.settings_id(), cutoff)
