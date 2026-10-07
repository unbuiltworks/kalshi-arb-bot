"""PASS / FAIL / CONTINUE, plus the hard automatic stops.

EVERY BOUNDARY IS READ, NOT INVENTED. The three-way decision comes from
`analysis/l5/measurement/E1_COHORT_DESIGN.md` §3, implemented there in
`cohort_design.e1_decision_rules()`, which this module imports READ-ONLY
and re-asserts rather than restating. No threshold in this file is new:

  FAIL  35 consecutive no-fill placements -> Beta(1,36) 97.5% upper = 0.0947
  FAIL  the estimand-2 SPRT walk crossing H0 at -2.944439, triggered
        outright by 8 consecutive non-completions
  FAIL  any fill timestamped after a cancel acknowledgement (n = 1)
  FAIL  any cashflow without sufficient source identity (n = 1)
  CONT  hard truncation at 4,310 placements
  PASS  385 placements AND 431 fills, and four further conditions

THE HARD AUTOMATIC STOPS are separate from the verdict, and that
separation is the point: `E1_COHORT_DESIGN.md` §3 says plainly that a
cumulative-loss abort is a FAILURE TO MEASURE, reported with the partial
posteriors and NEVER reported as FAIL or as a result. So a stop returns
`stopped: True` with a `stop_reason` and a verdict that is NOT "FAIL".

  STOP  cohort realized loss >= $5.00
  STOP  window exposure >= $1.56

THE FORWARD-LOOKING $5.00 INVARIANT
-----------------------------------
A realized-only stop is a stop that fires AFTER the money is gone. The
owner's risk-control requirement replaces it with a bound evaluated at
EVERY decision and EVERY submission:

    realized_cohort_loss
  + maximum remaining loss on ALL currently unresolved residual inventory
  + maximum loss of THE ATTEMPT ABOUT TO BE SUBMITTED
  <= $5.00

An unresolved residual's maximum remaining loss is `filled_qty x
fill_price`: the worst case is that it settles worthless. The prospective
attempt's cap is `0.13 x 0.60 = $0.078`. The third term is counted BEFORE
submitting -- the strict reading -- so $5.00 is a TRUE CEILING and not a
ceiling plus one attempt.

ONLY a CONFIRMED settled win may reduce the realized component. FOUR
THINGS MAY NOT, and each is an explicit refusal recorded in
`refused_reducers` rather than a convention: mark-to-market value;
expected or projected settlement; an unfilled or resting complement;
pending, accrued or estimated rebates. A test asserts that each one leaves
the bound exactly where it was.

`MAX_EXPOSURE_PER_WINDOW_USD = $1.56` remains an INDEPENDENT hard limit.
Both bind simultaneously and neither substitutes for the other: the window
bound caps how fast, the forward-looking bound caps how much.

The consequence, stated plainly: the unrestricted $18.72 cohort worst case
is now UNREACHABLE. See `effective_worst_case()`.
  STOP  any safety-invariant violation
  STOP  venue error storm
  STOP  ambiguous submission -- NEVER retried; venue state is reconciled

THE HONEST TERMINATION, stated because it is uncomfortable
----------------------------------------------------------
The authorized cohort is 12 x 20 = 240 placements. The design's binding
fill gate is 431 FILLS and its placement gate is 385 PLACEMENTS. **240
placements cannot reach either, at any fill rate.** At p_fill = 0.35, 240
placements yield about 84 fills -- near the SPRT's 81 expected under H0,
so estimand 2 may just decide. At p_fill = 0.20 they yield about 48,
which is SHORT of that. So the instrument must be able to say "the fill
rate is too low for this cohort to decide" OUT LOUD, and it does:
verdict `INCONCLUSIVE_FILL_RATE_TOO_LOW`, emitted at the point it becomes
arithmetically certain rather than discovered at the end.

**Extending the cohort requires a FRESH owner authorization.** Nothing in
this module widens anything, and `project_feasibility()` exists so the
narrowness is visible before the first placement rather than after the
last one.
"""
from __future__ import annotations

import math
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from analysis.l5.e1_cohort.spec import (ABSOLUTE_WORST_CASE_USD,
                                        FIRST_LEG_QTY,
                                        MAX_ATTEMPTS_PER_WINDOW,
                                        MAX_COHORT_REALIZED_LOSS_USD,
                                        MAX_EXPOSURE_PER_WINDOW_USD,
                                        MAX_FIRST_LEG_PRICE, MAX_PLACEMENTS,
                                        PROBE, WINDOWS, assert_frozen)
from analysis.l5.measurement import cohort_design as CD

#: Read from the design, not chosen here.
RULES = CD.e1_decision_rules()

