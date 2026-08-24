"""Read-only current-slate census for strict same-Polymarket sports ladders.

This is the supply/fill-economics gate before broadening the q1 live maker lane.
It never authenticates and never places orders.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
from typing import Any

from src.sports_poly_structural_monotone import (
    discover_structural_pairs,
    fetch_books,
    score_structural_pairs,
)

D = Decimal


def _jsonable(value: Any) -> Any:
    if isinstance(value, D):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


async def run_once() -> dict[str, Any]:
    feed, pairs, discovery = await discover_structural_pairs()
    books, quote_health = await fetch_books(set(feed), concurrency=6)
    candidates = score_structural_pairs(feed, books, pairs)

    candidate_families: Counter[str] = Counter(str(row["family"]) for row in candidates)
    improved_families: Counter[str] = Counter(
        str(row["family"]) for row in candidates if bool(row.get("improves_bid"))
    )
    locked_total = sum((D(str(row["quote_locked_profit"])) for row in candidates), D("0"))
    valid_quote_pct = (
        100.0 * quote_health.get("successful", 0) / max(1, quote_health.get("requested", 0))
    )

    out = {
        "status": "STRUCTURAL_SHADOW_CENSUS_COMPLETE",
        "discovery": discovery,
        "quote_health": {
            **quote_health,
            "valid_quote_pct": round(valid_quote_pct, 2),
        },
        "candidate_count_ge_3c": len(candidates),
        "candidate_families": dict(candidate_families.most_common()),
        "bid_improving_candidate_families": dict(improved_families.most_common()),
        "sum_q1_quote_locked_profit_not_realized": str(locked_total),
        "top_candidates": candidates[:25],
        "read_only": True,
        "order_entry": False,
        "realized": False,
    }
    print("SPORTS_POLY_STRUCTURAL_SHADOW " + json.dumps(_jsonable(out), separators=(",", ":")), flush=True)
    return out


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
