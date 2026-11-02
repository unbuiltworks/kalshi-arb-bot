"""The residual cap on a fill-contingent worst case, both call sites.

Established from the recovered tape, 2026-10-31. Order fe3923af's own
RESIDUAL_CAP note at 14:21:01.823751Z read:

    residual_realized      "2.17"    UP 2.17 HELD
    outstanding_residual  "-2.17"    a DOWN 2.17 completion RESTING
    prospective_before     "0.00"    netted to flat
    prospective_book_flat   true     -> the opening-leg exemption fired
    binds                   false    -> NOT REFUSED

The position became UP 4.25 against a 3-contract ceiling when the DOWN leg
was cancelled unfilled. A resting opposite-side order is an intention, not a
completed hedge.

`residual_cap_decision` is now the ONE definition, called by the gate in
`handle` (before record_submit) and by the final guard immediately before
the wire (after it). These tests drive that function directly for both call
shapes, so the primary gate is verified on its own terms and does not depend
on the guard to correct it.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX


K = D(2)

#: THE CAP IN FORCE WHEN THE INCIDENT HAPPENED, pinned deliberately.
#:
#: `ABS_RESIDUAL_CAP_CONTRACTS` was raised 3 -> 5 on owner authorization
#: 2026-11-02. The incident's residual peak was 4.25, which is BELOW 5, so
#: evaluated against the LIVE constant every `binds is True` assertion in
#: this file inverts and the file would appear to prove that the incident
#: order is fine.
#:
#: Rewriting those verdicts to False is how a regression test for a real
#: realized loss gets quietly deleted. So this file is pinned to the cap
#: that was ACTUALLY IN FORCE on 2026-10-31 instead. The arithmetic it
#: checks -- fill-contingent worst case, the resting-order exemption, both
#: call shapes -- is unchanged and still verified; only the ceiling it is
#: measured against is held fixed, which also insulates the file from every
#: future limit change.
#:
#: The consequence of the LIVE cap is not hidden: it is asserted directly by
#: `test_cap_five_readmits_the_1415z_order_six` at the end of this file.
#: captured AT IMPORT, before the autouse fixture can patch it, so the
#: consequence test below reads the LIVE constant and not the pinned one.
LIVE_CAP_AT_IMPORT = LX.ABS_RESIDUAL_CAP_CONTRACTS

CAP_IN_FORCE_AT_THE_INCIDENT = D("3")


@pytest.fixture(autouse=True)
def _pin_the_incident_cap(monkeypatch):
    monkeypatch.setattr(LX, "ABS_RESIDUAL_CAP_CONTRACTS",
                        CAP_IN_FORCE_AT_THE_INCIDENT)



def dec(resid, same, other, qty, side, *, clip=None, included=False):
    return LX.residual_cap_decision(
        resid=D(str(resid)), same_side=D(str(same)), other_side=D(str(other)),
        qty=D(str(qty)), side=side, k=K,
        clip=D(str(clip if clip is not None else qty)),
        same_side_includes_this_order=included)


# =================================================== THE RECOVERED 14:21 STATE

def test_the_primary_gate_now_refuses_the_recovered_state():
    """Before record_submit: resid 2.17, same-side 0, DOWN 2.17 resting."""
    r = dec("2.17", "0", "2.17", "2.08", "UP", clip="2.17")
    assert r["binds"] is True
    assert r["worst_case_before"] == "2.17"
    assert r["worst_case_after"] == "4.25"
    assert r["cap_contracts"] == "3"
    assert r["prospective_book_flat"] is False
    assert r["nothing_held"] is False


def test_the_final_guard_now_refuses_the_recovered_state():
    """After record_submit: this order is in the same-side sum."""
    r = dec("2.17", "2.08", "2.17", "2.08", "UP", clip="2.17", included=True)
    assert r["binds"] is True
    assert r["worst_case_before"] == "2.17"
    assert r["worst_case_after"] == "4.25"


def test_both_call_shapes_agree_on_the_recovered_state():
    """The whole point of one shared definition."""
    a = dec("2.17", "0", "2.17", "2.08", "UP", clip="2.17")
    b = dec("2.17", "2.08", "2.17", "2.08", "UP", clip="2.17", included=True)
    for k in ("binds", "worst_case_before", "worst_case_after",
              "abs_worst_after", "cap_contracts", "prospective_book_flat"):
        assert a[k] == b[k], (k, a[k], b[k])


def test_the_old_netted_basis_is_reported_and_is_the_wrong_answer():
    """It reproduces the tape's 0.00, which is why it is kept beside."""
    r = dec("2.17", "0", "2.17", "2.08", "UP", clip="2.17")
    assert r["netted_view_would_have_said"] == "0.00"
    assert r["basis"] == "FILL_CONTINGENT_WORST_CASE"


