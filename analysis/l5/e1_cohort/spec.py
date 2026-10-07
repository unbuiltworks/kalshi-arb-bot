"""COHORT-E1-LIVE — the E1 mechanics cohort runner spec. FROZEN. DISARMED.

NOT AUTHORIZED. NOTHING HERE MAY PLACE AN ORDER. The arming gate is the
owner's, it is not set, and no code in this package sets it.

WHAT THIS IS
------------
`analysis/l5/measurement/E1_COHORT_DESIGN.md` derived WHAT to measure and
WHEN to stop. It deliberately stopped short of an executable runner, and it
listed seven preconditions that only the owner can supply. This module is
the executable form of that design under a bounded authorization the owner
has NOT yet given: the parameters below were handed to this lane as frozen
and NOT re-chosen, re-tuned or re-derived here.

DERIVATION
----------
Derived from LIVE-L5-EXEC-1-XR2W
(`ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562`) by
DEEP-COPYING its SPEC and applying a literal DIFF. XR2W carries the
wire-serialization correction that killed Attempt 5, the venue-error
capture, the exception-typed lifecycle classification and the durable
latch; all of that is inherited rather than rewritten.

THE INHERITED MUTATION BUG IS NOT INHERITED
-------------------------------------------
`analysis/l5/audit/FROZEN_SPEC_MUTATION_AUDIT.md` records that the shared
`_apply` helper assigns `DIFF`'s OWN nested objects into the built spec, so
`SPEC[p] is DIFF[p]` for every mutable diff path in five specs (XR2W among
them). `_apply` here deep-copies the value, exactly as
`analysis/l5/stage6/spec.py` already does. Deep-copying changes object
identity and never the canonical JSON, so this is fingerprint-neutral by
construction; it is asserted by test, both that nothing is aliased and that
`diff_report()` hands out copies.

WHAT CHANGES FROM XR2W, AND WHAT DOES NOT
-----------------------------------------
XR2W is ONE post-only order of 0.01 shares, once, and no complement. E1 is
a COHORT: a bounded sequence of first legs, each with a complement, over
twelve windows. What is inherited unchanged: the serializer, the order
mutation path, the preview-and-echo requirement, the exception-typed
lifecycle, post-only, the tick, the deadman, the markouts, and the rule
that an ambiguous submission is never retried.

WHAT IS HONEST AND UNCOMFORTABLE
--------------------------------
12 x 20 = 240 placements. The design's binding fill-denominated gate is
431 FILLS and its placement gate is 385 PLACEMENTS. Both exceed 240 at
ANY fill rate. **PASS IS THEREFORE UNREACHABLE INSIDE THIS AUTHORIZATION**
and this spec says so in `pass_reachability`. The authorized cohort is a
FAIL-or-INCONCLUSIVE instrument. That is not a reason to widen it; it is a
reason for the owner to know it before authorizing. Extending the cohort
requires a FRESH owner authorization and a new manifest.
"""
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal as D
from typing import Any, Dict, List, Tuple

from analysis.l5.live_exec2.spec import _get
from analysis.l5.live_exec5.spec import SPEC as XR2W_SPEC
from analysis.l5.live_exec5.spec import XR2W_FINGERPRINT
from analysis.l5.live_exec5.spec import assert_frozen as assert_xr2w_frozen

PROBE = "COHORT-E1-LIVE"

# --------------------------------------------------------------------------
# THE FROZEN PARAMETERS. Handed to this lane. NOT chosen here, NOT tuned
# here, NOT re-derived here. Each line names where it came from.
# --------------------------------------------------------------------------

#: E1_COHORT_DESIGN.md §1.4/§1.5 — the cent-rounding observability floor for
#: estimand 8. 0.13 x $0.04 = $0.0052 -> $0.01; 0.12 x $0.04 = $0.0048 ->
#: $0.00 under banker's rounding.
FIRST_LEG_QTY = D("0.13")

#: analysis/l5/stage6/bounds.py MAX_FIRST_LEG_PRICE. INHERITED, not re-chosen.
MAX_FIRST_LEG_PRICE = D("0.60")

