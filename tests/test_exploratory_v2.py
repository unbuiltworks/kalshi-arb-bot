"""EXPLORATORY_V2 -- the gates gate what they claim, and the books reconcile.

Two classes of defect would make this study wrong in a way no summary table
would expose:

  * a gate that quietly suppresses fills outside its stated scope. Every gate
    except V2E is defined ONLY at the moment we would EXTEND; if one of them
    also dropped completions, its "residual saved" would be an artifact.
  * an attribution that does not reconcile. The per-fill split feeds the run
    length, volatility and over-par conclusions; if the pieces do not sum to
    the window totals then none of those per-share numbers mean anything.

Both are tested directly rather than through an aggregate.
"""
from __future__ import annotations

import pytest

from analysis.l5.exploratory_v2 import interventions as I
from analysis.l5.exploratory_v2 import run_vol_pair as R
from analysis.l5.residual_exit import forensics as F


def _w(fills, end=1000, redeem="Up"):
    return {"fills": [(t, px, oc, sz, px * sz) for (t, px, oc, sz) in fills],
            "meta": ("btc", "15m", end),
            "redeem": {"usd": 0.0, "outcome": redeem} if redeem else None}


FAR = 10_000_000


# --------------------------------------------------------------------------
# what a fill would do to OUR inventory

@pytest.mark.parametrize("res,side,sz,expect", [
    (0.0, "Up", 5.0, "OPEN"),
    (5.0, "Up", 5.0, "EXTEND"),
    (-5.0, "Down", 5.0, "EXTEND"),
    (5.0, "Down", 2.0, "COMPLETE_PARTIAL"),
    (5.0, "Down", 5.0, "COMPLETE_FLAT"),
    (5.0, "Down", 9.0, "SWITCH"),
])
def test_relation_is_computed_from_our_own_inventory(res, side, sz, expect):
    assert I._relation(res, side, sz) == expect


# --------------------------------------------------------------------------
# scope: everything but V2E may only touch an EXTEND

EXTEND_ONLY = [
    ("V2A", I.v2a_residual_cap(1.0)),
    ("V2B", I.v2b_linear_suppression(0.001)),   # k tiny => always suppress
    ("V2C", I.v2c_run_cutoff(1)),
    ("V2D", I.v2d_vol_cutoff(0.0)),
    ("V2F", I.v2f_hybrid(0.001, 1, 0.001, 1.0)),
]


@pytest.mark.parametrize("name,gate", EXTEND_ONLY)
@pytest.mark.parametrize("res,side,sz", [
    (0.0, "Up", 5.0),          # OPEN
    (5.0, "Down", 2.0),        # COMPLETE_PARTIAL
    (5.0, "Down", 5.0),        # COMPLETE_FLAT
    (5.0, "Down", 9.0),        # SWITCH
])
def test_a_maximally_aggressive_gate_still_never_blocks_a_non_extend(
        name, gate, res, side, sz):
    """Each gate above is parameterised to suppress every EXTEND it sees. If it
    still passes an OPEN, a COMPLETE or a SWITCH, its scope is right."""
    st = I.State("c", 0, res, 3, "Up", 0.5, 0.5, 100, 0, 0, 0)
    assert gate(st, (0, 0.5, side, sz, 0.5 * sz)) is True


@pytest.mark.parametrize("name,gate", EXTEND_ONLY)
def test_and_each_one_does_block_the_extend_it_is_aimed_at(name, gate):
    st = I.State("c", 0, 5.0, 3, "Up", 0.5, 0.5, 100, 0, 0, 0)
    assert gate(st, (0, 0.5, "Up", 5.0, 2.5)) is False


def test_v2e_is_the_one_gate_that_may_block_a_switch_and_that_is_its_point():
    """It targets the overshoot through zero, which is by definition not an
    extension, so a scope test that forbade it would forbid the mechanism."""
    g = I.v2e_matched_preservation(near=5.0, substantial=10.0)
    near_flat = I.State("c", 0, 2.0, 1, "Up", None, 0.5, 100, 0, 0, 0)
    assert g(near_flat, (0, 0.5, "Down", 50.0, 25.0)) is False   # would go -48
    assert g(near_flat, (0, 0.5, "Down", 8.0, 4.0)) is True      # -6, tolerable
    far = I.State("c", 0, 40.0, 1, "Up", None, 0.5, 100, 0, 0, 0)
    assert g(far, (0, 0.5, "Down", 90.0, 45.0)) is True          # not near flat


# --------------------------------------------------------------------------
# the cap is on the PROPOSED position

