"""Frozen S3 measurement specification.

S3 IS NOT A PREDICTOR. It carries no model of L5's next action, no
next-action features, and no conditional inventory forecast. Those endpoints
were preregistered and FAILED (next-action lift 1.001 vs persistence on
BTC15m, 12/12 windows; E2_POSITIVE=False at every horizon). This module must
never grow a feature whose purpose is to predict L5.

S3's single question:

    If we explicitly MAINTAIN bounded passive liquidity rather than predict
    L5's next fill, does the current PMUS BTC15m market provide enough clean
    maker execution to recreate the economically useful part of L5's
    inventory formation?

Every parameter below carries its PROVENANCE. A parameter whose provenance is
not surviving evidence is labelled as a design constraint, and no parameter
anywhere was chosen by looking at settlement P&L.

ZERO CAPITAL. NO ORDER SUBMISSION. NO SIGNING. Frozen prospective S1
(441c375e...b6fe) is not imported for mutation and not modified.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

# --------------------------------------------------------------------------
# Placement and maintenance mechanics, frozen before implementation.
#
# PROVENANCE KEYS
#   L5_EVIDENCE      measured from L5's own verified fills / the L2 archive
#   S2_FAILURE       forced by a demonstrated S2 failure mode
#   PAIR_ECONOMICS   arithmetic of fee-safe pair completion, not L5 behaviour
#   VENUE            read from PMUS market metadata at runtime
#   LEARNING_BOUND   deliberately small for a measurement stage
# --------------------------------------------------------------------------
SPEC: Dict[str, Any] = {
    "spec_name": "S3_passive_liquidity_measurement",
    "spec_version": 1,
    "is_predictor": False,

    # ---- placement -------------------------------------------------------
    "placement": {
        # L5 rests AT the touch: median 0 ticks below best bid, 26% exactly
        # at it (STRUCTURE.json S5, n=1926).
        "target_offset_ticks_below_touch": 0,
        "provenance_target_offset": "L5_EVIDENCE",

        # One level per side for the measurement stage. L5's ladder is >=3
        # simultaneous levels, but that count is a LOWER BOUND (cancels are
        # invisible) and widening it multiplies hypothetical fills without
        # improving the evidence standard.
        "levels_per_side": 1,
        "provenance_levels": "LEARNING_BOUND",

        # <=80 is a latent CLIP CEILING, not proof every order is 80
        # (19.4% of clips reach 80; median clip is 0.31 of 80). The intended
        # quantity is therefore well below the ceiling and is NOT 80.
        "intended_quantity_shares": 20.0,
        "clip_ceiling_shares": 80.0,
        "quantity_is_not_the_ceiling": True,
        "provenance_quantity": "LEARNING_BOUND",

        "both_sides": True,
        "provenance_both_sides": "L5_EVIDENCE",
        "maker_only_post_only": True,
        "taker_permitted": False,

        # PMUS exposes no mint/split/merge primitive. The $1.00 atomic taker
        # complete-set pair is IMPOSSIBLE there and MUST NOT be emulated with
        # two separate taker orders, which are a different instrument with
        # leg risk.
        "atomic_par_pair": "FORBIDDEN_NOT_AVAILABLE_ON_PMUS",
    },

    # ---- the reservation price: S2's failure mode, closed ----------------
    # S2 restored two-sided acquisition (matched fraction 0.800 -> 0.947,
    # stale side-ticks 719-1152 -> 0 in 11/11 windows) but its pair cost
    # degraded MONOTONICALLY, 0.940 at 01:00 to 1.052 at the close: each
    # displacement reprice re-offered at a worse level, converting
    # inactivity into adverse acquisition.
    #
    # S3 therefore caps acquisition price per side INDEPENDENTLY of where the
    # touch goes. The cap is pair arithmetic, NOT an L5 behaviour: L5's own
    # withdrawal shows no association with book variables, so a
    # price-triggered stand-down is explicitly OUR design choice and is
    # labelled as such.
    "reservation": {
        "max_combined_pair_cost": 0.96,
        "provenance": "PAIR_ECONOMICS",
        "rule": ("never place or reprice a side above "
                 "max_combined_pair_cost minus the best achievable price on "
                 "the complement; if the touch moves above that, STAND DOWN "
                 "on that side rather than follow it"),
        "follow_the_touch_unboundedly": False,
        "closes_s2_failure_mode": "monotonic pair-cost degradation",
    },

    # ---- maintenance -----------------------------------------------------
    # S1's failure was stranding: one bid untouched 876 s, ending 62 ticks
    # behind the market, inert from 04:00 onward. The thresholds below are
    # L5's own observable bounds, taken at the LOOSE end so they reprice
    # less, never more.
    "maintenance": {
        "reprice_on_displacement_price_distance": 0.06,
        "provenance_displacement": "L5_EVIDENCE",
        "displacement_note": ("L5's fill offset below the touch is p99 = 6 "
                              "and max = 9 on Polymarket's 0.01 grid, i.e. "
                              "0.06 in PRICE. PMUS crypto tick is 0.005, so "
                              "'6 ticks' does NOT translate and the "
                              "threshold is held in price, not ticks"),
        "reprice_on_staleness_s": 69,
        "provenance_staleness": "L5_EVIDENCE",
        "staleness_note": ("L5 is never absent from a token for more than "
                           "69 s; taken as the max, not a percentile"),
        "fixed_order_lifetime_s": None,
        "a_reprice_is_never_a_fill": True,
        "reprice_subject_to_reservation": True,
    },

    # ---- withdrawal ------------------------------------------------------
    # L5's withdrawal is strongly associated with TTE (Cliff's delta -0.82),
    # accumulated same-side inventory (+0.74), matched inventory (+0.72) and
    # clip exhaustion (+0.48), and NOT with any contemporaneous book
    # variable (|delta| <= 0.08 for spread, depth at touch, depth at our
    # price, price). S3's withdrawal rule is built from the former only.
    "withdrawal": {
        "stop_opening_new_interest_tte_s": 120,
        "provenance_tte": "L5_EVIDENCE",
        "tte_note": ("withdrawal-associated TTE median is 102 s against 485 s "
                     "for continuation; 120 s is the loose side of that"),
        "withdraw_on_clip_exhaustion": True,
        "provenance_clip_exhaustion": "L5_EVIDENCE",
        "clip_exhaustion_note": ("median fraction of 80 consumed before "
                                 "apparent withdrawal is 1.00, against 0.27 "
                                 "before a level move"),
        "withdraw_on_inventory_target_reached": True,
        "no_book_triggered_withdrawal": True,
        "book_triggered_withdrawal_note": ("the reservation stand-down is a "
                                           "PRICE constraint of ours, "
                                           "recorded separately from "
                                           "L5-evidenced withdrawal reasons "
                                           "so the two are never conflated"),
    },

    # ---- bounds ----------------------------------------------------------
    "bounds": {
        "markets_concurrent": 1,
        "outstanding_orders_per_side": 1,
        "residual_cap_shares": 80.0,
        "residual_cap_unchanged_from_s1_s2": True,
        "max_notional_per_market_usdc": 50.0,
        "provenance_bounds": "LEARNING_BOUND",
    },

    # ---- evidence standard ----------------------------------------------
    "evidence": {
        "ledgers": ("EVIDENCED_FILL", "UNCERTAIN", "NO_FILL"),
        "touch_equals_fill": False,
        "invented_queue_priority": False,
        "queue_ahead_is_an_estimate_with_stated_bounds": True,
        "hypothetical_fill_may_be_primary_economics": False,
        "primary_economics_requires": "independent execution evidence",
        "known_bias": ("a level-consumed standard fires only when the touch "
                       "crosses us and therefore selects adverse fills; it "
                       "is reported with that bias stated, never as an "
                       "unbiased estimate"),
        "partial_fill_estimate_only_where_defensible": True,
    },

    # ---- accounting ------------------------------------------------------
    "accounting": {
        "matched_pair_economics_and_residual_settlement_reported_separately":
            True,
        "rebates_counted": False,
        "rewards_counted": False,
        "rebate_rule": ("not counted unless actually attributable under PMUS "
                        "rules and actually credited; hypothetical fees and "
                        "rewards are accounted on their own lines and never "
                        "mixed into trading economics"),
        "realized_label_requires": "settlement",
    },

    # ---- cohort and gate, fixed BEFORE any collection -------------------
    "cohort": {
        "n_markets_required": 25,
        "horizon": "BTC15m",
        "contiguous_not_cherry_picked": True,
        "stop_rule": ("the cohort is fixed at 25 markets; it may not be "
                      "extended to reach a metric, and results may not be "
                      "read before the 25th market closes except for the "
                      "catastrophic-mismatch gate below"),
    },
    "catastrophic_mismatch_gate": {
        "evaluated_every_market": True,
        "purpose": ("abort collection early if the instrument is measuring "
                    "something broken, NOT to peek at economics"),
        "abort_if_any": {
            "orders_never_evidenced_in_first_n_markets": 5,
            "two_sided_evidenced_acquisition_markets_below": 0.20,
            "queue_ahead_unobservable_share_above": 0.50,
            "bbo_staleness_breaches_share_above": 0.10,
            "reservation_violations": 0,
            "any_order_submitted_or_signed": 0,
        },
        "note": ("a gate breach halts collection and is reported as a "
                 "measurement failure. It is never grounds for retuning a "
                 "placement parameter and re-running"),
    },

    # ---- advancement metrics, predefined -------------------------------
    "advancement_metrics": (
        "M1_evidenced_fills_per_market",
        "M2_two_sided_evidenced_acquisition",
        "M3_matched_fraction",
        "M4_residual_distribution_and_max",
        "M5_acquisition_pair_cost",
        "M6_queue_ahead_and_resting_time_distributions",
        "M7_markouts_1_5_10_30_60s",
        "M8_capital_tied_up",
        "M9_withdrawal_behaviour",
        "M10_hypothetical_fees_and_rewards_separately_accounted",
    ),

    # ---- hard constraints ------------------------------------------------
    "constraints": {
        "zero_capital": True,
        "order_submission": False,
        "signing": False,
        "s1_frozen_fingerprint":
            "441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe",
        "s1_modified": False,
        "no_next_action_prediction": True,
        "no_conditional_inventory_forecast": True,
        "not_optimized_against_settlement_pnl": True,
    },
}


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    """Stable fingerprint over the frozen spec, sorted-JSON SHA256."""
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


# Asserted by every S3 runner. A silent drift must fail the run, not the
# review.
#
# This is a HARD-CODED LITERAL on purpose. Computing it from SPEC at import
# would make assert_frozen() vacuous -- it would recompute the same value it
# compares against and pass for every possible edit to the spec.
S3_FINGERPRINT = \
    "8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df"


def assert_frozen() -> None:
    got = fingerprint()
    if got != S3_FINGERPRINT:
        raise SystemExit(
            "S3 spec moved: expected %s, got %s. A parameter changed after "
            "freezing; re-preregister rather than re-running."
            % (S3_FINGERPRINT, got))


if __name__ == "__main__":
    print(S3_FINGERPRINT)
