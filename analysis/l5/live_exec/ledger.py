"""LIVE-L5-EXEC-1 ledger: placement capture and SINGLE-LEG accounting.

Reuses the already-tested lifecycle machinery (OrderRecord, markouts) from
the micro probe rather than reimplementing it, so the seven states, the
partial-never-promoted rule, the venue-role requirement and the
markout-horizon honesty all carry over unchanged.

What differs: this is ONE LEG, so matched-pair economics are N/A -- reported
as N/A, never as zero -- and there is a hard one-order ceiling.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

# the lifecycle, markouts and language discipline are already tested
from analysis.l5.live_micro.ledger import (LIFECYCLE, OrderRecord, StateError,
                                           markouts)
from analysis.l5.live_exec.spec import (EXEC1_FINGERPRINT,
                                        MAX_POSSIBLE_LOSS_USD, PROBE, SPEC,
                                        VENUE_MAX_PRICE, VENUE_MIN_QTY,
                                        assert_frozen)

ARM_KEYS = ("PMUS_EXEC1_LIVE", "PMUS_EXEC1_SESSION_ID", "PMUS_EXEC1_ARM",
            "PMUS_EXEC1_GATE_EVIDENCE")


def exec1_armed() -> bool:
    """EXEC-1's own four keys. Isolated: no other executor's arming can
    arm this one, and arming this one arms nothing else."""
    sid = os.getenv("PMUS_EXEC1_SESSION_ID", "").strip()
    return (os.getenv("PMUS_EXEC1_LIVE", "").strip().upper() == "YES"
            and bool(sid)
            and os.getenv("PMUS_EXEC1_ARM", "").strip() == sid)


def may_submit(slug: str) -> Dict[str, Any]:
    gated = bool(slug) and os.getenv("PMUS_EXEC1_GATE_EVIDENCE",
                                     "").strip() == slug
    armed = exec1_armed()
    return {"armed": armed, "gate_asserted_for_slug": gated,
            "may_submit": bool(armed and gated),
            "refusal": (None if (armed and gated) else
                        "DISARMED: the owner has not set the four "
                        "PMUS_EXEC1_* keys for this slug.")}


def validate_size(min_qty: Optional[Any], tick: Optional[Any],
                  price: Optional[Any]) -> Dict[str, Any]:
    """Fail-closed on the venue's own grid. No universal constant."""
    reasons: List[str] = []
    if min_qty is None:
        reasons.append("MIN_QTY_ABSENT")
    elif D(str(min_qty)) >= 1:
        reasons.append("MIN_QTY_NOT_LESS_THAN_1:%s" % min_qty)
    if tick is None:
        reasons.append("TICK_ABSENT")
    else:
        t = D(str(tick))
        if t <= 0 or t >= 1:
            reasons.append("TICK_IMPLAUSIBLE:%s" % tick)
        elif price is not None and (D(str(price)) / t) % 1 != 0:
            reasons.append("PRICE_OFF_GRID:%s/%s" % (price, tick))
    if price is not None and D(str(price)) > VENUE_MAX_PRICE:
        reasons.append("PRICE_ABOVE_VENUE_CEILING:%s" % price)
    return {"pass": not reasons, "reasons": reasons,
            "quantity_to_use": (str(D(str(min_qty))) if min_qty is not None
                                else None)}


def capture_placement(*, side: str, price: Any, bbo_bid: Any, bbo_ask: Any,
                      depth_ahead: Any, quantity: Any, tte_s: Any,
                      book_timestamp: Any, book_age_s: Any,
                      order_timestamp: Any) -> Dict[str, Any]:
    """Every field the protocol requires at placement, nothing inferred."""
    spread = (None if bbo_bid is None or bbo_ask is None
              else str(D(str(bbo_ask)) - D(str(bbo_bid))))
    fresh = (book_age_s is not None
             and float(book_age_s) <= SPEC["book"]["staleness_bound_s"])
    return {
        "probe": PROBE, "spec_fingerprint": EXEC1_FINGERPRINT,
        "side": side, "price": str(price),
        "bbo_bid": None if bbo_bid is None else str(bbo_bid),
        "bbo_ask": None if bbo_ask is None else str(bbo_ask),
        "spread": spread,
        "depth_ahead_at_our_price": (None if depth_ahead is None
                                     else str(depth_ahead)),
        "depth_ahead_is_anonymous": True,
        "quantity": str(quantity), "tte_s": tte_s,
        "book_timestamp": book_timestamp, "book_age_s": book_age_s,
        "book_fresh_within_bound": fresh,
        "bbo_source": SPEC["book"]["bbo_source"],
        "order_timestamp": order_timestamp,
        "submitted_post_only": True,
        "submitted_post_only_implies_maker": False,
    }


def exec_account(order: Optional[OrderRecord]) -> Dict[str, Any]:
    """Single-leg accounting. matched is N/A, never zero."""
    filled = D("0") if order is None else order.filled_qty
    realized = bool(order is not None and order.is_realized())
    return {
        "probe": PROBE, "spec_fingerprint": EXEC1_FINGERPRINT,
        "matched_pair_economics": "N/A_SINGLE_LEG_EXPERIMENT",
        "matched_is_not_zero_it_is_not_applicable": True,
        "directional_residual_shares": str(filled),
        "directional_residual_pnl_usd": (
            "UNRESOLVED_UNTIL_SETTLEMENT" if not realized
            else "see settlement"),
        "fees_usd": "0" if order is None else str(order.fees_usd),
        "credited_rewards_usd": ("0" if order is None
                                 else str(order.credited_rewards_usd)),
        "final_net_usd": ("UNRESOLVED_UNTIL_CASH_CONFIRMED" if not realized
                          else "see settlement"),
        "venue_confirmed_liquidity_role": (
            None if order is None else (order.maker_taker or "UNRESOLVED")),
        "state": None if order is None else order.state,
        "n_orders": 0 if order is None else 1,
        "max_orders_this_experiment": SPEC["orders"][
            "max_orders_this_experiment"],
        "MAX_POSSIBLE_LOSS_USD": str(MAX_POSSIBLE_LOSS_USD),
        "orders_submitted_by_this_session": 0,
        "LANGUAGE": ("SUBMITTED is not a fill; a fill is not profit; "
                     "mark-to-market is not realized; only "
                     "FILLED+SETTLED+CASH_CONFIRMED is REALIZED"),
        "DOES_NOT_TEST_FULL_L5_ECONOMICS": True,
    }


class OneOrderOnly:
    """A hard ceiling of ONE order. A second request is refused, including
    a request made to repair a loss on the first."""

    def __init__(self) -> None:
        self.order: Optional[OrderRecord] = None
        self.refusals: List[str] = []

    def request(self, slug: str, outcome: str, price: Any,
                qty: Any) -> OrderRecord:
        assert_frozen()
        if self.order is not None:
            self.refusals.append("SECOND_ORDER_REFUSED")
            raise StateError(
                "LIVE-L5-EXEC-1 permits exactly ONE order. A second is "
                "refused, and repairing a loss is not an exception.")
        if D(str(qty)) != VENUE_MIN_QTY:
            raise StateError(
                "quantity must be the venue minimum %s, got %s"
                % (VENUE_MIN_QTY, qty))
        self.order = OrderRecord("exec1", slug, outcome, D(str(price)),
                                 D(str(qty)))
        return self.order
