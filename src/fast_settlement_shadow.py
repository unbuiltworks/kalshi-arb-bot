"""Fast-settlement, read-only Polymarket US <-> Kalshi shadow lane.

This launcher reuses the strict MLB identity matcher and the event-driven websocket
scanner, but only keeps markets whose scheduled game start is close enough that the
full trade -> result -> settlement cycle can be observed the same day.

Safety properties:
- no order-entry code is added here;
- exact participant/line/start-time/result-source validation remains mandatory;
- stale or empty books still fail closed in the underlying event-driven scanner.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from typing import Any

import httpx

from src import polymarket_kalshi_shadow_v5 as scanner
from src.polymarket_kalshi_shadow_v4 import (
    _kalshi_scheduled_utc,
    strict_discover_matches as strict_mlb_discover,
)

logger = logging.getLogger("kalshi_arb.fast_settlement_shadow")


def _hours_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(0.0, value)


async def fast_discover_matches(http: httpx.AsyncClient) -> list[Any]:
    """Return only strict MLB total pairs near enough to settle today.

    Upcoming games are included up to FAST_START_AHEAD_HOURS. Recently started
    games remain eligible for FAST_RECENT_START_HOURS so an in-play opportunity can
    still be measured while the game is underway.
    """
    ahead_h = _hours_env("FAST_START_AHEAD_HOURS", 12.0)
    recent_h = _hours_env("FAST_RECENT_START_HOURS", 4.5)
    now = datetime.now(timezone.utc)
    strict = await strict_mlb_discover(http)
    selected: list[Any] = []

    for match in strict:
        start = _kalshi_scheduled_utc(match.kalshi.rules_primary)
        if start is None:
            continue
        delta_h = (start - now).total_seconds() / 3600.0
        if -recent_h <= delta_h <= ahead_h:
            selected.append(match)
            logger.info(
                "FAST_PAIR %s",
                json.dumps(
                    {
                        "event": match.poly.event_title,
                        "line": str(match.poly.line),
                        "poly_slug": match.poly.slug,
                        "kalshi_ticker": match.kalshi.ticker,
                        "scheduled_start_utc": start.isoformat(),
                        "hours_to_start": round(delta_h, 3),
                        "window_ahead_h": ahead_h,
                        "recent_start_h": recent_h,
                        "strict_identity": True,
                        "shadow_only": True,
                    },
                    separators=(",", ":"),
                ),
            )

    selected.sort(
        key=lambda m: abs(
            (_kalshi_scheduled_utc(m.kalshi.rules_primary) - now).total_seconds()
        )
    )
    logger.info(
        "FAST_DISCOVERY %s",
        json.dumps(
            {
                "strict_matches": len(strict),
                "selected": len(selected),
                "ahead_hours": ahead_h,
                "recent_start_hours": recent_h,
                "shadow_only": True,
            },
            separators=(",", ":"),
        ),
    )
    return selected


# v5 resolves this module global at runtime, so replacing it preserves all of the
# event-driven book/fee/depth/latency logic while narrowing discovery only.
scanner.strict_discover_matches = fast_discover_matches


if __name__ == "__main__":
    scanner.main()
