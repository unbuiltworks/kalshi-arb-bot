"""Frozen constants for the running pair-cost control confirmation study.

Everything here is fixed BEFORE confirmation results are examined. The
discovery-derived target is frozen at 0.98 from the completion study
(COMPLETION_FINDINGS.md @ fc19d586), where cumulative matched pair cost
measured 0.973-1.013 across all 12 checkpoints with cross-window IQR
0.0112-0.0763. It is NOT refitted here and NOT optimized.

S3 is untouched (8b20a259...87df). No live orders. No threshold search.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

SPEC: Dict[str, Any] = {
    "spec_name": "running_pair_cost_control_confirmation",
    "spec_version": 1,

    # ---- the frozen discovery value ---------------------------------------
    "target": {
        "TARGET_COST": 0.98,
        "provenance": "DISCOVERY_COMPLETION_STUDY @ fc19d586",
        "is_fitted_here": False,
        "band_primary": [0.96, 1.00],
        "band_halfwidth_primary": 0.02,
        "band_provenance": ("the completion study's observed terminal "
                            "cross-window IQR of 0.0267, rounded out to "
                            "0.02 either side. Frozen, not tuned"),
        "bands_secondary_preregistered": [[0.97, 0.99], [0.93, 1.03]],
        "secondary_note": ("+/-0.01 and +/-0.05 are fixed NOW as sensitivity "
                           "bands so that a band cannot be chosen after "
                           "seeing which one passes"),
    },

    # ---- the primary null, stated with its arithmetic --------------------
    # If the two complementary books sum to S(t) ~ 0.98 and a trader buys
    # both sides at/near the touch, the realised pair cost is ~S(t) BY
    # ARITHMETIC, with no control of any kind. The earlier §5 geometry
    # statistic already measured mean(UP price) + mean(DOWN price) per
    # window at a median of 0.977 -- numerically indistinguishable from the
    # 0.978 terminal pair cost this study is asked to explain.
    #
    # So the burden of proof is on CONTROL, not on the null.
    "mechanical_null": {
        "name": "NULL_M_complement_sum",
        "statement": ("running matched pair cost ~ 0.98 is a consequence of "
                      "(a) complementary books summing to ~0.98 and (b) "
                      "acquiring both outcomes at or near the touch, and "
                      "requires no cost control whatever"),
        "prior_evidence_for_the_null": {
            "mean_UP_plus_mean_DOWN_price_per_window_median": 0.977,
            "source": "STRUCTURE.json S5 ladder geometry",
            "two_touch_sum_whole_window": "~0.99 (S2_FINDINGS.md §5)",
        },
        "is_primary_null": True,
        "burden_of_proof": "on CONTROL, not on the null",
    },

    # ---- the discriminating test ----------------------------------------
    # The ONLY thing that separates control from arithmetic is SELECTION:
    # does L5 transact when the available complement sum is transiently
    # LOW, rather than at an arbitrary time?
    #
    #   available_sum(t)  = best ask UP(t) + best ask DOWN(t)   [taker view]
    #                       and best bid UP(t) + best bid DOWN(t) [maker view]
    #   time_weighted_sum = mean of available_sum over the window's seconds
    #   L5_realised       = volume-weighted realised pair cost
    #   SELECTION_EDGE    = time_weighted_sum - L5_realised
    #
    # SELECTION_EDGE <= 0 within noise  =>  no control, NULL_M stands.
    # SELECTION_EDGE > 0 reproducibly   =>  genuine timing control.
    "discriminating_test": {
        "name": "SELECTION_EDGE",
        "definition": ("time-weighted mean available complement sum minus "
                       "L5's volume-weighted realised pair cost"),
        "maker_and_taker_views_both_required": True,
        "bar": {"min_edge": 0.005, "min_windows": 9, "of_windows": 12},
        "interpretation": ("a positive reproducible edge is the only "
                           "evidence that 0.98 is maintained rather than "
                           "inherited from the book"),
    },

    # ---- accounting conventions, all three run -------------------------
    # Inventory is fungible, so attributing acquisition dollars to matched
    # versus residual shares is genuinely ambiguous. The conclusion must be
    # reported as convention-dependent or convention-robust.
    "attribution_conventions": {
        "C1_FIFO": "matched shares take the earliest open lots on each side",
        "C2_LIFO": "matched shares take the most recent open lots",
        "C3_VWAP_POOLED": ("matched shares carry each side's running VWAP, "
                           "i.e. no lot identity at all"),
        "primary": "C3_VWAP_POOLED",
        "primary_reason": ("it is the only convention that does not impose a "
                           "lot-matching story the chain data cannot "
                           "evidence; FIFO and LIFO are reported beside it"),
        "report_convention_dependence": True,
    },

    # ---- separations that could manufacture the result -----------------
    "separations": {
        "fees_separate_from_raw_acquisition_cost": True,
        "fee_model": ("model-implied 0.07*p*(1-p) per L5_OPERATING_MODEL.md, "
                      "reported on its own line and never folded into pair "
                      "cost"),
        "atomic_par_pairs_separated": True,
        "atomic_par_pair_risk": ("a $1.00 atomic taker pair mechanically "
                                 "inserts par inventory, which pulls any "
                                 "running average toward 1.00 and can "
                                 "manufacture a false appearance of active "
                                 "control"),
        "PRIMARY_RESULT_REPEATED_WITH_PAR_PAIRS_EXCLUDED": True,
        "par_pair_share_of_BTC15m_volume": 0.0972,
    },

    # ---- counterfactual controllers ------------------------------------
    "counterfactuals": (
        "CF0_NULL_M_touch_taker_no_control",
        "CF1_always_lighter_side",
        "CF2_fixed_per_pair_ceiling",
        "CF3_fixed_per_action_reservation_price",
        "CF4_equal_share_controller",
        "CF5_random_permutation_preserving_prices_and_sizes",
        "CF6_running_average_pair_cost_controller",
    ),
    "counterfactual_scoring": {
        "must_reproduce_BOTH": ("observed matched-inventory accumulation",
                                "observed cumulative pair-cost trajectory"),
        "metric": ("L1 distance to L5's own trajectory in each, per window; "
                   "a controller reproducing one and not the other FAILS"),
        "smallest_wins": ("among controllers that reproduce both within "
                          "tolerance, the one with the fewest free "
                          "parameters is reported as the answer"),
    },

    # ---- frozen bins, chosen before results ----------------------------
    "frozen_bins": {
        "probability": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5,
                        0.6, 0.7, 0.8, 0.9, 1.0],
        "tte_s": [0, 120, 300, 600, 900],
        "imbalance_shares": [0, 40, 80, 160, 320, 100000],
        "large_price_move_threshold": 0.05,
        "large_price_move_lookback_s": 30,
        "action_class": ("EXTENDS", "COMPRESSES", "NEUTRAL"),
        "role_class": ("maker", "taker"),
        "no_subdivision_search": True,
        "min_n_per_cell": 30,
        "underpowered_cells_excluded_not_pooled": True,
    },

    # ---- the seven falsification tests ---------------------------------
    "falsification_tests": {
        "F1_entry_latency": "matched shares and seconds until the band is first entered",
        "F2_residency": "fraction of subsequent matched inventory AND time inside the band",
        "F3_max_excursion": "maximum absolute deviation from TARGET_COST",
        "F4_return_after_excursion": "does it re-enter after leaving, and how fast",
        "F5_LOWO_target": "does the frozen 0.98 survive leave-one-window-out",
        "F6_vs_marginal": "is cumulative materially more stable than marginal/per-parcel",
        "F7_vs_other_quantities": ("is it materially more stable than matched "
                                   "fraction, share allocation, dollar "
                                   "allocation, and raw price/probability"),
    },
    "F6_F7_fairness_requirement": (
        "a cumulative average is MECHANICALLY smoother than the increments "
        "it averages, so 'cumulative beats marginal' is near-tautological "
        "and is NOT evidence of control. F6 and F7 must therefore compare "
        "against the SAME cumulative transform of every competing quantity, "
        "and against NULL_M's own cumulative pair cost. Comparing a "
        "cumulative series to a marginal one would be the tautology this "
        "program has already hit twice"),

    # ---- the budget / headroom causal test -----------------------------
    "budget_test": {
        "definition": ("budget(t) = TARGET_COST * prospective_matched_shares "
                       "- prospective_total_matched_cost, using ONLY "
                       "information available at t"),
        "question": ("does cheap EXTEND inventory create measurable "
                     "headroom that is subsequently SPENT on dearer "
                     "COMPRESS inventory"),
        "quantify": "cheap-side savings -> subsequent complement premium",
        "window_by_window": True,
        "bar": {"min_rho": 0.40, "min_windows": 9, "of_windows": 12,
                "note": ("savings during EXTEND must correlate with later "
                         "willingness to pay above target on COMPRESS")},
        "causal_direction_caveat": (
            "savings and later premium are both driven by the same price "
            "path, so a positive correlation is consistent with, but does "
            "not establish, budget-driven choice. This is reported as an "
            "association unless the SELECTION_EDGE test also passes"),
    },

    # ---- verdict, defined before results -------------------------------
    "verdict_classes": {
        "REFUTED": ("the band is not maintained, OR NULL_M reproduces the "
                    "trajectory as well as L5 does"),
        "DESCRIPTIVE_ONLY": ("the band is real and stable but SELECTION_EDGE "
                             "is <= 0 within noise, so stability is "
                             "inherited from the book rather than "
                             "maintained"),
        "PREDICTIVE_CONTROL_SIGNAL": ("SELECTION_EDGE passes AND the budget "
                                      "test passes, so running cost "
                                      "predicts subsequent action"),
        "ECONOMIC_CONTROL_SIGNAL": ("the above AND the control demonstrably "
                                    "improves acquisition economics against "
                                    "every counterfactual, fees accounted "
                                    "separately and par pairs excluded"),
    },
    "exactly_one_verdict": True,

    "constraints": {
        "s3_modified": False,
        "s3_fingerprint":
            "8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df",
        "live_orders": False,
        "threshold_optimization": False,
        "target_refitted": False,
    },
}


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


# HARD-CODED LITERAL. Deriving this from SPEC at import would make the
# guard vacuous -- it would recompute the value it compares against.
PAIRCOST_FINGERPRINT = \
    "e90895545a94999b9f9ac5ff472e92efb1042f0be00bf2d42d0eb738f5523b10"


def assert_frozen() -> None:
    got = fingerprint()
    if got != PAIRCOST_FINGERPRINT:
        raise SystemExit(
            "pair-cost spec moved: expected %s, got %s. Re-preregister "
            "rather than re-running." % (PAIRCOST_FINGERPRINT, got))


if __name__ == "__main__":
    print(fingerprint())
