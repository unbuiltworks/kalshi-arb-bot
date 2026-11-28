"""The amplitude decomposition must not be able to invent its own answer.

Every test here exists because a specific way of getting this wrong would
produce a confident, wrong attribution:

  * causes that overlap would let one lost action support two hypotheses;
  * scoring the STRICT stage against public-book rows would make hypothesis D
    true by construction, since that source cannot reach TRADE_THROUGH_PRICE;
  * a relative-size counterfactual that silently fed an economic bound would
    turn a measurement into a sizing change we were told not to make.
"""
from __future__ import annotations

import pytest

from src.l5_amplitude_decomposition import (CAUSES, PLAUSIBLE_CLASSES,
                                            STRICT_CLASSES, UNEVALUATED,
                                            compare_accept, decompose)


def act(i, side, *, klass="MAKER_COPYABLE", size=1.0, lat=5.0,
        bid="0.40", ask="0.60", backlog=False, key=None):
    """One observer row, shaped as the tape writes it."""
    return {
        "action_key": key if key is not None else "k%d" % i,
        "l5_event_utc": "2026-09-23T17:%02d:00+00:00" % i,
        "latency_s": lat,
        "backlog": backlog,
        "l5": {"pmus_side": side, "l5_size": size, "l5_price": "0.50"},
        "pmus": {"bbo": {"bid": bid, "ask": ask}},
        "copyability": {"klass": klass},
        "safety_overlay": {"would_block": False},
        "interaction": {"klass": "QUEUE_AMBIGUOUS",
                        "classes_unreachable_here": ["TRADE_THROUGH_PRICE"]},
    }


def auth(keys, klass="TRADE_THROUGH_PRICE"):
    return {k: {"klass": klass} for k in keys}


def test_every_lost_action_is_attributed_to_exactly_one_cause():
    rows = [act(0, "UP", klass="NO_BOOK"),
            act(1, "DOWN", klass="REQUIRES_CROSSING"),
            act(2, "UP"), act(3, "DOWN")]
    d = decompose(rows, authenticated=auth(["k2", "k3"]))
    by = d["attribution"]["actions_by_cause"]
    lost = sum(by.values())
    reached = d["chain"]["acquired_under_accept"]
    assert lost + reached == d["chain"]["l5_actions"]
    assert set(by) <= set(CAUSES)


def test_the_first_failing_stage_wins_so_causes_cannot_double_count():
    # NO_BOOK precedes the STRICT stage: an action with no book must never
    # also be counted as NO_SUBSEQUENT_INTERACTION.
    rows = [act(0, "UP", klass="NO_BOOK")]
    d = decompose(rows, authenticated={})
    by = d["attribution"]["actions_by_cause"]
    assert by == {"NO_BOOK": 1}


def test_public_book_rows_cannot_score_the_strict_stage():
    # The failure this guards: attributing every permitted action to
    # QUEUE_AMBIGUOUS because the only source available cannot do better.
    rows = [act(0, "UP"), act(1, "DOWN")]
    d = decompose(rows)                      # no authenticated evidence
    assert d["strict_evaluable"] is False
    assert d["actions_without_authenticated_evidence"] == 2
    assert "QUEUE_AMBIGUOUS" not in d["attribution"]["actions_by_cause"]
    assert d["attribution"]["actions_unattributable"] == 2


def test_a_missing_authenticated_key_is_unevaluated_not_a_venue_finding():
    rows = [act(0, "UP"), act(1, "DOWN")]
    d = decompose(rows, authenticated=auth(["k0"]))
    assert d["chain"]["acquired_under_accept"] == 1
    assert d["strict_evaluable"] is False
    assert d["actions_without_authenticated_evidence"] == 1


def test_no_interaction_and_queue_ambiguous_are_different_findings():
    rows = [act(0, "UP"), act(1, "DOWN")]
    d = decompose(rows, authenticated={"k0": {"klass": "NO_INTERACTION"},
                                       "k1": {"klass": "QUEUE_AMBIGUOUS"}})
    by = d["attribution"]["actions_by_cause"]
    assert by["NO_SUBSEQUENT_INTERACTION"] == 1
    assert by["QUEUE_AMBIGUOUS"] == 1


