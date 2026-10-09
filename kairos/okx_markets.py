"""Read-only OKX views, isolated from execution selection and other venue feeds."""

import time

from kairos.domain import SafetyError, dec
from kairos.settings import STRATEGIES

ACCOUNT_SOURCES = {
    "okx-account": "OKX trading account · native assets, not bot allocation",
    "okx-bills": "OKX transaction bills · read-only evidence since allocation",
}
BILL_COLUMNS = {
    "ccy": "Currency",
    "balChg": "Balance change (balChg)",
    "bal": "Balance after (bal)",
    "fee": "Signed fee (native)",
    "billId": "Bill ID",
    "ordId": "Order ID",
    "tradeId": "Trade ID",
    "instType": "Product",
    "instId": "Instrument",
    "type": "Type",
    "subType": "Subtype",
    "sz": "Size (native)",
    "px": "Price",
    "ts": "Balance update (ms)",
    "fillTime": "Fill time (ms)",
}


class OKXMarkets:
    def __init__(self, engine):
        self.engine, self.spot = engine, engine.client

    async def catalog(self):
        return {
            key: {
                **pair.public(),
                "name": f"{self.spot.instruments[key]['instId']} · settle in {pair.quote}",
                "kind": "spot",
                "venue": self.spot.exchange,
                "margin": False,
                "volume_unit": pair.quote,
                "chart_volume_unit": "base asset",
                "candle_source": f"OKX U.S. {self.spot.environment} {self.spot.instruments[key]['instId']} native OHLCV · chart only",
                "execution_reason": "",
                "price_label": f"OKX {self.spot.environment} books5",
                "supported_strategies": list(STRATEGIES)
                if self.spot.environment == "demo"
                and pair.quote == self.spot.instruments[key]["quoteCcy"]
                else ["twap"],
            }
            for key, pair in self.spot.pairs.items()
        }

    async def market_snapshot(self, wanted, selected, volume_ids=frozenset()):
        instruments = await self.catalog()
        if wanted is not None and not wanted <= instruments.keys():
            raise SafetyError("Unknown OKX account-enabled market in quote selection")
        rows = []
        for identifier, market in instruments.items():
            if wanted is not None and identifier not in wanted:
                continue
            book = self.spot.market_data.books.get(identifier)
            quote = {}
            if book:
                try:
                    book.fresh(self.spot.market_data.book_age)
                    quote = {
                        "bid": str(book.bids[0][0]),
                        "ask": str(book.asks[0][0]),
                        "last": str(book.mid),
                        "received": book.received,
                    }
                except SafetyError:
                    pass
            rows.append(
                {
                    **market,
                    **quote,
                    "change_pct": None,
                    "change_received": None,
                    "volume": None,
                    "volume_received": None,
                }
            )
        return {
            "markets": rows,
            "received": max((r.get("received", 0) for r in rows), default=0),
            "change_received": None,
            "errors": [],
            "partial": wanted is not None,
            "note": "Native same-environment books5 for explicitly selected execution markets only. Browsing never retargets that feed. Charts use same-environment native REST candles, not execution prices. Turnover and 24h change are unavailable; no other venue/environment supplies them.",
        }

    async def candles(self, market, minutes):
        bars = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1H", 240: "4H", 1440: "1Dutc"}
        if minutes not in bars:
            raise SafetyError("Unsupported OKX chart interval")
        pair = self.spot.resolve(market["id"])
        rows = await self.spot.request(
            "GET",
            "/api/v5/market/candles",
            params={
                "instId": self.spot.instruments[pair.id]["instId"],
                "bar": bars[minutes],
                "limit": "300",
            },
        )
        if len(rows) > 300:
            raise SafetyError("OKX candle response exceeds the requested limit")
        candles, seen = [], set()
        for row in rows:
            if not isinstance(row, list) or len(row) != 9 or row[8] not in {"0", "1"}:
                raise SafetyError("Malformed OKX native candle")
            stamp = dec(row[0]) / 1000
            opening, high, low, close, volume = [dec(v) for v in row[1:6]]
            if (
                stamp <= 0
                or stamp > dec(time.time()) + 2
                or stamp % (minutes * 60)
                or stamp in seen
                or min(opening, high, low, close) <= 0
                or volume < 0
                or high < max(opening, close)
                or low > min(opening, close)
            ):
                raise SafetyError("Invalid or duplicate OKX native candle; no synthetic repair")
            seen.add(stamp)
            candles.append(
                {
                    "time": int(stamp),
                    **dict(
                        zip(
                            ("open", "high", "low", "close", "volume"),
                            map(str, (opening, high, low, close, volume)),
                            strict=True,
                        )
                    ),
                    "complete": row[8] == "1",
                }
            )
        return sorted(candles, key=lambda row: row["time"])

    async def account_snapshot(self, source):
        if source not in ACCOUNT_SOURCES:
            raise SafetyError("Unknown OKX account view")
        binding = self.engine.store.get("okx-account") if source == "okx-bills" else None
        if source == "okx-bills" and binding is None:
            raise SafetyError(
                "OKX bill evidence requires an existing allocation; this view never binds or funds an account"
            )
        proof = await self.engine.read_account()
        if source == "okx-bills":
            owned = {o["txid"]: o for o in self.engine.orders() if o.get("txid")}
            records = []
            for row in proof["bills"]:
                record = {key: row.get(key) for key in BILL_COLUMNS}
                if any(
                    value is not None and not isinstance(value, str) for value in record.values()
                ):
                    raise SafetyError(
                        "OKX bill fields must retain their native string representation; evidence not interpreted"
                    )
                order = owned.get(row.get("ordId"))
                classification = "Unrecognized — not adopted"
                if row.get("billId") in binding["bills"]:
                    classification = "Baseline — not bot activity"
                elif (
                    order
                    and row.get("type") == "2"
                    and row.get("instType") == "SPOT"
                    and row.get("instId") == order["instrument"]
                ):
                    classification = "Recorded order — not accounting approval"
                records.append({"classification": classification, **record})
            return {
                "source": source,
                "title": f"OKX {self.spot.environment} transaction bills since allocation · native strings; missing fields shown as —; no reconciliation approval or ledger change",
                "columns": ["Scope", *BILL_COLUMNS.values()],
                "rows": [
                    [
                        r["classification"],
                        *[r[k] if r[k] not in (None, "") else "—" for k in BILL_COLUMNS],
                    ]
                    for r in records
                ],
                "records": records,
                "bound_at": binding["bound_at"],
                "baseline": binding["baseline"],
                "reported_balance": proof["balance"],
                "truncated": False,
                "received": time.time(),
            }
        return {
            "source": source,
            "title": f"OKX {self.spot.environment} account ••••{proof['config']['uid'][-4:]} · full native assets, not bot allocation",
            "columns": ["Currency", "Cash balance", "Available", "Frozen"],
            "rows": [
                [asset, row["total"], row["available"], row["frozen"]]
                for asset, row in proof["balance"]["assets"].items()
            ],
            "truncated": False,
            "received": time.time(),
        }
