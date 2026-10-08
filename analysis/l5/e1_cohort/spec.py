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
ANY fill rate, so FORMAL E1 PASS IS UNREACHABLE. The owner accepted that
rather than widening the cohort, so there is no PASS outcome at all:
`outcome_taxonomy` admits exactly three, and no code path can label a
result PASS. Extending the cohort requires a FRESH owner authorization.

THE THREE AUTHORIZED OUTCOMES
-----------------------------
  1. FAIL                               a preregistered boundary is reached
  2. COMPLETION_SPRT_POSITIVE_CROSSING  the completion SPRT crosses H1 --
                                        evidence for the maker->maker
                                        completion question ONLY, NOT formal
                                        PASS, NOT proof of positive
                                        expectancy, NOT authorization for E2
  3. CONTINUE_INSUFFICIENT_EVIDENCE     anything else, with the truncation
                                        cause always named, so a risk
                                        truncation is distinguishable in the
                                        record from a low-fill-rate one

THE FORWARD-LOOKING RISK BOUND
------------------------------
`realized_cohort_loss + worst-case loss on ALL unresolved residual
inventory + worst-case loss of THE ATTEMPT ABOUT TO BE SUBMITTED <= $5.00`,
checked before every submission with the prospective attempt counted
first, so $5.00 is a true ceiling. `MAX_EXPOSURE_PER_WINDOW = $1.56`
remains an INDEPENDENT limit. The unrestricted $18.72 is unreachable.

WHAT MATTERS MOST
-----------------
The RAW TAPE. Whatever the classification, the append-only record must be
sufficient to recompute first-leg fill rate -> conditional maker->maker
completion rate -> residual rate/loss/duration -> pair cost -> actual
cashflows, afterwards and independently. A test reconstructs that whole
chain from a fixture, so sufficiency is known BEFORE money is spent.
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

#: A HARD AUTOMATIC STOP, tighter than the worst case. Since the owner's
#: risk-control change it is enforced FORWARD-LOOKING rather than on
#: realized loss alone -- see `analysis.l5.e1_cohort.stopping`.
MAX_COHORT_REALIZED_LOSS_USD = D("5.00")

#: ARITHMETIC: 0.13 x 0.60. The maximum loss of ONE attempt, and the third
#: term of the forward-looking invariant.
PER_ATTEMPT_MAX_LOSS_USD = D("0.078")

