"""Invariants of the inventory-aware complement controller.

The controller exists because the tail trace showed the engine holding 20 DOWN
at basis 0.5700 and then posting UP at 0.57, 0.40, ... 0.96, 0.97, 0.98, 0.99 --
following its own bid up as the market left, and completing pairs at 1.34.

These tests pin the properties that failure violated. The most important one is
`test_complement_quote_never_rises_because_the_touch_rose`: if that goes red the
defect is back.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P
from src import pmus_completion_shadow as S


def eng(**kw):
    kw.setdefault("clip", D(10))
    kw.setdefault("complement_controller", True)
    kw.setdefault("prospective_cap", False)
    return E.NativeEngine(**kw)


def bbo(bid, ask, t=0.0, last=None, vol="0"):
    return {"kind": "bbo", "t": t, "state": "MARKET_STATE_OPEN",
            "bid": D(str(bid)), "ask": D(str(ask)),
            "last": D(str(last)) if last is not None else D(str(bid)),
            "shares_traded_delta_lower_bound": vol}


# ------------------------------------------------------------------ the cap

def test_cap_is_theta_minus_the_held_basis():
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    assert e.complement_cap("up") == D("0.39")          # 0.96 - 0.57


def test_cap_is_floored_to_the_venue_tick_never_richer():
    e = eng()
    e.bk.add("down", D(30), D("13.30"), maker=True)      # basis 0.443333...
    cap = e.complement_cap("up")
    assert cap == D("0.51")                              # 0.5166... floored
    assert cap + e.bk.avg("down") <= E.THETA


def test_no_cap_applies_when_flat_this_is_not_a_global_ceiling():
    e = eng()
    assert e.complement_cap("up") is None
    assert e.complement_cap("down") is None


def test_no_cap_applies_to_the_side_we_are_already_long():
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    assert e.complement_cap("down") is None              # that is an EXTEND
    assert e.complement_cap("up") is not None            # that is a complement


def test_cap_can_be_non_positive_and_that_means_do_not_quote():
    e = eng()
    e.bk.add("down", D(10), D("9.90"), maker=True)        # basis 0.99
    assert e.complement_cap("up") <= 0


# -------------------------------------------- THE defect this closes

def test_complement_quote_never_rises_because_the_touch_rose():
    """The exact 0600z failure. Basis fixed, touch marching up: the posted
    complement price must not follow it."""
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    posted = []
    for i, bid in enumerate(("0.40", "0.60", "0.78", "0.84",
                             "0.96", "0.97", "0.99")):
        ask = str(min(D(bid) + D("0.01"), D("0.99")))
        for it in e.on_book(bbo(bid, ask, t=float(i))):
            if it.kind == E.REST and it.get("side") == "up":
                posted.append(D(str(it["price"])))
    assert posted, "no complement quote was ever posted"
    assert max(posted) <= D("0.39"), (
        "the complement was chased upward to %s" % max(posted))
    # and every posted quote implies an acceptable PAIR
    for px in posted:
        assert px + e.bk.avg("down") <= E.THETA


def test_the_pair_implied_by_any_complement_quote_never_exceeds_theta():
    """Property test over a grid of bases and touches."""
    for basis in ("0.20", "0.37", "0.5700", "0.80", "0.90"):
        for bid in ("0.05", "0.30", "0.50", "0.70", "0.90", "0.99"):
            e = eng()
            e.bk.add("down", D(20), D(20) * D(basis), maker=True)
            for it in e.on_book(bbo(bid, "0.99" if D(bid) >= D("0.98")
                                    else str(D(bid) + D("0.01")))):
                if it.kind == E.REST and it.get("side") == "up":
                    assert D(str(it["price"])) + D(basis) <= E.THETA


def test_a_capped_quote_is_labelled_so_the_journal_can_tell_them_apart():
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    reasons = [it.get("reason") for it in e.on_book(bbo("0.80", "0.81"))
               if it.kind == E.REST and it.get("side") == "up"]
    assert reasons == ["COMPLEMENT_AT_BASIS_CAP"]


def test_an_uncapped_open_still_says_two_sided_our_touch():
    e = eng()
    reasons = {it.get("side"): it.get("reason")
               for it in e.on_book(bbo("0.40", "0.41")) if it.kind == E.REST}
    assert set(reasons.values()) == {"TWO_SIDED_OUR_TOUCH"}


# ------------------------------------------------------- sequential accounting

def test_the_cap_tightens_when_a_fill_raises_the_basis():
    e = eng()
    e.bk.add("down", D(10), D("5.00"), maker=True)        # basis 0.50
    assert e.complement_cap("up") == D("0.46")
    e.bk.add("down", D(10), D("7.00"), maker=True)        # basis now 0.60
    assert e.complement_cap("up") == D("0.36")


def test_the_cap_loosens_only_when_the_basis_itself_improves():
    e = eng()
    e.bk.add("down", D(10), D("6.00"), maker=True)        # 0.60
    before = e.complement_cap("up")
    e.bk.add("down", D(10), D("2.00"), maker=True)        # 0.40 avg
    after = e.complement_cap("up")
    assert after > before
    # and the pair it implies is still bounded, which is the real invariant
    assert after + e.bk.avg("down") <= E.THETA


def test_the_cap_reads_only_present_inventory_no_future_rows():
    """The cap is a pure function of the Book. Feeding later rows cannot change
    a cap computed now, which is what 'no lookahead' means here."""
    e = eng()
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    cap_now = e.complement_cap("up")
    snapshot = (e.bk.q["down"], e.bk.c["down"])
    e.on_book(bbo("0.99", "0.99", t=5.0))
    assert e.complement_cap("up") == cap_now or (
        (e.bk.q["down"], e.bk.c["down"]) != snapshot)


# ------------------------------------------------------------------ EXTEND

def test_extend_of_the_long_side_is_refused_while_a_complement_is_outstanding():
    e = eng(extend_requires_balance=True)
    e.bk.add("down", D(20), D(20) * D("0.57"), maker=True)
    sides = [it.get("side") for it in e.on_book(bbo("0.40", "0.41"))
             if it.kind == E.REST]
    assert "down" not in sides, "kept buying the side already long"
    assert "up" in sides, "stopped working the complement too"
    assert e.extend_refused_while_imbalanced >= 1


def test_extend_from_flat_is_untouched_because_L5_extends():
    e = eng(extend_requires_balance=True)
    sides = sorted(it.get("side") for it in e.on_book(bbo("0.40", "0.41"))
                   if it.kind == E.REST)
    assert sides == ["down", "up"], "a flat OPEN must quote both sides"
    assert e.extend_refused_while_imbalanced == 0


def test_extend_resumes_once_balance_is_restored():
    e = eng(extend_requires_balance=True)
    e.bk.add("down", D(10), D("5.00"), maker=True)
    e.bk.add("up", D(10), D("4.00"), maker=True)          # balanced again
    sides = sorted(it.get("side") for it in e.on_book(bbo("0.40", "0.41"))
                   if it.kind == E.REST)
    assert sides == ["down", "up"]


# --------------------------------------------------------- fill model honours it

def test_a_resting_order_cannot_fill_above_its_own_limit():
    v = P.ReplayVenue(respect_limit=True)
    e = eng()
    row = bbo("0.90", "0.91", last="0.90", vol="500")
    got = v.fills_for(e, row, "up", D("0.39"), D(10), None)
    assert got is None, "filled a 0.39 bid at a 0.90 touch"


def test_a_resting_order_fills_at_its_own_limit_not_at_a_cheaper_touch():
    v = P.ReplayVenue(respect_limit=True)
    e = eng()
    row = bbo("0.30", "0.31", last="0.30", vol="500")
    got = v.fills_for(e, row, "up", D("0.39"), D(10), None)
    if got is not None:
        _qty, fpx = got
        assert fpx == D("0.39"), "took a discount the venue never gave us"


def test_the_frozen_arm_keeps_the_old_fill_model_exactly():
    assert P.ReplayVenue().respect_limit is False


# ----------------------------------------------------------------- NO inversion

def test_the_down_side_is_quoted_in_own_terms_one_minus_ask():
    """A NO inversion here would make every DOWN cap nonsense, so it is pinned
    at the quoting site and not only in the tape audit."""
    e = eng()
    got = {it.get("side"): D(str(it["price"]))
           for it in e.on_book(bbo("0.40", "0.45")) if it.kind == E.REST}
    assert got["up"] == D("0.40")
    assert got["down"] == D(1) - D("0.45")


def test_a_down_complement_cap_uses_the_up_basis():
    e = eng()
    e.bk.add("up", D(20), D(20) * D("0.30"), maker=True)
    assert e.complement_cap("down") == D("0.66")          # 0.96 - 0.30
    assert e.complement_cap("up") is None


# ------------------------------------------------------------------- fees

def test_no_maker_fee_is_assumed_in_the_cap_and_none_is_charged():
    """The cap is theta - basis with no fee term because a MAKER fill pays no
    fee at this venue. If the book ever charges one on a maker fill the cap is
    wrong, so both halves are pinned together."""
    bk = S.Book()
    bk.add("up", D(10), D("4.00"), maker=True)
    assert bk.fees == 0
    bk2 = S.Book()
    bk2.add("up", D(10), D("4.00"), maker=False)
    assert bk2.fees > 0


def test_frozen_checkpoint_is_untouched_by_the_controller_defaults():
    from scripts.audit_economic_complete import load, USABLE
    by = load()
    tot = sum(P.net_of(P.replay_window(by[s], winner=w, clip=D(50)))
              for s, w in USABLE.items())
    assert tot / len(USABLE) == D("6.116725359099999999999999992")