#: CLAUDE.md spring discipline, fee-inclusive. Copied verbatim.
PAIR_COST_CEILING = D("0.96")

MAX_ATTEMPTS_PER_WINDOW = 20

#: ARITHMETIC: 20 x 0.13 x 0.60. Not a guess and not a judgement.
MAX_EXPOSURE_PER_WINDOW_USD = D("1.56")

#: The owner's hard limit. Our window bound is strictly BELOW it.
AUTHORIZED_WINDOW_CEILING_USD = D("5.00")

WINDOWS = 12

#: ARITHMETIC: 12 x 20 x 0.13 x 0.60.
ABSOLUTE_WORST_CASE_USD = D("18.72")

#: A HARD AUTOMATIC STOP, tighter than the worst case.
MAX_COHORT_REALIZED_LOSS_USD = D("5.00")

MAX_OUTSTANDING_ORDERS = 1
VENUE_TICK = D("0.01")
VENUE_MIN_QTY = D("0.01")

MAX_PLACEMENTS = WINDOWS * MAX_ATTEMPTS_PER_WINDOW                    # 240

#: The design's gates, READ from the design and not restated as new numbers.
DESIGN_PLACEMENT_GATE = 385
DESIGN_BINDING_FILL_GATE = 431

#: src/pmus_fee_schedule.py, the frozen fee object this cohort prices with.
FEE_SCHEDULE_FINGERPRINT = \
    "ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629"

#: analysis/l5/measurement/E1_COHORT_DESIGN.md, the design this implements.
DESIGN_DOC = "analysis/l5/measurement/E1_COHORT_DESIGN.md"


def _check_arithmetic() -> None:
    """The three given numbers that are arithmetic, recomputed. If a future
    edit moves one parameter without the others, import fails."""
    want_window = MAX_ATTEMPTS_PER_WINDOW * FIRST_LEG_QTY * MAX_FIRST_LEG_PRICE
    if want_window != MAX_EXPOSURE_PER_WINDOW_USD:
        raise SystemExit(
            "COHORT-E1-LIVE: MAX_EXPOSURE_PER_WINDOW_USD is not "
            "20 x 0.13 x 0.60: %s != %s"
            % (MAX_EXPOSURE_PER_WINDOW_USD, want_window))
    want_total = WINDOWS * want_window
    if want_total != ABSOLUTE_WORST_CASE_USD:
        raise SystemExit(
            "COHORT-E1-LIVE: ABSOLUTE_WORST_CASE_USD is not "
            "12 x 20 x 0.13 x 0.60: %s != %s"
            % (ABSOLUTE_WORST_CASE_USD, want_total))
    if MAX_EXPOSURE_PER_WINDOW_USD >= AUTHORIZED_WINDOW_CEILING_USD:
        raise SystemExit(
            "COHORT-E1-LIVE: the window bound must be strictly BELOW the "
            "owner's ceiling: %s >= %s"
            % (MAX_EXPOSURE_PER_WINDOW_USD, AUTHORIZED_WINDOW_CEILING_USD))
    if MAX_COHORT_REALIZED_LOSS_USD > ABSOLUTE_WORST_CASE_USD:
        raise SystemExit(
            "COHORT-E1-LIVE: the cohort loss stop must be TIGHTER than the "
            "worst case, not looser: %s > %s"
            % (MAX_COHORT_REALIZED_LOSS_USD, ABSOLUTE_WORST_CASE_USD))
    if MAX_FIRST_LEG_PRICE >= PAIR_COST_CEILING:
        raise SystemExit("COHORT-E1-LIVE: the first-leg ceiling must leave "
                         "room under the pair-cost ceiling")
    if FIRST_LEG_QTY < VENUE_MIN_QTY or FIRST_LEG_QTY % VENUE_TICK != 0:
        raise SystemExit("COHORT-E1-LIVE: quantity is off the venue grid")


_check_arithmetic()


