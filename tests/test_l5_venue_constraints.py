"""The venue's own order constraints, verified instead of assumed.

WHAT WAS WRONG. `l5_btc5m_controller.Params` carries `tick = 0.01` and
`min_qty = 0.01` as DEFAULTS and nothing checked them against the venue,
while `l5_btc5m_runner.book()` was already fetching the market object that
carries `orderPriceMinTickSize` and `minimumTradeQty` -- and returning only
bid, ask, age and closed. The authority was on the wire, in the same
response, and was discarded.

VERIFIED AGAINST THE LIVE VENUE 2026-11-19 (cpc-btc-updown-5m-...-1620z):
minimumTradeQty 0.01, orderPriceMinTickSize 0.01, feeCoefficient 0.095. The
hardcoded values are currently RIGHT, which is the reason to gate rather
than ignore: a silent change to either would be invisible until orders were
rejected, or worse accepted at a size nobody intended.

THE SIZING CONSEQUENCE, which decides whether a $5 budget can trade at all:
the smallest permitted order is `minimumTradeQty * price`, so $0.0099 at a
0.99 favourite and $0.0008 at a 0.08 rung. A $5 window budget is therefore
bounded by OUR limits, not the venue's.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_venue_constraints as VC

os.environ["L5_BTC5M_CONTROLLER"] = "ON"
from src import l5_btc5m_controller as C          # noqa: E402

#: exactly what the live venue served
LIVE = {"minimumTradeQty": 0.01, "orderPriceMinTickSize": 0.01,
        "feeCoefficient": 0.095}


# ------------------------------------------------------- parsing

def test_the_live_venue_object_parses():
    s = VC.from_market(LIVE)
    assert s["ok"] is True and s["reason"] is None
    assert s["min_qty"] == D("0.01")
    assert s["tick"] == D("0.01")
    assert s["fee_coef"] == D("0.095")


@pytest.mark.parametrize("obj,reason", [
    (None, "VENUE_MARKET_UNPARSEABLE"),
    ("not a mapping", "VENUE_MARKET_UNPARSEABLE"),
    ({}, "VENUE_CONSTRAINTS_MISSING"),
    ({"minimumTradeQty": 0.01}, "VENUE_CONSTRAINTS_MISSING"),
    ({"orderPriceMinTickSize": 0.01}, "VENUE_CONSTRAINTS_MISSING"),
    ({"minimumTradeQty": "abc", "orderPriceMinTickSize": 0.01},
     "VENUE_CONSTRAINTS_MISSING"),
])
def test_missing_or_unreadable_constraints_refuse(obj, reason):
    s = VC.from_market(obj)
    assert s["ok"] is False and s["reason"] == reason
    assert s["min_qty"] is None and s["tick"] is None


@pytest.mark.parametrize("tick", [0, -0.01, 0.00001, 0.6, 1, 2])
def test_an_implausible_tick_refuses(tick):
    s = VC.from_market({"minimumTradeQty": 0.01,
                        "orderPriceMinTickSize": tick})
    assert s["ok"] is False
    assert s["reason"].startswith("VENUE_TICK_IMPLAUSIBLE")


@pytest.mark.parametrize("minq", [0, -1, 1, 1.5, 10])
def test_a_non_fractional_minimum_refuses(minq):
    """`pmus_micro_executor` makes the same demand: the market must
    explicitly advertise minimumTradeQty < 1, because every size this lane
    computes is fractional."""
    s = VC.from_market({"minimumTradeQty": minq,
                        "orderPriceMinTickSize": 0.01})
    assert s["ok"] is False
    assert s["reason"].startswith("VENUE_MIN_QTY_IMPLAUSIBLE")


def test_an_implausible_fee_refuses_but_a_missing_one_does_not():
    assert VC.from_market(dict(LIVE, feeCoefficient=2))["ok"] is False
    s = VC.from_market({"minimumTradeQty": 0.01,
                        "orderPriceMinTickSize": 0.01})
    assert s["ok"] is True and s["fee_coef"] is None


def test_a_venue_tick_other_than_ours_is_accepted_not_refused():
    """MEASURED COST OF THE OPPOSITE CHOICE: `pmus_micro_executor` required a
    0.005 tick, PMUS sports serve 0.01, and that hardcoded equality
    "REFUSED EVERY SPORTS MARKET ... the live path could not have placed a
    sports order at all". The venue's own grid is authoritative here."""
    s = VC.from_market({"minimumTradeQty": 0.01,
                        "orderPriceMinTickSize": 0.005})
    assert s["ok"] is True and s["tick"] == D("0.005")
    assert VC.check_order("0.085", "0.01", s) is None


