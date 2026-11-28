"""Reconcile VENUE EQUITY against the internal ledger. READ-ONLY, pure.

WHY THIS EXISTS. The account-wide loss limits read
`realized_attributed`, which is an ESTIMATE: on 2026-11-18 the BTC15m
ledger held 447 settled rows totalling -$167.2927, and 146 of the 254
UNKNOWN-origin rows were priced at the $1/share upper bound because the
ledger never saw their fills. Independent venue measurements over the
same period are about a third of that. Two numbers that disagree by 3x
cannot both drive a halt, and the ledger's own publisher says so: those
rows are "a bound and not a result".

This module does ONE thing: given venue equity observations and ledger
lines, it states what is MEASURED, what is still a BOUND, and what is
UNKNOWN -- separately, and without ever summing them into one figure
that could be mistaken for a result.

FOUR RULES, each of which exists because its absence produces a wrong
number in the permissive direction:

  1. EQUITY IS COMPARABLE ONLY BETWEEN TWO VERIFIED-FLAT INSTANTS.
     Buying power at a moment when positions are open reflects those
     positions' margin, not realized cash. Differencing a flat instant
     against a non-flat one silently books open risk as a result.
     Non-flat endpoints return UNCOMPARABLE rather than a number.

  2. INTERVALS ARE NEVER SUMMED OR MERGED. Two observation windows are
     two facts. The owner's ~$347 -> $322 and the lane's $334.37 ->
     $324.00 are different windows; adding them double-counts their
     overlap and averaging them invents a third window that nobody
     observed. Each interval is reported on its own, with its own
     timestamps.

  3. EQUITY CHANGE IS NOT TRADING P&L. Between two flat instants,
     cash moved by settled trading P&L PLUS credited incentives MINUS
     fees, plus any deposit or withdrawal. Omitting the incentive term
     makes trading look worse by exactly the rewards credited; omitting
     fees makes it look better. Both are carried explicitly, and an
     unknown term makes the residual UNKNOWN rather than zero.

  4. AN INCOMPLETE RECONCILIATION NEVER RELAXES A LIMIT. While any leg
     is unestablished, `conservative_figure_stays_authoritative` is
     True. The corrected figure is reported BESIDE the conservative one,
     never in place of it. Replacing the authoritative figure is an
     owner decision taken on a COMPLETE reconciliation, not an
     arithmetic consequence of a partial one.

WHAT IT DOES NOT DO. It computes no correction, writes no ledger key and
authorizes nothing. Applying an authenticated cost basis is the
InventoryStore verb that `l5_cost_basis_audit.apply_cost_basis_corrections`
reaches, which is owner-gated and already has its own guards. This module
only measures the gap those guards would close.

THE VERB IS NAMED INDIRECTLY ABOVE, ON PURPOSE. Its own test module
holds an architectural guard that greps every line of src/*.py for that
identifier and requires the set of files containing it to equal the
sanctioned callers. To a grep, a docstring mention is indistinguishable
from a call -- so spelling the identifier here would add this module to
that set, and the only ways out would be to widen the sanctioned list or
to soften the guard. Both would weaken a real protection to accommodate a
comment. Describing the verb in prose keeps the guard exact.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

ZERO = D("0")

#: the placeholder price a row of UNKNOWN origin was booked at
UPPER_BOUND_PRICE = D("1")
BASIS_UPPER_BOUND = "UPPER_BOUND_1USD_PER_CONTRACT"
BASIS_AUTHENTICATED = "AUTHENTICATED_FILLS"


def _d(v: Any) -> Optional[D]:
    """Decimal or None. None is never read as zero anywhere below."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return x if x.is_finite() else None


def _t(v: Any) -> Optional[dt.datetime]:
    try:
        out = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=dt.timezone.utc)


