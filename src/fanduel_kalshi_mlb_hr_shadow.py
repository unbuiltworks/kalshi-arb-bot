"""One-shot FanDuel vs Kalshi MLB home-run shadow scanner.

Matches exact game/start/player/threshold Home Run props between FanDuel and Kalshi
KXMLBHR, prices the real FanDuel outcome against the opposite executable Kalshi q=1
side including conservative Kalshi taker fees, and reports shadow economics.

Pregame settlement is NOT certified: FanDuel batter props require a plate appearance,
while Kalshi KXMLBHR requires the player to be in the starting lineup and record a
plate appearance. No alerts or order entry are exposed here.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import json
import os
import re
import time
from typing import Any, Mapping

import httpx

from src.mlb_game_side_normalization import canonical_team
from src.strict_mlb_prop_schedule_guard import kalshi_event_start_utc
from src.strict_mlb_prop_snapshot import conservative_kalshi_fee
from src.three_venue_mlb_snapshot_pricing import kalshi_nested_top_book

D = Decimal
ONE = D("1")
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
SERIES = "KXMLBHR"
EVENTS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"
FD_MARKET = "batter_home_runs_alternate"
MAX_START_SKEW_MS = 5 * 60 * 1000

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


def _name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _teams_from_title(value: Any) -> tuple[str, str] | None:
    text = str(value or "")
    head = text.split(":", 1)[0]
    if " vs " not in head:
        return None
    left, right = head.split(" vs ", 1)
    a, b = canonical_team(left), canonical_team(right)
    if not a or not b or a == b:
        return None
    return tuple(sorted((a, b)))


def _kalshi_market_identity(event: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any] | None:
    teams = _teams_from_title(event.get("title"))
    start = kalshi_event_start_utc(event, market)
    match = HR_TITLE_RE.fullmatch(str(market.get("title") or "").strip())
    ticker = str(market.get("ticker") or "")
    if teams is None or start is None or match is None or not ticker:
        return None
    return {
        "teams": teams,
        "start_ms": int(start.timestamp() * 1000),
        "player": match.group("player").strip(),
        "player_key": _name(match.group("player")),
        "threshold": D(match.group("threshold")),
        "ticker": ticker,
    }


def _fanduel_legs(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    home, away = canonical_team(body.get("home_team")), canonical_team(body.get("away_team"))
    start_ms = _parse_ms(body.get("commence_time"))
    event_id = str(body.get("id") or "")
    if not home or not away or home == away or start_ms is None or not event_id:
        return []
    teams = tuple(sorted((home, away)))
    bookmaker = next((
        row for row in body.get("bookmakers") or []
        if isinstance(row, Mapping) and str(row.get("key") or "").lower() == "fanduel"
    ), None)
    if not isinstance(bookmaker, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for market in bookmaker.get("markets") or []:
        if not isinstance(market, Mapping) or str(market.get("key") or "") != FD_MARKET:
            continue
        quote_ms = _parse_ms(market.get("last_update"))
        if quote_ms is None:
            continue
        for row in market.get("outcomes") or []:
            if not isinstance(row, Mapping):
                continue
            side_text = str(row.get("name") or "").strip().lower()
            side = "YES" if side_text == "over" else "NO" if side_text == "under" else None
            point, odds = _d(row.get("point")), _d(row.get("price"))
            player = str(row.get("description") or "").strip()
            if side is None or point is None or odds is None or odds <= ONE or not player:
                continue
            twice = point * D("2")
            if point < 0 or twice != twice.to_integral_value() or int(twice) % 2 != 1:
                continue
            threshold = point.to_integral_value(rounding=ROUND_FLOOR) + ONE
            out.append({
                "event_id": event_id,
                "teams": teams,
                "start_ms": start_ms,
                "player": player,
                "player_key": _name(player),
                "threshold": threshold,
                "side": side,
                "decimal_odds": odds,
                "cost": ONE / odds,
                "quote_ms": quote_ms,
            })
    return out


def evaluate_pair(fd: Mapping[str, Any], kid: Mapping[str, Any], market: Mapping[str, Any], *, multiplier: Decimal, snapshot_ms: int) -> dict[str, Any] | None:
    if fd["teams"] != kid["teams"] or abs(int(fd["start_ms"]) - int(kid["start_ms"])) > MAX_START_SKEW_MS:
        return None
    if fd["player_key"] != kid["player_key"] or D(fd["threshold"]) != D(kid["threshold"]):
        return None
    book = kalshi_nested_top_book(market)
    if book is None:
        return None
    if fd["side"] == "YES":
        k_side, k_price, k_depth = "NO", book.no_ask, book.no_depth
    elif fd["side"] == "NO":
        k_side, k_price, k_depth = "YES", book.yes_ask, book.yes_depth
    else:
        return None
    if k_depth < ONE:
        return None
    k_fee = conservative_kalshi_fee(1, k_price, multiplier)
    k_all_in = k_price + k_fee
    total_cost = D(fd["cost"]) + k_all_in
    profit = ONE - total_cost
    return {
        "player": fd["player"],
        "threshold": str(fd["threshold"]),
        "fanduel_side": fd["side"],
        "fanduel_decimal_odds": str(fd["decimal_odds"]),
        "fanduel_cost_per_dollar": str(fd["cost"]),
        "kalshi_ticker": kid["ticker"],
        "kalshi_side": k_side,
        "kalshi_raw_price": str(k_price),
        "kalshi_fee_upper_bound": str(k_fee),
        "kalshi_all_in_cost": str(k_all_in),
        "kalshi_depth_q1": str(k_depth),
        "total_cost_per_dollar": str(total_cost),
        "shadow_profit_per_dollar": str(profit),
        "shadow_roi_on_cost": str(profit / total_cost) if total_cost > 0 else "0",
        "quote_skew_ms": abs(int(fd["quote_ms"]) - snapshot_ms),
        "settlement_status": "NOT_EXECUTABLE_PREPARTICIPATION_AND_LIFECYCLE_RULE_MISMATCH",
    }


async def _fetch_kalshi(http: httpx.AsyncClient) -> tuple[list[dict[str, Any]], Decimal]:
    events_response, series_response = await asyncio.gather(
        http.get(f"{KALSHI}/events", params={"limit":200,"status":"open","series_ticker":SERIES,"with_nested_markets":"true"}),
        http.get(f"{KALSHI}/series/{SERIES}"),
    )
    events_response.raise_for_status(); series_response.raise_for_status()
    events_body, series_body = events_response.json(), series_response.json()
    events = [dict(row) for row in events_body.get("events") or [] if isinstance(row, Mapping)]
    series = series_body.get("series") if isinstance(series_body.get("series"), Mapping) else {}
    multiplier = _d(series.get("fee_multiplier"))
    fee_type = str(series.get("fee_type") or "")
    if fee_type not in {"quadratic", "quadratic_with_maker_fees"} or multiplier is None:
        raise RuntimeError(f"unsupported Kalshi fee metadata:{fee_type}:{multiplier}")
    return events, multiplier


async def _fetch_odds_events(http: httpx.AsyncClient, api_key: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    response = await http.get(EVENTS_URL, params={"apiKey":api_key,"dateFormat":"iso"})
    response.raise_for_status()
    rows = [dict(row) for row in response.json() if isinstance(row, Mapping)] if isinstance(response.json(), list) else []
    return rows, {
        "remaining": response.headers.get("x-requests-remaining", ""),
        "used": response.headers.get("x-requests-used", ""),
        "last_cost": response.headers.get("x-requests-last", ""),
    }


async def run_once() -> dict[str, Any]:
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("THE_ODDS_API_KEY required")
    async with httpx.AsyncClient(timeout=30.0) as http:
        kalshi_events, multiplier = await _fetch_kalshi(http)
        odds_events, events_quota = await _fetch_odds_events(http, api_key)

        kalshi_rows: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
        for event in kalshi_events:
            for market in event.get("markets") or []:
                if not isinstance(market, Mapping):
                    continue
                identity = _kalshi_market_identity(event, market)
                if identity is not None:
                    kalshi_rows.append((identity, market))

        targets = {(row["teams"], int(row["start_ms"])) for row, _market in kalshi_rows}
        matched_odds: list[dict[str, Any]] = []
        for event in odds_events:
            home, away = canonical_team(event.get("home_team")), canonical_team(event.get("away_team"))
            start_ms = _parse_ms(event.get("commence_time"))
            if not home or not away or start_ms is None:
                continue
            teams = tuple(sorted((home, away)))
            if any(teams == t and abs(start_ms - s) <= MAX_START_SKEW_MS for t, s in targets):
                matched_odds.append(event)

        fd_legs: list[dict[str, Any]] = []
        prop_quota: list[dict[str, str]] = []
        event_audit: list[dict[str, Any]] = []
        for event in matched_odds:
            event_id = str(event.get("id") or "")
            response = await http.get(
                EVENT_ODDS_URL.format(event_id=event_id),
                params={"apiKey":api_key,"bookmakers":"fanduel","markets":FD_MARKET,"oddsFormat":"decimal","dateFormat":"iso"},
            )
            response.raise_for_status()
            body = response.json()
            legs = _fanduel_legs(body if isinstance(body, Mapping) else {})
            fd_legs.extend(legs)
            prop_quota.append({
                "event_id": event_id,
                "remaining": response.headers.get("x-requests-remaining", ""),
                "used": response.headers.get("x-requests-used", ""),
                "last_cost": response.headers.get("x-requests-last", ""),
            })
            event_audit.append({
                "event_id": event_id,
                "away_team": event.get("away_team"),
                "home_team": event.get("home_team"),
                "commence_time": event.get("commence_time"),
                "fanduel_hr_legs": len(legs),
            })

    snapshot_ms = int(time.time() * 1000)
    comparisons: list[dict[str, Any]] = []
    for fd in fd_legs:
        for kid, market in kalshi_rows:
            row = evaluate_pair(fd, kid, market, multiplier=multiplier, snapshot_ms=snapshot_ms)
            if row is not None:
                comparisons.append(row)
    comparisons.sort(key=lambda row: D(row["shadow_profit_per_dollar"]), reverse=True)
    positives = [row for row in comparisons if D(row["shadow_profit_per_dollar"]) > 0]
    fresh_10s = [row for row in positives if int(row["quote_skew_ms"]) <= 10_000]
    return {
        "status": "FANDUEL_KALSHI_MLB_HR_SHADOW",
        "kalshi_hr_events": len(kalshi_events),
        "kalshi_hr_markets": len(kalshi_rows),
        "matched_odds_events": len(matched_odds),
        "event_audit": event_audit,
        "fanduel_hr_legs": len(fd_legs),
        "exact_directional_comparisons": len(comparisons),
        "positive_shadow_observations": len(positives),
        "fresh_10s_positive_observations": len(fresh_10s),
        "top_comparisons": comparisons[:50],
        "top_positive": positives[:30],
        "events_endpoint_quota": events_quota,
        "prop_quota": prop_quota,
        "kalshi_fee_multiplier": str(multiplier),
        "rule_gate": "FANDUEL_PA_REQUIRES_PA;KALSHI_REQUIRES_START_PLUS_PA;COMPLETE_LIFECYCLE_EQUIVALENCE_REQUIRED",
        "alert_eligible": False,
        "execution_eligible": False,
        "realized_pnl": "0",
        "read_only": True,
        "order_entry": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)
