"""Hosted-paper reconciliation reusing Kairos's existing execution/risk engine."""

import asyncio
import time
import uuid

from kairos import diagnostics, htf, programs, scalping
from kairos.alpaca import iso
from kairos.alpaca_transport import PendingAlpacaAccount, PendingAlpacaData
from kairos.domain import ZERO, SafetyError, dec, floor
from kairos.engine import Engine
from kairos.observations import Observations
from kairos.operations import Operations
from kairos.settings import DEFAULTS
from kairos.strategies import limit_price


class AlpacaEngine(Engine):
    exchange = "alpaca"
    defaults = {
        **DEFAULTS,
        "quote": "USD",
        "pair": "alpaca:BTC/USD",
        "paper_balance": "500",
        "order_size": "100",
        "max_exposure": "100",
        "daily_loss": "12.50",
        "live_budget": "0",
        "recover_initial": False,
    }

    def __init__(self, store, client, jev, publish, *, clock=None, observe=False):
        if store.get("settings") is None:
            store.put("settings", self.defaults)
        super().__init__(store, client, jev, publish, clock=clock)
        self.mode, self.paper_armed = "paper", False
        self.market_session = None
        self.market_wait = None
        self.account_wait = None
        self.long_retry = None
        self.retry_status = None
        self.operations = Operations(self)
        self.observations = Observations(self)
        self.observations.enabled = observe
        self.account_read_status = {"last_success_at": None, "last_failure": None, "recovery": None}
        self.kraken.order_guard = lambda: (
            self.running
            and self.paper_armed
            and not self.shutting_down
            and not self.account_wait
            and not self.long_retry
            and not self.recovery_required
        )

    async def initialize(self):
        await self.kraken.catalog()
        self.resolve(self.settings["pair"])
        if not self.ledger():
            self.reset_ledger(self.mode, self.settings["paper_balance"])
        recovery = self.store.get("paper-qualification-exit")
        qualification = self.store.get("paper-qualification") or {}
        failed_qualification = (
            qualification.get("status") == "interrupted; manual reconciliation required"
            and not self.is_flat()
        )
        self.recovery_required = (
            bool(self.orders(active=True))
            or failed_qualification
            or bool(recovery and recovery["status"] != "settled")
        )
        if self.recovery_required:
            self.last_error = (
                "Qualification recovery settlement pending; Reconcile before Start"
                if failed_qualification or recovery and recovery["status"] != "settled"
                else "Alpaca paper orders remain after restart; Reconcile before Start"
            )
        await self.refresh_fees(required=False)
        self.ready = True
        self.event("system", {"message": "Alpaca hosted paper ready; paused, never live"})
        self.emit_state()

    def snapshot(self):
        return {
            **super().snapshot(),
            "exchange": "alpaca",
            "paper_armed": self.paper_armed,
            "paper_enabled": self.kraken.allow_paper,
            "recovery_required": self.recovery_required or bool(self.orders(active=True)),
            "market_session": self.market_session,
            "operations": self.operations.snapshot(),
            "scheduled_recovery": self.retry_status,
            "paper_qualification": self.store.get("paper-qualification"),
            "paper_qualification_recovery": self.store.get("paper-qualification-exit"),
            "diagnostic_observation": self.observations.latest,
            "account_reads": {
                **self.account_read_status,
                "recovery": self.account_recovery_snapshot(),
            },
            "market_wait": {"since": self.market_wait[0], "timeout_seconds": 300}
            if self.market_wait
            else None,
            "fee_note": self.kraken.fee_note,
            "broker_account": self.store.get("alpaca-account", {}).get("id"),
            "capabilities": {
                "crypto_spot": "Alpaca hosted paper; passive limits, no post-only guarantee",
                "us_stocks": "Alpaca hosted paper; listed stocks/ETFs, whole-share scheduled limits, regular sessions",
                "market_data": "Free crypto data and IEX-only equities; not consolidated equity quotes",
                "live_execution": "unavailable; paper host is fixed in code",
                "funding_policy": "Dedicated unused paper account, fixed local allocation, no transfers or account resets",
                "order_expiry": "Passive GTC orders need Kairos to cancel; a crash can leave them working until reconciliation",
            },
        }

    async def qualify_paper(self, confirmation):
        additional = confirmation == "ONE ADDITIONAL ALPACA PAPER ATTEMPT"
        if confirmation != "ONE ALPACA PAPER ROUND TRIP" and not additional:
            raise SafetyError("Explicit one-round-trip paper authorization required")
        generation = self.stop_generation
        async with self.lock:
            previous = self.store.get("paper-qualification")
            orders = self.orders()
            if additional:
                permitted = bool(
                    previous
                    and not previous.get("retry_of")
                    and len(orders) == 1
                    and orders[0]["run_id"] == previous.get("run_id")
                    and orders[0]["side"] == "buy"
                    and orders[0]["maker"]
                    and orders[0]["status"] == "canceled"
                    and dec(orders[0]["filled"]) == 0
                )
                if not permitted:
                    raise SafetyError(
                        "One additional attempt requires the original tracked buy confirmed canceled and unfilled"
                    )
            elif previous or orders:
                raise SafetyError("Qualification already claimed; no automatic repeat")
            if (
                not self.ready
                or self.running
                or self.shutting_down
                or self.recovery_required
                or not self.is_flat()
                or htf.snapshot(self).get("position")
                or htf.snapshot(self).get("entry_attempt")
                or self.settings["strategy"] != "htf"
                or self.settings["htf_policy"] != "pullback-v1"
                or self.settings["pair"] != "alpaca:BTC/USD"
                or not self.kraken.allow_paper
            ):
                raise SafetyError(
                    "Qualification requires a paused, reconciled, unused flat BTC paper run with legacy policy"
                )
            await self.reconcile_account()
            await self.refresh_fees()
            await self.valuation(True)
            if self.stop_generation != generation or self.shutting_down:
                raise SafetyError("Qualification canceled by Stop")
            pair = self.resolve(self.settings["pair"])
            book = await self.kraken.book(pair)
            book.fresh(self.settings["stale_seconds"])
            price = pair.price(book.bids[0][0], "buy")
            budget = min(dec(25), self.limits()[0], self.balance("USD"))
            volume = floor(budget / (price * (1 + self.fees.reserve(pair) / 10000)), pair.lot)
            if volume < 2 * htf.minimum_volume(pair, price):
                raise SafetyError(
                    "Qualification budget cannot leave a tradeable exit after fees/rounding"
                )
            if self.stop_generation != generation or self.shutting_down:
                raise SafetyError("Qualification canceled by Stop")
            before = self.ledger()
            record = {
                "id": str(uuid.uuid4()),
                "status": "started",
                "at": self.clock(),
                "cash_before": str(self.balance("USD")),
                "budget": str(budget),
                "market_rules": {
                    k: str(getattr(pair, k)) for k in ("tick", "lot", "minimum", "cost_minimum")
                },
            }
            if additional:
                record["retry_of"] = previous["id"]
            # Retain the original failed claim and atomically claim the explicitly authorized retry.
            with self.store.db:
                if additional:
                    self.store._put("paper-qualification:" + previous["id"], previous)
                self.store._put("paper-qualification", record)
            self.running, self.paper_armed = True, True
            try:
                self.execution_purpose = (
                    f"execution-qualification:{record['id']}; excluded from strategy trial"
                )
                run = self.ensure_run()
                record["run_id"] = run["id"]
                self.update_operating_state()
                self.emit_state()
                plan = {
                    "id": record["id"],
                    "side": "buy",
                    "entry_limit": str(price),
                    "stop": str(price * dec(".97")),
                    "opened_at": self.clock(),
                    "deadline": self.clock() + 3600,
                    "exit_reason": None,
                }
                state = htf.snapshot(self)
                state.update(position=None, entry_attempt={"position": plan, "order_id": None})
                self.store.put(htf.key(self), state)
                self.event(
                    "qualification",
                    {"message": "Authorized one paper round trip; not a strategy signal", **record},
                )
                await self.place(pair, "buy", volume, price, book, maker=True)
                deadline = time.monotonic() + 60
                while self.orders(active=True) and time.monotonic() < deadline:
                    if self.stop_generation != generation or not self.running or self.shutting_down:
                        raise SafetyError(
                            "Qualification canceled by Stop; tracked order needs reconciliation"
                        )
                    await asyncio.sleep(3)
                    # A working order can reserve cash. Poll its confirmed execution,
                    # not a cross-endpoint account snapshot that assumes settled cash.
                    for order in self.orders(active=True):
                        await self.refresh_order(order)
                    htf.reconcile_entry(self)
                if self.orders(active=True):
                    await self.cancel_active()
                await self.reconcile_account()
                if self.stop_generation != generation or not self.running or self.shutting_down:
                    raise SafetyError("Qualification canceled by Stop; no exit submitted")
                held = htf.quantity(self, pair)
                record["entry_filled"] = str(
                    sum(dec(o["filled"]) for o in self.orders() if o["side"] == "buy")
                )
                record["entry_owned"] = str(held)
                if held:
                    book = await self.kraken.book(pair)
                    price = limit_price(book, "sell", self.settings["slippage_bps"])
                    volume = floor(min(held, self.limits()[0] / price), pair.lot)
                    if htf.below_minimum(pair, volume, price):
                        raise SafetyError(
                            "Qualification residual cannot be sold; retained, no dust-clearing buy"
                        )
                    await self.place(pair, "sell", volume, price, book, exit_only=True)
                    await self.reconcile_account()
                if not htf.quantity(self, pair):
                    saved = htf.snapshot(self)
                    saved["position"] = None
                    self.store.put(htf.key(self), saved)
                record.update(
                    status=("complete" if self.is_flat() else "residual retained")
                    if held
                    else "no entry fill; round trip untested",
                    cash_after=str(self.balance("USD")),
                    ended_at=self.clock(),
                )
                if self.ledger()["initial"] != before["initial"]:
                    raise SafetyError("Qualification allocation baseline changed")
            except (Exception, asyncio.CancelledError):
                record.update(
                    status="interrupted; manual reconciliation required", ended_at=self.clock()
                )
                self.recovery_required = True
                self.last_error = (
                    "Paper qualification interrupted; Reconcile and review the recorded attempt"
                )
                raise
            finally:
                self.execution_purpose = None
                self.running, self.paper_armed = False, False
                self.recovery_required = self.recovery_required or bool(self.orders(active=True))
                self.store.put("paper-qualification", record)
                self.event(
                    "qualification",
                    {"message": "Qualification ended; paused and unarmed", **record},
                )
                self.update_operating_state()
                if self.operations.alerts:
                    self.operations.alerts.send("execution qualification ended; engine paused")
                self.emit_state()

    def event(self, kind, data):
        try:
            super().event(kind, data)
        except Exception:
            self.running = False
            self.recovery_required = True
            self.long_retry = None
            self.last_error = "Audit/storage failure; manual recovery required"
            if hasattr(self, "operations"):
                self.operations.failed()
            raise

    def is_flat(self):
        return not any(dec(q) for asset, q in self.ledger()["balances"].items() if asset != "USD")

    def schedule_retry(self, exc):
        if not (
            self.settings["api_auto_recovery"]
            and self.running
            and not self.shutting_down
            and self.account_retry_safe()
            and self.is_flat()
        ):
            return False
        self.long_retry = (self.stop_generation, time.monotonic() + 300)
        self.retry_status = {
            "status": "waiting",
            "since": self.clock(),
            "attempts": 0,
            "max_attempts": 5,
            "interval_seconds": 300,
            "next_at": self.clock() + 300,
            "reason": str(exc),
        }
        self.market_wait = None
        self.htf_review.cancel()
        self.last_error = "Temporary API/data interruption; flat-account recovery scheduled (0/5). Orders blocked."
        self.event("scheduled-recovery", {"message": self.last_error, **self.retry_status})
        return True

    async def recover_scheduled(self):
        generation, due = self.long_retry
        if not self.running or self.shutting_down or generation != self.stop_generation:
            self.long_retry = None
            return
        if not self.account_retry_safe() or not self.is_flat():
            raise SafetyError(
                "Automatic recovery blocked: holdings, orders, loss or reconciliation latch"
            )
        if time.monotonic() < due:
            return
        self.retry_status["attempts"] += 1
        try:
            async with asyncio.timeout(60):
                await self.valuation(True)
                if not self.is_flat():
                    raise SafetyError("Automatic recovery found holdings; manual review required")
                equities = any(self.kraken.is_equity(p) for p in self.fee_pairs())
                self.market_session = await self.kraken.clock() if equities else None
                for pair in self.fee_pairs():
                    book = await self.kraken.book(pair)
                    book.fresh(self.settings["stale_seconds"])
        except (PendingAlpacaAccount, PendingAlpacaData, TimeoutError) as exc:
            if not self.running or self.shutting_down or generation != self.stop_generation:
                self.long_retry = None
                return
            self.retry_status.update(
                reason=str(exc) or "Recovery read timed out", last_attempt_at=self.clock()
            )
            self.event(
                "scheduled-recovery",
                {"message": "Read-only recovery attempt failed", **self.retry_status},
            )
            if self.retry_status["attempts"] >= 5:
                raise SafetyError(
                    "Five scheduled recovery attempts exhausted; manual Reconcile/Restart required"
                ) from exc
            self.long_retry = (generation, time.monotonic() + 300)
            self.retry_status["next_at"] = self.clock() + 300
            return
        if not self.running or self.shutting_down or generation != self.stop_generation:
            self.long_retry = None
            return
        self.retry_status.update(status="restored", ended_at=self.clock(), next_at=None)
        if self.settings["strategy"] == "htf":
            htf.arm(self)
        self.event(
            "scheduled-recovery",
            {
                "message": "Fresh account and market checks passed; next cycle rechecks strategy, no order replay",
                **self.retry_status,
            },
        )
        self.long_retry = None
        self.last_error = None

    def update_operating_state(self):
        status = (
            ("halted" if self.last_error or self.recovery_required else "paused")
            if not self.running
            else (
                "qualifying"
                if getattr(self, "execution_purpose", None)
                else "retry-wait"
                if self.long_retry
                else "waiting-account"
                if self.account_wait
                else "waiting-data"
                if self.market_wait
                else "running"
            )
        )
        self.operations.set(status, self.last_error)

    async def tick(self):
        try:
            await self._tick()
        finally:
            self.update_operating_state()
            self.emit_state()
        try:
            await self.observations.sample()
        except Exception as exc:
            diagnostics.capture(exc, "read-only-observer")
            # No recovery permission is created by the recorder. Event writes fail closed.
            self.event(
                "observation-error",
                {
                    "message": "Read-only diagnostic observation unavailable",
                    "exception_type": type(exc).__name__,
                },
            )

    def validate_capabilities(self, values):
        if (
            values["strategy"] == "htf"
            and values["htf_policy"] == "multibar-v2"
            and (
                values["pair"] != "alpaca:BTC/USD"
                or values["candle_minutes"] != 60
                or dec(values["paper_balance"]) > 500
                or dec(values["order_size"]) > 25
                or dec(values["max_exposure"]) > 100
                or dec(values["daily_loss"]) > dec("12.50")
                or values["stale_seconds"] > 10
                or dec(values["slippage_bps"]) > 10
                or dec(values["max_spread_bps"]) > 30
                or dec(values["htf_stop_bps"]) != 300
                or values["htf_max_hold_seconds"] != 604800
                or values["reinvest_profits"]
                or values["recover_initial"]
            )
        ):
            raise SafetyError(
                "Multi-bar trial requires BTC/USD hourly bars and the frozen trial risk limits"
            )
        if (
            values["product"] != "spot"
            or values["strategy"] == "arbitrage"
            or values["recover_initial"]
        ):
            raise SafetyError(
                "Alpaca supports unleveraged USD paper spot only; no arbitrage or capital recovery"
            )
        pair = self.resolve(values["pair"])
        if self.kraken.is_equity(pair) and values["strategy"] not in programs.STRATEGIES:
            raise SafetyError("Alpaca equities support DCA, TWAP and rebalancing only")
        if self.store.get("alpaca-account") and dec(values["paper_balance"]) != dec(
            self.ledger()["initial"]
        ):
            raise SafetyError("The hosted paper allocation cannot be silently resized or reset")

    def save_settings(self, settings):
        ledger = self.ledger()
        if not self.store.get("alpaca-account"):
            if self.orders() or any(dec(q) for a, q in ledger["balances"].items() if a != "USD"):
                raise SafetyError("Cannot resize an unreconciled Alpaca allocation")
            ledger["balances"]["USD"] = settings["paper_balance"]
            ledger["initial"] = settings["paper_balance"]
            ledger["recovery"]["original"] = settings["paper_balance"]
        with self.store.db:
            self.store._put("settings", settings)
            self.store._put("ledger:paper", ledger)

    async def set_mode(self, mode, confirmation):
        raise SafetyError(
            "Alpaca is hosted-paper only; live and local dry-run modes are unavailable"
        )

    def paper_history_restriction(self, order, operation, *, active_paper=None):
        return "Hosted-paper order history is retained for broker reconciliation"

    async def reset_paper(self):
        raise SafetyError(
            "Kairos never resets an Alpaca account or discards its reconciliation ledger"
        )

    async def live_preflight(self):
        raise SafetyError("Live Alpaca trading is not implemented")

    async def start(self, *, restart=False, confirmation=None):
        if self.long_retry:
            raise SafetyError("Scheduled recovery pending; Stop cancels it before manual Start")
        run = self.active_run() or {}
        if self.settings["strategy"] == "htf" and self.settings["htf_policy"] == "multibar-v2":
            from kairos.multibar import PROTOCOL_HASH

            qualification = self.store.get("paper-qualification") or {}
            if qualification.get("status") != "complete":
                raise SafetyError(
                    "Complete the separately authorized paper round trip before trial Start"
                )

            trial = self.store.get("multibar-trial:" + PROTOCOL_HASH) or {}
            if not trial and (not self.is_flat() or self.orders(active=True)):
                raise SafetyError(
                    "First trial Start requires flat, reconciled qualification holdings"
                )
            if self.is_flat() and self.clock() >= trial.get(
                "ends_at", run.get("trial_ends_at", float("inf"))
            ):
                raise SafetyError(
                    "Registered trial ended; no new entries. A new trial needs separate registration"
                )
        generation = self.stop_generation
        if not self.kraken.allow_paper:
            raise SafetyError(
                "Set ALLOW_ALPACA_PAPER_TRADING=true before submitting hosted paper orders"
            )
        expected = "RESTART ALPACA PAPER" if restart else "START ALPACA PAPER"
        if not self.paper_armed and confirmation != expected:
            raise SafetyError(f"Explicit {expected} confirmation is required")
        async with self.lock:
            self.validate_capabilities(self.settings)
            await self.reconcile_account(adopt=True)
            if self.stop_generation != generation:
                raise SafetyError("Start canceled by Stop")
            self.paper_armed = True
        await super().start(restart=restart, confirmation="RESTART ENGINE" if restart else None)
        self.market_wait = None
        self.update_operating_state()
        self.emit_state()

    async def stop(self):
        if (
            self.long_retry or self.market_wait or self.account_wait
        ) and not self.recovery_required:
            self.last_error = None
        if self.long_retry:
            self.retry_status.update(status="canceled by Stop", ended_at=self.clock())
        self.long_retry = None
        self.market_wait = None
        await super().stop()
        self.finish_account_recovery("stopped")
        self.update_operating_state()
        self.emit_state()

    async def reconcile(self, acknowledge=False):
        if self.long_retry:
            self.retry_status.update(status="canceled by Reconcile", ended_at=self.clock())
        self.long_retry = None
        await super().reconcile(acknowledge)
        self.finish_account_recovery("reconciled")
        recovery = self.store.get("paper-qualification-exit")
        if recovery and recovery["status"] != "settled":
            if self.is_flat() and not self.orders(active=True):
                recovery.update(status="settled", settled_at=self.clock())
                self.store.put("paper-qualification-exit", recovery)
                self.event("qualification-recovery", recovery)
            else:
                self.recovery_required = True
                self.last_error = (
                    "Qualification recovery retains holdings; review before another authorization"
                )
        self.update_operating_state()
        self.emit_state()

    def account_recovery_snapshot(self):
        result = self.account_read_status["recovery"]
        if result is not None:
            result = dict(result)
            if self.account_wait:
                result["duration_seconds"] = round(time.monotonic() - self.account_wait[0], 3)
        return result

    def finish_account_recovery(self, status):
        if not self.account_wait:
            return
        result = self.account_recovery_snapshot()
        result.update(status=status, ended_at=self.clock())
        self.account_wait = None
        self.account_read_status["recovery"] = result
        self.account_read_event(
            {
                "message": f"Alpaca account-read recovery {status}",
                **result,
                "last_success_at": self.account_read_status["last_success_at"],
            },
        )

    def account_read_event(self, data):
        try:
            self.event("account-read", data)
        except Exception:
            self.running = False
            self.recovery_required = True
            self.last_error = "Account-read audit storage failed; Reconcile and Restart required"
            result = self.account_recovery_snapshot()
            if result:
                result.update(status="halted", ended_at=self.clock())
                self.account_read_status["recovery"] = result
            self.account_wait = None
            raise  # Audit/storage failures must not leave automatic execution permitted.

    def account_retry_safe(self):
        return (
            self.paper_armed
            and not self.recovery_required
            and not self.orders(active=True)
            and not self.store.get("cycle")
            and not (
                self.daily_pnl is not None
                and -dec(self.daily_pnl) >= dec(self.settings["daily_loss"])
            )
        )

    def wait_for_account_data(self, exc):
        if not self.running or self.shutting_down:
            self.finish_account_recovery("stopped")
            return True  # A read finishing after Stop cannot renew permission.
        failure = {**exc.details, "at": self.clock()}
        self.account_read_status["last_failure"] = failure
        if self.account_wait:
            self.account_read_status["recovery"].update(failure)
        now = time.monotonic()
        if (
            not self.account_retry_safe()
            or self.account_wait
            and (
                now - self.account_wait[0] >= 120
                or self.account_read_status["recovery"]["attempts"] >= 3
            )
        ):
            if self.schedule_retry(exc):
                self.finish_account_recovery("scheduled retry")
                return True
            self.recovery_required = True
            exc.args = (
                str(exc)
                + "; automatic read recovery unavailable or exhausted; Reconcile before Start",
            )
            self.finish_account_recovery("halted")
            return False
        if not self.account_wait:
            self.account_wait = (now, now + 15)
            self.account_read_status["recovery"] = {
                "status": "waiting",
                "since": self.clock(),
                "attempts": 0,
                "max_attempts": 3,
                "timeout_seconds": 120,
            }
        else:
            self.account_wait = (self.account_wait[0], now + 15)
        self.account_read_status["recovery"].update(failure, next_retry_at=self.clock() + 15)
        self.htf_review.cancel()
        self.last_error = (
            str(exc) + "; bounded account reconciliation pending. Orders and local exits blocked."
        )
        self.account_read_event(
            {
                "message": self.last_error,
                **self.account_recovery_snapshot(),
                "last_success_at": self.account_read_status["last_success_at"],
            },
        )
        return True

    def expire_account_wait(self):
        failure = self.account_read_status["last_failure"] or {}
        if self.schedule_retry(
            PendingAlpacaAccount(
                failure.get("endpoint", "/v2/account"), "short recovery deadline exhausted"
            )
        ):
            self.finish_account_recovery("scheduled retry")
            return
        raise SafetyError("Alpaca account-read recovery deadline exhausted; Reconcile before Start")

    async def recover_account_reads(self):
        if not self.account_retry_safe():
            raise SafetyError(
                "Account-read recovery blocked by loss, orders or reconciliation latch"
            )
        now = time.monotonic()
        remaining = 120 - (now - self.account_wait[0])
        if remaining <= 0:
            return self.expire_account_wait()
        if now < self.account_wait[1]:
            return
        self.account_read_status["recovery"]["attempts"] += 1
        try:
            async with asyncio.timeout(remaining):
                # Full reconciliation plus current loss/session checks, never a broker write.
                await self.valuation(True)
                equities = any(self.kraken.is_equity(p) for p in self.fee_pairs())
                self.market_session = await self.kraken.clock() if equities else None
        except TimeoutError:
            return self.expire_account_wait()
        if not self.running or self.shutting_down:
            self.finish_account_recovery("stopped")
            return
        if time.monotonic() - self.account_wait[0] >= 120:
            return self.expire_account_wait()
        if self.settings["strategy"] == "htf":
            htf.arm(self)  # Retain ownership/protection; discard outage-era entry approval.
        self.finish_account_recovery("restored")
        if not self.market_wait:
            self.last_error = None
        # Normal strategy/freshness checks run on the next cycle; never replay an intent.

    def wait_for_market_data(self, exc):
        if not self.running or self.shutting_down:
            return True  # A concurrent explicit Stop wins; never turn running back on.
        if (
            not self.paper_armed
            or self.recovery_required
            or self.orders(active=True)
            or self.daily_pnl is not None
            and -dec(self.daily_pnl) >= dec(self.settings["daily_loss"])
        ):
            return False
        now = time.monotonic()
        if self.market_wait and now - self.market_wait[1] >= 300:
            return self.schedule_retry(
                exc
            )  # Bounded read recovery exhausted; explicit Restart required.
        if not self.market_wait:
            self.market_wait = (self.clock(), now)
            self.event(
                "data-wait",
                {
                    "message": "Waiting for fresh Alpaca market data; no stale-data orders, local exits may be blocked",
                    "reason": str(exc),
                    "since": self.clock(),
                    "feed": self.kraken.market_data.snapshot(),
                },
            )
        self.htf_review.cancel()
        self.last_error = (
            str(exc)
            + "; waiting up to five minutes for fresh data. No stale-data orders; local exits may be blocked."
        )
        return True

    def tag_order(self, order):
        super().tag_order(order)
        order.update(
            exchange="alpaca",
            fee_reported=False,
            planning_fee_bps=str(self.fees.reserve(self.resolve(order["pair"]), order["maker"])),
            fee_note="Order API omits fees; posted account activities are reconciled separately",
            execution="passive-limit" if order["maker"] else "ioc-limit",
            exchange_post_only=False,
            expiry_source="local cancellation; GTC can remain working while Kairos is offline"
            if order["maker"]
            else "broker IOC",
        )

    async def refresh_order(self, order):
        if order["mode"] != "paper":
            raise SafetyError("Unexpected order mode in Alpaca ledger")
        if not order["txid"]:
            order["txid"], row = await self.kraken.find_order(order["id"], order["created"])
            self.store.save_order(order)
        else:
            row = await self.kraken.query(order["txid"])
        raw = row["raw"]
        pair = self.resolve(order["pair"])
        if (
            raw["id"] != order["txid"]
            or raw["client_order_id"] != order["id"]
            or raw["symbol"].replace("/", "") != self.kraken.symbol(pair).replace("/", "")
            or raw["side"] != order["side"]
            or dec(raw["qty"]) != dec(order["volume"])
            or raw["type"] != "limit"
            or dec(raw["limit_price"]) != dec(order["price"])
        ):
            raise SafetyError("Alpaca order identity/terms changed; manual reconciliation required")
        self.apply(order, dec(row["vol_exec"]), dec(row["cost"]), ZERO, row["status"])
        return order

    def apply_fee(self, row):
        marker = "alpaca-activity:" + row["id"]
        prior = self.store.get(marker)
        if prior is not None:
            if prior != row:
                raise SafetyError("Alpaca activity was corrected; manual reconciliation required")
            return
        kind = row["activity_type"]
        if row.get("status") != "executed":
            raise SafetyError("Alpaca fee is not an executed activity")
        currency, amount = "USD", dec(row["net_amount"])
        if kind == "CFEE" and dec(row.get("qty", 0)):
            symbol = row.get("symbol", "").replace("/", "")
            pair = next(
                (
                    p
                    for p in self.kraken.pairs.values()
                    if self.kraken.symbol(p).replace("/", "") == symbol
                ),
                None,
            )
            if pair is None or self.kraken.is_equity(pair) or amount:
                raise SafetyError("Unsupported Alpaca crypto fee denomination")
            currency, amount = pair.base, dec(row["qty"])
        if amount > 0 or row.get("currency", currency) != currency:
            raise SafetyError("Unexpected Alpaca fee credit/currency")
        ledger = self.ledger()
        balance = dec(ledger["balances"].get(currency, 0)) + amount
        if balance < 0:
            raise SafetyError("Alpaca fee exceeds owned allocation; reconciliation required")
        ledger["balances"][currency] = str(balance)
        ledger["fees"][currency] = str(dec(ledger["fees"].get(currency, 0)) - amount)
        plans = []
        if currency != "USD":
            # Fee debits reduce owned inventory, not the gross broker fill quantities.
            for key in (htf.key(self), scalping.key(self)):
                state = self.store.get(key)
                if state and self.resolve(self.settings["pair"]).base == currency:
                    positions = [
                        state.get("position"),
                        state.get("entry_attempt", {}).get("position"),
                    ]
                    for position in positions:
                        if position:
                            position["inventory_adjustment"] = str(
                                dec(position.get("inventory_adjustment", 0)) + amount
                            )
                    plans.append((key, state))
        with self.store.db:
            self.store._put("ledger:paper", ledger)
            for key, value in plans:
                self.store._put(key, value)
            self.store._put(marker, row)
        self.event(
            "account-fee",
            {
                "activity_id": row["id"],
                "currency": currency,
                "amount": str(-amount),
                "attribution": "Account activity; not attributed to a particular order/run",
            },
        )

    @staticmethod
    def is_initial_funding(rows, cash):
        """One completed cash journal matching the unused account's initial cash."""
        if len(rows) != 1:
            return False
        row = rows[0]
        return (
            row.get("activity_type") == "JNLC"
            and row.get("status") == "executed"
            and row.get("currency", "USD") == "USD"
            and not row.get("symbol")
            and not dec(row.get("qty") or 0)
            and dec(row.get("net_amount")) == dec(cash) > ZERO
        )

    async def reconcile_initial_funding(self, activity_id, confirmation):
        if not activity_id or confirmation != "ACKNOWLEDGE INITIAL PAPER FUNDING":
            raise SafetyError("Explicit initial paper funding activity and confirmation required")
        generation = self.stop_generation
        async with self.lock:

            def guard():
                if (
                    not self.ready
                    or self.running
                    or self.shutting_down
                    or self.mode != "paper"
                    or self.recovery_required
                    or self.orders()
                    or self.store.get("cycle")
                    or self.stop_generation != generation
                ):
                    raise SafetyError(
                        "Initial funding reconciliation requires a paused unused account"
                    )

            guard()
            identity, ledger = self.store.get("alpaca-account"), self.ledger()
            if not identity or identity["baseline_activities"]:
                raise SafetyError(
                    "Initial funding acknowledgement requires an empty saved baseline"
                )
            initial = dec(ledger["initial"])
            if self.balance("USD") != initial or any(
                dec(q) for a, q in ledger["balances"].items() if a != "USD"
            ):
                raise SafetyError("Initial funding cannot change an existing portfolio")
            cash = initial + dec(identity["unallocated_cash"])
            # Two matching read passes catch changes during operator reconciliation.
            observed = None
            for _ in range(2):
                account = await self.kraken.account()
                positions = await self.kraken.positions()
                orders = await self.kraken.request(
                    "GET", "/v2/orders", params={"status": "all", "limit": 1}
                )
                rows = await self.kraken.activities(identity["activities_after"])
                if (
                    account["id"] != identity["id"]
                    or dec(account["cash"]) != cash
                    or positions
                    or orders
                    or not self.is_initial_funding(rows, cash)
                    or rows[0]["id"] != activity_id
                    or observed is not None
                    and observed != rows
                ):
                    raise SafetyError(
                        "Initial funding evidence does not match the saved unused account"
                    )
                observed = rows
            guard()
            if self.store.get("alpaca-account") != identity or self.ledger() != ledger:
                raise SafetyError("Saved account changed during initial funding reconciliation")
            # Retain the full row: subsequent corrections still fail normal reconciliation.
            updated = {**identity, "baseline_activities": {activity_id: observed[0]}}
            with self.store.db:
                self.store._put("alpaca-account", updated)
                self.event(
                    "account-reconciliation",
                    {
                        "message": "Operator acknowledged delayed initial paper funding; trading remains paused",
                        "activity_id": activity_id,
                        "activity_type": "JNLC",
                        "amount": str(cash),
                        "previous_baseline": identity["baseline_activities"],
                    },
                )
            self.emit_state()

    async def reconcile_account(self, *, adopt=False):
        identity = self.store.get("alpaca-account")
        if not identity and not adopt:
            raise SafetyError("Press Start to validate and bind the dedicated Alpaca paper account")
        account = await self.kraken.account()
        if not identity:
            positions = await self.kraken.positions()
            previous = await self.kraken.request(
                "GET", "/v2/orders", params={"status": "all", "limit": 1}
            )
            if positions or previous:
                raise SafetyError(
                    "First connection requires a dedicated unused Alpaca paper account; Kairos will not adopt existing orders/holdings"
                )
            if self.orders():
                raise SafetyError("Alpaca account identity missing for existing local orders")
            allocation = dec(self.settings["paper_balance"])
            available = min(dec(account["cash"]), dec(account["non_marginable_buying_power"]))
            if allocation > available:
                raise SafetyError("Alpaca paper cash is below the configured allocation")
            after = iso(int(self.clock()) // 86400 * 86400)
            baseline = await self.kraken.activities(after)
            if any(r["activity_type"] != "CSD" for r in baseline) and not self.is_initial_funding(
                baseline, account["cash"]
            ):
                raise SafetyError("First Alpaca connection requires an unused paper account")
            identity = {
                "id": account["id"],
                "unallocated_cash": str(dec(account["cash"]) - allocation),
                "activities_after": after,
                "baseline_activities": {r["id"]: r for r in baseline},
            }
            self.reset_ledger("paper", str(allocation))
            self.store.put("alpaca-account", identity)
        if identity["id"] != account["id"]:
            raise SafetyError(
                "Alpaca paper account changed; existing ledger must not be reassigned"
            )
        for order in self.orders(active=True):
            await self.refresh_order(order)
        if self.settings["strategy"] == "htf" and (
            htf.snapshot(self).get("entry_attempt", {}).get("order_id")
        ):
            htf.reconcile_entry(self)
        local = {o["txid"]: o for o in self.orders() if o["txid"]}
        for row in await self.kraken.open_orders():
            if row["id"] not in local or row["client_order_id"] != local[row["id"]]["id"]:
                raise SafetyError("Untracked Alpaca order; manual reconciliation required")
        for row in await self.kraken.activities(identity["activities_after"]):
            baseline = identity["baseline_activities"].get(row["id"])
            if baseline is not None:
                if baseline != row:
                    raise SafetyError("Alpaca baseline activity was corrected")
                continue
            if row["activity_type"] == "FILL":
                if row["order_id"] not in local:
                    raise SafetyError(
                        "External Alpaca fill detected; account must be exclusive to Kairos"
                    )
            elif row["activity_type"] in {"FEE", "CFEE"}:
                self.apply_fee(row)
            else:
                raise SafetyError(
                    "Unacknowledged Alpaca cash journal (JNLC); manual reconciliation required"
                    if row["activity_type"] == "JNLC"
                    else "External account activity or corporate action; manual reconciliation required"
                )
        # Re-read after fee activities; no assumptions about settlement timing or external edits.
        account = await self.kraken.account()
        positions = await self.kraken.positions()
        expected = {
            a: dec(q) for a, q in self.ledger()["balances"].items() if a != "USD" and dec(q)
        }
        if positions != expected:
            raise SafetyError(
                "Alpaca positions differ from confirmed fills/fees; reconcile before continuing"
            )
        cash = dec(account["cash"]) - dec(identity["unallocated_cash"])
        orders = self.orders()
        latest = orders[-1] if orders else None
        checkpoint = [latest[k] for k in ("id", "filled", "cost")] if latest else None
        delta = cash - self.balance("USD")
        if delta and not (
            latest
            and dec(latest["filled"]) > 0
            and checkpoint != self.store.get("alpaca-cash-checkpoint")
            and abs(delta) <= dec(".01")
        ):
            raise SafetyError(
                "Alpaca cash differs from confirmed fills/fees; reconcile before continuing"
            )
        ledger = self.ledger()
        ledger["balances"]["USD"] = str(cash)
        ledger["broker_rounding"] = str(dec(ledger.get("broker_rounding", 0)) + delta)
        with self.store.db:
            self.store._put("ledger:paper", ledger)
            self.store._put("alpaca-cash-checkpoint", checkpoint)
        self.account_read_status["last_success_at"] = self.clock()
        return account

    async def valuation(self, enforce=False):
        if not self.store.get("alpaca-account"):
            return {"USD": dec(1)}, ZERO
        await self.reconcile_account()
        prices = {"USD": dec(1), **self.kraken.position_prices}
        ledger = self.ledger()
        equity = sum(dec(q) * prices[a] for a, q in ledger["balances"].items() if dec(q))
        exposure = equity - self.balance("USD")
        self.record_valuation(equity, exposure, "day:paper")
        if enforce and -dec(self.daily_pnl) >= dec(self.settings["daily_loss"]):
            raise SafetyError("Daily marked-to-market loss limit reached; no further orders")
        return prices, exposure

    async def place(self, pair, side, volume, price, book, maker=False, **kwargs):
        if not self.paper_armed or not self.kraken.allow_paper or self.mode != "paper":
            raise SafetyError("Alpaca hosted paper is not armed")
        if self.account_wait or self.long_retry:
            raise SafetyError("Account reconciliation pending; orders blocked")
        self.validate_capabilities(self.settings)
        account = await self.reconcile_account()
        if not self.kraken.is_equity(pair) and account.get("crypto_status") != "ACTIVE":
            raise SafetyError("Alpaca crypto trading is not active for this account")
        if volume * price > dec("200000"):
            raise SafetyError("Alpaca paper order exceeds the documented crypto notional ceiling")
        available = await self.kraken.balances()
        required = (
            volume * price * (1 + self.fees.reserve(pair) / 10000) if side == "buy" else volume
        )
        if available.get("USD" if side == "buy" else pair.base, ZERO) < required:
            raise SafetyError("Alpaca available funds after holds are insufficient")
        if maker:
            current = await self.kraken.book(pair)
            current.fresh(self.settings["stale_seconds"])
            if (side == "buy" and price >= current.asks[0][0]) or (
                side == "sell" and price <= current.bids[0][0]
            ):
                raise SafetyError("Passive Alpaca limit now crosses; skip rather than chase")
            book = current
        return await super().place(pair, side, volume, price, book, maker, **kwargs)

    async def reconcile_portfolio(self):
        try:
            await self.reconcile_account()
        except Exception:
            self.recovery_required = True
            raise

    async def _tick(self):
        if not self.running:
            return await super().tick()
        try:
            async with self.lock:
                if self.long_retry:
                    await self.recover_scheduled()
                    return
                run = self.active_run() or {}
                if (
                    self.settings["strategy"] == "htf"
                    and self.settings["htf_policy"] == "multibar-v2"
                    and self.clock() >= run.get("trial_ends_at", float("inf"))
                    and self.is_flat()
                    and not self.orders(active=True)
                ):
                    self.running = False
                    self.event(
                        "system",
                        {"message": "14-day trial complete; entries disabled, history retained"},
                    )
                    return
                if self.account_wait:
                    await self.recover_account_reads()
                    self.emit_state()
                    return
                await self.cancel_active()
                await self.reconcile_account()
                equities = any(self.kraken.is_equity(p) for p in self.fee_pairs())
                self.market_session = await self.kraken.clock() if equities else None
                if equities and not self.market_session["is_open"]:
                    await self.valuation(False)
                    self.emit_state()
                    return  # Scheduled slots are skipped normally on the next open cycle.
                if self.market_wait:
                    if time.monotonic() - self.market_wait[1] >= 300:
                        if self.schedule_retry(
                            PendingAlpacaData("Market-data recovery deadline exhausted")
                        ):
                            return
                        raise SafetyError(
                            "Alpaca data recovery exhausted; explicit Restart required"
                        )
                    # Continue reconciliation and enforce the loss halt during data
                    # recovery. Neither fresh data nor the stream may restart a halt.
                    await self.valuation(True)
                    for pair in self.fee_pairs():
                        book = await self.kraken.book(pair)
                        book.fresh(self.settings["stale_seconds"])
                    if not self.running or self.shutting_down:
                        return
                    if self.settings["strategy"] == "htf":
                        htf.arm(self)  # Do not chase a setup observed before the outage.
                    self.market_wait, self.last_error = None, None
                    self.event(
                        "data-wait",
                        {
                            "message": "Fresh Alpaca data restored; rechecking strategy and risk gates, HTF entries re-baselined"
                        },
                    )
            await super().tick()
        except Exception as exc:
            if isinstance(exc, PendingAlpacaAccount) and self.wait_for_account_data(exc):
                self.emit_state()
                return
            if isinstance(exc, PendingAlpacaData) and self.wait_for_market_data(exc):
                self.emit_state()
                return
            error_id = diagnostics.capture(exc, "alpaca-reconciliation")
            self.running = False
            self.last_error = (
                str(exc) if isinstance(exc, SafetyError) else "Alpaca reconciliation failed"
            )
            self.recovery_required = True
            if self.long_retry:
                self.retry_status.update(
                    status="halted", ended_at=self.clock(), reason=self.last_error
                )
                self.long_retry = None
            self.finish_account_recovery("halted")
            self.event("error", {"message": self.last_error, "error_id": error_id})
            try:
                await self.stop()
            except Exception as cleanup:
                diagnostics.capture(cleanup, "alpaca-cleanup")
            self.emit_state()
