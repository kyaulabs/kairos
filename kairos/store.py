import json
import sqlite3
import time
from pathlib import Path

from kairos.domain import TERMINAL, SafetyError, dec


def encode(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, path, *, clock=None):
        self.clock = clock or (lambda: time.time())
        if path != ":memory:":
            Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS htf_minutes (
                pair TEXT NOT NULL, ts INTEGER NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY (pair, ts));
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
                kind TEXT NOT NULL, data TEXT NOT NULL);
        """)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self._put(key, value)

    def _put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, encode(value)))

    def orders(self):
        return [
            json.loads(row[0]) for row in self.db.execute("SELECT data FROM orders ORDER BY rowid")
        ]

    def display_orders(self, archived=False, limit=100):
        return [
            order
            for order in self.orders()
            if (order.get("mode") == "dry-run" and order.get("archived") is True) == archived
        ][-limit:]

    def paper_order_history(self, order_id, operation):
        with self.db:
            row = self.db.execute("SELECT data FROM orders WHERE id=?", (order_id,)).fetchone()
            order = json.loads(row[0]) if row else None
            if not order or order.get("mode") != "dry-run" or order.get("status") not in TERMINAL:
                raise SafetyError("Only completed paper orders can be archived or deleted")
            if operation == "delete":
                self.db.execute(
                    """DELETE FROM events WHERE json_extract(data, '$.mode')='dry-run'
                    AND ((kind='fill' AND json_extract(data, '$.order_id')=?)
                    OR (kind='order' AND json_extract(data, '$.id')=?))""",
                    (order_id, order_id),
                )
                self.db.execute("DELETE FROM orders WHERE id=?", (order_id,))
            elif operation in {"archive", "restore"}:
                order["archived"] = operation == "archive"
                # UPDATE preserves chronological row order, unlike save_order's REPLACE.
                self.db.execute("UPDATE orders SET data=? WHERE id=?", (encode(order), order_id))
            else:
                raise SafetyError("Unknown paper history action")
            self._put("order_history_revision", self.get("order_history_revision", 0) + 1)

    def save_order(self, order, ledger=None):
        # The cumulative fill checkpoint and its ledger update must commit together.
        with self.db:
            previous = self.db.execute(
                "SELECT data FROM orders WHERE id=?", (order["id"],)
            ).fetchone()
            previous = json.loads(previous[0]) if previous else {}
            if order.get("run_id"):
                key = "execution-run-totals:" + order["run_id"]
                totals = self.get(key, {"filled_orders": 0, "paid_fees": {}})
                if dec(order["filled"]) > 0 and not dec(previous.get("filled", 0)):
                    totals["filled_orders"] += 1
                delta = dec(order["fee"]) - dec(previous.get("fee", 0))
                if delta:
                    currency = order.get("quote", "USD")
                    totals["paid_fees"][currency] = str(
                        dec(totals["paid_fees"].get(currency, 0)) + delta
                    )
                self._put(key, totals)
            self.db.execute(
                "INSERT OR REPLACE INTO orders VALUES (?, ?)", (order["id"], encode(order))
            )
            if ledger is not None:
                key = (
                    "margin"
                    if order.get("product") == "margin"
                    else ("futures:" if order.get("product") == "futures" else "ledger:")
                    + order["mode"]
                )
                self._put(key, ledger)

    def event(self, kind, data):
        ts = self.clock()
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO events(ts,kind,data) VALUES (?,?,?)", (ts, kind, encode(data))
            )
        return {"id": cursor.lastrowid, "ts": ts, "kind": kind, "data": data}

    def history(self, limit=200):
        rows = self.db.execute(
            """SELECT e.id,e.ts,e.kind,e.data FROM events e
            WHERE NOT EXISTS (
                SELECT 1 FROM orders o
                WHERE o.id=CASE e.kind
                    WHEN 'fill' THEN json_extract(e.data, '$.order_id')
                    WHEN 'order' THEN json_extract(e.data, '$.id') END
                AND json_extract(e.data, '$.mode')='dry-run'
                AND json_extract(o.data, '$.mode')='dry-run'
                AND json_extract(o.data, '$.archived')=1
            ) ORDER BY e.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "kind": r[2], "data": json.loads(r[3])} for r in reversed(rows)
        ]

    def decision_summary(self, strategy, pair, mode, product, run_id=None):
        rows = self.db.execute(
            """SELECT json_extract(data, '$.action'),
                COALESCE(json_extract(data, '$.reason'), 'Legacy model assessment'), COUNT(*)
            FROM events WHERE kind='decision'
                AND json_extract(data, '$.strategy')=? AND json_extract(data, '$.pair')=?
                AND json_extract(data, '$.mode')=?
                AND COALESCE(json_extract(data, '$.product'), json_extract(data, '$.state.product'), 'spot')=?
                AND (? IS NULL OR json_extract(data, '$.run_id')=?)
            GROUP BY 1, 2 ORDER BY COUNT(*) DESC""",
            (strategy, pair, mode, product, run_id, run_id),
        ).fetchall()
        # Repeated checks are observations, not independent signals/trades. Older
        # records without these inputs remain unavailable, never inferred as zero.
        checks = self.db.execute(
            """WITH scoped AS (
                SELECT json_extract(data, '$.state') AS input FROM events
                WHERE kind='decision' AND json_extract(data, '$.strategy')=?
                AND json_extract(data, '$.pair')=? AND json_extract(data, '$.mode')=?
                AND COALESCE(json_extract(data, '$.product'), json_extract(data, '$.state.product'), 'spot')=?
                AND (? IS NULL OR json_extract(data, '$.run_id')=?)
            ), checks AS (
                SELECT CASE
                    WHEN json_type(input, '$.pullback_long') IN ('true', 'false')
                    THEN json_extract(input, '$.pullback_long') OR
                        (? != 'spot' AND json_extract(input, '$.pullback_short'))
                    WHEN json_type(input, '$.signal')='text'
                    THEN json_extract(input, '$.signal')='buy' OR
                        (? != 'spot' AND json_extract(input, '$.signal')='sell')
                    END AS signal,
                    CASE WHEN json_type(input, '$.pullback_long') IN ('true', 'false')
                    THEN json_extract(input, '$.entry_eligible') OR
                        (? != 'spot' AND json_extract(input, '$.short_entry_eligible'))
                    WHEN json_type(input, '$.cost_eligible') IN ('true', 'false')
                    THEN json_extract(input, '$.cost_eligible') AND
                        json_extract(input, '$.range_eligible') AND
                        (json_extract(input, '$.signal')='buy' OR
                            (? != 'spot' AND json_extract(input, '$.signal')='sell'))
                    END AS qualified
                FROM scoped
            ) SELECT COUNT(signal), SUM(signal), COUNT(qualified), SUM(qualified) FROM checks""",
            (strategy, pair, mode, product, run_id, run_id, product, product, product, product),
        ).fetchone()
        windows, candidates = self.db.execute(
            """SELECT COUNT(DISTINCT CASE WHEN kind='htf-observation' THEN json_extract(data,'$.window_end') END),
                      COUNT(DISTINCT CASE WHEN kind='candidate' THEN json_extract(data,'$.id') END)
               FROM events WHERE kind IN ('htf-observation','candidate') AND json_extract(data,'$.run_id')=?""",
            (run_id,),
        ).fetchone()
        return {
            "distinct_windows": windows if run_id else None,
            "distinct_candidates": candidates if run_id else None,
            "entry_checks": {
                "observed": checks[0],
                "signals": checks[1],
                "cost_observed": checks[2],
                "cost_qualified": checks[3],
            },
            "assessments": sum(count for _, _, count in rows),
            "actions": {
                action: sum(n for a, _, n in rows if a == action)
                for action in ("buy", "sell", "hold")
            },
            "hold_reasons": [
                {"reason": reason, "count": count}
                for action, reason, count in rows
                if action == "hold"
            ],
        }

    def close(self):
        self.db.close()
