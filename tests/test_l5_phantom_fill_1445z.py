"""The 1445z phantom DOWN fill, and the cap exemption it unlocked.

THE INCIDENT, 2026-11-04T14:47:13Z, campaign camp1.9f3c71a4e2b84d05c6178e3b.
Window cpc-btc-updown-15m-2026-11-04-1445z. The venue-authoritative
cost-basis audit records, for that market's UP leg:

    basis: {established: false, reason: "QUANTITY_MISMATCH",
            venue_qty: "2.0000", ledger_qty: "1.0000"}
    fills: 0.87@0.26 + 0.13@0.26 (order DGZEMA9013D1)
           0.65@0.25 + 0.35@0.25 (order DGZEXF7013D3)

So the VENUE held 2.0000 UP contracts for $0.5100, in two orders each
filled in two pieces. Our ledger held 1.0000 and no DOWN at all, and
`committed_released` was 1.2500 -- $0.51 of real UP plus $0.74 of a DOWN
leg that never became a position.

THE MECHANISM. `l5_pmus_live_runner` books order progress with
`side=str(o.get("side") or "")`, read from its own `live_orders` record,
and `apply_order_progress` trusted it. The venue's order row carries no
side to check it against, so a wrong side was unfalsifiable. A real UP
execution at WIRE 0.26 booked as DOWN has own price 1 - 0.26 = 0.74, which
is exactly the phantom leg our fill stream reported.

THE CONSEQUENCE, and the reason this is a safety defect and not only an
accounting one. The phantom DOWN netted the held UP contract to zero, so
`residual_cap_decision` read `nothing_held` true, took the `exempt_open`
branch that exists so a FIRST order cannot be refused by a bound derived
from its own size, and waived the cap for a second opening. Real venue
exposure reached 2 contracts against a 1-contract cap.

Because the basis could not be established the leg was booked at the
$1-a-share placeholder, so the window reported -$1.00 against a true
-$0.51.

TWO CORRECTIONS ARE PINNED HERE:

  * the only side that may be booked under a client order id is the side
    that was SUBMITTED under it; a disagreement is refused and the store is
    marked uncertain, because nothing in-process can tell which reading is
    the real order;
  * an apparent flat book may not exempt an opening from the cap while the
    book is uncertain.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX

MKT = "cpc-btc-updown-15m-2026-11-04-1445z"
COID = "l5f-phantom-1445z"


def mkstore(tmp_path):
    return LX.InventoryStore(str(tmp_path / "inv.json"))


def submitted(st, coid=COID, market=MKT, side="UP"):
    st.state.setdefault("submitted", {})[coid] = {
        "session_id": "camp.44eef799f5c1d211c4f80626",
        "market": market, "side": side}
    st.save()


def prog(cum, *, px="0.26", quantity="1", leaves=None, state="ORDER_STATE_NEW"):
    row = {"id": "DGZEMA9013D1", "state": state, "quantity": quantity,
           "cumQuantity": str(cum), "avgPx": px}
    if leaves is not None:
        row["leavesQuantity"] = str(leaves)
    return LX.order_progress(row)


def cap(**kw):
    base = dict(k=D("1"), clip=D("1"), other_side=D("0"),
                same_side=D("0"), qty=D("1"), side="UP",
                same_side_includes_this_order=False)
    base.update(kw)
    return LX.residual_cap_decision(**base)


# ===================================================== 1. THE REPRODUCTION
def test_the_1445z_phantom_down_fill_is_refused(tmp_path):
    """The exact event: a UP order's progress offered as a DOWN fill."""
    st = mkstore(tmp_path)
    submitted(st, side="UP")
    out = st.apply_order_progress(COID, prog("1"), market=MKT, side="DOWN")
    assert str(out.get("refused", "")).startswith("SUBMITTED_SIDE_DISAGREES")
    assert "DOWN" in out["refused"] and "UP" in out["refused"]
    # nothing was booked on EITHER leg
    assert st.position(MKT, "DOWN") == D("0")
    assert st.position(MKT, "UP") == D("0")
    assert st.leg_cost(MKT)["DOWN"] == D("0")


def test_the_refusal_marks_the_store_uncertain(tmp_path):
    """So exposure-increasing submissions fail closed until reconciled."""
    st = mkstore(tmp_path)
    submitted(st, side="UP")
    assert st.orders_uncertain() is None
    st.apply_order_progress(COID, prog("1"), market=MKT, side="DOWN")
    why = st.orders_uncertain()
    assert why is not None
    assert "SUBMITTED_SIDE_DISAGREES" in why


def test_the_phantom_cost_0_74_is_never_written(tmp_path):
    """own(DOWN, wire 0.26) = 0.74 -- the figure the fill stream reported."""
    st = mkstore(tmp_path)
    submitted(st, side="UP")
    st.apply_order_progress(COID, prog("1", px="0.26"), market=MKT,
                            side="DOWN")
    assert D(str(st.state.get("committed", {}).get(MKT, 0))) == D("0")


def test_a_market_disagreement_is_refused_too(tmp_path):
    st = mkstore(tmp_path)
    submitted(st, market=MKT, side="UP")
    out = st.apply_order_progress(COID, prog("1"),
                                  market="cpc-btc-updown-15m-2026-11-04-1500z",
                                  side="UP")
    assert str(out.get("refused", "")).startswith("SUBMITTED_MARKET_DISAGREES")
    assert st.position(MKT, "UP") == D("0")


