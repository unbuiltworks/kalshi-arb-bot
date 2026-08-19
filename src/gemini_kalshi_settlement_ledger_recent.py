"""Freshness shim for the read-only Gemini/Kalshi 15m settlement ledger.

Gemini's generic settled-event pagination is useful for historical coverage but can
lag the newest short-duration contracts.  Merge its official recently-settled feed
(last 24h, newest first) with the historical collector before the existing exact
window matcher runs.  This module remains public-data-only and cannot place orders.
"""
from __future__ import annotations

from typing import Any

import httpx

from src import gemini_kalshi_settlement_ledger_shadow as ledger
from src.gemini_kalshi_fast_overlap import GEMINI_REST
from src.gemini_kalshi_settlement_agreement import _gemini_settled as _historical_settled


async def _fresh_settled(http: httpx.AsyncClient, limit: int) -> list[dict[str, Any]]:
    recent_limit = min(500, max(1, int(limit)))
    response = await http.get(
        f"{GEMINI_REST}/events/recently-settled",
        params={"category": "crypto", "limit": recent_limit, "offset": 0},
    )
    response.raise_for_status()
    payload = response.json()
    recent = [row for row in payload.get("data", []) if isinstance(row, dict)]

    # Preserve the existing historical sample used by the 48h agreement study.
    historical = await _historical_settled(http, int(limit))
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*recent, *historical]:
        ticker = str(row.get("ticker") or "")
        key = ticker or str(row.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def main() -> None:
    ledger._gemini_settled = _fresh_settled
    ledger.main()


if __name__ == "__main__":
    main()
