"""Read-only live Polymarket same-venue nested full-game spread scanner.

For one team in one game, define P(t) = the team's final scoring margin > t.
For thresholds L < H, P(H) is a subset of P(L), therefore:

    P(L) + NOT P(H) >= 1

The Polymarket marketSides metadata maps each team to a signed spread and tells
whether that team's spread outcome is the market YES (long=True) or NO token.
A conventional team spread d corresponds to margin > -d, so the canonical margin
threshold is ``-description``. This lets the scanner compare the same team across
alternate full-game spread lines even when token orientation flips.

Fail-closed constraints: SPREAD metadata required, explicit segmented scopes are
rejected, only half-point lines are accepted, q=1 executable depth is required,
and conservative Polymarket taker fees are included. No authentication/order entry.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from typing import Any, Mapping

import httpx

from src.sports_live_poly_inventory import run_once as live_inventory
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book, polymarket_fee

D = Decimal
ONE = D("1")
HALF = D("0.5")
TARGET = D("0.03")
BASE = "https://gateway.polymarket.us"


def _is_half_point(value: Decimal) -> bool:
    return abs(value) % ONE == HALF


def _team_abbr(side: Mapping[str, Any]) -> str:
    team = side.get("team") if isinstance(side.get("team"), Mapping) else {}
    return str(side.get("teamAbbreviation") or team.get("abbreviation") or "").strip().lower()


def _signed_description(side: Mapping[str, Any]) -> Decimal | None:
    raw = str(side.get("description") or "").strip().replace("−", "-")
    try:
        return D(raw)
    except Exception:
        return None


def _is_full_game_spread(market: Mapping[str, Any]) -> bool:
    st = str(market.get("sportsMarketType") or "").lower()
    v2 = str(market.get("sportsMarketTypeV2") or "").upper()
    title = str(market.get("title") or market.get("question") or "").lower()
    slug = str(market.get("slug") or "").lower()
    if "SPREAD" not in v2 and "spread" not in st:
        return False
    segmented = (
        "first 5", "1st half", "first half", "2nd half", "second half",
        "1st quarter", "first quarter", "2nd quarter", "second quarter",
        "3rd quarter", "third quarter", "4th quarter", "fourth quarter",
        "period", "inning",
    )
    if any(x in title for x in segmented):
        return False
    if any(x in slug for x in ("-f5-", "-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-")):
        return False
    sides = market.get("marketSides")
    return isinstance(sides, list) and len(sides) >= 2


def _selected_ask(book: TopBook, selected_is_yes: bool) -> tuple[Decimal, Decimal]:
    return (book.yes_ask, book.yes_depth) if selected_is_yes else (book.no_ask, book.no_depth)


def _opposite_ask(book: TopBook, selected_is_yes: bool) -> tuple[Decimal, Decimal]:
    return (book.no_ask, book.no_depth) if selected_is_yes else (book.yes_ask, book.yes_depth)


def score_spread_pair(
    low_threshold: Decimal,
    low_book: TopBook,
    low_selected_is_yes: bool,
    high_threshold: Decimal,
    high_book: TopBook,
    high_selected_is_yes: bool,
) -> dict[str, Any] | None:
    if not low_threshold < high_threshold:
        return None
    if not _is_half_point(low_threshold) or not _is_half_point(high_threshold):
        return None
    low_px, low_depth = _selected_ask(low_book, low_selected_is_yes)
    high_px, high_depth = _opposite_ask(high_book, high_selected_is_yes)
    if low_depth < ONE or high_depth < ONE:
        return None
    low_fee = polymarket_fee(1, low_px)
    high_fee = polymarket_fee(1, high_px)
    all_in = low_px + low_fee + high_px + high_fee
    return {
        "low_margin_threshold": str(low_threshold),
        "high_margin_threshold": str(high_threshold),
        "low_buy_side": "YES" if low_selected_is_yes else "NO",
        "low_ask": str(low_px),
        "low_depth": str(low_depth),
        "low_fee": str(low_fee),
        "high_buy_side": "NO" if high_selected_is_yes else "YES",
        "high_ask": str(high_px),
        "high_depth": str(high_depth),
        "high_fee": str(high_fee),
        "all_in_cost": str(all_in),
        "locked_profit": str(ONE - all_in),
    }


async def run_once() -> dict[str, Any]:
    inventory = await live_inventory()
    events = [x for x in (inventory.get("events") or []) if isinstance(x, Mapping)]
    candidates: list[dict[str, Any]] = []
    for event in events:
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping) or not _is_full_game_spread(market):
                continue
            slug = str(market.get("slug") or "")
            if not slug:
                continue
            for side in market.get("marketSides") or []:
                if not isinstance(side, Mapping):
                    continue
                team = _team_abbr(side)
                description = _signed_description(side)
                if not team or description is None:
                    continue
                margin_threshold = -description
                if not _is_half_point(margin_threshold):
                    continue
                candidates.append({
                    "event_slug": str(event.get("slug") or event.get("id") or ""),
                    "event_title": event.get("title"),
                    "series": event.get("series_slug") or event.get("subcategory"),
                    "game_status": event.get("game_status"),
                    "score": event.get("score"),
                    "period": event.get("period"),
                    "market_slug": slug,
                    "market_title": market.get("title") or market.get("question"),
                    "team": team,
                    "spread_description": str(description),
                    "margin_threshold": margin_threshold,
                    "selected_is_yes": bool(side.get("long")),
                })

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

    by_team: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in candidates:
        if row["market_slug"] in books:
            by_team.setdefault((row["event_slug"], row["team"]), []).append(row)

    rows: list[dict[str, Any]] = []
    for (event_slug, team), markets in by_team.items():
        # One canonical row per threshold; duplicate aliases are ignored fail-closed.
        unique: dict[Decimal, dict[str, Any]] = {}
        duplicate_thresholds: set[Decimal] = set()
        for row in markets:
            t = D(str(row["margin_threshold"]))
            if t in unique:
                duplicate_thresholds.add(t)
            else:
                unique[t] = row
        ordered = [row for t, row in sorted(unique.items()) if t not in duplicate_thresholds]
        for i, low_row in enumerate(ordered):
            for high_row in ordered[i + 1:]:
                scored = score_spread_pair(
                    D(str(low_row["margin_threshold"])),
                    books[low_row["market_slug"]],
                    bool(low_row["selected_is_yes"]),
                    D(str(high_row["margin_threshold"])),
                    books[high_row["market_slug"]],
                    bool(high_row["selected_is_yes"]),
                )
                if scored is None:
                    continue
                locked = D(scored["locked_profit"])
                rows.append({
                    "event_slug": event_slug,
                    "event_title": low_row["event_title"],
                    "series": low_row["series"],
                    "game_status": low_row["game_status"],
                    "score": low_row["score"],
                    "period": low_row["period"],
                    "team": team,
                    "low_market_slug": low_row["market_slug"],
                    "low_market_title": low_row["market_title"],
                    "low_spread_description": low_row["spread_description"],
                    "high_market_slug": high_row["market_slug"],
                    "high_market_title": high_row["market_title"],
                    "high_spread_description": high_row["spread_description"],
                    **scored,
                    "identity": "TEAM_MARGIN_GT_LOW + NOT_TEAM_MARGIN_GT_HIGH >= 1",
                    "same_event_same_team_full_game_spread": True,
                    "half_point_only": True,
                    "classification": "POLY_NESTED_SPREAD_SHADOW_EXECUTABLE" if locked >= TARGET else "POLY_NESTED_SPREAD_SHADOW_NO_TRADE",
                    "rule_gate": "SAME_EVENT + SAME_TEAM + FULL_GAME_SPREAD + HALF_POINT; LIVE_RULE_AUDIT_STILL_REQUIRED",
                    "order_entry": False,
                    "realized": False,
                })

    rows.sort(key=lambda r: D(str(r["locked_profit"])), reverse=True)
    out = {
        "status": "SPORTS_POLY_LIVE_NESTED_SPREAD_V1_STRICT",
        "live_event_count": inventory.get("live_event_count"),
        "spread_team_sides_found": len(candidates),
        "books_fetched": len(books),
        "team_ladders": sum(len(v) >= 2 for v in by_team.values()),
        "pairs_scored": len(rows),
        "shadow_executable": sum(r["classification"] == "POLY_NESTED_SPREAD_SHADOW_EXECUTABLE" for r in rows),
        "best": rows[0] if rows else None,
        "target_locked": str(TARGET),
        "order_entry": False,
        "realized": False,
        "rows": rows[:50],
    }
    print("SPORTS_POLY_LIVE_NESTED_SPREAD " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
