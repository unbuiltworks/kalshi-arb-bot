"""REJECTED, RESTING and CANCELLED, derived from records the executor already
writes. Read-only, no new service, no new state.

WHY THESE THREE. The economic scoreboard already carries SUBMITTED, FILLED,
SETTLED and REALIZED as distinct states, with fees and venue-CREDITED rebates
kept on their own lines. The three here were missing from the label set, and
they are precisely the maker-execution outcomes a maker hypothesis lives or
dies on: did our quote REST, was it REJECTED before it ever rested, and did we
CANCEL it rather than let it fill. Without them, "we submitted and did not
fill" covers a refused order, a rejected order and a cancelled one -- three
different findings about L5 replication.

NOTHING HERE IS NEW EVIDENCE. Every field comes from somewhere the deployed
executor already records:

  REJECTED   the provenance record's own refusal -- `refused_by_layer` and
             `refused_reason` for an order this engine declined to send -- and,
             separately, a venue state containing REJECT for one it did send.
             The two are kept APART: a gate that refused is our decision, a
             venue rejection is the venue's, and collapsing them would hide
             which side said no.
  RESTING    `live_orders` with remaining > 0 and no terminal state hint.
             That is the same reading `outstanding_by_market` and the risk
             gate already take, so a resting order cannot be counted here and
             missed there.
  CANCELLED  `deadman`'s own classification: `cancelled` means the VENUE
             reported a terminal state on RE-READ. `still_open` and
             `unverified` are NOT cancelled and are reported as their own
             states, because an accepted cancel request is not a cancelled
             order and never was.

LABELS STAY SEPARATE, which is the whole discipline: a submitted order is not
a resting one, a resting one is not filled, a cancelled one is not settled,
and none of them is realized. Fees and rebates are passed THROUGH, never
netted into a count, and a rebate is only ever carried when the venue credited
it.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: the seven states, in lifecycle order. Named here so a caller cannot
#: silently invent an eighth or reorder them in a report.
LIFECYCLE = ("SUBMITTED", "REJECTED", "RESTING", "FILLED", "CANCELLED",
             "SETTLED", "REALIZED")

#: already carried by l5_economic_scoreboard / l5_episode_ledger
ALREADY_LABELLED = ("SUBMITTED", "FILLED", "SETTLED", "REALIZED")

#: what this module adds
ADDED_HERE = ("REJECTED", "RESTING", "CANCELLED")

#: a venue state containing any of these is terminal; REJECT is called out
#: separately because it is the only one that means the order never rested.
REJECT_HINT = "REJECT"
TERMINAL_HINTS = ("FILLED", "CANCEL", "REJECT", "EXPIR", "DONE")


def _d(v: Any) -> D:
    if isinstance(v, D):
        return v
    if v is None:
        return D(0)
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return D(0)


def rejected_from_decision(rec: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """OUR refusal, from the provenance record. None if nothing refused.

    This is an action the engine decided not to send. It is not a venue
    rejection and is never reported as one.
    """
    layer = rec.get("refused_by_layer")
    if not layer:
        return None
    return {
        "label": "REJECTED",
        "by": "ENGINE",
        "layer": str(layer),
        "reason": str(rec.get("refused_reason") or ""),
        "action_key": rec.get("action_key"),
        "market": rec.get("market"),
        "window": rec.get("window"),
        "reached_stage": rec.get("stage_reached"),
        "labels": ("an action this engine declined to transmit. NOT a venue "
                   "rejection, and NOT a submitted order."),
    }


def rejected_from_venue(order_row: Mapping[str, Any]
                        ) -> Optional[Dict[str, Any]]:
    """THE VENUE'S rejection of an order we did send. None if not rejected."""
    state = str(order_row.get("state") or order_row.get("status") or "")
    if REJECT_HINT not in state.upper():
        return None
    return {
        "label": "REJECTED",
        "by": "VENUE",
        "venue_state": state,
        "venue_order_id": order_row.get("id") or order_row.get("order_id"),
        "client_order_id": (order_row.get("clientOrderId")
                            or order_row.get("client_order_id")),
        "market": order_row.get("marketSlug") or order_row.get("market"),
        "labels": ("the venue refused to create or keep this order, so it "
                   "never rested. It WAS submitted; it was never resting."),
    }


def resting(store_live_orders: Mapping[str, Mapping[str, Any]]
            ) -> List[Dict[str, Any]]:
    """Orders still working at the venue, by the ledger's own reading.

    Deliberately the same test the risk gate uses -- remaining > 0 and no
    terminal hint -- so an order cannot be RESTING here and absent from the
    exposure figures.
    """
    out: List[Dict[str, Any]] = []
    for coid, o in sorted((store_live_orders or {}).items()):
        rem = _d(o.get("remaining"))
        if rem <= 0:
            continue
        state = str(o.get("state") or "")
        if any(h in state.upper() for h in TERMINAL_HINTS):
            continue
        out.append({
            "label": "RESTING",
            "client_order_id": coid,
            "venue_order_id": o.get("venue_order_id"),
            "market": o.get("market"),
            "side": o.get("side"),
            "price": str(o.get("price")),
            "remaining": str(rem),
            "submitted_at": o.get("t"),
            "session_id": o.get("session_id"),
            "adopted": bool(o.get("adopted")),
            "labels": ("still fillable, so still exposure. A resting order "
                       "is NOT a fill and NOT a cancellation."),
        })
    return out


