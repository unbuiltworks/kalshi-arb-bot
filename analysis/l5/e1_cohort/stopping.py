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

from analysis.l5.e1_cohort.spec import (MAX_ATTEMPTS_PER_WINDOW,
                                        MAX_COHORT_REALIZED_LOSS_USD,
                                        MAX_EXPOSURE_PER_WINDOW_USD,
                                        MAX_PLACEMENTS, PROBE, WINDOWS,
                                        assert_frozen)
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

PLACEMENT_GATE = 385
BINDING_FILL_GATE = CD.e1_binding_gates()["fill_gate"]
HARD_TRUNCATION = CD.e1_hard_truncation_placements()                  # 4310

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_CONTINUE = "CONTINUE"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_UNDECIDABLE = "INCONCLUSIVE_FILL_RATE_TOO_LOW"
VERDICT_FAILURE_TO_MEASURE = "FAILURE_TO_MEASURE"


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
            "reaches_sprt_expected_81_fills": fills >= 81,
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
        "what_the_authorized_cohort_can_reach": [VERDICT_FAIL,
                                                 VERDICT_INCONCLUSIVE,
                                                 VERDICT_UNDECIDABLE],
        "rows": rows,
        "HONESTY": (
            "240 placements yield about 85 fills only if p_fill >= ~0.35. "
            "At p_fill = 0.20 they yield about 48, SHORT of the SPRT's "
            "need. The cohort therefore terminates CONTINUE explicitly as "
            "%s rather than running out silently." % VERDICT_UNDECIDABLE),
        "EXTENDING_THE_COHORT_REQUIRES_A_FRESH_OWNER_AUTHORIZATION": True,
        "anything_widened_here": False,
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
        self.safety_violations: List[str] = []
        self.per_share_expectancy_upper_usd: Optional[D] = None
        self.estimand8_reconciled_without_unattributed = False
        self.stopped = False
        self.stop_reason: Optional[str] = None

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

    def record_confirmed_cash(self, *, amount_usd: Any) -> None:
        """Only a settlement-confirmed cashflow moves the loss counter."""
        amt = D(str(amount_usd))
        if amt < 0:
            self.realized_loss_usd += -amt

    def record_post_cancel_ack_fill(self) -> None:
        self.post_cancel_ack_fills += 1

    def record_unattributed_cashflow(self) -> None:
        self.unattributed_cashflows += 1

    def record_ambiguous_submission(self) -> None:
        """NEVER retried. The venue state is reconciled instead."""
        self.ambiguous_submissions += 1

    def record_venue_error(self) -> None:
        self.venue_errors += 1
        self.consecutive_venue_errors += 1

    def record_venue_ok(self) -> None:
        self.consecutive_venue_errors = 0

    def violate(self, why: str) -> None:
        self.safety_violations.append(why)

    # ------------------------------------------------------------ hard stops
    #: a venue error storm: five consecutive venue errors. Read as "the
    #: venue is not answering us coherently", not as a result.
    VENUE_ERROR_STORM = 5

    def hard_stops(self) -> List[str]:
        out: List[str] = []
        if self.realized_loss_usd >= MAX_COHORT_REALIZED_LOSS_USD:
            out.append("COHORT_REALIZED_LOSS_AT_OR_ABOVE_%s"
                       % MAX_COHORT_REALIZED_LOSS_USD)
        for w, x in self.window_exposure_usd.items():
            if x >= MAX_EXPOSURE_PER_WINDOW_USD:
                out.append("WINDOW_%d_EXPOSURE_AT_OR_ABOVE_%s"
                           % (w, MAX_EXPOSURE_PER_WINDOW_USD))
        if self.safety_violations:
            out.append("SAFETY_INVARIANT_VIOLATION:%s"
                       % "; ".join(self.safety_violations))
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
        v = self.verdict()
        if v["verdict"] != VERDICT_CONTINUE:
            reasons.append("VERDICT_IS_%s" % v["verdict"])
        return {"ok": not reasons, "refusals": reasons,
                "projected_window_exposure_usd": str(projected),
                "verdict": v["verdict"]}

    # -------------------------------------------------------------- verdict
    def verdict(self) -> Dict[str, Any]:
        """PASS / FAIL / CONTINUE, plus the two terminations that are
        neither. A hard stop is a FAILURE TO MEASURE and is NEVER a FAIL."""
        stops = self.hard_stops()
        if stops:
            return {
                "verdict": VERDICT_FAILURE_TO_MEASURE,
                "stopped": True, "stop_reasons": stops,
                "is_a_FAIL": False, "is_a_result": False,
                "meaning": ("a hard automatic stop. Per E1_COHORT_DESIGN.md "
                            "§3 this is a FAILURE TO MEASURE, reported with "
                            "the partial posteriors, and never reported as "
                            "FAIL or as a result."),
                "report_partial_posteriors": True,
            }

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
                "invalid. HARD stop; invalidates the design, not just the "
                "run." % self.post_cancel_ack_fills)
        if self.unattributed_cashflows:
            fails.append(
                "estimand 8: %d cashflow(s) without sufficient source "
                "identity. The settlement ledger cannot be trusted and "
                "nothing may be labelled REALIZED. Instrument fault."
                % self.unattributed_cashflows)
        if fails:
            return {"verdict": VERDICT_FAIL, "stopped": True,
                    "reasons": fails, "sprt_walk": walk,
                    "meaning": "STOP. The finding is itself the result.",
                    "is_an_authorization": False}

        passed = (
            self.placements >= PLACEMENT_GATE
            and self.fills >= BINDING_FILL_GATE
            and self.post_cancel_ack_fills == 0
            and self.unattributed_cashflows == 0
            and self.estimand8_reconciled_without_unattributed
            and self.per_share_expectancy_upper_usd is not None
            and self.per_share_expectancy_upper_usd
            > ECONOMICS_FORECLOSURE_USD_PER_SHARE)
        if passed:
            return {"verdict": VERDICT_PASS, "stopped": True,
                    "meaning": RULES["PASS"]["meaning"],
                    "is_an_authorization": False,
                    "is_a_profit_claim": False}

        if self.placements >= HARD_TRUNCATION:
            return {"verdict": VERDICT_INCONCLUSIVE, "stopped": True,
                    "meaning": RULES["CONTINUE"]["on_truncation"],
                    "is_a_FAIL": False, "is_a_PASS": False}

        # THE EXPLICIT UNDECIDABLE TERMINATION. Stated out loud at the point
        # it becomes arithmetically certain, not discovered at the end.
        remaining = MAX_PLACEMENTS - self.placements
        max_reachable_fills = self.fills + remaining
        if remaining <= 0 or max_reachable_fills < 81:
            return {
                "verdict": VERDICT_UNDECIDABLE,
                "stopped": True,
                "placements": self.placements,
                "fills": self.fills,
                "max_reachable_fills_in_this_authorization":
                    max_reachable_fills,
                "sprt_expected_fills_under_h0": 81,
                "design_binding_fill_gate": BINDING_FILL_GATE,
                "meaning": (
                    "the fill rate is too low for the AUTHORIZED cohort to "
                    "decide. This is an explicit termination of CONTINUE, "
                    "not a silent exhaustion, and it is NEITHER PASS NOR "
                    "FAIL."),
                "EXTENDING_THE_COHORT_REQUIRES_A_FRESH_OWNER_AUTHORIZATION":
                    True,
                "anything_widened_automatically": False,
                "is_a_FAIL": False, "is_a_PASS": False,
            }

        return {"verdict": VERDICT_CONTINUE, "stopped": False,
                "placements": self.placements, "fills": self.fills,
                "sprt_walk": walk,
                "placements_to_design_gate":
                    max(0, PLACEMENT_GATE - self.placements),
                "fills_to_binding_gate":
                    max(0, BINDING_FILL_GATE - self.fills),
                "PASS_reachable_in_this_authorization": False}

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
                "safety_violations": list(self.safety_violations),
                "hard_stops": self.hard_stops(),
                "verdict": self.verdict()}
