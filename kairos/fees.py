"""Account-reported trading fees, separate from operator settings and wallet balances."""

import time

from kairos import diagnostics
from kairos.clients import TransientFeeRead
from kairos.domain import ZERO, SafetyError, dec

MAX_AGE = 60
# Refresh before planning has to share the last seconds of a valid snapshot.
REFRESH_AFTER = 30


class FeeUnavailable(SafetyError):
    def __init__(self, message, *, retryable=False, error_id=None):
        super().__init__(message)
        self.retryable, self.error_id = retryable, error_id


class AccountFees:
    def __init__(self, spot, futures):
        self.spot, self.futures = spot, futures
        self.rates, self.errors, self.attempts = {}, {}, {}
        self.failures = {}
        self.hosted_paper = getattr(spot, "hosted_paper", False) is True
        name = getattr(spot, "fee_name", None)
        self.name = (
            name
            if isinstance(name, str)
            else "Alpaca planning"
            if self.hosted_paper
            else "Kraken account"
        )

    async def refresh(self, pairs, *, force=False):
        pairs = list({pair.id: pair for pair in pairs}.values())
        now = time.time()
        due = [
            pair
            for pair in pairs
            if force
            or pair.id not in self.attempts
            or not 0
            <= now - self.attempts[pair.id]
            < (MAX_AGE if pair.id in self.errors else REFRESH_AFTER)
        ]
        if due:
            for pair in due:
                self.attempts[pair.id] = now
            try:
                updates = {}
                spot = [p for p in due if not p.id.startswith("futures:")]
                if spot:
                    maker, taker = await self.spot.fees(spot)
                    for pair in spot:
                        updates[pair.id] = (maker.get(pair.id, taker[pair.id]), taker[pair.id])
                for pair in due:
                    if pair.id.startswith("futures:"):
                        if self.futures is None:
                            raise SafetyError("Futures fee adapter unavailable")
                        updates[pair.id] = await self.futures.fees(self.spot, pair)
                checked = {}
                for pair in due:
                    maker, taker = map(dec, updates[pair.id])
                    if not -1000 <= maker <= taker <= 1000 or taker < 0:
                        raise SafetyError("Invalid account fee rates")
                    checked[pair.id] = {
                        "pair": pair.id,
                        "symbol": pair.symbol,
                        "maker_bps": str(maker),
                        "taker_bps": str(taker),
                        "received": now,
                    }
                self.rates.update(checked)
                for pair in due:
                    self.errors.pop(pair.id, None)
                    self.failures.pop(pair.id, None)
            except (SafetyError, KeyError, TypeError, ValueError, AttributeError) as exc:
                error_id = diagnostics.capture(exc, "account-fees")
                retryable = isinstance(exc, TransientFeeRead)
                for pair in due:
                    self.errors[pair.id] = (
                        f"{self.name} fees unavailable; new orders blocked. Check read credentials and fee-query permissions."
                    )
                    self.failures[pair.id] = (retryable, error_id)
                raise FeeUnavailable(
                    self.errors[due[0].id], retryable=retryable, error_id=error_id
                ) from exc
        for pair in pairs:
            self.rate(pair)

    def rate(self, pair, maker=False):
        row = self.rates.get(pair.id)
        if pair.id in self.errors or not row or not 0 <= time.time() - row["received"] < MAX_AGE:
            retryable, error_id = self.failures.get(pair.id, (False, None))
            raise FeeUnavailable(
                f"{self.name} fees missing or stale; refresh before trading",
                retryable=retryable,
                error_id=error_id,
            )
        return dec(row["maker_bps" if maker else "taker_bps"])

    def reserve(self, pair, maker=False):
        # Never spend an anticipated maker rebate before it has actually been credited.
        return max(ZERO, self.rate(pair, maker))

    async def recheck(self, pair, maker, reserved):
        await self.refresh([pair], force=True)
        if self.reserve(pair, maker) > max(ZERO, reserved):
            raise SafetyError(
                "Kraken fees increased beyond the planned fee reserve; replan before trading"
            )
        return self.rate(pair, maker)

    def snapshot(self, pairs):
        rows = []
        for pair in pairs:
            row = self.rates.get(
                pair.id,
                {
                    "pair": pair.id,
                    "symbol": pair.symbol,
                    "maker_bps": None,
                    "taker_bps": None,
                    "received": None,
                },
            )
            fresh = row["received"] is not None and 0 <= time.time() - row["received"] < MAX_AGE
            rows.append(
                {
                    **row,
                    "stale": not fresh or pair.id in self.errors,
                    "error": self.errors.get(pair.id),
                    "error_id": self.failures.get(pair.id, (False, None))[1],
                }
            )
        return {
            "source": self.spot.fee_source
            if isinstance(getattr(self.spot, "fee_source", None), str)
            else "Kraken authenticated TradeVolume",
            "note": self.spot.fee_note
            if isinstance(getattr(self.spot, "fee_note", None), str)
            else None,
            "estimated": self.hosted_paper or getattr(self.spot, "fee_estimates", False) is True,
            "max_age_seconds": MAX_AGE,
            "markets": rows,
        }
