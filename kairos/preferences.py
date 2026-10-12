"""Shared dashboard preferences, isolated from trading stores and permission."""

import copy
import json
import math
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

from kairos.settings import CANDLE_INTERVALS

EXCHANGES = ("kraken", "alpaca", "okx", "okx-demo")
STUDIES = ("volume", "macd", "bollinger", "rsi", "sma")
DEFAULTS = {
    "appearance": {"theme": "dark", "accent": "violet"},
    "candle_interval": 1,
    "indicators": {
        **dict.fromkeys(STUDIES, False),
        "volume": True,
        "hidden": {},
        "settings": {},
        "collapsed": False,
    },
    **{f"favorites:{exchange}": [] for exchange in EXCHANGES},
    **{f"chart_market:{exchange}": None for exchange in EXCHANGES},
}
INPUTS = {
    "volume": (),
    "macd": ("fast", "slow", "signal"),
    "bollinger": ("period", "deviation"),
    "rsi": ("period",),
    "sma": ("fast", "slow"),
}
COLORS = {
    "volume": ("up", "down"),
    "macd": ("line", "signalColor", "up", "down"),
    "bollinger": ("line",),
    "rsi": ("line",),
    "sma": ("line", "signalColor"),
}


def require(condition):
    if not condition:
        raise ValueError("Invalid dashboard preference")


def market_id(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and all(
            character.isascii() and (character.isalnum() or character in ":/_-.")
            for character in value
        )
    )


def validate(key, value):
    require(isinstance(key, str) and key in DEFAULTS)
    if key == "appearance":
        require(isinstance(value, dict) and set(value) == {"theme", "accent"})
        require(value["theme"] in ("dark", "light"))
        require(value["accent"] in ("violet", "green", "red", "blue"))
    elif key == "candle_interval":
        require(type(value) is int and value in CANDLE_INTERVALS)
    elif key.startswith("favorites:"):
        require(isinstance(value, list) and len(value) <= 100 and all(map(market_id, value)))
        require(len(set(value)) == len(value))
    elif key.startswith("chart_market:"):
        require(value is None or market_id(value))
    else:
        require(
            isinstance(value, dict) and set(value) == {*STUDIES, "hidden", "settings", "collapsed"}
        )
        require(all(type(value[name]) is bool for name in (*STUDIES, "collapsed")))
        hidden, settings = value["hidden"], value["settings"]
        require(isinstance(hidden, dict) and set(hidden) <= set(STUDIES))
        require(all(type(flag) is bool for flag in hidden.values()))
        require(isinstance(settings, dict) and set(settings) <= set(STUDIES))
        for study, fields in settings.items():
            appearance = () if study == "volume" else ("style", "width")
            require(
                isinstance(fields, dict)
                and set(fields) <= {*INPUTS[study], *COLORS[study], *appearance}
            )
            for name, number in fields.items():
                if name in INPUTS[study]:
                    require(type(number) in (int, float) and math.isfinite(number))
                    require(
                        0.1 <= number <= 10
                        if name == "deviation"
                        else type(number) is int and 1 <= number <= 200
                    )
                elif name in COLORS[study]:
                    require(number in ("primary", "info", "warning", "success", "danger"))
                elif name == "style":
                    require(number in ("default", "solid", "dashed", "dotted"))
                elif name == "width":
                    require(
                        number == "default"
                        or type(number) in (int, float)
                        and number in (1, 1.5, 2, 3)
                    )
            if "fast" in fields or "slow" in fields:
                fast, slow = (12, 26) if study == "macd" else (8, 21)
                require(fields.get("fast", fast) < fields.get("slow", slow))
    return value


class Conflict(Exception):
    """Another page saved this preference; never overwrite it with a stale draft."""


class Preferences:
    def __init__(self, directory):
        self.path = Path(directory) / "dashboard.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise PermissionError(
                    "Dashboard preferences must be private and owned by the service user"
                )
        finally:
            os.close(fd)
        with closing(self.connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL, revision INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS asset_names (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL, fetched REAL NOT NULL)"
            )

    def connect(self):
        return sqlite3.connect(self.path)

    @staticmethod
    def snapshot(db):
        values = copy.deepcopy(DEFAULTS)
        revisions = dict.fromkeys(DEFAULTS, 0)
        for key, raw, revision in db.execute("SELECT key,value,revision FROM preferences"):
            require(type(revision) is int and revision >= 1)
            values[key] = validate(key, json.loads(raw))
            revisions[key] = revision
        return {"values": values, "revisions": revisions}

    def read_names(self):
        with closing(self.connect()) as db:
            row = db.execute("SELECT value,fetched FROM asset_names WHERE id=1").fetchone()
            return (json.loads(row[0]), row[1]) if row else ({}, 0)

    def save_names(self, names, fetched):
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO asset_names VALUES (1,?,?)", (json.dumps(names), fetched)
            )

    def read(self):
        db = self.connect()
        try:
            return self.snapshot(db)
        finally:
            db.close()

    def save(self, key, value, revision):
        validate(key, value)
        require(type(revision) is int and revision >= 0)
        db = self.connect()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT revision FROM preferences WHERE key=?", (key,)).fetchone()
                if revision != (row[0] if row else 0):
                    raise Conflict
                db.execute(
                    "INSERT OR REPLACE INTO preferences VALUES (?,?,?)",
                    (key, json.dumps(value, allow_nan=False), revision + 1),
                )
                return self.snapshot(db)
        finally:
            db.close()
