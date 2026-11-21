"""Whether a decision would OPEN new exposure or COMPLETE existing inventory.

REPORTING ONLY. Nothing here is a gate. It exists because an operator
reading `L5_LIVE_DECISION` rows could not tell the two apart.

WHY `legs_claimed` IS NOT THE ANSWER. The obvious discriminator is
`two_sided.legs_claimed`, and it is wrong: when a round retires,
`legs_claimed` resets to all-False, so a complement attempt against inventory
from an earlier round reads as an opening. Measured on the preserved tapes,
all 156 `TWO_SIDED_PROBE:COMPLEMENT_PAIR_ABOVE_CAP` rows -- complement
attempts by the refusal's own name -- carry `legs_claimed` all-False or None.

WHY NOT REUSE `submission_class` DIRECTLY. That function is authoritative and
charges the durable class budgets, but it needs a translated order (`tr`) and
so cannot answer for a decision refused before translation -- which is most
of them. This answers from REALIZED inventory and the candidate side alone,
so it is available on every decision, including the earliest refusals.

The vocabulary is deliberately the operator's (`NEW_OPEN` / `COMPLEMENT`),
not the ledger's (`OPEN` / `COMPLETE` / `REPLACE`). A row's ledger class is
only decided at submission; this is what the decision would DO to the
imbalance. They agree on effect and must not be confused for one another,
which is why the field name and the values differ.
"""
from decimal import Decimal as D, InvalidOperation
from typing import Any, Optional

NEW_OPEN = "NEW_OPEN"
COMPLEMENT = "COMPLEMENT"
FLAT_OPEN = "FLAT_OPEN"
UNKNOWN = "UNKNOWN"

ROLES = (NEW_OPEN, COMPLEMENT, FLAT_OPEN, UNKNOWN)

_UP = ("UP", "YES")
_DOWN = ("DOWN", "NO")


def _side(side: Any) -> Optional[bool]:
    """True for UP, False for DOWN, None if unreadable."""
    s = str(side or "").strip().upper()
    if s in _UP:
        return True
    if s in _DOWN:
        return False
    return None


def _dec(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def decision_role(*, residual: Any, side: Any) -> str:
    """What this side would do to the REALIZED unmatched imbalance.

    `residual` is signed in UP terms: positive means unmatched UP is held,
    negative means unmatched DOWN. That is the convention
    `L5_RESIDUAL_BOOK_SAMPLE` already reports (`residual: "-1.77"` with
    `held_side: "DOWN"`).

    FLAT_OPEN when nothing is held, so the row is distinguishable from an
    opening taken while inventory exists -- those are the post-pair legs that
    escaped the first-leg floor, and collapsing them into NEW_OPEN would hide
    exactly the case worth watching.

    UNKNOWN, never a guess, when either input is unreadable. A reporting
    field that silently invents a class is worse than one that admits it does
    not know.
    """
    up = _side(side)
    r = _dec(residual)
    if up is None or r is None:
        return UNKNOWN
    if r == 0:
        return FLAT_OPEN
    # holding unmatched UP and buying UP increases it; buying DOWN reduces it
    return NEW_OPEN if ((r > 0) == up) else COMPLEMENT


def role_of_decision(dec: Any) -> str:
    """`decision_role` for an already-built decision mapping, fail-open.

    Used where a decision row is assembled and a raising reporting field
    would be a far worse bug than a missing one.
    """
    try:
        if not hasattr(dec, "get"):
            return UNKNOWN
        return decision_role(residual=dec.get("residual"),
                             side=dec.get("side"))
    except BaseException:                                      # noqa: BLE001
        return UNKNOWN
