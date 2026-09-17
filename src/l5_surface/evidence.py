"""The surface's evidence ladder. Nine stages, and no promotions between them.

    DESIRED_SURFACE      our hypothesis said a level should be resting
    RESTABLE             the venue would accept a passive order there
    BOOK_REACHED         the book's touch reached our price
    TRADE_FLOW           volume actually executed at or through it
    EVIDENCED_FILL       the queue ahead of us cleared AND our size was
                         covered -- the ONLY stage that moves inventory
    INVENTORY_UPDATE     the position implied by evidenced fills
    OPPOSITE_RECYCLING   the opposite outcome interacted afterwards
    SETTLED              the venue resolved the market
    REALIZED             the venue confirmed cash

THE ONE RULE. The first four stages are ordered by strength and the gap
between BOOK_REACHED and EVIDENCED_FILL is where every optimistic backtest
lives: a print at our price says the price was reached, not that we were the
one who traded. Historically the two claims diverged by roughly half --
book_reached 97-98% against trade_flow_through 46-51% on the same quotes -- so
reading BOOK_REACHED as a fill would roughly double the apparent fill rate.

`advance` therefore refuses to skip a stage, and `evidenced_fill` is derived
ONLY from the queue-accounted criterion in `src.l5_shadow.maker`, which is
reused rather than restated. There is no argument that turns a weaker stage
into a fill; asking for one raises.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.l5_shadow import maker as MK

SCHEMA = "l5_surface_evidence/v1"

DESIRED_SURFACE = "DESIRED_SURFACE"
RESTABLE = "RESTABLE"
BOOK_REACHED = "BOOK_REACHED"
TRADE_FLOW = "TRADE_FLOW"
EVIDENCED_FILL = "EVIDENCED_FILL"
INVENTORY_UPDATE = "INVENTORY_UPDATE"
OPPOSITE_RECYCLING = "OPPOSITE_RECYCLING"
SETTLED = "SETTLED"
REALIZED = "REALIZED"

STAGES = (DESIRED_SURFACE, RESTABLE, BOOK_REACHED, TRADE_FLOW, EVIDENCED_FILL,
          INVENTORY_UPDATE, OPPOSITE_RECYCLING, SETTLED, REALIZED)

#: the stages that are NOT a fill, however suggestive they look
NOT_A_FILL = (DESIRED_SURFACE, RESTABLE, BOOK_REACHED, TRADE_FLOW)

#: the only stage that moves inventory
FILL_STAGE = EVIDENCED_FILL


class Promotion(Exception):
    """Raised when something tries to read a weak stage as a fill."""


@dataclass
class RungEvidence:
    """One rung's walk up the ladder. It only ever moves one rung at a time."""

    surface_id: str
    spec_sha256: str
    market_key: str
    outcome: str
    price: str
    desired_qty: str
    rung_index: int
    reached: List[str] = field(default_factory=lambda: [DESIRED_SURFACE])
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def stage(self) -> str:
        return self.reached[-1]

    def advance(self, to: str, **detail: Any) -> "RungEvidence":
        if to not in STAGES:
            raise ValueError("unknown stage %r" % (to,))
        i, j = STAGES.index(self.stage), STAGES.index(to)
        if j <= i:
            raise ValueError("the ladder does not go backwards: %s -> %s"
                             % (self.stage, to))
        if j != i + 1:
            raise ValueError(
                "cannot skip from %s to %s. Every stage between them is a "
                "claim that has to be evidenced separately -- skipping is how "
                "BOOK_REACHED becomes a fill." % (self.stage, to))
        self.reached.append(to)
        self.detail[to] = detail
        return self

    def is_fill(self) -> bool:
        """True only at EVIDENCED_FILL or beyond. There is no other path."""
        return STAGES.index(self.stage) >= STAGES.index(FILL_STAGE)

    def filled_qty(self) -> D:
        if not self.is_fill():
            return D("0")
        d = self.detail.get(EVIDENCED_FILL) or {}
        return D(str(d.get("modelled_fill_qty") or "0"))

    def assert_not_promoted(self) -> None:
        if self.stage in NOT_A_FILL and self.detail.get(EVIDENCED_FILL):
            raise Promotion("%s carries fill detail at stage %s"
                            % (self.price, self.stage))

    def report(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "surface_id": self.surface_id,
                "spec_sha256": self.spec_sha256,
                "market_key": self.market_key, "outcome": self.outcome,
                "price": self.price, "desired_qty": self.desired_qty,
                "rung_index": self.rung_index,
                "stage": self.stage, "reached": list(self.reached),
                "is_fill": self.is_fill(),
                "filled_qty": str(self.filled_qty()),
                "detail": self.detail,
                "labels": [
                    "RESTABLE / BOOK_REACHED / TRADE_FLOW are NOT fills and "
                    "are never counted as any.",
                    "filled_qty is nonzero only at EVIDENCED_FILL, which is "
                    "the queue-accounted VOLUME_AHEAD_CLEARED criterion.",
                ]}


