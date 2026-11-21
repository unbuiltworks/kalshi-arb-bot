"""The venue's position is the position of record, and adopting it can never
reduce measured exposure.

THE DEFECT, measured 2026-10-14. `reconcile_from_venue`'s docstring promised
"where they disagree the venue's net becomes the position of record"; it named
the market in `gross_detail_lost` and left our book alone. With our book at
7.00 UP against a venue row of 10.34 UP, `positions` kept reading 7.00 -- and
`positions` is what `risk_exposure_usd`, and therefore the $5 gate, reads. The
understatement was 3.34 shares, in the only direction that lets the gate
under-bind.

Each test below is one of the five scenarios the repair must hold for.
"""
from __future__ import annotations

import pathlib
import tempfile
from decimal import Decimal as D

import pytest

import src.l5_live_execution as EX

M = "cpc-btc-updown-15m-2026-09-25-1945z"
UP, DOWN = "%s|UP" % M, "%s|DOWN" % M


def store(positions=None, leg_cost=None):
    st = EX.InventoryStore(str(pathlib.Path(tempfile.mkdtemp()) / "i.json"))
    if positions:
        st.state["positions"] = dict(positions)
    if leg_cost:
        st.state["leg_cost"] = dict(leg_cost)
    return st


def venue(net, *, bought=None, sold="0", market=M):
    return {"marketSlug": market, "qtyBoughtDecimal": bought or net,
            "qtySoldDecimal": sold, "netPositionDecimal": net}


# ------------------------------------------- 1. venue greater than internal
def test_venue_position_greater_than_ours_is_adopted_and_raises_the_bound():
    """The exact measured defect."""
    st = store({UP: "7.00"}, {UP: "2.10"})
    assert st.risk_exposure_usd(M) == D("2.10")
    out = st.reconcile_from_venue([venue("10.34")], [])
    assert st.state["positions"] == {UP: "10.34"}
    assert st.risk_exposure_usd(M) >= D("10.34")
    assert out["gross_detail_lost"] == [M]
    assert out["ALERT"] and "VENUE POSITION ADOPTED" in out["ALERT"]


# ------------------------------------------- 2. venue smaller than internal
def test_a_smaller_venue_position_cannot_lower_the_measured_bound():
    """THE ONE THAT MATTERS MOST. A venue row with FEWER shares must not be
    allowed to shrink our exposure estimate: a net is consistent with many
    gross books, and the legs can lose more than the net suggests."""
    st = store({UP: "10.00"}, {UP: "9.00"})
    assert st.risk_exposure_usd(M) == D("9.00")
    st.reconcile_from_venue([venue("4.00")], [])
    assert st.state["positions"] == {UP: "4.00"}
    assert st.risk_exposure_usd(M) >= D("9.00"), "bound was allowed to shrink"
    assert st.state["risk_floor_usd"][M] == "9.00"


# ------------------------------------------------ 3. offsetting gross legs
def test_a_net_that_contradicts_a_matched_pair_takes_over_and_preserves_it():
    """Probe 07's shape: 2.47 UP + 5.28 DOWN nets -2.81, and a venue row of
    9 UP contradicts it. The venue wins the position of record; the pair is
    preserved as evidence rather than deleted."""
    st = store({UP: "2.47", DOWN: "5.28"}, {UP: "1.00", DOWN: "2.00"})
    out = st.reconcile_from_venue([venue("9")], [])
    assert st.state["positions"] == {UP: "9"}
    assert out["gross_net_agrees"][M] is False
    q = st.state["gross_quarantine"][M]
    assert q["positions"] == {UP: "2.47", DOWN: "5.28"}
    assert q["leg_cost"] == {UP: "1.00", DOWN: "2.00"}
    # the adopted shares carry an UPPER-BOUND basis, flagged as unknown
    assert st.state["leg_cost"][UP] == "9"
    assert UP in st.state["leg_cost_basis_unknown"]