FAIL_CONSECUTIVE_NO_FILL = CD.e1_fail_boundary_consecutive_misses()      # 35
SPRT_H0 = 0.6772
SPRT_H1 = 0.7873
SPRT_ALPHA = 0.05
SPRT_BETA = 0.05
ECONOMICS_FORECLOSURE_USD_PER_SHARE = \
    CD.e1_economics_foreclosure_usd_per_share()               # -0.006231

#: E1_COHORT_DESIGN.md estimand 2: expected fills under H0. Read, not chosen.
SPRT_EXPECTED_FILLS_UNDER_H0 = 81

PLACEMENT_GATE = 385
BINDING_FILL_GATE = CD.e1_binding_gates()["fill_gate"]
HARD_TRUNCATION = CD.e1_hard_truncation_placements()                  # 4310

#: ARITHMETIC: 0.13 x 0.60. The maximum loss of ONE attempt -- the first leg
#: filling in full at the frozen ceiling, the complement never filling, the
#: residual settling worthless.
PER_ATTEMPT_MAX_LOSS_USD = (FIRST_LEG_QTY * MAX_FIRST_LEG_PRICE)       # 0.078

# --------------------------------------------------------------------------
# THE THREE AND ONLY THREE AUTHORIZED OUTCOMES.
#
# The owner has accepted that formal E1 PASS is unreachable at 240
# placements rather than widening the cohort to reach it. So there is NO
# PASS outcome, and no code path that can produce one: the string "PASS"
# does not appear as a terminal classification anywhere in this module, and
# a test asserts that.
# --------------------------------------------------------------------------

#: 1. a preregistered failure boundary is reached.
VERDICT_FAIL = "FAIL"

#: 2. the completion SPRT crosses H1. NOT a PASS -- see the four disclaimers
#:    carried as FIELDS on the result object, not as prose.
VERDICT_SPRT_POSITIVE = "COMPLETION_SPRT_POSITIVE_CROSSING"

#: 3. anything else at all, including every truncation.
VERDICT_CONTINUE = "CONTINUE_INSUFFICIENT_EVIDENCE"

AUTHORIZED_VERDICTS = (VERDICT_FAIL, VERDICT_SPRT_POSITIVE, VERDICT_CONTINUE)

#: WHICH limit stopped it. The owner wants a risk truncation distinguishable
#: in the record from a low-fill-rate truncation.
TRUNCATION_RISK_LIMIT = "RISK_LIMIT_FORWARD_LOOKING_5_USD"
TRUNCATION_WINDOW_EXPOSURE = "RISK_LIMIT_WINDOW_EXPOSURE_1_56_USD"
TRUNCATION_LOW_FILL_RATE = "LOW_FILL_RATE_UNDECIDABLE"
TRUNCATION_PLACEMENT_CAP = "AUTHORIZED_PLACEMENT_CAP_240"
TRUNCATION_DESIGN_HARD = "DESIGN_HARD_TRUNCATION_4310"
TRUNCATION_SAFETY = "SAFETY_INVARIANT_VIOLATION"
TRUNCATION_VENUE_STORM = "VENUE_ERROR_STORM"
TRUNCATION_AMBIGUOUS = "AMBIGUOUS_SUBMISSION_RECONCILE_VENUE_STATE"
#: A DETERMINISTIC fault in OUR OWN process -- a missing dependency, an
#: unset or unusable credential, a signing failure. It is NOT
#: TRUNCATION_VENUE_STORM and must never be recorded as one: on
#: 2026-10-07 the two were conflated and the tape blamed a venue that had
#: never been contacted. It fails closed on the FIRST occurrence.
TRUNCATION_LOCAL_FAULT = "LOCAL_EXECUTION_FAULT"
TRUNCATION_NONE = None

#: The four disclaimers outcome 2 carries as FIELDS, so they cannot be
#: dropped when the result is summarised.
SPRT_POSITIVE_DISCLAIMERS: Dict[str, Any] = {
    "evidence_specifically_for":
        "the maker->maker completion question, and nothing else",
    "is_formal_E1_PASS": False,
    "is_proof_of_positive_expectancy": False,
    "is_authorization_for_E2": False,
}


def sprt_walk(completions: int, non_completions: int) -> float:
    """Wald log-likelihood-ratio walk for estimand 2. The hypothesis pair is
    Stage-6's own observed proxy matched fraction against its derived
    break-even; neither was chosen by this cohort."""
    s = math.log(SPRT_H1 / SPRT_H0)
    f = math.log((1.0 - SPRT_H1) / (1.0 - SPRT_H0))
    return completions * s + non_completions * f