# ------------------------------------------------- order checking

def test_a_conforming_order_passes():
    assert VC.check_order("0.08", "0.01", VC.from_market(LIVE)) is None


def test_a_sub_minimum_quantity_is_refused_and_never_rounded_up():
    """THE RULE: never round upward. Raising 0.005 to the 0.01 minimum
    doubles the order, which is how a penny intent becomes a ten-penny one."""
    why = VC.check_order("0.08", "0.005", VC.from_market(LIVE))
    assert why == "QTY_BELOW_VENUE_MINIMUM:0.005<0.01"


@pytest.mark.parametrize("px", ["0.085", "0.0801", "0.333"])
def test_an_off_tick_price_is_refused(px):
    assert VC.check_order(px, "0.01",
                          VC.from_market(LIVE)).startswith("PRICE_OFF_TICK")


def test_an_off_lot_quantity_is_refused():
    assert VC.check_order("0.08", "0.015",
                          VC.from_market(LIVE)).startswith("QTY_OFF_LOT")


@pytest.mark.parametrize("px", ["0", "1", "-0.1", "1.5"])
def test_a_price_outside_the_binary_range_is_refused(px):
    assert VC.check_order(px, "0.01",
                          VC.from_market(LIVE)).startswith("PRICE_OUT_OF_RANGE")


def test_an_unverified_spec_refuses_every_order():
    for spec in (None, {}, {"ok": False, "reason": "VENUE_CONSTRAINTS_MISSING"}):
        assert VC.check_order("0.08", "0.01", spec)


# --------------------------------------------- the sizing question

def test_the_smallest_permitted_order_is_under_a_cent():
    s = VC.from_market(LIVE)
    assert VC.smallest_order_usd(s, "0.99") == D("0.0099")
    assert VC.smallest_order_usd(s, "0.08") == D("0.0008")
    #: so a $5 window budget is bounded by OUR limits, not the venue's
    assert VC.smallest_order_usd(s, "0.99") * 500 < D("5.00")


def test_smallest_order_refuses_without_a_spec():
    assert VC.smallest_order_usd({"ok": False}, "0.50") is None
    assert VC.smallest_order_usd(VC.from_market(LIVE), "1.5") is None


# ------------------------------------------- the book carries them

def test_carry_always_returns_the_same_three_keys():
    """Absent means nobody looked; None means the venue did not say. A
    consumer must be able to tell those apart."""
    got = VC.carry(LIVE)
    assert set(got) == {VC.BOOK_MIN_QTY, VC.BOOK_TICK, VC.BOOK_FEE}
    bad = VC.carry({})
    assert set(bad) == set(got)
    assert all(v is None for v in bad.values())


def test_from_book_round_trips_carry():
    book = dict({"bid": "0.50", "ask": "0.51"}, **VC.carry(LIVE))
    s = VC.from_book(book)
    assert s["ok"] is True and s["tick"] == D("0.01")


def test_a_book_that_carries_nothing_is_unverified_not_defaulted():
    s = VC.from_book({"bid": "0.50", "ask": "0.51"})
    assert s["ok"] is False
    assert s["reason"] == "VENUE_CONSTRAINTS_UNVERIFIED"


# ----------------------------------- the gate, end to end in decide()

def params(**kw):
    base = dict(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
                clip_usd=D("0.08"), hedge_enabled=False, reserve_mode="NONE",
                max_exposure_usd=D("5.00"),
                straddle_p_pair_natural=D("0.070"),
                straddle_p_complete=D("0.131"),
                straddle_p_lone_win=D("0.0981"),
                adverse_selection_per_share=D("0"))
    base.update(kw)
    return C.Params(**base)


