import time
from unittest.mock import AsyncMock

from kairos.domain import Book, Pair, dec

BTC = Pair(
    "XXBTZUSD",
    "BTC/USD",
    "XXBT",
    "ZUSD",
    dec("0.1"),
    dec("0.00001"),
    dec("0.00005"),
    dec("0.5"),
    (2, 3, 4, 5),
    (2, 3, 4, 5),
)
ETH = Pair(
    "XETHZUSD",
    "ETH/USD",
    "XETH",
    "ZUSD",
    dec("0.01"),
    dec("0.0001"),
    dec("0.001"),
    dec("0.5"),
    (2, 3),
    (2, 3),
)
CROSS = Pair(
    "XETHXXBT",
    "ETH/BTC",
    "XETH",
    "XXBT",
    dec("0.00001"),
    dec("0.0001"),
    dec("0.001"),
    dec("0.00001"),
)


def book(pair=BTC, bid="9990", ask="10000", qty="10"):
    return Book(pair, [[dec(bid), dec(qty)]], [[dec(ask), dec(qty)]], time.time())


def candle_rows(minutes=1, count=120):
    end = int(time.time()) // (minutes * 60) * (minutes * 60)
    rows = []
    for i in range(count):
        opening = 10000 + i
        close = opening + i % 3 - 1
        rows.append(
            [
                end - (count - 1 - i) * minutes * 60,
                str(opening),
                str(max(opening, close) + 2),
                str(min(opening, close) - 2),
                str(close),
                str(close),
                "10",
                20,
            ]
        )
    return rows


def fake_kraken():
    class Fake:
        pass

    fake = Fake()
    fake.pairs = {p.id: p for p in (BTC, ETH, CROSS)}
    fake.allow_live = False
    fake.catalog = AsyncMock(return_value=fake.pairs)
    fake.book = AsyncMock(side_effect=lambda pair: book(pair))
    fake.marks = AsyncMock(side_effect=lambda pairs: {p.id: dec("9990") for p in pairs})
    fake.market_tickers = AsyncMock(
        return_value={
            p.id: {"bid": "9990", "ask": "10000", "last": "9995", "volume": "123.45"}
            for p in (BTC, ETH, CROSS)
        }
    )
    fake.balances = AsyncMock(
        return_value={"ZUSD": dec("10000"), "XXBT": dec("1"), "XETH": dec("10")}
    )
    fake.fees = AsyncMock(return_value=({BTC.id: dec(25)}, {BTC.id: dec(40)}))
    fake.request = AsyncMock(return_value={"status": "online"})
    fake.add = AsyncMock(return_value={"txid": ["TEST-ORDER"]})
    fake.query = AsyncMock(
        return_value={"vol_exec": "0.002", "cost": "20", "fee": "0.08", "status": "closed"}
    )
    fake.cancel = AsyncMock(return_value={"count": 1})
    fake.find_order = AsyncMock()
    fake.trades = AsyncMock(return_value=([], str(time.time_ns())))
    rows = [
        [int(time.time()) - (30 - i) * 900, "0", "0", "0", str(9000 + i * 40), "0", "10", 20]
        for i in range(30)
    ]
    fake.candles = AsyncMock(return_value=rows)
    fake.ohlc = AsyncMock(side_effect=lambda pair, minutes: candle_rows(minutes))
    return fake


def fake_jev(action="buy"):
    class Fake:
        pass

    fake = Fake()
    fake.key = "test-placeholder"
    fake.decide = AsyncMock(
        return_value={
            "action": action,
            "confidence": 0.9,
            "probabilities": {"buy": 0.95, "sell": 0.03, "hold": 0.02},
            "model": "test",
            "latency_ms": 1,
            "usage": {},
        }
    )
    return fake