#: The most 0.01 increments a 0.13 first leg can arrive in, so the bound on
#: complement tranches comes from the VENUE GRID and not from a judgement.
#: A tranche exists only because new first-leg inventory arrived; it is not
#: a re-quote. See `analysis.l5.e1_cohort.runner.complement_tranche`.
MAX_COMPLEMENT_TRANCHES = 13

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
    if PER_ATTEMPT_MAX_LOSS_USD != FIRST_LEG_QTY * MAX_FIRST_LEG_PRICE:
        raise SystemExit(
            "COHORT-E1-LIVE: PER_ATTEMPT_MAX_LOSS_USD is not 0.13 x 0.60: "
            "%s != %s" % (PER_ATTEMPT_MAX_LOSS_USD,
                          FIRST_LEG_QTY * MAX_FIRST_LEG_PRICE))
    if MAX_COMPLEMENT_TRANCHES != int(FIRST_LEG_QTY / VENUE_TICK):
        raise SystemExit(
            "COHORT-E1-LIVE: MAX_COMPLEMENT_TRANCHES is not 0.13 / 0.01: "
            "%s != %s" % (MAX_COMPLEMENT_TRANCHES,
                          int(FIRST_LEG_QTY / VENUE_TICK)))
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

    "live_provider": {
        "module": "analysis.l5.e1_cohort.pmus_provider",
        "class": "PmusWindowProvider",
        "venue": "polymarket_us",
        "armed_main_reaches_run_cohort": True,
        "armed_main_is_a_bare_raise": False,
        "reuses_not_rebuilds": [
            "src.l5_pmus_transport (canonical PMUS slug + resolve_market)",
            "src.l5_pmus_auth_book (the proven PMUS authenticated book, "
            "used UNMODIFIED -- no websocket core is vendored)",
        ],
        "built_another_discovery_stack": False,
        "built_another_book_stack": False,
        "series_pinned": "cpc-btc-updown-15m",
        "market_identity": "slug_plus_outcome_side",
        "ctf_token_ids_are_pmus_identity": False,
        "per_window_market_resolution": True,
        "market_is_pinned_before_an_order_is_possible": True,
        "windows_sequenced": WINDOWS,
        "windows_are_distinct": True,
        "an_already_consumed_window_start_can_be_reopened": False,
        "a_window_whose_end_has_passed_can_be_opened": False,
        "degraded_snapshot_is_ever_returned": False,
        "fails_closed_on": [
            "selector refusal (unidentified window)",
            "authorization refusal",
            "the PMUS book not proven usable for the SELECTED slug",
            "stale-window rollover",
            "a window whose end has already passed",
            "book loss before submission",
            "slug or outcome-side mismatch",
            "book for another market",
            "a timing authority that is not the PMUS authenticated book",
            "an absent, zero, non-finite or crossed executable price",
        ],
        "timing_authority": "pmus_authenticated_ws_strict",
        "rest_is_sufficient_timing_authority": False,
        "rest_role": "discovery_and_cross_check_only",
        "authbook_failure_may_fall_back_to_rest": False,
        # ON PMUS THERE IS ONE QUOTED BOOK PER MARKET, and the NO side is
        # its arithmetic complement (NO bid = 1 - YES ask). That is the
        # venue's own convention, not a shortcut: there is no second feed
        # to subscribe to. The Polymarket.com-era invariant asserted the
        # opposite because there WERE two token books there.
        "derived_complement_is_a_second_feed": False,
        "derived_complement_is_the_venue_convention": True,
        "defect_this_replaces": (
            "E1 discovered its market on gamma-api.polymarket.com, pinned "
            "two CTF token ids and streamed Polymarket.com's public CLOB "
            "websocket, then serialized that identity into an order to "
            "api.polymarket.us. PMUS answered 'Market not found' five "
            "times. Polymarket.com and Polymarket US are DIFFERENT "
            "VENUES: PMUS names a market "
            "cpc-btc-updown-15m-YYYY-MM-DD-HHMMz plus an outcomeSide, and "
            "has no CTF token ids at all. Proven by authenticated preview "
            "on 2026-10-07: the PMUS-native slug was ACCEPTED (HTTP 200, "
            "ORDER_STATE_PENDING_NEW) where the Polymarket.com slug was "
            "not recognized."),
    },

    "decision_feed": {
        "module": "analysis.l5.e1_cohort.pmus_book",
        "class": "PmusDecisionFeed",
        "collector": "src.l5_pmus_auth_book",
        "collector_is_vendored_or_reimplemented": False,
        "venue": "polymarket_us",
        "transport": "PMUS authenticated websocket",
        "identity": "slug_plus_outcome_side",
        "ctf_token_ids_used": False,
        # ONE QUOTED BOOK PER MARKET. AuthBook returns a snapshot only
        # when it is connected AND the book is two-sided, so "no snapshot"
        # is itself the fail-closed answer.
        "a_snapshot_requires_a_connected_two_sided_book": True,
        # THE PROVEN BOUND. XR2W Attempt 6 -- the only order path that
        # has produced an evidenced fill -- read AuthBook two-sided at
        # age 0.084s against a 2.0s bound and the venue filled the
        # resulting maker order. E1 carried 5.0s, looser than the bound
        # that has actually filled, on no evidence.
        "max_book_age_s": 2.0,
        "max_book_age_provenance": (
            "analysis/l5/live_exec5/XR2W_ATTEMPT_6.md: AuthBook "
            "two-sided at age 0.084s against the 2.0s bound; DOWN priced "
            "at own-side touch 0.6700 = 1 - 0.3300; wire price.value "
            "0.3300; ORDER_STATE_NEW 02:00:01Z -> ORDER_STATE_FILLED "
            "02:00:15.69Z, cumQuantity 0.01"),
        # THE PROVEN EXECUTION FEED IS AUTHBOOK, not either REST route.
        # The gateway book is a ~30-second cache, measured: direction
        # inference against it held 46.2% forward / 35.3% inverted over
        # 184 rows, 18.5% inside the spread.
        "execution_feed_proven_by_a_fill": "src.l5_pmus_auth_book.AuthBook",
        "rest_gateway_book_is_a_cache": True,
        "rest_gateway_book_may_price_an_order": False,
        "staleness_is_the_authenticated_stream_age": True,
        "staleness_depends_on_silence_alone": False,
        "source_must_be_the_authenticated_ws": True,
        "a_rest_sourced_snapshot_is_refused": True,
        # ======== BBO SEMANTICS, SETTLED FROM VENUE DATA ==============
        # Capture: cpc-btc-updown-15m-2026-10-07-2215z at 2026-10-07
        # T22:29:48Z, deployment dd7583fb, read-only. Fixture:
        # analysis/l5/e1_cohort/fixtures/pmus_bbo_2026-10-07T2229Z.json
        #
        # THE AUTHENTICATED WEBSOCKET CARRIES NO BEST-BID FIELD. Every
        # book frame's keys are exactly ["bids","marketSlug","offers",
        # "state","stats","transactTime"], with bestBid and bestAsk both
        # null. `bestBid` exists ONLY on REST /bbo. So E1's first-leg
        # price always comes from the LADDER TOP, and the ladder's order
        # is the only thing it rests on.
        "ws_carries_a_best_bid_field": False,
        "price_comes_from_the_ladder_top": True,
        "ladder_is_ordered_best_first": True,
        "ladder_ordering_is_assumed_rather_than_measured": False,
        "ladder_ordering_confirmed_by": (
            "three independent readings of one slug agreeing on 0.9900: "
            "REST /bbo marketData.bestBid.value, REST /book "
            "marketData.bids[0].px.value across 29 strictly descending "
            "levels, and the authenticated websocket's own first frame"),
        "a_nested_object_can_replace_the_top_of_book": False,
        "yes_buy_maker_price": "the YES best bid, wire value unchanged",
        "no_buy_maker_price": (
            "1 - YES best ask (the NO best bid); the wire value is "
            "1 - that = the YES best ask, because price.value is ALWAYS "
            "the long side's price"),
        "no_side_priced_in_no_terms_on_the_wire": False,
        "complement_is_the_arithmetic_complement_of_the_same_book": True,
        "complement_convention": "NO bid = 1 - YES ask; NO ask = 1 - YES bid",
        "complement_is_a_second_feed": False,
        "size_is_invented_when_absent": False,
        "unknown_size_is_reported_with_its_reason": True,
        "a_book_for_another_slug_is_refused": True,
        "stall_and_resubscribe_semantics_are_authbooks_own": True,
        "defect_this_replaces": (
            "the Polymarket.com-era book parsed `price_change` frames from "
            "the public CLOB websocket for two CTF token ids. Those frames "
            "describe a venue E1 does not trade on, so a healthy, fresh, "
            "correctly parsed book was still the WRONG MARKET'S book. The "
            "parser defect it fixed (11,438 of 11,523 frames dropped, "
            "reporting HEALTHY while serving a frozen top of book) is "
            "preserved as history in the Polymarket.com-era tapes."),
        "tested": "analysis/l5/e1_cohort/test_e1_pmus_feed_and_selector.py",
    },

    "cohort_authorization": {
        "module": "analysis.l5.e1_cohort.authorization",
        "problem": (
            "a single static PMUS_E1_GATE_EVIDENCE=<slug> cannot authorize "
            "a 12-window rolling cohort: twelve slugs, one env value."),
        "wrong_fix_not_taken": (
            "deleting the slug check would make this spec state a rule the "
            "code does not enforce, which is the defect class that "
            "superseded three fingerprints"),
        "boundary_1_session": (
            "PMUS_E1_GATE_EVIDENCE names the authorized SERIES. Only "
            "markets the FROZEN BTC15m SELECTOR produced during this "
            "authorized session may be traded."),
        "boundary_2_per_submission": (
            "the SPECIFIC RESOLVED PMUS SLUG and BOTH OUTCOME SIDES are "
            "enforced immediately before EVERY first-leg submission, "
            "before any transport exists. Token ids are NOT checked here, "
            "because a PMUS order carries none: checking one would be "
            "checking a Polymarket.com fact about a Polymarket US order, "
            "which is the defect that produced 'Market not found'."),
        "slug_check_deleted": False,
        "is_stricter_than_one_static_slug": True,
        "session_ledger_is_append_only": True,
        "a_slug_is_never_removed": False,
        "complement_may_select_its_own_market": False,
        "complement_inherits_the_first_leg_authorization": True,
        "recorded_per_window_in_the_raw_tape": [
            "selected PMUS slug", "market id", "both outcome sides",
            "selection provenance", "authorization result",
            "the pmus book health that authorized the window",
        ],
    },

    "durable_progress": {
        "module": "analysis.l5.e1_cohort.progress",
        "state_dir": "/state/pmus-ws/e1_cohort",
        "append_only": True,
        "a_restart_resumes": True,
        "a_restart_resets_counters": False,
        "a_restart_grants_another_12_windows": False,
        "rehydrates": ["windows used", "placements", "window exposure",
                       "realized loss", "unresolved residuals"],
        "reset_path_exists": False,
        "fails_closed_if_unwritable": True,
        "why": ("counters in memory bound a PROCESS, not a COHORT. A "
                "container that restarted twelve times would otherwise get "
                "twelve fresh cohorts with every counter reading zero."),
    },

    # ----------------------------------------------------------------------
    # THE ENTRYPOINT. This key MUST be in the DIFF. Inheriting it from XR2W
    # was a DEFECT: the deep-copy fixed the aliasing and then faithfully
    # copied a value that needed to change, so the spec still named
    # `scripts.l5_exec1xr2w_run` and a call graph through
    # `live_exec3.runner:run_experiment` and XR2W's SINGLE-SLOT latch.
    # Arming "per the spec" would have launched XR2W's ONE-ORDER probe and
    # measured nothing. Same class as the old fingerprint-pin bug: a shared
    # value that describes the parent, silently inherited by the child.
    # ----------------------------------------------------------------------
    "entrypoint": {
        "module": "scripts.l5_e1_cohort_run",
        "callable": "analysis.l5.e1_cohort.runner:main",
        "cohort_driver": "analysis.l5.e1_cohort.runner:run_cohort",
        "executes_the_real_runner": True,
        "is_preflight_only": False,
        "disarmed_behaviour": (
            "boots, asserts the frozen spec, prints the preflight and the "
            "DISARMED refusal, and returns 0 WITHOUT requesting a "
            "snapshot, claiming a latch slot or reaching an order "
            "endpoint"),
        "call_graph_to_submit": [
            "scripts.l5_e1_cohort_run:__main__",
            "analysis.l5.e1_cohort.runner:main",
            "analysis.l5.e1_cohort.runner:boot -> spec:assert_frozen",
            "analysis.l5.e1_cohort.runner:main [ARMED BRANCH]",
            "  arming:fingerprint_pinned  (PMUS_E1_AUTHORIZED_FINGERPRINT)",
            "  authorization:series_authorized  (PMUS_E1_GATE_EVIDENCE)",
            "  progress:CohortProgress -> :rehydrate  (RESTART RESUMES)",
            "  authorization:SessionAuthorization  (session ledger)",
            "  pmus_provider:PmusWindowProvider",
            "    pmus_selector:select_window"
            " -> pmus_identity:slug_for + :resolve"
            " -> src.l5_pmus_transport:resolve_market",
            "    authorization:authorize_selected   (BOUNDARY 1, series)",
            "    pmus_provider:await_pmus_book_ready",
            "    pmus_book:PmusDecisionFeed.health/.book"
            " -> src.l5_pmus_auth_book:AuthBook",
            "analysis.l5.e1_cohort.runner:run_cohort   (MANY attempts)",
            "analysis.l5.e1_cohort.runner:e1_preflight -> :eligible",
            "analysis.l5.e1_cohort.runner:submit_first_leg",
            "  authorization:enforce_before_submission"
            "   (BOUNDARY 2, EXACT PMUS SLUG + BOTH OUTCOME SIDES, "
            "before transport)",
            "analysis.l5.e1_cohort.runner:_submit_once",
            "  orders:order_body -> src.l5_order_body:build",
            "  orders:preview_request -> src.l5_order_body:preview_envelope",
            "  POST /v1/order/preview {'request': body}  (creates nothing)",
            "  src.l5_preview_gate:require_echo",
            "  analysis.l5.e1_cohort.latch:consume_attempt"
            "   (E1 BOUNDED NAMED-SLOT latch)",
            "analysis.l5.live_exec2.orders:BoundedOrderTransport.request"
            " -> POST /v1/orders",
            "analysis.l5.e1_cohort.runner:submit_complement",
            "  authorization:authorize_complement"
            "   (inherits the first leg's market; cannot select one)",
            "  analysis.l5.e1_cohort.latch:consume_complement"
            "   (named TRANCHE slot)",
        ],
        "reaches_e1_cohort_runner": True,
        "reaches_e1_bounded_named_slot_latch": True,
        "reaches_proven_xr2w_order_primitives": True,
        "MUST_NOT_RESOLVE_TO": [
            "scripts.l5_exec1xr2w_run",
            "analysis.l5.live_exec3.runner:run_experiment",
            "analysis.l5.live_exec2.latch:consume",
            "analysis.l5.live_exec2.orders:submit_one",
        ],
        "enters_xr2w_experiment_runner": False,
        "enters_xr2w_single_slot_latch": False,
        "inherited_from_the_parent": False,
        "why_this_key_must_be_in_the_DIFF": (
            "the parent's entrypoint describes the parent's experiment. A "
            "child that inherits it declares an execution path that runs "
            "somebody else's probe, and no amount of testing the child's "
            "own functions will notice -- which is exactly what happened."),
    },

    "authorization": {
        "state": "NOT_AUTHORIZED",
        "armed": False,
        "owner_values_set": False,
        "manifest": "analysis/l5/e1_cohort/E1_AUTHORIZATION_MANIFEST.md",
        "self_granted": False,
        "arming_is_the_owner_s_and_no_code_here_sets_it": True,
        "namespace": "PMUS_E1_*",
        "keys": ["PMUS_E1_LIVE", "PMUS_E1_SESSION_ID", "PMUS_E1_ARM",
                 "PMUS_E1_GATE_EVIDENCE"],
        "fingerprint_pin_env": "PMUS_E1_AUTHORIZED_FINGERPRINT",
        "reads_pmus_exec1_namespace": False,
        "why_a_dedicated_namespace": (
            "the service l5-live-probe-oneshot already carries "
            "PMUS_EXEC1_* for XR2W, so sharing the namespace would let a "
            "legacy value participate in arming E1. Two experiments, two "
            "namespaces."),
        "module": "analysis.l5.e1_cohort.arming",
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

    "forward_looking_loss_bound": {
        "evaluated_at": ["EVERY_DECISION", "EVERY_SUBMISSION"],
        "invariant": (
            "realized_cohort_loss + maximum_remaining_loss_on_ALL_"
            "currently_unresolved_residual_inventory + maximum_loss_of_THE_"
            "ATTEMPT_ABOUT_TO_BE_SUBMITTED <= 5.00"),
        "ceiling_usd": str(MAX_COHORT_REALIZED_LOSS_USD),
        "realized_only": False,
        "why_not_realized_only": (
            "a realized-only stop fires after the money is gone. This one "
            "refuses the submission that COULD cross the ceiling."),
        "prospective_attempt_is_counted_before_submitting": True,
        "is_a_true_ceiling_not_a_ceiling_plus_one_attempt": True,
        "per_attempt_max_loss_usd": str(PER_ATTEMPT_MAX_LOSS_USD),
        "per_attempt_arithmetic": "0.13 x 0.60 = 0.078",
        "residual_max_remaining_loss_rule": (
            "filled_qty x fill_price -- the worst case, which is that the "
            "residual settles worthless"),
        "residual_is_unresolved_until": "CONFIRMED_SETTLEMENT_CASHFLOW",
        "may_reduce_the_realized_component": [
            "a settled win whose settlement/cashflow is CONFIRMED",
        ],
        "may_never_reduce_the_bound": [
            "mark-to-market value",
            "expected or projected settlement",
            "an unfilled or resting complement",
            "pending, accrued or estimated rebates",
        ],
        "forbidden_reducers_are_explicit_refusals_not_conventions": True,
        "per_window_bound_usd": str(MAX_EXPOSURE_PER_WINDOW_USD),
        "per_window_bound_binds_independently": True,
        "both_bounds_bind_simultaneously": True,
        "neither_bound_substitutes_for_the_other": True,
        "unrestricted_worst_case_usd": str(ABSOLUTE_WORST_CASE_USD),
        "unrestricted_worst_case_is_reachable": False,
        "effective_worst_case_usd": str(MAX_COHORT_REALIZED_LOSS_USD),
        "module": "analysis.l5.e1_cohort.stopping",
    },

    "network": {
        "network_capable": True,
        "claims_to_be_network_incapable": False,
        "why_capable": (
            "E1's job is to place real orders, and XR2W's proven "
            "analysis/l5/live_exec2/orders.py uses urllib. Importing the "
            "production entrypoint therefore pulls http, http.client, "
            "socket, ssl, urllib, urllib.error, urllib.parse, "
            "urllib.request and urllib.response. That is NECESSARY and "
            "CORRECT."),
        "tests_make_zero_network_calls": True,
        "transport_reachable_only_through": [
            "the preview-echo gate",
            "the E1 named-slot latch",
        ],
        "HONESTY": (
            "a false safety claim is worse than an acknowledged "
            "capability. Nothing in this package claims E1 cannot reach "
            "the network; the provable claims are that the TESTS make zero "
            "network CALLS and that the transport is gated."),
    },

    "venue_error_stop": {
        "consecutive_errors": 5,
        "is_a_safety_stop": True,
        "is_an_economic_threshold": False,
        "frozen_for_this_cohort": True,
        "exact_errors_are_recorded": True,
        "automatic_retry": False,
        "why_no_retry": ("an automatic retry could duplicate an ambiguous "
                         "submission, which is the one thing that must "
                         "never happen. The venue state is reconciled "
                         "instead."),
    },

    # ===================== PACED OBSERVATION =============================
    # On 2026-10-08 the fresh cohort armed, opened
    # cpc-btc-updown-15m-2026-10-08-0100z, and spent all 960 of its
    # iterations in about ONE SECOND. Every refusal was
    # E9_PRICE_ABOVE_CEILING:0.62 -- the best bid was 0.62 against the
    # frozen $0.60 ceiling, so the bound did its job and nothing was ever
    # priced -- but the loop did not WAIT. It consumed one of twelve
    # authorized windows, placed nothing, and exited.
    "paced_observation": {
        "module": "analysis.l5.e1_cohort.pacing",
        "pace_s": 1.0,
        "min_pace_s": 0.05,
        "no_paced_wait_is_ever_zero": True,
        "max_consecutive_empty_snapshots": 30,
        # THE TWO DEADLINES, and they are not the same instant.
        "deadline_aware": True,
        "never_sleeps_past_the_trading_cutoff": True,
        "trading_cutoff_is_the_handover_tte": True,
        "waits_out_the_window_after_the_cutoff_so_it_rolls_over": True,
        "snapshot_carries_trading_cutoff_unix": True,
        "snapshot_carries_window_end_unix": True,
        # FAIL CLOSED: only an explicit allowlist is paced.
        "classification_is_an_allowlist": True,
        "an_unrecognised_refusal_is_terminal": True,
        "paced_are_statements_about_a_book_that_moves": [
            "E3_OUR_ORDER_IS_OUTSTANDING", "E3_OUR_RESIDUAL_IS_OPEN",
            "E4_WINDOW_NOT_OPEN", "E4_PAST_HANDOVER_TTE",
            "E7_*", "E8_*", "E9_*", "E10_*",
            "E11_WINDOW_LATCH_EXHAUSTED",
            "PROJECTED_WINDOW_EXPOSURE_*",
            "WINDOW_<n>_ATTEMPT_<k>_REFUSED_MAX_20"],
        "terminal_are_safety": [
            "E1_DISARMED", "E1_FINGERPRINT_NOT_PINNED_TO_THIS_BUILD",
            "E12_SAFETY_INVARIANT_VIOLATION", "QTY_*",
            "E11_LATCH_UNAVAILABLE", "COHORT_PLACEMENT_CAP_240",
            "FORWARD_LOOKING_LOSS_BOUND", "VERDICT_IS_*",
            "E4_WINDOW_OUT_OF_RANGE"],
        "ambiguous_submission_is_still_never_retried": True,
        "a_paced_refusal_is_not_a_venue_error": True,
        # AN INELIGIBLE MARKET COSTS NOTHING. may_place is only ASKED; the
        # attempt counter and the latch slot are consumed inside
        # submit_first_leg, which a refusal never reaches.
        "consumes_no_attempt": True,
        "consumes_no_latch_slot": True,
        "consumes_no_risk_budget": True,
        # THE CAP IS A NET, NOT A BUDGET.
        "iteration_cap": 11760,
        "iteration_cap_formula": ("windows * window_len_s / "
                                 "PRODUCTION_PACE_S + max_placements * 4"),
        "old_iteration_cap": 960,
        "old_cap_was_smaller_than_one_paced_cohort": True,
        "iteration_cap_is_derived_from_the_production_pace_not_the_test_one":
            True,
        "reaching_the_cap_is_an_anomaly_not_a_normal_ending": True,
        "changes_no_bound": True,
        "bounds_unchanged": {
            "first_leg_qty_shares": "0.13",
            "max_first_leg_price_usd": "0.60",
            "pair_cost_ceiling_usd": "0.96",
            "max_attempts_per_window": 20,
            "max_exposure_per_window_usd": "1.56",
            "max_cohort_realized_loss_usd": "5.00",
            "windows": 12},
        "provenance": ("analysis/l5/e1_cohort/E1_LIVE_COHORT_LAUNCH.md: "
                       "window cpc-btc-updown-15m-2026-10-08-0100z, "
                       "iterations 960 == the old cap, every refusal "
                       "E9_PRICE_ABOVE_CEILING:0.62, placements 0, "
                       "windows_used 1 of 12"),
        "patience_precedent": ("XR2W Attempt 6, the only evidenced fill "
                              "from our own order path, paced itself: "
                              "1,230 polls over 6,150.1 s"),
    },

    "supersedes_e1": {
        "reason": ("the complement accounting was CORRECTED: quantity now "
                   "tracks the CUMULATIVE first-leg fill rather than only "
                   "the first partial, so a 0.13 first leg filling 0.05 "
                   "then 0.08 accounts for the whole 0.13 instead of "
                   "silently leaving an 0.08 residual."),
        "must_not_be_rearmed": [
            "110a4fc49cc936431aa1d099623128ceb77cd91fc7625e6518f2ede4f01ba99c",
            "bfcff9ca9c4f27d73b2146876fcb62b8090ac0941fecaae8205032e76d07006a",
            "f6b4a89ffbd801deefb8bdadacc7d127b507c928cc188e6b506a1bbbbb65f8b4",
            "e11fc1abb9f5d0a4fa2ee95aff14ead8246edfc9bf08f88372bea5e60ce44310",
            "cdc635190765732e478169e172d2a638f6fed31468379fbf3e3fdc4e3930ae60",
            "481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486",
            "9db0d2a77a88a16415c69a84f385af39a272f04aff4e7b3fd554dd1baaded920",
            "9de41c1762342557668cdcdf6b57538122d5fb2e50d7546d57c22e0b37c759d5",
            "b32e8c7bbc4d496bbf05c633f151892ad74a8bf5b34a5750fc70715520881cf1",
        ],
        "reasons": {
            "b32e8c7bbc4d496bbf05c633f151892ad74a8bf5b34a5750fc70715520881cf1":
                ("its decision loop had NO PACING: a refusal was a bare "
                 "`continue`, so a window whose book sat above the $0.60 "
                 "ceiling got 960 refusals in about one second instead of "
                 "fifteen minutes of patience. It armed on 2026-10-08, "
                 "opened cpc-btc-updown-15m-2026-10-08-0100z, refused "
                 "every iteration with E9_PRICE_ABOVE_CEILING:0.62, "
                 "placed nothing, consumed one of twelve authorized "
                 "windows and exited. The cap, not the market, ended it"),
            "110a4fc49cc936431aa1d099623128ceb77cd91fc7625e6518f2ede4f01ba99c":
                ("the complement sized to only the first partial, so later "
                 "first-leg fills fell silently into residual"),
            "bfcff9ca9c4f27d73b2146876fcb62b8090ac0941fecaae8205032e76d07006a":
                ("entrypoint still named XR2W's single-order path, so "
                 "arming per the spec would have run XR2W's one-order "
                 "probe through the single-slot latch and measured "
                 "nothing"),
            "f6b4a89ffbd801deefb8bdadacc7d127b507c928cc188e6b506a1bbbbb65f8b4":
                ("it consumed XR2W's PMUS_EXEC1_* authorization namespace "
                 "and XR2W's latch directory, so a legacy value on "
                 "l5-live-probe-oneshot could have participated in arming "
                 "E1, and it implied a network-incapability it does not "
                 "have"),
            "e11fc1abb9f5d0a4fa2ee95aff14ead8246edfc9bf08f88372bea5e60ce44310":
                ("its ARMED production path terminated BEFORE run_cohort: "
                 "main()'s armed branch was a bare `raise SystemExit`, no "
                 "snapshot_provider existed, and setting the PMUS_E1_* "
                 "keys would have produced twelve empty windows"),
            "cdc635190765732e478169e172d2a638f6fed31468379fbf3e3fdc4e3930ae60":
                ("live_book parsed a frame shape the venue does not send: "
                 "it dropped 11,438 of 11,523 real frames (99.3%), so the "
                 "top of book populated once and never moved, and health "
                 "reported HEALTHY while serving that stale book. It "
                 "failed OPEN."),
            "481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486":
                ("it discovered and identified its market on POLYMARKET.COM -- gamma-api.polymarket.com plus two CTF token ids, streamed over Polymarket.com's public CLOB websocket -- and then serialized that identity into an order to api.polymarket.us, which answered \"Market not found\". It also treated an ABSENT first-leg bid as 0.00 and therefore as a cheap maker opportunity"),
            "9db0d2a77a88a16415c69a84f385af39a272f04aff4e7b3fd554dd1baaded920":
                ("its websocket book parse let a NESTED OBJECT carrying a "
                 "bids/offers key inherit the market slug and become a "
                 "second book event, which the fold then applied LAST -- "
                 "so a stray level could replace the authoritative top of "
                 "book. Reproduced against the venue's real frame shape: "
                 "bids[0] 0.9900 became stats.bids[0] 0.4900. It also "
                 "shipped WITHOUT the `websockets` dependency, so the "
                 "decision feed could never connect at all"),
            "9de41c1762342557668cdcdf6b57538122d5fb2e50d7546d57c22e0b37c759d5":
                ("it carried a 5.0s AuthBook staleness bound, LOOSER than the "
                 "2.0s bound of XR2W Attempt 6 -- the only order path that "
                 "has produced an evidenced fill -- on no evidence for 5.0"),
        },
    },

    "xr2w_parity": {
        "xr2w_is_byte_identical": True,
        "xr2w_modified": False,
        "reused_by_reference": [
            "src.l5_order_body.build via orders.order_body",
            "src.l5_order_body.preview_envelope via orders.preview_request",
            "src.l5_preview_gate.require_echo",
            "orders.BoundedOrderTransport.request (four permitted routes)",
            "orders.VenueHTTPError / PreviewRejected / SubmissionAmbiguous",
        ],
        "the_only_behavioural_difference": (
            "the latch: E1's bounded-count named slots in place of XR2W's "
            "single slot. Asserted by parity test over a single "
            "submission."),
        "verified_source_sha256_prefixes": {
            "analysis/l5/live_exec5/spec.py": "dda1e2c50be81209",
            "analysis/l5/live_exec2/orders.py": "800078a852f14d84",
            "analysis/l5/live_exec2/latch.py": "add23515a5a8a201",
            "src/l5_order_body.py": "50b9ff1376abea03",
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
            "E7 the PMUS AUTHENTICATED book for the pinned slug must be "
            "streaming, two-sided and fresher than the frozen bound, and "
            "its source must be that authenticated socket. REST is "
            "discovery and cross-check only and may NEVER order the tape; "
            "an AuthBook failure may not fall back to REST.",
            "E8 the book must be two-sided and EXECUTABLE on both the YES "
            "side and its arithmetic complement: a price that is absent, "
            "zero, negative, non-finite, out of (0,1), or crossed is "
            "INELIGIBLE, not a cheap maker opportunity.",
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
            "C2 the complement is the OPPOSITE OUTCOME SIDE of the same "
            "PMUS market -- the same slug, outcomeSide inverted. On PMUS "
            "it is quoted as the arithmetic complement of the one book "
            "(NO bid = 1 - YES ask), which is the venue's own convention "
            "and not a second feed.",
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
            "C7 the complement quantity tracks the CUMULATIVE first-leg "
            "filled quantity, not merely the first partial. A 0.13 first "
            "leg that fills 0.05 and LATER 0.08 must have the ENTIRE 0.13 "
            "accounted for. The shortfall is submitted as an ADDITIONAL "
            "TRANCHE at the attempt's ORIGINAL complement price.",
            "C9 a tranche is NOT chasing and NOT a re-quote. It replaces "
            "nothing, cancels nothing and moves no price; it is triggered "
            "by a FILL, never by a non-fill, and the two are therefore "
            "distinguishable by their trigger. An unsuccessful complement "
            "is still never replaced at a more aggressive price: it is "
            "cancelled at handover and its quantity recorded as residual.",
            "C10 any quantity that cannot LEGALLY or ECONOMICALLY receive "
            "a complement -- a shortfall below the venue minimum 0.01, or "
            "one whose complement price is infeasible under the frozen "
            "fee-inclusive $0.96 ceiling -- is EXPLICITLY RECORDED AS "
            "RESIDUAL with its reason. Never hidden and never implicit.",
            "C8 the complement is subject to the same latch, the same "
            "preview-and-echo requirement and the same deadman.",
        ],
        "taker_rescue": False,
        "chasing": False,
        "requote": False,
        "quantity_tracks": "CUMULATIVE_FIRST_LEG_FILLED_QTY",
        "quantity_tracks_only_the_first_partial": False,
        "later_fills_fall_silently_into_residual": False,
        "tranche_price_is_the_attempts_original": True,
        "tranche_trigger": "A_FILL_NEVER_A_NON_FILL",
        "max_tranches": MAX_COMPLEMENT_TRANCHES,
        "max_tranches_source": "0.13 / 0.01, the venue grid",
        "uncomplementable_quantity_is_explicitly_recorded_residual": True,
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
        "authority": "pmus_authenticated_ws_strict",
        "module": "src/l5_pmus_auth_book.py",
        "venue": "polymarket_us",
        "transport": "PMUS authenticated websocket",
        "a_connected_two_sided_book_is_required": True,
        "rest_role": "discovery_and_cross_check_only",
        "rest_may_order_the_tape": False,
        "rest_is_sufficient_timing_authority": False,
        "authbook_failure_may_fall_back_to_rest": False,
        "stall_s": 10.0,
        "resubscribe_slow_s": 30.0,
        "stall_semantics_are_authbooks_own": True,
        "superseded_authority": (
            "websocket_strict over "
            "wss://ws-subscriptions-clob.polymarket.com/ws/market, which "
            "is POLYMARKET.COM's feed. It was correctly implemented and "
            "live-verified on 11,438 frames, and it was still the wrong "
            "venue's book for an order sent to api.polymarket.us."),
    },

    "latch": {
        "kind": "BOUNDED_COUNT_NAMED_SLOTS",
        "module": "analysis.l5.e1_cohort.latch",
        "slots_per_window": MAX_ATTEMPTS_PER_WINDOW,
        "mechanism": "O_CREAT|O_EXCL per NAMED slot file",
        "session_scoped": True,
        "namespace_dir": "/state/pmus-ws/e1_cohort",
        "shares_the_mounted_volume_with_xr2w": True,
        "xr2w_latch_dir_is_distinct": True,
        "file_prefix": "e1.",
        "xr2w_a6_latch_is_evidence_and_is_never_touched": True,
        "e1_deletes_resets_or_renames_anything": False,
        "boot_assertion": "latch:assert_no_collision_with_xr2w",
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

    "outcome_taxonomy": {
        "authorized_outcomes": [
            "FAIL",
            "COMPLETION_SPRT_POSITIVE_CROSSING",
            "CONTINUE_INSUFFICIENT_EVIDENCE",
        ],
        "there_are_exactly_three": True,
        "formal_PASS_exists_as_an_outcome": False,
        "no_code_path_can_label_a_result_PASS": True,
        "why_no_pass": (
            "240 authorized placements is below the design's 385 placement "
            "gate and far below its 431 binding fill gate, which 240 "
            "placements cannot reach at ANY fill rate. The owner accepted "
            "that formal PASS is unreachable rather than widening the "
            "cohort to reach it."),
        "1_FAIL": "a preregistered failure boundary is reached",
        "2_COMPLETION_SPRT_POSITIVE_CROSSING": {
            "trigger": "the completion SPRT walk crosses H1",
            "evidence_specifically_for":
                "the maker->maker completion question, and nothing else",
            "is_formal_E1_PASS": False,
            "is_proof_of_positive_expectancy": False,
            "is_authorization_for_E2": False,
            "disclaimers_are_FIELDS_on_the_result_not_prose": True,
        },
        "3_CONTINUE_INSUFFICIENT_EVIDENCE": {
            "trigger": "anything else, including every truncation",
            "truncation_cause_is_always_named": True,
            "risk_truncation_is_distinguished_from_low_fill_truncation":
                True,
            "if_the_5_dollar_risk_limit_truncates_the_outcome_is": (
                "CONTINUE_INSUFFICIENT_EVIDENCE with truncation_cause "
                "RISK_LIMIT_FORWARD_LOOKING_5_USD"),
        },
        "authorized_placements": MAX_PLACEMENTS,
        "design_placement_gate": DESIGN_PLACEMENT_GATE,
        "design_binding_fill_gate": DESIGN_BINDING_FILL_GATE,
        "at_p_fill_0_35_expected_fills": 84,
        "at_p_fill_0_20_expected_fills": 48,
        "sprt_expected_fills_under_h0": 81,
        "placements_windows_quantity_or_ceiling_increased_to_reach_a_"
        "boundary": False,
        "parameters_move_on_interim_results": False,
        "extending_the_cohort_requires_a_FRESH_owner_authorization": True,
        "anything_widened_quietly": False,
    },

    "raw_observation_preservation": {
        "is_the_most_important_requirement": True,
        "why": ("an inconclusive run with a complete tape is worth far more "
                "than a crossing with a lossy one. The classification can "
                "be recomputed later; the tape cannot be recollected."),
        "append_only": True,
        "full_provenance_on_every_row": True,
        "must_support_computing_independently_afterwards": [
            "first-leg maker fill rate",
            "conditional maker->maker completion rate",
            "residual rate, residual loss and residual duration",
            "pair cost",
            "actual cashflows",
        ],
        "a_truncated_or_inconclusive_cohort_must_still_yield_the_chain":
            True,
        "sufficiency_is_asserted_by_test_BEFORE_money_is_spent": True,
        "reconstructor": "analysis.l5.e1_cohort.recorder:reconstruct_chain",
    },

    "recording": {
        "module": "analysis.l5.e1_cohort.recorder",
        "append_only": True,
        "provenance_on_every_row": True,
        "per_eligible_attempt": [
            "the exact PMUS authenticated book state at decision time, "
            "naming the slug it belongs to",
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
    ("forward_looking_loss_bound.ceiling_usd", "5.00"),
    ("forward_looking_loss_bound.realized_only", False),
    ("forward_looking_loss_bound."
     "prospective_attempt_is_counted_before_submitting", True),
    ("forward_looking_loss_bound."
     "is_a_true_ceiling_not_a_ceiling_plus_one_attempt", True),
    ("forward_looking_loss_bound.per_attempt_max_loss_usd", "0.078"),
    ("forward_looking_loss_bound.residual_is_unresolved_until",
     "CONFIRMED_SETTLEMENT_CASHFLOW"),
    ("forward_looking_loss_bound."
     "forbidden_reducers_are_explicit_refusals_not_conventions", True),
    ("forward_looking_loss_bound.per_window_bound_usd", "1.56"),
    ("forward_looking_loss_bound.per_window_bound_binds_independently",
     True),
    ("forward_looking_loss_bound.both_bounds_bind_simultaneously", True),
    ("forward_looking_loss_bound.neither_bound_substitutes_for_the_other",
     True),
    ("forward_looking_loss_bound.unrestricted_worst_case_is_reachable",
     False),
    ("forward_looking_loss_bound.effective_worst_case_usd", "5.00"),
    ("rebates.structurally_zero", True),
    ("rebates.sized_for_rebates", False),
    ("rebates.rebates_are_recorded_from_the_venue_never_modelled", True),
    ("complement.taker_rescue", False),
    ("complement.chasing", False),
    ("complement.requote", False),
    ("complement.quantity_tracks", "CUMULATIVE_FIRST_LEG_FILLED_QTY"),
    ("complement.quantity_tracks_only_the_first_partial", False),
    ("complement.later_fills_fall_silently_into_residual", False),
    ("complement.tranche_price_is_the_attempts_original", True),
    ("complement.tranche_trigger", "A_FILL_NEVER_A_NON_FILL"),
    ("complement.max_tranches", 13),
    ("complement."
     "uncomplementable_quantity_is_explicitly_recorded_residual", True),
    ("live_provider.armed_main_reaches_run_cohort", True),
    ("live_provider.armed_main_is_a_bare_raise", False),
    ("live_provider.built_another_discovery_stack", False),
    ("live_provider.built_another_book_stack", False),
    ("live_provider.series_pinned", "cpc-btc-updown-15m"),
    ("live_provider.market_identity", "slug_plus_outcome_side"),
    ("live_provider.ctf_token_ids_are_pmus_identity", False),
    ("live_provider.windows_sequenced", 12),
    ("live_provider.windows_are_distinct", True),
    ("live_provider."
     "an_already_consumed_window_start_can_be_reopened", False),
    ("live_provider.a_window_whose_end_has_passed_can_be_opened", False),
    ("live_provider.degraded_snapshot_is_ever_returned", False),
    ("live_provider.timing_authority", "pmus_authenticated_ws_strict"),
    ("live_provider.rest_is_sufficient_timing_authority", False),
    ("live_provider.rest_role", "discovery_and_cross_check_only"),
    ("live_provider.authbook_failure_may_fall_back_to_rest", False),
    ("live_provider.derived_complement_is_a_second_feed", False),
    ("live_provider.derived_complement_is_the_venue_convention", True),
    ("decision_feed.collector", "src.l5_pmus_auth_book"),
    ("decision_feed.collector_is_vendored_or_reimplemented", False),
    ("decision_feed.ctf_token_ids_used", False),
    ("decision_feed.a_snapshot_requires_a_connected_two_sided_book", True),
    ("decision_feed.source_must_be_the_authenticated_ws", True),
    ("decision_feed.a_rest_sourced_snapshot_is_refused", True),
    ("decision_feed.max_book_age_s", 2.0),
    ("decision_feed.execution_feed_proven_by_a_fill",
     "src.l5_pmus_auth_book.AuthBook"),
    ("decision_feed.rest_gateway_book_is_a_cache", True),
    ("decision_feed.rest_gateway_book_may_price_an_order", False),
    ("decision_feed.staleness_is_the_authenticated_stream_age", True),
    ("decision_feed.staleness_depends_on_silence_alone", False),
    ("decision_feed."
     "complement_is_the_arithmetic_complement_of_the_same_book", True),
    ("decision_feed.complement_is_a_second_feed", False),
    ("decision_feed.ws_carries_a_best_bid_field", False),
    ("decision_feed.price_comes_from_the_ladder_top", True),
    ("decision_feed.ladder_is_ordered_best_first", True),
    ("decision_feed."
     "ladder_ordering_is_assumed_rather_than_measured", False),
    ("decision_feed.a_nested_object_can_replace_the_top_of_book", False),
    ("decision_feed.no_side_priced_in_no_terms_on_the_wire", False),
    ("decision_feed.size_is_invented_when_absent", False),
    ("decision_feed.a_book_for_another_slug_is_refused", True),
    ("decision_feed.stall_and_resubscribe_semantics_are_authbooks_own",
     True),
    ("cohort_authorization.slug_check_deleted", False),
    ("cohort_authorization.is_stricter_than_one_static_slug", True),
    ("cohort_authorization.complement_may_select_its_own_market", False),
    ("cohort_authorization."
     "complement_inherits_the_first_leg_authorization", True),
    ("durable_progress.a_restart_resumes", True),
    ("durable_progress.a_restart_resets_counters", False),
    ("durable_progress.a_restart_grants_another_12_windows", False),
    ("durable_progress.reset_path_exists", False),
    ("durable_progress.fails_closed_if_unwritable", True),
    ("network.network_capable", True),
    ("network.claims_to_be_network_incapable", False),
    ("network.tests_make_zero_network_calls", True),
    ("network.transport_reachable_only_through",
     ["the preview-echo gate", "the E1 named-slot latch"]),
    ("authorization.namespace", "PMUS_E1_*"),
    ("authorization.reads_pmus_exec1_namespace", False),
    ("latch.namespace_dir", "/state/pmus-ws/e1_cohort"),
    ("latch.xr2w_latch_dir_is_distinct", True),
    ("latch.xr2w_a6_latch_is_evidence_and_is_never_touched", True),
    ("latch.boot_assertion", "latch:assert_no_collision_with_xr2w"),
    ("venue_error_stop.is_a_safety_stop", True),
    ("venue_error_stop.is_an_economic_threshold", False),
    ("venue_error_stop.consecutive_errors", 5),
    ("venue_error_stop.automatic_retry", False),
    ("supersedes_e1.must_not_be_rearmed",
     ["110a4fc49cc936431aa1d099623128ceb77cd91fc7625e6518f2ede4f01ba99c",
      "bfcff9ca9c4f27d73b2146876fcb62b8090ac0941fecaae8205032e76d07006a",
      "f6b4a89ffbd801deefb8bdadacc7d127b507c928cc188e6b506a1bbbbb65f8b4",
      "e11fc1abb9f5d0a4fa2ee95aff14ead8246edfc9bf08f88372bea5e60ce44310",
      "cdc635190765732e478169e172d2a638f6fed31468379fbf3e3fdc4e3930ae60",
      "481d1d3f04a54cb4a9c7e701f2a4c364bc9cefad88c6cec4245b39784d8e2486",
      "9db0d2a77a88a16415c69a84f385af39a272f04aff4e7b3fd554dd1baaded920",
      "9de41c1762342557668cdcdf6b57538122d5fb2e50d7546d57c22e0b37c759d5",
      "b32e8c7bbc4d496bbf05c633f151892ad74a8bf5b34a5750fc70715520881cf1"]),
    ("paced_observation.pace_s", 1.0),
    ("paced_observation.min_pace_s", 0.05),
    ("paced_observation.no_paced_wait_is_ever_zero", True),
    ("paced_observation.deadline_aware", True),
    ("paced_observation.never_sleeps_past_the_trading_cutoff", True),
    ("paced_observation.classification_is_an_allowlist", True),
    ("paced_observation.an_unrecognised_refusal_is_terminal", True),
    ("paced_observation.ambiguous_submission_is_still_never_retried", True),
    ("paced_observation.consumes_no_attempt", True),
    ("paced_observation.consumes_no_latch_slot", True),
    ("paced_observation.consumes_no_risk_budget", True),
    ("paced_observation.iteration_cap", 11760),
    ("paced_observation.old_iteration_cap", 960),
    ("paced_observation.changes_no_bound", True),
    ("entrypoint.module", "scripts.l5_e1_cohort_run"),
    ("entrypoint.callable", "analysis.l5.e1_cohort.runner:main"),
    ("entrypoint.cohort_driver",
     "analysis.l5.e1_cohort.runner:run_cohort"),
    ("entrypoint.reaches_e1_cohort_runner", True),
    ("entrypoint.reaches_e1_bounded_named_slot_latch", True),
    ("entrypoint.reaches_proven_xr2w_order_primitives", True),
    ("entrypoint.enters_xr2w_experiment_runner", False),
    ("entrypoint.enters_xr2w_single_slot_latch", False),
    ("entrypoint.inherited_from_the_parent", False),
    ("entrypoint.MUST_NOT_RESOLVE_TO",
     ["scripts.l5_exec1xr2w_run",
      "analysis.l5.live_exec3.runner:run_experiment",
      "analysis.l5.live_exec2.latch:consume",
      "analysis.l5.live_exec2.orders:submit_one"]),
    ("pair_cost.ceiling_usd", "0.96"),
    ("pair_cost.fee_inclusive", True),
    ("pair_cost.fee_schedule_fingerprint", FEE_SCHEDULE_FINGERPRINT),
    ("pair_cost.a_pair_above_the_ceiling_is_REFUSED_not_discounted", True),
    ("timing_authority.authority", "pmus_authenticated_ws_strict"),
    ("timing_authority.a_connected_two_sided_book_is_required", True),
    ("timing_authority.rest_is_sufficient_timing_authority", False),
    ("timing_authority.authbook_failure_may_fall_back_to_rest", False),
    ("timing_authority.rest_may_order_the_tape", False),
    ("latch.kind", "BOUNDED_COUNT_NAMED_SLOTS"),
    ("latch.slots_per_window", 20),
    ("latch.consumed_before_the_post", True),
    ("latch.released_on_failure", False),
    ("latch.is_a_bare_counter", False),
    ("stopping.boundaries_invented_here", False),
    ("stopping.hard_truncation_placements", 4310),
    ("outcome_taxonomy.there_are_exactly_three", True),
    ("outcome_taxonomy.formal_PASS_exists_as_an_outcome", False),
    ("outcome_taxonomy.no_code_path_can_label_a_result_PASS", True),
    ("outcome_taxonomy.authorized_outcomes",
     ["FAIL", "COMPLETION_SPRT_POSITIVE_CROSSING",
      "CONTINUE_INSUFFICIENT_EVIDENCE"]),
    ("outcome_taxonomy.2_COMPLETION_SPRT_POSITIVE_CROSSING.is_formal_E1_PASS",
     False),
    ("outcome_taxonomy.2_COMPLETION_SPRT_POSITIVE_CROSSING."
     "is_proof_of_positive_expectancy", False),
    ("outcome_taxonomy.2_COMPLETION_SPRT_POSITIVE_CROSSING."
     "is_authorization_for_E2", False),
    ("outcome_taxonomy.3_CONTINUE_INSUFFICIENT_EVIDENCE."
     "risk_truncation_is_distinguished_from_low_fill_truncation", True),
    ("outcome_taxonomy.placements_windows_quantity_or_ceiling_increased_to_"
     "reach_a_boundary", False),
    ("outcome_taxonomy.parameters_move_on_interim_results", False),
    ("outcome_taxonomy.extending_the_cohort_requires_a_FRESH_owner_"
     "authorization", True),
    ("outcome_taxonomy.anything_widened_quietly", False),
    ("raw_observation_preservation.is_the_most_important_requirement", True),
    ("raw_observation_preservation.append_only", True),
    ("raw_observation_preservation.full_provenance_on_every_row", True),
    ("raw_observation_preservation."
     "a_truncated_or_inconclusive_cohort_must_still_yield_the_chain", True),
    ("raw_observation_preservation."
     "sufficiency_is_asserted_by_test_BEFORE_money_is_spent", True),
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
    "b0f15c958c8fee779e3b9518885c7ddd3ea379afd5bb9759c44f878d7322f3ba"


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
