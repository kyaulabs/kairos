"""Read-only OKX views, isolated from execution selection and other venue feeds."""

import time

from kairos.domain import SafetyError

ACCOUNT_SOURCES = {"okx-account": "OKX trading account · native assets, not bot allocation"}


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
                "execution_reason": "",
                "price_label": f"OKX {self.spot.environment} books5",
                "supported_strategies": ["twap"],
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
            "note": "Native same-environment books5 for the explicitly selected execution market only. Browsing never retargets that feed. Historical candles, turnover and 24h change are unavailable; no other venue/environment supplies them.",
        }

    async def candles(self, market, minutes):
        raise SafetyError(
            "OKX historical charts are not integrated; native books5 remain the execution source; no cross-environment candle fallback"
        )

    async def account_snapshot(self, source):
        if source not in ACCOUNT_SOURCES:
            raise SafetyError("Unknown OKX account view")
        proof = await self.engine.read_account()
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
