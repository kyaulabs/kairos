import copy
import unittest

from kairos.domain import SafetyError, dec
from kairos.okx import PendingOKX
from kairos.okx_account import account, apply_executions, balances, mismatch, order_observation
from kairos.store import Store

BINDING = {
    "uid": "123456",
    "environment": "demo",
    "baseline": {"USDC": "10000", "BTC": "1", "USD": "10"},
}


def order(side="buy", identifier="a" * 32):
    quantity, price = (".0005", "50000") if side == "buy" else (".0004995", "49900")
    return {
        "id": identifier,
        "txid": None,
        "mode": "paper",
        "product": "spot",
        "run_id": "native-run",
        "instrument": "BTC-USD",
        "pair": "okx-demo:BTC-USD:USDC",
        "side": side,
        "base": "BTC",
        "quote": "USDC",
        "volume": quantity,
        "price": price,
        "filled": "0",
        "cost": "0",
        "fee": "0",
        "fees": {},
        "status": "submitting",
        "payload": {"sz": quantity, "px": price},
    }


def fill(intent, **changes):
    buy = intent["side"] == "buy"
    return {
        "instType": "SPOT",
        "instId": "BTC-USD",
        "tradeQuoteCcy": "USDC",
        "side": intent["side"],
        "clOrdId": intent["id"],
        "ordId": "101" if buy else "102",
        "fillSz": intent["volume"],
        "fillPx": intent["price"],
        "fee": "-.0000005" if buy else "-.02492505",
        "feeCcy": "BTC" if buy else "USDC",
        "execType": "T",
        "billId": "201" if buy else "202",
        "tradeId": "301" if buy else "302",
        "fillTime": "1791328407000",
        "ts": "1791328407001",
        **changes,
    }


def observation(intent, **changes):
    return {
        "instType": "SPOT",
        "instId": "BTC-USD",
        "tdMode": "cash",
        "ordType": "ioc",
        "tradeQuoteCcy": "USDC",
        "side": intent["side"],
        "clOrdId": intent["id"],
        "ordId": "101" if intent["side"] == "buy" else "102",
        "state": "filled",
        "sz": intent["volume"],
        "px": intent["price"],
        "accFillSz": intent["volume"],
        "uTime": "1791328407002",
        **changes,
    }


def snapshot(**assets):
    return {
        "assets": {
            k: {"total": str(v), "available": str(v), "frozen": "0"} for k, v in assets.items()
        }
    }


class NativeAccountingTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.store.put(
            "ledger:paper",
            {"balances": {"USDC": "500"}, "initial": "500", "fees": {}, "account_delta": {}},
        )

    def test_native_base_fee_then_quote_fee_sell_exact_once_and_preexisting_assets_unowned(self):
        buy = order()
        buy, added = apply_executions(self.store, buy, [fill(buy)], BINDING)
        self.assertEqual(len(added), 1)
        self.assertEqual(dec(self.store.get("ledger:paper")["balances"]["BTC"]), dec(".0004995"))
        self.assertEqual(buy["txid"], "101")  # Execution can precede the lost acknowledgement.
        buy = order_observation(buy, observation(buy))
        self.assertEqual(buy["status"], "closed")
        self.store.save_order(buy)
        buy, added = apply_executions(self.store, buy, [fill(buy), fill(buy)], BINDING)
        self.assertEqual(added, [])
        sell = order("sell", "b" * 32)
        sell, _ = apply_executions(self.store, sell, [fill(sell)], BINDING)
        sell = order_observation(sell, observation(sell))
        self.store.save_order(sell)
        ledger = self.store.get("ledger:paper")
        self.assertEqual(dec(ledger["balances"]["BTC"]), 0)
        self.assertEqual(dec(ledger["balances"]["USDC"]), dec("499.90012495"))
        self.assertNotIn("USD", ledger["balances"])
        self.assertEqual(ledger["fees"], {"BTC": "5E-7", "USDC": "0.02492505"})
        self.assertEqual(
            mismatch(BINDING, ledger, snapshot(USDC="9999.90012495", BTC=1, USD=10)), {}
        )
        totals = self.store.get("execution-run-totals:native-run")
        self.assertEqual(totals["filled_orders"], 2)
        self.assertEqual(
            {a: dec(q) for a, q in totals["paid_fees"].items()},
            {a: dec(q) for a, q in ledger["fees"].items()},
        )

    def test_pending_fee_or_cumulative_execution_never_invents_a_fill(self):
        intent = order()
        self.store.save_order(intent)
        observed = order_observation(intent, observation(intent))
        self.assertEqual(observed["status"], "settling")
        self.assertEqual(observed["filled"], "0")
        self.assertFalse(observed["fee_reported"])
        with self.assertRaises(PendingOKX):
            apply_executions(self.store, observed, [fill(intent, fee="")], BINDING)
        self.assertEqual(self.store.get("ledger:paper")["balances"], {"USDC": "500"})
        unfilled = order_observation(intent, observation(intent, state="canceled", accFillSz="0"))
        self.assertEqual(unfilled["status"], "canceled")
        self.assertEqual(unfilled["filled"], "0")
        self.assertTrue(unfilled["fee_reported"])

    def test_partial_canceled_entry_and_stale_live_observation_do_not_regress(self):
        intent = order()
        native = fill(intent, fillSz=".0002", fee="-.0000002")
        intent, _ = apply_executions(self.store, intent, [native], BINDING)
        intent = order_observation(intent, observation(intent, state="canceled", accFillSz=".0002"))
        self.assertEqual(intent["status"], "canceled")
        old = order_observation(
            intent, observation(intent, state="live", accFillSz="0", uTime="1791328407000")
        )
        self.assertEqual(old, intent)
        with self.assertRaisesRegex(SafetyError, "regressed"):
            order_observation(
                intent, observation(intent, state="live", accFillSz=".0002", uTime="1791328407003")
            )
        self.assertEqual(dec(self.store.get("ledger:paper")["balances"]["BTC"]), dec(".0001998"))

    def test_rebates_and_unallocated_third_currency_fees_keep_native_units(self):
        intent = order()
        intent, _ = apply_executions(
            self.store, intent, [fill(intent, fee=".01", feeCcy="USDC", execType="M")], BINDING
        )
        ledger = self.store.get("ledger:paper")
        self.assertEqual(dec(ledger["balances"]["USDC"]), dec("475.01"))
        self.assertEqual(dec(ledger["fees"]["USDC"]), dec("-.01"))
        another = order(identifier="c" * 32)
        apply_executions(
            self.store,
            another,
            [fill(another, ordId="103", billId="203", tradeId="303", fee="-.1", feeCcy="OKB")],
            BINDING,
        )
        ledger = self.store.get("ledger:paper")
        self.assertNotIn("OKB", ledger["balances"])
        self.assertEqual(ledger["unallocated_fee_effects"], {"OKB": "-0.1"})
        self.assertEqual(ledger["fees"]["OKB"], "0.1")
        self.assertEqual(ledger["account_delta"]["OKB"], "-0.1")

    def test_revised_records_duplicate_trade_ids_wrong_quotes_and_excess_fills_are_atomic_failures(
        self,
    ):
        intent = order()
        native = fill(intent, fillSz=".0002", fee="-.0000002")
        intent, _ = apply_executions(self.store, intent, [native], BINDING)
        before = copy.deepcopy(self.store.get("ledger:paper"))
        for change in (
            {"fee": "-.0000003"},
            {"billId": "999"},
            {"tradeQuoteCcy": "USD"},
            {"clOrdId": "foreign"},
            {"fillSz": ".001", "billId": "999", "tradeId": "999"},
            {"fillPx": "50001"},
        ):
            with self.assertRaises(SafetyError):
                apply_executions(self.store, intent, [{**native, **change}], BINDING)
            self.assertEqual(self.store.get("ledger:paper"), before)

    def test_complete_native_balances_identity_and_unexplained_changes(self):
        self.assertEqual(
            account([{"uid": "123456", "acctLv": "2", "autoLoan": False}])["uid"], "123456"
        )
        for row in (
            {"uid": "123456", "acctLv": "3", "autoLoan": False},
            {"uid": "123456", "acctLv": "2", "autoLoan": True},
        ):
            with self.assertRaises(SafetyError):
                account([row])
        row = {
            "uTime": "1791328407000",
            "details": [
                {
                    "ccy": "USDC",
                    "cashBal": "10000",
                    "availBal": "9999",
                    "frozenBal": "1",
                    "liab": "",
                    "interest": "0",
                }
            ],
        }
        self.assertEqual(balances([row])["assets"]["USDC"]["available"], "9999")
        with self.assertRaises(SafetyError):
            balances([])
        with self.assertRaises(SafetyError):
            balances([{**row, "details": [{**row["details"][0], "liab": "1"}]}])
        with self.assertRaises(SafetyError):
            balances([{**row, "details": [{**row["details"][0], "cashBal": ""}]}])
        ledger = self.store.get("ledger:paper")
        discrepancy = mismatch(BINDING, ledger, snapshot(USDC=10001, BTC=1, USD=10))
        self.assertEqual(discrepancy, {"USDC": {"expected": "10000", "observed": "10001"}})
        self.assertEqual(self.store.get("ledger:paper"), ledger)  # Never overwrite it to match.
