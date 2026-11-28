"""Does COMPLETE re-quoting earn its place? Frozen BEFORE the replay is run.

OWNER: "immediately build and validate COMPLETE repriced:touch_moved using
the already-proven OPEN/EXTEND mechanism. Treat this as an L5-fidelity
correction, not a new strategy... If the offline evidence is strong, design
an isolated bounded live A/B."

ORDERING, AGAIN, IS THE POINT. This bar is written before the replay runs.
The replay is retrospective and every knob in it -- which price the re-quote
posts at, what conversion rate to assume, how to charge rejections -- could
be tuned until the answer came out favourable. So the answer is defined
first.

THE THING I ALREADY KNOW THAT MAKES THIS NON-OBVIOUS, and the reason the bar
is about MONEY rather than about completions. The live session already
computes pair_at_touch for every completion decision, and in the first
session I opened it read 1.0798 with above_par true: opening at 0.4698 and
completing at the 0.61 touch would cost $1.08 for a $1.00 payout. Repricing
up to the touch does not only buy fills -- it buys them at a worse price,
and some of those pairs are locked losses. A version of this analysis that
counted "additional completions" would have scored that as a win.

SO THE COMPARISON IS NOT completion vs no completion. It is:

    complete at the re-quote price   ->  a KNOWN pair cost, win or lose
    hold the residual to settlement  ->  a distribution, measured at
                                         -$0.331449 per unit on our cohort

An above-par completion can still beat stranding. A completion at 1.08 locks
-$0.08 per unit against a residual that has averaged -$0.33. That is the
real question and the bar is set on it, not on fill counts.

WHAT WOULD MAKE THIS FAIL, stated in advance:
  * the re-quotes are mostly above par and the net is negative;
  * the net is positive only if every trigger converts, and dies at a
    realistic conversion haircut;
  * the extra orders exhaust the action budget and cost us openings;
  * the extra orders land on the #93 rejection surface often enough to trip
    the breaker more, which revokes complement proofs.

ROLLBACK IS ASYMMETRIC. Not shipping costs nothing. Shipping is a decision
only the owner makes, and this module never makes it.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

GATE = "L5_COMPLETE_REQUOTE_OFFLINE_BAR_v1"

# ------------------------------------------------------------- what is known
# Measured before this bar was written, from the committed modules named.
BASELINE = {
    "complete_orders": 868,
    "complete_requote_exits": 0,            # the mechanism is off for COMPLETE
    "open_requote_exits": 36,
    "extend_requote_exits": 35,
    "complete_rejected_share": 0.1866,      # 162 / 868
    "unfilled_completes_with_rest_book": 404,
    "touch_rose_above_our_price": 164,
    "and_book_reached_that_touch": 96,      # the UPPER bound, not a forecast
    "residual_loss_per_unit_usd": "-0.331449",   # l5_breakeven, 15 markets
    "pair_profit_per_matched_pair_usd": "0.057230",
    "observed_leg_completion_rate": 0.834196,
    "breakeven_leg_completion_rate": 0.845161,
    "source": "l5_complete_no_fill_forensics + l5_breakeven, pre-change tape",
}

# --------------------------------------------------------------- must show
MUST_SHOW = {
    # THE headline, and it is denominated in dollars rather than in fills.
    # A change that completes more and earns less is a loss dressed as
    # progress.
    "net_counterfactual_usd_must_be_positive": True,
    # ...and it must survive a realistic haircut, not only the perfect case.
    # A re-quote surrenders queue position and the tape cannot sequence the
    # touch against the ask, so the middle rung is the one that counts.
    "net_positive_at_conversion_rung": 0.50,
    # enough events that the answer is not one lucky market
    "additional_completions_min": 10,
    "distinct_markets_min": 5,
    # the money must not come from a handful of outliers
    "max_share_of_net_from_one_market": 0.50,
}

# ----------------------------------------------------------- must not worsen
MUST_NOT_WORSEN = {
    # extra orders on the #93 surface. Modelled at the OBSERVED complete
    # rejection rate, charged against the net.
    "rejection_cost_must_be_charged_against_net": True,
    # the breaker exists to stop rejection cascades revoking complement
    # proofs; re-quotes must not trip it materially more often
    "breaker_trips_per_session_relative_max": 1.25,
    # re-quotes consume actions. If they exhaust the budget the session stops
    # opening, and a completion bought with a lost opening is not a gain.
    "sessions_losing_an_opening_to_budget_exhaustion_max": 0,
    # this is a clone
    "acted_relation_mix_l1_worse_by_at_most": 0.02,
}

# ------------------------------------------------------------ the refusals
REFUSALS = {
    "aggregate_not_sequence": (
        "rest_book is an aggregate: min_opp_ask and max_touch are extrema "
        "over one window and cannot be sequenced. Every conversion figure is "
        "therefore a BOUND under an assumption, never an effect size, and the "
        "report must say so at every point where a number appears."),
    "one_sided_trigger_detection": (
        "max_touch only reveals the touch moving UP. A touch that moved down "
        "would also fire a re-quote and is invisible here, so the trigger "
        "count is a LOWER bound while the conversion is an UPPER bound. The "
        "two errors do not cancel and must not be described as offsetting."),
    "no_live_read": (
        "this bar is scored on the pre-change tape only. It may not be "
        "re-scored on data produced after any COMPLETE re-quote ships, and it "
        "may never read L5_COMPLETION_EXTENSION_GATE_v1's evidence window."),
}

VERDICTS = (
    "OFFLINE_EVIDENCE_STRONG",        # every must-show met, nothing worsened
    "OFFLINE_EVIDENCE_WEAK",          # safe but does not earn a live trial
    "OFFLINE_EVIDENCE_NEGATIVE",      # the tape says this loses money
    "UNVERIFIABLE",                   # the tape cannot answer it
)

# Only the first verdict authorises DESIGNING a live A/B. None of them
# authorises shipping one.
AUTHORISES_LIVE_AB_DESIGN = ("OFFLINE_EVIDENCE_STRONG",)
AUTHORISES_SHIPPING = ()

NON_CONTAMINATION = (
    "L5_COMPLETION_EXTENSION_GATE_v1 is frozen and running. This bar shares "
    "no threshold with it, reads no forward evidence, and its verdict has no "
    "effect on that gate's sample minimums or criteria. Any live trial "
    "designed from this bar must be separable in the tape by an explicit arm "
    "label, so the extension's attribution stays clean.")

ROLLBACK = ("Not shipping costs nothing. Shipping is an owner decision and "
            "this module never makes it.")

GATE_FROZEN_UTC = "2026-09-08T20:40:00+00:00"


def _payload() -> str:
    return json.dumps({"gate": GATE, "baseline": BASELINE,
                       "must_show": MUST_SHOW,
                       "must_not_worsen": MUST_NOT_WORSEN,
                       "refusals": REFUSALS,
                       "verdicts": list(VERDICTS)},
                      sort_keys=True, separators=(",", ":"))


def gate_sha16() -> str:
    return hashlib.sha256(_payload().encode()).hexdigest()[:16]


GATE_SHA16_AT_FREEZE = "699cc8205fd23383"


def assert_unmodified() -> str:
    """Raise if a later edit changed any criterion after the freeze."""
    got = gate_sha16()
    if GATE_SHA16_AT_FREEZE == "PENDING":
        return got
    if got != GATE_SHA16_AT_FREEZE:
        raise AssertionError(
            f"{GATE} was modified after it was frozen at {GATE_FROZEN_UTC}: "
            f"sha {got} != {GATE_SHA16_AT_FREEZE}. Publish v2; never edit v1.")
    return got


def describe() -> Mapping[str, Any]:
    return {"gate": GATE, "frozen_utc": GATE_FROZEN_UTC, "sha16": gate_sha16(),
            "baseline": BASELINE, "must_show": MUST_SHOW,
            "must_not_worsen": MUST_NOT_WORSEN, "refusals": REFUSALS,
            "verdicts": list(VERDICTS),
            "authorises_live_ab_design": list(AUTHORISES_LIVE_AB_DESIGN),
            "authorises_shipping": list(AUTHORISES_SHIPPING),
            "non_contamination": NON_CONTAMINATION, "rollback": ROLLBACK}
