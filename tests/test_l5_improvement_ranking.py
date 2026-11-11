"""The ranking must not launder a new strategy as a fidelity fix.

The owner's line: "Do not quietly turn L5 into generic market making or
arbitrage." The proposal ranked first -- reprice a resting COMPLETE to where
L5 moved -- is one tick in code away from "pay another cent to get filled",
which would be exactly that. These tests pin the classification, pin that
every entry carries a bounded test with a rollback, and pin that no entry
promises a forecast where the evidence only supports a bound.
"""
from __future__ import annotations

import pytest

from src import l5_capacity_curve as cc
from src import l5_improvement_ranking as ir

_SRC = open(ir.__file__).read()
_VALID = {ir.FIDELITY, ir.EXECUTION, ir.NEW_STRATEGY, ir.NOT_AN_OPPORTUNITY}


# ------------------------------------------------------------ the currency
def test_a_completion_point_is_the_slope_of_the_capacity_curve():
    """One unit, derived, not restated."""
    got = ir.usd_per_dollar_hour_per_point()
    direct = (cc.usd_per_dollar_hour(0.85) - cc.usd_per_dollar_hour(0.84))
    assert got == pytest.approx(direct, abs=1e-6)


def test_the_gap_to_breakeven_agrees_with_the_capacity_curve():
    assert ir.points_to_breakeven() == pytest.approx(
        (cc.breakeven_completion_rate() - cc.OBSERVED_COMPLETION_RATE) * 100,
        abs=1e-3)
    assert ir.points_to_breakeven() > 0        # we are below it


def test_the_ladder_marks_breakeven_correctly():
    rows = ir.ladder()
    need = ir.points_to_breakeven()
    for r in rows:
        assert r["clears_breakeven"] == (r["completion_points_gained"] >= need)
    # and it is monotone
    xs = [r["usd_per_day"] for r in rows]
    assert all(b > a for a, b in zip(xs, xs[1:]))


# ------------------------------------------------------- the classification
def test_every_opportunity_carries_a_valid_classification_and_a_reason():
    for o in ir.OPPORTUNITIES:
        assert o["classification"] in _VALID, o["id"]
        assert o["why_that_classification"].strip()
        assert o["evidence"] and all(e.strip() for e in o["evidence"])
        assert o["evidence_source"].strip()


def test_the_top_proposal_is_fidelity_and_says_what_it_is_not():
    top = ir.OPPORTUNITIES[0]
    assert top["classification"] == ir.FIDELITY
    why = top["why_that_classification"]
    assert "Paying up to get filled would be a new strategy" in why
    t = top["bounded_test"]
    assert "no price the model did not return" in t["explicitly_not"]
    assert "no crossing" in t["explicitly_not"]


def test_the_line_between_fidelity_and_new_strategy_is_stated_explicitly():
    line = ir.report()["the_line_that_must_not_be_crossed"]
    assert "Reprice to where L5 moved" in line
    assert "Pay another cent to get filled" in line


def test_selection_change_is_not_labelled_a_fidelity_fix():
    """L5's own selection is not observable, so it cannot be cloned."""
    sel = next(o for o in ir.OPPORTUNITIES
               if o["id"] == "select_for_time_to_work_and_a_balanced_book")
    assert sel["classification"] == ir.EXECUTION
    assert "not observable" in sel["why_that_classification"]


# --------------------------------------------------------- the bounded tests
def test_every_opportunity_has_a_bounded_test_with_a_rollback():
    for o in ir.OPPORTUNITIES:
        t = o["bounded_test"]
        for k in ("change", "explicitly_not", "bounds_unchanged",
                  "sample_before_reading", "what_would_make_it_fail",
                  "rollback"):
            assert k in t, (o["id"], k)
        assert t["bounds_unchanged"]


def test_no_bounded_test_loosens_a_money_limit():
    for o in ir.OPPORTUNITIES:
        t = o["bounded_test"]
        blob = " ".join(str(v) for v in t.values()).lower()
        for banned in ("raise the cap", "increase the cap", "widen the cap",
                       "lift the limit", "more bankroll", "larger size"):
            assert banned not in blob, (o["id"], banned)