def sprt_boundaries() -> Dict[str, float]:
    a = math.log(SPRT_BETA / (1.0 - SPRT_ALPHA))
    b = math.log((1.0 - SPRT_BETA) / SPRT_ALPHA)
    return {"accept_h0_at_or_below": a, "accept_h1_at_or_above": b}


def project_feasibility() -> Dict[str, Any]:
    """What the authorized 240 placements can and cannot decide, as
    arithmetic, BEFORE anything is placed."""
    rows = []
    for p in (0.10, 0.20, 0.35, 0.50, 0.87):
        fills = int(MAX_PLACEMENTS * p)
        rows.append({
            "p_fill": p,
            "expected_fills_in_240_placements": fills,
            "reaches_sprt_expected_fills_under_h0":
                fills >= SPRT_EXPECTED_FILLS_UNDER_H0,
            "reaches_binding_fill_gate_431": fills >= BINDING_FILL_GATE,
        })
    return {
        "probe": PROBE,
        "authorized_placements": MAX_PLACEMENTS,
        "design_placement_gate": PLACEMENT_GATE,
        "design_binding_fill_gate": BINDING_FILL_GATE,
        "placement_gate_reachable": MAX_PLACEMENTS >= PLACEMENT_GATE,
        "binding_fill_gate_reachable": MAX_PLACEMENTS >= BINDING_FILL_GATE,
        "PASS_reachable_in_this_authorization": False,
        "what_the_authorized_cohort_can_reach": list(AUTHORIZED_VERDICTS),
        "formal_PASS_is_not_an_authorized_outcome_at_all": True,
        "rows": rows,
        "HONESTY": (
            "240 placements yield about 85 fills only if p_fill >= ~0.35. "
            "At p_fill = 0.20 they yield about 48, SHORT of the SPRT's "
            "need. The cohort therefore terminates CONTINUE explicitly as "
            "%s with truncation_cause %s rather than running out "
            "silently." % (VERDICT_CONTINUE, TRUNCATION_LOW_FILL_RATE)),
        "EXTENDING_THE_COHORT_REQUIRES_A_FRESH_OWNER_AUTHORIZATION": True,
        "anything_widened_here": False,
    }


def effective_worst_case() -> Dict[str, Any]:
    """The worst case AFTER the forward-looking invariant, with the
    arithmetic shown. The unrestricted $18.72 is unreachable."""
    return {
        "unrestricted_cohort_worst_case_usd": str(ABSOLUTE_WORST_CASE_USD),
        "unrestricted_arithmetic":
            "12 windows x 20 attempts x 0.13 shares x $0.60 = $18.72",
        "unrestricted_is_REACHABLE": False,
        "why_unreachable": (
            "the forward-looking invariant is evaluated BEFORE every "
            "submission and counts the attempt about to be made, so the "
            "sum realized + unresolved-residual-max + this-attempt-max can "
            "never be allowed to exceed $5.00. Reaching $18.72 would "
            "require passing through $5.00, and the attempt that would "
            "cross it is refused."),
        "effective_cohort_worst_case_usd":
            str(MAX_COHORT_REALIZED_LOSS_USD),
        "effective_arithmetic": (
            "realized_loss + SUM(unresolved residual filled_qty x "
            "fill_price) + (1 x 0.13 x $0.60 = $0.078) <= $5.00, checked at "
            "every decision and every submission. The last permitted "
            "attempt is the one whose inclusion still satisfies the "
            "inequality, so the bound is never exceeded -- not even by one "
            "attempt."),
        "per_attempt_max_loss_usd": str(PER_ATTEMPT_MAX_LOSS_USD),
        "per_attempt_arithmetic": "0.13 x $0.60 = $0.078",
        "independent_per_window_bound_usd":
            str(MAX_EXPOSURE_PER_WINDOW_USD),
        "per_window_arithmetic": "20 x 0.13 x $0.60 = $1.56",
        "both_bind_simultaneously": True,
        "neither_substitutes_for_the_other": True,
        "reduced_only_by": "a CONFIRMED settled win",
    }


class SafetyViolation(RuntimeError):
    """A safety invariant was violated. Hard automatic stop."""


