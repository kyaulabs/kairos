"""Eight prospective, closed-candle spot hypotheses with bounded confirmation windows.

VWAP/sigma are volume-weighted HLC3 approximations, not trade-price VWAP/bands.
Pivots need two right-hand candles; setups, swing levels and anchors are never
backdated. Pending Fibonacci legs and breakout anchors remain fixed until the
setup confirms, expires or invalidates. These are research, not live policies.
"""

from collections import Counter, deque
from dataclasses import dataclass
from decimal import Decimal

from kairos.domain import ZERO, SafetyError, dec

VARIANTS = {
    "vwap-pullback": "VWAP trend pullback",
    "vwap-reclaim": "VWAP reclaim + volume confirmation",
    "vwap-anchored": "Confirmed-low anchored VWAP breakout + retest",
    "vwap-reversion": "VWAP lower 2σ recovery toward VWAP",
    "fib-confluence": "Fibonacci 38.2–61.8% + daily VWAP confluence",
    "fib-ema": "Fibonacci pullback + EMA trend continuation",
    "fib-extension": "Confirmed swing-high breakout to 1.618 extension",
    "fib-sweep": "Confirmed swing-low sweep + reclaim",
}
RULES = {
    "version": "vwap-fibonacci-pilot-v2",
    "warmup_bars": 50,
    "ema_fast": 20,
    "ema_slow": 50,
    "atr_bars": 14,
    "pivot_left_right": 2,
    "swing_max_age_bars": 96,
    "minimum_swing_atr": "1.5",
    "vwap_min_session_bars": 20,
    "volume_baseline_bars": 20,
    "reclaim_volume_multiple": "1.2",
    "touch_atr": "0.25",
    "confluence_atr": "0.25",
    "stop_buffer_atr": "0.25",
    "breakout_acceptance_atr": "0.1",
    "rejection_lower_wick": "0.35",
    "regime_slope_atr": "0.03",
    "setup_max_bars": 3,
    "retest_max_bars": 6,
    "default_target_r": "2",
    "max_hold_seconds": 14400,
}
DAILY_VARIANTS = {"vwap-pullback", "vwap-reclaim", "vwap-reversion", "fib-confluence"}


@dataclass(frozen=True)
class Setup:
    variant: str
    closed_at: int
    reference: Decimal
    stop: Decimal
    target: Decimal
    facts: dict


class WeightedPrices:
    def __init__(self):
        self.volume = self.pv = self.p2v = ZERO
        self.count = 0

    def add(self, row):
        price = sum(map(dec, row[2:5])) / 3
        volume = dec(row[6])
        self.volume += volume
        self.pv += price * volume
        self.p2v += price * price * volume
        self.count += 1

    def bands(self):
        if not self.volume:
            return None, None
        mean = self.pv / self.volume
        return mean, max(ZERO, self.p2v / self.volume - mean * mean).sqrt()


