"""Rate-safe wrapper for the read-only Odds-API.io MLB prop trade hunter.

The free Odds-API.io plan is capped per hour. A prior research deployment could
retry the REST event bootstrap too aggressively after HTTP 429 responses. This
wrapper keeps the working WebSocket strategy intact but turns 429 into a long,
in-process cooldown rather than a crash/restart loop.

No order transport is added here. The wrapped hunter remains read-only and
settlement-rule blocked.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from src import odds_api_io_mlb_hot_shadow as base
from src import odds_api_io_mlb_prop_trade_hunter as hunter

logger = logging.getLogger("kalshi_arb.odds_api_io_rate_safe_prop_trade_hunter")

# Conservative default for the current free-plan hourly REST limit. Keeping the
# process alive also preserves Railway restart protection and avoids a 429 storm.
RATE_LIMIT_COOLDOWN_SECONDS = 3600.0
_original_fetch_mlb_events = base.fetch_mlb_events


async def _rate_safe_fetch_mlb_events(*args, **kwargs):
    while True:
        try:
            return await _original_fetch_mlb_events(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            response = exc.response
            if response is None or response.status_code != 429:
                raise
            logger.warning(
                "ODDS_API_IO_RATE_LIMIT_COOLDOWN status=429 cooldown_s=%s action=SLEEP_IN_PROCESS_NO_RESTART",
                int(RATE_LIMIT_COOLDOWN_SECONDS),
            )
            await asyncio.sleep(RATE_LIMIT_COOLDOWN_SECONDS)


# base.run_session resolves this global at call time, including when invoked via
# the hunter's captured _BASE_RUN_SESSION reference.
base.fetch_mlb_events = _rate_safe_fetch_mlb_events


if __name__ == "__main__":
    hunter.main()
