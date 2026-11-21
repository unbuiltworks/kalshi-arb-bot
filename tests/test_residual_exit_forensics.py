"""RESIDUAL_EXIT_FORENSICS_V1 -- the inventory walk and its labels.

The walk is the whole analysis: if a transition is mislabelled, every segment,
rate and predictor downstream is wrong in a way no aggregate would reveal. So
the transitions are tested directly on hand-built fill sequences where the
right answer is arithmetic rather than judgement.

Two properties matter most and are tested explicitly:

  * a fill that CROSSES zero is a SWITCH, never a completion. Pooling the two
    would have turned L5's dominant behaviour (84% of episodes) into evidence
    that he completes to flat, which he almost never does.
  * an episode closed by its very next fill still records that fill as its
    `next_action`. The first version closed the episode before reading the
    action and silently produced None for exactly the cases that end fastest.
"""
from __future__ import annotations

import pytest

from analysis.l5.residual_exit import forensics as F


def _w(fills, end=1000, horizon="15m", redeem_outcome="Up"):
    """A window shaped like `load_windows` output. fills: (t, px, outcome, sz)."""
    return {
        "fills": [(t, px, oc, sz, px * sz) for (t, px, oc, sz) in fills],
        "meta": ("btc", horizon, end),
        "redeem": ({"usd": 0.0, "outcome": redeem_outcome}
                   if redeem_outcome else None),
    }


def _eps(fills, **kw):
    # capture_end far past the window end so the capture-tail rule never fires
    return F.episodes_for_window("cid", _w(fills, **kw), end_far())


def end_far() -> int:
    return 10_000_000


# --------------------------------------------------------------------------
# transition classification

@pytest.mark.parametrize("before,after,expect", [
    (0.0, 5.0, "OPEN"),
    (0.0, -5.0, "OPEN"),
    (5.0, 9.0, "EXTEND"),
    (-5.0, -9.0, "EXTEND"),
    (5.0, 2.0, "COMPLETE_PARTIAL"),
    (-5.0, -2.0, "COMPLETE_PARTIAL"),
    (5.0, 0.0, "COMPLETE_FLAT"),
    (-5.0, 0.0, "COMPLETE_FLAT"),
    (5.0, -3.0, "SWITCH"),
    (-5.0, 3.0, "SWITCH"),
])
def test_every_transition_is_named_from_arithmetic_alone(before, after, expect):
    assert F._classify(before, after) == expect


def test_crossing_zero_is_a_switch_and_not_a_completion():
    """The single most load-bearing distinction in the module."""
    assert F._classify(4.0, -6.0) == "SWITCH"
    assert F._classify(4.0, 0.0) == "COMPLETE_FLAT"


def test_a_hair_off_zero_is_not_called_flat():
    assert F._classify(4.0, 1e-3) == "COMPLETE_PARTIAL"
    assert F._classify(4.0, F.QTY_TOL / 2) == "COMPLETE_FLAT"


# --------------------------------------------------------------------------
# episode cutting

def test_a_clean_open_and_flat_completion_is_one_episode():
    eps = _eps([(10, 0.5, "Up", 10.0), (20, 0.5, "Down", 10.0)])
    assert len(eps) == 1
    e = eps[0]
    assert e["exit_mode"] == "COMPLETED_FLAT"
    assert e["opened_by"] == "OPEN"
    assert e["residual_side"] == "Up"
    assert e["duration_s"] == 10
    assert e["fills_in_episode"] == 2


def test_the_closing_fill_is_recorded_as_next_action_not_dropped():
    """Regression: the close used to happen before the action was read, so the
    fastest episodes reported next_action=None."""
    eps = _eps([(10, 0.5, "Up", 10.0), (20, 0.5, "Down", 10.0)])
    assert eps[0]["next_action"] == "COMPLETE_FLAT"
    assert eps[0]["next_action_dt_s"] == 10


def test_an_overshoot_ends_one_episode_and_opens_the_next():
    eps = _eps([(10, 0.5, "Up", 10.0), (20, 0.5, "Down", 25.0),
                (30, 0.5, "Up", 15.0)])
    assert [e["exit_mode"] for e in eps] == ["FLIPPED", "COMPLETED_FLAT"]
    assert eps[0]["residual_side"] == "Up"
    assert eps[1]["residual_side"] == "Down"      # the far side of the switch
    assert eps[1]["opened_by"] == "SWITCH"


def test_the_switching_fill_is_booked_once_to_the_episode_it_closes():
    """Otherwise the same 25 shares appear in two episodes' pair economics."""
    eps = _eps([(10, 0.5, "Up", 10.0), (20, 0.5, "Down", 25.0),
                (30, 0.5, "Up", 15.0)])
    assert eps[0]["episode_dn_qty"] == pytest.approx(25.0)
    assert eps[1]["episode_dn_qty"] == pytest.approx(0.0)
    assert eps[1]["episode_up_qty"] == pytest.approx(15.0)


