"""STAGE-6-S6P — the matched-pair probe spec. FROZEN. **NOT AUTHORIZED.**

    DISARMED. This spec describes a probe that has never been armed and
    whose arming variables are unset. Nothing in this package has placed an
    order. The owner reviews this frozen protocol before anything goes live.

LINEAGE. EXEC-1 -> EXEC-1-X -> XR -> XR2 -> XR2W -> **S6P**.
S6P is derived from XR2W by DEEP-COPYING `analysis.l5.live_exec5.spec.SPEC`
and applying a LITERAL DIFF, exactly as XR2W was derived from XR2 and XR2
from XR. The parent is neither modified nor re-derived: `assert_frozen()`
calls XR2W's own `assert_frozen()` first, so a change to the proven executor
breaks this build rather than being silently absorbed into it.

WHAT CHANGES, AND WHY IT IS A NEW PROBE RATHER THAN A PARAMETER CHANGE.
XR2W tests ONE THING: that a single post-only 0.01-share order serializes
correctly and crosses the wire. It is a WIRE test, and it succeeded as one.
It cannot test the thesis, because the thesis is not "an order can be sent".
The thesis is:

    passive first-leg fill -> fee-safe complement attempt -> partial-fill
    accounting -> bounded residual -> cancellation/deadman -> settlement
    reconciliation

Every link in that chain is absent from XR2W by construction: XR2W has one
leg, no complement, no pair economics, no partial-fill accounting beyond a
single 0.01-share all-or-nothing fill, and no residual larger than $0.0099.
Another one-leg run would produce another wire result and no evidence about
the thesis. S6P is therefore the SMALLEST build that can produce such
evidence, and it is deliberately not a larger one.

WHAT IS INHERITED UNCHANGED, AND IS RE-ASSERTED AT EVERY BOOT:
  * the proven serializer `src.l5_order_body` -- `build()`,
    `preview_envelope()`, `venue_price()`. No second serializer exists in
    this package; `grep` for a hand-rolled body here finds nothing. A
    duplicate serializer is what killed Attempt 5 and the repository
    records the same class of bug once before that.
  * the only order-mutation path, `analysis.l5.live_exec2.orders`, with its
    four permitted routes, `VenueHTTPError`, `SAFE_RESPONSE_HEADERS`,
    `REDACTED_REQUEST_HEADERS`, `PreviewRejected`
    (`crossed_order_boundary` False) and `SubmissionAmbiguous`
    (`crossed_order_boundary` True).
  * post-only (`participateDontInitiate`) on BOTH legs, tick 0.01,
    the venue 0.01 minimum quantity.
  * the TTE discipline: 900 s window, placement only while TTE > 120,
    checked at run start, pre-latch and pre-POST, with unknown TTE counting
    as past the boundary.
  * liquidity role from `execution.isAggressor` ONLY, never a bare
    `aggressor` field.
  * the durable `O_CREAT|O_EXCL` latch mechanism, session-scoped, consumed
    BEFORE the POST and never released on failure -- widened from one slot
    to two NAMED slots, which is the same guarantee stated twice rather
    than a looser one (see `pair_latch`).
  * the frozen PMUS fee schedule `src.pmus_fee_schedule`, fingerprint
    `ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629`.

WHAT THIS PROBE CANNOT ESTABLISH, stated in the spec itself so no reader of
a result can mistake its scope. See `CANNOT_ESTABLISH`.

NOTHING IS TUNED. No number in `bounds.py` was fitted to a replay, a grid or
a backtest. Each is an arithmetic worst case or a rule copied from CLAUDE.md
or from the proven spec, and each carries its justification in
`bounds.BOUND_PROVENANCE`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any, Dict, List

from analysis.l5.live_exec2.spec import _get
from analysis.l5.live_exec5.spec import SPEC as XR2W_SPEC
from analysis.l5.live_exec5.spec import XR2W_FINGERPRINT
from analysis.l5.live_exec5.spec import assert_frozen as assert_xr2w_frozen
from analysis.l5.stage6 import bounds as B
from analysis.l5.stage6 import pair_latch as PL
from src import pmus_fee_schedule as FS

PROBE = "STAGE-6-S6P"

#: S6P's OWN arming namespace. XR2W's PMUS_EXEC1_* keys cannot arm this and
#: these cannot arm XR2W. All four must be set, and the gate must name the
#: single frozen slug. NONE of them is set, by anyone, as of this commit.
ARM_KEYS = ("PMUS_S6P_LIVE", "PMUS_S6P_SESSION_ID", "PMUS_S6P_ARM",
            "PMUS_S6P_GATE_EVIDENCE")

#: the authorization pin, which must name THIS build's own fingerprint
FINGERPRINT_PIN_ENV = "PMUS_S6P_AUTHORIZED_FINGERPRINT"

#: the terminal classes, exhaustive. Every run ends in exactly one.
TERMINALS = (
    "DISARMED",                     # the owner has not armed it. Tonight's.
    "REFUSED_PREFLIGHT",            # a local gate said no; nothing sent
    "REFUSED_PAIR_COST",            # no fee-safe complement clears $0.96
    "NO_FIRST_LEG_FILL",            # first leg rested, never filled, cancelled
    "PAIR_COMPLETE",                # complement filled the whole first fill
    "PAIR_PARTIAL_RESIDUAL_BOUNDED",  # some matched, residual within bounds
    "RESIDUAL_BOUNDED_DEADMAN",      # TTE boundary reached; everything cancelled
    "PREVIEW_REJECTED",             # no network order boundary crossed
    "AMBIGUOUS_SUBMISSION",         # may have crossed; NEVER retried
    "UNCLASSIFIED",                 # phase unknown; never resolved by guess
)

#: honest scope limits, carried in the fingerprinted object
CANNOT_ESTABLISH = (
    "that the strategy is profitable. One pair in one market is one sample; "
    "it has no statistical power over expectancy, and a single favourable "
    "settlement would be indistinguishable from luck.",
    "that L5 would have acted the same way. S6P is an EXECUTION-MECHANISM "
    "test. Action-by-action fidelity against L5 is a separate comparison "
    "and this probe does not perform it.",
    "that the $480/day target is reachable. Nothing here measures capacity, "
    "frequency, or concurrency.",
    "that maker rebates contribute anything. At 1.00 share the rebate is "
    "structurally $0.00 on the venue's cent floor, so the probe is "
    "deliberately blind to rebate economics.",
    "that the residual has predictive skill. The probe bounds the residual; "
    "it does not and cannot evaluate it, and the repository's evidence does "
    "not support residual directional skill.",
    "that a complement WOULD have filled at a better price later. The "
    "complement is quoted once and not re-quoted, so the probe observes one "
    "patience path and not the distribution of them.",
    "that post-only guaranteed a maker role. Role is read from "
    "execution.isAggressor after the fact; an absent field leaves it "
    "UNRESOLVED and the probe reports UNRESOLVED rather than maker.",
    "anything at all about profitability if no leg fills. A run that ends "
    "NO_FIRST_LEG_FILL is a successful test of the cancellation path and "
    "evidence of nothing else.",
)

DIFF: Dict[str, Any] = {
    "probe": PROBE,
    "spec_version": 6,
    "derived_from_probe": "LIVE-L5-EXEC-1-XR2W",
    "derived_from_fingerprint": XR2W_FINGERPRINT,
    "correction_count": 0,
    "correction": ("NOT a correction to XR2W. XR2W is a correct WIRE test "
                   "and is unmodified. S6P is a new, larger experiment: the "
                   "smallest end-to-end test of the matched-pair thesis."),

    "authorization": {
        "armed": False,
        "arm_keys": list(ARM_KEYS),
        "arm_keys_set_at_freeze_time": False,
        "fingerprint_pin_env": FINGERPRINT_PIN_ENV,
        "owner_values_present_in_this_repository": False,
        "live_order_ever_placed_by_this_build": False,
        "railway_deployment_of_this_build": "NONE",
        "what_the_owner_would_be_authorizing": (
            "ONE post-only first leg of at most 1.00 share at a price at or "
            "below $0.60, in ONE named btc-updown-15m window, followed by "
            "AT MOST ONE post-only complement sized to the first leg's "
            "ACTUAL filled quantity at a price that keeps the fee-safe "
            "combined pair cost at or below $0.96; a cancel of whatever "
            "rests at TTE 120 s; and no repeat. Maximum arithmetic loss "
            "$0.60."),
    },

    "experiment": {
        "thesis_under_test": (
            "passive first-leg fill -> fee-safe complement attempt -> "
            "partial-fill accounting -> bounded residual -> "
            "cancellation/deadman -> settlement reconciliation"),
        "is_another_one_leg_test": False,
        "why_xr2w_cannot_test_it": (
            "XR2W has one leg, no complement, no pair economics, no "
            "partial-fill accounting beyond a single all-or-nothing "
            "0.01-share fill, and a residual bounded at $0.0099."),
        "legs": 2,
        "legs_are_sequential_not_concurrent": True,
        "markets": 1,
        "repeat_after_completion_or_crash": False,
        "parameter_tuning_performed": False,
        "fitted_to_any_backtest_or_grid": False,
    },

    "first_leg": {
        "liquidity": "MAKER_POST_ONLY",
        "participate_dont_initiate": True,
        "qty_shares": str(B.FIRST_LEG_QTY),
        "max_price_usd": str(B.MAX_FIRST_LEG_PRICE),
        "price_rule": ("the same-side best bid, offset 0, snapped to the "
                       "venue 0.01 tick -- inherited from XR2W unchanged"),
        "side_selection": "sha256(slug|session_id) parity, deterministic",
        "side_selection_is_a_forecast": False,
        "refuses_if_it_would_cross": True,
        "outstanding_orders_while_resting": 1,
    },

    "complement": {
        "liquidity": "MAKER_POST_ONLY",
        "participate_dont_initiate": True,
        "sized_from": "first_leg.filled_qty",
        "never_sized_from": "first_leg.intended_qty",
        "qty_snapped": "DOWN_TO_VENUE_MIN_QTY_GRID",
        "side": "the arithmetic complement of the first leg's side",
        "price_ceiling_rule": (
            "the highest venue-grid price whose fee-safe combined pair cost "
            "is at or below $%s, searched down the 0.01 grid rather than "
            "solved algebraically" % B.PAIR_COST_CEILING),
        "placed_only_after_the_first_leg_is_no_longer_outstanding": True,
        "patience": ("WAIT at one price until the TTE withdrawal boundary. "
                     "No arbitrary timer forces completion, and no re-quote, "
                     "re-price or second submission is authorized."),
        "forced_on_a_timer": False,
        "requote_authorized": False,
        "late_taker_behaviour_copied_from_mature_august_l5": False,
    },

    "pair_economics": {
        "ceiling_usd_per_contract": str(B.PAIR_COST_CEILING),
        "fee_basis": ("the TAKER coefficient on BOTH legs, under the "
                      "schedule in force at the fill's own timestamp, even "
                      "though both legs are post-only -- strictly "
                      "conservative"),
        "rebate_counted_in_the_gate": "ZERO",
        "rebate_structurally_zero_at_this_size": True,
        "near_par_tolerance": "LOCKED -- no near-$1 branch is implemented",
        "schedule_module": "src.pmus_fee_schedule",
        "schedule_fingerprint": FS.FINGERPRINT,
        "second_fee_table_in_this_package": False,
    },

    "partial_fill_accounting": {
        "inventory_updates_on_every_partial_fill": True,
        "updates_per_execution_not_per_poll_batch": True,
        "bounds_rechecked_after_every_single_fill": True,
        "executions_counted_exactly_once_by_id": True,
        "execution_without_an_id_is_refused_not_counted": True,
        "first_leg_cost_basis": "quantity_weighted_vwap",
        "complement_qty_reads_only_filled_qty": True,
        "liquidity_role_source": "execution.isAggressor",
        "bare_aggressor_field_used": False,
        "unresolved_role_promoted_to_maker": False,
    },

    "residual": {
        "max_shares": str(B.MAX_RESIDUAL_SHARES),
        "max_notional_usd": str(B.MAX_RESIDUAL_NOTIONAL_USD),
        "max_imbalance_shares": str(B.MAX_INVENTORY_IMBALANCE_SHARES),
        "predictive_skill_assumed": False,
        "flattened_on_a_timer": False,
        "carried_because": ("flattening on an arbitrary timer is the "
                            "behaviour CLAUDE.md forbids. The residual is "
                            "BOUNDED, not believed in."),
        "breach_behaviour": "FAIL CLOSED: stop, cancel, report",
    },

    "cancellation_and_deadman": {
        "withdraw_at_tte_s": B.TTE_CUTOFF_S,
        "unknown_tte_withdraws": True,
        "cancel_exact_by_order_id": True,
        "market_scoped_deadman_in_the_finally_block": True,
        "deadman_requires_arming": False,
        "deadman_requires_the_latch": False,
        "deadman_retried": False,
        "why_cancel_is_not_gated_like_submission": (
            "cancelling is how we stop risk; refusing to cancel because a "
            "flag is unset is the dangerous direction"),
        "resting_order_left_to_ride_to_settlement": False,
    },

    "latch": {
        "mechanism": "O_CREAT|O_EXCL",
        "module": "analysis.l5.stage6.pair_latch",
        "slots": list(PL.SLOTS),
        "slot_count": len(PL.SLOTS),
        "session_env": PL.SESSION_ENV,
        "dir": PL.LATCH_DIR,
        "consumed_before_the_post": True,
        "released_on_failure": False,
        "complement_slot_requires_a_spent_first_leg_slot": True,
        "capacity_counter_instead_of_named_slots": False,
        "why_named_slots": ("a capacity-2 counter would permit TWO first "
                            "legs, which is a different experiment"),
        "shares_a_latch_with_xr2w": False,
    },

    "terminal_classification": {
        "terminals": list(TERMINALS),
        "determined_by": "EXCEPTION_TYPE",
        "determined_by_reading_the_latch": False,
        "preview_failure_is": "PREVIEW_REJECTED",
        "preview_crossed_the_order_boundary": False,
        "submission_failure_is": "AMBIGUOUS_SUBMISSION",
        "submission_may_have_crossed_the_order_boundary": True,
        "unknown_phase_is": "UNCLASSIFIED",
        "unknown_phase_resolved_by_guess": False,
        "ambiguous_submission_retried": False,
        "ambiguous_submission_action": ("reconcile venue state by reading "
                                        "open orders and executions; never "
                                        "re-send"),
    },

    "settlement_reconciliation": {
        "realized_requires_settlement_cash_confirmation": True,
        "realized_requires_a_source_identity": True,
        "venue_realizedPnl_field_counts_as_realized": False,
        "balance_delta_without_source_identity_counts_as_realized": False,
        "mark_to_market_counts_as_realized": False,
        "estimated_rebate_counts_as_realized": False,
        "locked_pair_gross_counts_as_realized": False,
        "accounting_lines_reported_separately": 7,
        "blended_profit_figure_emitted": False,
        "module": "analysis.l5.stage6.settlement",
    },

    "wire_serialization": {
        "serializer": "src.l5_order_body",
        "hand_rolled_body_deleted": True,
        "second_serializer_in_this_package": False,
        "delegates_build": True,
        "delegates_preview_envelope": True,
        "price_carries_currency_usd": True,
        "quantity_is_a_json_number": True,
        "preview_takes_the_request_envelope": True,
        "no_side_price_field_is_invented_locally": True,
        "complement_mapping_unchanged": (
            "price.value is ALWAYS the long (YES) side's price. A NO order "
            "at NO price X goes on the wire as 1 - X. The complement leg is "
            "the first place this mapping is exercised on BOTH sides in one "
            "session, and it is exercised through the proven serializer, "
            "not re-derived."),
        "a5_root_cause_class": (
            "a duplicate serializer. This package contains none."),
    },

    "entrypoint": {
        "module": "analysis.l5.stage6.runner",
        "executes_the_real_runner": False,
        "is_preflight_only": True,
        "disarmed_behaviour": ("runs the whole decision path against the "
                               "venue adapter and stops at the DISARMED "
                               "refusal before any order mutation"),
        "call_graph_to_submit": [
            "analysis.l5.stage6.runner:run_probe",
            "analysis.l5.stage6.runner:_arming_gate       (DISARMED here)",
            "analysis.l5.stage6.machine:PairMachine.run",
            "  machine:_place_first_leg",
            "    analysis.l5.live_exec2.orders:submit_one(guard=...)",
            "      orders:order_body -> src.l5_order_body:build",
            "      orders:preview_request"
            " -> src.l5_order_body:preview_envelope",
            "      POST /v1/order/preview {'request': body} (creates nothing)",
            "      guard() -> stage6.pair_latch:consume('FIRST_LEG')"
            " -> guard()",
            "      POST /v1/orders",
            "  machine:_poll_fills -> stage6.inventory:apply_fill (per exec)",
            "  machine:_cancel_first_leg"
            " -> orders:cancel_exact",
            "  machine:_place_complement"
            " (qty from inventory.complement_qty())",
            "      guard() -> stage6.pair_latch:consume('COMPLEMENT')"
            " -> guard()",
            "  machine:_deadman -> orders:deadman_cancel_all",
            "analysis.l5.stage6.settlement:reconcile",
        ],
    },

    "bounds": B.summary(),
    "cannot_establish": list(CANNOT_ESTABLISH),

    "does_not_modify": {
        "live_exec5_spec_py": "UNMODIFIED",
        "xr2w_fingerprint": XR2W_FINGERPRINT,
        "live_exec2_orders_py": "IMPORTED, UNMODIFIED",
        "src_l5_order_body_py": "IMPORTED, UNMODIFIED",
        "src_pmus_fee_schedule_py": "IMPORTED, UNMODIFIED",
        "a6_s3_or_the_l5_book_shadow_collector": "UNTOUCHED",
    },
}


def _apply(spec: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = spec
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = value


def build() -> Dict[str, Any]:
    spec = copy.deepcopy(XR2W_SPEC)
    for dotted, value in DIFF.items():
        _apply(spec, dotted, value)
    return spec


SPEC: Dict[str, Any] = build()

#: the invariants that MUST hold, asserted at import and at every boot.
#: These are the claims a reader of a result is entitled to rely on.
S6P_INVARIANTS: tuple = (
    ("probe", PROBE),
    ("spec_version", 6),
    ("derived_from_fingerprint", XR2W_FINGERPRINT),
    # disarmed
    ("authorization.armed", False),
    ("authorization.arm_keys_set_at_freeze_time", False),
    ("authorization.owner_values_present_in_this_repository", False),
    ("authorization.live_order_ever_placed_by_this_build", False),
    ("authorization.railway_deployment_of_this_build", "NONE"),
    # the experiment
    ("experiment.is_another_one_leg_test", False),
    ("experiment.legs", 2),
    ("experiment.legs_are_sequential_not_concurrent", True),
    ("experiment.markets", 1),
    ("experiment.repeat_after_completion_or_crash", False),
    ("experiment.parameter_tuning_performed", False),
    ("experiment.fitted_to_any_backtest_or_grid", False),
    # maker-first, both legs
    ("first_leg.liquidity", "MAKER_POST_ONLY"),
    ("first_leg.participate_dont_initiate", True),
    ("first_leg.refuses_if_it_would_cross", True),
    ("first_leg.outstanding_orders_while_resting", 1),
    ("complement.liquidity", "MAKER_POST_ONLY"),
    ("complement.participate_dont_initiate", True),
    ("complement.sized_from", "first_leg.filled_qty"),
    ("complement.never_sized_from", "first_leg.intended_qty"),
    ("complement.forced_on_a_timer", False),
    ("complement.requote_authorized", False),
    ("complement.late_taker_behaviour_copied_from_mature_august_l5", False),
    ("complement."
     "placed_only_after_the_first_leg_is_no_longer_outstanding", True),
    # pair economics
    ("pair_economics.ceiling_usd_per_contract", "0.96"),
    ("pair_economics.rebate_counted_in_the_gate", "ZERO"),
    ("pair_economics.near_par_tolerance",
     "LOCKED -- no near-$1 branch is implemented"),
    ("pair_economics.schedule_fingerprint", FS.FINGERPRINT),
    ("pair_economics.second_fee_table_in_this_package", False),
    # partial fills
    ("partial_fill_accounting."
     "inventory_updates_on_every_partial_fill", True),
    ("partial_fill_accounting."
     "updates_per_execution_not_per_poll_batch", True),
    ("partial_fill_accounting."
     "bounds_rechecked_after_every_single_fill", True),
    ("partial_fill_accounting."
     "executions_counted_exactly_once_by_id", True),
    ("partial_fill_accounting."
     "execution_without_an_id_is_refused_not_counted", True),
    ("partial_fill_accounting.complement_qty_reads_only_filled_qty", True),
    ("partial_fill_accounting.liquidity_role_source",
     "execution.isAggressor"),
    ("partial_fill_accounting.bare_aggressor_field_used", False),
    ("partial_fill_accounting.unresolved_role_promoted_to_maker", False),
    # residual
    ("residual.max_notional_usd", "0.6000"),
    ("residual.max_imbalance_shares", "1.00"),
    ("residual.predictive_skill_assumed", False),
    ("residual.flattened_on_a_timer", False),
    ("residual.breach_behaviour", "FAIL CLOSED: stop, cancel, report"),
    # deadman
    ("cancellation_and_deadman.withdraw_at_tte_s", 120.0),
    ("cancellation_and_deadman.unknown_tte_withdraws", True),
    ("cancellation_and_deadman.deadman_requires_arming", False),
    ("cancellation_and_deadman.deadman_retried", False),
    ("cancellation_and_deadman."
     "resting_order_left_to_ride_to_settlement", False),
    # latch
    ("latch.mechanism", "O_CREAT|O_EXCL"),
    ("latch.slot_count", 2),
    ("latch.consumed_before_the_post", True),
    ("latch.released_on_failure", False),
    ("latch.complement_slot_requires_a_spent_first_leg_slot", True),
    ("latch.capacity_counter_instead_of_named_slots", False),
    ("latch.shares_a_latch_with_xr2w", False),
    # terminals
    ("terminal_classification.determined_by", "EXCEPTION_TYPE"),
    ("terminal_classification.determined_by_reading_the_latch", False),
    ("terminal_classification.preview_crossed_the_order_boundary", False),
    ("terminal_classification."
     "submission_may_have_crossed_the_order_boundary", True),
    ("terminal_classification.unknown_phase_resolved_by_guess", False),
    ("terminal_classification.ambiguous_submission_retried", False),
    # settlement
    ("settlement_reconciliation."
     "realized_requires_settlement_cash_confirmation", True),
    ("settlement_reconciliation."
     "venue_realizedPnl_field_counts_as_realized", False),
    ("settlement_reconciliation."
     "balance_delta_without_source_identity_counts_as_realized", False),
    ("settlement_reconciliation.mark_to_market_counts_as_realized", False),
    ("settlement_reconciliation.locked_pair_gross_counts_as_realized",
     False),
    ("settlement_reconciliation.blended_profit_figure_emitted", False),
    # the serializer
    ("wire_serialization.serializer", "src.l5_order_body"),
    ("wire_serialization.second_serializer_in_this_package", False),
    ("wire_serialization.delegates_build", True),
    ("wire_serialization.delegates_preview_envelope", True),
    ("wire_serialization.quantity_is_a_json_number", True),
    ("wire_serialization.price_carries_currency_usd", True),
    ("wire_serialization.preview_takes_the_request_envelope", True),
    # bounds, by value
    ("bounds.max_possible_loss_usd", "0.6000"),
    ("bounds.max_daily_realized_loss_usd", "0.6000"),
    ("bounds.pair_cost_ceiling_usd", "0.96"),
    ("bounds.first_leg_qty_shares", "1.00"),
    ("bounds.max_first_leg_price_usd", "0.60"),
    ("bounds.max_concurrent_markets", 1),
    ("bounds.max_outstanding_orders", 1),
    ("bounds.max_submissions_per_session", 2),
    ("bounds.max_first_legs", 1),
    ("bounds.max_complement_submissions", 1),
    ("bounds.tte_cutoff_s", 120.0),
    ("bounds.rebate_is_assumed_zero", True),
    # the proven parent is untouched
    ("does_not_modify.live_exec5_spec_py", "UNMODIFIED"),
    ("does_not_modify.xr2w_fingerprint", XR2W_FINGERPRINT),
)


def assert_envelope_preserved(spec: Dict[str, Any] = SPEC) -> None:
    bad: List[str] = []
    for dotted, want in S6P_INVARIANTS:
        try:
            got = _get(spec, dotted)
        except (KeyError, TypeError):
            bad.append("%s: MISSING, want %r" % (dotted, want))
            continue
        if got != want:
            bad.append("%s: want %r, got %r" % (dotted, want, got))
    if bad:
        raise SystemExit("STAGE-6-S6P changed the experiment or its loss "
                         "envelope: " + "; ".join(bad))


def canonical_json(spec: Dict[str, Any] = SPEC) -> str:
    return json.dumps(spec, sort_keys=True, separators=(",", ":"),
                      default=str)


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    return hashlib.sha256(canonical_json(spec).encode()).hexdigest()


#: HARD-CODED LITERAL so assert_frozen() cannot be vacuous. It is NOT
#: recomputed from the object it checks.
S6P_FINGERPRINT = \
    "6c7d2112ec600738bbc7ddb1eaaab8b4f8c99010dfc444404ae5ffc673e9f157"


def assert_frozen(spec: Dict[str, Any] = SPEC) -> None:
    """The parent, this build's invariants, and this build's identity."""
    assert_xr2w_frozen()        # and through it XR2, XR, EXEC-1-X, EXEC-1
    FS.assert_frozen()          # the fee schedule it prices pairs with
    B.assert_bounds_coherent()
    assert_envelope_preserved(spec)
    got = fingerprint(spec)
    if got != S6P_FINGERPRINT:
        raise SystemExit("STAGE-6-S6P is not the frozen spec: %s != %s. "
                         "Re-authorize rather than running."
                         % (got, S6P_FINGERPRINT))


def armed() -> bool:
    """S6P's own four keys. Isolated from every other executor's arming.

    Returns False in this repository as committed, because none of the four
    is set and no owner value appears anywhere in the tree.
    """
    sid = os.getenv("PMUS_S6P_SESSION_ID", "").strip()
    return (os.getenv("PMUS_S6P_LIVE", "").strip().upper() == "YES"
            and bool(sid)
            and os.getenv("PMUS_S6P_ARM", "").strip() == sid)


def may_submit(slug: str) -> Dict[str, Any]:
    gated = bool(slug) and os.getenv("PMUS_S6P_GATE_EVIDENCE",
                                     "").strip() == slug
    a = armed()
    pin = os.getenv(FINGERPRINT_PIN_ENV, "").strip()
    pinned = pin == S6P_FINGERPRINT
    return {
        "armed": a,
        "gate_asserted_for_slug": gated,
        "fingerprint_pinned_to_this_build": pinned,
        "may_submit": bool(a and gated and pinned),
        "refusal": (None if (a and gated and pinned) else
                    "DISARMED: the owner has not set the four PMUS_S6P_* "
                    "keys for this slug and pinned this build's "
                    "fingerprint."),
        "keys_present": {k: bool(os.getenv(k, "").strip())
                         for k in ARM_KEYS},
        "required_fingerprint": S6P_FINGERPRINT,
    }


def fingerprint_pinned() -> Dict[str, Any]:
    got = os.getenv(FINGERPRINT_PIN_ENV, "").strip()
    return {"env": FINGERPRINT_PIN_ENV, "set": bool(got),
            "matches_this_build": got == S6P_FINGERPRINT,
            "named": (got[:12] + "…") if got else None,
            "required": S6P_FINGERPRINT}


def diff_report() -> Dict[str, Any]:
    added, changed = {}, {}
    for dotted, value in DIFF.items():
        try:
            before = _get(XR2W_SPEC, dotted)
        except (KeyError, TypeError):
            added[dotted] = value
            continue
        if before != value:
            changed[dotted] = {"xr2w": before, "s6p": value}
    return {
        "from_probe": "LIVE-L5-EXEC-1-XR2W",
        "from_fingerprint": XR2W_FINGERPRINT,
        "to_probe": PROBE,
        "to_fingerprint": S6P_FINGERPRINT,
        "keys_added": added,
        "keys_changed": changed,
        "keys_removed": [],
        "parent_modified": False,
        "relationship": ("DERIVATION, not a correction. XR2W is a correct "
                         "wire test and is unmodified; S6P is a larger "
                         "experiment that reuses its primitives."),
        "max_possible_loss_usd_xr2w": "0.0099",
        "max_possible_loss_usd_s6p": str(B.MAX_POSSIBLE_LOSS_USD),
        "loss_envelope_widened": True,
        "loss_envelope_widening_is_deliberate": (
            "a matched-pair test cannot be run at 0.01 shares: the venue's "
            "0.01 quantity grid gives a 0.01-share leg no distinguishable "
            "partial-fill state, so the central thing under test would be "
            "unobservable. $0.0099 -> $0.60 is the smallest widening that "
            "makes partial fills observable."),
    }


assert_envelope_preserved()


if __name__ == "__main__":
    print(fingerprint())
