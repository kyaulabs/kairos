"""Finite OKX demo strategy consent. Signals never grant transport permission."""

import uuid

from kairos import htf, programs, scalping
from kairos.domain import BPS, ZERO, SafetyError, dec
from kairos.okx_engine import fingerprint


def markets(e):
    pairs = e.fee_pairs()
    for pair in pairs:
        if e.client.instruments[pair.id]["quoteCcy"] != pair.quote:
            raise SafetyError(
                "Demo strategies require native price/spending currencies; no stablecoin parity assumption"
            )
    return pairs


def prepare_position(e):
    """Quarantine pre-existing bot dust; never adopt it into a new signal lineage."""
    strategy = e.settings["strategy"]
    if strategy not in {"htf", "scalp"}:
        return
    module = htf if strategy == "htf" else scalping
    state = module.snapshot(e)
    if "excluded_inventory" not in state:
        if state.get("position") or state.get("entry_attempt"):
            raise SafetyError(
                "Existing strategy ownership needs investigation before demo authorization"
            )
        state["excluded_inventory"] = str(e.balance(e.resolve(e.settings["pair"]).base))
        e.store.put(module.key(e), state)
    module.prepare(e)
    if strategy == "htf":
        htf.arm(e)


async def preview(e, values):
    from kairos import okx_account as native
    from kairos.okx_cycle import confirmation, fees, idle

    idle(e)
    if e.client.environment != "demo":
        raise SafetyError("Strategy testing is OKX Demo only; live remains cycle/TWAP only")
    allowed = {
        "kind",
        "pair",
        "allocation",
        "budget",
        "duration_seconds",
        "max_orders",
        "buy_ceiling",
        "sell_floor",
    }
    if set(values) - allowed:
        raise SafetyError("Unknown demo strategy authorization fields")
    async with e.lock:
        idle(e)
        generation = e.stop_generation
        e.validate_capabilities(e.settings)
        if values.get("pair", e.settings["pair"]) != e.settings["pair"]:
            raise SafetyError(
                "Save the strategy market/settings before requesting its authorization"
            )
        await e.client.catalog()
        pair = e.resolve(e.settings["pair"])
        selected = markets(e)
        if pair.id not in {p.id for p in selected}:
            raise SafetyError("Include the primary market in the saved rebalance basket")
        if e.store.get("cycle"):
            raise SafetyError(
                "An incomplete arbitrage cycle remains; inspect its original legs and holdings before another run"
            )
        if pair.quote != e.settings["quote"]:
            raise SafetyError("Strategy allocation currency does not match its primary market")
        binding = e.store.get("okx-account")
        if binding and binding["quote"] != pair.quote:
            raise SafetyError("Existing allocation currency cannot be replaced or converted")
        await e.client.market_data.configure(selected, max_age=e.settings["stale_seconds"])
        await fees(e, pair, None)
        proof = await e.probe()
        if binding:
            await e.settle()
        if not proof["config"]["can_trade"] or proof["config"]["fee_type"] not in {"0", "1"}:
            raise SafetyError("Demo strategy requires Trade permission and native fee policy")
        clock = await e.client.request("GET", "/api/v5/public/time")
        if len(clock) != 1 or abs(e.clock() - native.native_time(clock[0]["ts"]) / 1000) > 2:
            raise SafetyError("OKX server/local clock skew exceeds 2s")
        allocation = dec(
            values.get(
                "allocation", binding["allocation"] if binding else e.settings["paper_balance"]
            )
        )
        available = dec(proof["balance"]["assets"].get(pair.quote, {}).get("available", 0))
        if (
            allocation <= 0
            or binding
            and allocation != dec(binding["allocation"])
            or not binding
            and allocation > available
        ):
            raise SafetyError(
                "Demo allocation exceeds available funds or changes an existing binding"
            )
        duration = values.get("duration_seconds", 3600)
        count = values.get("max_orders", 4)
        if (
            type(duration) is not int
            or not 30 <= duration <= 86400
            or type(count) is not int
            or not 1 <= count <= 100
        ):
            raise SafetyError(
                "Demo strategy requires 30–86400 seconds and 1–100 total order attempts"
            )
        budget = dec(values.get("budget", e.settings["order_size"]))
        if not ZERO < budget <= min(allocation, dec(e.settings["max_exposure"])):
            raise SafetyError(
                "Demo strategy starting-currency budget exceeds allocation/exposure bounds"
            )
        strategy = e.settings["strategy"]
        if strategy == "arbitrage" and count < 3:
            raise SafetyError("An arbitrage route requires permission for all three leg attempts")
        if strategy == "twap":
            raise SafetyError("Use the existing finite TWAP authorization for TWAP")
        if strategy in programs.STRATEGIES:
            programs.validate_markets(e.settings, e.resolve)
            saved = e.store.get(programs.key(e))
            if saved and (
                saved["status"] == "complete"
                or saved["config"] != programs.configuration(e.settings)
            ):
                raise SafetyError(
                    "Create a New strategy run before authorizing changed/completed settings"
                )
        if strategy == "dca" and (
            dec(e.settings["dca_amount"]) * e.settings["dca_count"] > budget
            or e.settings["dca_count"] > count
            or e.settings["dca_count"] * e.settings["dca_period_seconds"] > duration
        ):
            raise SafetyError(
                "DCA schedule, total prefunding and order count must fit this finite authorization"
            )
        if strategy in {"htf", "scalp"}:
            # Finish history I/O before capturing the preview's quote cohort.
            if strategy == "htf":
                from kairos.htf_review import native_rows

                minutes = e.settings["candle_minutes"]
                rows = await e.client.bars(pair, minutes)
                cutoff = int(e.clock()) // (minutes * 60) * minutes * 60
                if native_rows(pair, rows, cutoff, minutes)[0] is None:
                    raise SafetyError("Demo HTF requires 30 adjacent completed native bars")
            else:
                scalping.signal(await e.client.candles(pair, 1), e.settings, e.clock())
        # Every valuation uses an actual native same-currency market, never parity.
        for market in selected:
            await e.client.wait_limit_reference(
                market,
                e.settings["stale_seconds"],
                lambda: generation == e.stop_generation and not e.shutting_down,
            )
        books = {market.id: await e.client.book(market) for market in selected}
        # A later book read can itself wait. Validate the entire captured cohort
        # after all I/O; never silently authorize terms derived from an aged book.
        for book in books.values():
            book.fresh(e.settings["stale_seconds"])
        prices = {pair.quote: dec(1)}
        for market in selected:
            if market.quote == pair.quote:
                prices[market.base] = books[market.id].mid
        limits = {}
        for market in selected:
            if market.quote not in prices:
                raise SafetyError(
                    "No disclosed native valuation route for an intermediate currency"
                )
            book = books[market.id]
            if book.spread_bps > dec(e.settings["max_spread_bps"]):
                raise SafetyError("Demo market spread exceeds configured bound")
            fee = max(e.fees.reserve(market), e.fees.reserve(market, True))
            # Absolute bounds are visible in the one-use preview; signals cannot widen them.
            ceiling = market.price(book.asks[0][0] * dec("1.05"), "buy")
            floor = market.price(book.bids[0][0] * dec("0.95"), "sell")
            if market.id == pair.id:
                if values.get("buy_ceiling") not in (None, ""):
                    ceiling = market.price(dec(values["buy_ceiling"]), "buy")
                if values.get("sell_floor") not in (None, ""):
                    floor = market.price(dec(values["sell_floor"]), "sell")
            if min(ceiling, floor) <= 0 or floor >= ceiling:
                raise SafetyError("Invalid absolute demo strategy price bounds")
            limits[market.id] = {
                "base": market.base,
                "quote": market.quote,
                "instrument": e.client.instruments[market.id]["instId"],
                "market_rules": fingerprint(e.client.instruments[market.id]),
                "buy_ceiling": str(ceiling),
                "sell_floor": str(floor),
                "budget": str(budget / prices[market.quote]),
                "fee_bps": str(fee),
            }
        if (
            strategy in {"maker", "arbitrage"}
            or strategy == "htf"
            and e.settings["htf_policy"] != "multibar-v2"
        ):
            if e.jev is None:
                raise SafetyError(
                    "This strategy requires the configured Jev model; no deterministic substitute"
                )
        if generation != e.stop_generation or e.shutting_down:
            raise SafetyError("Demo strategy preview revoked by Stop")
        proposal = {
            "id": uuid.uuid4().hex,
            "kind": "strategy",
            "strategy": strategy,
            "environment": "demo",
            "account": "••••" + proof["config"]["uid"][-4:],
            "account_scope": fingerprint({"uid": proof["config"]["uid"], "environment": "demo"}),
            "pair": pair.id,
            "instrument": e.client.instruments[pair.id]["instId"],
            "base": pair.base,
            "opening_owned_balances": (e.ledger() or {}).get("balances", {}),
            "ledger_id": fingerprint(e.ledger()),
            "spending_currency": pair.quote,
            "allocation": str(allocation),
            "budget": str(budget),
            "markets": limits,
            "max_orders": count,
            "duration_seconds": duration,
            "created_at": e.clock(),
            "expires_at": e.clock() + 120,
            "generation": generation,
            "settings_id": e.settings_id(),
            "market_rules": fingerprint(e.client.instruments[pair.id]),
            "fee_bps": limits[pair.id]["fee_bps"],
            "demo_fee_allowance_bps": None,
            "fee_source": e.planning_source,
            "write_gate": e.client.allow_writes,
            "claimed": False,
            "confirmation": confirmation(e, "strategy"),
            "program_terms": programs.configuration(e.settings)
            if strategy in programs.STRATEGIES
            else None,
            "protocol": "okx-demo-bounded-strategies-v1; separate from the frozen Alpaca trial",
            "dust_policy": "Existing inventory remains owned. HTF/scalp exclude unrelated inventory. No cleanup buy, reset or automatic liquidation.",
            "execution_policy": "HTF initial book reads may wait up to 60s within the original authorization before evaluation; no orders or protective exits while data is unavailable, and recovered entry candidates are re-baselined. Before HTF evaluation, authenticated fees are refreshed with up to 30s grace for transient fee reads, within the original deadline; no orders or protective exits until fresh fees and books pass checks. Fee-age and authorized fee limits are unchanged. Later execution failures still stop the run. One outstanding order; IOC or demo post-only. Resting orders are locally canceled after at most 30s plus scheduler/settlement latency, or on Stop/expiry. OKX post-only has no server-side expiry here and can remain open during an outage. Restart never rearms. Price bounds also constrain protective exits; holdings can remain after permission ends.",
            "budget_policy": "Cumulative buy commitments per market in each listed native quote; starting-currency spending is also capped across markets. Intermediate currencies are never added together or treated at parity. Sales use allocated/acquired inventory only. Every durable attempt consumes the global count, even when rejected or unfilled.",
        }
        e.store.put("okx-preview:" + proposal["id"], proposal)
        e.emit_state()
        return proposal