# =================================================== A RESTING OPPOSITE LEG

@pytest.mark.parametrize("other", ["0.01", "1.00", "2.17", "5.00", "99"])
def test_a_resting_opposite_leg_never_reduces_the_worst_case(other):
    """However large, it cannot license more same-side exposure."""
    r = dec("2.17", "0", other, "2.08", "UP", clip="2.17")
    assert r["worst_case_after"] == "4.25", other
    assert r["binds"] is True, other


def test_a_resting_opposite_leg_does_not_make_a_held_book_flat():
    r = dec("2.17", "0", "2.17", "0.01", "UP", clip="2.17")
    assert r["nothing_held"] is False
    assert r["nothing_resting"] is False
    assert r["prospective_book_flat"] is False


# =================================================== SAME-SIDE RESTING ORDERS

def test_same_side_resting_orders_count_toward_maximum_exposure():
    """Two UP legs resting plus a third: all three count."""
    r = dec("0", "2.00", "0", "1.50", "UP", clip="2.00")
    # 0 held + 2.00 resting UP + 1.50 this = 3.50 worst case
    assert r["worst_case_after"] == "3.50"
    assert r["binds"] is True


def test_simultaneous_resting_orders_on_both_sides():
    """UP 1.00 and DOWN 3.00 resting, held flat, new UP 2.50.

    The DOWN 3.00 is given NO credit: worst case is 1.00 + 2.50 = 3.50.
    """
    r = dec("0", "1.00", "3.00", "2.50", "UP", clip="2.00")
    assert r["worst_case_after"] == "3.50"
    assert r["outstanding_other_side"] == "3.00"
    assert r["binds"] is True


# =================================================== PARTIAL FILLS

def test_a_partial_fill_is_carried_by_realized_and_remaining_by_outstanding():
    """1.00 of a 2.17 UP leg filled; 1.17 still resting.

    `worst_case_remaining` is what feeds same_side, so the unfilled part is
    still counted. 1.00 held + 1.17 resting + 2.08 new = 4.25.
    """
    r = dec("1.00", "1.17", "0", "2.08", "UP", clip="2.17")
    assert r["worst_case_after"] == "4.25"
    assert r["binds"] is True


def test_a_partial_completion_does_not_credit_its_unfilled_remainder():
    """DOWN 2.17 partially filled 1.00: the 1.00 IS netted into realized.

    Realized residual carries the filled 1.00 (2.17 UP - 1.00 DOWN = 1.17),
    and the unfilled 1.17 DOWN is other_side, which earns no credit.
    """
    r = dec("1.17", "0", "1.17", "2.08", "UP", clip="2.17")
    assert r["worst_case_after"] == "3.25"
    assert r["binds"] is True


# =================================================== CANCELLATIONS

def test_a_cancelled_opposite_leg_leaves_the_same_verdict():
    """The 1415z sequel: the DOWN leg is gone and nothing changes.

    This is the property the old basis lacked -- its verdict depended on a
    leg that could vanish, and did.
    """
    before = dec("2.17", "0", "2.17", "2.08", "UP", clip="2.17")
    after = dec("2.17", "0", "0", "2.08", "UP", clip="2.17")
    assert before["binds"] is after["binds"] is True
    assert before["worst_case_after"] == after["worst_case_after"] == "4.25"


def test_a_cancelled_same_side_leg_correctly_relaxes_the_bound():
    """Same-side cancellation genuinely reduces potential exposure.

    Two UP legs resting (2.00 together) plus a new 1.50 is a 3.50 worst
    case and binds. Cancel 1.50 of the resting pair, leaving 0.50, and the
    same new order is a 2.00 worst case and passes. Unlike an opposite-side
    cancellation, this one SHOULD change the verdict -- the exposure really
    is smaller.
    """
    with_leg = dec("0", "2.00", "0", "1.50", "UP", clip="2.00")
    assert with_leg["worst_case_after"] == "3.50"
    assert with_leg["binds"] is True
    without = dec("0", "0.50", "0", "1.50", "UP", clip="2.00")
    assert without["worst_case_after"] == "2.00"
    assert without["binds"] is False


# =================================================== RISK-REDUCING LEGS

def test_a_completion_against_held_inventory_is_never_refused():
    r = dec("2.17", "0", "0", "2.17", "DOWN", clip="2.17")
    assert r["increases_abs_prospective"] is False
    assert r["binds"] is False


@pytest.mark.parametrize("held", ["3.00", "5.00", "9.99"])
def test_a_completion_is_never_refused_at_any_depth(held):
    """Even far outside the cap, the leg that reduces it must go."""
    r = dec(held, "0", "0", held, "DOWN", clip=held)
    assert r["binds"] is False, held