def test_the_residual_cap_tests_the_proposed_position_not_the_current_one():
    """Testing the current one lets a single large fill step over the cap."""
    g = I.v2a_residual_cap(10.0)
    st = I.State("c", 0, 9.0, 1, "Up", None, 0.5, 100, 0, 0, 0)
    assert g(st, (0, 0.5, "Up", 100.0, 50.0)) is False     # proposed 109 > 10
    assert g(st, (0, 0.5, "Up", 1.0, 0.5)) is True         # proposed 10 <= 10


# --------------------------------------------------------------------------
# magnitude, never sign

def test_the_volatility_gate_uses_magnitude_only():
    """RESIDUAL_EXIT_FORENSICS_V1 falsified sign(dp) OOS. An up-move and an
    equal down-move must gate identically -- State carries |dp| so there is no
    sign available to use by accident."""
    g = I.v2d_vol_cutoff(0.05)
    f = (0, 0.5, "Up", 5.0, 2.5)
    up = I.State("c", 0, 5.0, 1, "Up", 0.08, 0.58, 100, 0, 0, 0)
    dn = I.State("c", 0, 5.0, 1, "Up", 0.08, 0.42, 100, 0, 0, 0)
    assert g(up, f) == g(dn, f) is False
    assert "dpa_60s" in I.State.__slots__ and "dp_60s" not in I.State.__slots__


def test_an_unknown_volatility_does_not_silently_suppress():
    g = I.v2d_vol_cutoff(0.05)
    st = I.State("c", 0, 5.0, 1, "Up", None, 0.5, 100, 0, 0, 0)
    assert g(st, (0, 0.5, "Up", 5.0, 2.5)) is True


# --------------------------------------------------------------------------
# reproducibility

def test_probabilistic_suppression_is_deterministic_and_order_independent():
    g = I.v2b_linear_suppression(50.0)
    st = I.State("cid-x", 7, 25.0, 1, "Up", None, 0.5, 100, 0, 0, 0)
    f = (0, 0.5, "Up", 5.0, 2.5)
    first = g(st, f)
    for _ in range(50):
        assert g(I.State("cid-x", 7, 25.0, 1, "Up", None, 0.5, 100, 0, 0, 0),
                 f) is first
    assert 0.0 <= I._u01("a", 1) < 1.0


def test_the_brake_comes_on_progressively_not_at_a_cliff():
    """Over many fills, a larger residual must suppress a larger share."""
    g = I.v2b_linear_suppression(100.0)
    f = (0, 0.5, "Up", 1.0, 0.5)
    def kept(res):
        return sum(1 for i in range(2000)
                   if g(I.State("c", i, res, 1, "Up", None, .5, 9, 0, 0, 0), f))
    assert kept(10.0) > kept(50.0) > kept(90.0)


# --------------------------------------------------------------------------
# the control must reproduce the authoritative window accounting

def test_control_keeps_every_fill_and_reproduces_the_window_numbers():
    w = _w([(10, 0.40, "Up", 10.0), (20, 0.50, "Down", 4.0)], redeem="Up")
    r = I.replay_window("cid", w, FAR, I.control())
    assert r["kept"] == 2 and r["dropped"] == 0
    assert r["matched"] == pytest.approx(4.0)
    assert r["pair_cost"] == pytest.approx(0.90)
    assert r["matched_pnl"] == pytest.approx(4.0 * 0.10)
    assert r["residual"] == pytest.approx(6.0)
    assert r["residual_pnl"] == pytest.approx(6.0 - 6.0 * 0.40)


def test_matched_plus_residual_pnl_equals_payout_minus_spend():
    """The identity that makes trading_pnl_after_fees meaningful: usdcSize is
    fee-inclusive, so this sum IS the net cash."""
    w = _w([(10, 0.40, "Up", 10.0), (20, 0.55, "Down", 4.0)], redeem="Up")
    r = I.replay_window("cid", w, FAR, I.control())
    payout = 10.0                       # Up won, 10 Up shares
    spend = 0.40 * 10.0 + 0.55 * 4.0
    assert r["matched_pnl"] + r["residual_pnl"] == pytest.approx(payout - spend)


def test_a_gate_that_removes_everything_reports_zero_rather_than_crashing():
    w = _w([(10, 0.40, "Up", 10.0), (20, 0.50, "Up", 4.0)], redeem="Up")
    r = I.replay_window("cid", w, FAR, lambda st, f: False)
    assert r["kept"] == 0 and r["matched"] == 0.0 and r["residual_pnl"] == 0.0