DIFF: Dict[str, Any] = {
    "probe": PROBE,
    "spec_version": 6,
    "derived_from_probe": "LIVE-L5-EXEC-1-XR2W",
    "derived_from_fingerprint": XR2W_FINGERPRINT,
    "correction_count": 2,
    "correction": ("XR2W's single probe becomes a BOUNDED COHORT: many "
                   "first legs with complements across twelve windows, "
                   "under a bounded-count named-slot latch. The "
                   "serializer, the order mutation path, the "
                   "preview-and-echo requirement, the exception-typed "
                   "lifecycle and the deadman are inherited unchanged."),

    "experiment_class": "MECHANICS MEASUREMENT COHORT, not a profit probe",

    "authorization": {
        "state": "NOT_AUTHORIZED",
        "armed": False,
        "owner_values_set": False,
        "manifest": "analysis/l5/e1_cohort/E1_AUTHORIZATION_MANIFEST.md",
        "self_granted": False,
        "arming_is_the_owner_s_and_no_code_here_sets_it": True,
        "deployed": False,
        "railway_service": None,
    },

    "cohort_bounds": {
        "first_leg_qty_shares": str(FIRST_LEG_QTY),
        "max_first_leg_price_usd": str(MAX_FIRST_LEG_PRICE),
        "pair_cost_ceiling_usd": str(PAIR_COST_CEILING),
        "pair_cost_ceiling_is_fee_inclusive": True,
        "max_attempts_per_window": MAX_ATTEMPTS_PER_WINDOW,
        "max_exposure_per_window_usd": str(MAX_EXPOSURE_PER_WINDOW_USD),
        "authorized_window_ceiling_usd": str(AUTHORIZED_WINDOW_CEILING_USD),
        "windows": WINDOWS,
        "max_placements": MAX_PLACEMENTS,
        "absolute_worst_case_usd": str(ABSOLUTE_WORST_CASE_USD),
        "max_cohort_realized_loss_usd": str(MAX_COHORT_REALIZED_LOSS_USD),
        "max_outstanding_orders": MAX_OUTSTANDING_ORDERS,
        "parameters_were_handed_to_this_lane_frozen": True,
        "parameters_were_tuned_here": False,
        "provenance": {
            "FIRST_LEG_QTY": "E1_COHORT_DESIGN.md §1.4 cent-rounding floor",
            "MAX_FIRST_LEG_PRICE": "analysis/l5/stage6/bounds.py, inherited",
            "PAIR_COST_CEILING": "CLAUDE.md spring discipline, verbatim",
            "MAX_EXPOSURE_PER_WINDOW_USD": "20 x 0.13 x 0.60, arithmetic",
            "ABSOLUTE_WORST_CASE_USD": "12 x 20 x 0.13 x 0.60, arithmetic",
            "AUTHORIZED_WINDOW_CEILING_USD": "the owner's hard limit",
            "MAX_COHORT_REALIZED_LOSS_USD": "hard stop, tighter than worst",
        },
    },

    "rebates": {
        "maker_rebate_at_this_size_usd": "0.00",
        "structurally_zero": True,
        "why": ("the maker rebate is round_half_even(0.0125 * C * p * (1-p), "
                "$0.01). At C = 0.13 the raw rebate is at most "
                "0.0125 * 0.13 * 0.25 = $0.000406, which rounds to $0.00 at "
                "EVERY price. There is no signal, not a weak one."),
        "is_correct_and_deliberate": True,
        "cohort_that_measures_rebates": "E2",
        "sized_for_rebates": False,
        "rebates_are_recorded_from_the_venue_never_modelled": True,
    },

    "size": {
        "qty_shares": str(FIRST_LEG_QTY),
        "qty_is_the_venue_minimum": False,
        "distinguishable_non_zero_fill_states": 13,
        "worst_case_per_placement_usd": "0.0780",
        "chosen_for": "observability of all eight estimands",
        "chosen_for_expectancy": False,
    },

    "eligibility": {
        "one_market_at_a_time": True,
        "one_outstanding_order_at_a_time": True,
        "maker_first": True,
        "post_only_participate_dont_initiate": True,
        "rules": [
            "E1 the cohort must be ARMED by the owner's gate for this exact "
            "market slug, and the frozen fingerprint must be pinned to THIS "
            "build. Neither is set by any code in this package.",
            "E2 the cohort verdict must be CONTINUE: not PASS, not FAIL, "
            "not INCONCLUSIVE, not stopped.",
            "E3 no order of ours may be outstanding, and no residual "
            "position of ours may be open, in any market.",
            "E4 the window must be open, within the frozen handover TTE, "
            "and the attempt count for this window must be below 20.",
            "E5 the cohort loss stop must be unbreached: realized loss "
            "strictly below $5.00.",
            "E6 projected window exposure after this attempt must remain "
            "strictly below $1.56.",
            "E7 the live two-token websocket must be streaming BOTH tokens "
            "with a strict-timing book fresher than the frozen bound. REST "
            "is reachability only and may NEVER order the tape.",
            "E8 the book must be two-sided and executable on both tokens.",
            "E9 the first-leg price must be at the same-side best bid "
            "(offset 0), on the 0.01 tick, at most $0.60, and strictly "
            "below the live ask so a post-only order cannot take.",
            "E10 the COMPLEMENT must be feasible: the cheapest complement "
            "price that satisfies the fee-inclusive pair-cost ceiling must "
            "be at or above $0.01 and on the tick. An attempt whose "
            "complement could not be priced at all is refused BEFORE the "
            "first leg, never discovered afterwards.",
            "E11 a bounded-count named latch slot for this window must be "
            "claimable. The slot is consumed BEFORE the POST and is never "
            "released on failure.",
            "E12 no safety-invariant violation may be recorded.",
        ],
    },

    "complement": {
        "rules": [
            "C1 the complement is placed only after the first leg reports a "
            "fill, and is sized to the ACTUAL filled quantity, never to the "
            "placed quantity.",
            "C2 the complement is the opposite token of the same market.",
            "C3 the complement price is the highest price on the 0.01 tick "
            "that keeps the fee-inclusive combined pair cost at or below "
            "$0.96 per share, and that is also at or below the same-side "
            "best bid so the order rests as a maker.",
            "C4 if no such price exists, NO complement is placed and the "
            "position is recorded as RESIDUAL. The pair is refused, not "
            "improved by relaxing the ceiling.",
            "C5 maker only. NO TAKER RESCUE, at any price, at any TTE.",
            "C6 ONE complement submission per first-leg fill. NO CHASING "
            "and NO RE-QUOTE: a complement that does not fill is cancelled "
            "at handover and the residual is recorded, not re-priced.",
            "C7 a partial first-leg fill yields a complement sized to that "
            "partial. A later additional fill on the same first leg does "
            "NOT trigger a second complement; the shortfall is residual.",
            "C8 the complement is subject to the same latch, the same "
            "preview-and-echo requirement and the same deadman.",
        ],
        "taker_rescue": False,
        "chasing": False,
        "requote": False,
    },

    "pair_cost": {
        "ceiling_usd": str(PAIR_COST_CEILING),
        "fee_inclusive": True,
        "fee_schedule": "src/pmus_fee_schedule.py",
        "fee_schedule_fingerprint": FEE_SCHEDULE_FINGERPRINT,
        "taker_theta_effective_2026_09_17": "0.0695",
        "both_legs_are_post_only_so_no_taker_fee_is_expected": True,
        "a_pair_above_the_ceiling_is_REFUSED_not_discounted": True,
    },

    "timing_authority": {
        "authority": "websocket_strict",
        "module": "src/l5_clob_ws.py",
        "endpoint": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        "both_tokens_required": True,
        "live_verified": True,
        "live_verified_frames": [11438, 11433],
        "rest_role": "rest_same_second_reachability",
        "rest_may_order_the_tape": False,
        "ordering_key": "recv_monotonic_ns",
    },

    "latch": {
        "kind": "BOUNDED_COUNT_NAMED_SLOTS",
        "module": "analysis.l5.e1_cohort.latch",
        "slots_per_window": MAX_ATTEMPTS_PER_WINDOW,
        "mechanism": "O_CREAT|O_EXCL per NAMED slot file",
        "session_scoped": True,
        "consumed_before_the_post": True,
        "released_on_failure": False,
        "is_a_bare_counter": False,
        "why_not_a_counter": ("a counter is read-then-write, so two "
                              "processes can read the same value and race "
                              "two orders into one slot. A NAMED slot is "
                              "won by exactly one O_EXCL create."),
        "cleared_by_claude": False,
    },

    "stopping": {
        "module": "analysis.l5.e1_cohort.stopping",
        "boundaries_source": DESIGN_DOC,
        "boundaries_invented_here": False,
        "pass_fail_continue": True,
        "fail_triggers": [
            "35 consecutive no-fill placements (Beta(1,36) 97.5% upper "
            "bound 0.0947, below 0.10)",
            "the estimand-2 SPRT walk crossing H0 at -2.944439, triggered "
            "outright by 8 consecutive non-completions",
            "any fill whose venue timestamp is after a cancel "
            "acknowledgement (n = 1): cancellation does not bound exposure",
            "any cashflow without sufficient source identity (n = 1)",
        ],
        "hard_truncation_placements": 4310,
        "hard_automatic_stops": [
            "cohort realized loss at or above $5.00",
            "window exposure at or above $1.56",
            "any safety-invariant violation",
            "venue error storm",
            "ambiguous submission: NEVER retried, venue state reconciled",
        ],
        "abort_is_not_a_verdict": True,
    },

    "pass_reachability": {
        "pass_is_reachable_in_this_authorization": False,
        "authorized_placements": MAX_PLACEMENTS,
        "design_placement_gate": DESIGN_PLACEMENT_GATE,
        "design_binding_fill_gate": DESIGN_BINDING_FILL_GATE,
        "why": ("240 authorized placements is below the design's 385 "
                "placement gate and far below its 431 binding fill gate, "
                "which 240 placements cannot reach at ANY fill rate. The "
                "authorized cohort can therefore reach FAIL or "
                "INCONCLUSIVE but NOT PASS."),
        "at_p_fill_0_35_expected_fills": 84,
        "at_p_fill_0_20_expected_fills": 48,
        "sprt_expected_fills_under_h0": 81,
        "terminates_CONTINUE_explicitly_when_undecidable": True,
        "undecidable_verdict": "INCONCLUSIVE_FILL_RATE_TOO_LOW",
        "extending_the_cohort_requires_a_FRESH_owner_authorization": True,
        "anything_widened_quietly": False,
    },

    "recording": {
        "module": "analysis.l5.e1_cohort.recorder",
        "append_only": True,
        "provenance_on_every_row": True,
        "per_eligible_attempt": [
            "the exact live two-token websocket book state at decision time",
            "first-leg side, price, quantity",
            "queue/touch state: size resting AT our price and AHEAD of it",
            "submitted / acknowledged / resting timestamps",
            "first-leg maker fill / PARTIAL fill / no-fill, with filled_qty",
            "complement order price and submission timing",
            "complement maker fill / partial / no-fill",
            "COMPLETE vs RESIDUAL",
            "time to first fill and time to completion",
            "realized pair cost",
            "residual size and residual duration",
            "every cancellation, and whether a fill raced it",
            "adverse-selection markouts from the websocket tape",
            "actual venue fees and rebates, NEVER modelled",
            "settlement and realized cashflow when available",
        ],
        "nothing_labelled_realized_before_settlement_and_cash_confirmation":
            True,
        "modelled_values_are_labelled_MODELLED": True,
    },

    "serializer": {
        "module": "src.l5_order_body",
        "duplicated_here": False,
        "why": ("a duplicate serializer caused the A5 HTTP 400, and the "
                "repository records the same class of bug happening twice. "
                "A test asserts that no file in this package contains a "
                "duplicate body."),
    },

    "mutation_path": {
        "module": "analysis.l5.live_exec2.orders",
        "is_the_only_order_mutation_path": True,
        "submit_one_per_transport_instance": True,
        "why_a_transport_per_attempt": (
            "BoundedOrderTransport refuses a second POST /v1/orders in one "
            "instance. The cohort therefore constructs ONE transport per "
            "attempt, so the one-submission-per-transport guarantee is "
            "preserved and the cohort bound is carried by the named-slot "
            "latch instead of being relaxed."),
        "live_exec2_modified": False,
    },

    "does_not_touch": [
        "analysis/l5/stage6/ (S6P)",
        "analysis/l5/live_exec5/ (XR2W) and its fingerprint",
        "the running l5-book-shadow collector",
        "A6 (FILLED / settlement unresolved / NOT REALIZED)",
        "any Kalshi path",
    ],
}


