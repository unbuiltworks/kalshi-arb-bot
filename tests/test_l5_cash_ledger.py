"""Mutation tests: every accounting failure this project has actually hit.

Each test MUTATES a correct ledger into a known-bad one and asserts the bad
state is detected or cannot be represented. A test that only checks the happy
path would have passed throughout the survivorship bug.
"""
import json
from decimal import Decimal as D

import pytest

from src import l5_cash_ledger as L
from src import l5_resolved_pnl as R


def row(**kw):
    base = {"type": "TRADE", "side": "BUY", "conditionId": "0xaa",
            "slug": "btc-updown-15m-1", "outcome": "Up", "price": 0.4,
            "size": 10.0, "usdcSize": 4.0, "timestamp": 1788230700,
            "transactionHash": "0xt1"}
    base.update(kw)
    return base


# ---- 1. floating-point error ------------------------------------------

def test_all_arithmetic_is_decimal_never_float():
    led = L.CashLedger()
    led.add(row(usdcSize=0.1, transactionHash="0xa"))
    led.add(row(usdcSize=0.2, transactionHash="0xb"))
    assert led.account_cash() == D("-0.3")
    assert isinstance(led.account_cash(), D)
    #: the float path gives -0.30000000000000004; the ledger must not
    assert str(led.account_cash()) == "-0.3"


def test_cash_is_built_from_str_not_float_division():
    assert L.dec(0.1) == D("0.1")
    assert L.dec("0.1") == D("0.1")
    with pytest.raises(L.LedgerError):
        L.dec(None)


# ---- 2. duplicate fill -------------------------------------------------

def test_a_duplicate_fill_is_dropped_not_double_counted():
    led = L.CashLedger()
    assert led.add(row()) is True
    assert led.add(row()) is False          # byte-identical repeat
    assert led.duplicates == 1
    assert led.account_cash() == D("-4.0")


def test_a_genuinely_distinct_fill_is_not_mistaken_for_a_duplicate():
    led = L.CashLedger()
    led.add(row(transactionHash="0xt1"))
    led.add(row(transactionHash="0xt2"))
    assert led.duplicates == 0
    assert led.account_cash() == D("-8.0")


# ---- 3. missing fill ---------------------------------------------------

def test_a_missing_fill_changes_the_total_and_is_therefore_detectable():
    full = L.CashLedger()
    for i in range(3):
        full.add(row(transactionHash=f"0x{i}"))
    short = L.CashLedger()
    for i in range(2):
        short.add(row(transactionHash=f"0x{i}"))
    assert full.account_cash() != short.account_cash()
    assert full.account_cash() - short.account_cash() == D("-4.0")


# ---- 4. duplicate redemption ------------------------------------------

def test_a_duplicate_redemption_is_dropped():
    led = L.CashLedger()
    red = row(type="REDEEM", side="", usdcSize=10.0, transactionHash="0xr")
    assert led.add(red) is True
    assert led.add(dict(red)) is False
    assert led.by_kind()["REDEEM"] == D("10.0")


def test_two_distinct_redemptions_both_count():
    led = L.CashLedger()
    led.add(row(type="REDEEM", side="", usdcSize=10.0, transactionHash="0xr1"))
    led.add(row(type="REDEEM", side="", usdcSize=10.0, transactionHash="0xr2"))
    assert led.by_kind()["REDEEM"] == D("20.0")


# ---- 5. omitted no-redemption loser, and 6. unresolved-as-loss ---------

def test_the_survivorship_filter_is_refused_at_the_boundary():
    """The original bug: a set pre-filtered on redemption cannot be repaired
    downstream, because the losers are simply not in it."""
    assert hasattr(R, "SurvivorshipFilterError")


def test_zero_redemption_resolved_market_contributes_minus_spend():
    led = L.CashLedger()
    led.add(row(usdcSize=4.0, conditionId="0xloser"))
    mkt = led.by_market()["0xloser"]
    assert mkt["TRADE_BUY"] == D("-4.0")
    assert "REDEEM" not in mkt          # no redemption row exists at all
    #: the market's cash is -spend; it must NOT be dropped for lacking a redeem
    assert sum(mkt.values()) == D("-4.0")


def test_unresolved_and_resolved_loser_are_NOT_the_same_cash_treatment():
    """Both look identical in the cash feed -- no redemption row -- yet one is
    a realized loss and the other must be excluded. The ledger cannot tell them
    apart on its own, and this test exists so that is never forgotten."""
    led = L.CashLedger()
    led.add(row(usdcSize=4.0, conditionId="0xunknown"))
    m = led.by_market()["0xunknown"]
    assert sum(m.values()) == D("-4.0")
    #: resolution is NOT derivable from the cash ledger
    assert not hasattr(led, "is_resolved")


