"""One-shot public-data-only Gemini/Kalshi 15m basis-transition study."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

import httpx

from src.gemini_kalshi_basis_transition_analyze import summarize
from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import match_settled_true_windows
from src.gemini_kalshi_threshold_dominance_7d import ASSETS, KALSHI_15M_SERIES, _kalshi_series
from src.gemini_kalshi_threshold_dominance_study import classify

logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_transition_run")


async def run(hours: int, gemini_limit: int, max_window_end_skew_s: float) -> None:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        gemini_task = asyncio.create_task(_gemini_settled(http, gemini_limit))
        kalshi_tasks = {
            asset: asyncio.create_task(_kalshi_series(http, hours=hours, series_ticker=series))
            for asset, series in KALSHI_15M_SERIES.items()
        }
        gemini = await gemini_task
        kalshi_by_asset = {asset: await task for asset, task in kalshi_tasks.items()}

    kalshi = [market for asset in ASSETS for market in kalshi_by_asset.get(asset, [])]
    matched = match_settled_true_windows(gemini, kalshi, max_window_end_skew_s)
    event_by_ticker = {str(e.get("ticker") or ""): e for e in gemini}
    market_by_ticker = {str(m.get("ticker") or ""): m for m in kalshi}
    enriched = []
    for row in matched:
        event = event_by_ticker.get(str(row.gemini_ticker))
        market = market_by_ticker.get(str(row.kalshi_ticker))
        if event is not None and market is not None:
            enriched.append(classify(row, event, market))

    payload = summarize(enriched)
    payload.update({
        "requested_hours": hours,
        "matched_15m_windows": len(enriched),
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    })
    logger.info(
        "CRYPTO_BASIS_TRANSITION_SUMMARY %s",
        json.dumps(payload, separators=(",", ":"), default=str),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=168)
    parser.add_argument("--gemini-limit", type=int, default=5000)
    parser.add_argument("--max-window-end-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.hours, args.gemini_limit, args.max_window_end_skew_s))


if __name__ == "__main__":
    main()
