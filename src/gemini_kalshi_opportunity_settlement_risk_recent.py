"""Fresh-settlement compatibility launcher for read-only opportunity-risk telemetry.

Uses Gemini's official recently-settled endpoint so 15-minute contracts cannot fall
behind a generic settled-event page, and supplies the legacy ``expiry_utc`` alias
expected by the existing audit formatter.  No order transport or account state is
used.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx

from src import gemini_kalshi_opportunity_settlement_audit_shadow as audit
from src import gemini_kalshi_opportunity_settlement_risk_shadow as risk
from src.gemini_kalshi_fast_overlap import GEMINI_REST
from src.gemini_kalshi_settlement_probe import match_settled_true_windows


async def _settlement_rows_recent(http: httpx.AsyncClient) -> list[Any]:
    gemini_response = await http.get(
        f"{GEMINI_REST}/events/recently-settled",
        params={"category": "crypto", "limit": 500, "offset": 0},
    )
    gemini_response.raise_for_status()
    gemini = [
        row for row in gemini_response.json().get("data", [])
        if isinstance(row, dict)
    ]

    kalshi_tasks = [
        audit.asyncio.create_task(
            audit._kalshi_settled_series(
                http,
                hours=audit.SETTLEMENT_LOOKBACK_HOURS,
                series_ticker=series,
            )
        )
        for series in audit.KALSHI_15M_SERIES.values()
    ]
    kalshi_lists = await audit.asyncio.gather(*kalshi_tasks)
    kalshi = [market for rows in kalshi_lists for market in rows]
    matched = match_settled_true_windows(gemini, kalshi, 30.0)

    # AgreementRow exposes ``expiry``.  The legacy formatter mistakenly reads
    # ``expiry_utc``; return a transparent compatibility view with both names.
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


def main() -> None:
    audit._settlement_rows = _settlement_rows_recent
    risk.main()


if __name__ == "__main__":
    main()
