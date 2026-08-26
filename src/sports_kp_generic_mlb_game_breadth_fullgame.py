"""Read-only full-game-only wrapper for the generic K/P MLB breadth census.

The generic normalizer currently labels every Polymarket MLB game market as
``full_contest``.  That can accidentally compare first-five/partial-game
Polymarket markets with full-game Kalshi markets and manufacture false edge.

This wrapper fails closed on clearly partial-game Polymarket labels before the
existing semantic matching/pricing pipeline sees them.  It adds no venue
requests and no order-entry capability.
"""
from __future__ import annotations

import re

from src import sports_kp_generic_mlb_game_breadth as breadth


_PARTIAL_GAME_PATTERNS = (
    re.compile(r"\bfirst\s+(?:3|three|5|five)\s+innings?\b", re.IGNORECASE),
    re.compile(r"\b1st\s+(?:3|three|5|five)\s+innings?\b", re.IGNORECASE),
    re.compile(r"\bfirst\s+inning\b", re.IGNORECASE),
    re.compile(r"\b1st\s+inning\b", re.IGNORECASE),
    re.compile(r"\bf\s*5\b", re.IGNORECASE),
)


def is_clearly_partial_game_label(source_label: str | None) -> bool:
    """Return True only for labels that explicitly identify a partial game."""
    text = (source_label or "").replace("-", " ")
    return any(pattern.search(text) for pattern in _PARTIAL_GAME_PATTERNS)


_ORIGINAL_NORMALIZE_POLYMARKET = breadth.normalize_polymarket_mlb


def _normalize_polymarket_full_game(*args, **kwargs):
    side = _ORIGINAL_NORMALIZE_POLYMARKET(*args, **kwargs)
    if side is not None and is_clearly_partial_game_label(side.source_label):
        return None
    return side


def main() -> None:
    # Monkey-patch only the read-only breadth module's imported normalizer.
    # Exact semantic identity, <=5-minute start-time, pricing, fee, and
    # deduplication rules remain owned by the existing production census.
    breadth.normalize_polymarket_mlb = _normalize_polymarket_full_game
    breadth.main()


if __name__ == "__main__":
    main()
