"""Causal four-family metas. Signals are proposals, never execution permission."""

from collections import Counter, deque
from decimal import Decimal

from kairos.domain import ZERO, SafetyError, dec
from kairos.research_signals import RULES, Setup, Signals

FAMILIES = {
    "vwap-meta": ("vwap-pullback", "vwap-reclaim", "vwap-anchored", "vwap-reversion"),
    "fib-meta": ("fib-confluence", "fib-ema", "fib-extension", "fib-sweep"),
}
FRAMES = (15, 30, 60, 360, 720)
VERSION = 1


class ParentImpulse:
    def __init__(self, minutes):
        self.max_age = RULES["swing_max_age_bars"] * minutes * 60
        self.parent = self.latest = self.last_end = None

    def valid(self, swing, end, close):
        return (
            swing is not None
            and swing[4] > swing[1]
            and end - swing[5] <= self.max_age
            and close >= swing[1]
        )

    def update(self, candidate, end, close, *, contiguous):
        if self.last_end is not None and end <= self.last_end:
            raise SafetyError("Parent impulse clock must advance")
        if not contiguous:
            self.parent = self.latest = None
        if self.parent is not None and not self.valid(self.parent, end, close):
            self.parent = None
        if self.latest is not None and not self.valid(self.latest, end, close):
            self.latest = None
        if candidate is not None and self.valid(candidate, end, close):
            self.latest = candidate
        if self.parent is None:
            self.parent = self.latest
        elif candidate is not None and self.latest == candidate:
            nested = candidate[1] >= self.parent[1] and candidate[4] <= self.parent[4]
            if not nested:
                self.parent = candidate
        self.last_end = end
        return self.parent


class Structure:
    def __init__(self, minutes):
        self.minutes = minutes
        self.rows = deque(maxlen=5)
        self.tr = deque(maxlen=RULES["atr_bars"])
        self.fast = self.slow = self.low = self.swing = self.anchor = self.anchor_known = None
        self.count = 0
        self.parent = ParentImpulse(minutes)

    def push(self, row):
        end, close = row[0] + self.minutes * 60, dec(row[4])
        prior = dec(self.rows[-1][4]) if self.rows else close
        contiguous = bool(self.rows and row[0] == self.rows[-1][0] + self.minutes * 60)
        self.tr.append(
            max(dec(row[2]) - dec(row[3]), abs(dec(row[2]) - prior), abs(dec(row[3]) - prior))
        )
        self.fast = close if self.fast is None else self.fast + dec(2) / 21 * (close - self.fast)
        self.slow = close if self.slow is None else self.slow + dec(2) / 51 * (close - self.slow)
        self.rows.append(row)
        self.count += 1
        candidate = None
        recent = list(self.rows)
        if len(recent) == 5 and all(
            b[0] - a[0] == self.minutes * 60 for a, b in zip(recent, recent[1:], strict=False)
        ):
            center, others = recent[2], recent[:2] + recent[3:]
            if all(dec(center[3]) < dec(r[3]) for r in others):
                self.low = (center[0], dec(center[3]), end)
                self.anchor, self.anchor_known = center[0], end
            if (
                self.low
                and center[0] > self.low[0]
                and all(dec(center[2]) > dec(r[2]) for r in others)
            ):
                candidate = (*self.low, center[0], dec(center[2]), end)
        self.swing = self.parent.update(candidate, end, close, contiguous=contiguous)


class Context:
    def __init__(self, minutes):
        self.minutes = minutes
        self.engine = Structure(minutes)
        self.bucket = []
        self.closed_at = self.prior_slow = self.previous_start = self.last_now = None
        self.contiguous = False

    def push(self, row):
        now = row[0] + 900
        if self.last_now is not None and now <= self.last_now:
            raise SafetyError("Confirmation clock must advance")
        self.last_now = now
        start = row[0] // (self.minutes * 60) * self.minutes * 60
        if self.bucket and self.bucket[0][0] // (self.minutes * 60) * self.minutes * 60 != start:
            self.bucket = []
        self.bucket.append(row)
        if now != start + self.minutes * 60:
            return
        group, self.bucket = self.bucket, []
        if [r[0] for r in group] != list(range(start, now, 900)):
            return  # An incomplete higher bar is unknown, never padded.
        higher = [
            start,
            group[0][1],
            str(max(dec(r[2]) for r in group)),
            str(min(dec(r[3]) for r in group)),
            group[-1][4],
            "0",
            str(sum((dec(r[6]) for r in group), ZERO)),
            sum(r[7] for r in group),
        ]
        self.contiguous = (
            self.previous_start is not None and start == self.previous_start + self.minutes * 60
        )
        self.prior_slow = self.engine.slow
        self.engine.push(higher)
        self.closed_at, self.previous_start = now, start

    @property
    def fresh(self):
        return self.closed_at is not None and self.last_now < self.closed_at + self.minutes * 60

    @property
    def atr(self):
        return sum(self.engine.tr) / len(self.engine.tr) if self.engine.tr else ZERO

    @property
    def nonfalling(self):
        return self.prior_slow is not None and self.engine.slow >= self.prior_slow

    @property
    def slope(self):
        return (
            (self.engine.slow - self.prior_slow) / self.atr
            if self.prior_slow is not None and self.atr
            else ZERO
        )