def test_the_actionable_proposal_names_the_risk_it_carries():
    top = ir.OPPORTUNITIES[0]
    assert "#93" in top["bounded_test"]["new_bound_required"]
    assert "rejection" in top["bounded_test"]["new_bound_required"]
    # and the failure mode where a cheaper fill costs price
    assert "half-profit sensitivity" in top["bounded_test"][
        "what_would_make_it_fail"]


def test_the_top_proposal_defers_to_the_frozen_experiment():
    top = ir.OPPORTUNITIES[0]
    assert "frozen gate" in top["blocked_by"]
    assert "attributable" in top["blocked_by"]


# ---------------------------------------------------------- the non-options
def test_bankroll_and_concurrency_are_kept_in_the_list_and_ranked_last():
    ids = [o["id"] for o in ir.OPPORTUNITIES]
    assert ids[-2:] == ["raise_concurrency", "more_bankroll"]
    for i in ids[-2:]:
        o = next(x for x in ir.OPPORTUNITIES if x["id"] == i)
        assert o["classification"] == ir.NOT_AN_OPPORTUNITY


def test_it_does_not_request_funding():
    money = next(o for o in ir.OPPORTUNITIES if o["id"] == "more_bankroll")
    assert money["expected_improvement"].startswith("ZERO")
    assert "do not request funding" in money["bounded_test"]["explicitly_not"]


def test_concurrency_is_refused_for_the_right_reason():
    c = next(o for o in ir.OPPORTUNITIES if o["id"] == "raise_concurrency")
    assert "UNVERIFIABLE" in c["why_that_classification"]
    assert "slope through the current rung is negative" in c[
        "why_that_classification"]
    assert "do not scale a negative unit economic" in c["bounded_test"][
        "explicitly_not"]


# --------------------------------------------------------------- the honesty
def test_the_unquantifiable_entry_says_unknown_rather_than_small():
    n93 = next(o for o in ir.OPPORTUNITIES
               if o["id"] == "why_the_venue_rejects_non_crossing_completes")
    assert "NOT YET QUANTIFIABLE" in n93["expected_improvement"]
    assert "unknown rather than small" in n93["expected_improvement"]
    assert n93["bounded_test"]["change"].startswith("none")


def test_no_entry_presents_a_bound_as_a_forecast():
    for o in ir.OPPORTUNITIES:
        e = o["expected_improvement"]
        assert any(w in e for w in ("UPPER BOUND", "RANGE", "NOT YET "
                                    "QUANTIFIABLE", "ZERO", "NEGATIVE")), o["id"]
    assert "never 'this many would have converted'" in ir.report()[
        "every_number_is_a_bound"]


def test_it_deploys_nothing():
    r = ir.report()
    assert "Nothing here is deployed" in r["read_only"]
    assert "frozen COMPLETE experiment is untouched" in r["read_only"]
    for forbidden in ("requests", "httpx", "place_order", "post(",
                      "subprocess", "os.system"):
        assert forbidden not in _SRC, forbidden
    assert _SRC.count('"w") as f') == 2


def test_it_does_not_import_the_live_path_or_the_frozen_experiment():
    for frozen in ("pmus_spring_l5_session", "l5_completion_extension_gate",
                   "l5_completion_extension_eval"):
        assert f"import {frozen}" not in _SRC, frozen


def test_ranks_are_unique_and_ordered():
    ranks = [o["rank"] for o in ir.OPPORTUNITIES]
    assert ranks == sorted(ranks) == list(range(1, len(ranks) + 1))


def test_the_top_proposal_is_a_one_condition_change_not_a_new_loop():
    """It was mis-scoped once as 'build a working loop'; it is not."""
    top = ir.OPPORTUNITIES[0]
    change = top["bounded_test"]["change"]
    assert "already live" in change and "already proven" in change
    assert "Nothing new is built" in change
    assert any("0 of 868 COMPLETEs" in e for e in top["evidence"])


def test_the_top_proposal_refuses_to_present_the_extend_gap_as_an_effect_size():
    e = ir.OPPORTUNITIES[0]["expected_improvement"]
    assert "NOT AN EFFECT SIZE" in e
    assert "confounded by relation" in e


def test_the_top_proposal_names_the_queue_cost_the_upper_bound_ignores():
    t = ir.OPPORTUNITIES[0]["bounded_test"]["new_bound_required"]
    assert "queue position" in t