def walk(rung_ev: RungEvidence, *, restable: Optional[bool],
         touch_reached: Optional[bool], prints: Optional[Sequence[dict]],
         queue_ahead: Any, placed_at: float, observed_until: float,
         coverage_complete: Optional[bool] = None) -> RungEvidence:
    """Advance one rung as far as the evidence actually carries it.

    Each step is gated on its own witness, and the walk STOPS at the first
    stage whose witness is absent or unreadable. It never jumps to the end
    because the end looks likely.
    """
    if not restable:
        rung_ev.detail[RESTABLE] = {
            "satisfied": False,
            "why": ("the venue would not accept a passive order at this price"
                    if restable is False else "restability not established"),
        }
        return rung_ev
    rung_ev.advance(RESTABLE, satisfied=True)

    if not touch_reached:
        rung_ev.detail[BOOK_REACHED] = {
            "satisfied": False,
            "why": ("the touch never reached our price"
                    if touch_reached is False
                    else "the book was not readable over the interval"),
        }
        return rung_ev
    rung_ev.advance(BOOK_REACHED, satisfied=True)

    ev = MK.evidence_for_quote(
        quote_px=rung_ev.price, quote_qty=rung_ev.desired_qty,
        ahead=queue_ahead, placed_at=placed_at, prints=prints,
        observed_until=observed_until, coverage_complete=coverage_complete)
    crit = ev.get("fill_criteria") or {}
    flow = bool(crit.get("TRADE_AT_PRICE") or crit.get("TRADE_THROUGH"))
    if not flow:
        rung_ev.detail[TRADE_FLOW] = {
            "satisfied": False, "criteria": crit,
            "why": ("no volume executed at or through the price. "
                    "BOOK_REACHED without TRADE_FLOW is the ordinary case and "
                    "is not a fill"),
        }
        return rung_ev
    rung_ev.advance(TRADE_FLOW, satisfied=True, criteria=crit,
                    volume_at_price=crit.get("volume_at_price"),
                    volume_through=crit.get("volume_through"))

    if ev.get("evidenced_fill") is not True:
        rung_ev.detail[EVIDENCED_FILL] = {
            "satisfied": False,
            "evidenced_fill": ev.get("evidenced_fill"),
            "reason": ev.get("reason"),
            "modelled_fill_qty": ev.get("modelled_fill_qty"),
            "queue_ahead": ev.get("queue_ahead"),
            "why": ("volume traded, but not enough to clear the queue ahead "
                    "of us AND cover our size. This is the gap where an "
                    "optimistic backtest lives."),
        }
        return rung_ev
    rung_ev.advance(EVIDENCED_FILL, satisfied=True,
                    modelled_fill_qty=ev.get("modelled_fill_qty"),
                    queue_ahead=ev.get("queue_ahead"),
                    time_to_fill_s=ev.get("time_to_fill_s"),
                    criteria=crit, reason=ev.get("reason"))
    return rung_ev


def inventory_from(evs: Sequence[RungEvidence]) -> Dict[str, Any]:
    """Inventory implied by EVIDENCED FILLS ONLY.

    A rung that reached TRADE_FLOW contributes exactly zero. That is the
    point: shadow inventory built from weaker stages is the thing that makes a
    strategy look profitable on liquidity it never had."""
    inv: Dict[str, D] = {}
    counted = 0
    for e in evs:
        e.assert_not_promoted()
        if not e.is_fill():
            continue
        q = e.filled_qty()
        if q <= 0:
            continue
        inv[e.outcome] = inv.get(e.outcome, D("0")) + q
        counted += 1
    up, dn = inv.get("Up", D("0")), inv.get("Down", D("0"))
    return {"stage": INVENTORY_UPDATE,
            "inventory": {k: str(v) for k, v in inv.items()},
            "matched": str(min(up, dn)), "residual": str(up - dn),
            "rungs_counted": counted, "rungs_considered": len(evs),
            "labels": ("from EVIDENCED_FILL only. SHADOW inventory: no order "
                       "was placed and nothing here is a position.")}


def recycling(evs: Sequence[RungEvidence], marks_s: Sequence[float]
              ) -> Dict[str, Any]:
    """Did the OPPOSITE outcome fill within each mark of a fill?

    Only fills on both sides count, so a rung that merely rested opposite an
    evidenced fill is not recycling."""
    fills = sorted(
        ((float((e.detail.get(EVIDENCED_FILL) or {}).get("t") or 0.0), e)
         for e in evs if e.is_fill()), key=lambda x: x[0])
    out: Dict[str, Any] = {"stage": OPPOSITE_RECYCLING, "n_fills": len(fills)}
    for m in marks_s:
        hit = 0
        for t0, e in fills:
            if any(t > t0 and (t - t0) <= m and o.outcome != e.outcome
                   for t, o in fills):
                hit += 1
        out["p_opposite_within_%ss" % m] = (
            None if not fills else round(hit / len(fills), 4))
        out["n_opposite_within_%ss" % m] = hit
    out["labels"] = ("both legs must be EVIDENCED_FILL. A resting opposite "
                     "rung is not an interaction.")
    return out


def settlement_placeholder(market_key: str) -> Dict[str, Any]:
    """SETTLED and REALIZED are venue facts and cannot be modelled here.

    Included so the ladder is complete and so nothing downstream has to invent
    the last two stages. A shadow surface never reaches them: it places no
    order, so the venue has nothing to resolve or pay."""
    return {"market_key": market_key,
            SETTLED: {"satisfied": False,
                      "why": "no order was placed, so nothing settles"},
            REALIZED: {"satisfied": False,
                       "why": ("realized requires venue-confirmed cash. "
                               "Modelled pair economics, mark-to-market and "
                               "estimated rebates are none of them.")}}
