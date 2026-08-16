"""Read-only predictive pair identity for Gemini/Kalshi 15-minute crypto HOT.

This module removes REST pair discovery from the rollover path. The next contract
identities are deterministic from the next quarter-hour boundary:

- Gemini: GEMI-{ASSET}15M%y%m%d%H%M-UP in UTC
- Kalshi: KX{ASSET}15M-%y%b%d%H%M-15 in America/New_York

The prediction is not treated as authoritative market metadata. It becomes useful only
when the existing authenticated venue WebSockets accept the identifiers and deliver
real top-of-book data; the HOT scanner remains read-only and settlement-mismatch gated.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import time
from zoneinfo import ZoneInfo

import httpx

from src.gemini_kalshi_aligned_watch import Pair
from src import gemini_kalshi_hot_verified_entry as verified

hot = verified.hot
logger = logging.getLogger("kalshi_arb.gemini_kalshi_predictive_hot")
ET = ZoneInfo("America/New_York")
WINDOW_S = 15 * 60


def _next_window_end(now_ts: float | None = None) -> datetime:
    now = time.time() if now_ts is None else float(now_ts)
    end_epoch = (int(now) // WINDOW_S + 1) * WINDOW_S
    return datetime.fromtimestamp(end_epoch, tz=timezone.utc)


def predicted_pair(asset: str, now_ts: float | None = None) -> Pair:
    asset = asset.upper()
    end = _next_window_end(now_ts)
    local_end = end.astimezone(ET)
    gemini_symbol = f"GEMI-{asset}15M{end.strftime('%y%m%d%H%M')}-UP"
    kalshi_ticker = f"KX{asset}15M-{local_end.strftime('%y%b%d%H%M').upper()}-15"
    return Pair(
        asset=asset,
        window_end=end,
        gemini_symbol=gemini_symbol,
        kalshi_ticker=kalshi_ticker,
    )


class PredictivePairDiscovery:
    """Drop-in PairDiscoveryCache with no REST pair-discovery calls."""

    def __init__(self, ttl_s: float = 1.0) -> None:
        self._last_logged: dict[str, str] = {}

    async def current_pair(self, http: httpx.AsyncClient, asset: str) -> Pair:
        del http
        pair = predicted_pair(asset)
        end_key = pair.window_end.isoformat()
        if self._last_logged.get(pair.asset) != end_key:
            self._last_logged[pair.asset] = end_key
            logger.info(
                "CRYPTO_HOT_PREDICTED_PAIR %s",
                json.dumps({
                    "asset": pair.asset,
                    "window_end_utc": end_key,
                    "gemini_symbol": pair.gemini_symbol,
                    "kalshi_ticker": pair.kalshi_ticker,
                    "identity_source": "DETERMINISTIC_QUARTER_HOUR_CLOCK",
                    "validated_only_by_live_ws_data": True,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )
        return pair


# Override the catalog installed by verified_entry. Fee metadata remains a cold-path
# startup check, but pair identity at every 15-minute rollover is now REST-free.
hot.PairDiscoveryCache = PredictivePairDiscovery


def main() -> None:
    verified.main()


if __name__ == "__main__":
    main()
