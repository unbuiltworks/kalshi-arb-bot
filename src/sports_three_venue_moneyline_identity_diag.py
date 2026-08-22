"""Read-only diagnostic for exact MLB moneyline identity alignment across venues."""
from __future__ import annotations

import asyncio
import json
from typing import Any
import httpx

from src.mlb_game_side_normalization import normalize_gemini_mlb, normalize_polymarket_mlb
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb

TARGET_TEAMS = {frozenset(("ATL", "MIL")), frozenset(("WSH", "MIA"))}


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    out=[]
    for r in rows:
        i=r.identity
        if i.market_family != "moneyline":
            continue
        teams=frozenset(i.event.participants)
        if teams not in TARGET_TEAMS:
            continue
        out.append({
            "venue": i.venue,
            "participants": list(i.event.participants),
            "scheduled_start_ms": i.event.scheduled_start_ms,
            "subject": i.subject,
            "family": i.market_family,
            "scope": i.scope,
            "comparator": i.comparator,
            "line": str(i.line) if i.line is not None else None,
            "contract_key": r.contract_key,
            "venue_market_id": r.venue_market_id,
            "side": r.side,
            "label": r.source_label,
        })
    return sorted(out, key=lambda x:(x["participants"],x["subject"],x["venue"],x["scheduled_start_ms"]))

async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as http:
        g,p,k=await asyncio.gather(fetch_gemini_mlb(http),fetch_polymarket_mlb(http),fetch_kalshi_mlb(http))
    rows=_rows(normalize_gemini_mlb(g)+normalize_polymarket_mlb(p)+normalize_kalshi_mlb_eastern(k))
    result={"status":"THREE_VENUE_MONEYLINE_IDENTITY_DIAG","rows":rows,"row_count":len(rows),"read_only":True,"order_entry":False}
    print("THREE_VENUE_MONEYLINE_IDENTITY_DIAG "+json.dumps(result,separators=(",",":"),default=str),flush=True)
    return result

def main()->None:
    asyncio.run(run_once())

if __name__=="__main__": main()
