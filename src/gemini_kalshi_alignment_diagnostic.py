"""Read-only diagnostic for Gemini/Kalshi 15-minute expiry alignment.

This intentionally does not price or trade anything. It answers whether the current
zero-overlap result is because no matching 15-minute windows exist, or because the
expiry timestamps are systematically offset. Kalshi short-duration crypto series
are identified directly from their KX<ASSET>15M ticker prefix so they are not lost
when human-readable title text omits the words "15 minutes".
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from statistics import median
from typing import Any

import httpx

from src.gemini_kalshi_fast_overlap import _gemini_active_crypto, _gemini_meta, _kalshi_expiry
from src.gemini_kalshi_rest_snapshot import _kalshi_open_paced

logger = logging.getLogger("kalshi_arb.gemini_kalshi_alignment_diagnostic")
KALSHI_15M_RE = re.compile(r"^KX(BTC|ETH|SOL|XRP)15M(?:-|$)", re.I)


def kalshi_15m_asset(market: dict[str, Any]) -> str | None:
    ticker = str(market.get("ticker") or "")
    event_ticker = str(market.get("event_ticker") or "")
    match = KALSHI_15M_RE.match(ticker) or KALSHI_15M_RE.match(event_ticker)
    return match.group(1).upper() if match else None


async def run_once() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_events, kalshi_markets = await asyncio.gather(
            _gemini_active_crypto(http), _kalshi_open_paced(http)
        )

    gemini_15m: list[tuple[dict[str, Any], str, Any]] = []
    for event in gemini_events:
        meta = _gemini_meta(event)
        if meta is not None:
            asset, duration, expiry = meta
            if duration == 15:
                gemini_15m.append((event, asset, expiry))

    kalshi_15m: list[tuple[dict[str, Any], str, Any]] = []
    for market in kalshi_markets:
        asset = kalshi_15m_asset(market)
        expiry = _kalshi_expiry(market)
        if asset and expiry:
            kalshi_15m.append((market, asset, expiry))
            logger.info("KALSHI_15M_LIFECYCLE %s", json.dumps({
                "asset": asset,
                "ticker": market.get("ticker"),
                "event_ticker": market.get("event_ticker"),
                "title": market.get("title"),
                "subtitle": market.get("subtitle"),
                "open_time": market.get("open_time"),
                "close_time": market.get("close_time"),
                "expected_expiration_time": market.get("expected_expiration_time"),
                "latest_expiration_time": market.get("latest_expiration_time"),
                "expiration_time": market.get("expiration_time"),
                "occurrence_datetime": market.get("occurrence_datetime"),
                "rules_primary": market.get("rules_primary"),
            }, separators=(",", ":")))

    nearest_rows: list[dict[str, Any]] = []
    skews_by_asset: dict[str, list[float]] = {}
    for event, asset, g_expiry in gemini_15m:
        candidates = []
        for market, k_asset, k_expiry in kalshi_15m:
            if k_asset != asset:
                continue
            skew = abs((g_expiry - k_expiry).total_seconds())
            candidates.append((skew, market, k_expiry))
        if not candidates:
            continue
        candidates.sort(key=lambda row: row[0])
        skew, market, k_expiry = candidates[0]
        skews_by_asset.setdefault(asset, []).append(skew)
        nearest_rows.append({
            "asset": asset,
            "gemini_ticker": event.get("ticker"),
            "gemini_expiry_utc": g_expiry.isoformat(),
            "kalshi_ticker": market.get("ticker"),
            "kalshi_expiry_utc": k_expiry.isoformat(),
            "kalshi_close_time": market.get("close_time"),
            "kalshi_expected_expiration_time": market.get("expected_expiration_time"),
            "nearest_skew_s": round(skew, 3),
        })

    nearest_rows.sort(key=lambda row: row["nearest_skew_s"])
    for row in nearest_rows[:20]:
        logger.info("GEMINI_KALSHI_ALIGNMENT_NEAREST %s", json.dumps(row, separators=(",", ":")))

    by_asset: dict[str, dict[str, Any]] = {}
    for asset, values in skews_by_asset.items():
        by_asset[asset] = {
            "gemini_windows_with_same_asset_candidate": len(values),
            "min_skew_s": min(values),
            "median_skew_s": median(values),
            "within_30s": sum(v <= 30 for v in values),
            "within_60s": sum(v <= 60 for v in values),
            "within_300s": sum(v <= 300 for v in values),
            "within_900s": sum(v <= 900 for v in values),
        }

    logger.info("GEMINI_KALSHI_ALIGNMENT_SUMMARY %s", json.dumps({
        "gemini_active_events": len(gemini_events),
        "gemini_15m_candidates": len(gemini_15m),
        "kalshi_open_markets": len(kalshi_markets),
        "kalshi_15m_candidates": len(kalshi_15m),
        "nearest_pairs_measured": len(nearest_rows),
        "by_asset": by_asset,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
