"""Read-only deep review of non-sports Polymarket US <-> Kalshi overlaps.

The fast universe audit uses a deliberately conservative threshold. This job runs
less frequently with a lower discovery threshold and prints the strongest candidate
pairs per category so we can build explicit settlement-rule matchers for crypto,
weather, macro, finance, politics, culture, technology, and geopolitics.

Nothing emitted here is executable. Fuzzy matching is only a research queue.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import json
import logging
import time

import httpx

from src import universe_overlap_audit as base
# Importing the continuous launcher applies the bounded rare-token candidate pool.
from src import universe_overlap_audit_v2 as _fast  # noqa: F401

logger = logging.getLogger("kalshi_arb.category_overlap_review")

base.CATEGORY_MAP.update({
    "climate": {"climate", "weather", "climate and weather"},
    "technology": {"technology", "tech", "science", "science and technology"},
    "science": {"science", "science and technology"},
    "geopolitics": {"politics", "geopolitics", "world"},
    "finance": {"finance", "financials", "companies", "commodities"},
})

TARGET_CATEGORIES = {
    "crypto", "climate", "macro", "finance", "politics", "culture",
    "technology", "science", "geopolitics",
}


async def run(threshold: float = 0.30, per_category: int = 30) -> None:
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=20.0, limits=httpx.Limits(max_connections=20)) as http:
        (poly, poly_truncated), (kalshi, kalshi_truncated) = await asyncio.gather(
            base.fetch_poly(http), base.fetch_kalshi(http)
        )
    candidates, stats = base.find_candidates(poly, kalshi, threshold=threshold)
    selected = [c for c in candidates if c["poly_category"] in TARGET_CATEGORIES]
    by_category = defaultdict(list)
    for row in selected:
        by_category[row["poly_category"]].append(row)

    logger.info("CATEGORY_REVIEW_SUMMARY %s", json.dumps({
        "shadow_only": True,
        "status": "REVIEW_ONLY_NOT_EXECUTABLE",
        "poly_markets": len(poly),
        "kalshi_markets": len(kalshi),
        "poly_truncated": poly_truncated,
        "kalshi_truncated": kalshi_truncated,
        "threshold": threshold,
        "candidate_pairs": len(selected),
        "counts": dict(Counter(c["poly_category"] for c in selected).most_common()),
        "elapsed_s": round(time.monotonic() - started, 2),
        **stats,
    }, separators=(",", ":")))

    for category in sorted(TARGET_CATEGORIES):
        rows = by_category.get(category, [])[:per_category]
        logger.info("CATEGORY_TOP %s", json.dumps({
            "category": category,
            "count": len(by_category.get(category, [])),
            "emitting": len(rows),
        }, separators=(",", ":")))
        for row in rows:
            logger.info("CATEGORY_CANDIDATE %s", json.dumps(row, separators=(",", ":")))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
