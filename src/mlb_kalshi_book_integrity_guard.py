"""Fail-closed integrity checks for Kalshi MLB orderbook snapshots.

Read-only research helper.  This does not place orders or alter execution state.
It exists to prevent malformed/crossed Kalshi YES/NO books from manufacturing
false cross-venue paper-arbitrage economics.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _finite_decimal(value: Any) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def audit_kalshi_top_book_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Audit the top Kalshi YES/NO bid pair used to derive executable asks.

    Kalshi exposes YES and NO bid books.  The opposite-side executable ask is
    derived as ``1 - best_opposite_bid``.  A malformed price outside [0, 1],
    non-positive/non-finite quantity, or crossed YES+NO bids (> 1) can produce
    impossible derived asks and false cross-venue locks, so such snapshots are
    rejected rather than normalized or guessed around.
    """
    blockers: list[str] = []
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    if not isinstance(book, dict):
        book = {}

    yes = book.get("yes_dollars") or []
    no = book.get("no_dollars") or []
    if not isinstance(yes, list) or not isinstance(no, list) or not yes or not no:
        blockers.append("KALSHI_BOOK_TWO_SIDED_DEPTH_REQUIRED")
        return {
            "verified": False,
            "blockers": blockers,
            "yes_bid": None,
            "no_bid": None,
            "yes_depth": None,
            "no_depth": None,
            "read_only": True,
            "order_entry": False,
            "live_probe_ready": False,
        }

    try:
        yes_level = yes[-1]
        no_level = no[-1]
        yes_bid = _finite_decimal(yes_level[0])
        yes_qty = _finite_decimal(yes_level[1])
        no_bid = _finite_decimal(no_level[0])
        no_qty = _finite_decimal(no_level[1])
    except (IndexError, TypeError):
        yes_bid = yes_qty = no_bid = no_qty = None

    if yes_bid is None or no_bid is None:
        blockers.append("KALSHI_BOOK_PRICE_INVALID")
    else:
        if not (Decimal("0") <= yes_bid <= Decimal("1")):
            blockers.append("KALSHI_YES_BID_OUT_OF_RANGE")
        if not (Decimal("0") <= no_bid <= Decimal("1")):
            blockers.append("KALSHI_NO_BID_OUT_OF_RANGE")
        if (
            Decimal("0") <= yes_bid <= Decimal("1")
            and Decimal("0") <= no_bid <= Decimal("1")
            and yes_bid + no_bid > Decimal("1")
        ):
            blockers.append("KALSHI_BOOK_CROSSED")

    if yes_qty is None or no_qty is None:
        blockers.append("KALSHI_BOOK_QUANTITY_INVALID")
    else:
        if yes_qty <= 0:
            blockers.append("KALSHI_YES_DEPTH_NONPOSITIVE")
        if no_qty <= 0:
            blockers.append("KALSHI_NO_DEPTH_NONPOSITIVE")

    return {
        "verified": not blockers,
        "blockers": blockers,
        "yes_bid": str(yes_bid) if yes_bid is not None else None,
        "no_bid": str(no_bid) if no_bid is not None else None,
        "yes_depth": str(yes_qty) if yes_qty is not None else None,
        "no_depth": str(no_qty) if no_qty is not None else None,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