def _apply(spec: Dict[str, Any], dotted: str, value: Any) -> None:
    """DEEP-COPIES the value. See the module docstring: the inherited helper
    aliased DIFF's nested objects into the built spec in five specs, and
    that bug is not inherited here."""
    parts = dotted.split(".")
    node = spec
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = copy.deepcopy(value)


def _refuse_build_time_collision() -> None:
    """Rec. G of the mutation audit: a DIFF carrying both "foo" and "foo.bar"
    would make _apply write into DIFF's own dict at import time."""
    for k in DIFF:
        prefix = k + "."
        for other in DIFF:
            if other.startswith(prefix):
                raise SystemExit(
                    "COHORT-E1-LIVE DIFF carries both %r and %r; the second "
                    "would write into the first at build time" % (k, other))


def build() -> Dict[str, Any]:
    _refuse_build_time_collision()
    spec = copy.deepcopy(XR2W_SPEC)
    for dotted, value in DIFF.items():
        _apply(spec, dotted, value)
    return spec


SPEC: Dict[str, Any] = build()


#: Asserted at import and at EVERY boot. Anything here that moves means the
#: build is not the one the manifest describes.
INVARIANTS: Tuple[Tuple[str, Any], ...] = (
    ("probe", PROBE),
    ("authorization.state", "NOT_AUTHORIZED"),
    ("authorization.armed", False),
    ("authorization.owner_values_set", False),
    ("authorization.self_granted", False),
    ("authorization.deployed", False),
    ("cohort_bounds.first_leg_qty_shares", "0.13"),
    ("cohort_bounds.max_first_leg_price_usd", "0.60"),
    ("cohort_bounds.pair_cost_ceiling_usd", "0.96"),
    ("cohort_bounds.max_attempts_per_window", 20),
    ("cohort_bounds.max_exposure_per_window_usd", "1.56"),
    ("cohort_bounds.authorized_window_ceiling_usd", "5.00"),
    ("cohort_bounds.windows", 12),
    ("cohort_bounds.max_placements", 240),
    ("cohort_bounds.absolute_worst_case_usd", "18.72"),
    ("cohort_bounds.max_cohort_realized_loss_usd", "5.00"),
    ("cohort_bounds.max_outstanding_orders", 1),
    ("cohort_bounds.parameters_were_tuned_here", False),
    ("rebates.structurally_zero", True),
    ("rebates.sized_for_rebates", False),
    ("rebates.rebates_are_recorded_from_the_venue_never_modelled", True),
    ("complement.taker_rescue", False),
    ("complement.chasing", False),
    ("complement.requote", False),
    ("pair_cost.ceiling_usd", "0.96"),
    ("pair_cost.fee_inclusive", True),
    ("pair_cost.fee_schedule_fingerprint", FEE_SCHEDULE_FINGERPRINT),
    ("pair_cost.a_pair_above_the_ceiling_is_REFUSED_not_discounted", True),
    ("timing_authority.authority", "websocket_strict"),
    ("timing_authority.both_tokens_required", True),
    ("timing_authority.rest_may_order_the_tape", False),
    ("latch.kind", "BOUNDED_COUNT_NAMED_SLOTS"),
    ("latch.slots_per_window", 20),
    ("latch.consumed_before_the_post", True),
    ("latch.released_on_failure", False),
    ("latch.is_a_bare_counter", False),
    ("stopping.boundaries_invented_here", False),
    ("stopping.hard_truncation_placements", 4310),
    ("pass_reachability.pass_is_reachable_in_this_authorization", False),
    ("pass_reachability.terminates_CONTINUE_explicitly_when_undecidable",
     True),
    ("pass_reachability.extending_the_cohort_requires_a_FRESH_owner_"
     "authorization", True),
    ("pass_reachability.anything_widened_quietly", False),
    ("serializer.module", "src.l5_order_body"),
    ("serializer.duplicated_here", False),
    ("mutation_path.module", "analysis.l5.live_exec2.orders"),
    ("mutation_path.live_exec2_modified", False),
    ("wire_serialization.serializer", "src.l5_order_body"),
    ("wire_serialization.quantity_is_a_json_number", True),
    ("wire_serialization.price_carries_currency_usd", True),
    ("wire_serialization.preview_takes_the_request_envelope", True),
    ("lifecycle_classification.determined_by", "EXCEPTION_TYPE"),
    ("recording.append_only", True),
    ("recording.nothing_labelled_realized_before_settlement_and_cash_"
     "confirmation", True),
)


