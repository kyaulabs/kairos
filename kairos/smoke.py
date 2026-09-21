"""Bounded, read-only integration checks. Never prints keys, balances, or remote bodies."""

import argparse
import asyncio
import fcntl
import os
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

from kairos.clients import Jev, Kraken, ticker_feed
from kairos.domain import SafetyError
from kairos.store import Store


async def run(with_jev):
    directory = Path(os.environ.get("DATA_DIR", "data"))
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)  # noqa: ASYNC240 -- one-shot startup
    with (directory / "engine.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SafetyError(
                "Stop Kairos before the smoke test to preserve nonce ordering"
            ) from None
        store = Store(str(directory / "kairos.sqlite3"))
        try:
            async with aiohttp.ClientSession() as session:
                client = Kraken(
                    session,
                    store,
                    os.environ.get("KRAKEN_API_KEY", ""),
                    os.environ.get("KRAKEN_PRIVATE_KEY", ""),
                    allow_live=False,
                )
                pairs = await client.catalog()
                pair = next(p for p in pairs.values() if p.symbol == "BTC/USD")
                book = await client.book(pair)
                assert book.mid > 0
                print("PASS Kraken public catalog and BTC/USD depth")
                candles = await client.candles(pair, 15)
                assert len(candles) >= 30
                print("PASS Kraken completed OHLC candles")
                received = asyncio.Event()

                def callback(kind, data):
                    if kind == "ticker":
                        received.set()

                task = asyncio.create_task(ticker_feed(session, [pair.symbol], callback))
                try:
                    async with asyncio.timeout(20):
                        await received.wait()
                    print("PASS Kraken WebSocket v2 ticker")
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                if client.key and client.secret:
                    await client.balances()
                    print("PASS Kraken authenticated read-only balances (values not displayed)")
                    await client.fees([pair])
                    print("PASS Kraken authenticated fee lookup")
                else:
                    print("SKIP authenticated Kraken checks: credentials absent")
                if with_jev:
                    model = Jev(
                        session,
                        os.environ.get("JEV_API_KEY", ""),
                        os.environ.get("JEV_MODEL", "jev-latest"),
                    )
                    await model.decide(
                        {
                            "strategy": "htf",
                            "symbol": pair.symbol,
                            "trend": "unclear",
                            "entry_eligible": False,
                            "inventory": "0",
                            "long_only": True,
                            "note": "Read-only integration test; do not trade",
                        }
                    )
                    print("PASS one real Jev assessment (no execution)")
                print("No AddOrder, validation order, CancelOrder, or funding endpoint was called.")
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jev", action="store_true", help="Make one billable Jev evaluation")
    args = parser.parse_args()
    load_dotenv(override=False)
    os.umask(0o077)
    try:
        asyncio.run(run(args.jev))
    except Exception as exc:
        print("FAIL " + (str(exc) if isinstance(exc, SafetyError) else type(exc).__name__))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
