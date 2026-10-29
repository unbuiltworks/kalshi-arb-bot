"""Does extended L5 completion earn its place? Frozen BEFORE the evidence.

OWNER: "Do not optimize around the first few results. Prioritize whether
extended L5 completion materially improves completion rate and reduces
stranded residual while preserving fidelity and every existing risk bound."

ORDERING IS THE WHOLE POINT, exactly as it was for the concurrency gate.
This module is written when the extension has produced ONE market and ONE
extended attempt -- far too little to see anything. A threshold chosen after
the numbers arrive is not a threshold, it is a description of the numbers.
The evaluator will import this; this will never import the evaluator, and a
test asserts it contains no code capable of computing its own criteria.

THE BASELINES ARE MEASURED ON PRE-CHANGE DATA AND FROZEN HERE. Recomputing
a baseline later, from a window that overlaps the change, is the most
comfortable way to prove yourself right.

WHAT WOULD MAKE THIS FAIL, stated in advance:
  * completion does not actually improve -- more attempts, same pairing;
  * it improves pairing but by chasing, so relation-mix fidelity drifts;
  * more completion attempts mean more REJECTED completes, which is the
    #93 surface and the complement-proof revocation that took the lane
    down at 13:09 on 2026-09-08.

ROLLBACK IS ASYMMETRIC ON PURPOSE. Reverting the extension needs no owner
decision -- it restores the prior behaviour and costs nothing. KEEPING it
past the trial is a decision only the owner makes.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

GATE = "L5_COMPLETION_EXTENSION_GATE_v1"

# ---------------------------------------------------------------- baselines
# All measured on data produced BEFORE 5db1bb8e deployed (18:47:44Z
# 2026-09-08) and frozen here so they cannot drift toward the answer.
BASELINE = {
    # session outcomes, 245 distinct session_end rows
    "sessions_matched": 36,
    "sessions_residual_hold": 143,
    "pairing_rate": 0.2011,          # 36 / (36 + 143)
    # complete-order outcomes, 666 orders
    "complete_orders": 666,
    "complete_fill_rate": 0.1370,
    "complete_no_fill_share": 0.6850,
    "complete_rejected_share": 0.1790,
    # settled cohort economics
    "paired_to_zero_markets": 12,
    "paired_to_zero_usd": "6.3639",
    "left_residual_markets": 4,
    "left_residual_usd": "-9.2384",
    "source": "l5_completion_economics + l5_r5f_cohort, pre-extension",
}

# ------------------------------------------------------------- must improve
MUST_IMPROVE = {
    # THE headline. Per SESSION, not per order: an order fill rate can rise
    # simply because more orders are placed, which is not the thing we want.
    "pairing_rate_relative_gain_min": 0.20,     # 0.2011 -> >= 0.2413
    # The money statement. Stranded residual is where every measured loss
    # is; if it does not fall, the extension has not done its job whatever
    # else improved.
    "stranded_residual_usd_per_settled_market_must_fall": True,
}

# --------------------------------------------------------- must not worsen
MUST_NOT_WORSEN = {
    # THE RISK THE OWNER'S FRAMING DID NOT COVER. More completion attempts
    # must not become more venue refusals: a rejected complete at a
    # non-crossing price revokes the complement proof.
    "rejected_completes_per_session_relative_max": 1.25,
    "complement_revocations_per_100_completes_must_not_rise": True,
    # Fidelity, same bar as the concurrency gate: this is a clone.
    "acted_relation_mix_l1_worse_by_at_most": 0.02,
    # The caps are untouched by the change, so this is a CHECK that they
    # still bind, not a new allowance.
    "worst_simultaneous_unmatched_usd_must_not_exceed_prior_max": True,
    "venue_ledger_must_reconcile": True,
}

# ------------------------------------------------------- sample minimums
# Below these the gate returns NOT_YET_ENOUGH_EVIDENCE and nothing else.
SAMPLE_MINIMUMS = {
    "forward_hours_on_the_extension": 48,
    "sessions_that_opened_a_residual": 40,
    # the extension only matters where it actually fired -- an action index
    # above the opening budget
    "sessions_where_the_extension_fired": 25,
    "settled_venue_confirmed_markets": 15,
}

VERDICTS = (
    "KEEP",                        # every must-improve met, nothing worsened
    "REVERT",                      # a must-not-worsen breached
    "NO_MATERIAL_IMPROVEMENT",     # safe but did not earn its place
    "UNVERIFIABLE",                # cannot be tied to venue evidence
    "NOT_YET_ENOUGH_EVIDENCE",     # sample minimums unmet
)

ROLLBACK = ("Reverting the extension needs NO owner decision: it restores "
            "prior behaviour and costs nothing. KEEPING it past the trial "
            "is an owner decision.")

GATE_FROZEN_UTC = "2026-09-08T19:05:00+00:00"
GATE_SHA16_AT_FREEZE = "fd6e31ddfbc42b40"


def _payload() -> str:
    return json.dumps({"gate": GATE, "baseline": BASELINE,
                       "must_improve": MUST_IMPROVE,
                       "must_not_worsen": MUST_NOT_WORSEN,
                       "sample_minimums": SAMPLE_MINIMUMS,
                       "verdicts": list(VERDICTS)},
                      sort_keys=True, separators=(",", ":"))


def gate_sha16() -> str:
    return hashlib.sha256(_payload().encode()).hexdigest()[:16]


def assert_unmodified() -> str:
    """Raise if a later edit changed any criterion.

    On failure the instruction is to publish a v2 gate with its own freeze
    time, NEVER to edit v1 -- a criterion that moves after the data arrives
    is not a criterion."""
    got = gate_sha16()
    if got != GATE_SHA16_AT_FREEZE:
        raise AssertionError(
            f"{GATE} was modified after it was frozen at {GATE_FROZEN_UTC}: "
            f"sha {got} != {GATE_SHA16_AT_FREEZE}. Publish v2; do not edit v1.")
    return got


def required_pairing_rate() -> float:
    """The number the extension has to beat, derived from the frozen
    baseline rather than restated as a literal that could drift."""
    return round(BASELINE["pairing_rate"]
                 * (1.0 + MUST_IMPROVE["pairing_rate_relative_gain_min"]), 4)


def describe() -> Mapping[str, Any]:
    return {"gate": GATE, "frozen_utc": GATE_FROZEN_UTC,
            "sha16": gate_sha16(), "baseline": BASELINE,
            "must_improve": MUST_IMPROVE, "must_not_worsen": MUST_NOT_WORSEN,
            "sample_minimums": SAMPLE_MINIMUMS, "verdicts": list(VERDICTS),
            "required_pairing_rate": required_pairing_rate(),
            "rollback": ROLLBACK}