def test_an_overshooting_completion_that_worsens_the_book_still_binds():
    """DOWN 9.00 against UP 2.17 lands at 6.83, which is worse, so it binds."""
    r = dec("2.17", "0", "0", "9.00", "DOWN", clip="2.17")
    assert r["abs_worst_after"] == "6.83"
    assert r["increases_abs_prospective"] is True
    assert r["binds"] is True


# =================================================== GENUINELY FLAT BOOKS

def test_a_genuinely_flat_book_exempts_the_opening_leg():
    r = dec("0", "0", "0", "2.90", "UP")
    assert r["nothing_held"] is True
    assert r["nothing_resting"] is True
    assert r["prospective_book_flat"] is True
    assert r["binds"] is False


def test_a_flat_book_at_the_guard_sees_only_this_order_resting():
    r = dec("0", "2.90", "0", "2.90", "UP", included=True)
    assert r["prospective_book_flat"] is True
    assert r["binds"] is False


@pytest.mark.parametrize("qty", ["0.01", "3.00", "8.00", "50"])
def test_a_first_leg_is_never_capped_by_its_own_size(qty):
    r = dec("0", "0", "0", qty, "UP")
    assert r["binds"] is False, qty


def test_a_book_that_is_NOT_flat_loses_the_exemption():
    """Held zero but something resting opposite: not a first leg."""
    r = dec("0", "0", "0.01", "8.00", "UP")
    assert r["nothing_resting"] is False
    assert r["prospective_book_flat"] is False
    assert r["binds"] is True


# =================================================== STALE / UNREADABLE STATE

def test_an_unreadable_side_counts_on_both_sides(tmp_path):
    """A row we cannot attribute must never shrink a worst case."""
    st = LX.InventoryStore(str(tmp_path / "s.json"))
    live = st.state.setdefault("live_orders", {})
    live["weird"] = {"market": "m", "side": "???", "price": "0.25",
                     "qty": "1.00", "remaining": "1.00"}
    st.save()
    assert st.outstanding_on_side("m", "UP") == D("1.00")
    assert st.outstanding_on_side("m", "DOWN") == D("1.00")


def test_an_unsized_row_counts_at_the_ceiling(tmp_path):
    """worst_case_remaining fails closed on a row it cannot size."""
    st = LX.InventoryStore(str(tmp_path / "s2.json"))
    live = st.state.setdefault("live_orders", {})
    live["nosize"] = {"market": "m", "side": "UP", "price": "0.25"}
    st.save()
    assert st.outstanding_on_side("m", "UP") >= LX.ABS_RESIDUAL_CAP_CONTRACTS


def test_equality_at_the_cap_passes():
    """Stated in the comparison string, so it is pinned."""
    r = dec("0", "0", "0.01", "3.00", "UP", clip="1.50")
    assert r["abs_worst_after"] == "3.00"
    assert r["cap_contracts"] == "3"
    assert r["binds"] is False


def test_the_absolute_ceiling_always_wins_over_a_large_clip():
    r = dec("2.00", "0", "0", "2.00", "UP", clip="37.73")
    assert r["which_binds"] == "ABSOLUTE"
    assert r["cap_contracts"] == "3"


# ============================================ THE CONSEQUENCE OF THE RAISE
#
# Deliberately OUTSIDE the autouse fixture's effect by reading the module
# constant itself, so it reports the LIVE cap rather than the pinned one.

def test_cap_five_readmits_the_1415z_order_six():
    """THE RAISE 3 -> 5 ADMITS THE ORDER THAT CAUSED THE INCIDENT.

    Owner-authorized on 2026-11-02 and recorded here rather than discovered
    again by a loss. The incident peak was |residual| 4.25 against a cap of
    3; 4.25 < 5, so the order six that the cap of 3 should have refused is
    no longer refused by the residual cap at all.

    WHAT STILL BOUNDS IT, since this ceiling no longer does: the $10
    campaign additional-loss gate, the $12 cash cap, and the $1.50
    worst-case-deficit ceiling. The residual cap was the tightest bound on
    CONTRACT COUNT and is now the loosest.

    If this test fails because the cap moved again, that is the point: the
    cost of the change has to be read before it is made.
    """
    peak = D("4.25")
    assert LIVE_CAP_AT_IMPORT == D("5"), (
        "the live cap moved; re-read what it now admits")
    assert peak < LIVE_CAP_AT_IMPORT, (
        "the incident peak is no longer under the cap -- update this record")
    assert peak > CAP_IN_FORCE_AT_THE_INCIDENT, (
        "the incident peak must exceed the cap that should have refused it")
