"""A gate that can only ever BLOCK live arming. It cannot arm anything.

WHY THE ASYMMETRY IS THE WHOLE DESIGN. The tempting version of this module
watches shadow expectancy and switches live trading on when it turns
positive. That version is forbidden, and not because it is hard: a positive
shadow mean is a measurement of a simulator against a book we did not trade,
over a window we chose after seeing it. Promoting on that alone is how a
strategy with no edge reaches production -- and on 2026-10-23 our own
282-window production shadow read -$0.2065 per settled window, so the
question is not hypothetical.

So the API has exactly one answer shape: `blocks_live`. True means REFUSE.
False means THIS GATE ADDS NO REFUSAL -- it does NOT mean "arm", and no
caller can read it as permission, because arming still requires the signed
mandate, the halt latch, the daily allowance, the validation gates, the
campaign budget and the cash gate, every one of which can still refuse. There
is deliberately no function here that returns a grant, no `permit`, no
`enable`, and no boolean whose True means "go".

WHAT IT TAKES TO STOP BLOCKING, and shadow alone is never enough:

  1. a shadow sample at least as large as the mandate's `min_settled_windows`;
  2. a shadow expectancy at least the mandate's `min_expectancy_usd`;
  3. an OUT-OF-SAMPLE evidence record whose id matches the mandate's
     `oos_evidence_id`, which is fresh, which states the fill economics it
     was computed under, and which is itself positive;
  4. a BOUNDED LIVE VALIDATION record against that same evidence id, with a
     venue-confirmed settled result.

(3) and (4) are the requirements the owner named and they are not derivable
from (1) and (2): in-sample shadow cannot establish out-of-sample behaviour,
and no amount of simulation establishes that our orders fill the way the
simulator assumed. A mandate cannot opt out of them --
`l5_mandate_signature` refuses any payload whose
`requires_bounded_live_validation` is not true.

FAIL CLOSED ON EVERYTHING. Missing, stale, unreadable, mismatched or
unparseable inputs all BLOCK. The only path to "no refusal from this gate" is
four positive, fresh, internally consistent records.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

#: how old an evidence or validation record may be and still count. Evidence
#: about a venue's microstructure decays; a year-old out-of-sample study is
#: not a statement about today's book.
DEFAULT_EVIDENCE_MAX_AGE_S = 30 * 24 * 3600.0

#: fill assumptions an OOS record may declare. CONSERVATIVE is the only one
#: that counts, and the allowlist is the code's, not the record's: a record
#: declaring OPTIMISTIC is evidence about a simulator, not about our fills.
ACCEPTED_FILL_BASES = ("CONSERVATIVE",)


def _d(v: Any) -> Optional[D]:
    if isinstance(v, bool):
        return None
    try:
        out = D(str(v))
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None
    return out if out.is_finite() else None


def _ts(v: Any) -> Optional[dt.datetime]:
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return t if t.tzinfo is not None else None


def _int(v: Any) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v


def evaluate(*, promotion: Any, shadow: Any, oos_evidence: Any = None,
             live_validation: Any = None,
             now: Optional[dt.datetime] = None,
             evidence_max_age_s: float = DEFAULT_EVIDENCE_MAX_AGE_S
             ) -> Dict[str, Any]:
    """`blocks_live` plus every reason it blocks. Default: blocks.

    `blocks_live: False` means ONLY that this gate contributes no refusal.
    It is never permission to trade.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    reasons: List[str] = []

    def block(r: str) -> None:
        if r not in reasons:
            reasons.append(r)

    # ---------------------------------------------------- the mandate terms
    if not isinstance(promotion, Mapping):
        block("PROMOTION_TERMS_MISSING")
        return _verdict(reasons, None, None)
    min_n = _int(promotion.get("min_settled_windows"))
    min_e = _d(promotion.get("min_expectancy_usd"))
    want_id = promotion.get("oos_evidence_id")
    if min_n is None or min_n < 1:
        block("PROMOTION_MIN_SAMPLE_INVALID")
    if min_e is None:
        block("PROMOTION_MIN_EXPECTANCY_INVALID")
    if not isinstance(want_id, str) or not want_id.strip():
        block("PROMOTION_OOS_EVIDENCE_ID_MISSING")
    # A mandate that did not demand bounded live validation must never have
    # verified; if one reaches here, that is a bug upstream and it blocks.
    if promotion.get("requires_bounded_live_validation") is not True:
        block("PROMOTION_LIVE_VALIDATION_NOT_REQUIRED")

    # ------------------------------------------------------- shadow (1)+(2)
    n_settled = exp = None
    if not isinstance(shadow, Mapping):
        block("SHADOW_STATS_MISSING")
    else:
        n_settled = _int(shadow.get("windows_settled"))
        exp = _d(shadow.get("net_per_settled_window"))
        if n_settled is None or n_settled < 0:
            block("SHADOW_SAMPLE_UNREADABLE")
        elif min_n is not None and n_settled < min_n:
            block("SHADOW_SAMPLE_TOO_SMALL")
        if exp is None:
            block("SHADOW_EXPECTANCY_UNREADABLE")
        elif min_e is not None and exp < min_e:
            block("SHADOW_EXPECTANCY_BELOW_THRESHOLD")

    # --------------------------------------------- out-of-sample evidence (3)
    if not isinstance(oos_evidence, Mapping):
        block("OOS_EVIDENCE_MISSING")
    else:
        got_id = oos_evidence.get("evidence_id")
        if not isinstance(got_id, str) or got_id != want_id:
            block("OOS_EVIDENCE_ID_MISMATCH")
        asof = _ts(oos_evidence.get("asof_utc"))
        if asof is None:
            block("OOS_EVIDENCE_TIMESTAMP_INVALID")
        elif (now - asof).total_seconds() > float(evidence_max_age_s):
            block("OOS_EVIDENCE_STALE")
        elif (now - asof).total_seconds() < 0:
            block("OOS_EVIDENCE_IN_THE_FUTURE")
        basis = oos_evidence.get("fill_basis")
        if not isinstance(basis, str) or basis.upper() not in \
                ACCEPTED_FILL_BASES:
            block("OOS_FILL_BASIS_NOT_ACCEPTED")
        oos_n = _int(oos_evidence.get("windows"))
        oos_net = _d(oos_evidence.get("net_per_window_usd"))
        if oos_n is None or oos_n < 1:
            block("OOS_EVIDENCE_SAMPLE_UNREADABLE")
        elif min_n is not None and oos_n < min_n:
            block("OOS_EVIDENCE_SAMPLE_TOO_SMALL")
        if oos_net is None:
            block("OOS_EVIDENCE_RESULT_UNREADABLE")
        elif min_e is not None and oos_net < min_e:
            block("OOS_EVIDENCE_BELOW_THRESHOLD")
        if oos_evidence.get("in_sample") is True:
            block("OOS_EVIDENCE_IS_IN_SAMPLE")

    # ------------------------------------------ bounded live validation (4)
    if not isinstance(live_validation, Mapping):
        block("LIVE_VALIDATION_MISSING")
    else:
        if live_validation.get("evidence_id") != want_id:
            block("LIVE_VALIDATION_EVIDENCE_MISMATCH")
        asof = _ts(live_validation.get("asof_utc"))
        if asof is None:
            block("LIVE_VALIDATION_TIMESTAMP_INVALID")
        elif (now - asof).total_seconds() > float(evidence_max_age_s):
            block("LIVE_VALIDATION_STALE")
        # SETTLED, VENUE-CONFIRMED, AND POSITIVE. A validation whose fills
        # are submitted-but-unsettled is not a result; `settlement_confirmed`
        # must be explicitly true rather than merely absent.
        if live_validation.get("settlement_confirmed") is not True:
            block("LIVE_VALIDATION_NOT_SETTLEMENT_CONFIRMED")
        lv_n = _int(live_validation.get("windows"))
        if lv_n is None or lv_n < 1:
            block("LIVE_VALIDATION_SAMPLE_UNREADABLE")
        lv_net = _d(live_validation.get("realized_net_usd"))
        if lv_net is None:
            block("LIVE_VALIDATION_RESULT_UNREADABLE")
        elif lv_net < 0:
            block("LIVE_VALIDATION_NEGATIVE")

    return _verdict(reasons, n_settled, exp)


def _verdict(reasons: List[str], n: Optional[int],
             exp: Optional[D]) -> Dict[str, Any]:
    return {
        # THE ONLY ANSWER THIS MODULE GIVES. True means refuse.
        "blocks_live": bool(reasons),
        "reasons": list(reasons),
        "observed_shadow_windows": n,
        "observed_shadow_expectancy_usd": (str(exp) if exp is not None
                                           else None),
        "labels": ("blocks_live False means THIS GATE ADDS NO REFUSAL. It is "
                   "not permission to trade: the signed mandate, halt latch, "
                   "daily allowance, validation gates, campaign budget and "
                   "cash gate each still refuse independently."),
    }


def telemetry(verdict: Mapping[str, Any]) -> Dict[str, Any]:
    """Safe to log, and names the posture so a reader cannot mistake a
    non-blocking verdict for an armed one."""
    return {"blocks_live": bool(verdict.get("blocks_live")),
            "reasons": list(verdict.get("reasons") or [])[:12],
            "observed_shadow_windows": verdict.get("observed_shadow_windows"),
            "observed_shadow_expectancy_usd":
                verdict.get("observed_shadow_expectancy_usd"),
            "can_this_gate_arm_anything": False,
            "enforcing": False}
