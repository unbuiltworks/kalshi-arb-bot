"""Probe 09 candidate: classify on realized inventory PLUS what is still out.

OFFLINE / SHADOW ONLY. Nothing here is wired to a live path, and it changes
nothing about L5_NATIVE -- the frozen rule still chooses side, price, size
and timing. This is a SAFETY_OVERLAY refusal, like Probe 08's, differing in
one respect: what counts as the residual it refuses to deepen.

WHY
---
Probe 08 classified on REALIZED residual, as specified, and its journal shows
the consequence exactly. Both DOWN opens carry `residual_before: "0"` and
were classified FLAT_OPEN -- the second because the first had not yet
FILLED. Two same-side orders submitted inside one unfilled window both look
like opening from flat, and that is how a rule against deepening imbalance
permitted a 3.93 imbalance.

THE CANDIDATE
-------------
An outstanding order reserves DIRECTION as well as risk:

    prospective residual = realized residual + signed outstanding, where
    every live order is counted at its full remaining quantity

`risk_exposure_usd` already prices an outstanding order as if it fills in
full. This makes the inventory classification agree with the risk bound
rather than lag it.

WHAT IT COSTS, STATED HERE BECAUSE THE REPLAY FOUND IT
------------------------------------------------------
Reserving direction from an order that has not filled means an UNFILLED
order consumes repair capacity. On Probe 08's own stream this rule suppresses
the second DOWN open -- the defect it was designed for -- and then also
suppresses every repair that actually filled, because two earlier repairs
were still resting and had already reserved the direction. The window ends
worse, not better. That is measured in `l5_probe09_counterfactual.py`, and it
is the reason this module is not a proposal.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src.l5_live_execution import (FLAT_OPEN, INCREASE_RESIDUAL,
                                   REDUCE_RESIDUAL, inventory_class)

REALIZED = "REALIZED"
PROSPECTIVE = "REALIZED_PLUS_OUTSTANDING"


def prospective_residual(realized: D, outstanding: D) -> D:
    """Residual if every live order filled in full, right now."""
    return D(str(realized)) + D(str(outstanding))


def classify(realized: D, outstanding: D, side: str, *,
             basis: str = PROSPECTIVE) -> str:
    """The class this basis would assign. `REALIZED` reproduces Probe 08."""
    if basis == REALIZED:
        return inventory_class(D(str(realized)), side)
    if basis != PROSPECTIVE:
        raise ValueError("unknown basis %r" % (basis,))
    return inventory_class(prospective_residual(realized, outstanding), side)


def decide(realized: D, outstanding: D, side: str, *,
           basis: str = PROSPECTIVE) -> Dict[str, Any]:
    """Class plus the refusal, in the shape the overlay would use."""
    k = classify(realized, outstanding, side, basis=basis)
    return {
        "inventory_class": k,
        "basis": basis,
        "realized_residual": str(D(str(realized))),
        "outstanding_residual": str(D(str(outstanding))),
        "prospective_residual": str(prospective_residual(realized,
                                                         outstanding)),
        "side": side,
        "permitted": k != INCREASE_RESIDUAL,
        "refusal": (None if k != INCREASE_RESIDUAL
                    else "IMBALANCE_INCREASE_SUPPRESSED"),
    }


class OutstandingBook:
    """Signed outstanding quantity, counted at full remaining size.

    Mirrors what `risk_exposure_usd` already does for money, for direction.
    An order leaves the book when it fills, is cancelled, or is otherwise
    terminal -- and only then does its reservation return.
    """

    def __init__(self) -> None:
        self._live: Dict[str, Dict[str, Any]] = {}

    def place(self, oid: str, side: str, qty: D) -> None:
        self._live[oid] = {"side": side, "remaining": D(str(qty))}

    def fill(self, oid: str, qty: D) -> None:
        o = self._live.get(oid)
        if o is None:
            return
        o["remaining"] -= D(str(qty))
        if o["remaining"] <= 0:
            self._live.pop(oid, None)

    def terminate(self, oid: str) -> None:
        self._live.pop(oid, None)

    def signed(self) -> D:
        r = D(0)
        for o in self._live.values():
            q = o["remaining"]
            r += q if str(o["side"]).upper().startswith("U") else -q
        return r

    def count(self) -> int:
        return len(self._live)