# --------------------------------------------- 4. venue reports no position
def test_venue_silence_is_not_divergence():
    """The venue drops a position the moment the market expires, while our
    book legitimately holds it until the redemption is booked. Treating that
    silence as disagreement would mark the book uncertain after every settled
    window."""
    st = store({UP: "3.00"}, {UP: "1.00"})
    out = st.reconcile_from_venue([], [])
    assert st.state["positions"] == {UP: "3.00"}
    assert st.risk_exposure_usd(M) == D("1.00")
    assert out["uncertainty_cleared"] is True
    assert st.orders_uncertain() is None
    assert out["ALERT"] is None
    assert not st.state.get("risk_floor_usd")


def test_a_market_the_venue_holds_and_we_never_booked_is_priceable():
    """Adopting a quantity with no basis used to leave the book INCOHERENT,
    so `risk_exposure_usd` raised instead of returning a bound -- fail-closed
    for new orders and useless for the risk-reducing actions that must stay
    available."""
    st = store()
    st.reconcile_from_venue([venue("10.34")], [])
    assert st.state["positions"] == {UP: "10.34"}
    assert st.book_violations(M) == []
    assert st.risk_exposure_usd(M) == D("10.34")   # $1 a share, the bound


# ------------------------------------------------------ 5. recovery
def test_uncertainty_and_the_floor_clear_when_the_books_agree_again():
    st = store({UP: "7.00"}, {UP: "2.10"})
    st.reconcile_from_venue([venue("10.34")], [])
    assert st.orders_uncertain()
    assert M in st.state["risk_floor_usd"]
    # a later read where our book matches the venue is authoritative again
    #: THE COST BECOMING KNOWN IS TWO FACTS, NOT ONE. Writing `leg_cost`
    #: alone left the market still named in `leg_cost_basis_unknown`, and
    #: the floor is now held by an unknown basis as well as by dispute --
    #: because adoption RESOLVES dispute by construction, so keying the
    #: floor to dispute alone deleted it in the same pass that raised it
    #: (cpc-btc-updown-15m-2026-11-18-2215z, 2026-11-18: $5 floor at
    #: 22:22:56Z, risk_floor_usd {} by 22:28:12Z, basis still unknown).
    #: The real path that learns a cost -- the attestation in
    #: `attest_other_strategy_cost` -- clears BOTH together and pops the
    #: floor itself, so no production state has a known cost still
    #: flagged unknown. This fixture now matches that.
    st.state["leg_cost"] = {UP: "1.20"}
    st.state["leg_cost_basis_unknown"] = []
    out = st.reconcile_from_venue([venue("10.34")], [])
    assert out["uncertainty_cleared"] is True
    assert not st.state["risk_floor_usd"]
    assert st.risk_exposure_usd(M) == D("1.20")


# ------------------------------------- the gates read the conservative state
def test_new_exposure_is_refused_while_reconciliation_is_uncertain():
    st = store({UP: "7.00"}, {UP: "2.10"})
    st.reconcile_from_venue([venue("10.34")], [])
    why = EX.SafetyLimits().refuse(
        price=D("0.5"), qty=D(1), market=M, committed_by_market={},
        residual_after=D(0), realized_today=D(0), open_markets=[M],
        outstanding_by_market={}, live_order_count=0,
        orders_uncertain=st.orders_uncertain(),
        risk_by_market={M: st.risk_exposure_usd(M)})
    assert why == "ORDER_STATE_UNCERTAIN"


def test_the_floor_is_a_lower_bound_not_a_replacement():
    """A proposed leg must still make the bound grow past the floor, or the
    floor would mask the very order being gated."""
    st = store({UP: "7.00"}, {UP: "2.10"})
    st.reconcile_from_venue([venue("10.34")], [])
    base = st.risk_exposure_usd(M)
    with_extra = st.risk_exposure_usd(M, extra_side="UP", extra_qty=D("5"),
                                      extra_price=D("1"))
    assert with_extra > base