def assert_envelope_preserved(spec: Dict[str, Any] = SPEC) -> None:
    bad: List[str] = []
    for dotted, want in INVARIANTS:
        try:
            got = _get(spec, dotted)
        except (KeyError, TypeError):
            bad.append("%s: MISSING" % dotted)
            continue
        if got != want:
            bad.append("%s: want %r, got %r" % (dotted, want, got))
    if bad:
        raise SystemExit("COHORT-E1-LIVE changed the experiment or its loss "
                         "envelope: " + "; ".join(bad))


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


#: HARD-CODED LITERAL on purpose. Computing it from SPEC at import would make
#: assert_frozen() vacuous: it would recompute the value it compares against
#: and pass for every possible edit.
E1_FINGERPRINT = \
    "790f1a6f0b34b4dcb23760d30258a068efb4999206f055ef53ac69db9fa880cb"


def assert_frozen(spec: Dict[str, Any] = SPEC) -> None:
    """Asserted at import and at every boot."""
    assert_xr2w_frozen()
    _check_arithmetic()
    assert_envelope_preserved(spec)
    got = fingerprint(spec)
    if got != E1_FINGERPRINT:
        raise SystemExit("COHORT-E1-LIVE is not the frozen spec: %s != %s"
                         % (got, E1_FINGERPRINT))