def test_dropping_a_fill_actually_changes_the_walk():
    w = _w([(10, 0.40, "Up", 10.0), (15, 0.40, "Up", 10.0),
            (20, 0.50, "Down", 4.0)], redeem="Up")
    full = I.replay_window("cid", w, FAR, I.control())
    capped = I.replay_window("cid", w, FAR, I.v2a_residual_cap(10.0))
    assert capped["dropped"] == 1
    assert capped["residual"] < full["residual"]
    assert capped["max_res"] < full["max_res"]


# --------------------------------------------------------------------------
# the frontier's honesty check

def test_the_frontier_reports_lost_matched_and_saved_residual_separately():
    base = {"matched_pnl": 100.0, "residual_pnl": -100.0, "matched_volume": 10.0,
            "abs_residual_at_settlement_sum": 50.0, "spend_usd": 1000.0,
            "fills_retained": 1.0}
    r = {"matched_pnl": 60.0, "residual_pnl": -50.0, "matched_volume": 7.0,
         "abs_residual_at_settlement_sum": 20.0, "spend_usd": 700.0,
         "fills_retained": 0.7}
    f = I.frontier(base, r)
    assert f["matched_pnl_lost_usd"] == pytest.approx(40.0)
    assert f["residual_pnl_improved_usd"] == pytest.approx(50.0)
    assert f["matched_lost_per_residual_dollar_saved"] == pytest.approx(0.8)
    assert f["net_gain_usd"] == pytest.approx(10.0)
    # matched VOLUME retained and matched P&L retained are different numbers and
    # both are reported: volume alone hides a worsened pair cost
    assert f["matched_volume_retained"] == pytest.approx(0.7)
    assert f["matched_pnl_retained"] == pytest.approx(0.6)


# --------------------------------------------------------------------------
# the attribution underneath the run/volatility/over-par conclusions

def _rows_for(fills, redeem="Up", capture_end=FAR):
    import analysis.l5.residual_exit.forensics as FF
    w = _w(fills, redeem=redeem)
    orig = FF.load_windows
    FF.load_windows = lambda cohort="BTC15m": ({"cid": w}, capture_end)
    try:
        return R.per_fill("BTC15m")
    finally:
        FF.load_windows = orig


def test_the_per_fill_split_reconciles_to_the_window_totals_exactly():
    rows = _rows_for([(10, 0.40, "Up", 10.0), (15, 0.45, "Up", 6.0),
                      (20, 0.50, "Down", 9.0)], redeem="Up")
    up_q, dn_q = 16.0, 9.0
    up_u, dn_u = 0.40 * 10 + 0.45 * 6, 0.50 * 9
    pair = up_u / up_q + dn_u / dn_q
    matched, residual = 9.0, 7.0
    assert sum(r["matched_shares"] for r in rows) == pytest.approx(2 * matched)
    assert sum(r["residual_shares"] for r in rows) == pytest.approx(residual)
    assert sum(r["matched_pnl"] for r in rows) == pytest.approx(
        matched * (1.0 - pair))
    assert sum(r["residual_pnl"] for r in rows) == pytest.approx(
        residual - residual * (up_u / up_q))


def test_the_lighter_side_attributes_entirely_to_matched():
    rows = _rows_for([(10, 0.40, "Up", 10.0), (20, 0.50, "Down", 4.0)])
    dn = [r for r in rows if r["side"] == "Down"][0]
    assert dn["residual_shares"] == pytest.approx(0.0)
    assert dn["matched_shares"] == pytest.approx(4.0)


def test_run_position_counts_consecutive_same_side_fills():
    rows = _rows_for([(10, .4, "Up", 1.0), (11, .4, "Up", 1.0),
                      (12, .4, "Up", 1.0), (13, .5, "Down", 4.0)])
    assert [r["run_len"] for r in rows] == [1, 2, 3, 1]


def test_the_instantaneous_pair_needs_a_print_on_the_other_side_first():
    rows = _rows_for([(10, .4, "Up", 1.0), (11, .5, "Down", 4.0)])
    assert rows[0]["inst_pair"] is None            # nothing to pair against yet
    assert rows[1]["inst_pair"] == pytest.approx(0.9)


def test_indeterminate_or_single_sided_windows_are_excluded_not_guessed():
    two = [(10, .4, "Up", 10.0), (20, .5, "Down", 4.0)]
    assert _rows_for([(10, .4, "Up", 10.0)]) == []          # single-sided
    # inside the capture tail the winner is unknown, so there is nothing to
    # attribute and the fills are dropped rather than scored against a guess
    assert _rows_for(two, redeem=None, capture_end=1010) == []
    # past the tail the missing redeem row IS the answer -- the held side lost
    assert len(_rows_for(two, redeem=None)) == 2
