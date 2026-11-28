"""A first leg needs a complement book to complete against.

THE EVIDENCE, from the 290 recorded ladders of 2026-10-25 bucketed by
seconds remaining:

    tau 600-900  100% two-sided    50 ask levels
    tau 300-420  100% two-sided    32
    tau 120-180  100% two-sided     8
    tau  60-120   56% two-sided     1
    tau   0- 60    0% two-sided     0   <- NO ASK AT ALL

Spread is 1.00c throughout and touch depth is 300-1100 contracts against
clips of 0.17-2, so depth never binds early. Late, one side simply ceases
to exist, and a pair cannot be completed against a side with no orders.

AND `no_new_entry_after_s` IS 270 OF A 300-SECOND WINDOW, so first legs
were admitted down to tau=30 -- inside the 0%-two-sided regime. The 0130z
window is the recorded consequence: first leg filled, complement had no
ask in all eight late frames, pair never completed, settled -$1.1594.

WHAT THIS IS NOT, and the distinction is the whole point. Requiring the
complement to be CHEAP at entry would have refused 7 of 7 recorded
episodes, 6 of which went on to complete below par at 0.72-0.96 -- the
complement gets cheaper later, so a price test at entry is wrong. This
requires only that the complement EXISTS with depth.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C


@pytest.fixture(autouse=True)
def controller_on(monkeypatch):
    """`decide` short-circuits to CONTROLLER_OFF before any gate runs, so
    every test here needs the switch. My first version of this file
    omitted it and 10 of 17 tests failed against a decision that had
    never been made."""
    monkeypatch.setenv(C.CONTROLLER_ENV, "ON")


def params(**kw):
    base = {"min_complement_shares": D("100")}
    base.update(kw)
    return C.Params(**base)


def book(bid="0.50", ask="0.51", bid_shares=None, ask_shares=None,
         age_s=0.1):
    return {"bid": D(bid), "ask": D(ask), "age_s": age_s,
            "bid_shares": bid_shares, "ask_shares": ask_shares}


def flat():
    return {C.UP: {"qty": "0", "cost": "0"},
            C.DOWN: {"qty": "0", "cost": "0"}}


def decide(p, bk, t=60.0, p_up=0.8):
    """p_up picks the FAVOURITE, which picks which side is being opened,
    which picks which depth is the complement's. Default 0.8 -> UP."""
    return C.decide(book=bk, t_in_window_s=t, inventory=flat(),
                    resting=[], params=p, p_up=p_up, p_source="TEST")


#: THE BAND IS 0.51-0.90 ON THE FAVOURITE'S BID, so a book outside it is
#: refused OUT_OF_BAND before any depth is consulted -- which is what
#: masked four of these tests in my first version.
UP_FAV = dict(bid="0.60", ask="0.61")      # UP bid 0.60, in band
DN_FAV = dict(bid="0.35", ask="0.36")      # DOWN bid 1-0.36 = 0.64, in band


# ------------------------------------------------------- OFF by default

def test_the_gate_is_off_in_the_shipped_defaults():
    assert C.Params().min_complement_shares == D("0")


@pytest.mark.parametrize("shares", [None, "0", "1", "99999"])
def test_while_off_depth_is_never_consulted(shares):
    """Unset means unchanged: the shipped engine must behave identically
    whatever the complement depth is."""
    p = C.Params()                      # gate off
    for sh in (None, D("0"), D("5000")):
        r = decide(p, book(bid_shares=sh, ask_shares=sh))
        assert "COMPLEMENT_DEPTH" not in str(r.get("why") or "")


# --------------------------------- ON: the complement must exist

def test_a_present_complement_with_depth_does_not_block():
    r = decide(params(), book(bid_shares=D("900"), ask_shares=D("900"),
                              **UP_FAV))
    assert "COMPLEMENT_DEPTH" not in str(r.get("why") or ""), r


@pytest.mark.parametrize("short", [D("0"), D("5"), D("99.99")])
def test_a_thin_complement_blocks_an_UP_first_leg(short):
    """THE 0130z CASE. Opening UP needs the DOWN book, whose depth IS the
    UP side's BID depth. No bid, no completion."""
    r = decide(params(), book(bid_shares=short, ask_shares=D("900"),
                              **UP_FAV))
    assert "COMPLEMENT_DEPTH_SHORT" in str(r.get("why") or ""), r


