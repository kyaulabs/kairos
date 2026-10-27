"""Small, activity-first VWAP/Fibonacci pilots using Kraken data and OKX spot assumptions.

Research only: no credentials, network clients, hosted-demo writes or live policies.
"""

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from kairos import okx_account
from kairos.backtest import (
    ARCHIVE,
    ROOT,
    SCENARIOS,
    HistoricalMarket,
    archive_rows,
    date,
    decimal,
    write_reports,
)
from kairos.domain import BPS, ZERO, Pair, SafetyError, dec, floor
from kairos.engine import EXECUTION_REVISION, Engine
from kairos.fees import AccountFees
from kairos.research_diagnostics import (
    clock_facts,
    economics_summary,
    entry_economics,
    execution_estimate,
    exit_economics,
)
from kairos.research_signals import RULES, VARIANTS, Signals
from kairos.settings import DEFAULTS, validate_settings
from kairos.store import Store
from kairos.strategies import limit_price

INSTRUMENT = Path(__file__).with_name("research_instrument.json")
ECONOMIC_POLICIES = ("diagnostic", "positive-target")
ENTRY_EXECUTIONS = ("ioc", "maker-best-bid-30s")
MAKER_ASSUMPTIONS = [
    "Entry is a single offline post-only buy at the observed next-open bid, fixed for 30 seconds; no repricing or taker fallback. Live OKX adapter still permits IOC only; post-only is supported in demo, not authorized here.",
    "Only a strictly later synthetic path price BELOW the bid before expiry can fund a maker fill. A touch, submission-time quote or crossing after expiry never fills. No historical OKX queue, trade timing or cancellation acknowledgement is reconstructed.",
    "First eligible crossing consumes preceding-minute liquidity; a partial fill cancels its remainder before owned protection. A crossing through the stop fills first, then triggers protective IOC at the observed adverse price; losing entries are not retrospectively canceled.",
    "Maker entry fees and taker exit fees use separate received-currency rates. All stops, targets and deadlines remain bot-managed with taker IOC reductions.",
    "The assumed extrema at seconds 20/40 dominate a 30-second lifetime: OLHC can fill at the low at second 20, whereas OHLC reaches its low after expiry. Zero-fill stress cases are timing sensitivity, not a risk or profitability improvement.",
]
ASSUMPTIONS = [
    "Activity-first retrospective pilot, not untouched holdout evidence or a profitability claim; refinements on this sample are tuning.",
    "Kraken XBTUSDT native candles are cross-venue price/volume inputs, NOT historical OKX quotes, depth, queues or fills.",
    "Daily UTC VWAP/sigma use volume-weighted candle HLC3 and population variance; actual trade-price VWAP cannot be reconstructed.",
    "EMA20/50 seeded from the first observed warmup close; ATR is the last 14 observed true ranges. Missing candles are not invented.",
    "Signals require adjacent completed candles and 50 observed warmup bars; daily VWAP setups require 20 observed session bars.",
    "Swing pivots need two adjacent closed candles on each side. Anchors become available only at confirmation; entries are never backdated.",
    "Next-open marketable IOC entries only at the first minute after a completed signal candle; a missing next minute expires the signal.",
    "Minute-path OLHC/OHLC points at seconds 1/20/40/59 are assumptions; protective exits act only at observed points, not guaranteed stop prices.",
    "IOC liquidity is capped by PREVIOUS completed Kraken minute volume times participation; current/future volume cannot fund fills. IOC remainders are canceled.",
    "OKX current public BTC-USDT instrument increments, not historical rules. Fee rates are explicit assumptions, NOT the user's account tier.",
    "Assumed OKX received-currency fee policy: BTC on buys, USDT on sells. Synthetic fills use production okx_account accounting; they are NOT broker evidence.",
    "KAIROS permits cash IOC orders, not native attached SL/TP or /trade/order-algo. Stops/targets are BOT-MANAGED and frozen at confirmation; never assume exchange-held protection.",
    "Planned stop risk includes assumed entry/exit fees and adverse spread/slippage. Budget is 1% of min(starting capital, current equity); retained dust reserves worst-case risk. Price gaps/liquidity delays can exceed it and are reported, never clamped away.",
    "4h maximum hold; no shorts, leverage, reinvestment, automatic loss-halt restart or terminal forced liquidation. Daily halts block new entries but owned protective reductions remain permitted.",
    "Signals are counted even while invested/halted; signal frequency, order attempts and filled entries are distinct activity measures.",
    "Sub-minimum residuals remain owned and marked. Closed trade episodes allocate cost basis only to sold net BTC; retained dust is not written off or counted as cash.",
    "Liquidity sweep is an OHLC swing-low break/reclaim hypothesis, not observed stop orders or proof of stop-hunting.",
]


