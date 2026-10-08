"""Demo summary representation proof; never round or adjust the economic ledger."""

from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from kairos import okx_account as native
from kairos.domain import TERMINAL, ZERO, SafetyError, dec


def _binary64_value(value):
    """Exact normal binary64 rounding using integers/Decimal, not floating point."""
    if value <= 0 or not -308 <= value.adjusted() <= 308:
        return None
    numerator, denominator = value.as_integer_ratio()
    exponent = numerator.bit_length() - denominator.bit_length()
    if numerator << max(-exponent, 0) < denominator << max(exponent, 0):
        exponent -= 1
    if not -1022 <= exponent <= 1023:
        return None  # No underflow/subnormal or overflow allowance for cash.
    shift = 52 - exponent
    numerator <<= max(shift, 0)
    denominator <<= max(-shift, 0)
    significand, remainder = divmod(numerator, denominator)
    if remainder * 2 > denominator or remainder * 2 == denominator and significand % 2:
        significand += 1
    if exponent == 1023 and significand == 1 << 53:
        return None
    with localcontext() as context:
        context.prec = 1100  # Exact finite decimal expansion, including 2**-1074.
        return Decimal(significand) * Decimal(2) ** (exponent - 52)


def demo_summary(value):
    """Shortest nearest-even decimal reporting of a normal 53-bit significand.

    This models the observed demo summary representation, not an OKX precision
    guarantee. Only complete exact bill proof may use it. Live stays strict.
    """
    rounded = _binary64_value(value)
    if rounded is None:
        return None
    with localcontext() as context:
        context.prec = 1100
        for digits in range(1, 18):
            quantum = Decimal(1).scaleb(rounded.adjusted() - digits + 1)
            candidate = rounded.quantize(quantum, rounding=ROUND_HALF_EVEN)
            if _binary64_value(candidate) == rounded:
                return candidate
    return None


def bill_evidence(binding, ledger, orders, bills, snapshot, differences):
    """Return proof only for exact bills plus the specific demo representation.

    Exact balance reconciliation remains unchanged. This is not an epsilon,
    external-activity acknowledgement, live allowance or ledger correction.
    """
    if binding["environment"] != "demo" or not differences:
        return None
    if any(
        demo_summary(dec(row["expected"])) != dec(row["observed"])
        or asset not in snapshot["assets"]
        or dec(snapshot["assets"][asset]["frozen"]) != ZERO
        or dec(snapshot["assets"][asset]["available"]) != dec(row["observed"])
        for asset, row in differences.items()
    ):
        return None

    def require(condition, reason):
        if not condition:
            raise SafetyError(
                f"OKX native bill precision proof failed: {reason}; trading remains blocked"
            )

    fills = {}
    for order in orders:
        require(order["status"] in TERMINAL, "unresolved original order")
        executions = order.get("executions", [])
        require(
            sum((dec(f["quantity"]) for f in executions), ZERO) == dec(order["filled"]),
            "incomplete execution quantities",
        )
        if executions:
            require(order.get("fee_reported"), "unconfirmed native fees")
        for fill in executions:
            key = (fill["order_id"], fill["trade_id"])
            require(
                key not in fills and fill["order_id"] == order["txid"], "ambiguous fill identity"
            )
            require(
                fill["fee_currency"] in (None, order["base"], order["quote"]),
                "unsupported fee currency",
            )
            fills[key] = (order, fill)
    require(bool(fills), "no owned executions")
    baseline_ids = set(binding["bills"])
    rows, seen, legs, fees = [], set(), {}, {}
    fields = (
        "billId",
        "ordId",
        "tradeId",
        "instType",
        "instId",
        "type",
        "subType",
        "ccy",
        "sz",
        "px",
        "fee",
        "balChg",
        "bal",
        "ts",
    )
    for bill in bills:
        identifier = native.identifier(bill["billId"])
        require(identifier not in seen, "duplicate bill identity")
        seen.add(identifier)
        if identifier in baseline_ids:
            continue
        require(
            all(isinstance(bill.get(k), str) and bill[k] for k in fields),
            "missing native bill fields",
        )
        key = (bill["ordId"], bill["tradeId"])
        require(key in fills, "unrecognized order/trade activity")
        order, fill = fills[key]
        asset = bill["ccy"]
        require(
            bill["instType"] == "SPOT"
            and bill["type"] == "2"
            and bill["instId"] == order["instrument"]
            and asset in {order["base"], order["quote"]},
            "instrument/product/currency differs from the original execution",
        )
        require(asset not in legs.setdefault(key, {}), "duplicate currency leg")
        legs[key][asset] = identifier
        gross = dec(fill["quantity"] if asset == order["base"] else fill["cost"])
        direction = (1 if order["side"] == "buy" else -1) * (1 if asset == order["base"] else -1)
        fee = dec(fill["fee_signed"]) if asset == fill["fee_currency"] else ZERO
        require(
            dec(bill["sz"]) == gross
            and dec(bill["px"]) == dec(fill["price"])
            and dec(bill["fee"]) == fee
            and dec(bill["balChg"]) == gross * direction + fee
            and bill["subType"] == ("1" if direction > 0 else "2"),
            "gross movement, fee or net change differs from the retained fill",
        )
        stamp = native.native_time(bill["ts"])
        require(
            stamp == fill["record_time"] and stamp <= snapshot["timestamp"],
            "bill timestamp differs or follows the balance snapshot",
        )
        fees[asset] = fees.get(asset, ZERO) - fee
        rows.append({k: bill[k] for k in fields})
    for key, (order, fill) in fills.items():
        require(set(legs.get(key, {})) == {order["base"], order["quote"]}, "missing currency leg")
        require(fill["bill_id"] in legs[key].values(), "original fill bill identity missing")
    rows.sort(key=lambda r: (native.native_time(r["ts"]), int(r["billId"])))
    checkpoint = {k: dec(v) for k, v in binding["baseline"].items()}
    changes = {}
    for row in rows:
        asset = row["ccy"]
        change = dec(row["balChg"])
        changes[asset] = changes.get(asset, ZERO) + change
        checkpoint[asset] = checkpoint.get(asset, ZERO) + change
        require(
            checkpoint[asset] == dec(row["bal"]),
            "native balance checkpoint does not conserve its changes",
        )
    for observed, expected in ((changes, ledger["account_delta"]), (fees, ledger["fees"])):
        require(
            all(
                dec(observed.get(k, 0)) == dec(expected.get(k, 0))
                for k in observed.keys() | expected.keys()
            ),
            "native bill totals differ from the exact ledger",
        )
    require(set(differences) <= changes.keys(), "summary difference has no owned bill checkpoint")
    return {
        "method": "exact_native_bills_with_demo_summary_representation",
        "summary_model": "shortest_decimal_of_nearest_even_binary64_normal",
        "bill_checkpoints": rows,
        "precision_differences": {
            asset: {**row, "difference": str(dec(row["observed"]) - dec(row["expected"]))}
            for asset, row in differences.items()
        },
        "ledger_adjustment": "0",
    }