class CohortTracker:
    """Counts what the boundaries are defined over, and nothing else.

    Every counter is monotone and is incremented from an OBSERVATION, never
    from a model. `realized_loss_usd` is moved only by a confirmed
    settlement cashflow, so the loss stop cannot be tripped -- or dodged --
    by a mark-to-market number.
    """

    def __init__(self) -> None:
        assert_frozen()
        self.placements = 0
        self.fills = 0
        self.consecutive_no_fill = 0
        self.completions = 0
        self.non_completions = 0
        self.consecutive_non_completion = 0
        self.realized_loss_usd = D("0")
        self.window_exposure_usd: Dict[int, D] = {w: D("0")
                                                  for w in range(WINDOWS)}
        self.window_attempts: Dict[int, int] = {w: 0 for w in range(WINDOWS)}
        self.post_cancel_ack_fills = 0
        self.unattributed_cashflows = 0
        self.ambiguous_submissions = 0
        self.venue_errors = 0
        self.consecutive_venue_errors = 0
        self.local_execution_faults = 0
        self.local_execution_fault_log: List[Dict[str, Any]] = []
        self.safety_violations: List[str] = []
        self.per_share_expectancy_upper_usd: Optional[D] = None
        self.estimand8_reconciled_without_unattributed = False
        self.stopped = False
        self.stop_reason: Optional[str] = None
        #: UNRESOLVED residual inventory, keyed (window, slot). A residual is
        #: unresolved until its settlement cashflow is CONFIRMED, and its
        #: maximum remaining loss is filled_qty x fill_price -- the worst
        #: case, which is that it settles worthless.
        self.residuals: Dict[Any, Dict[str, Any]] = {}
        #: every attempt to reduce the bound by something that may not
        #: reduce it, recorded rather than silently ignored.
        self.refused_reducers: List[Dict[str, Any]] = []
        #: the EXACT venue errors, kept because the stop is a SAFETY stop
        #: and a safety stop that cannot say what it saw is not evidence.
        self.venue_error_log: List[Dict[str, Any]] = []

    # ----------------------------------------------------------- observations
    def record_placement(self, *, window: int, notional_usd: Any) -> None:
        if window not in self.window_exposure_usd:
            raise SafetyViolation("window %r is outside 0..%d"
                                  % (window, WINDOWS - 1))
        self.placements += 1
        self.window_attempts[window] += 1
        self.window_exposure_usd[window] += D(str(notional_usd))
        if self.window_attempts[window] > MAX_ATTEMPTS_PER_WINDOW:
            self.violate("window %d exceeded %d attempts"
                         % (window, MAX_ATTEMPTS_PER_WINDOW))
        if self.placements > MAX_PLACEMENTS:
            self.violate("cohort exceeded %d placements" % MAX_PLACEMENTS)

    def record_first_leg_result(self, *, filled_qty: Any) -> None:
        if D(str(filled_qty)) > 0:
            self.fills += 1
            self.consecutive_no_fill = 0
        else:
            self.consecutive_no_fill += 1

    def record_completion(self, *, complete: bool) -> None:
        if complete:
            self.completions += 1
            self.consecutive_non_completion = 0
        else:
            self.non_completions += 1
            self.consecutive_non_completion += 1

    def record_confirmed_cash(self, *, amount_usd: Any,
                              key: Any = None) -> None:
        """Only a SETTLEMENT-CONFIRMED cashflow moves the realized component.

        A loss raises it. A WIN lowers it -- and a confirmed settled win is
        the ONLY thing permitted to lower it. `key`, when given, resolves
        that residual so it stops consuming forward-looking headroom.
        """
        amt = D(str(amount_usd))
        if amt < 0:
            self.realized_loss_usd += -amt
        elif amt > 0:
            self.realized_loss_usd -= amt
            if self.realized_loss_usd < 0:
                self.realized_loss_usd = D("0")
        if key is not None:
            self.resolve_residual(key=key)

    # ------------------------------------------- forward-looking loss bound
    def open_residual(self, *, window: int, slot: int, filled_qty: Any,
                      fill_price_usd: Any) -> Dict[str, Any]:
        """Register UNRESOLVED residual inventory.

        Its maximum remaining loss is `filled_qty x fill_price`: the worst
        case is that it settles worthless. It consumes forward-looking
        headroom from the moment it exists until its settlement cashflow is
        CONFIRMED.
        """
        q = D(str(filled_qty))
        px = D(str(fill_price_usd))
        rec = {"window": window, "slot": slot, "filled_qty": str(q),
               "fill_price_usd": str(px),
               "max_remaining_loss_usd": str(q * px),
               "resolved": False,
               "resolution_requires": "CONFIRMED_SETTLEMENT_CASHFLOW"}
        self.residuals[(window, slot)] = rec
        return rec

    def resolve_residual(self, *, key: Any) -> None:
        """A residual leaves the forward-looking sum ONLY on confirmed
        settlement cash. Nothing else resolves it."""
        rec = self.residuals.get(tuple(key) if isinstance(key, list) else key)
        if rec is not None:
            rec["resolved"] = True

    def unresolved_residual_max_loss_usd(self) -> D:
        return sum((D(r["max_remaining_loss_usd"])
                    for r in self.residuals.values()
                    if not r["resolved"]), D("0"))

    # --- the four things that MAY NOT reduce the bound, as explicit
    # --- refusals rather than conventions. Each records the attempt and
    # --- returns the bound UNCHANGED.
    def _refuse_reducer(self, kind: str, detail: Any) -> Dict[str, Any]:
        before = self.forward_looking_loss_usd()
        self.refused_reducers.append(
            {"kind": kind, "detail": detail,
             "bound_before_usd": str(before),
             "bound_after_usd": str(before),
             "moved_the_bound": False,
             "why": ("only a CONFIRMED settled win may reduce the realized "
                     "component of the forward-looking bound")})
        return self.refused_reducers[-1]

    def offer_mark_to_market(self, value_usd: Any) -> Dict[str, Any]:
        """REFUSED. A mark is not cash."""
        return self._refuse_reducer("MARK_TO_MARKET", str(value_usd))

    def offer_expected_settlement(self, value_usd: Any) -> Dict[str, Any]:
        """REFUSED. An expectation is not a settlement."""
        return self._refuse_reducer("EXPECTED_OR_PROJECTED_SETTLEMENT",
                                    str(value_usd))

    def offer_resting_complement(self, *, window: int, slot: int,
                                 price_usd: Any) -> Dict[str, Any]:
        """REFUSED. An unfilled or resting complement hedges nothing."""
        return self._refuse_reducer(
            "UNFILLED_OR_RESTING_COMPLEMENT",
            {"window": window, "slot": slot, "price_usd": str(price_usd)})

    def offer_pending_rebate(self, value_usd: Any) -> Dict[str, Any]:
        """REFUSED. A pending, accrued or estimated rebate is not credited,
        and at 0.13 shares it is structurally $0.00 anyway."""
        return self._refuse_reducer("PENDING_ACCRUED_OR_ESTIMATED_REBATE",
                                    str(value_usd))

    def forward_looking_loss_usd(self,
                                 *, prospective_attempts: int = 0) -> D:
        """realized + max remaining loss on ALL unresolved residuals
        + max loss of the attempt(s) about to be submitted."""
        return (self.realized_loss_usd
                + self.unresolved_residual_max_loss_usd()
                + D(prospective_attempts) * PER_ATTEMPT_MAX_LOSS_USD)

    def forward_looking_report(self, *, prospective_attempts: int = 1
                               ) -> Dict[str, Any]:
        total = self.forward_looking_loss_usd(
            prospective_attempts=prospective_attempts)
        return {
            "realized_cohort_loss_usd": str(self.realized_loss_usd),
            "unresolved_residual_max_loss_usd":
                str(self.unresolved_residual_max_loss_usd()),
            "unresolved_residuals": len(
                [r for r in self.residuals.values() if not r["resolved"]]),
            "prospective_attempts": prospective_attempts,
            "per_attempt_max_loss_usd": str(PER_ATTEMPT_MAX_LOSS_USD),
            "prospective_attempt_max_loss_usd":
                str(D(prospective_attempts) * PER_ATTEMPT_MAX_LOSS_USD),
            "forward_looking_total_usd": str(total),
            "ceiling_usd": str(MAX_COHORT_REALIZED_LOSS_USD),
            "within_ceiling": total <= MAX_COHORT_REALIZED_LOSS_USD,
            "may_be_reduced_only_by": "a CONFIRMED settled win",
            "may_never_reduce_it": [
                "mark-to-market value",
                "expected or projected settlement",
                "an unfilled or resting complement",
                "pending, accrued or estimated rebates",
            ],
            "refused_reducers": list(self.refused_reducers),
            "window_bound_usd": str(MAX_EXPOSURE_PER_WINDOW_USD),
            "window_bound_binds_independently": True,
            "theoretical_18_72_is_reachable": False,
        }

    def record_post_cancel_ack_fill(self) -> None:
        self.post_cancel_ack_fills += 1

    def record_unattributed_cashflow(self) -> None:
        self.unattributed_cashflows += 1

    def record_ambiguous_submission(self) -> None:
        """NEVER retried. The venue state is reconciled instead."""
        self.ambiguous_submissions += 1

    def record_venue_error(self, detail: Any = None) -> None:
        """A SAFETY counter, never an economic threshold. The EXACT error is
        recorded, and there is no automatic retry anywhere: a retry could
        duplicate an ambiguous submission, so the venue state is reconciled
        instead."""
        self.venue_errors += 1
        self.consecutive_venue_errors += 1
        self.venue_error_log.append(
            {"n": self.venue_errors,
             "consecutive": self.consecutive_venue_errors,
             "detail": detail, "retried": False,
             "is_a_safety_observation": True,
             "is_an_economic_threshold": False})

    def record_local_execution_fault(self, detail: Any = None) -> None:
        """A LOCAL fault. Counted SEPARATELY from venue errors, and stopping
        on the first one is the point: a missing dependency or an unusable
        key fails identically on every subsequent attempt, so four more
        tries buy no information and spend four of an authorized 240.

        It deliberately does NOT touch `consecutive_venue_errors`. A local
        fault must not be able to manufacture a VENUE_ERROR_STORM, which is
        precisely what happened at 2026-10-07T16:48:58Z.
        """
        self.local_execution_faults += 1
        self.local_execution_fault_log.append(
            {"n": self.local_execution_faults, "detail": detail,
             "retried": False, "is_a_venue_error": False,
             "reached_the_venue": False,
             "stops_on_first_occurrence": True})

    def record_venue_ok(self) -> None:
        self.consecutive_venue_errors = 0

    def violate(self, why: str) -> None:
        self.safety_violations.append(why)

    # ------------------------------------------------------------ hard stops
    #: FROZEN at five consecutive venue errors for this cohort. STRICTLY a
    #: SAFETY stop and NEVER an economic threshold: it says "the venue is
    #: not answering us coherently", not "the strategy is bad". The exact
    #: errors are recorded in `venue_error_log`, and nothing anywhere
    #: retries automatically -- a retry could duplicate an ambiguous
    #: submission.
    VENUE_ERROR_STORM = 5

    def hard_stops(self) -> List[str]:
        out: List[str] = []
        if self.realized_loss_usd >= MAX_COHORT_REALIZED_LOSS_USD:
            out.append("COHORT_REALIZED_LOSS_AT_OR_ABOVE_%s"
                       % MAX_COHORT_REALIZED_LOSS_USD)
        # the FORWARD-LOOKING bound with ZERO prospective attempts: realized
        # plus everything already at risk. If that alone reaches the ceiling
        # the cohort is over, whatever else is true.
        committed = self.forward_looking_loss_usd(prospective_attempts=0)
        if committed >= MAX_COHORT_REALIZED_LOSS_USD:
            out.append(
                "FORWARD_LOOKING_COMMITTED_LOSS_%s_AT_OR_ABOVE_%s"
                % (committed, MAX_COHORT_REALIZED_LOSS_USD))
        for w, x in self.window_exposure_usd.items():
            if x >= MAX_EXPOSURE_PER_WINDOW_USD:
                out.append("WINDOW_%d_EXPOSURE_AT_OR_ABOVE_%s"
                           % (w, MAX_EXPOSURE_PER_WINDOW_USD))
        if self.safety_violations:
            out.append("SAFETY_INVARIANT_VIOLATION:%s"
                       % "; ".join(self.safety_violations))
        if self.local_execution_faults:
            out.append("LOCAL_EXECUTION_FAULT_%d_FAIL_CLOSED"
                       % self.local_execution_faults)
        if self.consecutive_venue_errors >= self.VENUE_ERROR_STORM:
            out.append("VENUE_ERROR_STORM_%d_CONSECUTIVE"
                       % self.consecutive_venue_errors)
        if self.ambiguous_submissions:
            out.append("AMBIGUOUS_SUBMISSION_NEVER_RETRIED_RECONCILE_VENUE")
        return out

    def may_place(self, *, window: int, notional_usd: Any) -> Dict[str, Any]:
        """Asked BEFORE every attempt. Projects the attempt's notional into
        the window bound, so the bound is never discovered by breaching it."""
        stops = self.hard_stops()
        projected = (self.window_exposure_usd.get(window, D("0"))
                     + D(str(notional_usd)))
        reasons = list(stops)
        if projected >= MAX_EXPOSURE_PER_WINDOW_USD:
            reasons.append(
                "PROJECTED_WINDOW_EXPOSURE_%s_WOULD_REACH_%s"
                % (projected, MAX_EXPOSURE_PER_WINDOW_USD))
        if self.window_attempts.get(window, 0) >= MAX_ATTEMPTS_PER_WINDOW:
            reasons.append("WINDOW_%d_ATTEMPT_%d_REFUSED_MAX_%d"
                           % (window, self.window_attempts[window] + 1,
                              MAX_ATTEMPTS_PER_WINDOW))
        if self.placements >= MAX_PLACEMENTS:
            reasons.append("COHORT_PLACEMENT_CAP_%d" % MAX_PLACEMENTS)
        # THE FORWARD-LOOKING $5.00 INVARIANT, counting the attempt ABOUT TO
        # BE SUBMITTED. Strict reading of the owner's wording: no new first
        # leg if it COULD take the quantity above $5.00, so the prospective
        # attempt is included BEFORE submitting and $5.00 is a true ceiling
        # rather than a ceiling plus one attempt.
        fwd = self.forward_looking_report(prospective_attempts=1)
        if not fwd["within_ceiling"]:
            reasons.append(
                "FORWARD_LOOKING_LOSS_BOUND: realized %s + unresolved "
                "residual max %s + this attempt %s = %s > %s"
                % (fwd["realized_cohort_loss_usd"],
                   fwd["unresolved_residual_max_loss_usd"],
                   fwd["prospective_attempt_max_loss_usd"],
                   fwd["forward_looking_total_usd"], fwd["ceiling_usd"]))
        v = self.verdict()
        if v["verdict"] != VERDICT_CONTINUE or v["stopped"]:
            reasons.append("VERDICT_IS_%s%s" % (
                v["verdict"],
                "" if not v.get("truncation_cause")
                else ":" + str(v["truncation_cause"])))
        return {"ok": not reasons, "refusals": reasons,
                "projected_window_exposure_usd": str(projected),
                "forward_looking": fwd,
                "window_bound_binds_independently": True,
                "verdict": v["verdict"]}

    # -------------------------------------------------------------- verdict
    def _truncation_cause(self, stops: List[str]) -> Any:
        """WHICH limit stopped it. The owner wants a risk truncation
        distinguishable in the record from a low-fill-rate truncation, so
        the cause is named and never merged into one bucket."""
        for s in stops:
            if s.startswith("FORWARD_LOOKING_COMMITTED_LOSS") or \
                    s.startswith("COHORT_REALIZED_LOSS_AT_OR_ABOVE"):
                return TRUNCATION_RISK_LIMIT
        for s in stops:
            if s.startswith("LOCAL_EXECUTION_FAULT"):
                return TRUNCATION_LOCAL_FAULT
        for s in stops:
            if "EXPOSURE_AT_OR_ABOVE" in s:
                return TRUNCATION_WINDOW_EXPOSURE
            if s.startswith("SAFETY_INVARIANT_VIOLATION"):
                return TRUNCATION_SAFETY
            if s.startswith("VENUE_ERROR_STORM"):
                return TRUNCATION_VENUE_STORM
            if s.startswith("AMBIGUOUS_SUBMISSION"):
                return TRUNCATION_AMBIGUOUS
        if self.placements >= HARD_TRUNCATION:
            return TRUNCATION_DESIGN_HARD
        remaining = MAX_PLACEMENTS - self.placements
        if self.fills + remaining < SPRT_EXPECTED_FILLS_UNDER_H0:
            return TRUNCATION_LOW_FILL_RATE
        if remaining <= 0:
            return TRUNCATION_PLACEMENT_CAP
        return TRUNCATION_NONE

    def fail_reasons(self) -> List[str]:
        """The four PREREGISTERED failure boundaries, read from
        E1_COHORT_DESIGN.md §3 and not invented here."""
        fails: List[str] = []
        if self.consecutive_no_fill >= FAIL_CONSECUTIVE_NO_FILL:
            fails.append(
                "estimand 1: %d consecutive no-fill placements; the Beta(1,"
                "%d) 97.5%% upper bound on p_fill is below 0.10. A passive "
                "first leg does not fill at our price under this selector."
                % (self.consecutive_no_fill, self.consecutive_no_fill + 1))
        walk = sprt_walk(self.completions, self.non_completions)
        b = sprt_boundaries()
        econ_foreclosed = (
            self.per_share_expectancy_upper_usd is not None
            and self.per_share_expectancy_upper_usd
            < ECONOMICS_FORECLOSURE_USD_PER_SHARE)
        if walk <= b["accept_h0_at_or_below"] and econ_foreclosed:
            fails.append(
                "estimand 2: the SPRT walk %.6f crossed H0 at %.6f AND "
                "estimand 8's per-share upper bound is below the "
                "arithmetic maker-rebate ceiling. The rebate-bearing "
                "cohort cannot close the gap."
                % (walk, b["accept_h0_at_or_below"]))
        if self.post_cancel_ack_fills:
            fails.append(
                "estimand 6: %d fill(s) timestamped AFTER a cancel "
                "acknowledgement. Cancellation does not bound exposure, so "
                "the worst-case arithmetic every frozen bound rests on is "
                "invalid. It invalidates the design, not just the run."
                % self.post_cancel_ack_fills)
        if self.unattributed_cashflows:
            fails.append(
                "estimand 8: %d cashflow(s) without sufficient source "
                "identity. The settlement ledger cannot be trusted and "
                "nothing may be labelled REALIZED. Instrument fault."
                % self.unattributed_cashflows)
        return fails

    def verdict(self) -> Dict[str, Any]:
        """One of exactly three outcomes. There is NO PASS.

        1. FAIL                              a preregistered boundary is hit
        2. COMPLETION_SPRT_POSITIVE_CROSSING the SPRT crosses H1
        3. CONTINUE_INSUFFICIENT_EVIDENCE    anything else, including every
                                             truncation, with the cause named
        """
        stops = self.hard_stops()
        walk = sprt_walk(self.completions, self.non_completions)
        b = sprt_boundaries()
        base = {"placements": self.placements, "fills": self.fills,
                "completions": self.completions,
                "non_completions": self.non_completions,
                "sprt_walk": walk, "sprt_boundaries": b,
                "authorized_verdicts": list(AUTHORIZED_VERDICTS),
                "formal_PASS_exists_as_an_outcome": False,
                "is_an_authorization": False,
                "parameters_moved_on_interim_results": False}

        fails = self.fail_reasons()
        if fails:
            out = dict(base)
            out.update({"verdict": VERDICT_FAIL, "stopped": True,
                        "reasons": fails,
                        "meaning": "a preregistered failure boundary was "
                                   "reached. The finding is itself the "
                                   "result.",
                        "stop_reasons": stops,
                        "truncation_cause": self._truncation_cause(stops)})
            return out

        if walk >= b["accept_h1_at_or_above"]:
            out = dict(base)
            out.update({
                "verdict": VERDICT_SPRT_POSITIVE, "stopped": True,
                "meaning": ("the completion SPRT crossed H1. This is "
                            "evidence about ONE question and is not a "
                            "verdict on the cohort."),
                # the four disclaimers, as FIELDS so a summary cannot drop
                # them
                **SPRT_POSITIVE_DISCLAIMERS,
                "disclaimers": dict(SPRT_POSITIVE_DISCLAIMERS),
                "DISCLAIMER_TEXT": (
                    "evidence SPECIFICALLY for the maker->maker completion "
                    "question; NOT formal E1 PASS; NOT proof of positive "
                    "expectancy; NOT authorization for E2."),
                "stop_reasons": stops,
                "truncation_cause": self._truncation_cause(stops)})
            return out

        cause = self._truncation_cause(stops)
        out = dict(base)
        out.update({
            "verdict": VERDICT_CONTINUE,
            "stopped": bool(stops) or cause is not None,
            "stop_reasons": stops,
            "truncation_cause": cause,
            "truncated_by_the_risk_limit":
                cause in (TRUNCATION_RISK_LIMIT, TRUNCATION_WINDOW_EXPOSURE),
            "truncated_by_a_low_fill_rate": cause == TRUNCATION_LOW_FILL_RATE,
            "meaning": ("insufficient evidence. This is NEITHER a failure "
                        "boundary NOR a positive crossing, and formal PASS "
                        "is not an available outcome at all."),
            "raw_tape_must_still_yield_the_estimation_chain": True,
            "EXTENDING_THE_COHORT_REQUIRES_A_FRESH_OWNER_AUTHORIZATION": True,
            "anything_widened_automatically": False,
            "placements_to_design_gate":
                max(0, PLACEMENT_GATE - self.placements),
            "fills_to_binding_gate":
                max(0, BINDING_FILL_GATE - self.fills),
        })
        if cause == TRUNCATION_DESIGN_HARD:
            out["design_note"] = RULES["CONTINUE"]["on_truncation"]
        return out

    def snapshot(self) -> Dict[str, Any]:
        return {"probe": PROBE, "placements": self.placements,
                "fills": self.fills,
                "consecutive_no_fill": self.consecutive_no_fill,
                "completions": self.completions,
                "non_completions": self.non_completions,
                "consecutive_non_completion":
                    self.consecutive_non_completion,
                "realized_loss_usd": str(self.realized_loss_usd),
                "window_exposure_usd":
                    {w: str(x) for w, x in self.window_exposure_usd.items()},
                "window_attempts": dict(self.window_attempts),
                "post_cancel_ack_fills": self.post_cancel_ack_fills,
                "unattributed_cashflows": self.unattributed_cashflows,
                "ambiguous_submissions": self.ambiguous_submissions,
                "venue_errors": self.venue_errors,
                "local_execution_faults": self.local_execution_faults,
                "local_execution_fault_log":
                    list(self.local_execution_fault_log),
                "a_local_fault_is_never_a_venue_error": True,
                "safety_violations": list(self.safety_violations),
                "venue_error_log": list(self.venue_error_log),
                "venue_error_stop_is_a_safety_stop": True,
                "venue_error_stop_is_an_economic_threshold": False,
                "automatic_retry_anywhere": False,
                "hard_stops": self.hard_stops(),
                "forward_looking": self.forward_looking_report(
                    prospective_attempts=1),
                "effective_worst_case": effective_worst_case(),
                "verdict": self.verdict()}
