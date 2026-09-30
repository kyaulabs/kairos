"""Content-addressed research artifacts and an append-only experiment journal."""

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from kairos.domain import SafetyError


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def utc(text):
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise SafetyError("Research timestamps require a timezone")
    return int(value.timestamp())


class Artifacts:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def put(self, value):
        body, key = encode(value), digest(value)
        path = self.root / (key + ".json")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != body:
                raise SafetyError("Artifact collision or modification") from None
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
        return key

    def get(self, key):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise SafetyError("Invalid artifact key")
        path = self.root / (key + ".json")
        if path.is_symlink():
            raise SafetyError("Artifact symlink rejected")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != key:
            raise SafetyError("Artifact checksum changed")
        return json.loads(body)


class Registry:
    """All starts survive failures/crashes. No deletion API; interrupted trials stay started.

    This is a local audit control, not tamper-proof storage against its filesystem owner.
    Keep one registry for the study; external/manual experiments must also be declared.
    """

    def __init__(self, root):
        self.artifacts = Artifacts(Path(root) / "objects")
        path = Path(root) / "research.sqlite3"
        if path.is_symlink():
            raise SafetyError("Registry symlink rejected")
        new = not path.exists()
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        if not new and self.db.execute("PRAGMA application_id").fetchone()[0] != 1263686227:
            self.db.close()
            raise SafetyError("Not a Kairos research registry; refusing to use this database")
        os.chmod(path, 0o600)
        self.db.executescript("""
            PRAGMA application_id=1263686227;
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS journal (
                seq INTEGER PRIMARY KEY, at REAL NOT NULL, trial TEXT NOT NULL,
                kind TEXT NOT NULL, artifact TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seals (study TEXT PRIMARY KEY, artifact TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS final_claims (study TEXT PRIMARY KEY, trial TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS immutable_update BEFORE UPDATE ON journal
                BEGIN SELECT RAISE(ABORT, 'append only'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_delete BEFORE DELETE ON journal
                BEGIN SELECT RAISE(ABORT, 'append only'); END;
        """)

    def close(self):
        self.db.close()

    def record(self, trial, kind, payload):
        key = self.artifacts.put(payload)
        self.db.execute(
            "INSERT INTO journal(at,trial,kind,artifact) VALUES(?,?,?,?)",
            (time.time(), trial, kind, key),
        )
        return key

    @contextmanager
    def trial(self, spec):
        identifier = str(uuid.uuid4())
        self.record(identifier, "started", spec)
        try:
            yield identifier
        except BaseException as exc:
            self.record(identifier, "failed", {"type": type(exc).__name__, "message": str(exc)})
            raise
        else:
            self.record(identifier, "completed", {"spec": digest(spec)})

    def seal(self, study, specification):
        key = self.artifacts.put(specification)
        self.db.execute("INSERT INTO seals VALUES(?,?)", (study, key))
        self.record(study, "sealed", {"seal": key})
        return key

    def claim_final(self, study, specification, trial):
        """Consume the entire final family atomically BEFORE reading final data.

        A failed final attempt remains consumed. No automated retries with altered rules.
        """
        row = self.db.execute("SELECT artifact FROM seals WHERE study=?", (study,)).fetchone()
        if not row or self.artifacts.get(row[0]) != specification:
            raise SafetyError("Final evaluation requires the exact sealed specification")
        try:
            self.db.execute(
                "INSERT INTO final_claims VALUES(?,?)", (specification["holdout"], trial)
            )
        except sqlite3.IntegrityError:
            raise SafetyError("Final holdout already consumed; new data is required") from None
        self.record(trial, "final_opened", {"study": study, "seal": row[0]})

    def entries(self):
        return [
            dict(seq=s, at=t, trial=i, kind=k, artifact=a)
            for s, t, i, k, a in self.db.execute("SELECT * FROM journal ORDER BY seq")
        ]
