"""Display-only public metadata. Never used to identify or price execution markets."""

import asyncio
import json
import math
import sqlite3
import time

import aiohttp

SOURCE = "https://api.coingecko.com/api/v3/coins/list"
# Tickers are not unique in public catalogs. Pin known native assets to provider IDs,
# not the first same-symbol token, a wrapped token, or an unrelated namesake.
CANONICAL = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "AVAX": "avalanche-2",
    "BNB": "binancecoin",
    "USDT": "tether",
    "USDC": "usd-coin",
}


def names_from(rows):
    if not isinstance(rows, list) or not rows or len(rows) > 100_000:
        raise ValueError("Invalid asset-name catalog")
    candidates = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid asset-name row")
        symbol, name, identifier = row.get("symbol"), row.get("name"), row.get("id")
        if not all(isinstance(value, str) for value in (symbol, name, identifier)):
            raise ValueError("Invalid asset-name fields")
        if not all((symbol, name, identifier)):
            continue  # The public catalog includes assets without a usable ticker or name.
        symbol = symbol.upper()
        if (
            len(symbol) <= 24
            and symbol.isascii()
            and symbol.isalnum()
            and len(name) <= 120
            and all(ord(c) >= 32 for c in name)
        ):
            candidates.setdefault(symbol, {})[identifier] = name
    result = {}
    for symbol, options in candidates.items():
        if symbol in CANONICAL:
            if CANONICAL[symbol] in options:
                result[symbol] = options[CANONICAL[symbol]]
        elif len(options) == 1:
            result[symbol] = next(iter(options.values()))
    if "BTC" in result:
        result["XBT"] = result["BTC"]
    if "DOGE" in result:
        result["XDG"] = result["DOGE"]
    return result


class AssetNames:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock
        try:
            self.names, self.fetched = store.read_names()
            if (
                not isinstance(self.names, dict)
                or type(self.fetched) not in (int, float)
                or not math.isfinite(self.fetched)
            ):
                raise ValueError("Invalid cached asset names")
        except (ValueError, TypeError):
            self.names, self.fetched = {}, 0
        self.next_attempt = 0
        self.lock = asyncio.Lock()

    async def snapshot(self):
        async with self.lock:
            now = self.clock()
            if (
                not self.fetched or not 0 <= now - self.fetched < 86400
            ) and now >= self.next_attempt:
                self.next_attempt = now + 3600  # Failure never causes a request per browser poll.
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=6)
                    ) as session:
                        async with session.get(SOURCE, allow_redirects=False) as response:
                            response.raise_for_status()
                            body = bytearray()
                            async for chunk in response.content.iter_chunked(65536):
                                body.extend(chunk)
                                if len(body) > 8 * 1024 * 1024:
                                    raise ValueError("Asset-name catalog too large")
                    names = names_from(json.loads(body))
                    if not names:
                        raise ValueError("No usable asset names in catalog")
                    self.store.save_names(names, now)
                    self.names, self.fetched = names, now
                except (aiohttp.ClientError, TimeoutError, ValueError, sqlite3.Error, OSError):
                    pass  # Retain last known names, or display native tickers on a cold failure.
            return {"names": self.names, "source": SOURCE, "fetched_at": self.fetched or None}