class ResearchEngine(Engine):
    exchange = "okx-research"

    def __init__(self, store, market, variant, entry_execution="ioc"):
        super().__init__(
            store,
            market,
            SimpleNamespace(key="offline-no-model"),
            lambda *_: None,
            clock=lambda: market.now,
        )
        self.settings["strategy"] = variant
        market.fee_name = "Research assumed"
        market.fee_source = "Offline constant fee scenario; not historical account evidence"
        self.fees = AccountFees(market, None, clock=self.clock)
        self.position_id, self.reason = None, ""
        self.execution_purpose = "offline-vwap-fibonacci-pilot"
        self.sequence = 0
        self.entries_halted = self.reducing = False
        if entry_execution not in ENTRY_EXECUTIONS:
            raise SafetyError("Unknown research entry execution")
        self.entry_execution = entry_execution

    def validate_capabilities(self, values):
        if (
            values["strategy"] not in VARIANTS
            or values["product"] != "spot"
            or self.kraken.allow_live
        ):
            raise SafetyError("Research engine supports offline long-only spot only")

    def emit_state(self):
        pass

    def event(self, kind, data):
        # Orders, exact native asset deltas and diagnostics are retained in the report.
        pass

    def reset_ledger(self, mode, amount):
        super().reset_ledger(mode, amount)
        ledger = self.ledger(mode)
        ledger["account_delta"] = {}
        self.store.put("ledger:" + mode, ledger)

    def tag_order(self, order):
        super().tag_order(order)
        order.update(
            position_id=self.position_id,
            reason=self.reason,
            instrument="BTC-USDT",
            client_id=order["id"].replace("-", ""),
            stop_management="bot-managed",
            native_attached_stop=False,
            simulated=True,
            evidence_source="assumed offline passive" if order["maker"] else "assumed offline IOC",
        )
        order["payload"] = {
            "instId": order["instrument"],
            "tdMode": "cash",
            "clOrdId": order["client_id"],
            "side": order["side"],
            "ordType": "post_only" if order["maker"] else "ioc",
            "sz": order["volume"],
            "px": order["price"],
            "tradeQuoteCcy": order["quote"],
            "stpMode": "cancel_taker",
            "pxAmendType": "0",
        }

    async def valuation(self, enforce=False):
        return await super().valuation(enforce=enforce and not self.reducing)

    async def place(self, pair, side, volume, price, book, maker=False, **kwargs):
        if self.mode != "dry-run":
            raise SafetyError("Research cannot submit venue orders")
        if maker and (
            self.entry_execution != "maker-best-bid-30s"
            or side != "buy"
            or price != book.bids[0][0]
            or price >= book.asks[0][0]
        ):
            raise SafetyError("Research maker entry must be an opted-in best-bid buy")
        if side == "buy" and self.entries_halted:
            raise SafetyError("Latched entry loss halt")
        if side == "sell":
            owned = sum(
                (
                    net_quantity(o) * (1 if o["side"] == "buy" else -1)
                    for o in self.orders()
                    if o.get("position_id") == self.position_id
                ),
                ZERO,
            )
            if not self.position_id or not ZERO < volume <= owned:
                raise SafetyError("Research exit must reduce its tracked net BTC only")
        self.reducing = side == "sell"
        try:
            return await super().place(pair, side, volume, price, book, maker=maker, **kwargs)
        finally:
            self.reducing = False

    def apply(self, order, volume, cost, fee, status):
        if order["mode"] != "dry-run" or dec(order["filled"]):
            raise SafetyError("Expected one offline fill accounting checkpoint")
        order["status"] = status
        if not volume:
            self.store.save_order(order)
            return
        self.sequence += 1
        price = cost / volume
        fee_currency = order["base"] if order["side"] == "buy" else order["quote"]
        fee_native = (volume if order["side"] == "buy" else cost) * dec(order["fee_bps"]) / BPS
        stamp = str(int(self.clock() * 1000))
        row = {
            "instType": "SPOT",
            "instId": order["instrument"],
            "side": order["side"],
            "tradeQuoteCcy": order["quote"],
            "clOrdId": order["client_id"],
            "ordId": str(self.sequence),
            "fillSz": str(volume),
            "fillPx": str(price),
            "fee": str(-fee_native),
            "feeCcy": fee_currency,
            "execType": "M" if order["maker"] else "T",
            "billId": str(self.sequence),
            "tradeId": str(self.sequence),
            "fillTime": stamp,
            "ts": stamp,
        }
        updated, _ = okx_account.apply_executions(
            self.store, order, [row], {"environment": "offline", "uid": "0"}
        )
        order.clear()
        order.update(updated)
        order["fee_quote_equivalent"] = str(
            fee_native * price if fee_currency == order["base"] else fee_native
        )
        self.store.save_order(order)
        if any(dec(value) < 0 for value in self.ledger()["balances"].values()):
            raise SafetyError("Synthetic native accounting exceeded allocated funds")


def net_quantity(order):
    return dec(order["filled"]) + sum(
        (
            dec(x["fee_signed"])
            for x in order.get("executions", [])
            if x["fee_currency"] == order["base"]
        ),
        ZERO,
    )


def instrument():
    snapshot = json.loads(INSTRUMENT.read_text())
    row = snapshot["instrument"]
    return Pair(
        "okx-research:BTC-USDT:USDT",
        "BTC/USDT",
        "BTC",
        "USDT",
        dec(row["tickSz"]),
        dec(row["lotSz"]),
        dec(row["minSz"]),
        ZERO,
    )


def stop_risk_plan(
    pair,
    entry,
    stop,
    equity,
    initial,
    cash,
    dust_quantity,
    dust_mark,
    scenario,
    fee_bps,
    order_cap,
    exposure_cap,
    risk_percent,
    *,
    entry_fee_bps=None,
):
    """Exact received-currency fee model; lot-floor risk sizing, not a stop guarantee."""
    risk_percent, fee = dec(risk_percent), dec(fee_bps) / BPS
    buy_fee = fee if entry_fee_bps is None else dec(entry_fee_bps) / BPS
    if (
        not ZERO < risk_percent <= 1
        or not ZERO <= fee < 1
        or not ZERO <= buy_fee < 1
        or not ZERO < stop < entry
        or min(equity, initial, cash) < 0
    ):
        raise SafetyError("Invalid planned-risk inputs")
    exit_bound = execution_estimate(pair, stop, "sell", scenario)
    loss_per_gross_unit = entry - (1 - buy_fee) * exit_bound * (1 - fee)
    if exit_bound <= 0 or loss_per_gross_unit <= 0:
        raise SafetyError("Invalid stop execution estimate")
    budget = min(initial, equity) * risk_percent / 100
    dust_risk = dust_quantity * dust_mark
    available_risk = max(ZERO, budget - dust_risk)
    # A partial IOC or base-denominated buy fee can leave less than the sell
    # minimum owned. Reserve its full purchase cost: it cannot have a stop.
    potential_dust = pair.minimum * entry / (1 - buy_fee)
    sizing_risk = max(ZERO, available_risk - potential_dust)
    notional = min(order_cap, max(ZERO, exposure_cap - dust_risk), cash / (1 + buy_fee))
    quantity = floor(min(sizing_risk / loss_per_gross_unit, notional / entry), pair.lot)
    return quantity, {
        "percent": str(risk_percent),
        "capital_basis": str(min(initial, equity)),
        "capital_budget": str(budget),
        "dust_risk_reserved": str(dust_risk),
        "potential_unprotected_dust_cost": str(potential_dust),
        "risk_allowance": str(available_risk),
        "stop_execution_estimate": str(exit_bound),
        "loss_per_gross_unit": str(loss_per_gross_unit),
        "requested_stop_risk": str(quantity * loss_per_gross_unit),
        "requested_notional": str(quantity * entry),
        "guaranteed_loss_cap": False,
    }