def equity_interval(start: Mapping[str, Any],
                    end: Mapping[str, Any]) -> Dict[str, Any]:
    """One observed equity interval, or why it is not comparable.

    Each observation is {"t": iso, "usd": x, "flat": bool}. `flat` means
    the account was verified to hold no position at that instant --
    RULE 1. It is required to be explicitly True; a missing flag is not
    an assertion of flatness and refuses.
    """
    out: Dict[str, Any] = {
        "from_t": None, "to_t": None,
        "from_usd": None, "to_usd": None,
        "change_usd": None, "comparable": False, "reason": None,
    }
    t0, t1 = _t(start.get("t")), _t(end.get("t"))
    v0, v1 = _d(start.get("usd")), _d(end.get("usd"))
    out["from_t"] = None if t0 is None else t0.isoformat()
    out["to_t"] = None if t1 is None else t1.isoformat()
    out["from_usd"] = None if v0 is None else str(v0)
    out["to_usd"] = None if v1 is None else str(v1)
    if t0 is None or t1 is None:
        out["reason"] = "TIMESTAMP_UNREADABLE"
        return out
    if v0 is None or v1 is None:
        out["reason"] = "EQUITY_UNREADABLE"
        return out
    if t1 <= t0:
        out["reason"] = "INTERVAL_NOT_FORWARD"
        return out
    if start.get("flat") is not True or end.get("flat") is not True:
        #: RULE 1. Open positions make buying power incomparable.
        out["reason"] = "ENDPOINT_NOT_VERIFIED_FLAT"
        out["flat"] = {"from": start.get("flat"), "to": end.get("flat")}
        return out
    out.update(comparable=True, change_usd=str(v1 - v0),
               seconds=(t1 - t0).total_seconds())
    return out


def attribute_equity_change(interval: Mapping[str, Any], *,
                            settled_pnl_usd: Any = None,
                            fees_usd: Any = None,
                            incentives_credited_usd: Any = None,
                            transfers_usd: Any = None) -> Dict[str, Any]:
    """Split a comparable equity change into its named parts -- RULE 3.

    change = settled_pnl - fees + incentives + transfers + residual

    Every term is required to be READABLE. A term that is None leaves
    the residual UNKNOWN, because an unknown cash movement is not a
    zero one: booking it as zero would attribute somebody's deposit to
    trading skill.

    Fees are given as a POSITIVE cost and subtracted.
    """
    out: Dict[str, Any] = {"residual_usd": None, "ok": False,
                           "reason": None, "terms": {}}
    if not interval.get("comparable"):
        out["reason"] = "INTERVAL_NOT_COMPARABLE"
        return out
    change = _d(interval.get("change_usd"))
    if change is None:
        out["reason"] = "CHANGE_UNREADABLE"
        return out
    terms = {"settled_pnl_usd": _d(settled_pnl_usd),
             "fees_usd": _d(fees_usd),
             "incentives_credited_usd": _d(incentives_credited_usd),
             "transfers_usd": _d(transfers_usd)}
    out["terms"] = {k: (None if v is None else str(v))
                    for k, v in terms.items()}
    missing = sorted(k for k, v in terms.items() if v is None)
    if missing:
        out["reason"] = "TERM_UNREADABLE"
        out["missing"] = missing
        return out
    residual = (change - terms["settled_pnl_usd"] + terms["fees_usd"]
                - terms["incentives_credited_usd"]
                - terms["transfers_usd"])
    out.update(ok=True, residual_usd=str(residual),
               change_usd=str(change),
               labels=("residual is cash the named terms do not explain. "
                       "It is NOT profit and NOT a correction."))
    return out


