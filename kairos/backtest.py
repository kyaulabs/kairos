"""Historical HTF comparisons using production rules and the local paper executor.

No credentials, network clients, model calls or exchange writes. Kraken candles
are price inputs, NOT reconstructed OKX quotes, trade prints or broker fills.
"""

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import time
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zipfile import BadZipFile, ZipFile

from kairos import htf
from kairos.backtest_report import render
from kairos.domain import BPS, ZERO, Book, Pair, SafetyError, dec, floor
from kairos.engine import EXECUTION_REVISION, Engine
from kairos.htf_review import HTFReview, native_rows
from kairos.market_data import CandleHistory
from kairos.settings import DEFAULTS, validate_settings
from kairos.store import Store

ROOT = Path.home() / ".local/share/kairos-research"
ARCHIVE = ROOT / "kraken-btc-eth-001/Kraken_OHLCVT_Full_2026Q2.zip"
POLICIES = ("pullback-v1", "multibar-v2")
SCENARIOS = {
    "base": {"path": "OLHC", "spread_bps": "2", "slippage_bps": "10", "participation": ".01"},
    "stress": {"path": "OHLC", "spread_bps": "10", "slippage_bps": "30", "participation": ".005"},
}
ASSUMPTIONS = [
    "Retrospective research, not an untouched holdout or profitability certification.",
    "Provider-native closed bars available at boundary +1s by assumption; not Kraken's production minute-shifted rolling windows.",
    "Production HTF entry/candidate/protection/sizing rules and Kraken local paper accounting.",
    "pullback-v1 approves every permitted entry; Jev choices/early exits are NOT reconstructed.",
    "multibar-v2 remains deterministic; its 14-day authorization is replaced by the research end.",
    "Assumed minute paths at seconds 1/20/40/59, not actual ticks or 15-second worker timing.",
    "Passive fills require strict subsequent path crossings before cancellation/expiry; no touch fills.",
    "All fills share liquidity capped by PREVIOUS completed minute volume times participation.",
    "Missing minutes/bars are not filled in; protection can act only at observed path points.",
    "Assumed constant fees/spreads/slippage and market increments, not historical OKX evidence.",
    "Fees debited in quote currency by the shared paper executor; not OKX base-fee settlement.",
    "Loss halts stay latched; no automatic restart, terminal liquidation or portfolio reset.",
]


def archive_rows(archive, member, minutes, *, start=None, end=None):
    """Stream validated Kraken's seven-column OHLCVT format without extracting it."""
    history = CandleHistory(None, minutes, 1)
    previous = None
    with archive.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8") as text:
        for number, fields in enumerate(csv.reader(text), 1):
            try:
                if len(fields) != 7:
                    raise ValueError("column count")
                ts = int(fields[0])
                if start is not None and ts < start:
                    continue
                if end is not None and ts >= end:
                    break
                row = history.validate(
                    [ts, *fields[1:5], "0", fields[5], int(fields[6])], vwap_optional=True
                )
                if ts % (minutes * 60) or previous is not None and ts <= previous:
                    raise ValueError("unordered or unaligned timestamp")
            except (ValueError, SafetyError) as exc:
                raise SafetyError(f"Invalid {member} row {number}: {exc}") from None
            previous = ts
            yield row


class HistoricalBook(Book):
    def fresh(self, seconds):
        # The synthetic clock belongs to the market, not the machine's wall clock.
        if self.market.now - self.received > seconds or self.bids[0][0] >= self.asks[0][0]:
            raise SafetyError("Invalid historical synthetic book")

    def fill(self, side, volume, limit):
        quote = self.asks[0][0] if side == "buy" else self.bids[0][0]
        if side == "buy" and limit < quote or side == "sell" and limit > quote:
            return ZERO, ZERO
        filled = self.market.consume(volume)
        return filled, filled * limit


