"""Fail-closed integrity audit for public Kalshi MLB order books.

This module is read-only and contains no authentication or order-entry path.  It is
kept separate from the pricing parser so research scanners can prove a displayed
YES/NO book is economically coherent *before* deriving complementary asks.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return result if result.is_finite() else None


def audit_kalshi_binary_book(payload: dict[str, Any]) -> dict[str, Any]:
    """Verify the best displayed YES/NO bids form a coherent binary book.

    Kalshi order-book responses expose bids for both outcomes.  Research code derives
    the opposite ask as ``1 - best_bid``.  That derivation is only defensible when both
    best bids are finite, inside [0, 1], have positive displayed quantity, and are not
    crossed (YES bid + NO bid <= 1).  Anything else fails closed.
    """
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    if not isinstance(book, dict):
        return {"verified": False, "blocker": "KALSHI_BOOK_MALFORMED"}

    yes = book.get("yes_dollars") or []
    no = book.get("no_dollars") or []
    if not isinstance(yes, list) or not isinstance(no, list) or not yes or not no:
        return {"verified": False, "blocker": "KALSHI_TWO_SIDED_DEPTH_MISSING"}

    try:
        yes_row = yes[-1]
        no_row = no[-1]
        yes_px = _decimal(yes_row[0])
        yes_qty = _decimal(yes_row[1])
        no_px = _decimal(no_row[0])
        no_qty = _decimal(no_row[1])
    except (IndexError, KeyError, TypeError):
        return {"verified": False, "blocker": "KALSHI_BOOK_MALFORMED"}

    if None in (yes_px, yes_qty, no_px, no_qty):
        return {"verified": False, "blocker": "KALSHI_BOOK_NONFINITE_OR_MALFORMED"}
    assert yes_px is not None and yes_qty is not None and no_px is not None and no_qty is not None

    if not (Decimal("0") <= yes_px <= Decimal("1")) or not (
        Decimal("0") <= no_px <= Decimal("1")
    ):
        return {"verified": False, "blocker": "KALSHI_BOOK_PRICE_OUT_OF_RANGE"}
    if yes_qty <= 0 or no_qty <= 0:
        return {"verified": False, "blocker": "KALSHI_TWO_SIDED_DEPTH_MISSING"}
    if yes_px + no_px > Decimal("1"):
        return {"verified": False, "blocker": "KALSHI_BINARY_BOOK_CROSSED"}

    return {
        "verified": True,
        "blocker": None,
        "yes_bid": str(yes_px),
        "yes_depth": str(yes_qty),
        "no_bid": str(no_px),
        "no_depth": str(no_qty),
        "derived_yes_ask": str(Decimal("1") - no_px),
        "derived_no_ask": str(Decimal("1") - yes_px),
        "read_only": True,
        "order_entry": False,
    }
