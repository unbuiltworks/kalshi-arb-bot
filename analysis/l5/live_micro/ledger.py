"""LIVE-L5-MICRO-1 lifecycle ledger and accounting. DISARMED.

Enforces the language discipline in code rather than in review:
  SUBMITTED is not a fill. A fill is not profit. Mark-to-market is not
  realized. Only FILLED + SETTLED + CASH_CONFIRMED is REALIZED.

This module records. It cannot submit, sign, or move capital.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from analysis.l5.live_micro.spec import (LIFECYCLE, MARKOUT_HORIZONS, PROBE,
                                         SPEC, MICRO_FINGERPRINT,
                                         assert_frozen)

#: modules that can sign or submit. The ledger must never share a process
#: with one while DISARMED.
FORBIDDEN_WHEN_DISARMED = ("pmus_q1_executor", "pmus_micro_executor")


def micro_armed() -> bool:
    """The owner's four-key dual-arm. Claude must never set these."""
    sid = os.getenv("PMUS_MICRO_SESSION_ID", "").strip()
    return (os.getenv("PMUS_MICRO_LIVE", "").strip().upper() == "YES"
            and bool(sid)
            and os.getenv("PMUS_MICRO_ARM", "").strip() == sid)


def gate_asserted(slug: str) -> bool:
    return bool(slug) and os.getenv("PMUS_MICRO_GATE_EVIDENCE",
                                    "").strip() == slug


def assert_disarmed_or_owner_armed(slug: str) -> Dict[str, Any]:
    """The only place arming is evaluated. Returns a refusal, never raises
    on the disarmed path -- a disarmed probe is the NORMAL state."""
    armed = micro_armed()
    gated = gate_asserted(slug)
    return {
        "armed": armed, "gate_asserted_for_slug": gated,
        "may_submit": bool(armed and gated),
        "refusal": (None if (armed and gated) else
                    "DISARMED: owner has not set the four PMUS_MICRO_* keys "
                    "for this slug. No order may be submitted."),
    }


class StateError(RuntimeError):
    pass


class OrderRecord:
    """One real order's full lifecycle. Advances only along LIFECYCLE."""

    def __init__(self, client_id: str, slug: str, outcome: str,
                 price: D, qty: D):
        self.client_id = client_id
        self.slug = slug
        self.outcome = outcome
        self.price = D(str(price))
        self.intended_qty = D(str(qty))
        self.venue_order_id: Optional[str] = None
        self.state = "SUBMITTED"
        self.history: List[Dict[str, Any]] = []
        self.filled_qty = D("0")
        self.fill_events: List[Dict[str, Any]] = []
        self.maker_taker: Optional[str] = None     # from the venue, never assumed
        self.queue_info: List[Dict[str, Any]] = []
        self.fees_usd = D("0")
        self.credited_rewards_usd = D("0")         # only when actually credited
        self.settled = False
        self.cash_confirmed = False
        self.markouts: Dict[str, Any] = {}

    # ---- strict state machine ----
    def advance(self, new: str, *, at: float, detail: Any = None) -> None:
        if new not in LIFECYCLE:
            raise StateError("unknown state %r" % new)
        cur, nxt = LIFECYCLE.index(self.state), LIFECYCLE.index(new)
        # CANCELLED is reachable from any pre-settlement state
        cancelling = (new == "CANCELLED"
                      and self.state in ("SUBMITTED", "ACKNOWLEDGED",
                                         "PARTIALLY_FILLED"))
        if not cancelling and nxt <= cur:
            raise StateError(
                "illegal transition %s -> %s: the lifecycle only advances"
                % (self.state, new))
        if new == "FILLED" and self.filled_qty < self.intended_qty:
            raise StateError(
                "refusing FILLED while filled_qty %s < intended %s: a "
                "partial fill is NEVER promoted to a full fill"
                % (self.filled_qty, self.intended_qty))
        if new == "CASH_CONFIRMED" and not self.settled:
            raise StateError(
                "refusing CASH_CONFIRMED before SETTLED: only "
                "filled+settled+cash-confirmed is REALIZED")
        self.history.append({"from": self.state, "to": new, "at": at,
                             "detail": detail})
        self.state = new
        if new == "SETTLED":
            self.settled = True
        if new == "CASH_CONFIRMED":
            self.cash_confirmed = True

    def record_fill(self, qty: D, price: D, *, at: float,
                    maker_taker: str, fee_usd: D = D("0"),
                    queue: Optional[dict] = None) -> None:
        """A venue-reported fill on OUR order id. Never inferred from the
        touch, never from depth, never from a state string."""
        qty = D(str(qty))
        if qty <= 0:
            raise StateError("non-positive fill quantity")
        if self.filled_qty + qty > self.intended_qty:
            raise StateError(
                "fill %s would exceed intended %s -- reported as a CAP "
                "BREACH, never truncated" % (qty, self.intended_qty))
        if maker_taker not in ("maker", "taker"):
            raise StateError("maker/taker must come from the venue")
        self.filled_qty += qty
        self.fees_usd += D(str(fee_usd))
        self.maker_taker = maker_taker
        self.fill_events.append({"at": at, "qty": str(qty),
                                 "price": str(price),
                                 "maker_taker": maker_taker,
                                 "fee_usd": str(fee_usd)})
        if queue:
            self.queue_info.append({"at": at, **queue})
        target = ("FILLED" if self.filled_qty >= self.intended_qty
                  else "PARTIALLY_FILLED")
        if target == "PARTIALLY_FILLED" and self.state == "PARTIALLY_FILLED":
            return                      # another partial, same state
        self.advance(target, at=at, detail={"filled_qty": str(self.filled_qty)})

    def credit_reward(self, amount_usd: D, *, source: str) -> None:
        """Only an ACTUALLY CREDITED reward, with its venue source."""
        if not source:
            raise StateError("a reward needs a venue source to be credited")
        self.credited_rewards_usd += D(str(amount_usd))

    def is_realized(self) -> bool:
        return bool(self.filled_qty > 0 and self.settled
                    and self.cash_confirmed)


