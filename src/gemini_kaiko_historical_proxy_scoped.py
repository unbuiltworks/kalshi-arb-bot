"""Series-scoped launcher for historical Gemini/Kaiko proxy calibration.

This wrapper fixes the inherited broad Kalshi settlement scan by replacing it with
four explicitly scoped 15-minute crypto-series queries before invoking the existing
historical analyzer. Read-only; no order-entry path.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging

import httpx

from src import gemini_kaiko_historical_proxy as hist
from src.gemini_kalshi_fast_overlap import KALSHI_REST

logger = logging.getLogger("kalshi_arb.gemini_kaiko_historical_proxy_scoped")
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M")


async def _kalshi_settled_scoped(http: httpx.AsyncClient, hours: int) -> list[dict]:
    rows: list[dict] = []
    min_settled_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())

    for series_index, series_ticker in enumerate(SERIES):
        cursor = ""
        page = 0
        while True:
            params = {
                "series_ticker": series_ticker,
                "status": "settled",
                "limit": 1000,
                "mve_filter": "exclude",
                "min_settled_ts": min_settled_ts,
            }
            if cursor:
                params["cursor"] = cursor

            response: httpx.Response | None = None
            for attempt in range(6):
                response = await http.get(f"{KALSHI_REST}/markets", params=params)
                if response.status_code != 429:
                    break
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(8.0, 0.75 * (2 ** attempt))
                except (TypeError, ValueError):
                    delay = min(8.0, 0.75 * (2 ** attempt))
                logger.warning(
                    "KALSHI_HIST_PROXY_429 series=%s page=%d attempt=%d sleep_s=%.2f",
                    series_ticker, page, attempt + 1, delay,
                )
                await asyncio.sleep(max(0.05, delay))

            assert response is not None
            response.raise_for_status()
            payload = response.json()
            page_rows = list(payload.get("markets", []))
            rows.extend(page_rows)
            cursor = str(payload.get("cursor") or "")
            logger.info(
                "KALSHI_HIST_PROXY_SERIES_PAGE series=%s page=%d rows=%d cumulative=%d",
                series_ticker, page, len(page_rows), len(rows),
            )
            page += 1
            if not cursor:
                break
            await asyncio.sleep(0.25)

        if series_index + 1 < len(SERIES):
            await asyncio.sleep(0.15)

    return rows


# The analyzer resolves this imported global at runtime. Replace only the settlement
# fetcher; all matching, proxy math and statistics remain unchanged.
hist._kalshi_settled_paced = _kalshi_settled_scoped


def main() -> None:
    hist.main()


if __name__ == "__main__":
    main()
