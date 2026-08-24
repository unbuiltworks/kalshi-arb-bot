"""Read-only strict Polymarket MLB player-prop maker touch feasibility.

For exact same-event/same-player/same-stat integer GTE ladders, P(H) subset P(L).
Thus YES(P(L)) + NO(P(H)) pays at least $1 under common settlement.  This probe
scores maker->taker economics and, on a one-cent price grid, finds the most
aggressive post-only maker quote that still leaves >=3c after the current hedge ask.

No authentication or order entry.  A row is not executable until rule/participation
semantics and final fill-time book/depth checks are certified separately.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import re
from typing import Any, Mapping

import httpx

from src.sports_poly_near_term_inventory import run_once as near_term_inventory
from src.sports_poly_current_fees import maker_then_taker_locked_profit
from src.strict_mlb_prop_adapters import _poly_identity
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book

D = Decimal
ONE = D("1")
CENT = D("0.01")
TARGET = D("0.03")
BASE = "https://gateway.polymarket.us"


def _norm_player(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _integer(v: D) -> bool:
    return v >= ONE and v == v.to_integral_value()


def _bid(book: TopBook, buy_yes: bool) -> D:
    return ONE - (book.no_ask if buy_yes else book.yes_ask)


def _ask(book: TopBook, buy_yes: bool) -> tuple[D, D]:
    return (book.yes_ask, book.yes_depth) if buy_yes else (book.no_ask, book.no_depth)


def _aggressive_quote(*, maker_book: TopBook, maker_buy_yes: bool,
                      hedge_book: TopBook, hedge_buy_yes: bool) -> dict[str, Any] | None:
    maker_bid = _bid(maker_book, maker_buy_yes)
    maker_ask, _ = _ask(maker_book, maker_buy_yes)
    hedge_ask, hedge_depth = _ask(hedge_book, hedge_buy_yes)
    if hedge_depth < ONE or maker_bid <= 0 or maker_ask <= maker_bid:
        return None
    upper = maker_ask - CENT
    if upper < maker_bid:
        return None
    best: D | None = None
    best_econ: Mapping[str, Any] | None = None
    px = maker_bid
    guard = 0
    while px <= upper + D("0.0000001") and guard < 100:
        econ = maker_then_taker_locked_profit(maker_price=px, taker_price=hedge_ask, quantity=1)
        if D(str(econ["locked_profit"])) >= TARGET:
            best = px
            best_econ = econ
        px += CENT
        guard += 1
    if best is None or best_econ is None:
        return None
    join = maker_then_taker_locked_profit(maker_price=maker_bid, taker_price=hedge_ask, quantity=1)
    return {
        "maker_side": "YES" if maker_buy_yes else "NO",
        "maker_best_bid": str(maker_bid),
        "maker_ask": str(maker_ask),
        "safe_maker_price": str(best),
        "ticks_improved_from_bid": int(((best - maker_bid) / CENT).to_integral_value()),
        "one_tick_from_touch": bool(best >= upper),
        "join_bid_locked_profit": str(join["locked_profit"]),
        "safe_quote_locked_profit": str(best_econ["locked_profit"]),
        "hedge_side": "YES" if hedge_buy_yes else "NO",
        "hedge_ask": str(hedge_ask),
        "hedge_q1_depth": str(hedge_depth),
        "hedge_taker_fee": str(best_econ["taker_fee"]),
        "safe_all_in": str(best_econ["all_in_cost"]),
    }


async def run_once() -> dict[str, Any]:
    inventory = await near_term_inventory()
    events = [e for e in (inventory.get("events") or []) if isinstance(e, Mapping)]
    groups: dict[tuple[str, str, str, tuple[str, str], str], list[dict[str, Any]]] = {}
    market_meta: dict[str, Mapping[str, Any]] = {}
    slugs: set[str] = set()

    for event in events:
        event_slug = str(event.get("slug") or event.get("id") or "")
        series = str(event.get("series_slug") or event.get("subcategory") or "").lower()
        if "mlb" not in series and "mlb" not in event_slug.lower():
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            ident = _poly_identity(market)
            if ident is None:
                continue
            family, player, teams, game_date, threshold = ident
            if not _integer(threshold):
                continue
            slug = str(market.get("slug") or "")
            if not slug:
                continue
            key = (game_date, family, _norm_player(player), tuple(sorted(teams)), event_slug)
            groups.setdefault(key, []).append({
                "slug": slug,
                "threshold": threshold,
                "player": player,
                "family": family,
                "event_slug": event_slug,
                "event_title": event.get("title"),
                "event_start_time": event.get("start_time"),
                "phase": event.get("phase"),
                "game_date": game_date,
                "teams": list(sorted(teams)),
                "title": market.get("title") or market.get("question"),
            })
            market_meta[slug] = market
            slugs.add(slug)

    books: dict[str, TopBook] = {}
    sem = asyncio.Semaphore(12)
    async with httpx.AsyncClient(timeout=8.0) as http:
        async def fetch(slug: str) -> None:
            async with sem:
                try:
                    r = await http.get(f"{BASE}/v1/markets/{slug}/book")
                    if r.status_code == 200:
                        b = poly_top_book(r.json())
                        if b is not None:
                            books[slug] = b
                except Exception:
                    pass
        await asyncio.gather(*(fetch(s) for s in sorted(slugs)))

    rows: list[dict[str, Any]] = []
    for key, ladder in groups.items():
        unique: dict[D, dict[str, Any]] = {}
        dup: set[D] = set()
        for item in ladder:
            t = D(str(item["threshold"]))
            if t in unique:
                dup.add(t)
            else:
                unique[t] = item
        ordered = [x for t, x in sorted(unique.items()) if t not in dup and x["slug"] in books]
        for i, low in enumerate(ordered):
            for high in ordered[i+1:]:
                if not D(str(low["threshold"])) < D(str(high["threshold"])):
                    continue
                # Guaranteed structure: low YES + high NO. Either leg may be maker.
                options = (
                    ("MAKER_LOW_YES_HEDGE_HIGH_NO", low, True, high, False),
                    ("MAKER_HIGH_NO_HEDGE_LOW_YES", high, False, low, True),
                )
                for direction, maker_row, maker_yes, hedge_row, hedge_yes in options:
                    scored = _aggressive_quote(
                        maker_book=books[maker_row["slug"]], maker_buy_yes=maker_yes,
                        hedge_book=books[hedge_row["slug"]], hedge_buy_yes=hedge_yes,
                    )
                    if scored is None:
                        continue
                    rows.append({
                        "direction": direction,
                        "player": low["player"],
                        "family": low["family"],
                        "event_slug": low["event_slug"],
                        "event_title": low["event_title"],
                        "event_start_time": low["event_start_time"],
                        "phase": low["phase"],
                        "game_date": low["game_date"],
                        "teams": low["teams"],
                        "low_threshold": str(low["threshold"]),
                        "high_threshold": str(high["threshold"]),
                        "low_slug": low["slug"],
                        "low_title": low["title"],
                        "high_slug": high["slug"],
                        "high_title": high["title"],
                        "maker_slug": maker_row["slug"],
                        "hedge_slug": hedge_row["slug"],
                        **scored,
                        "identity": "YES(GTE_LOW)+NO(GTE_HIGH)>=1",
                        "same_event_same_player_same_stat": True,
                        "rule_gate": "SAME_VENUE_STRICT_IDENTITY_OK; PARTICIPATION/VOID/SETTLEMENT_SEMANTICS_REQUIRE_FINAL_CERTIFICATION",
                        "order_entry": False,
                        "realized": False,
                    })

    rows.sort(key=lambda r: (r["one_tick_from_touch"], D(r["safe_quote_locked_profit"]), -D(r["safe_all_in"])), reverse=True)
    near = [r for r in rows if r["one_tick_from_touch"]]
    by_family: dict[str, int] = {}
    for r in rows:
        by_family[r["family"]] = by_family.get(r["family"], 0) + 1
    out = {
        "status": "SPORTS_POLY_MLB_MAKER_TOUCH_PROBE_V1",
        "inventory_events": inventory.get("event_count"),
        "mlb_prop_markets": len(slugs),
        "books_fetched": len(books),
        "semantic_ladders": sum(1 for v in groups.values() if len(v) >= 2),
        "safe_maker_directions_3c": len(rows),
        "one_tick_from_touch_3c": len(near),
        "safe_by_family": by_family,
        "top": rows[:50],
        "target_locked_after_fill": str(TARGET),
        "price_grid_assumption": "0.01 shadow-only; live order would use market-reported increment",
        "order_entry": False,
        "realized": False,
    }
    print("SPORTS_POLY_MLB_MAKER_TOUCH_PROBE " + json.dumps(out,separators=(",",":"),default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
