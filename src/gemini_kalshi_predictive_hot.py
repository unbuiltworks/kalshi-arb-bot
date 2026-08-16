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
from src import gemini_kalshi_multi_ws_hot_catalog as catalog

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


def _log_pair(owner, pair: Pair) -> None:
    end_key = pair.window_end.isoformat()
    last = getattr(owner, "_predictive_last_logged", {})
    if last.get(pair.asset) == end_key:
        return
    last[pair.asset] = end_key
    setattr(owner, "_predictive_last_logged", last)
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


class PredictivePairDiscovery:
    """Drop-in PairDiscoveryCache with no REST pair-discovery calls."""

    def __init__(self, ttl_s: float = 1.0) -> None:
        del ttl_s
        self._predictive_last_logged: dict[str, str] = {}

    async def current_pair(self, http: httpx.AsyncClient, asset: str) -> Pair:
        del http
        pair = predicted_pair(asset)
        _log_pair(self, pair)
        return pair


async def _predictive_catalog_current_pair(self, http: httpx.AsyncClient, asset: str) -> Pair:
    """Hard override in case the catalog class was already captured by the scanner."""
    del http
    pair = predicted_pair(asset)
    _log_pair(self, pair)
    return pair


# Apply both override forms. Python normally resolves PairDiscoveryCache dynamically
# from the HOT module, but patching the catalog method too makes the no-REST guarantee
# robust even if an imported wrapper retained BackgroundPairCatalog itself.
hot.PairDiscoveryCache = PredictivePairDiscovery
catalog.BackgroundPairCatalog.current_pair = _predictive_catalog_current_pair


def main() -> None:
    logger.info("CRYPTO_HOT_PREDICTIVE_MODE_ENABLED read_only=true order_entry=false")
    verified.main()


if __name__ == "__main__":
    main()
