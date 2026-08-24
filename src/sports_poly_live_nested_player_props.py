"""Read-only live Polymarket same-player MLB prop threshold Dutch scanner.

For one exact MLB game/player/stat family, define P(n) as the binary proposition
"player records at least n units of the stat". For integer thresholds L < H:

    YES(P(L)) + NO(P(H)) >= 1

because P(H) is a subset of P(L). This creates a guaranteed minimum $1 payout
when both contracts share the same venue/rule family and resolve normally.

Semantic grouping reuses the production strict MLB Polymarket adapter rather than
fuzzy title matching. Supported families are hits, hits+runs+RBIs, total bases and
strikeouts. Only integer thresholds, q=1 executable depth and public live books
are scored. Conservative Polymarket taker fees are included. No authentication or
order entry; any economic positive remains rule-audit shadow until promoted.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import re
from typing import Any, Mapping

import httpx

from src.sports_live_poly_inventory import run_once as live_inventory
from src.strict_mlb_prop_adapters import _poly_identity
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book, polymarket_fee

D = Decimal
ONE = D("1")
TARGET = D("0.03")
BASE = "https://gateway.polymarket.us"


def _norm_player(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _is_integer_threshold(value: Decimal) -> bool:
    return value == value.to_integral_value() and value >= ONE


def score_prop_pair(low_threshold: Decimal, low: TopBook, high_threshold: Decimal, high: TopBook) -> dict[str, Any] | None:
    if not low_threshold < high_threshold:
        return None
    if not _is_integer_threshold(low_threshold) or not _is_integer_threshold(high_threshold):
        return None
    if low.yes_depth < ONE or high.no_depth < ONE:
        return None
    low_fee = polymarket_fee(1, low.yes_ask)
    high_fee = polymarket_fee(1, high.no_ask)
    all_in = low.yes_ask + low_fee + high.no_ask + high_fee
    return {
        "low_threshold": str(low_threshold),
        "high_threshold": str(high_threshold),
        "low_yes_ask": str(low.yes_ask),
        "low_yes_depth": str(low.yes_depth),
        "low_fee": str(low_fee),
        "high_no_ask": str(high.no_ask),
        "high_no_depth": str(high.no_depth),
        "high_fee": str(high_fee),
        "all_in_cost": str(all_in),
        "locked_profit": str(ONE - all_in),
    }


async def run_once() -> dict[str, Any]:
    inventory = await live_inventory()
    events = [x for x in (inventory.get("events") or []) if isinstance(x, Mapping)]
    candidates: list[dict[str, Any]] = []
    for event in events:
        series = str(event.get("series_slug") or event.get("subcategory") or "").lower()
        if "mlb" not in series and "mlb" not in str(event.get("slug") or "").lower():
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            ident = _poly_identity(market)
            if ident is None:
                continue
            family, player, teams, game_date, threshold = ident
            if not _is_integer_threshold(threshold):
                continue
            slug = str(market.get("slug") or "")
            if not slug:
                continue
            candidates.append({
                "event_slug": str(event.get("slug") or event.get("id") or ""),
                "event_title": event.get("title"),
                "series": series,
                "game_status": event.get("game_status"),
                "score": event.get("score"),
                "period": event.get("period"),
                "market_slug": slug,
                "market_title": market.get("title") or market.get("question"),
                "family": family,
                "player": player,
                "player_key": _norm_player(player),
                "teams": tuple(sorted(teams)),
                "game_date": game_date,
                "threshold": threshold,
            })

    books: dict[str, TopBook] = {}
    sem = asyncio.Semaphore(16)
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

    by_prop: dict[tuple[str, str, str, tuple[str, str], str], list[dict[str, Any]]] = {}
    for row in candidates:
        if row["market_slug"] not in books:
            continue
        key = (
            row["game_date"], row["family"], row["player_key"], row["teams"], row["event_slug"]
        )
        by_prop.setdefault(key, []).append(row)

    rows: list[dict[str, Any]] = []
    for _, markets in by_prop.items():
        # Fail closed if two slugs claim the same semantic threshold.
        unique: dict[Decimal, dict[str, Any]] = {}
        duplicates: set[Decimal] = set()
        for row in markets:
            t = D(str(row["threshold"]))
            if t in unique:
                duplicates.add(t)
            else:
                unique[t] = row
        ordered = [row for t, row in sorted(unique.items()) if t not in duplicates]
        for i, low_row in enumerate(ordered):
            for high_row in ordered[i + 1:]:
                scored = score_prop_pair(
                    D(str(low_row["threshold"])), books[low_row["market_slug"]],
                    D(str(high_row["threshold"])), books[high_row["market_slug"]],
                )
                if scored is None:
                    continue
                locked = D(scored["locked_profit"])
                rows.append({
                    "event_slug": low_row["event_slug"],
                    "event_title": low_row["event_title"],
                    "series": low_row["series"],
                    "game_status": low_row["game_status"],
                    "score": low_row["score"],
                    "period": low_row["period"],
                    "family": low_row["family"],
                    "player": low_row["player"],
                    "teams": list(low_row["teams"]),
                    "game_date": low_row["game_date"],
                    "low_market_slug": low_row["market_slug"],
                    "low_market_title": low_row["market_title"],
                    "high_market_slug": high_row["market_slug"],
                    "high_market_title": high_row["market_title"],
                    **scored,
                    "identity": "YES_PLAYER_STAT_GTE_LOW + NO_PLAYER_STAT_GTE_HIGH >= 1",
                    "same_event_same_player_same_stat": True,
                    "classification": "POLY_NESTED_PROP_SHADOW_EXECUTABLE" if locked >= TARGET else "POLY_NESTED_PROP_SHADOW_NO_TRADE",
                    "rule_gate": "STRICT_POLY_IDENTITY + SAME_GAME_PLAYER_STAT + INTEGER_GTE_THRESHOLDS; LIVE_RULE_AUDIT_STILL_REQUIRED",
                    "order_entry": False,
                    "realized": False,
                })

    rows.sort(key=lambda r: D(str(r["locked_profit"])), reverse=True)
    by_family: dict[str, int] = {}
    for row in candidates:
        by_family[row["family"]] = by_family.get(row["family"], 0) + 1
    out = {
        "status": "SPORTS_POLY_LIVE_NESTED_PLAYER_PROPS_V1_STRICT",
        "live_event_count": inventory.get("live_event_count"),
        "strict_prop_markets_found": len(candidates),
        "strict_prop_markets_by_family": by_family,
        "books_fetched": len(books),
        "player_stat_ladders": sum(len(v) >= 2 for v in by_prop.values()),
        "pairs_scored": len(rows),
        "shadow_executable": sum(r["classification"] == "POLY_NESTED_PROP_SHADOW_EXECUTABLE" for r in rows),
        "best": rows[0] if rows else None,
        "target_locked": str(TARGET),
        "order_entry": False,
        "realized": False,
        "rows": rows[:75],
    }
    print("SPORTS_POLY_LIVE_NESTED_PLAYER_PROPS " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
