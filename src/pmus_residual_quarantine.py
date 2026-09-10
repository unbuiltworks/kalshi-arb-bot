"""RESIDUAL QUARANTINE: a dormant residual stops consuming the one active
acquisition slot, and stops consuming nothing else.

OWNER GO 2026-09-10: "implement and prove residual quarantine, but do not
activate it on the current live campaign."

SHIPS DARK. `quarantine_enabled()` reads PMUS_RESIDUAL_QUARANTINE and
defaults OFF. Nothing here is wired into the live session; the campaign that
is currently holding a residual must reach a clean settlement boundary first.

--------------------------------------------------------------------------
WHAT THIS CHANGES, AND THE MUCH LONGER LIST OF WHAT IT DOES NOT
--------------------------------------------------------------------------
Changes: ONE thing. A market that is dormant -- no resting orders, inventory
venue-confirmed -- stops occupying the single active ACQUISITION slot.

Does not change, and each is enforced by a test that fails if it is removed:

  * the market stays in `held`, continuously, from quarantine to settlement;
  * settlement reconciliation keeps monitoring it;
  * its FULL conservative downside stays in combined_loss -- the same
    min(up,dn) - spent floor, unmodified, unweighted, undiscounted;
  * its own state machine may still COMPLETE later if an allowed <=$1.00
    opportunity appears (reduces_risk_only already decides this exactly);
  * no dollar cap rises and no exposure is forgiven.

The motivating failure, measured: 2026-09-10, the campaign worked 20 markets
in 93 minutes, then took a residual in a multi-day county-cricket fixture and
opened no further first leg for 3h20m. The book sat at 0.77/0.84 -- outside
the 0.25-0.75 band -- so the engine could not even work the position it was
guarding. It could only wait. Meanwhile 32 markets were eligible and carrying
$630k/minute. One residual = zero new experiments, for a $5.70 position.

--------------------------------------------------------------------------
THE ADMISSION GATE COMES FROM THE EXISTING MACHINERY
--------------------------------------------------------------------------
OWNER: "The exact gate should come from the existing combined-loss machinery
rather than inventing another risk system." Every term below is a function
that already ships in pmus_spring_l5_session:

    combined_loss(realized, ALL held incl. quarantined)
        + worst_case(proposed acquisition)
        >  daily_loss_stop()            # -$15.00 at the active rung

`held` includes quarantined markets. That is the entire point: quarantine
frees a SCHEDULING slot and frees nothing else. Concurrency stops being a
policy dial and becomes a consequence of the same budget that governs today,
so three stranded residuals refuse the fourth acquisition by arithmetic
rather than by a hand-set number.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Iterable, Mapping, Optional, Sequence

QUARANTINE_ENV = "PMUS_RESIDUAL_QUARANTINE"
# Active ACQUISITION concurrency. Unchanged by quarantine, and quarantine is
# not a route to raising it: this is how many markets may be ACQUIRING, not
# how many may hold inventory.
ACTIVE_ACQUISITION_SLOTS = 1


def quarantine_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Ships DARK. An unrecognised value leaves it OFF."""
    env = os.environ if env is None else env
    return str(env.get(QUARANTINE_ENV, "")).strip().lower() in (
        "1", "true", "yes", "on")


# ------------------------------------------------------------ eligibility
class QuarantineRefused(Exception):
    """Raised with the failing conditions named. A market that cannot be
    established as dormant is NOT dormant -- 'we could not check' and 'it
    holds' must never collapse into one answer."""

    def __init__(self, failed: Sequence[str]):
        self.failed = list(failed)
        super().__init__("quarantine refused: " + ", ".join(self.failed))


def eligibility(market: Mapping[str, Any]) -> dict:
    """May this market leave the active acquisition scheduler?

    `market` carries: resting_orders (int), venue_confirmed (bool),
    inventory (an Inventory), in_held (bool), settlement_monitored (bool).

    Each condition is reported by name so a refusal says WHICH one failed
    rather than just being false.
    """
    inv = market.get("inventory")
    resting = market.get("resting_orders")
    checks = {
        # OWNER: "there are zero resting orders in that market"
        "zero_resting_orders": resting == 0,
        # OWNER: "its held inventory is venue-confirmed"
        "venue_confirmed": market.get("venue_confirmed") is True,
        # a market with no inventory is FLAT, not quarantined: it should
        # simply leave. Quarantining flat would hide a scheduler bug.
        "has_inventory": inv is not None and (inv.up != 0 or inv.dn != 0),
        # OWNER: "it remains continuously included in `held`"
        "stays_in_held": market.get("in_held") is True,
        # OWNER: "...and settlement reconciliation"
        "stays_settlement_monitored": market.get("settlement_monitored") is True,
    }
    return {"eligible": all(checks.values()), "checks": checks,
            "failed": sorted(k for k, v in checks.items() if not v)}


def quarantine(market: Mapping[str, Any]) -> dict:
    """Move a dormant market out of the acquisition scheduler. Refuses loudly."""
    e = eligibility(market)
    if not e["eligible"]:
        raise QuarantineRefused(e["failed"])
    return {
        "slug": market.get("slug"),
        "state": "QUARANTINED",
        "occupies_acquisition_slot": False,
        # everything below is unchanged, and is restated on the record so a
        # reader cannot mistake quarantine for release
        "in_held": True,
        "settlement_monitored": True,
        "counted_in_combined_loss": True,
        "complement_management_active": True,
        "dollar_caps_changed": None,
        "exposure_forgiven": None,
    }


