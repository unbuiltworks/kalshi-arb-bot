"""Install fresh-settlement reads for opportunity audit telemetry."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx

from src import gemini_kalshi_opportunity_settlement_audit_shadow as audit
from src.gemini_kalshi_fast_overlap import GEMINI_REST
from src.gemini_kalshi_settlement_probe import match_settled_true_windows


async def _settlement_rows_recent(http: httpx.AsyncClient) -> list[Any]:
    response = await http.get(
        f"{GEMINI_REST}/events/recently-settled",
        params={"category": "crypto", "limit": 500, "offset": 0},
    )
    response.raise_for_status()
    gemini = [row for row in response.json().get("data", []) if isinstance(row, dict)]

    tasks = [
        audit.asyncio.create_task(
            audit._kalshi_settled_series(
                http,
                hours=audit.SETTLEMENT_LOOKBACK_HOURS,
                series_ticker=series,
            )
        )
        for series in audit.KALSHI_15M_SERIES.values()
    ]
    lists = await audit.asyncio.gather(*tasks)
    kalshi = [market for rows in lists for market in rows]
    matched = match_settled_true_windows(gemini, kalshi, 30.0)
    return [
        SimpleNamespace(
            asset=row.asset,
            expiry=row.expiry,
            expiry_utc=row.expiry,
            gemini_ticker=row.gemini_ticker,
            kalshi_ticker=row.kalshi_ticker,
            gemini_outcome=row.gemini_outcome,
            kalshi_outcome=row.kalshi_outcome,
            agrees=row.agrees,
            gemini_strike=row.gemini_strike,
            gemini_settlement=row.gemini_settlement,
            gemini_margin_bps=row.gemini_margin_bps,
        )
        for row in matched
    ]


def install() -> None:
    audit._settlement_rows = _settlement_rows_recent