def test_residual_left_at_the_bell_is_carried_not_completed():
    eps = _eps([(10, 0.5, "Up", 10.0), (20, 0.5, "Down", 4.0)])
    assert len(eps) == 1
    assert eps[0]["exit_mode"] == "CARRIED_TO_SETTLEMENT"
    assert eps[0]["settlement_residual_signed"] == pytest.approx(6.0)
    assert eps[0]["exit_t"] == 1000


def test_an_episode_with_no_following_fill_says_so_rather_than_guessing():
    """`NONE_CARRIED` is not 'L5 waited' -- an unfilled quote leaves no trace."""
    eps = _eps([(10, 0.5, "Up", 10.0)])
    assert eps[0]["next_action"] == "NONE_CARRIED"
    assert eps[0]["t_to_opposite_fill_s"] is None


# --------------------------------------------------------------------------
# economics

def test_settlement_pnl_uses_the_window_average_cost_and_says_so():
    # buys 10 Up at .40 and 4 Down at .50; Up wins; residual 6 Up at avg .40
    eps = _eps([(10, 0.40, "Up", 10.0), (20, 0.50, "Down", 4.0)],
               redeem_outcome="Up")
    e = eps[0]
    assert e["episode_residual_pnl"] == pytest.approx(6.0 - 6.0 * 0.40)
    assert e["episode_residual_pnl_basis"] == "window_avg_cost_of_residual_side"


def test_a_losing_residual_pays_nothing_and_loses_its_cost():
    eps = _eps([(10, 0.40, "Up", 10.0), (20, 0.50, "Down", 4.0)],
               redeem_outcome="Down")
    assert eps[0]["episode_residual_pnl"] == pytest.approx(-6.0 * 0.40)


def test_a_completed_episode_has_no_settlement_residual_pnl_at_all():
    """Its economics are in the pair cost; a number there would double-count."""
    eps = _eps([(10, 0.5, "Up", 10.0), (20, 0.5, "Down", 10.0)])
    assert eps[0]["episode_residual_pnl"] is None
    assert "extinguished by trading" in eps[0]["episode_residual_pnl_basis"]


def test_pair_cost_is_only_defined_when_both_legs_exist():
    eps = _eps([(10, 0.40, "Up", 10.0)])
    assert eps[0]["episode_pair_cost"] is None
    assert eps[0]["episode_matched_pnl"] is None


def test_resolved_economically_requires_both_flat_and_a_sub_par_pair():
    cheap = _eps([(10, 0.40, "Up", 10.0), (20, 0.50, "Down", 10.0)])[0]
    dear = _eps([(10, 0.60, "Up", 10.0), (20, 0.55, "Down", 10.0)])[0]
    assert cheap["resolved_flat"] and cheap["resolved_economically"]
    assert dear["resolved_flat"] and not dear["resolved_economically"]


# --------------------------------------------------------------------------
# settlement source

def test_the_capture_tail_is_indeterminate_and_is_never_guessed():
    w = _w([(10, 0.5, "Up", 10.0)], redeem_outcome=None)
    won, src = F.settlement(w, capture_end=w["meta"][2] + 10)
    assert won is None and src == "indeterminate_capture_tail"
    e = F.episodes_for_window("cid", w, capture_end=w["meta"][2] + 10)[0]
    assert e["episode_residual_pnl"] is None
    assert e["episode_residual_pnl_basis"].startswith("UNOBSERVABLE:")


def test_a_long_settled_window_with_no_redeem_row_means_the_held_side_lost():
    w = _w([(10, 0.5, "Up", 10.0)], redeem_outcome=None)
    won, src = F.settlement(w, capture_end=w["meta"][2] + F.TRUNCATION_S + 1)
    assert won is False and src == "no_redeem_implies_held_side_lost"


# --------------------------------------------------------------------------
# the favored-side question, which is a SIDE question and not a run question

def test_next_fill_side_is_derived_from_the_action_not_reasserted():
    assert F.next_fill_side({"next_action": "EXTEND",
                             "residual_side": "Up"}) == "Up"
    assert F.next_fill_side({"next_action": "SWITCH",
                             "residual_side": "Up"}) == "Down"
    assert F.next_fill_side({"next_action": "COMPLETE_PARTIAL",
                             "residual_side": "Down"}) == "Up"
    assert F.next_fill_side({"next_action": "NONE_CARRIED",
                             "residual_side": "Up"}) is None


def test_a_tie_at_exactly_half_has_no_favored_side():
    eps = _eps([(10, 0.50, "Up", 10.0), (20, 0.50, "Down", 4.0)])
    assert eps[0]["favored"] == "NONE"
    assert eps[0]["residual_is_favored"] is False


def test_a_flip_with_no_favored_side_is_not_counted_as_flipping_away():
    """Folding ties into 'away' manufactures a tilt that is not in the tape."""
    eps = _eps([(10, 0.50, "Up", 10.0), (20, 0.50, "Down", 25.0),
                (30, 0.50, "Up", 15.0)])
    seg = F.segment(eps)
    assert "FLIPPED_NO_FAVORED_SIDE" in seg
    assert "FLIPPED_AWAY_FROM_FAVORED" not in seg
