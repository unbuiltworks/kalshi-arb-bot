"""Direct FanDuel vs Kalshi MLB multi-family prop shadow scanner.

Scans exact player/game/stat/threshold counterparts for Hits, Hits+Runs+RBIs,
Total Bases, Pitcher Strikeouts, and Home Runs. FanDuel contributes only real offered
Over/Under outcomes; Kalshi contributes the exact opposite executable top-of-book
side. Economics are evaluated for q=1..5 payouts using current Kalshi fee metadata,
with total capital constrained below the existing $5 experiment ceiling.

Research-only. Settlement certification remains a separate fail-closed gate; this
module sends no alerts and has no order-entry path.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import json
import os
import re
import time
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_mlb_prop_shadow import _kalshi_teams
from src.mlb_game_side_normalization import canonical_team
from src.strict_mlb_prop_adapters import FAMILY_SERIES, _kalshi_identity, _name
from src.strict_mlb_prop_schedule_guard import kalshi_event_start_utc
from src.strict_mlb_prop_snapshot import conservative_kalshi_fee
from src.three_venue_mlb_snapshot_pricing import kalshi_nested_top_book

D = Decimal
ONE = D("1")
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
MAX_START_SKEW_MS = 5 * 60 * 1000
MAX_Q = 5
MAX_CAPITAL = D("5")
# Conservative practical floor for a future manual FanDuel leg. This is a sizing
# guard only; no result becomes executable until current account/jurisdiction limits
# are confirmed at alert time.
ASSUMED_FD_MIN_STAKE = D("0.10")
EVENTS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"

HR_FAMILY = "baseball_player_home_runs"
HR_SERIES = "KXMLBHR"
SERIES_FAMILY = {series: family for family, series in FAMILY_SERIES.items()} | {HR_SERIES: HR_FAMILY}
FAMILY_STAT = {
    "baseball_player_hits": "hits",
    "baseball_player_hits_runs_rbis": "hits_runs_rbis",
    "baseball_player_total_bases": "total_bases",
    "baseball_player_strikeouts": "strikeouts",
    HR_FAMILY: "home_runs",
}
FD_MARKET_TO_FAMILY = {
    "batter_hits_alternate": "baseball_player_hits",
    "batter_hits_runs_rbis_alternate": "baseball_player_hits_runs_rbis",
    "batter_total_bases_alternate": "baseball_player_total_bases",
    "batter_home_runs_alternate": HR_FAMILY,
    "pitcher_strikeouts": "baseball_player_strikeouts",
    "pitcher_strikeouts_alternate": "baseball_player_strikeouts",
}
FAMILY_TO_FD_MARKETS = {
    family: tuple(sorted(key for key, value in FD_MARKET_TO_FAMILY.items() if value == family))
    for family in set(FD_MARKET_TO_FAMILY.values())
}
HR_TITLE_RE = re.compile(r"^(?P<player>.+?):\s*(?P<threshold>\d+)\+\s*home runs\?$", re.I)


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _parse_ms(value: Any) -> int | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.astimezone(timezone.utc).timestamp() * 1000)
    except Exception:
        return None


def _kalshi_row(event: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any] | None:
    series = str(event.get("series_ticker") or market.get("series_ticker") or "")
    family = SERIES_FAMILY.get(series)
    teams = _kalshi_teams(event)
    start = kalshi_event_start_utc(event, market)
    ticker = str(market.get("ticker") or "")
    if family is None or teams is None or start is None or not ticker:
        return None
    if family == HR_FAMILY:
        match = HR_TITLE_RE.fullmatch(str(market.get("title") or "").strip())
        if match is None:
            return None
        player, threshold = match.group("player").strip(), D(match.group("threshold"))
    else:
        parsed = _kalshi_identity(event, market, family)
        if parsed is None:
            return None
        player, threshold = parsed
    book = kalshi_nested_top_book(market)
    if book is None:
        return None
    return {
        "series": series,
        "family": family,
        "stat": FAMILY_STAT[family],
        "teams": teams,
        "start_ms": int(start.timestamp() * 1000),
        "player": player,
        "player_key": _name(player),
        "threshold": D(threshold),
        "ticker": ticker,
        "book": book,
    }


def _fanduel_legs(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    home, away = canonical_team(body.get("home_team")), canonical_team(body.get("away_team"))
    start_ms = _parse_ms(body.get("commence_time"))
    event_id = str(body.get("id") or "")
    if not home or not away or home == away or start_ms is None or not event_id:
        return []
    teams = tuple(sorted((home, away)))
    bookmaker = next((row for row in body.get("bookmakers") or [] if isinstance(row, Mapping) and str(row.get("key") or "").lower() == "fanduel"), None)
    if not isinstance(bookmaker, Mapping):
        return []

    best: dict[tuple[str, str, Decimal, str], dict[str, Any]] = {}
    for market in bookmaker.get("markets") or []:
        if not isinstance(market, Mapping):
            continue
        market_key = str(market.get("key") or "")
        family = FD_MARKET_TO_FAMILY.get(market_key)
        quote_ms = _parse_ms(market.get("last_update"))
        if family is None or quote_ms is None:
            continue
        for raw in market.get("outcomes") or []:
            if not isinstance(raw, Mapping):
                continue
            side_text = str(raw.get("name") or "").strip().lower()
            side = "YES" if side_text == "over" else "NO" if side_text == "under" else None
            point, odds = _d(raw.get("point")), _d(raw.get("price"))
            player = str(raw.get("description") or "").strip()
            if side is None or point is None or odds is None or odds <= ONE or not player:
                continue
            twice = point * D("2")
            if point < 0 or twice != twice.to_integral_value() or int(twice) % 2 != 1:
                continue
            threshold = point.to_integral_value(rounding=ROUND_FLOOR) + ONE
            player_key = _name(player)
            marker = (family, player_key, threshold, side)
            row = {
                "event_id": event_id,
                "teams": teams,
                "start_ms": start_ms,
                "family": family,
                "stat": FAMILY_STAT[family],
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
            # Lower stake for the same $1 payout means better odds. Prefer freshest on tie.
            if current is None or row["unit_stake"] < current["unit_stake"] or (row["unit_stake"] == current["unit_stake"] and quote_ms > current["quote_ms"]):
                best[marker] = row
    return list(best.values())


def best_plan(fd: Mapping[str, Any], k: Mapping[str, Any], *, multiplier: Decimal, snapshot_ms: int) -> dict[str, Any] | None:
    if fd["teams"] != k["teams"] or abs(int(fd["start_ms"]) - int(k["start_ms"])) > MAX_START_SKEW_MS:
        return None
    if fd["family"] != k["family"] or fd["player_key"] != k["player_key"] or D(fd["threshold"]) != D(k["threshold"]):
        return None
    book = k["book"]
    if fd["side"] == "YES":
        k_side, k_price, k_depth = "NO", book.no_ask, book.no_depth
    elif fd["side"] == "NO":
        k_side, k_price, k_depth = "YES", book.yes_ask, book.yes_depth
    else:
        return None

    best_row: dict[str, Any] | None = None
    for q in range(1, min(MAX_Q, int(k_depth)) + 1):
        fd_stake = D(q) * D(fd["unit_stake"])
        if fd_stake < ASSUMED_FD_MIN_STAKE:
            continue
        k_fee = conservative_kalshi_fee(q, k_price, multiplier)
        k_cost = D(q) * k_price + k_fee
        total_cost = fd_stake + k_cost
        if total_cost > MAX_CAPITAL:
            continue
        profit = D(q) - total_cost
        row = {
            "family": fd["family"],
            "stat": fd["stat"],
            "player": fd["player"],
            "threshold": str(fd["threshold"]),
            "quantity": q,
            "fanduel_side": fd["side"],
            "fanduel_market": fd["market_key"],
            "fanduel_decimal_odds": str(fd["decimal_odds"]),
            "fanduel_stake": str(fd_stake),
            "kalshi_ticker": k["ticker"],
            "kalshi_side": k_side,
            "kalshi_raw_price": str(k_price),
            "kalshi_fee_upper_bound": str(k_fee),
            "kalshi_cost": str(k_cost),
            "kalshi_depth": str(k_depth),
            "total_capital": str(total_cost),
            "shadow_locked_profit": str(profit),
            "shadow_roi_on_capital": str(profit / total_cost) if total_cost > 0 else "0",
            "quote_skew_ms": abs(int(fd["quote_ms"]) - snapshot_ms),
            "settlement_status": "NOT_EXECUTABLE_COMPLETE_RULE_CERTIFICATION_REQUIRED",
        }
        if best_row is None or D(row["shadow_locked_profit"]) > D(best_row["shadow_locked_profit"]):
            best_row = row
    return best_row


async def _fetch_series(http: httpx.AsyncClient, series: str) -> tuple[list[dict[str, Any]], Decimal]:
    event_response = await http.get(f"{KALSHI}/events", params={"limit":200,"status":"open","series_ticker":series,"with_nested_markets":"true"})
    event_response.raise_for_status()
    series_response = await http.get(f"{KALSHI}/series/{series}")
    series_response.raise_for_status()
    body = event_response.json(); meta_body = series_response.json()
    events = [dict(row) for row in body.get("events") or [] if isinstance(row, Mapping)]
    meta = meta_body.get("series") if isinstance(meta_body.get("series"), Mapping) else {}
    multiplier = _d(meta.get("fee_multiplier")); fee_type = str(meta.get("fee_type") or "")
    if multiplier is None or fee_type not in {"quadratic", "quadratic_with_maker_fees"}:
        raise RuntimeError(f"bad fee metadata {series}:{fee_type}:{multiplier}")
    return events, multiplier


async def _fetch_odds_events(http: httpx.AsyncClient, api_key: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    response = await http.get(EVENTS_URL, params={"apiKey":api_key,"dateFormat":"iso"})
    response.raise_for_status()
    payload = response.json()
    rows = [dict(row) for row in payload if isinstance(row, Mapping)] if isinstance(payload, list) else []
    return rows, {"remaining":response.headers.get("x-requests-remaining",""),"used":response.headers.get("x-requests-used",""),"last_cost":response.headers.get("x-requests-last","")}


async def run_once() -> dict[str, Any]:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("THE_ODDS_API_KEY required")
    async with httpx.AsyncClient(timeout=30.0) as http:
        # Sequential Kalshi series discovery avoids recreating earlier public 429 bursts.
        events_by_series: dict[str, list[dict[str, Any]]] = {}
        multipliers: dict[str, Decimal] = {}
        for series in SERIES_FAMILY:
            events, multiplier = await _fetch_series(http, series)
            events_by_series[series] = events
            multipliers[series] = multiplier
            await asyncio.sleep(0.05)

        k_rows: list[dict[str, Any]] = []
        inventory = Counter()
        for series, events in events_by_series.items():
            for event in events:
                for market in event.get("markets") or []:
                    if not isinstance(market, Mapping):
                        continue
                    row = _kalshi_row(event, market)
                    if row is not None:
                        k_rows.append(row); inventory[row["family"]] += 1

        odds_events, event_quota = await _fetch_odds_events(http, api_key)
        # Map each Kalshi game to the exact FanDuel market families actually needed.
        target_markets: dict[str, set[str]] = defaultdict(set)
        event_by_id: dict[str, dict[str, Any]] = {}
        for event in odds_events:
            home, away = canonical_team(event.get("home_team")), canonical_team(event.get("away_team"))
            start_ms = _parse_ms(event.get("commence_time")); event_id = str(event.get("id") or "")
            if not home or not away or start_ms is None or not event_id:
                continue
            teams = tuple(sorted((home, away)))
            families = {
                k["family"] for k in k_rows
                if k["teams"] == teams and abs(int(k["start_ms"]) - start_ms) <= MAX_START_SKEW_MS
            }
            if not families:
                continue
            event_by_id[event_id] = event
            for family in families:
                target_markets[event_id].update(FAMILY_TO_FD_MARKETS.get(family, ()))

        async def fetch_fd(event_id: str, markets: set[str]):
            response = await http.get(
                EVENT_ODDS_URL.format(event_id=event_id),
                params={"apiKey":api_key,"bookmakers":"fanduel","markets":",".join(sorted(markets)),"oddsFormat":"decimal","dateFormat":"iso"},
            )
            response.raise_for_status()
            return event_id, response

        fd_results = await asyncio.gather(*(fetch_fd(event_id, markets) for event_id, markets in target_markets.items()))
        fd_legs: list[dict[str, Any]] = []
        prop_quota: list[dict[str, Any]] = []
        event_audit: list[dict[str, Any]] = []
        for event_id, response in fd_results:
            body = response.json(); legs = _fanduel_legs(body if isinstance(body, Mapping) else {})
            fd_legs.extend(legs)
            event = event_by_id[event_id]
            prop_quota.append({"event_id":event_id,"requested_markets":sorted(target_markets[event_id]),"remaining":response.headers.get("x-requests-remaining",""),"used":response.headers.get("x-requests-used",""),"last_cost":response.headers.get("x-requests-last","")})
            event_audit.append({"event_id":event_id,"away_team":event.get("away_team"),"home_team":event.get("home_team"),"commence_time":event.get("commence_time"),"requested_markets":sorted(target_markets[event_id]),"fanduel_legs":len(legs)})

    snapshot_ms = int(time.time() * 1000)
    plans: list[dict[str, Any]] = []
    for fd in fd_legs:
        for k in k_rows:
            row = best_plan(fd, k, multiplier=multipliers[k["series"]], snapshot_ms=snapshot_ms)
            if row is not None:
                plans.append(row)
    plans.sort(key=lambda row: D(row["shadow_locked_profit"]), reverse=True)
    positives = [row for row in plans if D(row["shadow_locked_profit"]) > 0]
    fresh_10s = [row for row in positives if int(row["quote_skew_ms"]) <= 10_000]
    return {
        "status":"FANDUEL_KALSHI_MLB_PROP_MULTI_SHADOW",
        "kalshi_inventory_by_family":dict(inventory),
        "kalshi_exact_markets":len(k_rows),
        "matched_odds_events":len(target_markets),
        "event_audit":event_audit,
        "fanduel_legs":len(fd_legs),
        "exact_matched_plans":len(plans),
        "positive_shadow_plans":len(positives),
        "fresh_10s_positive_plans":len(fresh_10s),
        "positive_by_family":dict(Counter(row["family"] for row in positives)),
        "top_plans":plans[:60],
        "top_positive":positives[:40],
        "events_endpoint_quota":event_quota,
        "prop_quota":prop_quota,
        "max_quantity":MAX_Q,
        "max_capital":str(MAX_CAPITAL),
        "assumed_fanduel_min_stake":str(ASSUMED_FD_MIN_STAKE),
        "rule_gate":"COMPLETE_FANDUEL_PA_VS_KALSHI_FAMILY_LIFECYCLE_CERTIFICATION_REQUIRED",
        "alert_eligible":False,
        "execution_eligible":False,
        "realized_pnl":"0",
        "read_only":True,
        "order_entry":False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)
