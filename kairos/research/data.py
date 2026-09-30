"""Bounded public-only acquisition; vintage metadata is not historical arrival evidence."""

import json
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from kairos.domain import SafetyError, dec
from kairos.research.artifacts import utc

SOURCE = "https://data.alpaca.markets/v1beta3/crypto/us/bars"


@dataclass(frozen=True)
class Bar:
    start: int
    end: int
    available: int
    open: str
    high: str
    low: str
    close: str
    volume: str

    def validate(self):
        if any(type(x) is not int for x in (self.start, self.end, self.available)):
            raise SafetyError("Bar timestamps must be integer UTC seconds")
        if self.end - self.start != 3600 or self.start % 3600 or self.available < self.end:
            raise SafetyError("Invalid bar completion/availability times")
        o, h, lo, c, v = map(dec, (self.open, self.high, self.low, self.close, self.volume))
        if not 0 < lo <= min(o, c) <= max(o, c) <= h or v < 0:
            raise SafetyError("Invalid OHLCV")
        return self


def validate_bars(rows):
    bars = tuple(Bar(**row).validate() for row in rows)
    if any(a.start >= b.start for a, b in zip(bars[:-1], bars[1:], strict=True)):
        raise SafetyError("Duplicate or unordered bars; do not silently sort or forward fill")
    return bars


def available_history(bars, decision_at, count):
    """Return only a contiguous, completed information set, never a forming/late bar."""
    history = [b for b in bars if b.available <= decision_at and b.end <= decision_at][-count:]
    if len(history) != count or any(
        a.end != b.start for a, b in zip(history[:-1], history[1:], strict=True)
    ):
        return ()
    if decision_at - history[-1].available >= 3600:
        return ()
    return tuple(history)


def purged_labels(samples, start, end, embargo_seconds):
    """Samples are (decision_time, label_available_time, features, label)."""
    if start >= end or embargo_seconds < 0:
        raise SafetyError("Invalid chronological boundary")
    return [s for s in samples if start <= s[0] and s[0] <= s[1] < end - embargo_seconds]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SafetyError("Research data redirects are disabled")


def public_get(params):
    # Fixed public host, no headers/credentials/environment loading, no broker write API.
    request = urllib.request.Request(SOURCE + "?" + urllib.parse.urlencode(params))
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        if response.url.split("?")[0] != SOURCE:
            raise SafetyError("Unexpected research data redirect")
        return json.load(response)


def collect(registry, plan, get=public_get, clock=time.time):
    spec = {"operation": "collect", "plan": plan}
    with registry.trial(spec) as trial:
        if plan["source"] != SOURCE or plan["universe"] != ["BTC/USD", "ETH/USD"]:
            raise SafetyError("This collector only supports the preregistered public BTC/ETH study")
        start, end = utc(plan["data_start"]), utc(plan["final_end"])
        if start >= end or end > clock() or end - start > 4 * 366 * 86400:
            raise SafetyError("Research history must be completed and bounded to four years")
        params = {
            "symbols": ",".join(plan["universe"]),
            "timeframe": "1Hour",
            "start": plan["data_start"],
            "end": end - 1,
            "limit": 10000,
            "sort": "asc",
        }
        # Alpaca accepts RFC3339 bounds; never reinterpret a local timezone.
        params["end"] = datetime.fromtimestamp(end - 1, UTC).isoformat()
        rows = {s: [] for s in plan["universe"]}
        pages, tokens = [], set()
        for _ in range(24):
            data = get(params)
            received = clock()
            pages.append(
                registry.record(
                    trial,
                    "download_page",
                    {
                        "source": SOURCE,
                        "params": dict(params),
                        "retrieved_at": received,
                        "body": data,
                    },
                )
            )
            if set(data["bars"]) - set(rows):
                raise SafetyError("Unexpected market in public response")
            for symbol, values in data["bars"].items():
                for value in values:
                    ts = utc(value["t"])
                    if not start <= ts < end:
                        raise SafetyError("Out-of-window bar")
                    rows[symbol].append(
                        asdict(
                            Bar(
                                ts,
                                ts + 3600,
                                ts + 3600 + plan["publication_delay_seconds"],
                                *(str(dec(value[k])) for k in ("o", "h", "l", "c", "v")),
                            ).validate()
                        )
                    )
            token = data.get("next_page_token")
            if not token:
                break
            if token in tokens:
                raise SafetyError("Repeated pagination token")
            tokens.add(token)
            params["page_token"] = token
            time.sleep(0.4)
        else:
            raise SafetyError("Public history incomplete at page bound")
        boundaries = [
            ("development", start, utc(plan["final_start"])),
            ("final", utc(plan["final_start"]), end),
        ]
        partitions, quality = {}, {}
        for symbol, values in rows.items():
            bars = validate_bars(values)
            partitions[symbol], quality[symbol] = {}, {}
            for name, lo, hi in boundaries:
                subset = [asdict(b) for b in bars if lo <= b.start < hi]
                partitions[symbol][name] = registry.artifacts.put(subset)
                quality[symbol][name] = {
                    "rows": len(subset),
                    "expected": (hi - lo) // 3600,
                    "coverage": len(subset) / ((hi - lo) // 3600),
                }
        result = {
            "schema": 1,
            "source": SOURCE,
            "feed": "Alpaca US crypto",
            "availability": plan["availability"],
            "historical_revisions_known": False,
            "execution_quotes_available": False,
            "pages": pages,
            "partitions": partitions,
            "quality": quality,
            "plan": registry.artifacts.put(plan),
        }
        return registry.record(trial, "dataset", result)