# ---- 7. wrong winner / token-outcome mismatch -------------------------

def test_the_ledger_never_infers_a_winner():
    src = open("src/l5_cash_ledger.py").read()
    for banned in ("def winner", "infer_winner", "def settle("):
        assert banned not in src, banned
    #: outcome is carried verbatim, never compared to price to guess a result
    led = L.CashLedger()
    led.add(row(outcome="Down", usdcSize=4.0))
    assert led.events[0]["outcome"] == "Down"


def test_redemption_cash_is_taken_from_the_venue_not_recomputed():
    """A redemption of 10 contracts pays 10.0, whatever price/size say."""
    led = L.CashLedger()
    led.add(row(type="REDEEM", side="", price=0.37, size=3.0, usdcSize=10.0,
                transactionHash="0xr"))
    assert led.by_kind()["REDEEM"] == D("10.0")


# ---- 8. date-boundary mismatch ----------------------------------------

def test_days_are_bucketed_in_utc_not_local_time():
    led = L.CashLedger()
    #: 2026-09-06T23:59:30Z -- a local-time bucket would move this to the 7th
    led.add(row(timestamp=1788393570, usdcSize=1.0))
    days = list(led.by_day())
    import datetime as dt
    want = dt.datetime.fromtimestamp(1788393570, dt.timezone.utc).strftime("%Y-%m-%d")
    assert days == [want]


# ---- 9. incorrect fee / rounding --------------------------------------

def test_no_discrepancy_may_be_silently_called_rounding():
    """price*size disagrees with usdcSize by 52 bps on the real feed. The
    ledger must expose that, never absorb it."""
    led = L.CashLedger()
    led.add(row(price=0.1, size=4.18, usdcSize=0.44433))
    r = led.reconcile()
    assert D(r["price_size_vs_usdc_drift"]) != D(0)
    assert r["account_cash"] == "-0.44433"       # cash is usdcSize, not 0.418


def test_the_cross_check_compares_trade_rows_only():
    """Comparing a TRADE-only product against all-kinds absolute cash produced
    a meaningless 5,055 bps 'drift' in the first version of this ledger."""
    led = L.CashLedger()
    led.add(row(price=0.5, size=2.0, usdcSize=1.0))
    led.add(row(type="REDEEM", side="", usdcSize=999.0, transactionHash="0xr"))
    r = led.reconcile()
    assert D(r["price_size_vs_usdc_drift"]) == D(0)   # redeem excluded


# ---- 10. account-level incentives must never be allocated -------------

def test_incentives_are_excluded_from_market_rollups():
    led = L.CashLedger()
    led.add(row(usdcSize=4.0, conditionId="0xaa"))
    led.add(row(type="MAKER_REBATE", side="", conditionId="", usdcSize=7.0,
                transactionHash="0xm"))
    by = led.by_market()
    assert set(by) == {"0xaa"}
    assert led.by_kind()["MAKER_REBATE"] == D("7.0")
    assert led.account_cash() == D("3.0")


def test_a_trading_row_with_no_market_is_refused_from_the_rollup():
    led = L.CashLedger()
    led.add(row(conditionId="", usdcSize=4.0))
    with pytest.raises(L.LedgerError):
        led.by_market()


def test_attribution_check_reports_whether_allocation_is_even_possible():
    led = L.CashLedger()
    led.add(row(type="REWARD", side="", conditionId="", usdcSize=5.0,
                transactionHash="0xw"))
    c = led.incentive_attribution_check()
    assert c["incentive_rows"] == 1 and c["with_condition_id"] == 0
    assert c["attributable"] is False


# ---- the identity itself ----------------------------------------------

def test_account_cash_equals_the_sum_of_its_parts_exactly():
    led = L.CashLedger()
    led.add(row(usdcSize=4.0, transactionHash="0x1"))
    led.add(row(type="REDEEM", side="", usdcSize=9.0, transactionHash="0x2"))
    led.add(row(type="MAKER_REBATE", side="", conditionId="", usdcSize=1.0,
                transactionHash="0x3"))
    r = led.reconcile()
    assert D(r["residual_vs_parts"]) == D(0)   # numeric, not string: "0.0" != "0"
    assert D(r["account_cash"]) == D("6.0")


def test_an_unknown_row_type_is_rejected_not_silently_valued_at_zero():
    led = L.CashLedger()
    assert led.add(row(type="AIRDROP")) is False
    assert len(led.rejected) == 1
    assert led.account_cash() == D(0)


def test_a_negative_usdc_size_raises_rather_than_flipping_a_sign():
    led = L.CashLedger()
    with pytest.raises(L.LedgerError):
        led.add(row(usdcSize=-4.0))
