"""Three-sample persistence check for the freshest sportsbook/Kalshi K positives.

Narrows the existing targeted revalidator to two exact pitcher-strikeout candidates:
BetRivers Brady Singer 6+ Ks vs Kalshi NO and DraftKings George Kirby 7+ Ks vs
Kalshi NO. Runs three fresh samples several seconds apart. Each sample requests one
pitcher_strikeouts_alternate market for two games (about two Odds API credits/sample)
and refreshes exact Kalshi depth-1 books.

Research-only. No alerts or order entry.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
import time
from typing import Any

from src import allbooks_kalshi_positive_revalidate as probe

D = Decimal

SINGER_TICKER = "KXMLBKS-26AUG201240STLCIN-CINBSINGER51-6"
KIRBY_TICKER = "KXMLBKS-26AUG201410SEAMIL-SEAGKIRBY68-7"

NARROW_TARGETS = {
    "48f1a38db4ac9c09c80dc35075975eff": {
        "markets": ("pitcher_strikeouts_alternate",),
        "tickers": (SINGER_TICKER,),
    },
    "f71617a2d08c1daac109bf9f87e7ddae": {
        "markets": ("pitcher_strikeouts_alternate",),
        "tickers": (KIRBY_TICKER,),
    },
}


def _candidate_key(row: dict[str, Any]) -> str | None:
    ticker = str(row.get("kalshi_ticker") or "")
    book = str(row.get("sportsbook") or "")
    if ticker == SINGER_TICKER and book == "betrivers":
        return "BETRIVERS_SINGER_6K"
    if ticker == KIRBY_TICKER and book == "draftkings":
        return "DRAFTKINGS_KIRBY_7K"
    return None


async def run_once(samples: int = 3, spacing_seconds: float = 5.0) -> dict[str, Any]:
    original = probe.TARGETS
    probe.TARGETS = NARROW_TARGETS
    try:
        rows: list[dict[str, Any]] = []
        quota_rows: list[dict[str, Any]] = []
        for index in range(samples):
            started = time.time()
            result = await probe.run_once()
            positives = result.get("top_positive") or []
            plans = result.get("top_plans") or []
            by_key: dict[str, dict[str, Any]] = {}
            for row in plans:
                if not isinstance(row, dict):
                    continue
                key = _candidate_key(row)
                if key is not None:
                    by_key[key] = row
            rows.append({
                "sample": index + 1,
                "captured_unix": started,
                "positive_revalidated_plans": result.get("positive_revalidated_plans"),
                "positive_fresh_5s": result.get("positive_fresh_5s"),
                "positive_fresh_10s": result.get("positive_fresh_10s"),
                "candidates": by_key,
                "all_positive_count": len(positives),
            })
            quota_rows.extend(result.get("quota") or [])
            if index + 1 < samples:
                await asyncio.sleep(spacing_seconds)
    finally:
        probe.TARGETS = original

    persistence: dict[str, dict[str, Any]] = {}
    for key in ("BETRIVERS_SINGER_6K", "DRAFTKINGS_KIRBY_7K"):
        seen = [sample["candidates"].get(key) for sample in rows]
        observed = [row for row in seen if isinstance(row, dict)]
        positive = [row for row in observed if D(str(row.get("shadow_locked_profit") or "0")) > 0]
        fresh5 = [row for row in positive if int(row.get("quote_skew_ms") or 10**9) <= 5000]
        fresh10 = [row for row in positive if int(row.get("quote_skew_ms") or 10**9) <= 10000]
        persistence[key] = {
            "samples_observed": len(observed),
            "positive_samples": len(positive),
            "fresh_5s_positive_samples": len(fresh5),
            "fresh_10s_positive_samples": len(fresh10),
            "profits": [row.get("shadow_locked_profit") for row in observed],
            "quote_skews_ms": [row.get("quote_skew_ms") for row in observed],
            "sportsbook_odds": [row.get("sportsbook_decimal_odds") for row in observed],
            "kalshi_prices": [row.get("kalshi_raw_price") for row in observed],
            "quantities": [row.get("quantity") for row in observed],
        }

    return {
        "status": "ALLBOOKS_KALSHI_PITCHER_POSITIVE_PERSISTENCE",
        "samples": rows,
        "persistence": persistence,
        "quota": quota_rows,
        "rule_gate": "BOOKMAKER_SPECIFIC_COMPLETE_RULE_CERTIFICATION_REQUIRED",
        "alert_eligible": False,
        "execution_eligible": False,
        "realized_pnl": "0",
        "read_only": True,
        "order_entry": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)

# Deployment trigger only; no behavioral change.