class HistoricalMarket:
    allow_live = False
    hosted_paper = True
    native_history_policy = "offline-kraken-native-bars-proxy"

    def __init__(self, pair, scenario, maker, taker, minutes, bars):
        self.pair, self.pairs = pair, {pair.id: pair}
        self.scenario, self.maker, self.taker = scenario, maker, taker
        self.minutes, self.bars = minutes, iter(bars)
        self.next_bar = next(self.bars, None)
        self.completed = deque(maxlen=30)
        self.now, self.mid, self.remaining = 0, dec(1), ZERO
        self.market_data = self

    def advance(self, now):
        self.now = now
        while self.next_bar and self.next_bar[0] + self.minutes * 60 <= now:
            self.completed.append(self.next_bar)
            self.next_bar = next(self.bars, None)

    def consume(self, quantity):
        amount = floor(min(quantity, self.remaining), self.pair.lot)
        self.remaining -= amount
        return amount

    async def catalog(self):
        return self.pairs

    async def fees(self, pairs):
        return ({p.id: self.maker for p in pairs}, {p.id: self.taker for p in pairs})

    async def book(self, pair):
        half = dec(self.scenario["spread_bps"]) / (2 * BPS)
        book = HistoricalBook(
            pair,
            [(pair.price(self.mid * (1 - half), "buy"), self.remaining)],
            [(pair.price(self.mid * (1 + half), "sell"), self.remaining)],
            self.now,
        )
        book.market = self
        return book

    async def marks(self, pairs):
        return {p.id: (await self.book(p)).bids[0][0] for p in pairs}

    def snapshot(self):
        return {"status": "offline, assumed execution; no network"}


class HistoricalReview(HTFReview):
    """Production policy projection; explicit entry-permission ablation for Jev."""

    def __init__(self, engine):
        super().__init__(engine)
        self.view = None
        self.quote_sensitive = False
        self.counted_window = None

    async def refresh(self, pair, data):
        market = self.engine.kraken
        cutoff = self.window_end(market.now)
        if self.cutoff != cutoff:
            self.rows, self.available, self.required = native_rows(
                pair, list(market.completed), cutoff, market.minutes
            )
            self.cutoff = cutoff
            self.history_minutes = market.minutes
        self.status = (
            "Offline native history ready" if self.rows else "Offline native history incomplete"
        )
        return self.rows

    def entry_view(self, rows, pair, book):
        self.view = super().entry_view(rows, pair, book)
        view = self.view
        if self.engine.settings["htf_policy"] == "multibar-v2":
            self.quote_sensitive = bool(view["candidate"])
        else:
            gates = view["structural_gates"]
            values = view["gate_values"]
            close = dec(rows[-1][4])
            self.quote_sensitive = all(
                v
                for k, v in gates.items()
                if k not in {"recovery_close_and_quote", "no_chase_close_and_quote"}
            ) and dec(values["recovery_floor"]) <= close <= dec(values["chase_ceiling"])
        if self.counted_window != view["candle_close_time"]:
            counts = self.engine.gates
            counts["observed_windows"] += 1
            counts["structural_ready"] += int(view["pullback_long"])
            counts["entry_eligible_at_first_quote"] += int(view["entry_eligible"])
            for name, passed in view["structural_gates"].items():
                counts["failed:" + name] += int(not passed)
            self.counted_window = view["candle_close_time"]
        return view

    def take(self):
        now = self.engine.clock()
        if now < self.next_at:
            return None
        self.next_at = now + 2 * self.engine.settings["interval_seconds"]
        return {
            "action": htf.entry_side(self.view, "spot"),
            "confidence": 1,
            "probabilities": {},
            "deterministic": True,
            "model": "Multi-bar rules"
            if self.engine.settings["htf_policy"] == "multibar-v2"
            else "Offline permitted-entry ablation; NOT Jev",
            "allowed_actions": ["hold", "buy"],
            "observed_at": now,
            "expires_at": now + 30,
            "latency_ms": 0,
        }


