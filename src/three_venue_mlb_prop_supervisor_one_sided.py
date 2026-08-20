"""Three-venue MLB prop supervisor with safe one-sided Polymarket book support.

Thin Polymarket player-prop books can legitimately expose only bids or only offers.
The legacy strict parser rejects those markets entirely. This wrapper preserves the
existing scanner and settlement gates while representing only the actually
executable side; the absent side has zero depth and therefore cannot be selected by
the optimizer.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src import three_venue_mlb_prop_safe_scan as scan
from src.strict_mlb_prop_snapshot import TopBook

D = Decimal


def _decimal_field(row: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        value = row.get(name)
        if isinstance(value, dict):
            value = value.get("value")
        if value is None:
            continue
        try:
            parsed = D(str(value))
        except Exception:
            continue
        if parsed.is_finite():
            return parsed
    return None


def poly_top_book_one_sided(payload: dict[str, Any]) -> TopBook | None:
    """Return only executable Polymarket sides; never synthesize missing depth."""
    body = payload.get("marketData") if isinstance(payload.get("marketData"), dict) else payload
    if not isinstance(body, dict):
        return None
    bids = body.get("bids") or []
    offers = body.get("offers") or body.get("asks") or []
    bid = bids[0] if bids and isinstance(bids[0], dict) else None
    offer = offers[0] if offers and isinstance(offers[0], dict) else None
    if bid is None and offer is None:
        return None

    bid_px = _decimal_field(bid, "px", "price") if bid is not None else None
    bid_qty = _decimal_field(bid, "qty", "quantity", "size") if bid is not None else None
    ask_px = _decimal_field(offer, "px", "price") if offer is not None else None
    ask_qty = _decimal_field(offer, "qty", "quantity", "size") if offer is not None else None

    bid_valid = bool(
        bid_px is not None and bid_qty is not None
        and D("0") <= bid_px <= D("1") and bid_qty > 0
    )
    ask_valid = bool(
        ask_px is not None and ask_qty is not None
        and D("0") <= ask_px <= D("1") and ask_qty > 0
    )
    if not bid_valid and not ask_valid:
        return None
    if bid_valid and ask_valid and bid_px > ask_px:
        return None

    # Placeholder prices on absent sides are deliberately paired with zero depth.
    # The downstream optimizer sizes by min(depth), so it cannot execute them.
    yes_ask = ask_px if ask_valid else D("1")
    yes_depth = ask_qty if ask_valid else D("0")
    no_ask = D("1") - bid_px if bid_valid else D("1")
    no_depth = bid_qty if bid_valid else D("0")
    assert yes_ask is not None and yes_depth is not None and no_ask is not None and no_depth is not None
    return TopBook(
        yes_ask=yes_ask,
        yes_depth=yes_depth,
        no_ask=no_ask,
        no_depth=no_depth,
    )


# Patch the scanner's imported binding before the persistent supervisor starts.
scan.poly_top_book = poly_top_book_one_sided

from src import three_venue_mlb_prop_supervisor as supervisor  # noqa: E402


def main() -> None:
    supervisor.main()


if __name__ == "__main__":
    main()
