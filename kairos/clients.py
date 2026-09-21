import asyncio
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import aiohttp

from kairos.domain import Book, Pair, SafetyError, dec


class ExchangeRejected(SafetyError):
    pass


def signature(path, payload, secret):
    post = urlencode(payload)
    digest = hashlib.sha256((str(payload["nonce"]) + post).encode()).digest()
    return base64.b64encode(
        hmac.new(base64.b64decode(secret), path.encode() + digest, hashlib.sha512).digest()
    ).decode()


class Kraken:
    def __init__(self, session, store, key="", secret="", allow_live=False):
        self.session, self.store = session, store
        self.key, self.secret, self.allow_live = key, secret, allow_live
        self.lock = asyncio.Lock()
        self.last_request = 0
        self.pairs = {}

    async def request(self, method, params=None, private=False):
        """No automatic retries: a timed-out write may already have reached Kraken."""
        if private and method not in {
            "BalanceEx",
            "TradeVolume",
            "QueryOrders",
            "OpenOrders",
            "ClosedOrders",
        }:
            if method not in {"AddOrder", "CancelOrder"} or not self.allow_live:
                raise SafetyError("Exchange write is not authorized")
        params = dict(params or {})
        headers = {}
        path = f"/0/{'private' if private else 'public'}/{method}"
        # Serialize authenticated nonces and pace REST calls conservatively.
        async with self.lock:
            await asyncio.sleep(max(0, 0.8 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            if private:
                if not self.key or not self.secret:
                    raise SafetyError("Kraken credentials are not configured")
                nonce = max(time.time_ns() // 1000, self.store.get("nonce", 0) + 1)
                self.store.put("nonce", nonce)
                params["nonce"] = nonce
                headers = {"API-Key": self.key, "API-Sign": signature(path, params, self.secret)}
            try:
                async with self.session.request(
                    "POST" if private else "GET",
                    "https://api.kraken.com" + path,
                    data=params if private else None,
                    params=None if private else params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as response:
                    if response.status != 200:
                        raise SafetyError(
                            f"Kraken HTTP {response.status}; request outcome may be unknown"
                        )
                    result = await response.json()
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                raise SafetyError(
                    "Kraken transport failure; request outcome may be unknown"
                ) from exc
            if result.get("error"):
                # Never echo a remote response body or credentials to logs/browser.
                known = [
                    e.split(":", 1)[-1]
                    for e in result["error"]
                    if e
                    in {
                        "EAPI:Invalid key",
                        "EAPI:Invalid signature",
                        "EAPI:Invalid nonce",
                        "EGeneral:Permission denied",
                        "EOrder:Insufficient funds",
                        "EOrder:Order minimum not met",
                        "EOrder:Cost minimum not met",
                        "EOrder:Rate limit exceeded",
                        "EAPI:Rate limit exceeded",
                        "EOrder:Unknown order",
                        "EOrder:Post only order",
                    }
                ]
                raise ExchangeRejected(
                    "Kraken rejected request: " + (", ".join(known) or "API error")
                )
            return result["result"]

    async def catalog(self):
        data = await self.request("AssetPairs")
        self.pairs = {
            key: Pair.parse(key, value)
            for key, value in data.items()
            if value.get("wsname")
            and value.get("status") == "online"
            and value.get("aclass_base", "currency") == "currency"
            and value.get("asset_class", "crypto") == "crypto"
            and not key.endswith(".d")
        }
        return self.pairs

    async def book(self, pair):
        result = await self.request("Depth", {"pair": pair.id, "count": 100})
        data = next(iter(result.values()))
        book = Book(
            pair,
            [[dec(p), dec(v)] for p, v, *_ in data["bids"]],
            [[dec(p), dec(v)] for p, v, *_ in data["asks"]],
            time.time(),
        )
        if not book.bids or not book.asks:
            raise SafetyError("Empty order book")
        book.fresh(10)
        return book

    async def candles(self, pair, minutes):
        result = await self.request("OHLC", {"pair": pair.id, "interval": minutes})
        rows = next(v for k, v in result.items() if k != "last")
        # Kraken always includes the still-forming candle as the final row.
        return rows[:-1]

    async def marks(self, pairs):
        if not pairs:
            return {}
        result = await self.request("Ticker", {"pair": ",".join(p.id for p in pairs)})
        return {key: dec(value["b"][0]) for key, value in result.items()}

    async def balances(self):
        result = await self.request("BalanceEx", private=True)
        return {
            asset: max(dec(0), dec(row["balance"]) - dec(row.get("hold_trade", 0)))
            for asset, row in result.items()
        }

    async def fees(self, pairs):
        result = await self.request("TradeVolume", {"pair": ",".join(p.id for p in pairs)}, True)
        taker = {key: dec(row["fee"]) * 100 for key, row in result.get("fees", {}).items()}
        maker = {key: dec(row["fee"]) * 100 for key, row in result.get("fees_maker", {}).items()}
        if any(p.id not in taker for p in pairs):
            raise SafetyError("Cannot verify fee tier for every active pair")
        return maker, taker

    async def query(self, txid):
        result = await self.request("QueryOrders", {"txid": txid}, True)
        if txid not in result:
            raise SafetyError("Tracked order missing from Kraken response")
        return result[txid]

    async def find_order(self, client_id, since):
        opened = await self.request("OpenOrders", private=True)
        for txid, row in opened["open"].items():
            if row.get("cl_ord_id") == client_id:
                return txid, row
        # Paginate rather than silently overlooking an order in a busy account.
        offset = 0
        while offset < 1000:
            result = await self.request(
                "ClosedOrders", {"start": int(since) - 60, "ofs": offset}, True
            )
            for txid, row in result["closed"].items():
                if row.get("cl_ord_id") == client_id:
                    return txid, row
            offset += len(result["closed"])
            if offset >= result["count"] or not result["closed"]:
                break
        raise SafetyError(
            "Uncertain order not found. Keep stopped; reconcile its client ID with Kraken"
        )

    async def add(self, params):
        if not self.allow_live:
            raise SafetyError("Live exchange writes are disabled by ALLOW_LIVE_TRADING")
        return await self.request("AddOrder", params, True)

    async def cancel(self, txid):
        if not self.allow_live:
            raise SafetyError("Live exchange writes are disabled by ALLOW_LIVE_TRADING")
        return await self.request("CancelOrder", {"txid": txid}, True)

    async def trades(self, pair, since):
        result = await self.request("Trades", {"pair": pair.id, "since": since})
        rows = next(v for k, v in result.items() if k != "last")
        return rows, str(result["last"])


class Jev:
    def __init__(self, session, key, model="jev-latest"):
        self.session, self.key, self.model = session, key, model

    async def decide(self, state):
        if not self.key:
            raise SafetyError("JEV_API_KEY is not configured")
        started = time.monotonic()
        body = {
            "model": self.model,
            "state": state,
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": (
                        "Assess the supplied spot strategy state. Choose buy, sell, or hold. "
                        "Use the computed trend and liquidity descriptions; do not calculate sizing "
                        "or invent unseen data. In spot mode there is no leverage or short selling. "
                        "In paper margin mode buy opens/increases long or reduces short; sell "
                        "opens/increases short or reduces long. Fees and hard risk "
                        "checks are enforced by code. For arbitrage, buy means permit the computed "
                        "cycle, hold means abstain, and sell is not applicable. The reported "
                        "probabilities are assessments, not guaranteed future returns."
                    ),
                    "criteria": {
                        "buy": "Conditions support the proposed long entry, bid quote, or positive-net arbitrage cycle.",
                        "sell": "Conditions support reducing existing spot inventory or offering an ask quote.",
                        "hold": "Conditions are unclear, costs dominate, or no permitted action is justified.",
                    },
                }
            },
        }
        try:
            async with self.session.post(
                "https://api.typesafe.ai/v1/systemone",
                json=body,
                headers={"Authorization": "Bearer " + self.key},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as response:
                if response.status != 200:
                    raise SafetyError(f"Jev HTTP {response.status}; no trade")
                result = await response.json()
            answer = result["answers"]["action"]
            probabilities = answer["probabilities"]
            if (
                answer["type"] != "choice"
                or answer["choice"] not in {"buy", "sell", "hold"}
                or set(probabilities) != {"buy", "sell", "hold"}
                or not all(0 <= dec(p) <= 1 for p in probabilities.values())
                or abs(sum(dec(p) for p in probabilities.values()) - 1) > dec("0.02")
                or not 0 <= dec(answer["confidence"]) <= 1
            ):
                raise ValueError("Invalid decision")
            return {
                "action": answer["choice"],
                "confidence": float(answer["confidence"]),
                "probabilities": probabilities,
                "model": str(result["model"]),
                "latency_ms": round((time.monotonic() - started) * 1000),
                "usage": result.get("usage", {}),
            }
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            raise SafetyError("Jev response failed validation or timed out; no trade") from exc


async def ticker_feed(session, symbols, callback):
    """Public WS v2 stream; display only. Execution independently fetches fresh depth."""
    backoff = 1
    while True:
        try:
            async with session.ws_connect(
                "wss://ws.kraken.com/v2", heartbeat=20, receive_timeout=40
            ) as ws:
                await ws.send_json(
                    {
                        "method": "subscribe",
                        "params": {"channel": "ticker", "symbol": symbols, "event_trigger": "bbo"},
                    }
                )
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(message.data)
                        if data.get("success") is False:
                            raise SafetyError("Kraken ticker subscription rejected")
                        if data.get("channel") == "ticker":
                            backoff = 1
                            for row in data["data"]:
                                callback(
                                    "ticker",
                                    {
                                        "symbol": row["symbol"],
                                        "bid": row["bid"],
                                        "ask": row["ask"],
                                        "last": row.get("last"),
                                        "received": time.time(),
                                    },
                                )
                    elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break
        except (aiohttp.ClientError, TimeoutError, SafetyError, ValueError, KeyError):
            pass
        callback("feed", {"status": "disconnected", "retry_seconds": backoff})
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)
