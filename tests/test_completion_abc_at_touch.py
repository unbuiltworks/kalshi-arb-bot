"""Completion policies A, B and C on authenticated evidence.

TWO RESULTS, and the second one governs.

B AND C ADMIT NOTHING THAT A REJECTS. pair_behind = pair_at_touch - d*tick,
so with d=2 on a one-cent tick A admits pair_at_touch <= 0.98 while B admits
pair_at_touch < 1.00. The B-only band is (0.98, 1.00), which on a cent grid
is the single value 0.99 -- and across 399 authenticated residual samples in
four windows it occurred ZERO times. All three policies admit 12 moments.
The experiment is therefore a pure PLACEMENT change and carries no economic
loosening.

AND COMPLETION IS EV-NEUTRAL AGAINST HOLDING. Completing locks
margin = 1 - basis - mk. Holding the unmatched leg, marked at the same book,
is worth (1 - mk) - basis, because a matched pair redeems exactly $1.00 and
the complement trades at mk. Those are the same expression. The difference is
identically zero -- algebra, not a sample statistic -- and it is zero at all
12 moments to four decimals. After fees it is negative as a taker and neutral
as a maker only because the verified maker fee is zero.

So no completion policy, at any depth or ceiling, can recover an adversely
selected first leg: ALL of a matched pair's value comes from the price at
which the first leg was acquired. The five richest-looking moments, in 2215z
at margins of 10 to 43 cents a contract, are the proof: the complement got
cheap precisely because our held leg was winning, holding was worth exactly
the same, and all five are past the completion cutoff anyway.

WHAT THE EVIDENCE CANNOT DO. There were FOUR venue-confirmed fills in the
whole campaign and every one is a first leg. There is no order-level evidence
of a completion fill at any depth, so no policy's fill rate is supported --
not A's, not B's, not C's. That is why the recommendation is a
fill-probability MEASUREMENT rather than a promotion.

AND ONE WINDOW EARNS THE INSTRUCTION'S LOT-LEVEL WARNING. 2300z's book held
3.3600 at an average own cost of 0.605, which is exactly 2 x 1.68 and exactly
(0.21 + 1.00)/2: the real venue-confirmed lot of 1.68 at 0.21 blended with a
second lot at the 1.00 unattested-basis placeholder. On the aggregate average
every 2300z completion prices at 1.77-1.94 and the window vanishes. On the
venue-confirmed basis it contributes SIX of the 12 admissible moments.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

DOC = json.load(open("analysis/l5/completion_abc_at_touch_20261103.json"))
ADM = DOC["admissibility_results"]
TOT = ADM["totals"]
LOT = DOC["lot_level_acquisition_costs"]
EV = DOC["expected_settlement_versus_holding"]
EXP = DOC["recommended_bounded_live_experiment"]

TICK = D("0.01")
DEPTH = 2


# ------------------------------------------------------------ item 1

def test_parity_is_clean_on_the_named_commit_and_evidence_work_is_preserved():
    p = DOC["item1_parity"]
    assert p["head"] == "6db042fc"
    assert p["ids"] == 80 and p["digest"] == "eb672d14b4baf439"
    assert p["identical_to_baseline"] is True
    assert p["corrected_evidence_instrumentation_preserved"] is True
    assert len(p["preserved"]) >= 5


# -------------------------------------------- the admissibility identity

def test_the_b_only_band_is_a_single_cent_and_it_is_empty():
    """Why B adds nothing: its extra range holds one representable price."""
    # A admits pair_behind <= 0.96, i.e. pair_at_touch <= 0.98
    a_max_touch = D("0.96") + DEPTH * TICK
    assert a_max_touch == D("0.98")
    # B admits pair_at_touch < 1.00, so the extra band is (0.98, 1.00)
    extra = [D(c) / 100 for c in range(99, 100)]
    assert extra == [D("0.99")]
    assert TOT["B_only"] == 0


def test_all_three_policies_admit_the_same_number_of_moments():
    assert TOT["A"] == TOT["B"] == TOT["C"] == 12
    assert TOT["samples"] == 399


def test_per_window_counts_sum_to_the_totals():
    for key in ("A", "B", "C", "B_only"):
        assert sum(w[key] for w in ADM["per_window"]) == TOT[key], key
    assert sum(w["samples"] for w in ADM["per_window"]) == TOT["samples"]


def test_no_window_has_a_b_only_moment():
    for w in ADM["per_window"]:
        assert w["B_only"] == 0, w["window"]


def test_what_b_changes_is_where_it_rests_not_whether_it_completes():
    assert "not WHETHER to complete but WHERE to rest" in ADM["what_B_actually_changes"]
    assert "0.02" in ADM["what_B_actually_changes"]


def test_the_experiment_is_stated_to_carry_no_economic_loosening():
    assert "no economic loosening" in EXP["design"]["admission_rule"]


# --------------------------------------------- the EV identity, exactly

def test_the_ev_neutrality_claim_is_withdrawn_and_the_reason_recorded():
    """OWNER CORRECTION, accepted. The identity is arithmetic; the EV reading
    assumed (1 - mk) is the true settlement probability, and it is not --
    `mk` is one side of a spread, a maker fill is not guaranteed, and a taker
    pays a fee. The sign is UNMEASURED, and unknown is not neutral."""
    assert "THE EV CLAIM DOES NOT" in EV["result"]
    assert "UNMEASURED" in EV["result"]
    assert "OWNER CORRECTION" in EV["correction"]
    assert "offered price" in EV["correction"].lower() or \
        "OFFERED price" in EV["correction"]
    assert "'unknown' is not 'neutral'" in EV["correction"]


def test_what_survives_the_correction_is_listed_and_still_arithmetic():
    keep = EV["what_is_still_true"]
    assert any("arithmetic" in k for k in keep)
    assert any("CONFIRMED gain against the basis actually paid" in k
               for k in keep)
    assert any("pays a fee" in k for k in keep)


def test_the_two_overclaims_are_explicitly_withdrawn():
    w = EV["what_I_withdraw"]
    assert any("cannot improve expected P&L" in x for x in w)
    assert any("net realized P&L is a legitimate outcome" in x for x in w)


@pytest.mark.parametrize("basis,mk", [
    ("0.3900", "0.4000"), ("0.3900", "0.1800"), ("0.4500", "0.5300"),
    ("0.2100", "0.7400"), ("0.2100", "0.7700"), ("0.4700", "0.5900"),
])
def test_the_identity_holds_for_every_observed_basis_and_complement(basis, mk):
    """ARITHMETIC ONLY. This says the two expressions are equal; it does NOT
    say completing and holding have equal expected value, which would need
    (1 - mk) to be the true settlement probability."""
    b, m = D(basis), D(mk)
    complete_margin = D(1) - b - m          # locked by completing
    hold_marked_at_this_book = (D(1) - m) - b
    assert complete_margin == hold_marked_at_this_book


@pytest.mark.parametrize("basis,mk,true_p", [
    ("0.4500", "0.5300", "0.30"),   # held leg worth less than the book implies
    ("0.4500", "0.5300", "0.70"),   # worth more
])
def test_the_sign_moves_with_the_true_probability_not_with_the_identity(
        basis, mk, true_p):
    """Why the withdrawn claim was wrong, in one test. Holding is worth the
    TRUE probability, not the complement's offered price, so completing can
    be strictly better or strictly worse."""
    b, m, p = D(basis), D(mk), D(true_p)
    complete = D(1) - b - m                 # certain
    hold_true_ev = p - b                    # held leg pays 1 with prob p
    if p < D(1) - m:
        assert complete > hold_true_ev      # completing WINS
    else:
        assert complete < hold_true_ev      # completing LOSES


def test_the_first_leg_attribution_claim_is_kept_but_bounded():
    """The consequence text is retained for the record. It is TRUE of the
    locked margin against basis paid, and NOT a statement about expected
    value -- which the correction above withdraws."""
    assert "FIRST leg was acquired" in EV["consequence"]
    assert "OWNER CORRECTION" in EV["correction"]


def test_the_rich_looking_moments_are_explained_rather_than_claimed():
    s = EV["the_2215z_illustration"]
    assert "look like free money" in s
    assert "They are not" in s
    assert "holding it was worth exactly the same" in s


def test_the_2215z_moments_are_all_past_the_completion_cutoff():
    w = [x for x in ADM["per_window"] if x["window"] == "2215z"][0]
    assert w["all_past_cutoff"] is True
    assert "720" in w["timing"]
    assert "none is actionable" in w["timing"]


def test_actionable_counts_are_smaller_than_admissible_counts():
    assert TOT["actionable_within_cutoffs"] == 7
    assert TOT["actionable_within_cutoffs"] < TOT["B"]
    assert TOT["actionable_on_an_uncontaminated_basis"] == 1


# ------------------------------------------- lot level, and the 2300z lesson

def test_three_windows_agree_with_the_venue_and_one_does_not():
    agree = [w for w in LOT["windows"] if w["agrees"]]
    disagree = [w for w in LOT["windows"] if not w["agrees"]]
    assert len(agree) == 3 and len(disagree) == 1
    assert disagree[0]["window"] == "2300z"
    assert all(w["lots"] == 1 for w in agree)


def test_the_contaminated_average_is_arithmetically_identified():
    d = [w for w in LOT["windows"] if w["window"] == "2300z"][0]
    # 3.36 is exactly twice the venue quantity
    assert D("3.3600") == 2 * D("1.68")
    # and 0.605 is exactly the blend of the real lot with a 1.00 placeholder
    assert (D("0.2100") + D("1.00")) / 2 == D("0.605")
    assert "placeholder" in d["diagnosis"]


def test_the_contamination_would_have_hidden_the_whole_window():
    d = [w for w in LOT["windows"] if w["window"] == "2300z"][0]
    assert "1.77-1.94" in d["consequence"]
    assert "SIX admissible moments" in d["consequence"]
    # and on the aggregate basis even the cheapest complement clears nothing
    assert D("0.605") + D("0.7400") > D("1.00")
    # while on the venue basis it does
    assert D("0.2100") + D("0.7400") < D("1.00")


def test_reconciling_that_basis_is_a_stated_prerequisite():
    assert "MUST be reconciled" in LOT["prerequisite"]
    assert any("2300z-class" in p for p in EXP["prerequisites_before_any_live_run"])


# --------------------------------------- fees, rebates, queue, partials

def test_no_rebate_is_credited_because_none_is_verified():
    f = DOC["policy_definitions_as_tested"]["fee_treatment"]
    assert "ZERO ASSUMED" in f["maker_rebate"]
    assert "null on all four fills" in f["maker_rebate"]
    assert "0.0125 maker coefficient is NOT applied" in f["maker_rebate"]


def test_the_maker_fee_is_zero_by_venue_verification_not_assumption():
    f = DOC["policy_definitions_as_tested"]["fee_treatment"]
    assert "VENUE-VERIFIED" in f["maker_fee"]
    assert "venue_order_progress.commission" in f["maker_fee"]


def test_queue_position_is_a_bound_and_no_fill_is_claimed():
    q = DOC["execution_realism"]["queue_position"]
    assert "NO per-order queue position" in q
    assert "UPPER BOUND" in q
    assert "no fill is claimed" in q


def test_partial_fill_behaviour_is_reported_unmeasurable():
    assert "none observed" in DOC["execution_realism"]["partial_fills"]
    assert "no partial-fill behaviour is measurable" in DOC["execution_realism"]["partial_fills"]


def test_zero_complement_fills_means_no_policy_fill_rate_is_supported():
    e = DOC["evidence_base"]
    assert e["order_level_fills_in_the_entire_campaign"] == 4
    assert e["complement_fills_in_the_entire_campaign"] == 0
    assert "not A's, not B's, not C's" in e["why_that_matters"]


def test_cancel_and_decision_latency_come_from_venue_confirmed_timings():
    s = DOC["execution_realism"]["cancel_replace_latency"]
    assert "0.175s" in s and "1.571s" in s and "0.337s" in s


# ----------------------------------- the entry-filter question, answered

def test_we_do_not_stop_opening_first_legs_and_the_reason_is_not_denial():
    s = DOC["should_we_stop_opening_first_legs_in_adverse_states"]
    assert s["answer"].startswith("NO")
    assert "confirmed 4 of 4" in s["adverse_selection_is_real"]
    assert "No filter is promotable" in s["but_no_state_signal_survives"]
    assert "n=4" in s["and_our_own_fill_sample_is_four"]
    assert "not going to fit one" in s["and_our_own_fill_sample_is_four"]


def test_the_mechanism_is_attributed_to_placement_and_left_out_of_scope():
    s = DOC["should_we_stop_opening_first_legs_in_adverse_states"]
    assert "adverse by construction" in s["the_mechanism_is_placement_not_state"]
    assert "Out of scope" in s["the_mechanism_is_placement_not_state"]


# -------------------------------------------------- the experiment design

def test_the_experiment_is_a_measurement_not_a_profit_experiment():
    assert "FILL-PROBABILITY MEASUREMENT" in EXP["what_it_is"]
    assert "cannot distinguish A from B" in EXP["why_not_framed_on_P&L"]
    assert "measuring noise" in EXP["why_not_framed_on_P&L"]


def test_every_bound_the_instruction_asked_for_is_numeric():
    L = EXP["limits"]
    assert L["max_exposure_per_window_usd"] == 2.0
    assert L["max_cumulative_completion_notional_usd"] == 20.0
    assert L["cumulative_realized_loss_limit_usd"] == 5.0
    assert L["max_concurrent_markets"] == 1 and L["max_live_orders"] == 1
    assert EXP["design"]["windows"] == 20
    assert "fail closed" in L["stop_on_loss_limit"]


def test_the_protections_the_instruction_named_are_all_listed_unchanged():
    unchanged = " ".join(EXP["limits"]["unchanged"])
    for need in ("cancel margin 300s", "watchdog", "exact-order cancellation",
                 "reconciliation", "residual cap", "daily loss limit", "HALT"):
        assert need in unchanged, need


def test_the_099_cap_stays_disabled_and_c_is_replay_only():
    ne = " ".join(EXP["limits"]["not_enabled"])
    assert "$0.99 global cap stays DISABLED" in ne
    assert "NOT part of the live arm" in ne
    assert "no opening pair-cap change" in ne
    assert "no first-leg selection change" in ne


def test_success_and_failure_criteria_are_both_measurable():
    assert len(EXP["success_criteria"]) >= 5
    assert len(EXP["failure_criteria"]) >= 4
    assert any("20 percentage points" in c for c in EXP["success_criteria"])
    assert any("NOT RESOLVED" in c for c in EXP["failure_criteria"])


def test_the_power_limitation_is_stated_rather_than_glossed():
    s = EXP["design"]["statistical_honesty"]
    assert "cannot detect a small one" in s
    assert "not a promotion" in s


def test_the_experiment_does_not_overclaim_what_it_could_license():
    assert "would NOT license" in EXP["what_a_positive_result_would_license"]
    assert "cannot" in EXP["what_it_cannot_establish"]
    assert "EV identity" in EXP["what_it_cannot_establish"]


# ------------------------------------------------------------- safety

def test_nothing_live_moved_and_nothing_was_authorized():
    s = DOC["safety"]
    assert s["live_policy_changed"] is False
    assert s["live_trading_authorized"] is False
    assert s["pair_cap"].startswith("0.96")
    assert s["completion_099_cap"] == "disabled"
    assert s["cancel_margin_s"] == 300.0
    assert s["armed"] is False
    assert s["new_services"] == 0
    assert s["orders_placed"] == 0
    assert s["deployed"] is False
    assert "no src change" in s["changes_in_this_turn"]
