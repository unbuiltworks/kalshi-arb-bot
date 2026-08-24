"""Read-only Polymarket maker-first nested-Dutch trigger census.

Scores strict same-venue sports ladders across LIVE and next-24h PREGAME events:
EVENT totals, SAME-TEAM totals, same-team alternate spreads, and canonical MLB
player/stat threshold ladders. Ladders are never mixed across metric/scope.

Totals and spreads are league-agnostic: NFL/WNBA/soccer/tennis/etc. are included
whenever Polymarket exposes the required metadata. MLB player props remain a
separate strict canonical parser until equivalent parsers are certified elsewhere.

A MAKER_TRIGGER_CANDIDATE is hypothetical until the maker order actually fills;
the hedge must then be final-revalidated. No authentication or order entry here.
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
from src.sports_poly_live_nested_dutch import _is_full_game_total, _threshold
from src.sports_poly_live_nested_spread import _is_full_game_spread, _signed_description, _team_abbr
from src.strict_mlb_prop_adapters import _poly_identity
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book

D = Decimal
ONE = D("1")
TARGET = D("0.03")
BASE = "https://gateway.polymarket.us"
TEAM_TOTAL_RE = re.compile(r"-tt-([a-z0-9]+)-", re.I)


def _norm_player(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _is_half_point(value: Decimal) -> bool:
    return abs(value) % ONE == D("0.5")


def _is_integer(value: Decimal) -> bool:
    return value >= ONE and value == value.to_integral_value()


def _total_scope(market: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return (kind, scope). Never mix an event total with a team total."""
    if _is_full_game_total(market):
        return ("FULL_GAME_TOTAL", "EVENT")
    st = str(market.get("sportsMarketType") or "").strip().lower()
    slug = str(market.get("slug") or "").strip().lower()
    if "full_game_total" not in st:
        return None
    m = TEAM_TOTAL_RE.search(slug)
    if not m:
        return None
    # Team totals are full-contest team scoring totals. Period/quarter variants are rejected.
    if any(x in slug for x in ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "-f5-")):
        return None
    return ("TEAM_TOTAL", m.group(1).lower())


def _ask(book: TopBook, buy_yes: bool) -> tuple[Decimal, Decimal]:
    return (book.yes_ask, book.yes_depth) if buy_yes else (book.no_ask, book.no_depth)


def _bid(book: TopBook, buy_yes: bool) -> Decimal:
    return ONE - (book.no_ask if buy_yes else book.yes_ask)


