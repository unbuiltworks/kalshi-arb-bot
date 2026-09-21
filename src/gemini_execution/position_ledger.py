"""Our position, the VENUE's position, and the refusal when they disagree.

THE PROBLEM THIS EXISTS FOR. `Inventory(yes_qty, no_qty)` can represent a
state Gemini's published schemas say the venue does not carry. The settled
positions reference defines the field as "Signed position held at settlement.
Positive values represent a `yes` position; negative values represent a `no`
position", and the `positions@account` stream carries ONE signed `position`
per instrument symbol with no outcome dimension at all. Against that, the open
`getPositions` example shows two rows on one instrumentId. We have never read
a Gemini position of our own, so WHICH ONE IS TRUE IS UNRESOLVED.

WHAT THIS MODULE DOES ABOUT IT, WHICH IS NOT TO GUESS. It reduces both
candidate shapes to the ONE quantity that is well defined under either --
the signed position -- reconciles it against our own ledger, and RECORDS WHICH
SHAPE THE VENUE ACTUALLY SENT. So the first time it runs against the live
account it answers the open question as a by-product of doing its job, rather
than requiring a separate investigation:

    PER_OUTCOME_ROWS  two rows for one symbol, yes and no      -> model A
    SIGNED_SCALAR     one row whose sign is the outcome        -> model B
    FLAT_NO_ROWS      no row while our ledger says we hold     -> B or C
    UNREADABLE        refuse; an unread venue is never a flat one

IT FAILS CLOSED. `reconcile` never returns a number it could not establish.
Unreadable is UNREADABLE and disagreement is DISAGREE, and both are refusals
-- the caller stops rather than trading on a position it cannot confirm.

WHY SIGNED IS THE RIGHT COMMON DENOMINATOR. Under model A the account's
economic exposure on a contract is still yes_qty - no_qty; the matched part
redeems 1.00 against itself whichever way the contract resolves. Under model B
it is the only thing that exists. So a ledger that reconciles on the SIGNED
quantity is correct under both, and one that reconciles on the pair is correct
under at most one.

MATCHED QUANTITY IS REPORTED, NEVER RECONCILED. It is carried here as
`our_matched` and explicitly labelled unverifiable against the venue until the
representation is resolved, so that no caller can quietly treat it as
confirmed state.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_POSITION_LEDGER_V1"

PER_OUTCOME_ROWS = "PER_OUTCOME_ROWS"
SIGNED_SCALAR = "SIGNED_SCALAR"
FLAT_NO_ROWS = "FLAT_NO_ROWS"
UNREADABLE = "UNREADABLE"

AGREE = "AGREE"
DISAGREE = "DISAGREE"

#: which venue shape implies which of the models in
#: analysis/gemini/EVIDENCE_PACKET_003_VENUE_SEMANTICS.md
MODEL_OF = {
    PER_OUTCOME_ROWS: "A -- independent YES and NO inventories coexist",
    SIGNED_SCALAR: "B -- one signed position; the opposite outcome nets",
    FLAT_NO_ROWS: "B or C -- nothing is held, so it netted or was cashed out",
    UNREADABLE: "UNRESOLVED -- the read did not establish anything",
}


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def signed_position(inv: Any) -> D:
    """OUR signed position: long YES positive, long NO negative."""
    return D(str(inv.yes_qty)) - D(str(inv.no_qty))


def _rows_for(rows: Sequence[Mapping[str, Any]], symbol: str
              ) -> List[Mapping[str, Any]]:
    want = str(symbol).upper()
    out = []
    for r in rows or ():
        s = str(r.get("instrumentSymbol") or r.get("symbol") or "").upper()
        if s == want:
            out.append(r)
    return out


def venue_signed(rows: Optional[Sequence[Mapping[str, Any]]], symbol: str
                 ) -> Dict[str, Any]:
    """Reduce whatever shape the venue sent to ONE signed quantity.

    `rows is None` means the read did not succeed. That is UNREADABLE, and it
    is emphatically not zero: an account whose positions could not be read is
    not an account that holds nothing.
    """
    if rows is None:
        return {"representation": UNREADABLE, "signed": None,
                "why": "positions were not readable; an unread venue is "
                       "never a flat one"}
    mine = _rows_for(rows, symbol)
    if not mine:
        return {"representation": FLAT_NO_ROWS, "signed": D("0"),
                "rows": 0,
                "why": "the venue returned no row for this instrument"}

    # A row that carries a SIGNED `position` says so itself. A row that
    # carries only an unsigned quantity plus an `outcome` has to be signed
    # from the outcome -- and a row that carries neither is unreadable, not
    # assumed.
    signed = D("0")
    outcomes = set()
    saw_signed_field = False
    for r in mine:
        outcome = str(r.get("outcome") or "").lower()
        if outcome in ("yes", "no"):
            outcomes.add(outcome)
        if r.get("position") is not None:
            v = _dec(r.get("position"))
            if v is None:
                return {"representation": UNREADABLE, "signed": None,
                        "why": "a row's `position` could not be read: %r"
                               % (r.get("position"),)}
            saw_signed_field = True
            signed += v
            continue
        q = _dec(r.get("totalQuantity") if r.get("totalQuantity") is not None
                 else r.get("positionQuantity"))
        if q is None:
            return {"representation": UNREADABLE, "signed": None,
                    "why": "a row carried neither a signed `position` nor a "
                           "readable quantity: %r" % (dict(r),)}
        if outcome == "yes":
            signed += q
        elif outcome == "no":
            signed -= q
        else:
            return {"representation": UNREADABLE, "signed": None,
                    "why": "a row carried a quantity but no outcome, so its "
                           "sign is unknown: %r" % (dict(r),)}

    rep = SIGNED_SCALAR
    if len(mine) > 1 and outcomes == {"yes", "no"}:
        # BOTH OUTCOMES, ONE INSTRUMENT. This is the observation that settles
        # the open question, and it is recorded as such.
        rep = PER_OUTCOME_ROWS
    return {"representation": rep, "signed": signed, "rows": len(mine),
            "outcomes": sorted(outcomes),
            "carried_signed_field": saw_signed_field,
            "why": None}


def reconcile(inv: Any, rows: Optional[Sequence[Mapping[str, Any]]],
              symbol: str) -> Dict[str, Any]:
    """Our ledger against the venue's own position. Fails closed.

    `verdict` is AGREE only when the venue was READ and its signed position
    equals ours exactly. Everything else -- unreadable, disagreement, a shape
    that could not be reduced -- is a refusal, and `tradeable` is False.
    """
    v = venue_signed(rows, symbol)
    ours = signed_position(inv)
    out: Dict[str, Any] = {
        "label": LABEL, "symbol": symbol,
        "our_signed": str(ours),
        "our_yes_qty": str(inv.yes_qty), "our_no_qty": str(inv.no_qty),
        "our_matched": str(inv.matched()),
        "venue_representation": v["representation"],
        "venue_signed": (None if v["signed"] is None else str(v["signed"])),
        "implies_model": MODEL_OF.get(v["representation"]),
        "matched_is_verified": False,
        "matched_label": (
            "our_matched is OUR OWN bookkeeping. Until the venue "
            "representation is resolved it is NOT confirmed state and must "
            "not be reported as Gemini economics."),
    }
    if v["signed"] is None:
        out.update(verdict=UNREADABLE, tradeable=False, why=v.get("why"))
        return out
    if v["signed"] != ours:
        out.update(
            verdict=DISAGREE, tradeable=False,
            why=("venue signed position %s does not equal our ledger's %s; "
                 "the account state is not established and nothing may be "
                 "decided on it" % (v["signed"], ours)))
        return out
    out.update(verdict=AGREE, tradeable=True, why=None)
    # AGREEMENT ON THE SIGNED QUANTITY IS NOT AGREEMENT ON THE SHAPE. A flat
    # ledger agrees with a flat venue under every model, and says nothing.
    if v["representation"] == PER_OUTCOME_ROWS:
        out["resolves_open_question"] = (
            "the venue returned BOTH outcomes for one instrument: model A. "
            "Inventory(yes_qty, no_qty) is faithful and matched inventory is "
            "real venue state.")
    elif v["representation"] == SIGNED_SCALAR and ours != 0:
        out["resolves_open_question"] = (
            "the venue returned ONE signed row while we held a position: "
            "model B. Matched inventory is not venue state and every "
            "two-leg metric derived from it is invalid.")
    elif v["representation"] == FLAT_NO_ROWS and D(str(inv.matched())) > 0:
        out["resolves_open_question"] = (
            "our ledger holds a matched pair and the venue holds NO row for "
            "the instrument: model B or C. The pair does not exist at the "
            "venue.")
    return out
