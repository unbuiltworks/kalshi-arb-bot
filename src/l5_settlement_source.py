"""Authoritative settlement for a PMUS window, from the venue's own field.

The standing rule on this branch is that a terminal `last` price is NOT
settlement evidence. This module accepts exactly one thing: the venue's
`settlementPx` on a market it reports as expired/settled. That field is the
venue's statement of what the contract paid, which is what settlement means.

The market's price is quoted for the LONG (UP) side, so:

    settlementPx == 1  ->  UP paid $1      -> winner "up"
    settlementPx == 0  ->  UP paid nothing -> winner "down"

Anything else -- a missing field, a still-open market, or a fractional value
-- raises. A fractional settlementPx would mean the payout is not the binary
we model, and guessing at it would silently corrupt every residual P&L.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Optional

SETTLED_STATES = ("MARKET_STATE_EXPIRED", "MARKET_STATE_SETTLED",
                  "MARKET_STATE_RESOLVED")


class SettlementUnavailable(Exception):
    """Raised instead of guessing a winner."""


def _val(v) -> Optional[D]:
    if isinstance(v, dict):
        v = v.get("value")
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                        # noqa: BLE001
        return None


def winner_from_bbo(bbo: Dict[str, Any]) -> str:
    """Map a venue `bbo` payload to 'up'/'down', or refuse."""
    md = bbo.get("marketData") if "marketData" in bbo else bbo
    if not isinstance(md, dict):
        raise SettlementUnavailable("no marketData in payload")
    state = str(md.get("state") or "")
    if state not in SETTLED_STATES:
        raise SettlementUnavailable(
            f"market state {state!r} is not settled; a last price is not "
            f"settlement evidence")
    px = _val(md.get("settlementPx"))
    if px is None:
        raise SettlementUnavailable("settlementPx is absent")
    if px == 1:
        return "up"
    if px == 0:
        return "down"
    raise SettlementUnavailable(
        f"settlementPx {px} is neither 0 nor 1; the payout is not the binary "
        f"this model assumes, so no winner is inferred")
