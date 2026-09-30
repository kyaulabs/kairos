"""One active exchange with isolated stores; switching is a paused, flat operation."""

import asyncio
import contextlib

from kairos.alpaca_markets import AlpacaMarkets
from kairos.domain import SafetyError, dec
from kairos.retail import RetailMarkets


def require_flat(engine):
    if (
        engine.running
        or engine.orders(active=True)
        or engine.recovery_required
        or engine.store.get("cycle")
    ):
        raise SafetyError("Stop and reconcile the current exchange before switching")
    for mode in ("dry-run", "trading", "paper"):
        ledger = engine.ledger(mode)
        if ledger and any(
            dec(q) for a, q in ledger["balances"].items() if a != engine.settings["quote"]
        ):
            raise SafetyError(
                "Exchange switching would leave holdings without active protection; close them first"
            )
        futures = engine.futures.ledger(mode)
        if futures and any(dec(p["quantity"]) for p in futures["positions"].values()):
            raise SafetyError("Close Futures positions before switching exchanges")
    margin = engine.store.get("margin") or {}
    if any(dec(p["quantity"]) for p in margin.get("positions", {}).values()):
        raise SafetyError("Close margin positions before switching exchanges")


class ExchangeDesk:
    def __init__(self, app, store, factories, *, default="kraken"):
        self.app, self.store, self.factories = app, store, factories
        self.engines = {}
        self.active = store.get("active-exchange", default)
        if self.active not in factories:
            raise SafetyError("Unknown saved exchange")
        self.lock = asyncio.Lock()

    def engine(self, name):
        if name not in self.engines:

            def publish(kind, data):
                if name == self.active:
                    self.app["hub"].publish(kind, data)

            self.engines[name] = self.factories[name](publish)
        return self.engines[name]

    def retail(self, engine):
        return (
            AlpacaMarkets(engine.kraken)
            if getattr(engine.kraken, "hosted_paper", False) is True
            else RetailMarkets(engine.kraken, engine.futures.client)
        )

    async def initialize(self):
        engine = self.engine(self.active)
        self.app["engine"], self.app["retail"] = engine, self.retail(engine)
        await engine.initialize()
        engine.task = asyncio.create_task(engine.run())

    async def switch(self, name, confirmation):
        if name not in self.factories or confirmation != "SWITCH EXCHANGE":
            raise SafetyError("Choose a supported exchange and confirm SWITCH EXCHANGE")
        async with self.lock:
            old = self.app["engine"]
            if name == self.active:
                return old
            async with old.lock:
                require_flat(old)
                candidate = self.engine(name)
                if not candidate.ready:
                    await candidate.initialize()
                async with (
                    self.app["market_lock"],
                    self.app["candle_lock"],
                    self.app["account_lock"],
                ):
                    self.store.put("active-exchange", name)
                    old.shutting_down = True
                    old.htf_review.cancel()
                    if old.task:
                        old.task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await old.task
                        old.task = None
                    await old.kraken.market_data.close()
                    self.active = name
                    self.app["engine"], self.app["retail"] = candidate, self.retail(candidate)
                    candidate.shutting_down = False
                    candidate.mode = "paper" if name == "alpaca" else "dry-run"
                    if name == "alpaca":
                        candidate.paper_armed = False
                    candidate.running = False
                    for key in (
                        "market_cache",
                        "market_change_cache",
                        "spot_quote_cache",
                        "candle_cache",
                        "account_cache",
                    ):
                        self.app[key].clear()
                    self.app["hub"].tickers.clear()
                    self.app["feed_restart"].set()
                    candidate.event(
                        "system",
                        {
                            "message": "Exchange changed; selected engine remains paused and separately accounted",
                            "exchange": name,
                        },
                    )
                    candidate.emit_state()
                    candidate.task = asyncio.create_task(candidate.run())
            return candidate

    async def close(self):
        for engine in self.engines.values():
            await engine.close()
            await engine.kraken.market_data.close()
