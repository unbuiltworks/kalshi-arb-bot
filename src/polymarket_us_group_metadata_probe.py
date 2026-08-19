"""Inspect public Polymarket US active-market metadata for group structures.

This is a schema/inventory probe only.  It queries the public gateway, prints
selected structural fields and key names, and never authenticates or imports an
order transport.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
from typing import Any, Mapping

import httpx

GATEWAY = "https://gateway.polymarket.us"


def _pick(row: Mapping[str, Any]) -> dict[str, Any]:
    wanted = (
        "id", "slug", "question", "title", "category", "subcategory",
        "active", "closed", "archived", "eventId", "event_id", "eventSlug",
        "event_slug", "instrument_product", "instrumentProduct",
        "instrument_product_series", "instrumentProductSeries", "product",
        "productCode", "marketStructure", "market_structure", "outcome_type",
        "outcomeType", "outcome_strike", "outcomeStrike", "outcome_rank",
        "outcomeRank", "rank", "sportsMarketTypeV2", "sportsMarketType",
        "line", "bestBid", "bestAsk", "feeCoefficient",
    )
    out = {key: row.get(key) for key in wanted if key in row}
    # Preserve shallow nested metadata containers without dumping rules text.
    for key in ("metadata", "instrument", "productMetadata", "product_metadata", "event"):
        value = row.get(key)
        if isinstance(value, Mapping):
            out[key] = {
                sub: value.get(sub)
                for sub in value
                if any(token in sub.lower() for token in ("product", "rank", "outcome", "event", "series", "structure", "strike"))
            }
    return out


async def probe() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as http:
        for offset in range(0, 500, 100):
            response = await http.get(
                f"{GATEWAY}/v1/markets",
                params={"active": "true", "closed": "false", "limit": 100, "offset": offset},
            )
            response.raise_for_status()
            body = response.json()
            page = body.get("markets") if isinstance(body, Mapping) else body
            if not isinstance(page, list):
                page = body.get("data") if isinstance(body, Mapping) else []
            page = [dict(item) for item in (page or []) if isinstance(item, Mapping)]
            rows.extend(page)
            if len(page) < 100:
                break
            await asyncio.sleep(0.15)

    key_counts: Counter[str] = Counter()
    product_counts: Counter[str] = Counter()
    structure_counts: Counter[str] = Counter()
    interesting: list[dict[str, Any]] = []
    for row in rows:
        key_counts.update(row.keys())
        product = str(
            row.get("instrument_product")
            or row.get("instrumentProduct")
            or row.get("productCode")
            or ""
        ).strip().lower()
        if product:
            product_counts[product] += 1
        structure = str(row.get("marketStructure") or row.get("market_structure") or "").strip()
        if structure:
            structure_counts[structure] += 1
        picked = _pick(row)
        if product or structure or any(key in picked for key in ("outcome_rank", "outcomeRank", "rank")):
            interesting.append(picked)

    structural_keys = sorted(
        key for key in key_counts
        if any(token in key.lower() for token in ("product", "rank", "outcome", "event", "series", "structure", "strike"))
    )
    return {
        "mode": "POLYMARKET_US_GROUP_METADATA_PROBE",
        "active_markets_sampled": len(rows),
        "structural_keys": structural_keys,
        "product_counts": dict(product_counts.most_common(50)),
        "structure_counts": dict(structure_counts.most_common(20)),
        "interesting_samples": interesting[:40],
        "top_level_keys": sorted(key_counts),
        "read_only": True,
        "authenticated": False,
        "order_entry": False,
    }


def main() -> None:
    print(
        "POLYMARKET_US_GROUP_METADATA "
        + json.dumps(asyncio.run(probe()), sort_keys=True, separators=(",", ":"), default=str)
    )


if __name__ == "__main__":
    main()