def test_strict_accepts_only_a_print_through_our_price():
    assert STRICT_CLASSES == ("TRADE_THROUGH_PRICE",)
    rows = [act(0, "UP")]
    assert decompose(rows, authenticated=auth(["k0"], "TRADE_AT_PRICE")
                     )["chain"]["acquired_under_accept"] == 0
    assert decompose(rows, authenticated=auth(["k0"], "TRADE_THROUGH_PRICE")
                     )["chain"]["acquired_under_accept"] == 1


def test_q1_size_loss_is_measured_against_l5s_actual_size():
    rows = [act(0, "UP", size=10.0), act(1, "DOWN", size=1.0)]
    d = decompose(rows, authenticated=auth(["k0", "k1"]))
    # both reproduced, but q=1 took one unit where L5 took ten
    assert d["chain"]["acquired_under_accept"] == 2
    assert d["hypotheses"]["B_q1_translation_destroys_dynamics"][
        "size_units_lost_to_q1"] == pytest.approx(9.0)


def test_the_relative_size_counterfactual_can_restore_amplitude():
    # This is exactly the shape hypothesis B predicts. Under q=1, two small
    # buys and two huge sells are 2 up and 2 down: the imbalance walks
    # +1, +2, +1, 0 -- it touches zero and never crosses, so the overshoot is
    # zero. With L5's real sizes the SAME four actions walk +1, +2, -48, -98:
    # they cross and overshoot hard. If the live windows behave like this,
    # the q=1 clamp is destroying the dynamics rather than the venue.
    rows = [act(0, "UP", size=1.0), act(1, "UP", size=1.0),
            act(2, "DOWN", size=50.0), act(3, "DOWN", size=50.0)]
    d = decompose(rows, authenticated=auth(["k0", "k1", "k2", "k3"]))
    q1 = d["amplitude"]["MIRROR_RAW"]
    cf = d["hypotheses"]["B_q1_translation_destroys_dynamics"][
        "counterfactual_relative_size"]
    assert q1["inventory_crossed_zero"] is False
    assert q1["overshoot_past_zero"] == 0
    assert cf["inventory_crossed_zero"] is True
    assert cf["overshoot_past_zero"] == pytest.approx(98.0)


def test_the_counterfactual_is_labelled_a_measurement_not_a_proposal():
    d = decompose([act(0, "UP")], authenticated=auth(["k0"]))
    note = d["hypotheses"]["B_q1_translation_destroys_dynamics"][
        "counterfactual_relative_size"]["note"]
    assert "not a proposal" in note
    assert d["phase"] == "PHASE_1_MEASUREMENT_ONLY"


def test_latency_is_reported_split_by_outcome_so_c_is_testable():
    rows = [act(0, "UP", lat=1.0), act(1, "DOWN", lat=99.0,
                                       klass="REQUIRES_CROSSING")]
    d = decompose(rows, authenticated=auth(["k0"]))
    c = d["hypotheses"]["C_latency_misses_the_overshoot_actions"]
    assert c["latency_s_p50_reached"] == 1.0
    assert c["latency_s_p50_lost"] == 99.0


def test_plausible_is_a_separate_entry_point_and_says_it_assumes_queue():
    rows = [act(0, "UP"), act(1, "DOWN")]
    a = auth(["k0", "k1"], "QUEUE_AMBIGUOUS")
    strict = decompose(rows, STRICT_CLASSES, authenticated=a)
    plausible = decompose(rows, PLAUSIBLE_CLASSES, authenticated=a)
    assert strict["chain"]["acquired_under_accept"] == 0
    assert plausible["chain"]["acquired_under_accept"] == 2
    cmp_ = compare_accept(rows)
    assert set(cmp_) == {"STRICT", "PLAUSIBLE", "L5_NATIVE", "note"}
    assert "upper bound" in cmp_["note"]


def test_the_native_baseline_is_never_filtered_by_our_controls():
    # L5_NATIVE must count what he did, including actions the venue blocked
    # and actions our overlay would refuse.
    rows = [act(0, "UP", klass="NO_BOOK"),
            act(1, "DOWN", klass="REQUIRES_CROSSING")]
    rows[0]["safety_overlay"] = {"would_block": True}
    d = decompose(rows, authenticated={})
    assert d["amplitude"]["L5_NATIVE"]["max_abs_imbalance"] == 1
    assert d["chain"]["l5_actions"] == 2