# ----------------------------------------------------- the portfolio gate
def held_inventories(positions: Iterable[Mapping[str, Any]]) -> list:
    """Every held inventory, ACTIVE AND QUARANTINED ALIKE.

    This function exists to be a single place that cannot be filtered. A
    caller that wanted "just the active ones" for the budget would be
    computing a smaller loss than the portfolio actually carries, which is
    exactly the failure the mutation tests are aimed at.
    """
    out = []
    for p in positions:
        inv = p.get("inventory")
        if inv is not None:
            out.append(inv)
    return out


def portfolio_headroom(realized_today, positions, session=None) -> dict:
    """combined_loss(realized, ALL held) against the daily stop."""
    session = _session() if session is None else session
    invs = held_inventories(positions)
    combined = session.combined_loss(D(str(realized_today)), invs)
    stop = session.daily_loss_stop()
    return {
        "combined_loss_usd": str(combined),
        "daily_loss_stop_usd": str(stop),
        "headroom_usd": str(combined - stop),
        "held_markets": len(invs),
        "quarantined_markets": sum(
            1 for p in positions if p.get("state") == "QUARANTINED"),
        "labels": ("held includes QUARANTINED markets; each contributes its "
                   "full min(up,dn)-spent floor, undiscounted"),
    }


def may_admit_new_acquisition(realized_today, positions,
                              proposed_worst_case,
                              active_acquisitions: int = 0,
                              session=None) -> dict:
    """OWNER's gate, verbatim:

        combined_loss(realized, all held positions)
          + worst_case(proposed acquisition)  >  -$15

    plus the acquisition-slot limit, which quarantine does NOT relax: it only
    stops a dormant residual from occupying the slot in the first place.

    STRICTLY GREATER THAN, matching combined_budget_tripped's inclusive
    boundary -- at exactly the stop, the stop has fired.

    `proposed_worst_case` is a NEGATIVE dollar figure (a loss). A caller that
    passes a positive number is describing a gain, and a gain has no place in
    a worst case; that is refused rather than silently negated.
    """
    session = _session() if session is None else session
    wc = D(str(proposed_worst_case))
    if wc > 0:
        raise ValueError(
            f"proposed worst case must be a loss (<= 0), got {wc}: a positive "
            "worst case would BUY headroom instead of consuming it")
    head = portfolio_headroom(realized_today, positions, session)
    combined = D(head["combined_loss_usd"])
    stop = D(head["daily_loss_stop_usd"])
    after = combined + wc
    checks = {
        "budget_after_proposed_above_stop": after > stop,
        "acquisition_slot_free": active_acquisitions < ACTIVE_ACQUISITION_SLOTS,
    }
    return {
        "admit": all(checks.values()),
        "checks": checks,
        "failed": sorted(k for k, v in checks.items() if not v),
        "combined_loss_usd": str(combined),
        "proposed_worst_case_usd": str(wc),
        "combined_after_proposed_usd": str(after),
        "daily_loss_stop_usd": str(stop),
        "active_acquisitions": active_acquisitions,
        "acquisition_slots": ACTIVE_ACQUISITION_SLOTS,
        **{k: head[k] for k in ("held_markets", "quarantined_markets")},
        "labels": ("gate is combined_loss(realized, ALL held incl. "
                   "quarantined) + worst_case(proposed) > daily_loss_stop(); "
                   "existing R5F entry gates still apply on top of this"),
    }


# ------------------------------------------- a quarantined market may still complete
def may_complete_in_quarantine(inv, side_up: bool, px, qty,
                               session=None) -> dict:
    """OWNER: "its own state machine may still COMPLETE later if an allowed
    <=$1 opportunity appears."

    Delegates to the EXISTING predicates rather than restating them:
    reduces_risk_only (does this raise the market's guaranteed floor?) and
    the completion pair ceiling. Restating either here would create a second
    copy that could drift from the one the live path enforces.
    """
    session = _session() if session is None else session
    p, q = D(str(px)), D(str(qty))
    risk_reducing = session.reduces_risk_only(inv, side_up, p, q)
    # pair_invariant_ok, NOT a hasattr-guarded lookup. The first draft called
    # a `pair_cost_after_ok` that does not exist and fell back to True -- a
    # ceiling check that always passes, in the one function whose job is
    # refusing an above-par completion. A missing predicate must break the
    # import, not quietly approve.
    within_ceiling = session.pair_invariant_ok(inv, side_up, p, q)
    return {
        "allowed": bool(risk_reducing and within_ceiling),
        "risk_reducing": bool(risk_reducing),
        "within_pair_ceiling": bool(within_ceiling),
        "matched_pair_cost_after": (
            str(session.matched_pair_cost_after(inv, side_up, p, q))
            if session.matched_pair_cost_after(inv, side_up, p, q) is not None
            else None),
        "pair_ceiling_usd": str(session.completion_pair_ceiling()),
        "floor_before_usd": str(session.guaranteed_settlement_floor(inv)),
        "floor_after_usd": str(session.floor_after_fill(inv, side_up, p, q)),
        "labels": ("quarantine never widens what a market may do: this is "
                   "the same reduces_risk_only the live path uses"),
    }


def _session():
    from src import pmus_spring_l5_session as s
    return s