class Signals:
    def __init__(self, minutes, variant, *, context=None):
        if (
            minutes not in ((5, 15, 30, 60) if context is not None else (5, 15))
            or variant not in VARIANTS
        ):
            raise SafetyError("Unknown VWAP/Fibonacci research variant or timeframe")
        self.minutes, self.variant = minutes, variant
        self.context = context
        self.seen_anchor_known = None
        self.rows = deque(
            maxlen=max(
                RULES["swing_max_age_bars"] + 2 * RULES["pivot_left_right"],
                (2 * RULES["pivot_left_right"] + 1) * (context.minutes // minutes) + 2
                if context
                else 0,
            )
        )
        self.tr = deque(maxlen=RULES["atr_bars"])
        self.count = 0
        self.fast = self.slow = None
        self.daily, self.day = WeightedPrices(), None
        self.anchor, self.anchor_known, self.anchored = None, None, WeightedPrices()
        self.low = self.swing = self.pending = self.previous = None
        self.events = Counter()

    def cancel(self, reason):
        self.events[reason] += 1
        self.pending = None

    def push(self, row, now):
        step = self.minutes * 60
        end, day = row[0] + step, row[0] // 86400
        if end > now or self.rows and row[0] <= self.rows[-1][0]:
            raise SafetyError("Only ordered completed candles may enter research indicators")
        opening, high, low, close = map(dec, row[1:5])
        old = self.previous
        contiguous = bool(old and row[0] == old["ts"] + step)
        if self.pending:
            if not contiguous:
                self.cancel("gap_expired")
            elif end > self.pending["expires"]:
                self.cancel("expired")
            elif self.variant in DAILY_VARIANTS and day != self.pending["day"]:
                self.cancel("session_expired")
            elif close <= self.pending["stop"]:
                self.cancel("invalidated")
        context = self.context
        if context is None:
            prior_close = dec(self.rows[-1][4]) if self.rows else close
            self.tr.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
            self.fast = (
                close
                if self.fast is None
                else self.fast + dec(2) / (RULES["ema_fast"] + 1) * (close - self.fast)
            )
            self.slow = (
                close
                if self.slow is None
                else self.slow + dec(2) / (RULES["ema_slow"] + 1) * (close - self.slow)
            )
        else:
            self.fast = context.engine.fast if context.engine.fast is not None else close
            self.slow = context.engine.slow if context.engine.slow is not None else close
        window = RULES["volume_baseline_bars"]
        volumes = [dec(r[6]) for r in list(self.rows)[-window:]]
        baseline = sum(volumes) / window if len(volumes) == window else None
        self.rows.append(row)
        self.count += 1
        if day != self.day:
            self.day, self.daily = day, WeightedPrices()
        self.daily.add(row)
        vwap, sigma = self.daily.bands()
        atr = context.atr if context else sum(self.tr) / len(self.tr)
        pivot = RULES["pivot_left_right"]
        recent = list(self.rows)[-(2 * pivot + 1) :]
        new_anchor = False
        if context:
            self.low, self.swing = context.engine.low, context.engine.swing
            if context.engine.anchor_known != self.seen_anchor_known:
                self.seen_anchor_known = context.engine.anchor_known
                if not (self.variant == "vwap-anchored" and self.pending):
                    self.anchor, self.anchor_known = (
                        context.engine.anchor,
                        context.engine.anchor_known,
                    )
                    self.anchored = WeightedPrices()
                    if self.anchor is not None:
                        for r in self.rows:
                            if r[0] >= self.anchor:
                                self.anchored.add(r)
                        new_anchor = True
        elif len(recent) == 2 * pivot + 1 and all(
            b[0] - a[0] == step for a, b in zip(recent, recent[1:], strict=False)
        ):
            center, others = recent[pivot], recent[:pivot] + recent[pivot + 1 :]
            if all(dec(center[3]) < dec(r[3]) for r in others):
                self.low = (center[0], dec(center[3]), end)
                if not (self.variant == "vwap-anchored" and self.pending):
                    self.anchor, self.anchor_known, self.anchored = center[0], end, WeightedPrices()
                    for r in recent[pivot:]:
                        self.anchored.add(r)
                    new_anchor = True
            if (
                self.low
                and center[0] > self.low[0]
                and all(dec(center[2]) > dec(r[2]) for r in others)
            ):
                self.swing = (*self.low, center[0], dec(center[2]), end)
        if self.anchor is not None and not new_anchor:
            self.anchored.add(row)
        avwap, _ = self.anchored.bands()
        current = {
            "ts": row[0],
            "end": end,
            "day": day,
            "close": close,
            "low": low,
            "high": high,
            "vwap": vwap,
            "sigma": sigma,
            "avwap": avwap,
            "anchor": self.anchor,
            "slow": self.slow,
        }
        checks = {
            "warmup_observed_bars": self.count >= RULES["warmup_bars"],
            "adjacent_closed_bars": contiguous,
            "positive_atr": atr > 0,
        }
        if context:
            checks.update(
                structure_warmup_observed_bars=context.engine.count >= RULES["warmup_bars"],
                structure_adjacent_closed_bars=context.contiguous and context.fresh,
            )
        same_day = bool(old and old["day"] == day and old["vwap"] is not None and vwap is not None)
        if self.variant in DAILY_VARIANTS:
            checks.update(
                same_UTC_session=same_day,
                vwap_session_warmup=self.daily.count >= RULES["vwap_min_session_bars"]
                and vwap is not None,
            )
        ready = all(checks.values())
        bullish = close > opening
        tolerance, buffer = atr * dec(RULES["touch_atr"]), atr * dec(RULES["stop_buffer_atr"])
        slope = (
            context.slope if context else (self.slow - old["slow"]) / atr if old and atr else ZERO
        )
        threshold = dec(RULES["regime_slope_atr"])
        regime = (
            "falling"
            if self.fast < self.slow and slope < -threshold
            else "rising"
            if self.fast > self.slow and slope > threshold
            else "mixed"
        )
        wick = (min(opening, close) - low) / (high - low) if high > low else ZERO
        facts = {
            "signal_close": str(close),
            "atr": str(atr),
            "ema20": str(self.fast),
            "ema50": str(self.slow),
            "regime": regime,
            "vwap": str(vwap) if vwap is not None else None,
            "sigma": str(sigma) if sigma is not None else None,
            "prior_volume_mean": str(baseline) if baseline is not None else None,
        }
        if context:
            facts.update(
                structure_minutes=context.minutes,
                confirmation_minutes=self.minutes,
                context_closed_at=context.closed_at,
                context_observed_bars=context.engine.count,
                context_prior_slow=str(context.prior_slow)
                if context.prior_slow is not None
                else None,
                context_slow_nonfalling=context.nonfalling,
            )
        nonfalling = context.nonfalling if context else bool(old and self.slow >= old["slow"])
        setup = None
        if ready and self.pending:
            p = self.pending
            higher_low = low > p["low"] and close > old["close"]
            micro_break = close > p["reaction_high"]
            confirm = False
            if self.variant == "vwap-pullback":
                confirm = (
                    self.fast > self.slow
                    and close > vwap
                    and bullish
                    and (higher_low or micro_break)
                )
            elif self.variant == "vwap-reclaim":
                confirm = (
                    close > vwap
                    and bullish
                    and (
                        low >= vwap - tolerance or low <= vwap + tolerance and close > old["close"]
                    )
                )
            elif self.variant == "vwap-anchored":
                confirm = (
                    self.anchor == p["anchor"]
                    and avwap is not None
                    and low <= avwap + tolerance
                    and close > avwap
                    and bullish
                )
            elif self.variant == "vwap-reversion":
                p["rejected"] |= wick >= dec(RULES["rejection_lower_wick"])
                confirm = bool(
                    sigma
                    and p["rejected"]
                    and higher_low
                    and bullish
                    and vwap - 2 * sigma <= close < vwap
                )
            elif self.variant == "fib-confluence":
                confirm = bullish and close > vwap and (higher_low or micro_break)
            elif self.variant == "fib-ema":
                confirm = (
                    self.fast > self.slow
                    and nonfalling
                    and close >= self.fast
                    and bullish
                    and micro_break
                )
            elif self.variant == "fib-extension":
                confirm = (
                    low >= p["level"] - tolerance
                    and close >= p["level"] + atr * dec(RULES["breakout_acceptance_atr"])
                    and bullish
                )
            elif self.variant == "fib-sweep":
                confirm = close > p["level"] and bullish and (higher_low or micro_break)
            if context and self.variant == "fib-ema":
                confirm = self.fast > self.slow and nonfalling and bullish and micro_break
            if context and self.variant == "fib-extension":
                confirm = low >= p["level"] - tolerance and close >= p["level"] and bullish
            stop = min(p["stop"], close - atr)
            target = vwap if self.variant == "vwap-reversion" else p["target"]
            if target is None:
                target = close + dec(RULES["default_target_r"]) * (close - stop)
            checks.update(
                pending_setup=True,
                closed_bar_confirmation=bool(confirm),
                valid_long_price_plan=bool(ZERO < stop < close < target),
            )
            if confirm and checks["valid_long_price_plan"]:
                candidate_facts = {
                    **p["facts"],
                    **facts,
                    "arming_facts": p["facts"],
                    "armed_at": p["armed_at"],
                    "candidate_id": p["id"],
                    "confirmed_at": end,
                }
                setup = Setup(self.variant, end, close, stop, target, candidate_facts)
                self.events["confirmed"] += 1
                self.pending = None
            elif p["target"] is not None and close >= p["target"]:
                self.cancel("target_passed")
            else:
                p["reaction_high"] = min(p["reaction_high"], high)
        if ready and self.pending is None and setup is None:
            trigger, stop, target, details = False, low - buffer, None, {}
            if self.variant == "vwap-pullback":
                trigger = (
                    self.fast > self.slow
                    and vwap >= old["vwap"]
                    and old["close"] > old["vwap"]
                    and low <= vwap + tolerance
                    and high >= vwap - tolerance
                )
            elif self.variant == "vwap-reclaim":
                trigger = bool(
                    old["close"] <= old["vwap"]
                    and close > vwap
                    and baseline
                    and dec(row[6]) >= baseline * dec(RULES["reclaim_volume_multiple"])
                )
            elif self.variant == "vwap-anchored":
                trigger = bool(
                    avwap is not None
                    and old["anchor"] == self.anchor
                    and old["avwap"] is not None
                    and old["close"] <= old["avwap"] < close
                    and close > avwap
                )
                details = {
                    "anchor": self.anchor,
                    "anchor_at": self.anchor,
                    "anchor_known_at": self.anchor_known,
                }
            elif self.variant == "vwap-reversion":
                trigger = bool(sigma and low < vwap - 2 * sigma and close < vwap)
            else:
                s = self.swing
                valid = bool(
                    s
                    and end - s[5]
                    <= RULES["swing_max_age_bars"] * (context.minutes * 60 if context else step)
                    and s[4] - s[1] >= atr * dec(RULES["minimum_swing_atr"])
                )
                checks["confirmed_meaningful_upswing"] = valid
                if valid:
                    low_at, bottom, low_known, high_at, top, high_known = s
                    width = top - bottom
                    lower, upper = top - width * dec(".618"), top - width * dec(".382")
                    details = {
                        "swing_low_at": low_at,
                        "swing_high_at": high_at,
                        "swing_low": str(bottom),
                        "swing_high": str(top),
                        "swing_known_at": max(low_known, high_known),
                        "fib_382": str(upper),
                        "fib_618": str(lower),
                    }
                    if context:
                        details.update(
                            impulse_policy="causal-parent-retention-v1",
                            impulse_original_expiry=high_known
                            + RULES["swing_max_age_bars"] * context.minutes * 60,
                        )
                    stop, target = top - width * dec(".786") - buffer, top
                    touch = low <= upper and high >= lower
                    if self.variant == "fib-confluence":
                        trigger = touch and lower - atr * dec(
                            RULES["confluence_atr"]
                        ) <= vwap <= upper + atr * dec(RULES["confluence_atr"])
                    elif self.variant == "fib-ema":
                        trigger = touch and self.fast > self.slow and nonfalling
                    elif self.variant == "fib-extension":
                        trigger = bool(
                            old["close"] <= top < close
                            and baseline
                            and dec(row[6]) >= baseline * dec(RULES["reclaim_volume_multiple"])
                        )
                        stop, target = (
                            top - width * dec(".236") - buffer,
                            bottom + width * dec("1.618"),
                        )
                    elif self.variant == "fib-sweep":
                        trigger, stop = low < bottom < close and bullish, low - buffer
                    details["level"] = bottom if self.variant == "fib-sweep" else top
            if trigger and ZERO < stop < close:
                lifetime = (
                    RULES["retest_max_bars"]
                    if self.variant == "vwap-anchored"
                    else RULES["setup_max_bars"]
                )
                self.pending = {
                    "id": f"{self.variant}:{end}",
                    "armed_at": end,
                    "expires": end + lifetime * step,
                    "day": day,
                    "low": low,
                    "reaction_high": high,
                    "stop": stop,
                    "target": target,
                    "rejected": wick >= dec(RULES["rejection_lower_wick"]),
                    "facts": {
                        k: str(v) if isinstance(v, Decimal) else v
                        for k, v in {**facts, **details}.items()
                    },
                    **details,
                }
                self.events["armed"] += 1
        if (
            context
            and ready
            and self.variant == "fib-extension"
            and setup is None
            and self.pending is not None
            and self.pending["armed_at"] == end
        ):
            p = self.pending
            stop, target = min(p["stop"], close - atr), p["target"]
            if ZERO < stop < close < target:
                candidate_facts = {
                    **p["facts"],
                    **facts,
                    "arming_facts": p["facts"],
                    "armed_at": p["armed_at"],
                    "candidate_id": p["id"],
                    "confirmed_at": end,
                }
                setup = Setup(self.variant, end, close, stop, target, candidate_facts)
                self.events["confirmed"] += 1
                self.pending = None
                checks.update(
                    pending_setup=True, closed_bar_confirmation=True, valid_long_price_plan=True
                )
        checks.setdefault("pending_setup", self.pending is not None)
        checks.setdefault("closed_bar_confirmation", setup is not None)
        self.previous = current
        return checks, setup