def pack(value):
    """Lossless JSON arithmetic checkpoints; no pickle or dynamic class loading."""
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, deque):
        return {"deque": [pack(v) for v in value], "maxlen": value.maxlen}
    if isinstance(value, tuple):
        return {"tuple": [pack(v) for v in value]}
    if isinstance(value, list):
        return [pack(v) for v in value]
    if isinstance(value, dict):
        return {k: pack(v) for k, v in value.items()}
    return value


def unpack(value):
    if isinstance(value, list):
        return [unpack(v) for v in value]
    if isinstance(value, dict):
        if set(value) == {"decimal"}:
            return dec(value["decimal"])
        if set(value) == {"tuple"}:
            return tuple(unpack(v) for v in value["tuple"])
        if set(value) == {"deque", "maxlen"}:
            return deque((unpack(v) for v in value["deque"]), maxlen=value["maxlen"])
        return {k: unpack(v) for k, v in value.items()}
    return value


class MetaSignals:
    def __init__(self, policy, structure_minutes):
        if policy not in FAMILIES or structure_minutes not in FRAMES:
            raise SafetyError("Unsupported meta strategy or structure timeframe")
        self.policy, self.structure_minutes = policy, structure_minutes
        self.context = Context(structure_minutes)
        self.engines = {name: Signals(15, name, context=self.context) for name in FAMILIES[policy]}
        self.raw_counts, self.selected_counts = Counter(), Counter()

    def cancel(self):
        for engine in self.engines.values():
            engine.cancel("entry_rebaseline")

    def push(self, row, now):
        if row[0] + 900 > now:
            raise SafetyError("Forming confirmation bar")
        self.context.push(row)
        raw = {}
        for family, engine in self.engines.items():
            _, setup = engine.push(row, now)
            if setup:
                raw[family] = setup
        self.raw_counts.update(raw.keys())
        selected = next((family for family in FAMILIES[self.policy] if family in raw), None)
        if selected is None:
            return None
        self.selected_counts[selected] += 1
        original = raw[selected]
        facts = {
            **original.facts,
            "selected_strategy": selected,
            "underlying_candidate_id": original.facts["candidate_id"],
            "combination_policy": self.policy,
            "candidate_id": f"{self.policy}:{selected}:{original.closed_at}",
            "selection_known_at": original.closed_at,
        }
        return Setup(
            self.policy,
            original.closed_at,
            original.reference,
            original.stop,
            original.target,
            facts,
        )

    def checkpoint(self):
        context = {k: v for k, v in vars(self.context).items() if k != "engine"}
        structure = {k: v for k, v in vars(self.context.engine).items() if k != "parent"}
        children = {}
        for name, engine in self.engines.items():
            children[name] = {
                k: vars(v) if k in {"daily", "anchored"} else v
                for k, v in vars(engine).items()
                if k != "context"
            }
        return pack(
            {
                "version": VERSION,
                "policy": self.policy,
                "frame": self.structure_minutes,
                "context": context,
                "structure": structure,
                "parent": vars(self.context.engine.parent),
                "children": children,
                "raw_counts": self.raw_counts,
                "selected_counts": self.selected_counts,
            }
        )

    @classmethod
    def restore(cls, saved):
        s = unpack(saved)
        if s["version"] != VERSION:
            raise SafetyError("Meta signal checkpoint version changed")
        obj = cls(s["policy"], s["frame"])
        vars(obj.context).update(s["context"])
        vars(obj.context.engine).update(s["structure"])
        vars(obj.context.engine.parent).update(s["parent"])
        for name, fields in s["children"].items():
            engine = obj.engines[name]
            for k, v in fields.items():
                if k in {"daily", "anchored"}:
                    vars(getattr(engine, k)).update(v)
                else:
                    setattr(engine, k, Counter(v) if k == "events" else v)
        obj.raw_counts, obj.selected_counts = (
            Counter(s["raw_counts"]),
            Counter(s["selected_counts"]),
        )
        return obj