class HistoricalEngine(Engine):
    """Compress observations; replace unavailable trade prints with a declared scenario."""

    def __init__(self, *args, end, **kwargs):
        self.end, self.reasons, self.gates = end, Counter(), Counter()
        self.candidates = set()
        super().__init__(*args, **kwargs)
        self.execution_purpose = "offline-current-htf-research"
        self.htf_review = HistoricalReview(self)

    def validate_capabilities(self, values):
        if values["strategy"] != "htf" or values["product"] != "spot":
            raise SafetyError("Historical runner supports spot HTF only")

    def tag_order(self, order):
        super().tag_order(order)
        order["reason"] = (self.latest_decision or {}).get("reason", "Offline HTF")

    def active_run(self):
        run = super().active_run()
        return {**run, "trial_ends_at": self.end} if run else None

    def emit_state(self):
        pass

    def event(self, kind, data):
        if kind == "decision":
            self.reasons[data.get("reason", "unknown")] += 1
            return None
        if kind == "candidate":
            self.candidates.add(data["id"])
        return super().event(kind, data)

    async def paper_makers(self):
        market = self.kraken
        for order in self.orders("dry-run", True):
            if not order["maker"]:
                raise SafetyError("Unexpected unfinished historical taker")
            if (
                order["created"] < market.now <= order["expires"]
                and market.scenario.get("passive", "cross") != "none"
                and (
                    market.mid < dec(order["price"])
                    if order["side"] == "buy"
                    else market.mid > dec(order["price"])
                )
            ):
                volume = market.consume(dec(order["volume"]) - dec(order["filled"]))
                filled = dec(order["filled"]) + volume
                cost = dec(order["cost"]) + volume * dec(order["price"])
                self.apply(
                    order,
                    filled,
                    cost,
                    cost * dec(order["fee_bps"]) / BPS,
                    "closed" if filled == dec(order["volume"]) else "open",
                )
        # Normal Engine.tick cancels the unfilled remainder, as in local paper execution.


def lineages(orders):
    positions = {}
    for order in orders:
        if dec(order["filled"]):
            positions.setdefault(order["htf_id"], []).append(order)
    result = []
    for fills in positions.values():
        quantity = sum((dec(o["filled"]) * (1 if o["side"] == "buy" else -1) for o in fills), ZERO)
        if quantity:
            continue
        result.append(
            {
                "opened_at": fills[0]["created"],
                "closed_at": fills[-1]["created"],
                "reason": fills[-1].get("reason"),
                "net_pnl": str(
                    sum(
                        (
                            dec(o["cost"]) * (1 if o["side"] == "sell" else -1) - dec(o["fee"])
                            for o in fills
                        ),
                        ZERO,
                    )
                ),
            }
        )
    return result


