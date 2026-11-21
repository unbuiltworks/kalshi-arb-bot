"""L5_MAKER_EXECUTION_V1 -- the second treatment. Native stays frozen.

`L5_NATIVE` 030d8bf is the immutable control and nothing here touches it.
What this module consumes is the frozen rule's OWN output: an action it
classified `mode: MAKER` -- L5's price strictly inside our touch, so copying
him means resting -- and then refused `STRICT_NOT_EVALUABLE` because, in
replay, no interaction evidence existed to prove a hypothetical resting
order would have filled.

That refusal is correct in replay and must stay. Live it is backwards: we do
not need to prove the fill, we can place the order and let the venue answer.
Probe 06 spent 43 of 52 actions on that refusal and took the other 6, so the
clone currently replicates only L5's aggressive actions -- the opposite of
the behaviour the whole programme is trying to reproduce.

The preregistered lifecycle is in analysis/l5/MAKER_LIFECYCLE_V1.md. The
rules that matter most are the ones a desire for fills would erode, so they
are the ones stated first and tested hardest:

    P1  rest at exactly L5's price; never improve, never chase
    L1  never reprice a resting order
    L2  NEVER convert an unfilled maker order into a taker order. Only a NEW
        L5 action the frozen rule independently calls TAKER may be aggressive

Nothing below FILLED books inventory, and FILLED comes only from an
authoritative order refresh -- never from book movement.
"""
from __future__ import annotations

import time
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: the frozen rule's refusal that means "restable, but unprovable in replay".
#: This module exists to answer it empirically instead.
UNEVALUABLE = "STRICT_NOT_EVALUABLE"
MAKER, TAKER = "MAKER", "TAKER"

#: ladder states, from L5_PASSIVE_SURFACE_SHADOW_V1. Never collapsed.
INTENT = "INTENT"
PLACED = "PLACED"
BOOK_REACHED = "BOOK_REACHED"
TRADE_FLOW = "TRADE_FLOW"
FILLED = "FILLED"
PARTIAL = "PARTIAL"
CANCELLED = "CANCELLED"
LADDER = (INTENT, PLACED, BOOK_REACHED, TRADE_FLOW, PARTIAL, FILLED,
          CANCELLED)

#: why a resting order was cancelled. An impatience timer is deliberately
#: absent: L5's reversal is evidence, ours is not.
CANCEL_L5_REVERSAL = "L5_REVERSAL"
CANCEL_WINDOW_GUARD = "WINDOW_GUARD"


def is_passive_refusal(rec: Mapping[str, Any]) -> bool:
    """Did the frozen rule call this action restable and then decline it?

    Both conditions are required. An action refused for staleness, for a
    missing book, or by the safety overlay is NOT a maker candidate: only the
    one refusal that means "we could rest here, but replay cannot prove the
    fill".
    """
    tr = rec.get("PMUS_TRANSLATION") or {}
    return (str(tr.get("mode") or "").upper() == MAKER
            and str(rec.get("refused_reason") or "") == UNEVALUABLE)


def maker_intent(rec: Mapping[str, Any], *, lam: D,
                 quantity_increment: Optional[D] = None,
                 floor_fn=None) -> Optional[Dict[str, Any]]:
    """The resting order this passive action asks for, or None.

    The price is L5's own, not our touch and not one tick better: copying his
    price is the replication, and improving it would be a different policy
    wearing the same name.
    """
    if not is_passive_refusal(rec):
        return None
    l5 = rec.get("L5_NATIVE") or {}
    side = l5.get("side")
    price = l5.get("price")
    size = l5.get("l5_size")
    if side is None or price is None or size is None:
        return None
    try:
        desired = D(str(size)) * lam
        px = D(str(price))
    except Exception:                                          # noqa: BLE001
        return None
    if desired <= 0 or px <= 0:
        return None
    executable = desired
    if quantity_increment is not None and floor_fn is not None:
        executable = floor_fn(desired, quantity_increment)
    return {
        "action_key": rec.get("action_key"),
        "side": side,
        "price": str(px),                       # P1: L5's price exactly
        "qty_desired": str(desired),
        "qty": str(executable),
        "quantization_delta": str(desired - executable),
        "mode": MAKER,
        "post_only": True,                      # P2
        "state": INTENT,
        "placeable": executable > 0,
        "labels": ("an INTENT, not a fill and not an order. Nothing below "
                   "FILLED books inventory."),
    }


