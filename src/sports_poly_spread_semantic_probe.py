"""Read-only semantic audit for top non-MLB Polymarket spread maker candidates.

Prints exact provider market metadata/marketSides for the top candidate rows so
we can prove which outcome token corresponds to which participant/line before any
non-MLB spread order is permitted. No auth, no order entry, no capital mutation.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import httpx

from src.sports_poly_maker_trigger_census import run_once as census

BASE = "https://gateway.polymarket.us"


def _safe_market(m: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "slug": m.get("slug"),
        "title": m.get("title") or m.get("question"),
        "sportsMarketType": m.get("sportsMarketType"),
        "sportsMarketTypeV2": m.get("sportsMarketTypeV2"),
        "line": m.get("line"),
        "gameId": m.get("gameId"),
        "gameStartTime": m.get("gameStartTime"),
        "active": m.get("active"),
        "closed": m.get("closed"),
        "archived": m.get("archived"),
        "minimumTradeQty": m.get("minimumTradeQty"),
        "orderPriceMinTickSize": m.get("orderPriceMinTickSize"),
        "marketSides": m.get("marketSides"),
        "outcomes": m.get("outcomes"),
    }


async def run_once() -> dict[str, Any]:
    c = await census()
    rows = [r for r in (c.get("top_candidates") or []) if isinstance(r, Mapping) and r.get("kind") == "FULL_GAME_SPREAD"][:12]
    slugs = sorted({str(r[s]) for r in rows for s in ("low_slug", "high_slug") if r.get(s)})
    markets: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=8.0) as http:
        for slug in slugs:
            try:
                resp = await http.get(f"{BASE}/v1/markets/{slug}")
                if resp.status_code == 200 and isinstance(resp.json(), Mapping):
                    markets[slug] = _safe_market(resp.json())
            except Exception:
                continue
    out = {
        "status": "SPORTS_POLY_SPREAD_SEMANTIC_PROBE_V2",
        "candidate_count": len(rows),
        "series": sorted({str(r.get("series") or "unknown") for r in rows}),
        "rows": rows,
        "markets": markets,
        "order_entry": False,
        "funds_at_risk": False,
    }
    print("SPORTS_POLY_SPREAD_SEMANTIC_PROBE " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