def pilot_settings(pair, minutes, scenario, initial, order_cap, exposure_cap, daily_loss):
    # A valid production-settings seed; research variants are deliberately NOT registered for live/dashboard selection.
    return validate_settings(
        {
            **DEFAULTS,
            "strategy": "maker",
            "product": "spot",
            "pair": pair.id,
            "quote": pair.quote,
            "candle_minutes": minutes,
            "paper_balance": str(initial),
            "order_size": str(order_cap),
            "max_exposure": str(exposure_cap),
            "daily_loss": str(daily_loss),
            "reinvest_profits": False,
            "recover_initial": False,
            "interval_seconds": 15,
            "slippage_bps": scenario["slippage_bps"],
        }
    )


async def simulate(
    minutes_rows,
    native_bars,
    pair,
    settings,
    scenario,
    variant,
    start,
    end,
    maker=8,
    taker=10,
    *,
    signals=None,
    risk_percent=1,
    economic_policy="diagnostic",
    entry_execution="ioc",
    entry_gate=None,
):
    if economic_policy not in ECONOMIC_POLICIES:
        raise SafetyError("Unknown research entry-economic policy")
    if not ZERO < dec(risk_percent) <= 1:
        raise SafetyError("Planned risk must be positive and no greater than 1%")
    market = HistoricalMarket(
        pair, scenario, dec(maker), dec(taker), settings["candle_minutes"], ()
    )
    store = Store(":memory:", clock=lambda: market.now)
    store.put("settings", settings)
    engine = ResearchEngine(store, market, variant, entry_execution)
    passive = entry_execution == "maker-best-bid-30s"
    buy_fee_bps = maker if passive else taker
    signals = signals or Signals(settings["candle_minutes"], variant)
    bars = iter(native_bars)
    next_bar = next(bars, None)
    initial = dec(settings["paper_balance"])
    previous = position = pending_entry = None
    gates, reasons, lifecycle, regimes = Counter(), Counter(), Counter(), Counter()
    signals_by_day, filled_by_day = Counter(), Counter()
    signal_count = missed_open = processed = exposed = 0
    peak = net = initial
    drawdown = ZERO
    daily, trades, dust, risk_breaches = {}, [], [], []
    max_position_loss = ZERO
    stop_overruns = 0
    first_price = last_price = halt_at = None
    candidates, exits = {}, []
    coverage = {name: Counter() for name in ("hour_UTC", "weekday_UTC", "minute_of_hour")}

    def candidate_record(pending):
        key = pending["id"]
        if key not in candidates:
            facts = pending["facts"]
            armed_at = pending["armed_at"]
            candidates[key] = {
                "candidate_id": key,
                "armed_at": armed_at,
                "arm_clock": clock_facts(armed_at),
                "warmup_arm": armed_at < start,
                "arming_facts": facts,
                "arm_reference": facts.get("signal_close"),
                "arm_stop": str(pending["stop"]),
                "arm_target": str(pending["target"]) if pending["target"] is not None else None,
                "expires_at": pending["expires"],
                "target_room_at_arm": str(pending["target"] - dec(facts["signal_close"]))
                if pending["target"] is not None
                else None,
                "outcome": "pending_at_end",
            }
            known_at = facts.get("swing_known_at", facts.get("anchor_known_at", armed_at))
            history = [
                r
                for r in getattr(signals, "rows", ())
                if known_at <= r[0] + settings["candle_minutes"] * 60 < armed_at
            ]
            width = dec(facts["atr"]) * dec(RULES["touch_atr"])
            levels = {}
            for name in ("swing_low", "swing_high", "fib_382", "fib_618"):
                if facts.get(name) is None:
                    continue
                level = dec(facts[name])
                touches = [
                    r[0] + settings["candle_minutes"] * 60
                    for r in history
                    if dec(r[3]) <= level + width and dec(r[2]) >= level - width
                ]
                levels[name] = {
                    "level": str(level),
                    "known_at": known_at,
                    "age_seconds": armed_at - known_at,
                    "prior_touch_bars": len(touches),
                    "last_touch_at": touches[-1] if touches else None,
                    "trailing_observed_bars": len(history),
                    "frozen_zone_half_width": str(width),
                }
            candidates[key]["level_context"] = levels
        return candidates[key]

    def setup_record(setup):
        key = setup.facts.get("candidate_id", f"{variant}:{setup.closed_at}")
        if key not in candidates:
            candidates[key] = {
                "candidate_id": key,
                "armed_at": setup.facts.get("armed_at"),
                "arming_facts": setup.facts.get("arming_facts", {}),
                "outcome": "confirmed",
            }
        return candidates[key]

    def observe(row, minute):
        nonlocal signal_count, missed_open
        closed_at = row[0] + settings["candle_minutes"] * 60
        before = dict(getattr(signals, "events", {}))
        pending = getattr(signals, "pending", None)
        checks, setup = signals.push(row, minute)
        if not start <= closed_at < end:
            return None
        for name, value in getattr(signals, "events", {}).items():
            delta = value - before.get(name, 0)
            lifecycle[name] += delta
            if delta and pending and name not in {"armed", "confirmed"}:
                candidate_record(pending).update(outcome=name, resolved_at=closed_at)
        current_pending = getattr(signals, "pending", None)
        if current_pending:
            candidate_record(current_pending)
        gates["observed_windows"] += 1
        for name, passed in checks.items():
            gates["failed:" + name] += int(not passed)
        if setup:
            record = candidate_record(pending) if pending else setup_record(setup)
            arm_reference = record.get("arm_reference")
            arm_stop = record.get("arm_stop")
            armed_at = record.get("armed_at")
            record.update(
                confirmed_at=setup.closed_at,
                confirmation_clock=clock_facts(setup.closed_at),
                confirmation_bars=(setup.closed_at - armed_at) / (settings["candle_minutes"] * 60)
                if armed_at is not None
                else None,
                confirmation_reference=str(setup.reference),
                confirmation_stop=str(setup.stop),
                confirmation_target=str(setup.target),
                confirmation_price_rise=str(setup.reference - dec(arm_reference))
                if arm_reference is not None
                else None,
                confirmation_stop_widening=str(dec(arm_stop) - setup.stop)
                if arm_stop is not None
                else None,
                target_room_at_confirmation=str(setup.target - setup.reference),
                outcome="confirmed",
            )
            signal_count += 1
            regimes[setup.facts.get("regime", "unspecified")] += 1
            signals_by_day[str(closed_at // 86400 * 86400)] += 1
            if setup.closed_at != minute:
                record.update(outcome="missing_next_execution_minute", resolved_at=minute)
                missed_open += 1
                reasons["Signal expired: next execution minute absent"] += 1
                return None
        return setup

    def check_position_risk():
        nonlocal max_position_loss, stop_overruns
        if not position:
            return
        bid = pair.price(market.mid * (1 - dec(scenario["spread_bps"]) / (2 * BPS)), "buy")
        mark = bid * (1 - dec(scenario["slippage_bps"]) / BPS) * (1 - dec(taker) / BPS)
        loss = max(
            ZERO, position["entry_cost"] - position["proceeds"] - position["quantity"] * mark
        )
        max_position_loss = max(max_position_loss, loss)
        if (
            position.get("exit_reason") == "Stop"
            and loss > dec(position["risk_plan"]["filled_stop_risk"])
            and not position.get("overrun")
        ):
            position["overrun"] = True
            stop_overruns += 1
        if loss > dec(position["risk_plan"]["risk_allowance"]) and not position.get(
            "risk_breached"
        ):
            position["risk_breached"] = True
            risk_breaches.append(
                {
                    "position_id": position["id"],
                    "observed_at": market.now,
                    "loss": str(loss),
                    "risk_allowance": position["risk_plan"]["risk_allowance"],
                    "exit_reason": position.get("exit_reason"),
                }
            )
            reasons["Observed position loss exceeded planned risk allowance"] += 1

    def retire_position():
        nonlocal position
        check_position_risk()
        residual = position["quantity"]
        if residual:
            dust.append(
                {
                    "position_id": position["id"],
                    "quantity": str(residual),
                    "cost_basis": str(position["entry_cost"] * residual / position["owned"]),
                    "reason": position["exit_reason"],
                }
            )
        exit_audit = exit_economics(position, engine.orders(), pair, scenario, taker)
        exit_audit.update(
            position_id=position["id"],
            episode_retired=True,
            cost_negative_at_entry=dec(
                position["entry_economics"]["net_target_reward_per_gross_unit"]
            )
            <= 0,
        )
        exits.append(exit_audit)
        if position["sold"]:
            sold_risk = dec(position["risk_plan"]["filled_stop_risk"]) * (
                position["sold"] / position["owned"]
            )
            trades.append(
                {
                    "opened_at": position["opened_at"],
                    "closed_at": position["last_exit_at"],
                    "net_pnl": str(
                        position["proceeds"]
                        - position["entry_cost"] * position["sold"] / position["owned"]
                    ),
                    "reason": position["exit_reason"],
                    "retained_quantity": str(residual),
                    "risk_plan": position["risk_plan"],
                    "risk_breached": position.get("risk_breached", False),
                    "sold_share_planned_risk": str(sold_risk),
                    "realized_R": str(dec(exit_audit["net_sold_pnl"]) / sold_risk),
                    "exit_diagnostics": exit_audit,
                }
            )
        reasons["Exit complete or residual below native minimum"] += 1
        position = None

    def accept_entry(order, setup, record, risk_plan):
        nonlocal position
        risk_plan["filled_stop_risk"] = str(
            dec(order["filled"]) * dec(risk_plan["loss_per_gross_unit"])
        )
        order.update(
            risk_plan=risk_plan,
            signal_closed_at=setup.closed_at,
            signal_facts=setup.facts,
            protection={"stop": str(setup.stop), "target": str(setup.target)},
        )
        owned = net_quantity(order)
        record.update(
            outcome="filled" if owned else "unfilled_IOC",
            requested_quantity=order["volume"],
            filled_quantity=order["filled"],
            entry_economics=entry_economics(
                pair,
                dec(order["price"]),
                setup.stop,
                setup.target,
                scenario,
                taker,
                gross=dec(order["filled"]),
                owned=owned,
                entry_fee_bps=buy_fee_bps,
            ),
        )
        order["entry_economics"] = record["entry_economics"]
        if passive and owned:
            order["first_fill_at"] = market.now
            record["passive_fill_at"] = market.now
        store.save_order(order)
        if owned:
            position = {
                "id": order["position_id"],
                "quantity": owned,
                "owned": owned,
                "entry_cost": dec(order["cost"]),
                "sold": ZERO,
                "proceeds": ZERO,
                "opened_at": market.now,
                "stop": setup.stop,
                "target": setup.target,
                "deadline": market.now + RULES["max_hold_seconds"],
                "exit_reason": None,
                "risk_plan": risk_plan,
                "entry_economics": record["entry_economics"],
            }
            filled_by_day[str(int(market.now) // 86400 * 86400)] += 1
        reasons[
            "Filled passive entry"
            if passive and owned
            else "Filled IOC entry"
            if owned
            else "IOC entry had no assumed liquidity"
        ] += 1

    def cancel_entry(outcome):
        nonlocal pending_entry
        order, _, record, _ = pending_entry
        order.update(status="canceled", passive_outcome=outcome, resolved_at=market.now)
        record.update(outcome=outcome, resolved_at=market.now, filled_quantity="0")
        store.save_order(order)
        reasons["Passive entry canceled: " + outcome] += 1
        pending_entry = None

    try:
        for row in minutes_rows:
            ts = row[0]
            if ts < start:
                previous = row
                continue
            if ts >= end:
                break
            market.now, market.mid = ts + 1, dec(row[1])
            await engine.refresh_fees()
            for name, value in clock_facts(ts).items():
                coverage[name][value] += 1
            if first_price is None:
                first_price = market.mid
                await engine.initialize()
                await engine.start()
            setup = None
            while next_bar and next_bar[0] + settings["candle_minutes"] * 60 <= ts:
                setup = observe(next_bar, ts)
                next_bar = next(bars, None)
            market.remaining = (
                dec(previous[6]) * dec(scenario["participation"])
                if previous and previous[0] + 60 == ts
                else ZERO
            )
            points = (
                (row[1], row[3], row[2], row[4])
                if scenario["path"] == "OLHC"
                else (row[1], row[2], row[3], row[4])
            )
            attempted_exit = False
            for offset, price in zip((1, 20, 40, 59), points, strict=True):
                market.now, market.mid = ts + offset, dec(price)
                if pending_entry:
                    order, resting_setup, record, risk_plan = pending_entry
                    if market.now >= order["expires"]:
                        cancel_entry("passive_expired")
                    elif engine.entries_halted or not engine.running:
                        cancel_entry("passive_loss_halt")
                    elif market.now > order["created"] and market.mid < dec(order["price"]):
                        quantity = market.consume(dec(order["volume"]))
                        if quantity:
                            engine.apply(
                                order,
                                quantity,
                                quantity * dec(order["price"]),
                                ZERO,
                                "closed" if quantity == dec(order["volume"]) else "canceled",
                            )
                            order["passive_observation"] = {
                                "mid": str(market.mid),
                                "crossed_at": market.now,
                                "prior_minute_liquidity_used": str(quantity),
                                "partial_remainder_canceled": quantity < dec(order["volume"]),
                                "crossed_through_stop": market.mid <= resting_setup.stop,
                            }
                            accept_entry(order, resting_setup, record, risk_plan)
                            pending_entry = None
                    if pending_entry and market.mid <= resting_setup.stop:
                        cancel_entry("passive_invalidated")
                    elif pending_entry and market.mid >= resting_setup.target:
                        cancel_entry("passive_target_passed")
                if position:
                    if not position.get("exit_reason"):
                        position["exit_reason"] = (
                            "Stop"
                            if market.mid <= position["stop"]
                            else "Target"
                            if market.mid >= position["target"]
                            else "4h deadline"
                            if market.now >= position["deadline"]
                            else None
                        )
                    if position["exit_reason"] and not position.get("exit_trigger"):
                        position["exit_trigger"] = {
                            "reason": position["exit_reason"],
                            "observed_at": market.now,
                            "mid": str(market.mid),
                        }
                    if (
                        position["exit_reason"] == "Target"
                        and market.mid <= position["stop"]
                        and position.get("stop_after_target_at") is None
                    ):
                        position["stop_after_target_at"] = market.now
                    if position["exit_reason"]:
                        quantity = floor(position["quantity"], pair.lot)
                        book = await market.book(pair)
                        bound = limit_price(book, "sell", settings["slippage_bps"])
                        if quantity < pair.minimum or quantity * bound < pair.cost_minimum:
                            retire_position()
                        elif engine.running and not attempted_exit:
                            attempted_exit = True
                            engine.position_id, engine.reason = (
                                position["id"],
                                position["exit_reason"],
                            )
                            quantity = floor(min(quantity, engine.limits()[0] / bound), pair.lot)
                            try:
                                order = await engine.place(pair, "sell", quantity, bound, book)
                                order["execution_observation"] = {
                                    "mid": str(market.mid),
                                    "bid": str(book.bids[0][0]),
                                    "prior_minute_liquidity_remaining": str(
                                        market.remaining + dec(order["filled"])
                                    ),
                                    "first_trigger": position["exit_trigger"],
                                }
                                store.save_order(order)
                                position["quantity"] -= dec(order["filled"])
                                position["sold"] += dec(order["filled"])
                                position["proceeds"] += dec(order["cost"]) - dec(order["fee"])
                                if dec(order["filled"]):
                                    position["last_exit_at"] = market.now
                                remainder = floor(position["quantity"], pair.lot)
                                if (
                                    remainder < pair.minimum
                                    or remainder * bound < pair.cost_minimum
                                ):
                                    retire_position()
                            except SafetyError as exc:
                                reasons["Exit blocked: " + str(exc)] += 1
                if offset == 1 and setup:
                    record = setup_record(setup)
                    book = await market.book(pair)
                    bound = (
                        book.bids[0][0]
                        if passive
                        else limit_price(book, "buy", settings["slippage_bps"])
                    )
                    record.update(
                        next_open_at=market.now,
                        next_open_clock=clock_facts(market.now),
                        next_open_mid=str(market.mid),
                        next_open_ask=str(book.asks[0][0]),
                        next_open_entry_bound=str(bound),
                        target_room_at_entry=str(setup.target - bound),
                        room_consumed_after_confirmation=str(bound - setup.reference),
                        next_open_move_from_confirmation=str(market.mid - setup.reference),
                        entry_spread_and_quote_tick_cost=str(
                            (bound if passive else book.asks[0][0]) - market.mid
                        ),
                        entry_slippage_and_bound_tick_cost=str(
                            ZERO if passive else bound - book.asks[0][0]
                        ),
                        previous_minute_present=bool(previous and previous[0] + 60 == ts),
                        previous_minute_volume=previous[6]
                        if previous and previous[0] + 60 == ts
                        else None,
                        available_liquidity=str(market.remaining),
                        entry_economics=entry_economics(
                            pair,
                            bound,
                            setup.stop,
                            setup.target,
                            scenario,
                            taker,
                            entry_fee_bps=buy_fee_bps,
                        ),
                    )
                    gate = (
                        entry_gate.evaluate(setup, market.now, settings["candle_minutes"])
                        if entry_gate is not None
                        else None
                    )
                    if gate is not None:
                        record["entry_gate"] = gate
                    if not engine.running or engine.entries_halted:
                        record["outcome"] = "blocked_loss_halt"
                        reasons["Signal blocked: latched loss halt"] += 1
                    elif pending_entry:
                        record["outcome"] = "blocked_pending_entry"
                        reasons["Signal blocked: resting entry"] += 1
                    elif position:
                        record["outcome"] = "blocked_owned_position"
                        reasons["Signal blocked: active owned position"] += 1
                    else:
                        if not setup.stop < bound < setup.target:
                            record["outcome"] = "blocked_price_plan"
                            reasons["Signal blocked: next open outside stop/target plan"] += 1
                        elif (
                            economic_policy == "positive-target"
                            and dec(record["entry_economics"]["net_target_reward_per_gross_unit"])
                            <= ZERO
                        ):
                            record["outcome"] = "blocked_target_economics"
                            reasons["Signal blocked: nonpositive modeled target reward"] += 1
                        elif gate is not None and not gate["allowed"]:
                            record["outcome"] = "blocked_assessment_" + gate["reason"]
                            reasons["Research entry filter: " + gate["reason"]] += 1
                        else:
                            await engine.valuation()
                            dust_quantity = engine.balance(pair.base)
                            dust_mark = book.bids[0][0]
                            quantity, risk_plan = stop_risk_plan(
                                pair,
                                bound,
                                setup.stop,
                                dec(engine.equity),
                                initial,
                                engine.balance(pair.quote),
                                dust_quantity,
                                dust_mark,
                                scenario,
                                taker,
                                *engine.limits(),
                                risk_percent,
                                entry_fee_bps=buy_fee_bps,
                            )
                            engine.position_id, engine.reason = (
                                f"{variant}:{setup.closed_at}",
                                VARIANTS[variant],
                            )
                            try:
                                if quantity < pair.minimum or quantity * bound < pair.cost_minimum:
                                    raise SafetyError("Risk/funding budget below native minimum")
                                order = await engine.place(
                                    pair, "buy", quantity, bound, book, maker=passive
                                )
                                if passive:
                                    risk_plan["filled_stop_risk"] = "0"
                                    order.update(
                                        risk_plan=risk_plan,
                                        signal_closed_at=setup.closed_at,
                                        signal_facts=setup.facts,
                                        protection={
                                            "stop": str(setup.stop),
                                            "target": str(setup.target),
                                        },
                                        entry_economics=record["entry_economics"],
                                    )
                                    store.save_order(order)
                                    record.update(
                                        outcome="passive_pending",
                                        posted_at=market.now,
                                        passive_expires_at=order["expires"],
                                        requested_quantity=order["volume"],
                                    )
                                    pending_entry = (order, setup, record, risk_plan)
                                else:
                                    accept_entry(order, setup, record, risk_plan)
                            except SafetyError as exc:
                                record.update(outcome="blocked_entry_safety", blocker=str(exc))
                                reasons["Entry blocked: " + str(exc)] += 1
                check_position_risk()
                held, cash = engine.balance(pair.base), engine.balance(pair.quote)
                bid = pair.price(market.mid * (1 - dec(scenario["spread_bps"]) / (2 * BPS)), "buy")
                net = cash + held * bid * (1 - dec(scenario["slippage_bps"]) / BPS) * (
                    1 - dec(taker) / BPS
                )
                # Production marked-to-market daily-loss bookkeeping; halts never rearm automatically.
                engine.record_valuation(cash + held * bid, held * bid, "day:dry-run")
                if not engine.entries_halted and -dec(engine.daily_pnl) >= dec(
                    settings["daily_loss"]
                ):
                    engine.entries_halted, halt_at, engine.last_error = (
                        True,
                        market.now,
                        "Daily marked-to-market entry loss limit reached; protective reductions remain active",
                    )
                peak = max(peak, net)
                drawdown = max(drawdown, (peak - net) / peak * 100)
                exposed += int(held > 0)
            processed += 1
            daily[str(ts // 86400 * 86400)] = str(net)
            last_price, previous = dec(row[4]), row
        if first_price is None:
            raise SafetyError("No execution minutes in requested pilot")
        # Count native signals missed after the last available execution minute too.
        while next_bar and next_bar[0] + settings["candle_minutes"] * 60 < end:
            observe(next_bar, end)
            next_bar = next(bars, None)
        if pending_entry:
            cancel_entry("passive_study_end")
        orders = engine.orders()
        if position:
            exit_audit = exit_economics(position, orders, pair, scenario, taker)
            exit_audit.update(
                position_id=position["id"],
                episode_retired=False,
                cost_negative_at_entry=dec(
                    position["entry_economics"]["net_target_reward_per_gross_unit"]
                )
                <= 0,
            )
            exits.append(exit_audit)
        expected = {name: Counter() for name in coverage}
        for ts in range(start, end, 60):
            for name, value in clock_facts(ts).items():
                expected[name][value] += 1
        closed_R = [dec(t["realized_R"]) for t in trades]
        entries = [o for o in orders if o["side"] == "buy"]
        fills = [o for o in entries if dec(o["filled"])]
        days = (end - start) / 86400
        gaps = [
            (b.get("first_fill_at", b["created"]) - a.get("first_fill_at", a["created"])) / 3600
            for a, b in zip(fills, fills[1:], strict=False)
        ]
        opening = (
            first_price
            * (1 + dec(scenario["spread_bps"]) / (2 * BPS))
            * (1 + dec(scenario["slippage_bps"]) / BPS)
        )
        closing = (
            last_price
            * (1 - dec(scenario["spread_bps"]) / (2 * BPS))
            * (1 - dec(scenario["slippage_bps"]) / BPS)
        )
        holding = initial / opening * (1 - dec(taker) / BPS) * closing * (1 - dec(taker) / BPS)
        return {
            "policy": variant,
            **({"entry_execution": entry_execution} if passive else {}),
            "economic_policy": economic_policy,
            "minutes": settings["candle_minutes"],
            "scenario": scenario,
            "start": start,
            "end": end,
            "settings": engine.settings,
            "maker_fee_bps": str(maker),
            "taker_fee_bps": str(taker),
            "pair_rules": pair.public(),
            "observed_minutes": processed,
            "missing_minutes": (end - start) // 60 - processed,
            "gate_counts": dict(gates),
            "distinct_candidates": signal_count,
            "entry_attempts": len(entries),
            "filled_entry_orders": len(fills),
            "unfilled_entry_orders": len(entries) - len(fills),
            "completed_trades": len(trades),
            "wins": sum(dec(t["net_pnl"]) > 0 for t in trades),
            "net_return_pct": str((net / initial - 1) * 100),
            "path_drawdown_pct": str(drawdown),
            "paid_fees": str(sum((dec(o.get("fee_quote_equivalent", 0)) for o in orders), ZERO)),
            "fees_by_currency": engine.ledger()["fees"],
            "ending_liquidation_equity": str(net),
            "retained_quantity": str(engine.balance(pair.base)),
            "time_in_market_pct": exposed / (processed * 4) * 100,
            "cash_return_pct": "0",
            "full_allocation_holding_return_pct": str((holding / initial - 1) * 100),
            "halted": engine.entries_halted or not engine.running,
            "halt_at": halt_at,
            "halt_reason": engine.last_error,
            "decision_reasons": dict(reasons),
            "daily_equity": daily,
            "trades": trades,
            "orders": orders,
            "dust": dust,
            "diagnostics": {
                "candidates": list(candidates.values()),
                "exits": exits,
                "economics": economics_summary(list(candidates.values()), exits),
                "clock_coverage": {
                    name: {
                        str(value): {
                            "expected_minutes": count,
                            "observed_minutes": coverage[name][value],
                            "missing_minutes": count - coverage[name][value],
                        }
                        for value, count in sorted(expected[name].items())
                    }
                    for name in expected
                },
            },
            "risk_units": {
                "closed_trades": len(closed_R),
                "mean_closed_R": str(sum(closed_R, ZERO) / len(closed_R)) if closed_R else None,
                "median_closed_R": str(median(closed_R)) if closed_R else None,
                "total_closed_R": str(sum(closed_R, ZERO)),
                "definition": "Sold-share net P&L / sold-share actual-fill planned stop risk; retained BTC is excluded, not written off.",
            },
            "risk": {
                "percent": str(risk_percent),
                "stop_management": "bot-managed",
                "native_attached_stops_supported_by_kairos": False,
                "guaranteed_loss_cap": False,
                "max_requested_stop_risk": str(
                    max((dec(o["risk_plan"]["requested_stop_risk"]) for o in entries), default=ZERO)
                ),
                "max_filled_stop_risk": str(
                    max((dec(o["risk_plan"]["filled_stop_risk"]) for o in entries), default=ZERO)
                ),
                "max_observed_position_loss": str(max_position_loss),
                "budget_breaches": len(risk_breaches),
                "breach_details": risk_breaches,
                "stop_execution_overruns": stop_overruns,
            },
            "activity": {
                "signals": signal_count,
                "setup_lifecycle": dict(lifecycle),
                "regimes": dict(regimes),
                "signals_per_day": signal_count / days,
                "filled_entries_per_day": len(fills) / days,
                "days_with_fills": len(filled_by_day),
                "study_days": days,
                "entry_fill_rate_pct": len(fills) / len(entries) * 100 if entries else 0,
                "median_gap_hours": median(gaps) if gaps else None,
                "max_gap_hours": max(gaps) if gaps else None,
                "longest_idle_hours": max(
                    b - a
                    for a, b in zip(
                        [start, *[o.get("first_fill_at", o["created"]) for o in fills]],
                        [*[o.get("first_fill_at", o["created"]) for o in fills], end],
                        strict=True,
                    )
                )
                / 3600,
                "missed_next_open": missed_open,
                "signals_by_day": dict(signals_by_day),
                "fills_by_day": dict(filled_by_day),
            },
        }
    finally:
        await engine.htf_review.close()
        for source in (minutes_rows, bars):
            close = getattr(source, "close", None)
            if close:
                close()
        store.close()


def execute_case(job):
    (
        archive_path,
        pair,
        settings,
        scenario,
        variant,
        start,
        end,
        maker,
        taker,
        risk_percent,
        economic_policy,
        entry_execution,
    ) = job
    started = time.monotonic()
    with ZipFile(archive_path) as archive:
        result = asyncio.run(
            simulate(
                archive_rows(archive, "XBTUSDT_1.csv", 1, start=start - 60, end=end),
                archive_rows(
                    archive,
                    f"XBTUSDT_{settings['candle_minutes']}.csv",
                    settings["candle_minutes"],
                    start=start - 2 * 86400,
                    end=end,
                ),
                pair,
                settings,
                scenario,
                variant,
                start,
                end,
                maker,
                taker,
                risk_percent=risk_percent,
                economic_policy=economic_policy,
                entry_execution=entry_execution,
            )
        )
    result["runtime_seconds"] = time.monotonic() - started
    return result


def prepare_data(source, destination, start, end, minutes):
    """Copy ONLY the selected pilot + warmup; never transfer the 8.4 GiB archive."""
    if destination.exists() or destination.with_suffix(".receipt.json").exists():
        raise SafetyError("Selected archive/receipt already exists")
    if source.resolve() == destination.resolve():
        raise SafetyError("Selection cannot replace source archive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    members = {}
    with ZipFile(source) as archive, ZipFile(destination, "x", ZIP_DEFLATED) as selected:
        for interval in sorted({1, *minutes}):
            member = f"XBTUSDT_{interval}.csv"
            count = 0
            with (
                selected.open(member, "w") as raw,
                io.TextIOWrapper(raw, encoding="utf-8", newline="") as text,
            ):
                writer = csv.writer(text)
                for row in archive_rows(
                    archive,
                    member,
                    interval,
                    start=start - (60 if interval == 1 else 2 * 86400),
                    end=end,
                ):
                    writer.writerow([*row[:5], row[6], row[7]])
                    count += 1
            info = archive.getinfo(member)
            members[member] = {
                "source_crc32": info.CRC,
                "source_bytes": info.file_size,
                "selected_rows": count,
            }
    destination.chmod(0o600)
    receipt = {
        "source": str(source),
        "source_size": source.stat().st_size,
        "start": start,
        "end": end,
        "native_warmup_days": 2,
        "members": members,
        "selected_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }
    with destination.with_suffix(".receipt.json").open("x") as file:
        file.write(json.dumps(receipt, indent=2))
    destination.with_suffix(".receipt.json").chmod(0o600)
    return receipt


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--start", type=date, default="2026-06-01")
    parser.add_argument(
        "--end",
        type=date,
        default="2026-06-08",
        help="Exclusive UTC date; one-week pilot by default",
    )
    parser.add_argument("--minutes", type=int, nargs="+", choices=(5, 15), default=[5, 15])
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=["base"])
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument(
        "--maker-fee-bps",
        type=decimal,
        default=dec(8),
        help="Assumed OKX rate, not account evidence",
    )
    parser.add_argument(
        "--taker-fee-bps",
        type=decimal,
        default=dec(10),
        help="Assumed OKX rate, not account evidence",
    )
    parser.add_argument("--initial", type=decimal, default=dec(1000))
    parser.add_argument(
        "--risk-percent",
        type=decimal,
        default=dec(1),
        help="Planned stop risk, fees/slippage included; at most 1%%",
    )
    parser.add_argument(
        "--order-cap",
        type=decimal,
        help="Defaults to starting capital; risk sizing may impose a smaller position",
    )
    parser.add_argument(
        "--exposure-cap", type=decimal, help="Defaults to starting capital; unleveraged spot only"
    )
    parser.add_argument(
        "--economic-policy",
        choices=ECONOMIC_POLICIES,
        default="diagnostic",
        help="Diagnostic preserves baseline entries; positive-target requires strictly positive modeled net reward at the frozen target. Research only, not expectancy or guaranteed profit.",
    )
    parser.add_argument("--entry-execution", choices=ENTRY_EXECUTIONS, default="ioc")
    parser.add_argument("--daily-loss", type=decimal, default=dec("12.50"))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--prepare-data",
        type=Path,
        metavar="PILOT.zip",
        help="Prepare a small transferable input ZIP only; do not simulate",
    )
    args = parser.parse_args()
    report, output, created = None, None, False
    try:
        if (
            args.start >= args.end
            or args.workers < 1
            or not ZERO <= args.maker_fee_bps <= args.taker_fee_bps <= 1000
            or not ZERO < args.risk_percent <= 1
        ):
            raise SafetyError("Invalid pilot interval, workers or fees")
        if args.prepare_data:
            receipt = prepare_data(
                args.archive, args.prepare_data, args.start, args.end, args.minutes
            )
            print(
                f"Selected pilot data: {args.prepare_data} ({args.prepare_data.stat().st_size} bytes)\nSHA-256: {receipt['selected_sha256']}"
            )
            return
        output = args.output or ROOT / "vwap-fibonacci" / (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + ".json"
        )
        if output.suffix != ".json" or output.exists() or output.with_suffix(".html").exists():
            raise SafetyError(
                "Use a new .json output path; its HTML companion must not already exist"
            )
        pair = instrument()
        with ZipFile(args.archive) as archive:
            members = {
                name: {"crc32": archive.getinfo(name).CRC, "bytes": archive.getinfo(name).file_size}
                for name in sorted({"XBTUSDT_1.csv", *(f"XBTUSDT_{m}.csv" for m in args.minutes)})
            }
        report = {
            "title": f"VWAP + Fibonacci · {args.economic_policy} / 1% planned risk",
            "scope_note": "Eight new research-only setups. Activity first; no parameter sweep, Jev calls or live strategy registration.",
            "trade_description": "Closed strategy episodes; retained sub-minimum BTC is reported separately, never written off. Full fills and native fees are in JSON.",
            "source": str(args.archive),
            "symbol": "BTC/USDT · Kraken inputs / OKX spot target",
            "target_venue": "OKX U.S. spot",
            "instrument_snapshot": json.loads(INSTRUMENT.read_text()),
            "risk_policy": {
                "initial_capital": str(args.initial),
                "planned_percent": str(args.risk_percent),
                "basis": "min(initial, current equity), reserve retained dust",
                "fees_slippage_included": True,
                "guaranteed": False,
            },
            "economic_policy": args.economic_policy,
            "economic_rule": "No economic rejection"
            if args.economic_policy == "diagnostic"
            else "(1-buy BTC fee) * target IOC bound * (1-sell USDT fee) - next-open entry IOC bound > 0; illustrative all-sold unit, not expected profit or a liquidation guarantee",
            "execution_model": "Offline best-bid post-only entry / taker IOC exit; NOT authorized live"
            if args.entry_execution != "ioc"
            else "KAIROS-supported cash IOC limits with bot-managed stops/targets; NOT native attached SL/TP",
            "status": "running",
            "start": args.start,
            "end": args.end,
            "broker_verified": False,
            "historical_final": False,
            "planned_cases": len(args.minutes) * len(args.variants) * len(args.scenarios),
            "members": members,
            "assumptions": [a for a in ASSUMPTIONS if not a.startswith("Next-open marketable")]
            + MAKER_ASSUMPTIONS
            if args.entry_execution != "ioc"
            else ASSUMPTIONS,
            "rules": RULES,
            "variant_labels": VARIANTS,
            "execution_revision": EXECUTION_REVISION,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "rules_sha256": hashlib.sha256(
                Path(__file__).with_name("research_signals.py").read_bytes()
            ).hexdigest(),
            "diagnostics_sha256": hashlib.sha256(
                Path(__file__).with_name("research_diagnostics.py").read_bytes()
            ).hexdigest(),
            "cases": [],
        }
        receipt_path = args.archive.with_suffix(".receipt.json")
        if receipt_path.exists():
            report["selection_receipt"] = json.loads(receipt_path.read_text())
            if (
                hashlib.sha256(args.archive.read_bytes()).hexdigest()
                != report["selection_receipt"]["selected_sha256"]
            ):
                raise SafetyError("Selected archive checksum differs from receipt")
        jobs = []
        for minutes in args.minutes:
            for variant in args.variants:
                for name in args.scenarios:
                    scenario = {**SCENARIOS[name], "name": name}
                    settings = pilot_settings(
                        pair,
                        minutes,
                        scenario,
                        args.initial,
                        args.order_cap if args.order_cap is not None else args.initial,
                        args.exposure_cap if args.exposure_cap is not None else args.initial,
                        args.daily_loss,
                    )
                    jobs.append(
                        (
                            args.archive,
                            pair,
                            settings,
                            scenario,
                            variant,
                            args.start,
                            args.end,
                            args.maker_fee_bps,
                            args.taker_fee_bps,
                            args.risk_percent,
                            args.economic_policy,
                            args.entry_execution,
                        )
                    )
        write_reports(report, output, create=True)
        created = True
        print(
            f"Small OKX-target pilot: {len(jobs)} cases, {int((args.end - args.start) / 86400)} days.\nHTML: {output.with_suffix('.html')}",
            flush=True,
        )
        with ProcessPoolExecutor(max_workers=min(args.workers, len(jobs))) as pool:
            for future in as_completed([pool.submit(execute_case, job) for job in jobs]):
                result = future.result()
                report["cases"].append(result)
                report["cases"].sort(
                    key=lambda c: (
                        args.minutes.index(c["minutes"]),
                        args.variants.index(c["policy"]),
                        args.scenarios.index(c["scenario"]["name"]),
                    )
                )
                if len(report["cases"]) == len(jobs):
                    report["status"] = "complete"
                write_reports(report, output, create=False)
                a = result["activity"]
                print(
                    f"{result['policy']} {result['minutes']}m: signals={a['signals']} attempts={result['entry_attempts']} filled={result['filled_entry_orders']} closed={result['completed_trades']} halted={result['halted']}",
                    flush=True,
                )
    except (SafetyError, OSError, KeyError, BadZipFile) as exc:
        if created:
            report.update(status="failed", error=str(exc))
            write_reports(report, output, create=False)
        parser.exit(1, f"Pilot failed: {exc}\n")


if __name__ == "__main__":
    main()