FLAT = {C.UP: {"qty": "0", "cost": "0"}, C.DOWN: {"qty": "0", "cost": "0"}}
PLAIN = {"bid": "0.60", "ask": "0.61", "age_s": 0.1}


def decide(book=None, **kw):
    return C.decide(book=book or PLAIN, t_in_window_s=10.0, inventory=FLAT,
                    resting=[], params=params(**kw))


def legs(res):
    return [o for o in res["orders"] if o.get("reason") == "STRADDLE_LEG"]


def test_off_by_default_the_decision_is_unchanged(monkeypatch):
    """An unset variable must leave every existing decision byte-identical,
    including on a book that carries no constraints at all."""
    monkeypatch.delenv(VC.REQUIRE_ENV, raising=False)
    assert VC.required() is False
    assert len(legs(decide())) == 2


@pytest.mark.parametrize("raw", ["", "YES", "1", "true", "required",
                                 "REQUIRE_ALL", "NO"])
def test_only_the_exact_token_arms_the_requirement(monkeypatch, raw):
    monkeypatch.setenv(VC.REQUIRE_ENV, raw)
    assert VC.required() is (raw.strip().upper() == "REQUIRE")


def test_when_required_an_unverified_book_holds(monkeypatch):
    monkeypatch.setenv(VC.REQUIRE_ENV, "REQUIRE")
    res = decide()
    assert res["orders"] == []
    assert res["why"] == "VENUE_CONSTRAINTS_UNVERIFIED"


def test_when_required_a_verified_book_still_trades(monkeypatch):
    """NOT a disable switch: with the venue's real numbers present the
    decision is the same one it makes today."""
    monkeypatch.setenv(VC.REQUIRE_ENV, "REQUIRE")
    assert len(legs(decide(book=dict(PLAIN, **VC.carry(LIVE))))) == 2


def test_a_venue_that_disagrees_with_our_params_holds(monkeypatch):
    """If the venue moves its grid under us, we stop rather than submit on a
    stale assumption."""
    monkeypatch.setenv(VC.REQUIRE_ENV, "REQUIRE")
    book = dict(PLAIN, **VC.carry({"minimumTradeQty": 0.05,
                                   "orderPriceMinTickSize": 0.01}))
    res = decide(book=book)
    assert res["orders"] == []
    assert res["why"].startswith("VENUE_CONSTRAINTS_DISAGREE")
    assert "minq=0.05/0.01" in res["why"]


def test_a_venue_tick_change_also_holds(monkeypatch):
    monkeypatch.setenv(VC.REQUIRE_ENV, "REQUIRE")
    book = dict(PLAIN, **VC.carry({"minimumTradeQty": 0.01,
                                   "orderPriceMinTickSize": 0.005}))
    res = decide(book=book)
    assert res["orders"] == []
    assert "tick=0.005/0.01" in res["why"]


def test_the_gate_holds_rather_than_cancelling(monkeypatch):
    """An unverified venue is not a reason to withdraw working orders -- a
    cancel is an action, and this gate's job is to stop NEW ones."""
    monkeypatch.setenv(VC.REQUIRE_ENV, "REQUIRE")
    res = C.decide(book=PLAIN, t_in_window_s=10.0, inventory=FLAT,
                   resting=[{"id": "R1", "side": C.UP, "price": D("0.08"),
                             "qty": D("1.00"), "age_s": 1.0}],
                   params=params())
    assert not [o for o in res["orders"] if o.get("op") == "CANCEL"]


def test_every_existing_safety_gate_still_precedes_it(monkeypatch):
    """The venue gate must not become a way PAST anything. A stale book is
    still refused as a stale book."""
    monkeypatch.setenv(VC.REQUIRE_ENV, "REQUIRE")
    res = C.decide(book=dict(PLAIN, age_s=99.0, **VC.carry(LIVE)),
                   t_in_window_s=10.0, inventory=FLAT, resting=[],
                   params=params())
    assert legs(res) == []