def _score_maker_pair(*, low_book: TopBook, low_buy_yes: bool, high_book: TopBook, high_buy_yes: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    low_ask, low_depth = _ask(low_book, low_buy_yes)
    high_ask, high_depth = _ask(high_book, high_buy_yes)
    low_bid = _bid(low_book, low_buy_yes)
    high_bid = _bid(high_book, high_buy_yes)
    options = (
        ("MAKER_LOW_HEDGE_HIGH", low_bid, high_ask, high_depth, low_buy_yes, high_buy_yes),
        ("MAKER_HIGH_HEDGE_LOW", high_bid, low_ask, low_depth, high_buy_yes, low_buy_yes),
    )
    for direction, maker_px, hedge_px, hedge_depth, maker_buy_yes, hedge_buy_yes in options:
        if maker_px <= 0 or maker_px >= ONE or hedge_depth < ONE:
            continue
        econ = maker_then_taker_locked_profit(maker_price=maker_px, taker_price=hedge_px, quantity=1)
        rows.append({
            "direction": direction,
            "maker_buy_side": "YES" if maker_buy_yes else "NO",
            "maker_price_current_best_bid": str(maker_px),
            "maker_rebate_if_filled": str(econ["maker_rebate"]),
            "hedge_buy_side": "YES" if hedge_buy_yes else "NO",
            "hedge_ask": str(hedge_px),
            "hedge_q1_depth": str(hedge_depth),
            "hedge_taker_fee": str(econ["taker_fee"]),
            "all_in_after_hypothetical_maker_fill": str(econ["all_in_cost"]),
            "locked_profit_after_hypothetical_maker_fill": str(econ["locked_profit"]),
            "classification": "MAKER_TRIGGER_CANDIDATE" if econ["locked_profit"] >= TARGET else "MAKER_TRIGGER_NO_TRADE",
        })
    rows.sort(key=lambda r: D(r["locked_profit_after_hypothetical_maker_fill"]), reverse=True)
    return rows


async def run_once() -> dict[str, Any]:
    inventory = await near_term_inventory()
    events = [e for e in (inventory.get("events") or []) if isinstance(e, Mapping)]
    totals: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    spreads: dict[tuple[str, str], list[dict[str, Any]]] = {}
    props: dict[tuple[str, str, str, tuple[str, str], str], list[dict[str, Any]]] = {}
    market_slugs: set[str] = set()

    for event in events:
        event_slug = str(event.get("slug") or event.get("id") or "")
        series = str(event.get("series_slug") or event.get("subcategory") or "unknown").lower()
        phase = str(event.get("phase") or "UNKNOWN")
        start_time = event.get("start_time")
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            slug = str(market.get("slug") or "")
            if not slug:
                continue
            common = {"series": series, "phase": phase, "event_start_time": start_time}

            total_scope = _total_scope(market)
            if total_scope is not None:
                kind, scope = total_scope
                threshold = _threshold(market)
                if threshold is not None and _is_half_point(threshold):
                    totals.setdefault((event_slug, kind, scope), []).append({"slug": slug, "threshold": threshold, "title": market.get("title") or market.get("question"), "total_scope": scope, "kind": kind, **common})
                    market_slugs.add(slug)

            if _is_full_game_spread(market):
                for side in market.get("marketSides") or []:
                    if not isinstance(side, Mapping):
                        continue
                    team = _team_abbr(side)
                    description = _signed_description(side)
                    if not team or description is None:
                        continue
                    threshold = -description
                    if not _is_half_point(threshold):
                        continue
                    spreads.setdefault((event_slug, team), []).append({"slug": slug, "threshold": threshold, "title": market.get("title") or market.get("question"), "buy_yes": bool(side.get("long")), "team": team, "side_description": str(description), **common})
                    market_slugs.add(slug)

            if "mlb" in series or "mlb" in event_slug.lower():
                ident = _poly_identity(market)
                if ident is not None:
                    family, player, teams, game_date, threshold = ident
                    if _is_integer(threshold):
                        key = (game_date, family, _norm_player(player), tuple(sorted(teams)), event_slug)
                        props.setdefault(key, []).append({"slug": slug, "threshold": threshold, "title": market.get("title") or market.get("question"), "player": player, "family": family, **common})
                        market_slugs.add(slug)

    books: dict[str, TopBook] = {}
    sem = asyncio.Semaphore(20)
    async with httpx.AsyncClient(timeout=8.0) as http:
        async def fetch(slug: str) -> None:
            async with sem:
                try:
                    response = await http.get(f"{BASE}/v1/markets/{slug}/book")
                    if response.status_code == 200:
                        book = poly_top_book(response.json())
                        if book is not None:
                            books[slug] = book
                except Exception:
                    return
        await asyncio.gather(*(fetch(slug) for slug in sorted(market_slugs)))

    rows: list[dict[str, Any]] = []

    def score_ladder(kind: str, key: Any, ladder: list[dict[str, Any]], *, spread: bool = False) -> None:
        unique: dict[Decimal, dict[str, Any]] = {}
        dup: set[Decimal] = set()
        for item in ladder:
            t = D(str(item["threshold"]))
            if t in unique:
                dup.add(t)
            else:
                unique[t] = item
        ordered = [item for t, item in sorted(unique.items()) if t not in dup and item["slug"] in books]
        for i, low in enumerate(ordered):
            for high in ordered[i + 1:]:
                if not D(str(low["threshold"])) < D(str(high["threshold"])):
                    continue
                low_buy_yes = bool(low.get("buy_yes", True))
                high_selected_yes = bool(high.get("buy_yes", True))
                high_buy_yes = not high_selected_yes if spread else False
                if not spread:
                    low_buy_yes, high_buy_yes = True, False
                for option in _score_maker_pair(low_book=books[low["slug"]], low_buy_yes=low_buy_yes, high_book=books[high["slug"]], high_buy_yes=high_buy_yes):
                    rows.append({
                        "kind": kind,
                        "series": str(low.get("series") or "unknown"),
                        "phase": str(low.get("phase") or "UNKNOWN"),
                        "event_start_time": low.get("event_start_time"),
                        "ladder_key": str(key),
                        "low_slug": low["slug"], "low_title": low.get("title"), "low_threshold": str(low["threshold"]),
                        "high_slug": high["slug"], "high_title": high.get("title"), "high_threshold": str(high["threshold"]),
                        **({"total_scope": low.get("total_scope")} if kind in {"FULL_GAME_TOTAL", "TEAM_TOTAL"} else {}),
                        **({"player": low.get("player"), "family": low.get("family")} if kind == "MLB_PLAYER_PROP" else {}),
                        **({"team": low.get("team"), "low_side_description": low.get("side_description"), "high_side_description": high.get("side_description")} if kind == "FULL_GAME_SPREAD" else {}),
                        **option,
                        "quantity": 1, "maker_fill_required": True, "hedge_final_revalidation_required": True, "order_entry": False, "realized": False,
                    })

    for key, ladder in totals.items(): score_ladder(key[1], key, ladder)
    for key, ladder in spreads.items(): score_ladder("FULL_GAME_SPREAD", key, ladder, spread=True)
    for key, ladder in props.items(): score_ladder("MLB_PLAYER_PROP", key, ladder)

    rows.sort(key=lambda r: D(r["locked_profit_after_hypothetical_maker_fill"]), reverse=True)
    positive = [r for r in rows if r["classification"] == "MAKER_TRIGGER_CANDIDATE"]
    by_kind: dict[str, dict[str, int]] = {}
    by_series_kind: dict[str, dict[str, int]] = {}
    by_phase_series_kind: dict[str, dict[str, int]] = {}
    for row in rows:
        k, s, p = row["kind"], row.get("series") or "unknown", row.get("phase") or "UNKNOWN"
        bucket = by_kind.setdefault(k, {"scored": 0, "maker_trigger_candidates": 0})
        bucket["scored"] += 1
        sb = by_series_kind.setdefault(f"{s}|{k}", {"scored": 0, "maker_trigger_candidates": 0})
        sb["scored"] += 1
        psb = by_phase_series_kind.setdefault(f"{p}|{s}|{k}", {"scored": 0, "maker_trigger_candidates": 0})
        psb["scored"] += 1
        if row["classification"] == "MAKER_TRIGGER_CANDIDATE":
            bucket["maker_trigger_candidates"] += 1
            sb["maker_trigger_candidates"] += 1
            psb["maker_trigger_candidates"] += 1

    out = {
        "status": "SPORTS_POLY_MAKER_TRIGGER_CENSUS_V4_SCOPED_TOTALS_MULTI_SPORT",
        "inventory_event_count": inventory.get("event_count"),
        "live_event_count": inventory.get("live_event_count"),
        "pregame_event_count": inventory.get("pregame_event_count"),
        "inventory_horizon_hours": inventory.get("horizon_hours"),
        "inventory_series_counts": inventory.get("series_counts"),
        "inventory_phase_series_counts": inventory.get("phase_series_counts"),
        "unique_markets_requested": len(market_slugs),
        "books_fetched": len(books),
        "maker_scenarios_scored": len(rows),
        "maker_trigger_candidates": len(positive),
        "by_kind": by_kind,
        "by_series_kind": by_series_kind,
        "by_phase_series_kind": by_phase_series_kind,
        "best": rows[0] if rows else None,
        "top_candidates": positive[:100],
        "target_locked_after_fill": str(TARGET),
        "classification_note": "CANDIDATE_ONLY: maker not filled; event totals and team totals are separate scopes; hedge requires final revalidation; spread side semantics require provider-side audit before promotion",
        "order_entry": False,
        "realized": False,
    }
    print("SPORTS_POLY_MAKER_TRIGGER_CENSUS " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