def diff_report() -> Dict[str, Any]:
    """Provenance. Values are DEEP-COPIED on the way out, so a caller who
    annotates the report cannot reach back into DIFF (Rec. F of the mutation
    audit)."""
    added: Dict[str, Any] = {}
    changed: Dict[str, Any] = {}
    for dotted, value in DIFF.items():
        try:
            before = _get(XR2W_SPEC, dotted)
        except (KeyError, TypeError):
            added[dotted] = copy.deepcopy(value)
            continue
        if before != value:
            changed[dotted] = {"xr2w": copy.deepcopy(before),
                               "e1": copy.deepcopy(value)}
    return {"from_probe": "LIVE-L5-EXEC-1-XR2W",
            "from_fingerprint": XR2W_FINGERPRINT,
            "to_probe": PROBE,
            "to_fingerprint": E1_FINGERPRINT,
            "keys_added": added, "keys_changed": changed, "keys_removed": [],
            "design": DESIGN_DOC,
            "fee_schedule_fingerprint": FEE_SCHEDULE_FINGERPRINT,
            "AUTHORIZATION": "NOT AUTHORIZED. NOT ARMED. NO OWNER VALUE SET."}


def boot_assertions() -> Dict[str, Any]:
    """Called at every boot, before anything else. Fails closed."""
    assert_frozen()
    return {"probe": PROBE, "fingerprint": E1_FINGERPRINT,
            "invariants_checked": len(INVARIANTS),
            "armed": False, "authorized": False,
            "LABEL": "DISARMED. NO ORDER MAY BE PLACED."}


#: ASSERTED AT IMPORT. Importing this module at all is a freeze check, so
#: nothing downstream can run against a spec that has moved.
assert_frozen()
