"""The missed-pair metric was measuring a constant. This is the repair.

THE RESULT, from the authenticated record rather than from the metric: of
184 inventory-adjusted completion evaluations across the four campaign
windows that held inventory, ZERO were below the $1.00 redemption value and
zero were at or below the 0.96 ceiling. The cheapest in 110 minutes was
exactly 1.0000. Meanwhile the old metric was publishing
`missed_profitable_pair: YES` at about 3.5 rows a second.

WHY v1 WAS WRONG, and it is two separate faults, not one.

  IT MEASURED A CONSTANT. Both opening legs quote d ticks behind their own
  touch, so pair = 1 - spread - 2*d*t: 0.9500 on every one-tick book at
  every price level. A verdict keyed on that figure carries no information.

  AND IT MEASURED THE WRONG QUANTITY. `PAIR_COST_ABOVE_CEILING` has exactly
  one producer, `pair_cost_refusal`, and that function returns None unless
  the order would create NEW MATCHED QUANTITY -- so it cannot fire on a
  genuine flat first leg at all. When it fires we were holding inventory,
  and the quantity it compared was eff = cost_after / matched_after over the
  basis ALREADY PAID. At 2230z the record published a 0.9400 "missed 6c
  pair" against an effective 1.04 that was correctly refused.

So v1 is FROZEN -- records carrying it are already on the tape -- and v2
(`missed_profitable_pair_in_state`) answers the question the opening stage
needs. v2 is never more optimistic than v1, and this file proves that
exhaustively rather than by assertion.

AND THE 683 ARE RETRACTED. Commit 03ff6eb8 concluded the achievable pair was
0.97 and the 0.96 ceiling therefore "arithmetically unreachable at depth 2".
That used 1 + spread - 2*d*t, quoting THROUGH each touch; a maker rests
BEHIND it, giving 1 - spread - 2*d*t = 0.9500, which CLEARS the ceiling. The
sign was corrected in code at 917d1ffe but that conclusion was never
withdrawn, and it was the wrong quantity regardless of sign.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_missed_pair_blocker as MPB

DOC = json.load(open("analysis/l5/missed_pair_evidence_audit_20261103.json"))
I2 = DOC["item2_four_way_separation"]
I683 = DOC["item2b_the_683_refusals"]
I3 = DOC["item3_label_correction"]
I5 = DOC["item5_how_many_opportunities"]
I6 = DOC["item6_the_loss_driver"]
I7 = DOC["item7_highest_impact_change"]


# ------------------------------------------------------ item 1: parity

def test_parity_is_reported_clean_on_the_named_commit():
    p = DOC["item1_parity"]
    assert p["head"] == "2eda65a9"
    assert p["ids"] == 80 and p["digest"] == "eb672d14b4baf439"
    assert p["identical_to_baseline"] is True
    assert p["tree_modified_during_run"] is False


# ------------------------------- item 2: the four categories, separated

def test_the_fresh_pair_figure_is_level_invariant_in_fact_not_in_claim():
    """Derived from the deployed accessor, not restated here."""
    from src import l5_two_sided_probe as TSP
    d, t = int(TSP.depth_ticks()), D("0.01")
    pairs = set()
    for lo in range(20, 91):
        bid = D(lo) / 100
        ask = bid + t
        u = TSP.quote_own_price(TSP.UP, bid=bid, ask=ask, tick=t, depth_ticks=d)
        dn = TSP.quote_own_price(TSP.DOWN, bid=bid, ask=ask, tick=t,
                                 depth_ticks=d)
        pairs.add(u + dn)
    assert pairs == {D("0.9500")}, pairs          # one value, 71 price levels


def test_the_fresh_pair_clears_the_ceiling_so_the_683_story_cannot_hold():
    from src import l5_two_sided_probe as TSP
    assert D("0.9500") <= TSP.max_pair_cost()


def test_a_refusal_count_measures_ticks_not_chances():
    a = I2["A_fresh_pair_theoretical"]["measured_volume"]
    assert a["rows_in_a_144s_slice"] == 501
    assert a["distinct_states"] == 21
    assert a["tick_amplification"] == "23.9x"
    assert "not 683 lost opportunities" in a["meaning"]


def test_zero_authenticated_evaluations_were_below_redemption():
    b = I2["B_inventory_adjusted_economically_valid"]
    assert b["authenticated_taker_evaluations"] == 184
    assert b["below_redemption_1_00"] == 0
    assert b["at_or_below_ceiling_0_96"] == 0
    assert D(b["pair_min"]) == D("1.0000")
    assert b["crossed"] == 0
    assert len(b["windows"]) == 4


def test_every_window_cheapest_pair_is_at_or_above_redemption():
    b = I2["B_inventory_adjusted_economically_valid"]
    for w, p in b["cheapest_per_window"].items():
        assert D(p) >= D("1.00"), (w, p)


def test_the_single_boundary_case_is_reported_as_one_not_as_zero():
    """Honesty in both directions: it was not zero, and it was not many."""
    one = I2["B_inventory_adjusted_economically_valid"]["one_boundary_maker_case"]
    assert D(one["pair_behind_touch"]) == D("0.9600")
    assert D(one["pair_at_touch"]) == D("0.9800")
    assert D(one["pair_taker"]) == D("1.0000")
    assert "ONLY the behind-touch" in one["admissible_under_0_96"]
    assert one["order_resting_next_sample"] is True
    assert "did not fill" in one["verdict"]


def test_the_rest_tape_is_retracted_as_an_executable_source():
    d = I2["D_supported_only_by_sparse_or_lagged_REST"]
    assert "RETRACTED" in d["verdict"]
    assert "93.0%" in d["coverage"]
    assert "twelve cents" in d["known_disagreement"]
    assert "Window-level statistics over it remain usable" in d["verdict"]


def test_the_authenticated_source_is_named_primary_and_is_fresh():
    c = I2["C_supported_by_authenticated_executable_book"]
    assert c["source"] == "authenticated_ws"
    assert max(c["stream_age_range_s"]) < 0.2


# -------------------------------------------- item 2b: the 683, retracted

def test_the_683_cannot_be_reclassified_and_that_is_stated_not_finessed():
    assert I683["can_they_be_reclassified_individually"] is False
    assert "AGGREGATE COUNTER" in I683["why_not"]
    assert "written in RESPONSE" in I683["why_not"]


def test_the_683_class_is_settled_structurally_instead():
    s = I683["but_their_class_is_determinable_structurally"]
    assert "6386" in s["sole_producer"]
    assert "matched_after <= matched_before" in s["and_it_cannot_fire_on_a_flat_first_leg"]
    assert "COMPLETING legs" in s["therefore"]


def test_both_errors_in_the_prior_reconstruction_are_named():
    r = I683["the_prior_reconstruction_is_retracted"]
    assert "03ff6eb8" in r["where"]
    assert "1 + spread" in r["error_1_wrong_sign"]
    assert "0.97 should have been 0.95" in r["error_1_wrong_sign"]
    assert "never the quantity the gate compared" in r["error_2_wrong_quantity"]
    assert "is FALSE" in r["consequence"]
    assert r["status"].startswith("RETRACTED")


# ----------------------------------------- item 3: the label, in the code

def test_v1_keeps_its_exact_meaning():
    """A record with no inventory and a cheap pair still reads YES on v1."""
    r = MPB.record(market="m", reason="PAIR_COST_ABOVE_CEILING",
                   decision={"pair_own_cost": "0.9400"}, entry_ceiling="0.96",
                   stage="OPENING")
    assert r["missed_profitable_pair"] == "YES"
    assert r["definitions"]["missed_profitable_pair"] == MPB.DEF_V1


def test_v2_downgrades_an_opening_refusal_held_against_inventory():
    r = MPB.record(market="m", reason="PAIR_COST_ABOVE_CEILING",
                   decision={"pair_own_cost": "0.9400", "stream_age_s": "0.02"},
                   entry_ceiling="0.96", stage="OPENING",
                   held_side="UP", held_qty="2.04",
                   book_source="authenticated_ws")
    assert r["missed_profitable_pair"] == "YES"
    assert r["missed_profitable_pair_in_state"] == "N/A"
    assert r["in_state"]["downgraded_because"] == MPB.UNMATCHED_HELD
    assert "2.04 unmatched contracts" in r["in_state"]["not_an_opportunity_in_state"]


def test_v2_downgrades_on_the_effective_pair_when_the_caller_knows_it():
    r = MPB.record(market="m", reason="PAIR_COST_ABOVE_CEILING",
                   decision={"pair_own_cost": "0.9400"}, entry_ceiling="0.96",
                   stage="OPENING", effective_pair_cost="1.0400")
    assert r["missed_profitable_pair_in_state"] == "NO"
    assert "EFFECTIVE_PAIR_ABOVE_REDEMPTION" in r["in_state"]["downgraded_because"]
    assert r["in_state"]["effective_pair_cost"] == "1.0400"


def test_a_flat_opening_with_a_cheap_pair_is_still_a_yes_on_v2():
    """v2 must not simply refuse everything -- that would be the other bug."""
    r = MPB.record(market="m", reason="MAX_LIVE_ORDERS",
                   decision={"pair_own_cost": "0.9400"}, entry_ceiling="0.96",
                   stage="OPENING", held_qty="0")
    assert r["missed_profitable_pair_in_state"] == "YES"


def test_a_decision_stage_completion_verdict_is_untouched_by_v2():
    """At DECISION stage the priced pair IS the real completion, so v1 was
    always right there and v2 must agree with it."""
    for pc, want in (("0.9400", "YES"), ("1.1000", "NO")):
        r = MPB.record(market="m", reason="PAIR_ABOVE_REDEMPTION",
                       decision={"pair_own_cost": pc}, entry_ceiling="0.96",
                       stage="DECISION", held_side="UP", held_qty="2.04")
        assert r["missed_profitable_pair"] == want
        assert r["missed_profitable_pair_in_state"] == want


def test_v2_is_never_more_optimistic_than_v1_exhaustively():
    rank = {"N/A": 0, "NO": 1, "UNKNOWN": 2, "YES": 3}
    seen = 0
    for stage in ("OPENING", "DECISION"):
        for pc in (None, "0.9400", "1.1000"):
            for hq in (None, "0", "2.04"):
                for eff in (None, "1.0400", "0.9000"):
                    r = MPB.record(market="m",
                                   reason="PAIR_COST_ABOVE_CEILING",
                                   decision={"pair_own_cost": pc},
                                   entry_ceiling="0.96", stage=stage,
                                   held_side="UP", held_qty=hq,
                                   effective_pair_cost=eff)
                    assert (rank[r["missed_profitable_pair_in_state"]]
                            <= rank[r["missed_profitable_pair"]])
                    seen += 1
    assert seen == 54


def test_the_fresh_pair_caveat_rides_every_opening_record():
    r = MPB.record(market="m", reason="X", decision={"pair_own_cost": "0.95"},
                   stage="OPENING")
    assert r["in_state"]["fresh_pair_caveat"] == MPB.FRESH_PAIR_IS_LEVEL_INVARIANT


# ---------------------------------- item 4: evidence basis as a first field

def test_an_authenticated_current_book_is_executable_evidence():
    e = MPB.evidence_basis(book_source="authenticated_ws", stream_age_s="0.017")
    assert e["basis"] == MPB.EV_AUTHENTICATED
    assert e["executable_evidence"] is True


def test_a_stale_authenticated_frame_is_not_executable_evidence():
    e = MPB.evidence_basis(book_source="authenticated_ws", stream_age_s="30")
    assert e["basis"] == MPB.EV_REST_SPARSE
    assert e["executable_evidence"] is False


def test_a_rest_observation_is_never_executable_evidence():
    for src in ("rest_tape", "REST_POLL", "scratchpad tape"):
        e = MPB.evidence_basis(book_source=src, stream_age_s="0.1")
        assert e["executable_evidence"] is False


def test_an_unstated_source_is_unknown_rather_than_assumed_good():
    e = MPB.evidence_basis(book_source=None, stream_age_s="0.01")
    assert e["basis"] == MPB.EV_UNKNOWN
    assert e["executable_evidence"] is False


def test_an_unreadable_stream_age_does_not_earn_the_authenticated_label():
    e = MPB.evidence_basis(book_source="authenticated_ws", stream_age_s="junk")
    assert e["basis"] == MPB.EV_UNKNOWN


# ----------------------------------------- items 5, 6, 7: the conclusions

def test_the_opportunity_count_is_one_and_it_was_not_captured():
    assert I5["genuinely_profitable_inventory_adjusted_authenticated"] == 1
    assert I5["acted_on"] == 0
    assert I5["taker_evaluations_below_redemption"] == 0
    assert I5["of"] == 184
    assert I5["missed_profitable_pairs_under_v2"] == 0
    assert I5["every_refusal_was_correct"] is True
    assert "not going to assert a fill that did not happen" in \
        I5["why_that_one_was_not_captured"]


def test_the_loss_driver_is_adverse_selection_with_the_others_refuted():
    assert I6["answer"] == "FIRST-LEG ADVERSE SELECTION"
    assert I6["candidate_1_insufficient_complement_liquidity"]["verdict"] == "REFUTED"
    assert "446x" in I6["candidate_1_insufficient_complement_liquidity"]["evidence"]
    assert I6["candidate_2_a_correctable_execution_defect"]["verdict"].startswith("REFUTED")
    assert "CONFIRMED, 4 of 4" in I6["candidate_3_first_leg_adverse_selection"]["verdict"]


def test_the_complement_got_dearer_in_every_single_window():
    path = I6["candidate_3_first_leg_adverse_selection"]["complement_price_path_after_our_fill"]
    assert len(path) == 4
    for w, s in path.items():
        lo, hi = s.split(" own")[0].split(" -> ")
        assert D(hi) > D(lo), (w, s)


def test_the_highest_impact_finding_is_arithmetic_not_a_sample():
    assert I7["this_is_arithmetic_not_a_sample"] is True
    assert "less fillable price" in I7["finding"].lower() or \
        "LESS FILLABLE PRICE" in I7["finding"]
    assert "self-defeating" in I7["consequence"]


def test_the_change_is_identified_as_an_owner_policy_decision_not_taken():
    assert "ceiling" in I7["the_change_itself"]
    assert "live trading policy" in I7["why_I_am_not_implementing_either"]
    assert "owner decision" in I7["why_I_am_not_implementing_either"]


def test_entry_filters_are_not_re_proposed():
    assert "No filter is promotable" in I7["entry_filters_are_already_refused"]
    assert "not re-running that" in I7["entry_filters_are_already_refused"]


def test_nothing_about_live_policy_moved():
    s = DOC["safety"]
    assert s["live_trading_policy_changed"] is False
    assert s["pair_cap"].startswith("0.96")
    assert s["cancel_margin_s"] == 300.0
    assert s["completion_099_cap"] == "disabled"
    assert s["risk_limits"] == "unchanged"
    assert s["armed"] is False
    assert s["new_infrastructure"] == 0
    assert s["orders_placed"] == 0
    assert s["deployed"] is False