def check(e, pair, side, volume, price, include_intent=None):
    scope = e.permission()
    limit = scope["markets"].get(pair.id)
    if not limit or side not in {"buy", "sell"}:
        raise SafetyError("Market/side outside the finite demo strategy authorization")
    children = [
        o
        for o in e.orders()
        if o.get("authorization_id") == scope["id"] and o["id"] != include_intent
    ]
    if len(children) >= scope["max_orders"]:
        raise SafetyError("Demo strategy attempt count exhausted; no automatic rearming")
    if (
        side == "buy"
        and price > dec(limit["buy_ceiling"])
        or side == "sell"
        and price < dec(limit["sell_floor"])
    ):
        raise SafetyError("Demo strategy price exceeds its absolute authorization bound")
    fee = max(e.fees.reserve(pair), e.fees.reserve(pair, True))
    if fee > dec(limit["fee_bps"]):
        raise SafetyError("Demo strategy fee allowance increased; new preview required")

    def committed(order):
        return max(
            dec(order["volume"]) * dec(order["price"]) * (1 + dec(order["fee_bps"]) / BPS),
            dec(order["cost"]) + max(ZERO, dec(order.get("fees", {}).get(order["quote"], 0))),
        )

    buys = [o for o in children if o["side"] == "buy"]
    if side == "buy":
        needed = volume * price * (1 + fee / BPS)
        if sum((committed(o) for o in buys if o["pair"] == pair.id), ZERO) + needed > dec(
            limit["budget"]
        ):
            raise SafetyError("Demo strategy native market budget exhausted")
        if pair.quote == scope["spending_currency"] and sum(
            (committed(o) for o in buys if o["quote"] == pair.quote), ZERO
        ) + needed > dec(scope["budget"]):
            raise SafetyError("Demo strategy starting-currency budget exhausted")


