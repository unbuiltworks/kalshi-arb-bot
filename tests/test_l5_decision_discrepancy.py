"""The sub-$0.20 hypothesis, refuted; and L5's actual inventory discrepancy.

ITEM 5 -- SUB-0.20. Our own ledger suggested the band below own 0.20 was the
only profitable place to open: 5 venue-settled residual legs, 8.86 units,
paid 0.1455/unit and redeemed 0.2235/unit, i.e. +0.0780/unit.

THAT WAS SELECTION BIAS, and this is the test of it. Those 5 legs were
conditioned on STRANDING: a cheap leg only becomes residual when the
complement never arrived, and whether it arrived correlates with the price
move that also decides the payoff. On L5's 16,786 sub-0.20 fills --
unconditioned on whether they later paired, which is the right denominator
for an ENTRY decision -- the sign reverses: alpha -0.0244 at z -11.7, stable
in both chronological halves at z -7.7 and z -9.2.

So sub-0.20 is ADVERSELY priced. The favourable end is 0.65-1.01, which is
the favourite-longshot bias already in the record, and which this engine has
no authorized action class for holding to settlement.

ITEM 6 -- THE DISCREPANCY. ADD->ADD is L5's single most common transition
(36.5%), and our engine cannot ADD at all. But it is not a candidate: his
residual sits at median own price 0.395 where HIS fill-conditioned alpha is
+0.0043 at z=0.8 -- indistinguishable from fairly priced -- while OURS in the
same band lost 0.3994/unit. Same band, opposite outcome. The difference is
execution quality, not inventory policy.

Nothing here was enabled. Both continuous-inventory flags stay OFF, no band,
cap or limit moved, and the cancellation margin was reverted.
"""
from __future__ import annotations

import json

import pytest

DOC = json.load(open("analysis/l5/l5_decision_discrepancy_20261103.json"))
S5 = DOC["item5_sub_020_hypothesis"]
S6 = DOC["item6_highest_frequency_discrepancy"]
S7 = DOC["item7_candidate_search"]


# ------------------------------------------------- item 5: refuted

def test_the_sub_020_hypothesis_is_refuted():
    assert S5["verdict"].startswith("REFUTED")
    assert S5["powered_test"]["alpha"] < 0
    assert S5["powered_test"]["z"] < -7


def test_the_powered_sample_is_three_orders_of_magnitude_bigger():
    assert S5["powered_test"]["n_sub_020"] == 16786
    assert "5 venue-settled residual legs" in S5["our_evidence_that_suggested_it"]


def test_the_sign_reverses_in_both_chronological_halves():
    for half in ("train", "holdout"):
        assert S5["powered_test"][half]["alpha"] < 0, half
        assert S5["powered_test"][half]["z"] < -7, half


def test_the_selection_bias_is_named_as_conditioning_on_the_outcome():
    b = S5["the_selection_bias"]
    assert "conditioned on STRANDING" in b
    assert "Conditioning on the outcome is the bias" in b


def test_every_sub_020_sub_band_is_negative():
    a = S5["by_band_alpha"]
    for band in ("0.00-0.05", "0.05-0.10", "0.10-0.15", "0.15-0.20"):
        assert a[band] < 0, band


def test_the_favourable_end_is_the_favourite_band_not_the_longshot_band():
    a = S5["by_band_alpha"]
    assert a["0.65-1.01"] == max(a.values())
    assert a["0.65-1.01"] > 0


def test_nothing_was_enabled_by_the_investigation():
    assert "no flag, band or limit was changed" in S5["not_enabled"]


# ------------------------------------------------- item 6: the discrepancy

def test_add_to_add_is_the_highest_frequency_transition():
    t = S6["l5_transitions"]
    top = max(t.items(), key=lambda kv: kv[1]["pct"])
    assert top[0] == "ADD->ADD"
    assert top[1]["pct"] == 36.5


def test_l5_mostly_adds_again_rather_than_reducing():
    assert S6["add_followed_by"]["another ADD"] > S6["add_followed_by"]["a REDUCE"]


def test_l5_carries_a_large_contract_residual_but_a_small_dollar_one():
    m = S6["per_window_median"]
    assert m["final_residual_contracts"] == 100.0
    assert m["final_residual_usd"] < 50
    assert m["final_residual_usd_over_bought_usd"] < 0.10


def test_he_only_works_off_a_quarter_of_his_peak_residual():
    assert S6["per_window_median"]["final_over_peak_residual_ratio"] > 0.7


def test_the_residual_dollar_figure_cross_checks_an_independent_study():
    assert "consistent" in S6["cross_check"]


def test_the_discrepancy_is_execution_quality_not_inventory_policy():
    w = S6["why_it_is_NOT_a_candidate"]
    assert "EXECUTION QUALITY, not inventory policy" in w
    assert "0.3994" in w          # our loss in the same band
    assert "+0.0043" in w         # his alpha in the same band


# ------------------------------------------------- item 7: NO-GO

def test_no_arm_beats_the_baseline_holdout():
    arms = {a["arm"]: a for a in S7["arms"]}
    base = arms["BASELINE d2 cap0.96"]["holdout"]
    better = [a["arm"] for a in S7["arms"] if a["holdout"] > base]
    assert better == [], better


def test_the_no_go_is_not_achieved_by_refusing_trades():
    """The distinction the owner asked for explicitly."""
    for a in S7["arms"]:
        assert a["traded_pct"] == 84, a["arm"]
    assert "none of them is refusing trades" in S7["result"]


def test_depth_zero_is_reported_inconclusive_rather_than_negative():
    d = S7["depth0_is_INCONCLUSIVE_not_negative"]
    assert "cannot represent a queue-priority fill" in d
    assert "our_position_within_level UNKNOWN" in d


def test_the_verdict_is_no_go():
    assert DOC["verdict"].startswith("NO-GO")
    assert "retaining trading activity" in DOC["verdict"]


# ------------------------------------------------- what was preserved/reverted

def test_the_verified_accounting_corrections_are_preserved():
    from decimal import Decimal as D
    from src import l5_cross_shadow as X
    from src import pmus_cross_shadow_live as Y
    from src import l5_continuous_inventory as CI
    assert X.TAKER_THETA == D("0.0695")
    assert Y.TAKER_THETA == D("0.0695")
    assert X.MAKER_THETA == D("0.0125")        # untouched, not invented
    assert hasattr(CI, "inventory_epoch")


def test_the_maker_coefficient_was_not_invented():
    m = DOC["preserved"]["maker_theta"]
    assert "NOT verifiable by effective date" in m
    assert "NOT invented" in m


def test_historical_rebates_are_not_assumed_to_apply_today():
    assert "NOT assumed to apply today" in DOC["preserved"]["historical_rebates"]


def test_the_cancellation_margin_was_reverted_and_the_floor_is_raise_only():
    from src import l5_runtime_config_verify as V
    from src import l5_two_sided_probe as TS
    assert V.APPROVED["cancel_margin_s"] == 300.0
    assert V.APPROVED["defensive_terminal_s"] == 600.0
    assert V.APPROVED["defensive_guard_opens_s"] == 540.0
    assert V.APPROVED["defensive_last_actionable_s"] == 585.0
    # the floor is untouched and can only ever be WIDENED by configuration
    assert TS.CANCEL_MARGIN_S == 180.0
    assert TS.cancel_margin_s() >= TS.CANCEL_MARGIN_S
    assert "preserved in git history at f2ad5c69" in DOC["reverted"]["cancel_margin"]
