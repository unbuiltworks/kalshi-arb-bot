"""Venue adapters: market-agnostic separation of venue parsing from policy.

The consolidation spec (issue #167) requires that everything the pipeline
LEARNS (state buckets, action distributions, pair accounting, comparison
logic) be expressed against a normalized vocabulary, with all venue-specific
knowledge — slug formats, outcome label conventions, field names — isolated
behind an adapter. Swapping venues (Polymarket up/down -> Kalshi KXBTC15M,
another binary market family, ...) then means writing one new adapter, not
touching policy or analysis code.

An adapter answers exactly two questions about a raw venue fill dict:

  parse_window(raw)   -> WindowInfo | None   which binary window is this?
  normalize_fill(raw) -> NormalizedFill | None   what happened, normalized?

Normalized vocabulary (matches canonical_sequence schema v1):
  outcome  "Up" / "Down"        (first-listed outcome / its complement)
  side     "BUY" / "SELL"
  price    dollars per share in [0, 1]
  quantity shares
  dollars  cash notional (price*quantity when the venue omits it)

Read-only research code. Nothing here trades or authenticates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from .realtime_mirror_base import fnum, normalize_outcome


@dataclass(frozen=True)
class WindowInfo:
    """A single binary settlement window on some venue."""
    venue: str
    window_id: str            # canonical id (the slug on Polymarket)
    asset: str                # "BTC", "ETH", ...
    duration_minutes: int
    window_start: int         # epoch seconds
    window_end: int           # epoch seconds (settlement)


@dataclass(frozen=True)
class NormalizedFill:
    """One fill in the canonical vocabulary, venue details erased."""
    timestamp: int
    outcome: str              # "Up" | "Down"
    side: str                 # "BUY" | "SELL"
    price: float
    quantity: float
    dollars: float
    transaction_id: str


class VenueAdapter(Protocol):
    """What policy/analysis code is allowed to know about a venue."""

    venue: str

    def parse_window(self, raw: Dict[str, Any]) -> Optional[WindowInfo]:
        """Identify the binary window a raw fill belongs to, or None."""
        ...

    def normalize_fill(self, raw: Dict[str, Any]) -> Optional[NormalizedFill]:
        """Normalize a raw fill, or None if it isn't a usable trade."""
        ...


class PolymarketUpDownAdapter:
    """Polymarket short-crypto up/down windows (L5's habitat).

    Slug format: ``{asset}-updown-{5m|15m}-{window_start_epoch}``.
    Outcome labels arrive as Up/Down (any case) or yes/no on some tokens;
    normalize_outcome maps both, yes->Up / no->Down by design.
    """

    venue = "polymarket"
    WINDOW_RE = re.compile(r"^(btc|eth)-updown-(5m|15m)-(\d+)$", re.IGNORECASE)

    def parse_window(self, raw: Dict[str, Any]) -> Optional[WindowInfo]:
        slug = str(raw.get("slug") or raw.get("eventSlug") or "")
        m = self.WINDOW_RE.match(slug)
        if not m:
            return None
        start = int(m.group(3))
        dur_min = 5 if m.group(2).lower() == "5m" else 15
        return WindowInfo(venue=self.venue, window_id=slug,
                          asset=m.group(1).upper(), duration_minutes=dur_min,
                          window_start=start,
                          window_end=start + dur_min * 60)

    def normalize_fill(self, raw: Dict[str, Any]) -> Optional[NormalizedFill]:
        if raw.get("type") not in (None, "TRADE"):
            return None
        outcome = str(normalize_outcome(raw.get("outcome")) or "").title()
        if outcome not in ("Up", "Down"):
            return None
        side = str(raw.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            return None
        price = fnum(raw.get("price")) or 0.0
        qty = fnum(raw.get("size")) or 0.0
        return NormalizedFill(
            timestamp=int(fnum(raw.get("timestamp")) or 0),
            outcome=outcome, side=side, price=price, quantity=qty,
            dollars=fnum(raw.get("usdcSize")) or price * qty,
            transaction_id=str(raw.get("transactionHash") or ""),
        )


DEFAULT_ADAPTER = PolymarketUpDownAdapter()
