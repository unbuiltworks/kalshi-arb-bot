"""All-US-sportsbooks vs Kalshi MLB multi-family prop shadow scanner.

Uses one regions=us event-props request per matched game to collect every returned
sportsbook, then compares each real offered outcome against the exact opposite Kalshi
q=1..5 side. This materially widens price discovery without increasing The Odds API
market-count cost versus a FanDuel-only request.

Research-only: bookmaker-specific settlement certification remains mandatory before
manual alerts or execution. No order-entry or notification code is present.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_FLOOR
import json
import os
import time
from typing import Any, Mapping

import httpx

from src import fanduel_kalshi_mlb_prop_multi_shadow as base
from src.mlb_game_side_normalization import canonical_team
from src.strict_mlb_prop_adapters import _name
from src.strict_mlb_prop_snapshot import conservative_kalshi_fee

D = Decimal
ONE = D("1")


def _sportsbook_legs(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    home, away = canonical_team(body.get("home_team")), canonical_team(body.get("away_team"))
    start_ms = base._parse_ms(body.get("commence_time"))
    event_id = str(body.get("id") or "")
    if not home or not away or home == away or start_ms is None or not event_id:
        return []
    teams = tuple(sorted((home, away)))
    best: dict[tuple[str, str, str, Decimal, str], dict[str, Any]] = {}
    for bookmaker in body.get("bookmakers") or []:
        if not isinstance(bookmaker, Mapping):
            continue
        book = str(bookmaker.get("key") or bookmaker.get("title") or "").strip().lower()
        if not book:
            continue
        for market in bookmaker.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            market_key = str(market.get("key") or "")
            family = base.FD_MARKET_TO_FAMILY.get(market_key)
            quote_ms = base._parse_ms(market.get("last_update"))
            if family is None or quote_ms is None:
                continue
            for raw in market.get("outcomes") or []:
                if not isinstance(raw, Mapping):
                    continue
                side_text = str(raw.get("name") or "").strip().lower()
                side = "YES" if side_text == "over" else "NO" if side_text == "under" else None
                point, odds = base._d(raw.get("point")), base._d(raw.get("price"))
                player = str(raw.get("description") or "").strip()
                if side is None or point is None or odds is None or odds <= ONE or not player:
                    continue
                twice = point * D("2")
                if point < 0 or twice != twice.to_integral_value() or int(twice) % 2 != 1:
                    continue
                threshold = point.to_integral_value(rounding=ROUND_FLOOR) + ONE
                player_key = _name(player)
                marker = (book, family, player_key, threshold, side)
                row = {
                    "book": book,
                    "event_id": event_id,
                    "teams": teams,
                    "start_ms": start_ms,
                    "family": family,
                    "stat": base.FAMILY_STAT[family],
                    "player": player,
                    "player_key": player_key,
                    "threshold": threshold,
                    "side": side,
                    "decimal_odds": odds,
                    "unit_stake": ONE / odds,
                    "quote_ms": quote_ms,
                    "market_key": market_key,
                }
                current = best.get(marker)
                if current is None or row["unit_stake"] < current["unit_stake"] or (row["unit_stake"] == current["unit_stake"] and quote_ms > current["quote_ms"]):
                    best[marker] = row
    return list(best.values())


def best_plan(sb: Mapping[str, Any], k: Mapping[str, Any], *, multiplier: Decimal, snapshot_ms: int) -> dict[str, Any] | None:
    if sb["teams"] != k["teams"] or abs(int(sb["start_ms"]) - int(k["start_ms"])) > base.MAX_START_SKEW_MS:
        return None
    if sb["family"] != k["family"] or sb["player_key"] != k["player_key"] or D(sb["threshold"]) != D(k["threshold"]):
        return None
    book = k["book"]
    if sb["side"] == "YES":
        k_side, k_price, k_depth = "NO", book.no_ask, book.no_depth
    elif sb["side"] == "NO":
        k_side, k_price, k_depth = "YES", book.yes_ask, book.yes_depth
    else:
        return None

    best_row = None
    for q in range(1, min(base.MAX_Q, int(k_depth)) + 1):
        sb_stake = D(q) * D(sb["unit_stake"])
        if sb_stake < base.ASSUMED_FD_MIN_STAKE:
            continue
        k_fee = conservative_kalshi_fee(q, k_price, multiplier)
        k_cost = D(q) * k_price + k_fee
        total = sb_stake + k_cost
        if total > base.MAX_CAPITAL:
            continue
        profit = D(q) - total
        row = {
            "sportsbook": sb["book"],
            "family": sb["family"],
            "stat": sb["stat"],
            "player": sb["player"],
            "threshold": str(sb["threshold"]),
            "quantity": q,
            "sportsbook_side": sb["side"],
            "sportsbook_market": sb["market_key"],
            "sportsbook_decimal_odds": str(sb["decimal_odds"]),
            "sportsbook_stake": str(sb_stake),
            "kalshi_ticker": k["ticker"],
            "kalshi_side": k_side,
            "kalshi_raw_price": str(k_price),
            "kalshi_fee_upper_bound": str(k_fee),
            "kalshi_cost": str(k_cost),
            "kalshi_depth": str(k_depth),
            "total_capital": str(total),
            "shadow_locked_profit": str(profit),
            "shadow_roi_on_capital": str(profit / total) if total > 0 else "0",
            "quote_skew_ms": abs(int(sb["quote_ms"]) - snapshot_ms),
            "settlement_status": "NOT_EXECUTABLE_BOOKMAKER_RULE_CERTIFICATION_REQUIRED",
        }
        if best_row is None:
            best_row = row
            continue
        new_profit, old_profit = D(row["shadow_locked_profit"]), D(best_row["shadow_locked_profit"])
        if new_profit > old_profit or (new_profit == old_profit and D(row["total_capital"]) < D(best_row["total_capital"])):
            best_row = row
    return best_row


async def run_once() -> dict[str, Any]:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("THE_ODDS_API_KEY required")
    async with httpx.AsyncClient(timeout=30.0) as http:
        events_by_series = {}; multipliers = {}
        for series in base.SERIES_FAMILY:
            events, multiplier = await base._fetch_series(http, series)
            events_by_series[series] = events; multipliers[series] = multiplier
            await asyncio.sleep(0.05)

        k_rows = []; inventory = Counter()
        for events in events_by_series.values():
            for event in events:
                for market in event.get("markets") or []:
                    if not isinstance(market, Mapping):
                        continue
                    row = base._kalshi_row(event, market)
                    if row is not None:
                        k_rows.append(row); inventory[row["family"]] += 1

        odds_events, event_quota = await base._fetch_odds_events(http, api_key)
        targets: dict[str, set[str]] = defaultdict(set); event_by_id = {}
        for event in odds_events:
            home, away = canonical_team(event.get("home_team")), canonical_team(event.get("away_team"))
            start_ms = base._parse_ms(event.get("commence_time")); event_id = str(event.get("id") or "")
            if not home or not away or start_ms is None or not event_id:
                continue
            teams = tuple(sorted((home, away)))
            families = {k["family"] for k in k_rows if k["teams"] == teams and abs(int(k["start_ms"]) - start_ms) <= base.MAX_START_SKEW_MS}
            if not families:
                continue
            event_by_id[event_id] = event
            for family in families:
                targets[event_id].update(base.FAMILY_TO_FD_MARKETS.get(family, ()))

        async def fetch_event(event_id: str, markets: set[str]):
            response = await http.get(
                base.EVENT_ODDS_URL.format(event_id=event_id),
                params={"apiKey":api_key,"regions":"us","markets":",".join(sorted(markets)),"oddsFormat":"decimal","dateFormat":"iso"},
            )
            response.raise_for_status()
            return event_id, response

        responses = await asyncio.gather(*(fetch_event(event_id, markets) for event_id, markets in targets.items()))
        legs = []; quotas = []; audits = []
        for event_id, response in responses:
            body = response.json(); parsed = _sportsbook_legs(body if isinstance(body, Mapping) else {})
            legs.extend(parsed)
            event = event_by_id[event_id]
            book_counts = Counter(row["book"] for row in parsed)
            quotas.append({"event_id":event_id,"requested_markets":sorted(targets[event_id]),"remaining":response.headers.get("x-requests-remaining",""),"used":response.headers.get("x-requests-used",""),"last_cost":response.headers.get("x-requests-last","")})
            audits.append({"event_id":event_id,"away_team":event.get("away_team"),"home_team":event.get("home_team"),"commence_time":event.get("commence_time"),"sportsbook_legs":len(parsed),"books":dict(book_counts)})

    snapshot_ms = int(time.time() * 1000)
    plans = []
    for sb in legs:
        for k in k_rows:
            row = best_plan(sb, k, multiplier=multipliers[k["series"]], snapshot_ms=snapshot_ms)
            if row is not None:
                plans.append(row)
    plans.sort(key=lambda row: D(row["shadow_locked_profit"]), reverse=True)
    positives = [row for row in plans if D(row["shadow_locked_profit"]) > 0]
    fresh_10s = [row for row in positives if int(row["quote_skew_ms"]) <= 10_000]
    books_seen = Counter(row["book"] for row in legs)
    return {
        "status":"ALLBOOKS_KALSHI_MLB_PROP_MULTI_SHADOW",
        "kalshi_inventory_by_family":dict(inventory),
        "kalshi_exact_markets":len(k_rows),
        "matched_odds_events":len(targets),
        "sportsbooks_seen":dict(books_seen),
        "sportsbook_legs":len(legs),
        "exact_matched_plans":len(plans),
        "positive_shadow_plans":len(positives),
        "fresh_10s_positive_plans":len(fresh_10s),
        "positive_by_book":dict(Counter(row["sportsbook"] for row in positives)),
        "positive_by_family":dict(Counter(row["family"] for row in positives)),
        "top_plans":plans[:80],
        "top_positive":positives[:60],
        "event_audit":audits,
        "events_endpoint_quota":event_quota,
        "prop_quota":quotas,
        "max_quantity":base.MAX_Q,
        "max_capital":str(base.MAX_CAPITAL),
        "provisional_min_sportsbook_stake":str(base.ASSUMED_FD_MIN_STAKE),
        "rule_gate":"BOOKMAKER_SPECIFIC_COMPLETE_RULE_CERTIFICATION_REQUIRED",
        "alert_eligible":False,
        "execution_eligible":False,
        "realized_pnl":"0",
        "read_only":True,
        "order_entry":False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)