def upper_bound_overstatement(legs: Sequence[Mapping[str, Any]]
                              ) -> Dict[str, Any]:
    """How much of the booked loss the $1 placeholder could be hiding.

    One entry per leg: {"key", "qty", "basis", "measured_cost_usd"}.
    `measured_cost_usd` is the AUTHENTICATED cost from
    `l5_cost_basis_audit`; None means the audit could not establish it.

    For an established leg the overstatement is qty*$1 - measured_cost,
    which is >= 0 because no fill prices above $1. A leg whose measured
    cost is absent contributes NOTHING to `resolvable_usd` and is
    counted in `unresolved_qty` instead -- RULE 4, and the audit's own
    "missing is never zero".

    A leg already on AUTHENTICATED_FILLS is not a placeholder and is
    skipped rather than re-counted.
    """
    resolvable = ZERO
    unresolved_qty = ZERO
    bound_qty = ZERO
    established: List[str] = []
    unestablished: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for leg in legs:
        key = str(leg.get("key") or "")
        basis = str(leg.get("basis") or "")
        if basis != BASIS_UPPER_BOUND:
            skipped.append(key)
            continue
        qty = _d(leg.get("qty"))
        if qty is None or qty < 0:
            unestablished.append({"key": key, "why": "QTY_UNREADABLE"})
            continue
        bound_qty += qty
        measured = _d(leg.get("measured_cost_usd"))
        if measured is None:
            unresolved_qty += qty
            unestablished.append({"key": key, "why": "COST_UNESTABLISHED"})
            continue
        placeholder = qty * UPPER_BOUND_PRICE
        if measured > placeholder:
            #: a measured cost ABOVE the bound contradicts the bound's
            #: definition; it is evidence of a bad read, not a correction.
            unestablished.append({"key": key,
                                  "why": "MEASURED_ABOVE_UPPER_BOUND",
                                  "measured_cost_usd": str(measured),
                                  "placeholder_usd": str(placeholder)})
            unresolved_qty += qty
            continue
        resolvable += placeholder - measured
        established.append(key)
    complete = not unestablished
    return {
        "placeholder_legs": len(established) + len(unestablished),
        "established_legs": len(established),
        "unestablished_legs": len(unestablished),
        "unestablished": unestablished,
        "skipped_not_placeholder": len(skipped),
        "bound_qty": str(bound_qty),
        "unresolved_qty": str(unresolved_qty),
        #: the amount authenticated prices WOULD move realized toward
        #: zero. Positive means the ledger overstates the loss.
        "resolvable_usd": str(resolvable),
        "complete": complete,
        #: RULE 4
        "conservative_figure_stays_authoritative": not complete,
        "labels": ("resolvable_usd is what AUTHENTICATED fills prove. It "
                   "is not applied here and relaxes no limit. While "
                   "complete is false the conservative figure remains "
                   "authoritative."),
    }


def reconcile(*, intervals: Sequence[Mapping[str, Any]],
              ledger_realized_attributed_usd: Any,
              ledger_realized_unattributed_usd: Any = 0,
              overstatement: Optional[Mapping[str, Any]] = None
              ) -> Dict[str, Any]:
    """The reconciled statement. Intervals stay separate -- RULE 2.

    `intervals` is a sequence of already-attributed intervals, each the
    output of `attribute_equity_change` merged with its
    `equity_interval`. They are reported as a LIST and never combined.
    """
    attributed = _d(ledger_realized_attributed_usd)
    unattributed = _d(ledger_realized_unattributed_usd)
    out: Dict[str, Any] = {
        "intervals": list(intervals),
        "interval_count": len(intervals),
        "comparable_intervals": sum(1 for i in intervals
                                    if i.get("comparable")),
        "ledger": {
            "realized_attributed_usd": (None if attributed is None
                                        else str(attributed)),
            "realized_unattributed_usd": (None if unattributed is None
                                          else str(unattributed)),
        },
        "overstatement": (None if overstatement is None
                          else dict(overstatement)),
        "corrected_realized_attributed_usd": None,
        "conservative_figure_stays_authoritative": True,
        "labels": ("intervals are separate observations and are never "
                   "summed. The corrected figure is reported BESIDE the "
                   "conservative one, never instead of it."),
    }
    if attributed is None:
        out["reason"] = "LEDGER_REALIZED_UNREADABLE"
        return out
    if overstatement is None:
        out["reason"] = "NO_OVERSTATEMENT_EVIDENCE"
        return out
    resolvable = _d(overstatement.get("resolvable_usd"))
    if resolvable is None:
        out["reason"] = "RESOLVABLE_UNREADABLE"
        return out
    #: moving TOWARD zero: a loss of -167 with 20 of overstatement is -147
    out["corrected_realized_attributed_usd"] = str(attributed + resolvable)
    out["still_a_bound_for_legs"] = overstatement.get("unestablished_legs")
    out["conservative_figure_stays_authoritative"] = not bool(
        overstatement.get("complete"))
    return out