def cancelled(deadman_record: Mapping[str, Any]) -> Dict[str, Any]:
    """Split a `deadman` result into cancelled and the two NOT-cancelled
    states it deliberately keeps apart.

    `cancelled` is the only one the venue has confirmed terminal on re-read.
    `still_open` is the dangerous case -- the request returned and the order
    is still working -- and `unverified` is the unknown one. Neither is ever
    reported as a cancellation, which is the same rule `deadman` itself
    follows when it marks the store UNCERTAIN.
    """
    rec = deadman_record or {}

    def rows(key, label, note):
        out = []
        for o in (rec.get(key) or []):
            if not isinstance(o, Mapping):
                o = {"venue_order_id": str(o)}
            out.append({"label": label,
                        "client_order_id": o.get("client_order_id"),
                        "venue_order_id": o.get("venue_order_id"),
                        "market": o.get("market"),
                        "venue_state": o.get("venue_state"),
                        "labels": note})
        return out

    return {
        "reason": rec.get("reason"),
        "CANCELLED": rows(
            "cancelled", "CANCELLED",
            "the VENUE reported a terminal state on re-read. This is the "
            "only confirmed cancellation."),
        "NOT_CANCELLED_STILL_OPEN": rows(
            "still_open", "RESTING",
            "the cancel request returned and the order is STILL WORKING. "
            "Reported as resting, never as cancelled; the store is UNCERTAIN."),
        "NOT_CANCELLED_UNVERIFIED": rows(
            "unverified", "UNKNOWN",
            "the outcome could not be established. Not cancelled, not "
            "resting -- unknown, and the store is UNCERTAIN."),
        "labels": ("an accepted cancel request is not a cancelled order. "
                   "Only a venue re-read makes it one."),
    }


def classify(*, decisions: Sequence[Mapping[str, Any]] = (),
             live_orders: Optional[Mapping[str, Mapping[str, Any]]] = None,
             venue_order_rows: Sequence[Mapping[str, Any]] = (),
             deadman_records: Sequence[Mapping[str, Any]] = (),
             fees_usd: Any = None,
             rebates_credited_usd: Any = None) -> Dict[str, Any]:
    """One record carrying all three added labels, side by side.

    `fees_usd` and `rebates_credited_usd` are passed THROUGH and reported on
    their own lines. They are never netted into any count, and a rebate is
    carried only when the venue credited it -- a zero commission is not a
    rebate.
    """
    engine_rejected = [r for r in (rejected_from_decision(d)
                                   for d in decisions) if r]
    venue_rejected = [r for r in (rejected_from_venue(o)
                                  for o in venue_order_rows) if r]
    rest = resting(live_orders or {})
    cancels = [cancelled(dm) for dm in deadman_records]
    confirmed = [r for c in cancels for r in c["CANCELLED"]]
    still_open = [r for c in cancels for r in c["NOT_CANCELLED_STILL_OPEN"]]
    unverified = [r for c in cancels for r in c["NOT_CANCELLED_UNVERIFIED"]]

    return {
        "LABEL": "L5_ACTION_OUTCOMES",
        "lifecycle": list(LIFECYCLE),
        "already_labelled_elsewhere": list(ALREADY_LABELLED),
        "added_here": list(ADDED_HERE),
        "REJECTED": {
            "by_engine": engine_rejected,
            "by_venue": venue_rejected,
            "counts": {"engine": len(engine_rejected),
                       "venue": len(venue_rejected)},
            "labels": ("kept apart on purpose: a gate that refused is OUR "
                       "decision, a venue rejection is the venue's."),
        },
        "RESTING": {"orders": rest, "count": len(rest),
                    "usd_still_fillable": str(sum(
                        (_d(o["price"]) * _d(o["remaining"]) for o in rest),
                        D(0)))},
        "CANCELLED": {
            "confirmed": confirmed, "count": len(confirmed),
            "not_cancelled_still_open": still_open,
            "not_cancelled_unverified": unverified,
        },
        "fees_usd": (None if fees_usd is None else str(_d(fees_usd))),
        "rebates_credited_usd": (None if rebates_credited_usd is None
                                 else str(_d(rebates_credited_usd))),
        "labels": ("REPORTING ONLY. Derived from records the executor already "
                   "writes; it holds no state, decides nothing and gates "
                   "nothing. Fees and CREDITED rebates are separate lines and "
                   "are never netted into a count. None means no reading, "
                   "never zero."),
    }
