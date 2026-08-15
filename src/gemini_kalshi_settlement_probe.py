"""Paced read-only wrapper for the Gemini/Kalshi settlement-agreement study."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging

import httpx

from src.gemini_kalshi_fast_overlap import KALSHI_REST
from src.gemini_kalshi_settlement_agreement import _gemini_settled, match_settled

logger = logging.getLogger("kalshi_arb.gemini_kalshi_settlement_probe")


async def _kalshi_settled_paced(http: httpx.AsyncClient, hours: int) -> list[dict]:
    rows: list[dict] = []
    cursor = ""
    page = 0
    min_settled_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    while True:
        params = {
            "status": "settled",
            "limit": 1000,
            "mve_filter": "exclude",
            "min_settled_ts": min_settled_ts,
        }
        if cursor:
            params["cursor"] = cursor

        response = None
        for attempt in range(6):
            response = await http.get(f"{KALSHI_REST}/markets", params=params)
            if response.status_code != 429:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(8.0, 0.75 * (2 ** attempt))
            except ValueError:
                delay = min(8.0, 0.75 * (2 ** attempt))
            logger.warning("KALSHI_SETTLED_429 page=%s attempt=%s sleep_s=%.2f", page, attempt + 1, delay)
            await asyncio.sleep(delay)

        assert response is not None
        response.raise_for_status()
        payload = response.json()
        rows.extend(payload.get("markets", []))
        cursor = str(payload.get("cursor") or "")
        page += 1
        if not cursor:
            return rows
        await asyncio.sleep(0.35)


async def run(hours: int, gemini_limit: int, max_expiry_skew_s: float) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled_paced(http, hours),
        )

    rows = match_settled(gemini, kalshi, max_expiry_skew_s=max_expiry_skew_s)
    disagreements = [row for row in rows if not row.agrees]
    by_asset: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = by_asset.setdefault(row.asset, {"matched": 0, "agreed": 0, "disagreed": 0})
        bucket["matched"] += 1
        bucket["agreed" if row.agrees else "disagreed"] += 1
    for row in disagreements:
        logger.info("GEMINI_KALSHI_DISAGREEMENT %s", json.dumps(row.as_dict(), separators=(",", ":")))
    logger.info("GEMINI_KALSHI_AGREEMENT_SUMMARY %s", json.dumps({
        "gemini_settled_events_scanned": len(gemini),
        "kalshi_settled_markets_scanned": len(kalshi),
        "matched_15m_windows": len(rows),
        "agreements": len(rows) - len(disagreements),
        "disagreements": len(disagreements),
        "agreement_rate": ((len(rows) - len(disagreements)) / len(rows)) if rows else None,
        "by_asset": by_asset,
        "hours": hours,
        "max_expiry_skew_s": max_expiry_skew_s,
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
    }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--gemini-limit", type=int, default=500)
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.hours, args.gemini_limit, args.max_expiry_skew_s))


if __name__ == "__main__":
    main()
