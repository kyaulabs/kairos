"""Bounded free-feed discovery and read-only Alpaca hosted account views."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from kairos.alpaca import iso, timestamp
from kairos.domain import SafetyError, dec
from kairos.programs import STRATEGIES
from kairos.retail import quote_turnover

ACCOUNT_SOURCES = {
    "alpaca-account": "Alpaca paper account",
    "alpaca-positions": "Alpaca paper positions",
    "alpaca-orders": "Alpaca paper orders",
    "alpaca-activities": "Alpaca paper activities",
}


class AlpacaMarkets:
    def __init__(self, client):
        self.spot = client
        self.quotes, self.attempts = {}, {}
        self.volumes, self.volume_attempts = {}, {}

    async def refresh_volumes(self, wanted):
        # Only an explicit browser watchlist may warm this cache. Catalog browsing
        # and generic quote requests must never infer a volume subscription.
        if not wanted <= self.spot.pairs.keys() or len(wanted) > 100:
            raise SafetyError("Select at most 100 known Alpaca volume markets per request")
        now = time.time()
        due = [
            key
            for key in sorted(wanted)
            if time.monotonic() - self.volume_attempts.get(key, -300) >= 300
        ]
        errors = []
        for equity in (False, True):
            keys = [key for key in due if self.spot.is_equity(self.spot.pairs[key]) == equity]
            if not keys:
                continue
            symbols = {self.spot.symbol(self.spot.pairs[key]): key for key in keys}
            for key in keys:
                self.volume_attempts[key] = time.monotonic()
            try:
                updates = {}
                if equity:
                    data = await self.spot.request(
                        "GET",
                        "/v2/stocks/snapshots",
                        params={"symbols": ",".join(symbols), "feed": "iex"},
                        data=True,
                        background=True,
                    )
                    for symbol, snapshot in data.items():
                        if symbol not in symbols:
                            raise SafetyError("Unexpected symbol in Alpaca volume snapshot")
                        bar = snapshot.get("dailyBar")
                        if not bar:
                            continue
                        value, stamp = quote_turnover(bar["v"], bar.get("vw")), timestamp(bar["t"])
                        if value < 0 or stamp > now:
                            raise SafetyError("Invalid Alpaca daily volume")
                        updates[symbols[symbol]] = {
                            "volume": str(value),
                            "volume_asof": stamp,
                            "volume_session": str(
                                datetime.fromtimestamp(stamp, ZoneInfo("America/New_York")).date()
                            ),
                        }
                else:
                    # 288 completed five-minute bars cover exactly 24h, excluding
                    # the forming interval. Sparse bars are valid; an empty result
                    # is unavailable, not a fabricated zero-volume observation.
                    cutoff = int(now) // 300 * 300
                    params = {
                        "symbols": ",".join(symbols),
                        "timeframe": "5Min",
                        "start": iso(cutoff - 86400),
                        "end": iso(cutoff - 1),
                        "limit": 10000,
                        "sort": "asc",
                    }
                    totals, seen, tokens = {}, set(), set()
                    for _ in range(20):
                        data = await self.spot.request(
                            "GET",
                            "/v1beta3/crypto/us/bars",
                            params=params,
                            data=True,
                            background=True,
                        )
                        for symbol, bars in data["bars"].items():
                            if symbol not in symbols:
                                raise SafetyError("Unexpected symbol in Alpaca volume bars")
                            for bar in bars:
                                stamp, value = timestamp(bar["t"]), dec(bar["v"])
                                if (
                                    not cutoff - 86400 <= stamp < cutoff
                                    or stamp % 300
                                    or value < 0
                                    or (symbol, stamp) in seen
                                ):
                                    raise SafetyError("Invalid or duplicate Alpaca volume bar")
                                seen.add((symbol, stamp))
                                totals[symbol] = totals.get(symbol, dec(0)) + quote_turnover(
                                    value, bar.get("vw")
                                )
                        token = data.get("next_page_token")
                        if not token:
                            break
                        if token in tokens:
                            raise SafetyError("Alpaca volume pagination repeated a token")
                        tokens.add(token)
                        params["page_token"] = token
                    else:
                        raise SafetyError("Alpaca volume history incomplete")
                    updates = {
                        symbols[symbol]: {"volume": str(value), "volume_asof": cutoff}
                        for symbol, value in totals.items()
                    }
                for key, value in updates.items():
                    self.volumes[key] = {**value, "volume_received": now}
                if set(updates) != set(keys):
                    errors.append("Some Alpaca volumes unavailable; cached timestamps retained")
            except (SafetyError, KeyError, TypeError, ValueError):
                errors.append("Alpaca volume unavailable; cached timestamps retained")
        return errors

    async def catalog(self):
        return {
            key: {
                **pair.public(),
                "name": self.spot.assets[key].get("name", pair.symbol),
                "kind": "equity" if self.spot.is_equity(pair) else "spot",
                "venue": "alpaca",
                "margin": False,
                "volume_unit": "USD",
                "chart_volume_unit": "shares" if self.spot.is_equity(pair) else "base asset",
                "execution_reason": "",
                "price_label": "IEX mid quote" if self.spot.is_equity(pair) else "mid quote",
                "supported_strategies": sorted(STRATEGIES)
                if self.spot.is_equity(pair)
                else ["htf", "maker", "scalp", *sorted(STRATEGIES)],
            }
            for key, pair in self.spot.pairs.items()
        }

    async def market_snapshot(self, wanted, selected, volume_ids=frozenset()):
        if volume_ids and (wanted is None or not volume_ids <= wanted):
            raise SafetyError("Volume refresh requires an explicit targeted quote selection")
        instruments = await self.catalog()
        if wanted is not None and not wanted <= instruments.keys():
            raise SafetyError("Unknown Alpaca market in quote selection")
        # Full discovery does not trigger thousands of free-plan quote requests.
        requested = (
            list(wanted)
            if wanted is not None
            else list(dict.fromkeys([selected, *list(instruments)[:60]]))
        )
        if len(requested) > 100:
            raise SafetyError("Select at most 100 Alpaca quotes per request")
        due = [key for key in requested if time.monotonic() - self.attempts.get(key, 0) >= 10]
        errors = await self.refresh_volumes(volume_ids)
        for equity in (False, True):
            keys = [key for key in due if self.spot.is_equity(self.spot.pairs[key]) == equity]
            if not keys:
                continue
            symbols = {self.spot.symbol(self.spot.pairs[key]): key for key in keys}
            params = {"symbols": ",".join(symbols)}
            if equity:
                params["feed"] = "iex"
            path = "/v2/stocks/quotes/latest" if equity else "/v1beta3/crypto/us/latest/quotes"
            for key in keys:
                self.attempts[key] = time.monotonic()
            try:
                data = await self.spot.request(
                    "GET", path, params=params, data=True, background=True
                )
                for symbol, row in data["quotes"].items():
                    if symbol not in symbols:
                        raise SafetyError("Unexpected symbol in Alpaca quotes")
                    bid, ask = dec(row["bp"]), dec(row["ap"])
                    if not 0 < bid < ask:
                        continue
                    self.quotes[symbols[symbol]] = {
                        "bid": str(bid),
                        "ask": str(ask),
                        "last": str((bid + ask) / 2),
                        "received": timestamp(row["t"]),
                    }
            except SafetyError:
                errors.append("Alpaca quotes unavailable; previous timestamps retained")
        rows = [
            {
                **row,
                **self.quotes.get(key, {}),
                **self.volumes.get(key, {}),
                "change_pct": None,
                "change_received": None,
            }
            for key, row in instruments.items()
            if wanted is None or key in wanted
        ]
        return {
            "markets": rows,
            "received": max((r.get("received", 0) for r in rows), default=0),
            "change_received": None,
            "errors": errors,
            "partial": wanted is not None,
            "note": "Free quotes: bounded subset, stocks IEX-only. Volume: favorites/chart refresh every 5m; other rows use cache only. Hover volume for feed, window and age. 24h change unavailable.",
        }

    async def candles(self, market, minutes):
        rows = await self.spot.bars(self.spot.pairs[market["id"]], minutes, background=True)
        return [
            {
                "time": r[0],
                **dict(zip(("open", "high", "low", "close"), r[1:5], strict=True)),
                "volume": r[6],
            }
            for r in rows
        ]

    async def account_snapshot(self, source):
        if source not in ACCOUNT_SOURCES:
            raise SafetyError("Unknown Alpaca account view")
        truncated = False
        if source == "alpaca-account":
            row = await self.spot.account(background=True)
            columns = ["Field", "Broker paper account value (not bot allocation)"]
            keys = (
                "id",
                "status",
                "currency",
                "cash",
                "equity",
                "non_marginable_buying_power",
                "crypto_status",
                "trading_blocked",
            )
            rows = [[k, str(row.get(k, "—"))] for k in keys]
        else:
            path = {
                "alpaca-positions": "/v2/positions",
                "alpaca-orders": "/v2/orders",
                "alpaca-activities": "/v2/account/activities",
            }[source]
            params = (
                {"status": "all", "limit": 100}
                if source == "alpaca-orders"
                else {"page_size": 100, "direction": "desc"}
                if source == "alpaca-activities"
                else None
            )
            raw = await self.spot.request("GET", path, params=params, background=True)
            columns = {
                "alpaca-positions": [
                    "symbol",
                    "qty",
                    "side",
                    "avg_entry_price",
                    "current_price",
                    "market_value",
                ],
                "alpaca-orders": [
                    "id",
                    "client_order_id",
                    "symbol",
                    "side",
                    "qty",
                    "filled_qty",
                    "status",
                ],
                "alpaca-activities": [
                    "id",
                    "activity_type",
                    "symbol",
                    "qty",
                    "price",
                    "net_amount",
                ],
            }[source]
            rows = [[str(row.get(k, "—")) for k in columns] for row in raw]
            truncated = source != "alpaca-positions" and len(raw) >= 100
        return {
            "source": source,
            "title": ACCOUNT_SOURCES[source],
            "columns": columns,
            "rows": rows,
            "truncated": truncated,
            "received": time.time(),
        }