# ============================================ 2. THE CAP EXEMPTION IT USED
def test_uncertainty_withdraws_the_flat_book_exemption():
    """The exemption is withdrawn; whether that CHANGES the verdict is
    then ordinary cap arithmetic.

    AT k=1 THE EXEMPTION IS NOT LOAD-BEARING, and saying so is the point.
    `cap = min(k * max(qty, clip), ABS)`, so at k=1 a first order of q gets
    a cap of at least q and `a_abs > cap` is false whatever the exemption
    says. The 1445z second opening was therefore NOT permitted by the
    exemption -- it was permitted because `resid` itself was wrong, 0
    instead of 1. That is what the side binding above fixes; this guard is
    defence in depth for the configurations where the exemption DOES decide.
    """
    sure = cap(resid=D("0"), certain=True)
    assert sure["prospective_book_flat"] is True
    unsure = cap(resid=D("0"), certain=False)
    assert unsure["prospective_book_flat"] is False
    # at k=1 both still permit the first contract, and must
    assert sure["binds"] is False and unsure["binds"] is False


def test_where_the_exemption_decides_uncertainty_refuses():
    """k * clip < qty is the regime in which `exempt_open` is the only
    reason a first order passes. There, an uncertain book refuses it."""
    sure = cap(resid=D("0"), qty=D("1"), k=D("0.5"), certain=True)
    assert sure["binds"] is False, sure
    unsure = cap(resid=D("0"), qty=D("1"), k=D("0.5"), certain=False)
    assert unsure["binds"] is True, unsure


def test_certain_is_reported_so_the_record_says_which_rule_ran():
    assert cap(resid=D("0"), certain=False)["certain"] is False
    assert cap(resid=D("0"), certain=True)["certain"] is True


def test_the_exemption_is_unchanged_for_a_genuinely_flat_certain_book():
    """The correction must not refuse ordinary first openings."""
    out = cap(resid=D("0"), same_side=D("0"), other_side=D("0"),
              qty=D("1"), certain=True)
    assert out["binds"] is False


def test_a_second_opening_binds_once_the_phantom_is_not_booked():
    """With the real 1 UP still on the book, the cap refuses opening two."""
    out = cap(resid=D("1"), qty=D("1"), side="UP", certain=True)
    assert out["binds"] is True, out
    assert D(out["cap_contracts"]) == D("1")


def test_risk_reducing_orders_are_still_never_refused_under_uncertainty():
    """A completion shrinks |worst| and must keep flowing while uncertain."""
    out = cap(resid=D("1"), qty=D("1"), side="DOWN", certain=False)
    assert out["binds"] is False, out


# =========================================== 3. WHAT MUST NOT BE DISTURBED
def test_successive_partial_fills_under_one_coid_still_accumulate(tmp_path):
    """The venue filled each order in two pieces. That is the normal case."""
    st = mkstore(tmp_path)
    submitted(st, side="UP")
    st.apply_order_progress(COID, prog("0.87", px="0.26"), market=MKT,
                            side="UP")
    st.apply_order_progress(COID, prog("1.00", px="0.26"), market=MKT,
                            side="UP")
    assert st.position(MKT, "UP") == D("1.00")
    assert st.orders_uncertain() is None


def test_a_repeated_cumulative_read_books_nothing_more(tmp_path):
    """Delayed and duplicated venue responses are idempotent."""
    st = mkstore(tmp_path)
    submitted(st, side="UP")
    st.apply_order_progress(COID, prog("1"), market=MKT, side="UP")
    before = st.position(MKT, "UP")
    st.apply_order_progress(COID, prog("1"), market=MKT, side="UP")
    st.apply_order_progress(COID, prog("1"), market=MKT, side="UP")
    assert st.position(MKT, "UP") == before


def test_an_order_with_no_submitted_record_keeps_previous_behaviour(tmp_path):
    """ABSENT IS NOT A MISMATCH: pre-existing orders are not stranded."""
    st = mkstore(tmp_path)
    out = st.apply_order_progress(COID, prog("1"), market=MKT, side="UP")
    assert "refused" not in out
    assert st.position(MKT, "UP") == D("1")
    assert st.orders_uncertain() is None


def test_the_correct_side_books_normally(tmp_path):
    st = mkstore(tmp_path)
    submitted(st, side="UP")
    out = st.apply_order_progress(COID, prog("1", px="0.26"), market=MKT,
                                  side="UP")
    assert "refused" not in out
    assert st.position(MKT, "UP") == D("1")
    # own(UP, wire 0.26) == 0.26
    assert st.leg_cost(MKT)["UP"] == D("0.26")


# ================================================== 4. THE STRUCTURAL PIN
def test_every_residual_cap_call_site_states_its_certainty():
    """A new call site cannot silently reintroduce the unguarded exemption."""
    src = open("src/l5_live_execution.py").read()
    calls = src.count("residual_cap_decision(")
    # one definition + one reference in this module's own docstring-free
    # code paths; every CALL must pass certain=
    passes = src.count("certain=(self.store.orders_uncertain() is None)")
    assert calls >= 3, calls          # def + two money-path call sites
    assert passes == 2, (
        "both money-path residual_cap_decision call sites must state "
        "certainty; found %d" % passes)
