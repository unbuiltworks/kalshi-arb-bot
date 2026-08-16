"""Coverage-verified historical Gemini/Kaiko proxy calibration launcher.

This wrapper fixes two inherited data-quality hazards before invoking the historical
analyzer:

1. Kalshi settlement history is restricted to the four 15-minute crypto series.
2. Gemini public spot trades are paged with ``since_tid`` whenever the 500-record
   response cap is reached before the target close, so T-minus observations cannot
   silently use a stale truncated batch.

Read-only; no order-entry path.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import httpx

from src import gemini_kaiko_historical_proxy as hist
from src.gemini_kalshi_fast_overlap import KALSHI_REST

logger = logging.getLogger("kalshi_arb.gemini_kaiko_historical_proxy_scoped")
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M")
TRADE_PAGE_LIMIT = 500
MAX_TRADE_PAGES = 20
# The base analyzer starts max(offsets)+10 seconds before close. Its default replay
# offsets currently top out at 60s, so this is the exact target-close horizon used
# by the deployed historical study.
TRADE_COVERAGE_HORIZON_MS = int((max(hist.DEFAULT_OFFSETS_S) + 10.0) * 1000)


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


async def _request_trade_page(
    http: httpx.AsyncClient,
    *,
    asset: str,
    timestamp_ms: int | None = None,
    since_tid: int | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "limit_trades": TRADE_PAGE_LIMIT,
        "include_breaks": "false",
    }
    if since_tid is not None:
        params["since_tid"] = since_tid
    elif timestamp_ms is not None:
        params["timestamp"] = timestamp_ms

    response: httpx.Response | None = None
    for attempt in range(6):
        response = await http.get(
            f"{hist.GEMINI_REST}/trades/{hist.SPOT_SYMBOL[asset]}",
            params=params,
        )
        if response.status_code != 429:
            break
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else min(4.0, 0.25 * (2 ** attempt))
        except (TypeError, ValueError):
            delay = min(4.0, 0.25 * (2 ** attempt))
        logger.warning(
            "GEMINI_HIST_PROXY_429 asset=%s since_tid=%s attempt=%d sleep_s=%.2f",
            asset, since_tid, attempt + 1, delay,
        )
        await asyncio.sleep(max(0.05, delay))

    assert response is not None
    response.raise_for_status()
    payload = response.json()
    rows = list(payload) if isinstance(payload, list) else []
    rows.sort(key=lambda row: (hist._trade_ms(row) or 0, int(row.get("tid") or 0)))
    return rows


async def _spot_trades_coverage_verified(
    http: httpx.AsyncClient,
    *,
    asset: str,
    start_ms: int,
    max_attempts: int = 6,  # kept for drop-in signature compatibility
) -> tuple[list[dict[str, Any]], bool]:
    del max_attempts
    target_close_ms = start_ms + TRADE_COVERAGE_HORIZON_MS
    all_rows: list[dict[str, Any]] = []
    seen_tids: set[int] = set()
    since_tid: int | None = None
    coverage_verified = False

    for page in range(MAX_TRADE_PAGES):
        page_rows = await _request_trade_page(
            http,
            asset=asset,
            timestamp_ms=start_ms if page == 0 else None,
            since_tid=since_tid,
        )
        if not page_rows:
            # Empty page after a complete prior page means there are no more trades;
            # the last observed trade is therefore genuinely the latest before target.
            coverage_verified = True
            break

        new_rows: list[dict[str, Any]] = []
        page_tids: list[int] = []
        for row in page_rows:
            try:
                tid = int(row.get("tid"))
            except (TypeError, ValueError):
                tid = -1
            if tid >= 0:
                page_tids.append(tid)
                if tid in seen_tids:
                    continue
                seen_tids.add(tid)
            new_rows.append(row)
        all_rows.extend(new_rows)
        all_rows.sort(key=lambda row: (hist._trade_ms(row) or 0, int(row.get("tid") or 0)))

        max_ts = max((hist._trade_ms(row) or 0 for row in page_rows), default=0)
        if max_ts >= target_close_ms:
            coverage_verified = True
            break
        if len(page_rows) < TRADE_PAGE_LIMIT:
            # The API returned fewer than its maximum: this is the complete tail after
            # the requested cursor, so no hidden trade can exist between max_ts/target.
            coverage_verified = True
            break
        if not page_tids:
            break
        next_tid = max(page_tids)
        if since_tid is not None and next_tid <= since_tid:
            break
        since_tid = next_tid
        await asyncio.sleep(0.05)

    max_ts = max((hist._trade_ms(row) or 0 for row in all_rows), default=0)
    logger.info(
        "GEMINI_HIST_PROXY_COVERAGE asset=%s pages_max=%d rows=%d target_close_ms=%d max_trade_ms=%d verified=%s",
        asset, MAX_TRADE_PAGES, len(all_rows), target_close_ms, max_ts, coverage_verified,
    )
    # The base analyzer's boolean field is named ``spot_trade_response_capped_500``.
    # Return True only when coverage remains unverified after paging, so such windows
    # remain visibly quarantined instead of being treated as clean observations.
    return all_rows, (not coverage_verified)


hist._kalshi_settled_paced = _kalshi_settled_scoped
hist._spot_trades = _spot_trades_coverage_verified


def main() -> None:
    hist.main()


if __name__ == "__main__":
    main()
