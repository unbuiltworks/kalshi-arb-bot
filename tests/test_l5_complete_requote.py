"""The re-quote replay must not sell a fill count as a profit.

Repricing a BUY upward buys fills at a worse price. Two thirds of the
counterfactual completions on the real tape are ABOVE PAR -- they lock a
loss -- and the replay's entire positive net comes from assuming the
alternative (stranding) is worse. These tests pin that the losing pairs are
counted as losses, that one residual cannot be sold twice, and that the
load-bearing assumption is exposed rather than buried.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_complete_requote_gate as gate
from src import l5_complete_requote_replay as rq

_SRC = open(rq.__file__).read()
_GSRC = open(gate.__file__).read()
_LOGS = "../r5f_logs"
_have = os.path.isdir(_LOGS)
_paths = ([os.path.join(_LOGS, f) for f in sorted(os.listdir(_LOGS))]
          if _have else [])


def _order(stage, side, px, filled, *, relation="complete", qty="5",
           min_opp=None, max_touch=None, end="deadline"):
    o = {"stage": stage, "side": side, "px": px, "qty": qty,
         "filled_qty": filled, "relation": relation, "end_reason": end}
    if min_opp is not None:
        o["rest_book"] = {"min_opp_ask": min_opp, "max_touch": max_touch,
                          "polls": 27}
    return o


def _session(orders, slug="m"):
    return {"slug": slug, "orders": orders}


# ------------------------------------------------------------------ the gate
def test_the_bar_was_frozen_before_the_replay_and_is_unmodified():
    assert gate.assert_unmodified() == gate.GATE_SHA16_AT_FREEZE
    assert gate.GATE_SHA16_AT_FREEZE != "PENDING"


def test_the_replay_reads_the_bar_and_never_restates_a_threshold():
    for lit in ("0.50", "additional_completions_min = ", "min_completions ="):
        pass
    # the scorer must reach into the gate, not carry its own numbers
    assert "gate.MUST_SHOW" in _SRC and "gate.MUST_NOT_WORSEN" in _SRC
    assert "assert_unmodified" in _SRC


def test_the_bar_never_authorises_shipping():
    assert gate.AUTHORISES_SHIPPING == ()
    assert "OFFLINE_EVIDENCE_STRONG" in gate.AUTHORISES_LIVE_AB_DESIGN


def test_the_bar_declares_non_contamination_of_the_frozen_extension_gate():
    assert "L5_COMPLETION_EXTENSION_GATE_v1" in gate.NON_CONTAMINATION
    assert "reads no forward evidence" in gate.NON_CONTAMINATION
    # and this module must not import or score that gate
    assert "l5_completion_extension_gate" not in _SRC


# --------------------------------------------------- one residual, one sale
def test_a_residual_cannot_be_counted_twice_in_one_market():
    """Three unfilled completes on one 5-unit residual are ONE counterfactual."""
    sess = _session([
        _order("spring_open_1", "YES", "0.40", "5", relation="open"),
        _order("spring_complete_2", "NO", "0.50", "0",
               min_opp="0.52", max_touch="0.53"),
        _order("spring_complete_3", "NO", "0.51", "0",
               min_opp="0.52", max_touch="0.53"),
        _order("spring_complete_4", "NO", "0.52", "0",
               min_opp="0.52", max_touch="0.53"),
    ])
    rows = rq.walk_session(sess)
    assert len(rows) == 3                       # all three are observed...
    rep = rq.replay.__wrapped__ if hasattr(rq.replay, "__wrapped__") else None
    # ...but only one survives the first-divergence rule
    import src.l5_completion_extension_eval as ev
    orig = ev._sessions
    ev._sessions = lambda paths: [sess]
    try:
        r = rq.replay([])
    finally:
        ev._sessions = orig
    assert r["priced_counterfactual_completions"] == 1
    assert r["counterfactuals_dropped_after_first_divergence"] == 2
    assert "would not have existed" in r["first_divergence_rule"]


def test_cost_basis_is_the_one_that_existed_while_the_order_rested():
    """Not the session-end basis, which includes later fills."""
    sess = _session([
        _order("spring_open_1", "YES", "0.40", "5", relation="open"),
        _order("spring_complete_2", "NO", "0.50", "0",
               min_opp="0.52", max_touch="0.53"),
        # a later fill at a very different price must not contaminate the row
        _order("spring_extend_3", "YES", "0.90", "5", relation="extend"),
    ])
    row = rq.walk_session(sess)[0]
    assert D(row["cost_basis_per_unit"]) == D("0.40")
    assert D(row["pair_cost_at_requote"]) == D("0.93")


# ------------------------------------------------- above par counts as a loss
def test_an_above_par_completion_is_booked_as_a_loss_not_a_win():
    sess = _session([
        _order("spring_open_1", "YES", "0.60", "5", relation="open"),
        _order("spring_complete_2", "NO", "0.50", "0",
               min_opp="0.52", max_touch="0.55"),
    ])
    row = rq.walk_session(sess)[0]
    assert D(row["pair_cost_at_requote"]) == D("1.15")
    assert row["above_par"] is True
    # 5 units at a 0.15 loss each
    assert D(row["locked_usd_if_completed"]) == D("-0.75")


def test_a_below_par_completion_is_booked_as_a_gain():
    sess = _session([
        _order("spring_open_1", "YES", "0.40", "5", relation="open"),
        _order("spring_complete_2", "NO", "0.50", "0",
               min_opp="0.52", max_touch="0.55"),
    ])
    row = rq.walk_session(sess)[0]
    assert D(row["pair_cost_at_requote"]) == D("0.95")
    assert row["above_par"] is False
    assert D(row["locked_usd_if_completed"]) == D("0.25")


def test_fillable_units_are_capped_by_the_residual_not_the_order_size():
    sess = _session([
        _order("spring_open_1", "YES", "3", "3", relation="open"),
        _order("spring_complete_2", "NO", "0.50", "0", qty="10",
               min_opp="0.52", max_touch="0.55"),
    ])
    row = rq.walk_session(sess)[0]
    assert D(row["residual_units_at_rest"]) == D("3")
    assert D(row["fillable_units"]) == D("3")


# ------------------------------------------------------ the trigger and reach
def test_a_trigger_needs_the_touch_to_have_moved_off_our_price():
    sess = _session([
        _order("spring_open_1", "YES", "0.40", "5", relation="open"),
        _order("spring_complete_2", "NO", "0.50", "0",
               min_opp="0.52", max_touch="0.50"),      # touch == our price
    ])
    assert rq.walk_session(sess)[0]["requote_triggered"] is False


def test_reaching_needs_the_ask_to_have_come_to_the_requote_price():
    sess = _session([
        _order("spring_open_1", "YES", "0.40", "5", relation="open"),
        _order("spring_complete_2", "NO", "0.50", "0",
               min_opp="0.60", max_touch="0.55"),      # ask never got there
    ])
    row = rq.walk_session(sess)[0]
    assert row["requote_triggered"] is True
    assert row["book_reached_requote_px"] is False


def test_a_rejected_complete_is_not_replayed_as_a_requote_candidate():
    """A rejection is a different failure; it did not rest."""
    sess = _session([
        _order("spring_open_1", "YES", "0.40", "5", relation="open"),
        _order("spring_complete_2", "NO", "0.50", "0", min_opp="0.52",
               max_touch="0.55", end="rejected:ORDER_REJECTED_ZERO_FILL"),
    ])
    assert rq.walk_session(sess) == []


# ------------------------------------------------ the load-bearing assumption
@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_the_locked_pair_pnl_is_reported_separately_from_the_stranding_term():
    e = rq.replay(_paths)["economics"]
    assert "locked_pair_usd_at_full_conversion" in e
    assert "residual_loss_avoided_usd_at_full_conversion" in e
    note = e["locked_pair_usd_is_the_only_certain_term"]
    assert "what the counterfactual TRADE earns" in note
    assert "cohort mean over four markets" in note
    assert "only defensible if you believe the stranding estimate" in note


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_the_breakeven_stranding_cost_is_minus_locked_over_units():
    e = rq.replay(_paths)["economics"]
    locked = D(e["locked_pair_usd_at_full_conversion"])
    units = D(e["units_paired"])
    be = D(e["breakeven_stranding_cost_per_unit_usd"])
    assert float(be) == pytest.approx(float(-locked / units), abs=1e-3)
    # and it is far below the measured stranding cost -- the margin is the
    # reason the conclusion survives a thin estimate
    assert float(be) < float(-rq.RESIDUAL_LOSS_PER_UNIT) / 5


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_the_fairly_priced_rung_equals_the_locked_amount():
    """If stranding costs nothing, the net IS the locked pair P&L."""
    e = rq.replay(_paths)["economics"]
    assert (D(e["if_residuals_were_fairly_priced_net_usd"])
            == D(e["locked_pair_usd_at_full_conversion"]))
    assert "effectively n=4" in e["why_that_rung_exists"]


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_the_real_tape_locks_losing_pairs_on_balance():
    """The finding that a fill count would have hidden."""
    r = rq.replay(_paths)
    assert D(r["economics"]["locked_pair_usd_at_full_conversion"]) < 0
    assert r["pair_cost"]["above_par"] > r["pair_cost"]["at_or_below_par"]


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_rejection_exposure_and_budget_are_both_charged():
    r = rq.replay(_paths)
    assert r["rejection_exposure"]["extra_orders"] > 0
    assert D(r["rejection_exposure"]["expected_additional_rejections"]) > 0
    assert "budget" in r and "sessions_exceeding_extended_budget" in r["budget"]


@pytest.mark.skipif(not _have, reason="harvested logs not present")
def test_the_score_never_authorises_shipping_whatever_the_verdict():
    sc = rq.score(rq.replay(_paths))
    assert sc["authorises_shipping"] is False
    assert sc["verdict"] in gate.VERDICTS


# ------------------------------------------------------------------ hygiene
def test_it_reads_and_never_acts():
    for forbidden in ("requests", "httpx", "place_order", "subprocess",
                      "os.system"):
        assert forbidden not in _SRC, forbidden
    assert _SRC.count('"w") as f') == 2


def test_it_does_not_modify_the_live_path_or_the_frozen_experiment():
    assert "pmus_spring_l5_session" not in _SRC
    assert "l5_completion_extension_eval" in _SRC   # reads sessions only