def markouts(order: OrderRecord, marks: Dict[int, Optional[D]],
             settlement_mark: Optional[D] = None) -> Dict[str, Any]:
    """Real markouts after a fill. Mark source must be an observed bid.

    A horizon with no observation is None with a reason -- never carried
    forward from a nearer horizon, which would understate adverse
    selection.
    """
    out: Dict[str, Any] = {"fill_price": str(order.price),
                           "mark_source": "observed_best_bid",
                           "LABEL": "MARK_TO_MARKET_NOT_REALIZED"}
    for k in MARKOUT_HORIZONS:
        m = marks.get(k)
        out["t_plus_%d" % k] = (
            {"markout": None, "reason": "NO_OBSERVATION"} if m is None
            else {"markout": str(D(str(m)) - order.price),
                  "mark": str(m)})
    out["settlement"] = (
        {"markout": None, "reason": "NOT_SETTLED"} if settlement_mark is None
        else {"markout": str(D(str(settlement_mark)) - order.price),
              "mark": str(settlement_mark),
              "LABEL": "REALIZED_only_if_cash_confirmed"})
    return out


def account(orders: List[OrderRecord]) -> Dict[str, Any]:
    """The five separate lines. Never summed into a single headline."""
    sh: Dict[str, D] = {}
    cost: Dict[str, D] = {}
    fees = D("0")
    rewards = D("0")
    for o in orders:
        if o.filled_qty <= 0:
            continue
        sh[o.outcome] = sh.get(o.outcome, D("0")) + o.filled_qty
        cost[o.outcome] = cost.get(o.outcome, D("0")) + o.filled_qty * o.price
        fees += o.fees_usd
        rewards += o.credited_rewards_usd
    ks = sorted(sh)
    matched = min(sh[ks[0]], sh[ks[1]]) if len(ks) >= 2 else D("0")
    pair_cost = sum((cost[k] / sh[k] for k in ks[:2] if sh[k] > 0), D("0"))
    matched_econ = matched * (D("1") - pair_cost) if matched > 0 else D("0")
    resid_shares = (abs(sh[ks[0]] - sh[ks[1]]) if len(ks) >= 2
                    else (sh[ks[0]] if ks else D("0")))
    realized = [o for o in orders if o.is_realized()]
    return {
        "probe": PROBE, "spec_fingerprint": MICRO_FINGERPRINT,
        "matched_pair_economics_usd": str(matched_econ),
        "matched_shares": str(matched),
        "pair_cost": str(pair_cost),
        "directional_residual_shares": str(resid_shares),
        "directional_residual_pnl_usd": "UNRESOLVED_UNTIL_SETTLEMENT",
        "fees_usd": str(fees),
        "credited_rewards_usd": str(rewards),
        "final_net_usd": ("UNRESOLVED_UNTIL_CASH_CONFIRMED"
                          if len(realized) < len([o for o in orders
                                                  if o.filled_qty > 0])
                          else "see realized_lines"),
        "n_orders": len(orders),
        "n_orders_with_fills": sum(1 for o in orders if o.filled_qty > 0),
        "n_orders_REALIZED": len(realized),
        "states": {o.client_id: o.state for o in orders},
        "RESIDUAL_DRAG_RATIO": (
            str(abs(D(str(resid_shares))) / matched) if matched > 0 else None),
        "L5_REFERENCE_DRAG_RATIO": 1.1217,
        "LANGUAGE": ("SUBMITTED is not a fill; a fill is not profit; "
                     "mark-to-market is not realized; only "
                     "FILLED+SETTLED+CASH_CONFIRMED is REALIZED"),
    }