async def simulate(
    minutes_rows,
    native_bars,
    pair,
    settings,
    scenario,
    start,
    end,
    maker,
    taker,
    *,
    fast_forward=True,
):
    """Stream execution minutes; evaluate only closed native bars, preserving owned exits."""
    maker, taker = dec(maker), dec(taker)
    if not (ZERO <= maker <= 1000 and ZERO <= taker <= 1000) or start >= end:
        raise SafetyError("Invalid historical costs or period")
    market = HistoricalMarket(pair, scenario, maker, taker, settings["candle_minutes"], native_bars)
    store = Store(":memory:", clock=lambda: market.now)
    store.put("settings", settings)
    engine = HistoricalEngine(
        store,
        market,
        SimpleNamespace(key="offline-no-model"),
        lambda *_: None,
        clock=lambda: market.now,
        end=end,
    )
    initial = dec(settings["paper_balance"])
    cash, held, peak, drawdown, net = initial, ZERO, initial, ZERO, initial
    previous, last_window, first_price, last_price, halt_at = None, None, None, None, None
    active = False
    processed, held_points = 0, 0
    daily = {}
    try:
        for row in minutes_rows:
            ts = row[0]
            if ts < start:
                previous = row
                continue
            if ts >= end:
                break
            market.advance(ts + 1)
            market.mid = dec(row[1])
            if first_price is None:
                first_price = market.mid
                await engine.initialize()
                await engine.start()
            # Future/current minute volume cannot finance a hypothetical fill.
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
            window = engine.htf_review.window_end(ts)
            if (
                fast_forward
                and engine.running
                and not held
                and not active
                and window != last_window
            ):
                await engine.refresh_fees()
                review = engine.htf_review
                rows = await review.refresh(pair, {})
                if rows is not None:
                    book = await market.book(pair)
                    view = review.entry_view(rows, pair, book)
                    if not review.quote_sensitive and not view["entry_eligible"]:
                        state = htf.snapshot(engine)
                        context = htf.entry_context(engine, pair, view, book)
                        htf.observe_entry(engine, state, view, context)
                        engine.reasons[
                            "Closed structural gates block all quotes; flat fast-forward"
                        ] += 1
                        last_window = window
                else:
                    engine.reasons["Native history incomplete; entries blocked"] += 1
                    last_window = window
            for offset, price in zip((1, 20, 40, 59), points, strict=True):
                market.now, market.mid = ts + offset, dec(price)
                # Safe fast path: no quote can repair a closed structural failure.
                evaluate = (
                    held or active or engine.htf_review.quote_sensitive or window != last_window
                )
                if engine.running and evaluate:
                    await engine.tick()
                    cash, held = engine.balance(pair.quote), engine.balance(pair.base)
                    active = bool(engine.orders(active=True))
                    last_window = window
                    if not engine.running and halt_at is None:
                        halt_at = market.now
                if held:
                    bid = pair.price(
                        market.mid * (1 - dec(scenario["spread_bps"]) / (2 * BPS)), "buy"
                    )
                    net = cash + held * bid * (1 - dec(settings["slippage_bps"]) / BPS) * (
                        1 - taker / BPS
                    )
                    held_points += 1
                else:
                    net = cash
                peak = max(peak, net)
                drawdown = max(drawdown, (peak - net) / peak * 100)
            processed += 1
            last_price = dec(row[4])
            daily[str(ts // 86400 * 86400)] = str(net)
            previous = row
        if first_price is None:
            raise SafetyError("No execution minutes in the requested period")
        await engine.cancel_active()
        orders = engine.orders()
        trades = lineages(orders)
        # Full-allocation holding is a reference, NOT an exposure-matched alpha comparison.
        entry = (
            first_price
            * (1 + dec(scenario["spread_bps"]) / (2 * BPS))
            * (1 + dec(settings["slippage_bps"]) / BPS)
        )
        closing = (
            last_price
            * (1 - dec(scenario["spread_bps"]) / (2 * BPS))
            * (1 - dec(settings["slippage_bps"]) / BPS)
        )
        holding = initial / (entry * (1 + taker / BPS)) * closing * (1 - taker / BPS)
        return {
            "policy": settings["htf_policy"],
            "minutes": settings["candle_minutes"],
            "scenario": scenario,
            "start": start,
            "end": end,
            "settings": settings,
            "maker_fee_bps": str(maker),
            "taker_fee_bps": str(taker),
            "pair_rules": pair.public(),
            "observed_minutes": processed,
            "missing_minutes": (end - start) // 60 - processed,
            "gate_counts": dict(engine.gates),
            "distinct_candidates": len(engine.candidates),
            "entry_attempts": sum(o["side"] == "buy" for o in orders),
            "filled_entry_orders": sum(o["side"] == "buy" and dec(o["filled"]) > 0 for o in orders),
            "unfilled_entry_orders": sum(
                o["side"] == "buy" and not dec(o["filled"]) for o in orders
            ),
            "completed_trades": len(trades),
            "wins": sum(dec(t["net_pnl"]) > 0 for t in trades),
            "net_return_pct": str((net / initial - 1) * 100),
            "path_drawdown_pct": str(drawdown),
            "paid_fees": str(engine.ledger()["fees"].get(pair.quote, "0")),
            "ending_liquidation_equity": str(net),
            "retained_quantity": str(held),
            "time_in_market_pct": held_points / (processed * 4) * 100,
            "cash_return_pct": "0",
            "full_allocation_holding_return_pct": str((holding / initial - 1) * 100),
            "halted": not engine.running,
            "halt_at": halt_at,
            "halt_reason": engine.last_error,
            "decision_reasons": dict(engine.reasons),
            "daily_equity": daily,
            "trades": trades,
            "orders": orders,
        }
    finally:
        await engine.htf_review.close()
        for source in (minutes_rows, market.bars):
            close = getattr(source, "close", None)
            if close:
                close()
        store.close()


def execute_case(job):
    archive_path, symbol, pair, settings, scenario, start, end, maker, taker = job
    started = time.monotonic()
    minutes = settings["candle_minutes"]
    with ZipFile(archive_path) as archive:
        result = asyncio.run(
            simulate(
                archive_rows(archive, f"{symbol}_1.csv", 1, start=start - 60, end=end),
                archive_rows(
                    archive,
                    f"{symbol}_{minutes}.csv",
                    minutes,
                    start=start - minutes * 30 * 60,
                    end=end,
                ),
                pair,
                settings,
                scenario,
                start,
                end,
                maker,
                taker,
            )
        )
    result["runtime_seconds"] = time.monotonic() - started
    return result


def run_jobs(jobs, workers):
    if workers == 1 or len(jobs) == 1:
        yield from map(execute_case, jobs)
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            for future in as_completed([pool.submit(execute_case, job) for job in jobs]):
                yield future.result()


def write_reports(report, output, *, create):
    """Checkpoint only this run's report; never overwrite a pre-existing report."""
    html = render(report, output.name)
    mode = "x" if create else "w"
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path, content in (
        (output, json.dumps(report, indent=2, allow_nan=False)),
        (output.with_suffix(".html"), html),
    ):
        with path.open(mode, encoding="utf-8") as file:
            path.chmod(0o600)
            file.write(content)


def decimal(value):
    try:
        return dec(value)
    except SafetyError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def date(value):
    try:
        return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
    except ValueError:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD (UTC)") from None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument(
        "--symbol", default="XBTUSDT", help="Kraken archive symbol, e.g. XBTUSDT or ETHUSDT"
    )
    parser.add_argument("--start", type=date, default="2020-01-01", help="Inclusive UTC date")
    parser.add_argument("--end", type=date, default="2026-07-01", help="Exclusive UTC date")
    parser.add_argument(
        "--minutes", type=int, nargs="+", choices=(5, 15, 60, 240, 1440), default=[15, 60]
    )
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS))
    parser.add_argument(
        "--workers",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help="Isolated parallel simulations",
    )
    parser.add_argument("--maker-fee-bps", type=decimal, default=dec(20))
    parser.add_argument("--taker-fee-bps", type=decimal, default=dec(35))
    parser.add_argument("--initial", type=decimal, default=dec(500))
    parser.add_argument("--order-cap", type=decimal, default=dec(25))
    parser.add_argument("--exposure-cap", type=decimal, default=dec(100))
    parser.add_argument("--daily-loss", type=decimal, default=dec("12.50"))
    parser.add_argument("--tick", type=decimal, default=dec(".1"), help="Assumed market tick")
    parser.add_argument(
        "--lot", type=decimal, default=dec(".00000001"), help="Assumed quantity increment"
    )
    parser.add_argument(
        "--minimum", type=decimal, default=dec(".00001"), help="Assumed minimum quantity"
    )
    parser.add_argument(
        "--cost-minimum", type=decimal, default=ZERO, help="Assumed minimum order notional"
    )
    parser.add_argument(
        "--output", type=Path, help="New .json report path; paired branded .html written alongside"
    )
    args = parser.parse_args()
    created = False
    try:
        if (
            args.start >= args.end
            or min(args.tick, args.lot, args.minimum) <= 0
            or args.cost_minimum < 0
            or not ZERO <= args.maker_fee_bps <= args.taker_fee_bps <= 1000
            or args.workers < 1
        ):
            raise SafetyError("Invalid period or market increments")
        quote = next(
            (q for q in ("USDT", "USDC", "USD", "EUR", "GBP", "JPY") if args.symbol.endswith(q)),
            None,
        )
        if quote is None:
            raise SafetyError("Use a native archive symbol with a supported fiat/stablecoin quote")
        base = args.symbol[: -len(quote)]
        pair = Pair(
            "okx-research:" + args.symbol,
            base + "/" + quote,
            base,
            quote,
            args.tick,
            args.lot,
            args.minimum,
            args.cost_minimum,
        )
        output = args.output or ROOT / "htf-backtests" / (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + args.symbol + ".json"
        )
        if output.suffix != ".json":
            raise SafetyError("Use a .json output path; its paired .html report is automatic")
        if output.exists() or output.with_suffix(".html").exists():
            raise SafetyError(f"JSON or HTML report already exists: {output}")
        with ZipFile(args.archive) as archive:
            required = {f"{args.symbol}_1.csv", *(f"{args.symbol}_{m}.csv" for m in args.minutes)}
            members = {
                name: {"crc32": archive.getinfo(name).CRC, "bytes": archive.getinfo(name).file_size}
                for name in sorted(required)
            }
            report = {
                "source": str(args.archive),
                "symbol": args.symbol,
                "status": "running",
                "broker_verified": False,
                "jev_reconstructed": False,
                "historical_final": False,
                "planned_cases": len(args.minutes) * len(args.policies) * len(args.scenarios),
                "members": members,
                "assumptions": ASSUMPTIONS,
                "execution_revision": EXECUTION_REVISION,
                "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "cases": [],
            }
            print(
                f"Historical HTF research — simulated fills, not broker/Jev reconstruction\nHTML report: {output.with_suffix('.html')}",
                flush=True,
            )
            jobs = []
            for minutes in args.minutes:
                for policy in args.policies:
                    for name in args.scenarios:
                        scenario = {**SCENARIOS[name], "name": name}
                        settings = validate_settings(
                            {
                                **DEFAULTS,
                                "pair": pair.id,
                                "quote": pair.quote,
                                "strategy": "htf",
                                "product": "spot",
                                "htf_policy": policy,
                                "candle_minutes": minutes,
                                "paper_balance": str(args.initial),
                                "order_size": str(args.order_cap),
                                "max_exposure": str(args.exposure_cap),
                                "daily_loss": str(args.daily_loss),
                                "reinvest_profits": False,
                                "recover_initial": False,
                                "interval_seconds": 15,
                                "slippage_bps": scenario["slippage_bps"],
                            }
                        )
                        jobs.append(
                            (
                                args.archive,
                                args.symbol,
                                pair,
                                settings,
                                scenario,
                                args.start,
                                args.end,
                                args.maker_fee_bps,
                                args.taker_fee_bps,
                            )
                        )
            print(
                f"Running {len(jobs)} comparisons with {min(args.workers, len(jobs))} workers ...",
                flush=True,
            )
            for result in run_jobs(jobs, args.workers):
                report["cases"].append(result)
                report["cases"].sort(
                    key=lambda case: (
                        args.minutes.index(case["minutes"]),
                        args.policies.index(case["policy"]),
                        args.scenarios.index(case["scenario"]["name"]),
                    )
                )
                if len(report["cases"]) == report["planned_cases"]:
                    report["status"] = "complete"
                write_reports(report, output, create=not created)
                created = True
                print(
                    f"{result['policy']} / {result['minutes']}m / {result['scenario']['name']}: trades={result['completed_trades']} attempts={result['entry_attempts']} net={dec(result['net_return_pct']):+.2f}% drawdown={dec(result['path_drawdown_pct']):.2f}% fees={dec(result['paid_fees']):.2f} halted={result['halted']}",
                    flush=True,
                )
            print(f"HTML: {output.with_suffix('.html')}\nJSON: {output}")
    except (SafetyError, OSError, KeyError, BadZipFile) as exc:
        if created:
            report.update(status="failed", error=str(exc))
            write_reports(report, output, create=False)
        parser.exit(1, f"Backtest failed: {exc}\n")


if __name__ == "__main__":
    main()
