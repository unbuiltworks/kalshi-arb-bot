"""Read-only live Polymarket US nested-total Dutch-book scanner.

For two explicit full-game event-total thresholds L < H on the same sporting event:

    YES(total > L) + NO(total > H) >= 1

for every final score when the two markets resolve on the same full-game scoring
metric. This module is EVENT TOTALS ONLY. Team totals are a separate valid nested
family but must never be mixed with event totals; the maker census handles them
under an explicit team-total scope.

The scanner uses public order books, requires q=1 depth, and scores conservative
Polymarket taker fees. No authentication or order entry.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import re
from typing import Any, Mapping

import httpx

from src.sports_live_poly_inventory import run_once as live_inventory
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book, polymarket_fee

D = Decimal
ONE = D("1")
HALF = D("0.5")
TARGET = D("0.03")
BASE = "https://gateway.polymarket.us"
OVER_RE = re.compile(r"\bover\s+(-?\d+(?:\.\d+)?)\b", re.I)


def _threshold(market: Mapping[str, Any]) -> Decimal | None:
    title = str(market.get("title") or market.get("question") or "")
    match = OVER_RE.search(title)
    if match:
        try:
            return D(match.group(1))
        except Exception:
            return None
    line = market.get("line")
    try:
        return D(str(line)) if line not in (None, "") else None
    except Exception:
        return None


def _is_half_point(value: Decimal) -> bool:
    return abs(value) % ONE == HALF


def _is_full_game_total(market: Mapping[str, Any]) -> bool:
    """Fail closed on EVENT full-game totals; explicitly reject team totals."""
    st = str(market.get("sportsMarketType") or "").strip().lower()
    if "full_game_total" not in st:
        return False
    title = str(market.get("title") or market.get("question") or "").lower()
    slug = str(market.get("slug") or "").lower()
    if "-tt-" in slug:
        return False
    if any(x in title for x in (
        "first 5", "1st half", "first half", "2nd half", "second half",
        "quarter", "period", "inning", "corner", "total bases",
    )):
        return False
    if any(x in slug for x in ("-f5-", "-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "astatc-")):
        return False
    return True


def score_pair(low_threshold: Decimal, low: TopBook, high_threshold: Decimal, high: TopBook) -> dict[str, Any] | None:
    if not low_threshold < high_threshold:
        return None
    if not _is_half_point(low_threshold) or not _is_half_point(high_threshold):
        return None
    if low.yes_depth < ONE or high.no_depth < ONE:
        return None
    low_fee = polymarket_fee(1, low.yes_ask)
    high_fee = polymarket_fee(1, high.no_ask)
    all_in = low.yes_ask + low_fee + high.no_ask + high_fee
    locked = ONE - all_in
    return {"low_threshold": str(low_threshold), "high_threshold": str(high_threshold), "low_yes_ask": str(low.yes_ask), "low_yes_depth": str(low.yes_depth), "low_fee": str(low_fee), "high_no_ask": str(high.no_ask), "high_no_depth": str(high.no_depth), "high_fee": str(high_fee), "all_in_cost": str(all_in), "locked_profit": str(locked)}


async def run_once() -> dict[str, Any]:
    inventory = await live_inventory()
    events = [x for x in (inventory.get("events") or []) if isinstance(x, Mapping)]
    candidates: list[dict[str, Any]] = []
    for event in events:
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping) or not _is_full_game_total(market):
                continue
            threshold = _threshold(market)
            slug = str(market.get("slug") or "")
            if threshold is None or not _is_half_point(threshold) or not slug:
                continue
            candidates.append({"event_slug": str(event.get("slug") or event.get("id") or ""), "event_title": event.get("title"), "series": event.get("series_slug") or event.get("subcategory"), "game_status": event.get("game_status"), "score": event.get("score"), "period": event.get("period"), "market_slug": slug, "market_title": market.get("title") or market.get("question"), "threshold": threshold})

    books: dict[str, TopBook] = {}
    sem = asyncio.Semaphore(12)
    async with httpx.AsyncClient(timeout=8.0) as http:
        async def fetch(row: dict[str, Any]) -> None:
            async with sem:
                try:
                    r = await http.get(f"{BASE}/v1/markets/{row['market_slug']}/book")
                    if r.status_code == 200:
                        book = poly_top_book(r.json())
                        if book is not None:
                            books[row["market_slug"]] = book
                except Exception:
                    return
        await asyncio.gather(*[fetch(row) for row in candidates])

    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        if row["market_slug"] in books:
            by_event.setdefault(row["event_slug"], []).append(row)

    rows: list[dict[str, Any]] = []
    for event_slug, markets in by_event.items():
        markets.sort(key=lambda x: x["threshold"])
        for i, low_row in enumerate(markets):
            for high_row in markets[i + 1:]:
                scored = score_pair(D(str(low_row["threshold"])), books[low_row["market_slug"]], D(str(high_row["threshold"])), books[high_row["market_slug"]])
                if scored is None:
                    continue
                locked = D(scored["locked_profit"])
                rows.append({"event_slug": event_slug, "event_title": low_row["event_title"], "series": low_row["series"], "game_status": low_row["game_status"], "score": low_row["score"], "period": low_row["period"], "low_market_slug": low_row["market_slug"], "low_market_title": low_row["market_title"], "high_market_slug": high_row["market_slug"], "high_market_title": high_row["market_title"], **scored, "identity": "YES_EVENT_TOTAL_OVER_LOW + NO_EVENT_TOTAL_OVER_HIGH >= 1", "same_event_full_game_total": True, "half_point_only": True, "classification": "POLY_NESTED_SHADOW_EXECUTABLE" if locked >= TARGET else "POLY_NESTED_SHADOW_NO_TRADE", "rule_gate": "EXPLICIT_EVENT_FULL_GAME_TOTAL + SAME_EVENT + HALF_POINT_THRESHOLDS; LIVE_RULE_AUDIT_STILL_REQUIRED", "order_entry": False, "realized": False})

    rows.sort(key=lambda r: D(r["locked_profit"]), reverse=True)
    out = {"status": "SPORTS_POLY_LIVE_NESTED_DUTCH_V3_EVENT_TOTAL_ONLY", "live_event_count": inventory.get("live_event_count"), "live_total_markets_found": len(candidates), "books_fetched": len(books), "events_with_total_ladders": sum(len(v) >= 2 for v in by_event.values()), "pairs_scored": len(rows), "shadow_executable": sum(r["classification"] == "POLY_NESTED_SHADOW_EXECUTABLE" for r in rows), "best": rows[0] if rows else None, "target_locked": str(TARGET), "order_entry": False, "realized": False, "rows": rows[:50]}
    print("SPORTS_POLY_LIVE_NESTED_DUTCH " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
