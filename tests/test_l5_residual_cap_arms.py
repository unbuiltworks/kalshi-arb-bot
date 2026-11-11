"""Regressions for the residual-cap shadow arms.

Three of these exist because an earlier version of this counterfactual was
wrong in a way that still produced a plausible table: it checked the cap on
the position the extend WOULD create (production checks the position BEFORE
it), it started each arm's inventory at zero when Railway retention began
mid-campaign, and it classified an extra extend as 'productive' whenever
total matched inventory grew for any reason at all.
"""
from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

from src import l5_residual_cap_arms as rc

ROOT = Path(__file__).resolve().parents[1]


def _inv(up="0", dn="0", cost_up="0", cost_dn="0", unit="1.92"):
    return rc.ArmInventory(up=D(up), dn=D(dn), cost_up=D(cost_up),
                           cost_dn=D(cost_dn), unit=D(unit))


# ---------------------------------------------- the cap is production's cap
def test_the_cap_is_read_the_way_production_reads_it():
    """THE REGRESSION. The live shell tests
    `abs(inv.behavioral_residual()) >= cap` -- the residual BEFORE the
    extend, with `>=`. So it PERMITS an extend that overshoots and refuses
    only the next one. Testing the post-fill position instead is a tighter
    rule, and under it the CONTROL arm refused a production extend that
    really happened -- a self-consistency failure, not a finding."""
    inv = _inv(dn="11.32", cost_dn="4.177")          # 5.896 units
    assert inv.cap_allows_extend(D("1.72"), 6) is True     # overshoot allowed
    inv.add("dn", D("1.72"), D("0.37"))              # now 6.79 units
    assert inv.cap_allows_extend(D("1.72"), 6) is False    # next one refused
    assert inv.cap_allows_extend(D("1.72"), 8) is True


def test_the_cap_is_inert_without_a_behavioural_unit():
    inv = rc.ArmInventory(dn=D("9"), unit=None)
    assert inv.cap_allows_extend(D("1"), 6) is True


def test_an_unbounded_arm_never_refuses():
    inv = _inv(dn="500", cost_dn="200")
    assert inv.cap_allows_extend(D("1.72"), rc.UNBOUNDED) is True


# ------------------------------------------------- inventory is the arm's own
def test_peaks_track_the_arms_own_history():
    inv = _inv()
    inv.add("dn", D("1.92"), D("0.47"))
    inv.add("dn", D("1.92"), D("0.43"))
    assert inv.peak_residual_units == D("2")
    inv.add("up", D("3.84"), D("0.50"))              # flat again
    assert inv.residual == D("0")
    assert inv.peak_residual_units == D("2")         # a PEAK, not a level


def test_matched_is_min_of_the_two_sides():
    """The structural fact the whole P3 question turns on: a contract added
    to the side already held cannot become matched."""
    inv = _inv(up="6.08", dn="10.13", cost_up="3", cost_dn="1.1")
    before = inv.matched
    inv.add("dn", D("5"), D("0.10"))                 # add to the LARGER side
    assert inv.matched == before
    inv.add("up", D("20"), D("0.10"))                # now the other side wins
    assert inv.matched > before


def test_surplus_side_is_none_when_flat():
    assert _inv(up="3", dn="3").surplus_side is None
    assert _inv(up="4", dn="3").surplus_side == "up"
    assert _inv(up="3", dn="4").surplus_side == "dn"


# --------------------------------------------------------- the accounting
def test_the_pnl_decomposition_closes_against_payout_minus_spend():
    inv = _inv(up="6.08", dn="10.13", cost_up="2.5", cost_dn="1.6266")
    for winner in ("up", "dn"):
        out = rc.decompose_pnl(inv, winner)
        assert out["decomposition_closes"], (winner, out)


def test_the_lad_mia_terminal_position_reproduces_the_venue_number():
    """+6.0034 is a venue-confirmed realized figure. If this stops
    reproducing, the accounting has drifted away from the money."""
    inv = _inv(up="6.08", dn="10.13", cost_up="0", cost_dn="0")
    inv.cost_up = D("4.12660") - D("0")
    inv.cost_dn = D("0")
    out = rc.decompose_pnl(inv, "dn")
    assert D(out["total_pnl"]) == D("6.0034")


def test_an_unsettled_market_decomposes_to_none_not_zero():
    assert rc.decompose_pnl(_inv(up="1", dn="2", cost_dn="1"), None) is None


# --------------------------------------------------------------- quantiles
def test_quantiles_do_not_interpolate_a_value_no_market_produced():
    xs = [D("1"), D("2"), D("100")]
    out = rc.quantiles(xs)
    assert out["p50"] == "2" and out["max"] == "100" and out["n"] == 3
    assert all(D(out[k]) in xs for k in ("p50", "p75", "p90"))


def test_quantiles_of_nothing_are_none_not_zero():
    out = rc.quantiles([])
    assert out["p50"] is None and out["n"] == 0


# ------------------------------------------------------------- shadow only
def test_the_live_cap_constant_is_still_six():
    from src import pmus_l5_ramp_ladder as ladder
    assert rc.CONTROL_CAP == int(ladder.rung("R5FT")["residual_units"]) == 6


def test_nothing_armed_imports_this():
    for rel in ("src/pmus_spring_l5_session.py",
                "src/pmus_l5_continuous_session.py",
                "src/pmus_sports_l5_decision.py"):
        p = ROOT / rel
        if p.exists():
            assert "l5_residual_cap_arms" not in p.read_text(), rel
