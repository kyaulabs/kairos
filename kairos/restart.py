"""Private restart coordination; never creates or renews trading permission."""

import contextlib
import fcntl
import json
import math
import os
import secrets
import stat
import time
from pathlib import Path

from kairos.domain import SafetyError

CHECKPOINT = "restart-checkpoint"
MUTABLE_OPERATION_FIELDS = {"status", "phase", "submitted"}


class StopRevoked(SafetyError):
    """The gateway durably accepted Stop; the worker watcher owns final reporting."""


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


class Control:
    """A private, atomic Stop generation shared by the web and engine processes."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "restart-control.json"
        with self.lock():
            if not self.path.exists():
                self.write(
                    {
                        "format": 1,
                        "epoch": secrets.token_hex(16),
                        "csrf": secrets.token_urlsafe(32),
                        "generation": 0,
                    }
                )
            self.read_unlocked()

    @contextlib.contextmanager
    def lock(self):
        fd = os.open(
            self.directory / "restart-control.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        try:
            self.private(fd)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    @staticmethod
    def private(fd):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SafetyError("Restart control must be private and owned by the service user")

    def read_unlocked(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as source:
            self.private(source.fileno())
            value = json.load(source)
        if not (
            value.get("format") == 1
            and isinstance(value.get("epoch"), str)
            and len(value["epoch"]) == 32
            and isinstance(value.get("csrf"), str)
            and len(value["csrf"]) == 43
            and type(value.get("generation")) is int
            and value["generation"] >= 0
        ):
            raise SafetyError("Invalid restart control; trading remains blocked")
        return value

    def read(self):
        with self.lock():
            return self.read_unlocked()

    def write(self, value):
        temporary = self.directory / (".restart-control-" + secrets.token_hex(8))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as target:
                json.dump(value, target, allow_nan=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def revoke(self):
        with self.lock():
            value = self.read_unlocked()
            value["generation"] += 1
            value["stopped_at"] = time.time()
            self.write(value)
            return value["generation"]

    def matches(self, checkpoint):
        current = self.read()
        return all(checkpoint.get(key) == current[key] for key in ("epoch", "generation"))


def eligible(engine, scope):
    return bool(
        engine.restart_control
        and scope
        and engine.client.environment == "demo"
        and scope.get("kind") == "strategy"
        and scope.get("strategy") == "htf"
        and scope.get("protocol")
        == "okx-demo-bounded-strategies-v1; separate from the frozen Alpaca trial"
    )


def capture(engine):
    """Called only after successful finite authorization, never from a preview."""
    scope = engine.authorization
    if not eligible(engine, scope):
        engine.store.put(CHECKPOINT, None)
        return
    control = engine.restart_generation
    if not control or not engine.restart_control.matches(control):
        raise SafetyError("Stop won authorization; no restart checkpoint created")
    run = engine.active_run()
    if not run:
        raise SafetyError("Restart checkpoint requires an existing execution run")
    checkpoint = {
        "format": 1,
        "epoch": control["epoch"],
        "generation": control["generation"],
        "boot_id": boot_id(),
        "scope": scope,
        "monotonic_deadline": engine.authorization_monotonic_deadline,
        "run_id": run["id"],
        "execution_revision": run["execution_revision"],
    }
    engine.store.put(CHECKPOINT, checkpoint)
    engine.restart_generation = {key: checkpoint[key] for key in ("epoch", "generation")}


def validate(engine, checkpoint):
    scope = checkpoint.get("scope") if isinstance(checkpoint, dict) else None
    if not eligible(engine, scope) or checkpoint.get("format") != 1:
        raise SafetyError("This trading mode has no restart recovery contract; remain paused")
    monotonic = checkpoint.get("monotonic_deadline")
    if (
        not isinstance(monotonic, (int, float))
        or not math.isfinite(monotonic)
        or checkpoint.get("boot_id") != boot_id()
        or time.monotonic() >= monotonic
        or engine.clock() >= scope["deadline"]
    ):
        raise SafetyError("Original restart permission expired or kernel boot changed; no renewal")
    if not engine.restart_control.matches(checkpoint):
        raise SafetyError("Stop revoked the restart checkpoint; no resume")
    operation = engine.store.get("okx-operation")
    if (
        not operation
        or operation.get("status") != "running"
        or any(
            operation.get(key) != value
            for key, value in scope.items()
            if key not in MUTABLE_OPERATION_FIELDS
        )
    ):
        raise SafetyError("Original operation changed or ended; no replay or resume")
    run = engine.store.get(engine.run_key())
    if (
        scope["settings_id"] != engine.settings_id()
        or scope["environment"] != engine.client.environment
        or scope["pair"] != engine.settings["pair"]
        or not engine.client.allow_writes
        or not run
        or run["id"] != checkpoint["run_id"]
        or run["execution_revision"] != checkpoint["execution_revision"]
        or run["settings_id"] != scope["settings_id"]
        or run.get("purpose") != "okx_demo_strategy"
    ):
        raise SafetyError(
            "Restart settings, account environment, write gate or execution run changed"
        )
    return scope


async def restore(engine, checkpoint):
    """Reconcile first; retain original ownership, attempt counts and both deadlines."""
    from kairos import htf
    from kairos.domain import dec
    from kairos.engine import EXECUTION_REVISION
    from kairos.okx_engine import fingerprint
    from kairos.okx_strategy import markets

    scope = validate(engine, checkpoint)
    if engine.recovery_required or engine.orders(active=True) or engine.store.get("cycle"):
        raise SafetyError("Restart reconciliation is incomplete; original intents retained")
    if (
        fingerprint({"uid": engine.account_info["uid"], "environment": engine.client.environment})
        != scope["account_scope"]
    ):
        raise SafetyError("Restart account differs from the original authorization")
    if {pair.id for pair in markets(engine)} != set(scope["markets"]):
        raise SafetyError("Restart market scope changed")
    for identifier, limit in scope["markets"].items():
        if fingerprint(engine.client.instruments[identifier]) != limit["market_rules"]:
            raise SafetyError("Restart instrument rules changed; new preview required")
    used = sum(order.get("authorization_id") == scope["id"] for order in engine.orders())
    if used >= scope["max_orders"]:
        raise SafetyError("Original durable attempt limit reached; no restart permission")
    # Never call ensure_run/prepare: those can create a new run or reset schedules.
    engine.resumed_run = {
        "id": checkpoint["run_id"],
        "execution_revision": checkpoint["execution_revision"],
    }
    engine.stop_generation = scope["generation"]
    engine.execution_purpose = "okx_demo_strategy"
    engine.authorization = scope
    engine.authorization_monotonic_deadline = checkpoint["monotonic_deadline"]
    engine.restart_generation = {key: checkpoint[key] for key in ("epoch", "generation")}
    engine.running = engine.armed = True
    try:
        await engine.wait_htf_book()
        await engine.wait_htf_fees()
        for identifier, limit in scope["markets"].items():
            pair = engine.resolve(identifier)
            if max(engine.fees.reserve(pair), engine.fees.reserve(pair, True)) > dec(
                limit["fee_bps"]
            ):
                raise SafetyError(
                    "Restart fees exceed the original allowance; new preview required"
                )
        validate(engine, checkpoint)  # Stop/expiry can win every awaited preflight read.
        engine.permission()
        htf.arm(engine)  # Retire pre-restart entry candidates; retain owned exit plans.
    except BaseException:
        engine.running, engine.armed, engine.authorization = False, False, None
        engine.resumed_run = None
        raise
    engine.last_error = None
    engine.event(
        "system",
        {
            "message": "Resumed the original finite OKX demo HTF authorization after reconciliation. Original wall/monotonic deadlines, budget, durable attempts, accounting and execution run retained; no replay.",
            "authorization_id": scope["id"],
            "run_id": checkpoint["run_id"],
            "previous_execution_revision": checkpoint["execution_revision"],
            "execution_revision": EXECUTION_REVISION,
        },
    )
