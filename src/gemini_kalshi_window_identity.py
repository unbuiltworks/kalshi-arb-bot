"""Authoritative read-only identity helpers for Gemini/Kalshi 15-minute research.

Kalshi's ``expected_expiration_time`` is when the outcome is expected to be known;
for the short-duration crypto contracts the market's actual measurement window ends
at ``close_time``. The contract rules confirm the same endpoint. Cross-venue window
matching therefore uses Kalshi ``close_time`` and the KX<ASSET>15M series ticker.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

KALSHI_15M_RE = re.compile(r"^KX(BTC|ETH|SOL|XRP)15M(?:-|$)", re.I)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def kalshi_15m_asset(market: dict[str, Any]) -> str | None:
    """Identify supported 15m crypto from the series/event/market ticker."""
    for value in (market.get("ticker"), market.get("event_ticker")):
        match = KALSHI_15M_RE.match(str(value or ""))
        if match:
            return match.group(1).upper()
    return None


def kalshi_window_end(market: dict[str, Any]) -> datetime | None:
    """Return the contract measurement-window endpoint, not settlement ETA."""
    return parse_dt(market.get("close_time"))


def kalshi_expected_resolution(market: dict[str, Any]) -> datetime | None:
    """Return Kalshi's forecasted outcome-known time for latency accounting."""
    return parse_dt(market.get("expected_expiration_time"))
