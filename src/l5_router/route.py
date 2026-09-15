"""Route one frozen intent to a venue, to both, or to NEITHER.

NEITHER IS A FIRST-CLASS OUTCOME, not a failure path. The whole reason the
intent carries an economic ceiling rather than a target price is so this
function can decline: if no venue can acquire the desired inventory at or under
the ceiling, the correct action is not to trade. A router that always picks a
venue has quietly converted a risk decision into a venue-selection problem.

RANKING IS ON EXPECTED REALIZED ECONOMICS, WHICH MEANS EFFECTIVE COST. Not the
quoted price -- a quote under the ceiling whose fee pushes it over is not
cheaper, it is ineligible. Not modelled fill probability alone -- `would_fill`
is an upper bound derived from visible depth, and ranking primarily on an upper
bound systematically prefers whichever venue displays more size.

CAPACITY IS A CONSTRAINT, NOT A TIEBREAK. A venue that cannot supply the
incremental quantity does not win on price.

WHAT THIS DOES NOT DO. It does not size, it does not decide when, and it never
promotes a modelled fill to a real one. It selects among adapter evaluations
that already exist.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal as D
from typing import Any, Optional, Sequence

SCHEMA = "l5_routing_decision/v1"
ROUTES = ("NEITHER", "PMUS", "GEMINI", "BOTH")


@dataclass
class RoutingDecision:
    intent_id: str
    decided_at: float
    route: str
    reason: str
    ranked: list = field(default_factory=list)
    declined: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["schema"] = SCHEMA
        return d


def _eligible(ev) -> tuple[bool, Optional[str]]:
    """Can this venue actually do it? Every no carries its reason."""
    if ev.stage == "no_market":
        return False, ev.mapping_reason or "no equivalent market on this venue"
    if not ev.would_submit:
        return False, ev.would_submit_reason or "would not submit"
    if ev.within_ceiling is not True:
        return False, "effective cost is not within the intent ceiling"
    if ev.effective_cost is None:
        return False, "no effective cost could be computed"
    return True, None


def _capacity_ok(ev, qty: D) -> bool:
    """Unknown capacity is NOT treated as sufficient.

    `executable_qty` -- size resting at every price we would accept, read off
    the FULL ladder -- is preferred when the adapter supplies it, because touch
    depth is the wrong question: a book 1 deep at the best price with 500 one
    tick behind it can supply 10 comfortably, and ranking on the touch declines
    a venue that could have filled us. Adapters that only see the touch still
    fall back to it.

    An adapter that can see neither reports None, and None must not read as
    'enough'. Failing closed on unknown capacity is the difference between a
    venue that can fill us and one we merely cannot see failing to."""
    have = ev.executable_qty if getattr(ev, "executable_qty", None) is not None \
        else ev.available_depth
    if have is None:
        return False
    try:
        return D(str(have)) >= qty
    except Exception:                                        # noqa: BLE001
        return False


def route(intent, evaluations: Sequence[Any], *,
          allow_both: bool = False, now: Optional[float] = None
          ) -> RoutingDecision:
    """Choose among adapter evaluations of the SAME intent.

    `allow_both` defaults to False: splitting one intent across two venues is a
    capital and reconciliation decision, not a pricing one, and it stays off
    until each venue has independently produced realized evidence.
    """
    t = time.time() if now is None else now
    ids = {e.intent_id for e in evaluations}
    if len(ids) > 1:
        raise ValueError(f"evaluations span multiple intents: {sorted(ids)}")
    if evaluations and intent.id not in ids:
        raise ValueError("evaluations do not belong to this intent")

    qty = D(intent.incremental_qty)
    ranked, declined = [], []
    for ev in evaluations:
        ok, why = _eligible(ev)
        if not ok:
            declined.append({"venue": ev.venue, "reason": why})
            continue
        if not _capacity_ok(ev, qty):
            declined.append({
                "venue": ev.venue,
                "reason": (
                    f"insufficient or unknown capacity for {qty} "
                    f"(touch depth={ev.available_depth}, executable at ceiling="
                    f"{getattr(ev, 'executable_qty', None)})")})
            continue
        ranked.append({
            "venue": ev.venue,
            "effective_cost": ev.effective_cost,
            "would_fill": ev.would_fill,
            "depth": ev.available_depth,
            "executable_qty": getattr(ev, "executable_qty", None),
            "fee_basis": ev.expected_fee_basis,
        })

    # cheapest all-in cost first; a modelled would_fill only breaks ties, so an
    # upper-bound belief can never outrank a real cost difference.
    ranked.sort(key=lambda r: (D(str(r["effective_cost"])),
                               0 if r["would_fill"] else 1))

    if not ranked:
        return RoutingDecision(
            intent_id=intent.id, decided_at=t, route="NEITHER",
            reason=("no venue can acquire the desired inventory within the "
                    "intent's economic ceiling and capacity. NEITHER is the "
                    "correct action, not a fallback."),
            ranked=[], declined=declined)

    if allow_both and len(ranked) > 1:
        return RoutingDecision(
            intent_id=intent.id, decided_at=t, route="BOTH",
            reason="both venues are eligible and splitting was explicitly enabled",
            ranked=ranked, declined=declined)

    best = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    reason = f"cheapest effective cost {best['effective_cost']}"
    if runner:
        reason += (f" vs {runner['venue']} at {runner['effective_cost']}")
    return RoutingDecision(
        intent_id=intent.id, decided_at=t, route=best["venue"],
        reason=reason, ranked=ranked, declined=declined)