class MakerOrder:
    """One resting order's life, with every stage kept apart."""

    def __init__(self, intent: Mapping[str, Any], *, now: float,
                 touch_at_placement: Optional[D] = None) -> None:
        self.intent = dict(intent)
        self.state = PLACED
        self.placed_at = now
        self.terminal_at: Optional[float] = None
        self.venue_order_id: Optional[str] = None
        self.touch_at_placement = touch_at_placement
        self.best_touch_while_resting = touch_at_placement
        self.book_reached = False
        self.trade_flow = False
        self.cum_qty = D(0)
        self.leaves_qty = D(str(intent.get("qty") or 0))
        self.avg_px: Optional[D] = None
        self.commission: Optional[D] = None
        self.liquidity: Optional[str] = None     # MAKER/TAKER at fill
        self.cancel_reason: Optional[str] = None

    # -- book evidence: executability and flow, never a fill -------------

    def observe_touch(self, touch: Optional[D]) -> None:
        """The touch moved. BOOK_REACHED is executability, not a fill."""
        if touch is None:
            return
        px = D(str(self.intent["price"]))
        if (self.best_touch_while_resting is None
                or touch < self.best_touch_while_resting):
            self.best_touch_while_resting = touch
        if touch <= px:
            self.book_reached = True

    def observe_print(self, price: Optional[D]) -> None:
        """A print at or through our price. Real flow. Still not our fill."""
        if price is None:
            return
        if price <= D(str(self.intent["price"])):
            self.trade_flow = True

    # -- the venue's own answer ------------------------------------------

    def apply_progress(self, prog: Mapping[str, Any], *, now: float) -> str:
        """Fill state from an authoritative order refresh. The only mover."""
        cum = D(str(prog.get("cum_qty") or 0))
        if cum < self.cum_qty:
            return self.state                    # backwards: caller decides
        self.cum_qty = cum
        leaves = prog.get("leaves")
        if leaves is not None:
            self.leaves_qty = D(str(leaves))
        if prog.get("avg_px") is not None:
            self.avg_px = D(str(prog.get("avg_px")))
        if prog.get("commission") is not None:
            self.commission = D(str(prog.get("commission")))
        if prog.get("liquidity") is not None:
            self.liquidity = str(prog.get("liquidity"))
        if prog.get("terminal"):
            self.state = FILLED if self.cum_qty > 0 else CANCELLED
            self.terminal_at = now
        elif self.cum_qty > 0:
            self.state = PARTIAL
        return self.state

    # -- the preregistered cancel rules ----------------------------------

    def should_cancel(self, *, now: float, deadline: float,
                      guard_s: float,
                      l5_reversed: bool = False) -> Optional[str]:
        """L3 and L4 only. There is no impatience timer, by design."""
        if self.state in (FILLED, CANCELLED):
            return None
        if l5_reversed:
            return CANCEL_L5_REVERSAL
        if now >= deadline - guard_s:
            return CANCEL_WINDOW_GUARD
        return None

    def rest_seconds(self, *, now: Optional[float] = None) -> float:
        end = self.terminal_at if self.terminal_at is not None else (
            now if now is not None else time.time())
        return round(end - self.placed_at, 3)

    def as_dict(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        return {
            "action_key": self.intent.get("action_key"),
            "state": self.state,
            "l5_native": {"side": self.intent.get("side"),
                          "price": self.intent.get("price")},
            "translation": {"price": self.intent.get("price"),
                            "qty_desired": self.intent.get("qty_desired"),
                            "qty": self.intent.get("qty"),
                            "quantization_delta":
                                self.intent.get("quantization_delta"),
                            "post_only": self.intent.get("post_only")},
            "venue_order_id": self.venue_order_id,
            "rest_seconds": self.rest_seconds(now=now),
            "touch_at_placement": (None if self.touch_at_placement is None
                                   else str(self.touch_at_placement)),
            "best_touch_while_resting":
                (None if self.best_touch_while_resting is None
                 else str(self.best_touch_while_resting)),
            "book_reached": self.book_reached,
            "trade_flow": self.trade_flow,
            "cum_qty": str(self.cum_qty),
            "leaves_qty": str(self.leaves_qty),
            "avg_px": None if self.avg_px is None else str(self.avg_px),
            "commission": (None if self.commission is None
                           else str(self.commission)),
            "liquidity_at_fill": self.liquidity,
            "cancel_reason": self.cancel_reason,
            "labels": ("book_reached and trade_flow are executability and "
                       "flow. Neither is a fill: only the venue's own order "
                       "row moves inventory."),
        }


def never_cross(order: MakerOrder, new_action_mode: Optional[str]) -> bool:
    """L2, as a function so it can be tested rather than merely intended.

    True means an aggressive order is permitted -- and that is ONLY when a
    NEW L5 action is independently classified TAKER by the frozen rule. An
    unfilled maker order never earns a cross by having waited.
    """
    return str(new_action_mode or "").upper() == TAKER


class Obstruction:
    """How often the envelope prevented faithful maker replication.

    Measured before anything is relaxed: a resting order holds the single
    live-order slot for its whole life, where a taker order holds it for
    milliseconds. That may be the binding constraint on replicating a
    liquidity provider, and if it is, the number should say so first.
    """

    def __init__(self) -> None:
        self.intents = 0
        self.placed = 0
        self.blocked_one_live = 0
        self.blocked_envelope = 0
        self.blocked_other: Dict[str, int] = {}
        self.slot_occupancy_s = 0.0

    def record(self, refusal: Optional[str]) -> None:
        self.intents += 1
        if refusal is None:
            self.placed += 1
        elif refusal == "MAX_LIVE_ORDERS":
            self.blocked_one_live += 1
        elif refusal in ("MAX_USD_PER_MARKET", "MAX_TOTAL_EXPOSURE"):
            self.blocked_envelope += 1
        else:
            self.blocked_other[refusal] = self.blocked_other.get(refusal, 0) + 1

    def as_dict(self) -> Dict[str, Any]:
        return {"maker_intents": self.intents,
                "maker_placed": self.placed,
                "maker_blocked_by_one_live": self.blocked_one_live,
                "maker_blocked_by_envelope": self.blocked_envelope,
                "maker_blocked_other": dict(self.blocked_other),
                "slot_occupancy_s": round(self.slot_occupancy_s, 3),
                "intents_lost_while_resting": self.blocked_one_live,
                "labels": ("obstruction is measured, not assumed. No limit "
                           "changes until these numbers exist.")}
