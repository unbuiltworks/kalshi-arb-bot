"""The 2026-11-02 2230z "missed completion", reconstructed to the millisecond.

CONCLUSION C. There was no missed profitable pair. Every opposite-side price
the executor observed between t+130 and t+148 put the pair at 1.05-1.10 own
against $1.00 redemption, and the sequence moved monotonically against us.

WHAT PROMPTED THE INVESTIGATION WAS MY OWN DEFECT, and these tests pin that
too, because it is the more useful finding. I reported a 0.9351 pair at t+130
from `scratchpad/tape/*.jsonl`. The arithmetic was right (1 - 0.53 = 0.47;
0.47 + 0.47 = 0.94); the observation was not. That tape is a ~30-second
sampler, dark for 92.6% of the window, and its reading 273ms before our fill
was contradicted 229ms later by the executor's authenticated websocket book
-- 0.5200/0.5300 against 0.4000/0.4100, twelve cents apart in the same
second.

Three independent refutations, none of which needs the other:

  1. the authenticated book, stream age 0.017s, read 0.4000/0.4100 at
     22:32:12.184 while the tape read 0.5200/0.5300 at 22:32:12.419;
  2. the tape's 22:32:12.419 row repeats 22:32:10.345 byte for byte with
     `shares_traded_delta_lower_bound: 0.0` -- claiming nothing traded --
     while 3,599.2 shares traded before its next observation 16.6s later;
  3. our resting order was a maker UP bid at 0.4700. A 0.47 bid cannot fill
     while the bid is 0.52, so the fill itself places the touch at or below
     0.47 and the tape's 0.52 is impossible.

The gate that refused was `TAKER_POLICY:WORST_OUTCOME_NOT_POSITIVE`, and it
was right: completing at 0.6000 own would have improved the worst case from
-$0.9988 to -$0.2228, which bounds a tail without creating a pair. That is a
hedge, not a completion.

And the decision was not late in any way that mattered. The engine priced the
complement first at +1.571s against an earliest attachable frame at +1.234s
-- 337ms of recoverable latency, against a pair that frame already put at
1.05 at the touch and 1.03 behind it. Both exceed redemption, so
fill-event-triggered completion reaches the same refusal.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

DOC = json.load(open("analysis/l5/missed_completion_2230z_forensics_20261103.json"))
I3 = DOC["item3_every_completion_evaluation"]
I4 = DOC["item4_the_exact_gate"]
I5 = DOC["item5_observed_book_with_timestamps"]
I7 = DOC["item7_maker_vs_taker_realism"]
CF = DOC["fill_triggered_completion_counterfactual"]
TAPE = DOC["the_premise_was_my_own_tape_defect"]


# ------------------------------------------------- the conclusion itself

def test_the_conclusion_is_c_and_claims_no_missed_pair():
    assert DOC["deliverable"]["conclusion"] == "C"
    assert "do not claim a missed profitable pair" in DOC["deliverable"]["statement"]
    assert "CONCLUSION C" in DOC["headline"]


def test_neither_a_nor_b_is_claimed_and_both_refusals_are_reasoned():
    why = DOC["deliverable"]["not_a_or_b"]
    assert "WORST_OUTCOME_NOT_POSITIVE" in why
    assert "1.05/1.03" in why


# ------------------------------------- items 1 and 2: the fill was prompt

def test_the_executor_learned_of_the_fill_within_200ms():
    i1 = DOC["item1_fill_timestamps"]
    assert i1["venue_fill_record_utc"] == "2026-11-02T22:32:10.618852Z"
    assert i1["executor_learned"]["lag_s"] < 0.2


def test_inventory_was_released_in_under_two_seconds_so_booking_is_ruled_out():
    i2 = DOC["item2_inventory_available"]
    assert i2["lag_from_fill_s"] < 2.0
    assert "RULED OUT" in i2["verdict"]


# --------------------------- item 3: the engine was not starved of chances

def test_the_completion_was_priced_five_times_in_seventeen_seconds():
    assert I3["count_priced"] == 5
    assert len(I3["priced_evaluations"]) == 5
    ts = [e["t_plus_s"] for e in I3["priced_evaluations"]]
    assert ts == sorted(ts)
    assert ts[0] < 132.5 and ts[-1] > 148.0


def test_the_first_priced_evaluation_is_within_two_seconds_of_the_fill():
    first = I3["priced_evaluations"][0]["t_plus_s"]
    assert first - 130.6 < 2.0


def test_every_priced_pair_exceeds_one_dollar_redemption():
    for e in I3["priced_evaluations"]:
        pair = D(e.get("pair_own") or e["pair_if_maker"])
        assert pair > D("1.00"), e


def test_the_cheapest_pair_anywhere_is_above_redemption():
    assert D(I3["cheapest_pair_anywhere_in_window"]) == D("1.0300")
    assert D(I3["cheapest_pair_anywhere_in_window"]) > D("1.00")
    assert "not one it could have taken" in I3["cheapest_is"]


# ------------------------------------------- item 4: the gate, not a guess

def test_the_gate_is_named_from_an_individual_decision_record():
    assert I4["gate"] == "TAKER_POLICY:WORST_OUTCOME_NOT_POSITIVE"
    assert I4["is_the_gate_incorrect"] is False
    for e in I3["priced_evaluations"]:
        if e["record"] == "L5_TS_COMPLETION":
            assert e["withheld_by"] == I4["gate"]


def test_the_refusal_is_a_hedge_refusal_not_a_completion_refusal():
    ev = I3["priced_evaluations"][1]
    assert D(ev["worst_before"]) < D(ev["worst_after"]) < 0
    assert ev["both_positive_after"] is False
    assert "HEDGE" in I4["arithmetic"]


def test_the_cooldown_counters_are_explicitly_not_treated_as_the_blocker():
    dc = I4["decide_cooldown_rows"]
    assert dc["count_in_window"] == 10
    assert dc["did_it_prevent_the_completion"] is False
    assert "forbids" in dc["why"]
    # and the count is reconciled against the whole slice, not quoted loose
    assert "14 OPENING" in dc["counted_from"]


def test_the_runtime_instrumentation_already_agreed_with_conclusion_c():
    """Corroboration I held before starting: no DECISION row says YES."""
    r = I3["runtime_own_verdict"]
    assert r["any_missed_profitable_pair_YES"] is False
    assert r["decision_stage_rows"] == 14
    assert set(r["distinct_reasons"]) == {"DECIDE_COOLDOWN",
                                          "PAIR_ABOVE_REDEMPTION"}
    assert r["decision_stage_rows"] == (
        I4["decide_cooldown_rows"]["count_in_window"] + 4)


def test_the_pair_never_returned_under_redemption_in_the_whole_window():
    w = I5["through_window_end"]
    assert w["ever_below_one_dollar"] is False
    assert D(w["cheapest_maker_pair_after_the_move"]) > D("1.00")
    assert D(w["last_observation_pair"]) > D("1.40")
    assert "excluded rather than interpolated" in w["note"]


def test_the_next_lead_is_recorded_as_a_label_trap_and_not_acted_on():
    """The 14 OPENING 'YES' rows: the flag and the refusal price different
    things, so YES there is not evidence of an available pair."""
    n = DOC["next_lead_not_acted_on"]
    assert "conflation" in n["mechanism"]
    assert "l5_live_execution.py:6386" in n["mechanism"]
    assert "YES is not necessarily YES" in n["consequence"]
    assert n["status"].startswith("NOT investigated and NOTHING changed")


# -------------------------------- item 5: the book, with freshness stated

def test_the_complement_moved_monotonically_against_us():
    pairs = [D(r["pair"]) for r in I5["sequence_from_fill"]]
    assert pairs[0] == D("1.0500") and pairs[-1] == D("1.1100")
    # non-decreasing apart from sub-tick chop; never back under redemption
    assert min(pairs) > D("1.00")
    assert pairs[-1] > pairs[0]


def test_no_decision_in_the_window_read_a_stale_book():
    ages = [r["stream_age_s"] for r in I5["sequence_from_fill"]
            if r["stream_age_s"] is not None]
    assert ages and max(ages) < 0.2


def test_the_own_price_convention_is_stated_and_self_consistent():
    assert "1-p" in I5["note"]
    for r in I5["sequence_from_fill"]:
        assert D(r["down_maker_own"]) == D(1) - D(r["up_ask"])
        assert D(r["pair"]) == D("0.47") + D(r["down_maker_own"])


# --------------------------- item 6: nothing procedural blocked submission

def test_no_order_cooldown_reconciliation_or_schedule_blocked_it():
    i6 = DOC["item6_order_cooldown_reconciliation_scheduling"]
    assert i6["existing_order_blocked_it"] is False
    assert i6["cooldown_blocked_it"] is False
    assert i6["reconciliation_state_blocked_it"] is False
    assert i6["scheduling_delay"]["avoidable_delay_s"] < 0.5
    assert "only thing that did was the price" in i6["verdict"]


# ------------------------------------- item 7: the maker path was dominated

def test_every_maker_complement_price_was_also_above_redemption():
    assert I7["every_maker_pair_above_redemption"] is True
    for p in I7["maker_pairs"]:
        assert D(p) > D("1.00")


def test_the_maker_path_was_dominated_on_queue_as_well_as_price():
    assert I7["would_a_post_only_maker_complement_have_filled"].startswith("NO")
    assert max(D(q) for q in I7["queue_ahead_displayed"]) > D("900")
    assert "both axes" in I7["verdict"] or "BOTH axes" in I7["verdict"]


# ------------------------------- the counterfactual, answered not asserted

def test_fill_triggered_completion_would_not_have_changed_the_decision():
    assert CF["answer"] == "NO"
    assert CF["recoverable_latency_s"] < 0.5
    assert D(CF["pair_at_earliest_frame"]) > D("1.00")
    assert D(CF["pair_behind_touch_at_earliest_frame"]) > D("1.00")


def test_the_pre_fill_instant_is_reported_unmeasured_and_not_claimed():
    pf = CF["the_pre_fill_instant_is_not_measured"]
    assert "NO_BOOK_ROW" in pf["record"]
    assert "INFERENCE" in pf["inference_withheld"]
    assert "not claimed as an opportunity" in pf["inference_withheld"]


def test_even_the_inferred_099_pair_is_shown_fee_negative():
    """So the disabled $0.99 cap gains nothing from this window either."""
    pf = CF["the_pre_fill_instant_is_not_measured"]
    assert "fee-negative" in pf["and_it_would_not_have_paid"]
    # theta * C * p * (1-p) per the venue-verified taker coefficient
    fee = D("0.0695") * D("2.04") * D("0.52") * D("0.48")
    assert fee / D("2.04") > D("0.01")        # exceeds the 1c gross gain
    assert "disabled" in pf["and_it_would_not_have_paid"]


# -------------------------------------- my own defect, pinned on purpose

def test_the_premise_is_retracted_as_my_own_tape_artifact():
    assert "artifact of my own REST book tape" in DOC["headline"]
    assert "0.9351" in TAPE["what_i_claimed_before"]
    assert "derivation was correct" in TAPE["the_arithmetic_was_right_the_input_was_not"]


def test_three_independent_refutations_are_recorded():
    r = TAPE["three_independent_refutations"]
    assert len(r) == 3
    assert any("stream_age" in x for x in r)
    assert any("shares_traded_delta_lower_bound 0.0" in x for x in r)
    assert any("does not fill while the bid is 0.52" in x for x in r)


def test_the_tape_is_dark_for_most_of_the_window():
    cov = TAPE["tape_coverage_2230z"]
    assert cov["dark_fraction"] > 0.9
    assert cov["usable_for_second_level"] is False
    assert cov["max_gap_s"] > 30


def test_the_defect_is_recorded_as_systematic_across_windows():
    s = TAPE["this_is_systematic_not_one_window"]
    assert s["windows_measured"] >= 20
    assert s["median_dark_fraction"] > 0.9
    assert s["median_inter_sample_gap_s"] > 10


def test_the_retraction_scope_is_bounded_rather_than_total():
    scope = TAPE["scope_of_the_retraction"]
    assert "second-level resolution is unreliable" in scope
    assert "Window-level statistics" in scope and "remain usable" in scope
    assert "l5_actions.pkl" in scope and "not implicated" in scope


# --------------------------------------------------------- safety is held

def test_every_standing_limit_is_unchanged_and_nothing_was_deployed():
    s = DOC["safety"]
    assert s["pair_cap"].startswith("0.96")
    assert s["cancel_margin_s"] == 300.0
    assert s["armed"] is False
    assert s["live_orders_placed_this_turn"] == 0
    assert s["new_services"] == 0
    assert s["completion_099_cap"] == "disabled"
    assert s["continuous_inventory_flags"] == "both OFF"
    assert s["runtime_decision_change"].startswith("NONE")
    assert s["deployment"].startswith("NOT deployed")


def test_both_observability_changes_are_read_only():
    o = DOC["observability_improvement_added"]
    assert o["change_1"]["consulted_by_any_gate"] is False
    assert o["change_1"]["additive"] is True
    assert o["change_2"]["money_path"] is False
    assert "no event timestamp" in o["precise_missing_record"]
