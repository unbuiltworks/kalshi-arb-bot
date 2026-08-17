"""Public diagnostic for Gemini sports payload shape. No auth or order entry."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import httpx


def slim_event(event: Mapping[str, Any]) -> dict[str, Any]:
    contracts = []
    for contract in event.get("contracts") or []:
        if not isinstance(contract, Mapping):
            continue
        contracts.append({
            key: contract.get(key)
            for key in (
                "id", "ticker", "instrumentSymbol", "label", "abbreviatedName",
                "marketState", "strike", "prices", "description"
            )
            if key in contract
        })
        if len(contracts) >= 4:
            break
    return {
        key: event.get(key)
        for key in (
            "id", "ticker", "title", "category", "series", "status",
            "effectiveDate", "expiryDate", "sportsMarket", "subcategory"
        )
        if key in event
    } | {"contracts": contracts}


async def main_async() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.get(
            "https://api.gemini.com/v1/prediction-markets/events",
            params={"status": "active", "category": "sports", "limit": 500, "offset": 0},
        )
        response.raise_for_status()
        body = response.json()
    rows = [row for row in body.get("data") or [] if isinstance(row, Mapping)]
    baseball = []
    for row in rows:
        sports = row.get("sportsMarket") if isinstance(row.get("sportsMarket"), Mapping) else {}
        if str(sports.get("sport") or "").lower() == "baseball" or "mlb" in str(row.get("ticker") or "").lower():
            baseball.append(row)
    sample = baseball[:4] if baseball else rows[:4]
    print(json.dumps({
        "total_sports": len(rows),
        "baseball_like": len(baseball),
        "samples": [slim_event(row) for row in sample],
    }, separators=(",", ":"), default=str))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
