"""Read-only live rule/metadata probe for KXU3 <-> Polymarket US URC audit.

No authentication and no order-entry code.  Fetches the exact Polymarket US market
records for the August 2026 URC slugs already in our shadow counterpart inventory
and prints settlement-relevant metadata so the execution certification can be based
on the venue actually traded (gateway.polymarket.us), not global polymarket.com.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

BASE = "https://gateway.polymarket.us"
SLUGS = (
    "urc-usunemp-sa-august-2026-09-04-gt4pt0pct",
    "urc-usunemp-sa-august-2026-09-04-gt4pt2pct",
    "urc-usunemp-sa-august-2026-09-04-gt4pt4pct",
)

# Fields likely to carry contract semantics.  Unknown/new fields are also surfaced
# by top-level_keys so schema changes are visible without dumping order/account data.
SEMANTIC_KEYS = (
    "id", "ticker", "slug", "question", "title", "subtitle", "description",
    "resolutionSource", "resolution_source", "rules", "rule", "marketType",
    "category", "subcategory", "startDate", "endDate", "expirationDate",
    "expirationTime", "closeTime", "active", "closed", "archived",
    "automaticResolution", "settlementPriceCalculationText", "sportsMarketType",
    "sportsMarketTypeV2", "line", "operator", "threshold", "unit",
)


def _semantic_view(market: dict[str, Any]) -> dict[str, Any]:
    out = {key: market.get(key) for key in SEMANTIC_KEYS if key in market}
    out["top_level_keys"] = sorted(str(key) for key in market.keys())
    tags = market.get("tags")
    if isinstance(tags, list):
        out["tags"] = tags
    subject = market.get("subject")
    if isinstance(subject, dict):
        out["subject"] = subject
    return out


async def run_once() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20.0) as http:
        for slug in SLUGS:
            url = f"{BASE}/v1/market/slug/{slug}"
            try:
                response = await http.get(url)
                body: Any = response.json() if response.content else {}
                market = body.get("market") if isinstance(body, dict) else None
                rows.append({
                    "slug": slug,
                    "status_code": response.status_code,
                    "market": _semantic_view(market) if isinstance(market, dict) else None,
                    "response_keys": sorted(body.keys()) if isinstance(body, dict) else [],
                })
            except Exception as exc:
                rows.append({"slug": slug, "error": f"{type(exc).__name__}:{str(exc)[:300]}"})
    result = {
        "status": "KP_UNEMPLOYMENT_RULE_PROBE",
        "venue": "POLYMARKET_US",
        "base": BASE,
        "rows": rows,
        "read_only": True,
        "order_entry": False,
    }
    print("KP_UNEMPLOYMENT_RULE_PROBE " + json.dumps(result, separators=(",", ":"), default=str))
    return result


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