@pytest.mark.parametrize("short", [D("0"), D("5"), D("99.99")])
def test_a_thin_complement_blocks_a_DOWN_first_leg(short):
    """The mirror: opening DOWN needs the UP book, i.e. the ASK depth."""
    r = decide(params(), book(bid_shares=D("900"), ask_shares=short,
                              **DN_FAV), p_up=0.2)
    assert "COMPLEMENT_DEPTH_SHORT" in str(r.get("why") or ""), r


def test_the_OTHER_sides_depth_is_irrelevant_to_a_first_leg():
    """Only the complement matters. Opening UP with a deep bid is fine
    even if the ask is empty -- we are not buying from the ask."""
    r = decide(params(), book(bid_shares=D("900"), ask_shares=D("0"),
                              **UP_FAV))
    assert "COMPLEMENT_DEPTH" not in str(r.get("why") or ""), r


def test_unreadable_depth_refuses_rather_than_assuming():
    """A book we cannot measure is not one we can rely on completing
    against. Fail closed."""
    r = decide(params(), book(bid_shares=None, ask_shares=None,
                              **UP_FAV))
    assert "COMPLEMENT_DEPTH_UNREADABLE" in str(r.get("why") or ""), r


@pytest.mark.parametrize("junk", ["abc", "", "nan"])
def test_junk_depth_is_unreadable_not_zero(junk):
    r = decide(params(), book(bid_shares=junk, ask_shares=junk,
                              **UP_FAV))
    assert "COMPLEMENT_DEPTH" in str(r.get("why") or ""), r


def test_the_threshold_is_honoured_exactly():
    at = decide(params(min_complement_shares=D("500")),
                book(bid_shares=D("500"), ask_shares=D("500"), **UP_FAV))
    assert "COMPLEMENT_DEPTH" not in str(at.get("why") or "")
    under = decide(params(min_complement_shares=D("500")),
                   book(bid_shares=D("499.99"), ask_shares=D("500"),
                        **UP_FAV))
    assert "COMPLEMENT_DEPTH_SHORT" in str(under.get("why") or "")


# ------------------------------- it gates OPENINGS, not completions

def test_it_never_blocks_a_hedge_that_reduces_inventory():
    """A held leg must stay completable: the gate is on FIRST LEGS. With
    UP held, the decision path is the hedge, and refusing it would strand
    inventory the engine could still close."""
    inv = {C.UP: {"qty": "2", "cost": "1.00"},
           C.DOWN: {"qty": "0", "cost": "0"}}
    r = C.decide(book=book(bid_shares=D("0"), ask_shares=D("900"),
                           **UP_FAV),
                 t_in_window_s=60.0, inventory=inv, resting=[],
                 params=params(), p_up=0.8, p_source="TEST")
    #: whatever it decides, it is not refused for want of complement depth
    assert "COMPLEMENT_DEPTH" not in str(r.get("why") or ""), r


def test_the_observed_late_window_shape_is_refused_and_the_early_is_not():
    """The two rows of the liquidity table that matter, as a test.
    tau<60 had NO ASK (ask_shares 0); tau 300-420 had bid 533 / ask 308."""
    #: tau<60 had NO ASK, so a DOWN first leg (which buys UP) is refused
    late = decide(params(min_complement_shares=D("100")),
                  book(bid_shares=D("2102"), ask_shares=D("0"), **DN_FAV),
                  p_up=0.2)
    assert "COMPLEMENT_DEPTH_SHORT" in str(late.get("why") or ""), late
    #: tau 300-420 had bid 533 / ask 308 -- both sides admit either leg
    for pu, bk in ((0.8, UP_FAV), (0.2, DN_FAV)):
        early = decide(params(min_complement_shares=D("100")),
                       book(bid_shares=D("533"), ask_shares=D("308"), **bk),
                       p_up=pu)
        assert "COMPLEMENT_DEPTH" not in str(early.get("why") or ""), early
