"""Allowlisted account reads, separate from bot allocations and execution state."""

import math
from itertools import islice

from kairos.domain import SafetyError

# No browser-supplied endpoint, parameters, credentials, or write methods.
SOURCES = {
    "spot-balances": ("spot", "BalanceEx", "Spot / FX / xStocks wallet"),
    "spot-orders": ("spot", "OpenOrders", "Spot-family open orders"),
    "spot-trades": ("spot", "TradesHistory", "Recent spot-family trades (first page)"),
    "margin-balance": ("spot", "TradeBalance", "Spot margin collateral · USD valuation"),
    "margin-positions": ("spot", "OpenPositions", "Spot margin positions"),
    "earn": ("spot", "Earn/Allocations", "Earn allocations · exchange-reported units"),
    "deposits": ("spot", "Ledgers", "Deposit ledger (first page)"),
    "withdrawals": ("spot", "Ledgers", "Withdrawal ledger (first page)"),
    "futures-balances": ("futures", "accounts", "Futures wallets · native account units"),
    "futures-positions": ("futures", "openpositions", "Futures positions · contract units"),
    "futures-orders": ("futures", "openorders", "Futures open orders · contract units"),
    "futures-fills": ("futures", "fills", "Recent futures fills (first page)"),
}


def scalar(value):
    if value is None or isinstance(value, (dict, list)):
        return "—"
    if isinstance(value, float) and not math.isfinite(value):
        return "—"
    return str(value)


def flatten(data, path="", depth=0):
    if depth > 8:
        yield [path, "Nested data omitted"]
    elif isinstance(data, dict):
        for key, value in data.items():
            yield from flatten(value, f"{path}.{key}" if path else str(key), depth + 1)
    elif isinstance(data, list):
        for i, value in enumerate(data):
            yield from flatten(value, f"{path}[{i}]", depth + 1)
    else:
        yield [path, scalar(data)]


async def account_snapshot(spot, futures, source):
    if source not in SOURCES:
        raise SafetyError("Unknown read-only account view")
    venue, method, title = SOURCES[source]
    if venue == "futures":
        if futures is None:
            raise SafetyError("Futures read-only adapter is unavailable")
        data = await futures.get(method)
    else:
        if not spot.key or not spot.secret:
            raise SafetyError("Spot read-only credentials are not configured")
        params = {"asset": "ZUSD"} if method == "TradeBalance" else None
        if method == "Ledgers":
            params = {"type": "deposit" if source == "deposits" else "withdrawal"}
        data = await spot.request(method, params, private=True)

    truncated = False
    if source == "spot-balances":
        columns = ["Asset (exchange ID)", "Balance", "Trade hold", "Credit", "Credit used"]
        rows = [
            [
                asset,
                *[
                    scalar(row.get(key))
                    for key in ("balance", "hold_trade", "credit", "credit_used")
                ],
            ]
            for asset, row in data.items()
        ]
    elif source == "spot-orders":
        columns = ["Order", "Market", "Side", "Type", "Limit", "Quantity", "Filled", "Status"]
        rows = [
            [
                key,
                *[
                    scalar(row.get("descr", {}).get(k))
                    for k in ("pair", "type", "ordertype", "price")
                ],
                *[scalar(row.get(k)) for k in ("vol", "vol_exec", "status")],
            ]
            for key, row in data["open"].items()
        ]
    elif source in {"spot-trades", "margin-positions"}:
        positions = source == "margin-positions"
        columns = [
            "Record",
            "Market",
            "Side",
            "Quantity",
            "Closed quantity" if positions else "Price",
            "Cost (quote)",
            "Margin" if positions else "Fee (quote)",
        ]
        keys = (
            "pair",
            "type",
            "vol",
            "vol_closed" if positions else "price",
            "cost",
            "margin" if positions else "fee",
        )
        records = data if positions else data["trades"]
        rows = [[key, *[scalar(row.get(k)) for k in keys]] for key, row in records.items()]
        truncated = not positions and data.get("count", len(rows)) > len(rows)
    elif source == "margin-balance":
        labels = {
            "eb": "Equivalent balance",
            "tb": "Trade balance",
            "m": "Margin used",
            "n": "Unrealized P&L",
            "e": "Equity",
            "mf": "Free margin",
            "ml": "Margin level (%)",
        }
        columns = ["Metric", "Exchange-reported value"]
        rows = [[label, scalar(data.get(key))] for key, label in labels.items()]
    elif source in {"deposits", "withdrawals"}:
        keys = ("asset", "amount", "fee", "time", "balance")
        columns = ["Ledger record", "Asset", "Amount", "Fee", "Time (Unix)", "Balance after entry"]
        rows = [
            [identifier, *[scalar(row.get(k)) for k in keys]]
            for identifier, row in data["ledger"].items()
        ]
        truncated = data.get("count", len(rows)) > len(rows)
    elif source == "earn":
        # Keep currency paths intact; do not pool allocations with available spot balances.
        columns = ["Allocation field", "Exchange-reported value"]
        rows = list(
            islice(
                flatten(
                    {"converted_asset": data.get("converted_asset"), "items": data.get("items", [])}
                ),
                1001,
            )
        )
        truncated = bool(data.get("next_cursor"))
    elif source == "futures-balances":
        columns = ["Wallet / field / currency", "Exchange-reported value"]
        roots = {
            "type",
            "currency",
            "balances",
            "currencies",
            "auxiliary",
            "marginRequirements",
            "triggerEstimates",
            "balance",
            "portfolioValue",
            "availableMargin",
            "unrealizedFunding",
            "unrealizedPnL",
            "pnl",
        }
        wallets = {
            name: {key: value for key, value in row.items() if key in roots}
            for name, row in data["accounts"].items()
        }
        rows = list(islice(flatten(wallets), 1001))
    else:
        key, fields = {
            "futures-positions": (
                "openPositions",
                ("symbol", "side", "size", "price", "unrealizedFunding"),
            ),
            "futures-orders": (
                "openOrders",
                ("order_id", "symbol", "side", "orderType", "limitPrice", "unfilledSize"),
            ),
            "futures-fills": ("fills", ("fill_id", "symbol", "side", "size", "price", "fillTime")),
        }[source]
        columns = list(fields)
        rows = [[scalar(row.get(field)) for field in fields] for row in data[key]]
    return {
        "source": source,
        "venue": venue,
        "title": title,
        "columns": columns,
        "rows": rows[:1000],
        "truncated": truncated or len(rows) > 1000,
    }
