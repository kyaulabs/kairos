import json
import sqlite3
import time
from pathlib import Path


def encode(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, data TEXT NOT NULL);
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

    def save_order(self, order, ledger=None):
        # The cumulative fill checkpoint and its ledger update must commit together.
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO orders VALUES (?, ?)", (order["id"], encode(order))
            )
            if ledger is not None:
                key = "margin" if order.get("product") == "margin" else "ledger:" + order["mode"]
                self._put(key, ledger)

    def event(self, kind, data):
        ts = time.time()
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO events(ts,kind,data) VALUES (?,?,?)", (ts, kind, encode(data))
            )
        return {"id": cursor.lastrowid, "ts": ts, "kind": kind, "data": data}

    def history(self, limit=200):
        rows = self.db.execute(
            "SELECT id,ts,kind,data FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "kind": r[2], "data": json.loads(r[3])} for r in reversed(rows)
        ]

    def close(self):
        self.db.close()
