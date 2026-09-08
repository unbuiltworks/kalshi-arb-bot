"""The A/B design must be a design, not a deployment — and must not leak.

Two things it has to get right. A market's arm can never change, because a
residual can span a restart and a market that flipped arms mid-position would
belong to neither. And an unlabelled session must land in NEITHER arm, since
defaulting it to control would feed treated sessions straight into
L5_COMPLETION_EXTENSION_GATE_v1's evidence, which is the contamination the
whole design exists to prevent.
"""
from __future__ import annotations

import pytest

from src import l5_complete_requote_ab as ab
from src import l5_complete_requote_gate as bar
from src import l5_completion_extension_gate as frozen

_SRC = open(ab.__file__).read()


# ---------------------------------------------------------------- assignment
def test_a_markets_arm_is_stable_forever():
    for slug in ("aec-atp-a-b-2026-09-08", "tsc-mlb-x-y-2026-09-07", "z"):
        assert ab.arm_for(slug) == ab.arm_for(slug)


def test_arms_are_the_two_named_ones_and_nothing_else():
    arms = {ab.arm_for(f"m-{i}") for i in range(200)}
    assert arms == {ab.ARM_CONTROL, ab.ARM_TREAT}


def test_the_split_is_roughly_even_over_many_markets():
    b = ab.balance_check([f"market-{i}" for i in range(2000)])
    assert 0.45 <= b["treat_share"] <= 0.55


def test_assignment_is_by_market_not_by_session_or_clock():
    assert ab.ASSIGNMENT["unit"] == "market slug"
    assert "span sessions" in ab.ASSIGNMENT["why_not_random_per_session"]
    assert "time-of-day" in ab.ASSIGNMENT["why_not_alternate_by_time"]


def test_a_different_salt_gives_a_different_split_so_it_is_not_hardcoded():
    a = [ab.arm_for(f"m{i}") for i in range(50)]
    b = [ab.arm_for(f"m{i}", salt="other") for i in range(50)]
    assert a != b


# --------------------------------------------------------- non-contamination
def test_the_frozen_extension_gate_is_not_edited_by_this_design():
    assert frozen.assert_unmodified() == frozen.GATE_SHA16_AT_FREEZE
    assert "l5_completion_extension_gate" not in _SRC


def test_the_mechanism_names_the_arm_field_and_the_control_only_rule():
    n = ab.NON_CONTAMINATION
    assert ab.ARM_FIELD in n["mechanism"]
    assert "CONTROL-arm sessions only" in n["mechanism"]
    assert "untouched" in n["mechanism"]


def test_an_unlabelled_session_must_land_in_neither_arm():
    """Defaulting to control would leak treated sessions into the gate."""
    w = ab.NON_CONTAMINATION["what_would_break_it"]
    assert "NEITHER arm" in w
    assert "never defaulted to" in w and "control" in w


def test_the_cost_of_the_split_is_stated_not_hidden():
    c = ab.NON_CONTAMINATION["cost"]
    assert "half rate" in c
    assert "forward hours" in c


# ---------------------------------------------------------------- the bounds
def test_every_money_limit_is_listed_as_unchanged():
    b = " ".join(ab.BOUNDS_UNCHANGED)
    for surface in ("$2.50", "$7.50", "concurrency 1", "maker-only",
                    "post-only", "daily loss stop", "frozen model sha"):
        assert surface in b, surface


def test_the_new_bounds_are_about_containing_rejections():
    nb = ab.NEW_BOUNDS_REQUIRED
    assert nb["max_complete_requotes_per_session"] > 0
    assert nb["requotes_charge_the_action_budget"] is True
    assert nb["rejection_breaker_covers_requotes"] is True
    assert "revokes the complement proof" in nb["why"]


def test_no_bound_is_loosened_anywhere_in_the_design():
    blob = _SRC.lower()
    for banned in ("raise the cap", "increase the cap", "widen the cap",
                   "more bankroll", "larger size", "lift the limit"):
        assert banned not in blob, banned


# ------------------------------------------------------------- the endpoint
def test_the_primary_endpoint_is_money_not_completions():
    p = ab.PRIMARY_ENDPOINT
    assert "dollars per capital-dollar-hour" in p["name"]
    assert "ABOVE PAR" in p["why_not_completion_rate"]
    assert "score locked losses as wins" in p["why_not_completion_rate"]


def test_pair_cost_above_par_is_a_secondary_endpoint():
    assert any("above par" in s for s in ab.PRIMARY_ENDPOINT["secondary"])


def test_early_stops_are_declared_safety_not_efficacy():
    assert "safety stops, not efficacy reads" in ab.STOP_EARLY["why"]


# ------------------------------------------------------------ authorisation
def test_the_design_authorises_nothing():
    a = ab.AUTHORISATION
    assert a["this_module_authorises"] == "nothing"
    assert a["offline_verdict_required"] in bar.AUTHORISES_LIVE_AB_DESIGN
    assert a["offline_bar"] == bar.GATE
    assert "defaults OFF and no code here sets it" in a["arming_requires"]


def test_it_never_sets_the_live_flag():
    for banned in ("setenv", "os.environ[", "environ.setdefault",
                   'PMUS_SPRING_COMPLETE_REQUOTE"] ='):
        assert banned not in _SRC, banned


def test_it_reads_and_never_acts():
    for forbidden in ("requests", "httpx", "place_order", "subprocess",
                      "os.system"):
        assert forbidden not in _SRC, forbidden


def test_it_states_what_the_offline_evidence_predicts_so_it_can_be_wrong():
    assert "LOSE about 3.2 cents a unit" in ab.__doc__
    assert "not to confirm the fill count" in ab.__doc__
