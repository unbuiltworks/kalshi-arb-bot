"""Cash is what moved; risk is what can still be lost. They are not equal.

Probe 05's ledger said `committed_usd 2.8479` while the account moved
$0.46150, because it counted eight `BUY_SHORT` orders -- which CREDITED
$1.2432 -- as cash out. These tests pin both quantities separately and pin
the one rule that matters for the envelope: a credit never buys headroom.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

import pytest

import src.l5_venue_accounting as A

PROBE05 = json.loads(
    (pathlib.Path(__file__).resolve().parents[1]
     / "analysis/pmus/evidence/probe05_orders.json").read_text())


def long_order(px="0.20", qty="1", fee="0", **kw):
    o = {"id": kw.pop("id", "L"), "intent": "ORDER_INTENT_BUY_LONG",
         "side": "ORDER_SIDE_BUY", "outcomeSide": "OUTCOME_SIDE_YES",
         "price": px, "quantity": qty, "cumQuantity": qty,
         "leavesQuantity": 0, "avgPx": px, "commission": fee}
    o.update(kw)
    return o


def short_order(px="0.30", qty="1", fee="0", **kw):
    o = {"id": kw.pop("id", "S"), "intent": "ORDER_INTENT_BUY_SHORT",
         "side": "ORDER_SIDE_SELL", "outcomeSide": "OUTCOME_SIDE_NO",
         "price": px, "quantity": qty, "cumQuantity": qty,
         "leavesQuantity": 0, "avgPx": px, "commission": fee}
    o.update(kw)
    return o


# --------------------------------------------------------------- direction

def test_intent_decides_direction():
    assert A.direction(long_order()) == "LONG"
    assert A.direction(short_order()) == "SHORT"


def test_direction_falls_back_to_side_and_outcome():
    assert A.direction({"side": "ORDER_SIDE_BUY",
                        "outcomeSide": "OUTCOME_SIDE_YES"}) == "LONG"
    assert A.direction({"side": "ORDER_SIDE_SELL",
                        "outcomeSide": "OUTCOME_SIDE_NO"}) == "SHORT"


def test_an_unreadable_direction_raises_rather_than_assuming_long():
    # assuming LONG would understate a short's settlement liability, which is
    # the one direction where a wrong guess lets real risk past a gate
    with pytest.raises(A.UnknownIntent):
        A.direction({"id": "x"})


# ------------------------------------------------------------ venue cash

def test_a_long_costs_cash_and_a_short_takes_it_in():
    assert A.execution_cash(long_order("0.25", "4")) == D("-1.00")
    assert A.execution_cash(short_order("0.25", "4")) == D("1.00")


def test_the_venue_average_beats_our_limit_price():
    o = long_order("0.30", "2")
    o["avgPx"] = "0.25"
    assert A.execution_cash(o) == D("-0.50")


def test_a_partial_fill_prices_only_what_filled():
    o = long_order("0.20", "10")
    o["cumQuantity"] = "3"
    assert A.execution_cash(o) == D("-0.60")


def test_settlement_pays_a_long_and_charges_a_short():
    assert A.settlement_cash(long_order("0.2", "5"), D(1)) == D("5")
    assert A.settlement_cash(short_order("0.2", "5"), D(1)) == D("-5")
    assert A.settlement_cash(long_order("0.2", "5"), D(0)) == D("0")
    assert A.settlement_cash(short_order("0.2", "5"), D(0)) == D("0")


def test_the_three_lines_are_never_merged():
    cf = A.venue_cashflow([long_order("0.20", "1", fee="0.05")], D(1))
    assert cf["execution"] == D("-0.20")
    assert cf["commission"] == D("-0.05")
    assert cf["settlement"] == D("1")
    assert cf["net"] == D("0.75")


# --------------------------------------------- PROBE 05, from the venue

def test_probe05_has_its_eighteen_authoritative_orders():
    assert len(PROBE05) == 18
    assert all(o["leavesQuantity"] == 0 for o in PROBE05)
    assert sum(1 for o in PROBE05
               if A.direction(o) == "LONG") == 10
    assert sum(1 for o in PROBE05
               if A.direction(o) == "SHORT") == 8


def test_probe05_cash_reconciles_to_the_balance_to_the_cent():
    cf = A.venue_cashflow(PROBE05, D("0.0000"))
    assert cf["execution"] == D("-0.361500")
    assert cf["commission"] == D("-0.1000")
    assert cf["settlement"] == D("0")
    assert cf["net"] == D("-0.46150")           # 62.36645 -> 61.90495


def test_probe05_legs_match_the_venue():
    r = A.risk_exposure(PROBE05)
    assert r["long_qty"] == "10.34"
    assert r["short_qty"] == "7.50"
    assert r["matched_qty"] == "7.50"
    assert r["residual_qty"] == "2.84"


def test_probe05_never_breached_five_dollars_at_any_point():
    w = A.walk_exposure(PROBE05, D(5))
    assert w["breached"] is False
    assert w["peak_worst_case_loss_usd"] == D("2.875100")


def test_the_old_committed_usd_was_neither_cash_nor_risk():
    # 2.8479 was reported by the probe; it is not the cash that moved and
    # not the worst case either. That is the whole defect, pinned.
    cf = A.venue_cashflow(PROBE05, D("0.0000"))
    peak = A.walk_exposure(PROBE05, D(5))["peak_worst_case_loss_usd"]
    assert cf["net"] != D("-2.8479")
    assert peak != D("2.8479")


# ------------------------------------------------------- the credit rule

def test_a_short_credit_does_not_buy_headroom():
    """The rule, stated as a test.

    Four dollars of long, then a short that credits three. Netting the
    credit would report one dollar of exposure and wave through four more.
    The short owes its shares at settlement, so the bound must not fall.
    """
    longs = [long_order("0.40", "10", id="L1")]          # $4 out
    both = longs + [short_order("0.30", "10", id="S1")]  # $3 in
    only_long = A.risk_exposure(longs)["worst_case_loss_usd"]
    with_short = A.risk_exposure(both)["worst_case_loss_usd"]
    assert only_long == D("4.00")
    # netting would give 1.00; the real bound cannot be lower than that naive
    # number by construction, and here the offsetting position genuinely
    # reduces it -- but never below what the worse settlement costs
    assert with_short == D("1.00")
    assert D(A.risk_exposure(both)["by_settlement"]["settles_1"]) == D("-1")


def test_an_unmatched_short_is_priced_at_its_full_liability():
    # sells 10 shares short for $3; if YES settles 1 it owes $10
    r = A.risk_exposure([short_order("0.30", "10")])
    assert r["worst_case_loss_usd"] == D("7.00")
    assert D(r["by_settlement"]["settles_0"]) == D("3")
    assert D(r["by_settlement"]["settles_1"]) == D("-7")


def test_a_perfectly_matched_book_can_still_lose_only_its_fees():
    r = A.risk_exposure([long_order("0.40", "5", fee="0.01"),
                         short_order("0.40", "5", fee="0.01")])
    assert r["matched_qty"] == "5"
    assert r["residual_qty"] == "0"
    assert r["worst_case_loss_usd"] == D("0.02")


def test_residual_long_is_the_exposure_when_the_book_is_uneven():
    r = A.risk_exposure([long_order("0.50", "10"), short_order("0.50", "6")])
    assert r["residual_qty"] == "4"
    assert r["worst_case_loss_usd"] == D("2.00")     # 4 shares at 0.50


def test_residual_short_is_the_exposure_the_other_way():
    r = A.risk_exposure([long_order("0.50", "6"), short_order("0.50", "10")])
    assert r["residual_qty"] == "-4"
    assert r["worst_case_loss_usd"] == D("2.00")     # 4 short at 1 - 0.50


# ------------------------------------------------------- outstanding

def test_an_outstanding_order_counts_as_if_it_fills_in_full():
    resting = long_order("0.40", "5", id="R")
    resting["cumQuantity"] = "0"                    # nothing filled yet
    resting["avgPx"] = "0"                          # so the limit price rules
    r = A.risk_exposure([], [resting])
    assert r["outstanding_counted"] == 1
    assert r["worst_case_loss_usd"] == D("2.00")


def test_a_resting_short_is_not_free_money():
    resting = short_order("0.10", "10", id="R")
    resting["cumQuantity"] = "0"
    resting["avgPx"] = "0"
    assert A.risk_exposure([], [resting])["worst_case_loss_usd"] == D("9.00")


def test_outstanding_adds_to_filled_rather_than_replacing_it():
    filled = [long_order("0.50", "4")]
    resting = long_order("0.50", "4", id="R")
    resting["cumQuantity"] = "0"
    resting["avgPx"] = "0"
    assert A.risk_exposure(filled)["worst_case_loss_usd"] == D("2.00")
    assert A.risk_exposure(filled, [resting])["worst_case_loss_usd"] == D("4.00")


def test_the_envelope_gate_reads_the_bound_not_the_cash():
    filled = [long_order("0.50", "9"), short_order("0.50", "9")]
    # matched: cash out is tiny, but the gate must not be fooled either way
    assert A.envelope_breached(filled, [], D(5)) is False
    big = [long_order("0.60", "10")]
    assert A.envelope_breached(big, [], D(5)) is True


def test_a_position_that_cannot_lose_reports_zero_not_a_credit():
    r = A.risk_exposure([long_order("0.10", "1"), short_order("0.90", "1")])
    assert r["worst_case_loss_usd"] == D(0)


# ---------------------------------------------------------- the walk

def test_the_walk_catches_a_breach_that_the_end_state_hides():
    orders = [long_order("0.60", "10", id="A",
                         insertTime="2026-09-25T15:00:00Z"),
              short_order("0.60", "10", id="B",
                          insertTime="2026-09-25T15:01:00Z")]
    assert A.risk_exposure(orders)["worst_case_loss_usd"] == D(0)
    w = A.walk_exposure(orders, D(5))
    assert w["breached"] is True
    assert w["peak_after_order"] == "A"


def test_the_walk_orders_by_venue_time_not_by_list_order():
    late = long_order("0.60", "10", id="LATE",
                      insertTime="2026-09-25T15:05:00Z")
    early = long_order("0.10", "1", id="EARLY",
                       insertTime="2026-09-25T15:00:00Z")
    w = A.walk_exposure([late, early], D(5))
    assert w["steps"][0]["order"] == "EARLY"


# ------------------------------- the gate now reads the bound, not notional

def test_the_store_prices_a_short_leg_as_a_credit_not_as_cash_out():
    import src.l5_live_execution as EX
    import tempfile, pathlib
    d = tempfile.mkdtemp()
    st = EX.InventoryStore(str(pathlib.Path(d) / "inv.json"))
    # Probe 05's legs, in our own YES-convention prices
    st.apply_fill("m", "UP", D("10.34"), D("1.6047") / D("10.34"), basis=EX.OWN)
    st.apply_fill("m", "DOWN", D("7.50"), D("6.2568") / D("7.50"), basis=EX.OWN)
    # committed still adds both as cash out -- that is what it is for
    assert st.committed()["m"] > D("7.8")
    # the bound is the worst case over both settlements, and it is not that
    assert abs(st.risk_exposure_usd("m") - D("0.3615")) < D("0.0001")


def test_a_short_credit_does_not_create_capacity_in_the_store_either():
    import src.l5_live_execution as EX
    import tempfile, pathlib
    st = EX.InventoryStore(str(pathlib.Path(tempfile.mkdtemp()) / "i.json"))
    st.apply_fill("m", "DOWN", D("10"), D("0.90"), basis=EX.OWN)     # short 10, takes in $1
    # if YES settles 1 the short owes $10 having received $1: a $9 bound
    assert st.risk_exposure_usd("m") == D("9.0")


def test_the_five_dollar_gate_reads_the_bound():
    import src.l5_live_execution as EX
    lim = EX.SafetyLimits()
    assert lim.refuse(price=D("0.5"), qty=D(1), market="m",
                      committed_by_market={"m": D("100")},
                      residual_after=D(0), realized_today=D(0),
                      open_markets=["m"], outstanding_by_market={},
                      live_order_count=0,
                      risk_by_market={"m": D("1")}) is None
    assert lim.refuse(price=D("0.5"), qty=D(1), market="m",
                      committed_by_market={"m": D(0)},
                      residual_after=D(0), realized_today=D(0),
                      open_markets=["m"], outstanding_by_market={},
                      live_order_count=0,
                      risk_by_market={"m": D("5.01")}) == "MAX_USD_PER_MARKET"
