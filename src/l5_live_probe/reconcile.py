"""Checks that let a probe run unattended -- and stop itself when it must.

WHAT "UNATTENDED" HAS TO MEAN. Not "nobody is watching" but "nothing needs a
human to decide the safe action". Every condition below has exactly one correct
response and it is always the conservative one: STOP, CANCEL, or HOLD. None of
them requires judgement, so none of them requires chat.

THE ASYMMETRY THAT MAKES THIS SAFE. Read-only observation may self-recover and
run forever; anything that moves money must FAIL CLOSED and must not relaunch
itself after a stop. So `decide()` can return STOP but never START, and there
is deliberately no resume path in this module -- restarting a money-moving
session is an owner action, not a state transition.

RECONCILIATION IS THE THIRD LEG. A stop that leaves orders resting is not a
stop, and inventory the venue reports but we do not is not a discrepancy to
average out -- it is the venue's number against ours, and the VENUE wins.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

LABEL = "LIVE_PROBE_RECONCILE/unattended"

STOP = "STOP"            # halt and latch; no new exposure
CANCEL = "CANCEL_ALL"    # pull every resting order now
HOLD = "HOLD"            # keep inventory, place nothing new
CONTINUE = "CONTINUE"

# A stop latches. Nothing in this module clears it.
LATCH_REASONS = ("DAILY_LOSS_STOP", "DEADMAN_STALE", "INVENTORY_MISMATCH",
                 "ORPHAN_ORDER", "SETTLEMENT_STALLED", "SHA_DRIFT",
                 "ACCOUNTING_UNAVAILABLE")


def _d(v: Any, default: str = "0") -> D:
    try:
        return D(str(v)) if v is not None else D(default)
    except Exception:                                         # noqa: BLE001
        return D(default)


def inventory_matches(ours: Sequence[Any], venue: Sequence[Any]) -> Dict[str, Any]:
    """Our book against the venue's. The venue is authoritative."""
    o = {str(k): _d(v) for k, v in dict(ours or {}).items()} if isinstance(ours, dict) else {}
    v = {str(k): _d(x) for k, x in dict(venue or {}).items()} if isinstance(venue, dict) else {}
    keys = sorted(set(o) | set(v))
    diffs = [{"key": k, "ours": str(o.get(k, D("0"))),
              "venue": str(v.get(k, D("0")))}
             for k in keys if o.get(k, D("0")) != v.get(k, D("0"))]
    return {"ok": not diffs, "diffs": diffs,
            "note": "the venue's number is authoritative; ours is the one that "
                    "is wrong when they disagree"}


def orphan_orders(ours: Sequence[str], venue_open: Sequence[str]) -> Dict[str, Any]:
    """An order resting at the venue that we do not recognise is an orphan, and
    an orphan is never safe to leave resting."""
    mine = {str(x) for x in (ours or ())}
    live = {str(x) for x in (venue_open or ())}
    orphans = sorted(live - mine)
    lost = sorted(mine - live)
    return {"ok": not orphans and not lost,
            "orphans_at_venue": orphans, "ours_not_at_venue": lost}


def decide(*, combined_loss: Any = None, daily_stop: Any = None,
           heartbeat_age_s: Optional[float] = None, deadman_s: Optional[float] = None,
           inventory: Optional[Dict[str, Any]] = None,
           orders: Optional[Dict[str, Any]] = None,
           settlement_age_s: Optional[float] = None,
           settlement_max_age_s: Optional[float] = None,
           serving_sha: Optional[str] = None, expected_sha: Optional[str] = None,
           accounting_ok: Optional[bool] = None) -> Dict[str, Any]:
    """One conservative answer. Reasons accumulate; the strongest action wins.

    Anything UNKNOWN is treated as unsafe. A probe that cannot establish its own
    state does not get to keep trading on the assumption it is fine.
    """
    reasons: List[Dict[str, str]] = []

    def add(code: str, action: str, why: str) -> None:
        reasons.append({"reason": code, "action": action, "why": why})

    if combined_loss is None or daily_stop is None:
        add("ACCOUNTING_UNAVAILABLE", STOP,
            "combined loss or the stop is unknown; an unmeasurable budget is "
            "a breached one")
    elif _d(combined_loss) <= _d(daily_stop):
        add("DAILY_LOSS_STOP", STOP,
            f"combined loss {combined_loss} reached {daily_stop} (inclusive)")

    if deadman_s is not None:
        if heartbeat_age_s is None:
            add("DEADMAN_STALE", CANCEL,
                "heartbeat age unknown; staleness cannot be ruled out")
        elif heartbeat_age_s > float(deadman_s):
            add("DEADMAN_STALE", CANCEL,
                f"last heartbeat {heartbeat_age_s:.0f}s ago exceeds "
                f"{float(deadman_s):.0f}s; state is stale")

    if inventory is not None and not inventory.get("ok", False):
        add("INVENTORY_MISMATCH", STOP,
            f"our inventory disagrees with the venue on "
            f"{len(inventory.get('diffs') or [])} key(s)")

    if orders is not None and not orders.get("ok", False):
        add("ORPHAN_ORDER", CANCEL,
            f"orphans at venue={orders.get('orphans_at_venue')} "
            f"ours_missing={orders.get('ours_not_at_venue')}")

    if settlement_max_age_s is not None:
        if settlement_age_s is None:
            add("SETTLEMENT_STALLED", STOP,
                "settlement age unknown; the accounting path cannot be vouched for")
        elif settlement_age_s > float(settlement_max_age_s):
            add("SETTLEMENT_STALLED", STOP,
                f"no settlement progress in {settlement_age_s:.0f}s "
                f"(limit {float(settlement_max_age_s):.0f}s)")

    if expected_sha is not None:
        if serving_sha is None:
            add("SHA_DRIFT", STOP, "serving SHA unknown")
        elif not (serving_sha.startswith(expected_sha)
                  or expected_sha.startswith(serving_sha)):
            add("SHA_DRIFT", STOP,
                f"serving {serving_sha[:12]} != expected {expected_sha[:12]}")

    if accounting_ok is False:
        add("ACCOUNTING_UNAVAILABLE", STOP, "accounting-path assertions failed")

    # STOP dominates CANCEL dominates HOLD. A stop also cancels: leaving orders
    # resting after a stop is not a stop.
    if any(r["action"] == STOP for r in reasons):
        action, cancel = STOP, True
    elif any(r["action"] == CANCEL for r in reasons):
        action, cancel = CANCEL, True
    else:
        action, cancel = CONTINUE, False
    return {
        "label": LABEL, "action": action, "cancel_resting_orders": cancel,
        "latched": action == STOP,
        "reasons": reasons, "n_reasons": len(reasons),
        "may_auto_resume": False,
        "labels": ("a STOP latches and NOTHING here clears it; restarting a "
                   "money-moving session is an owner action, never a state "
                   "transition. This module can stop a probe and can never "
                   "start one."),
    }