async def recheck(e, proposal):
    if fingerprint(e.ledger()) != proposal["ledger_id"]:
        raise SafetyError("Demo owned balances/fees changed after preview; inspect a new preview")
    if e.client.environment != "demo" or {p.id for p in markets(e)} != set(proposal["markets"]):
        raise SafetyError("Demo strategy environment/market scope changed")
    for identifier, limit in proposal["markets"].items():
        pair = e.resolve(identifier)
        if fingerprint(e.client.instruments[identifier]) != limit["market_rules"]:
            raise SafetyError("Demo strategy market rules changed")
        if max(e.fees.reserve(pair), e.fees.reserve(pair, True)) > dec(limit["fee_bps"]):
            raise SafetyError("Demo strategy fees increased; prepare a new preview")


async def run(e):
    strategy = e.settings["strategy"]
    if strategy in programs.STRATEGIES:
        await programs.run(e)
    elif strategy == "htf":
        await e.wait_htf_book()
        await e.wait_htf_fees()
        await htf.run(e)
    elif strategy == "scalp":
        await scalping.run(e)
    elif strategy == "arbitrage":
        scope = e.permission()
        used = sum(o.get("authorization_id") == scope["id"] for o in e.orders())
        if used + 3 > scope["max_orders"]:
            e.running = False
            return
        await e.arbitrage(e.resolve(e.settings["pair"]))
    else:
        await e.directional(e.resolve(e.settings["pair"]))
