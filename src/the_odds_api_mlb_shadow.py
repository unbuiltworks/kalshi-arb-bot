"""Read-only MLB sportsbook-vs-prediction-market shadow scanner.

Uses The Odds API as a cheap broad sportsbook discovery layer and compares its
moneyline/spread/total prices with the existing Gemini/Polymarket/Kalshi q=1
snapshot pricing. It never places orders.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any, Mapping

import httpx

from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.mlb_game_side_normalization import canonical_team, normalize_gemini_mlb, normalize_polymarket_mlb
from src.three_venue_mlb_price_scan import _dedupe, price_counterparts
from src.universal_prediction_quant import MarketQuote, find_cross_venue_locks
from src.universal_sports_identity import RuleAudit, SportsContractIdentity, SportsEventIdentity
from src.universal_three_venue_sports import fetch_gemini_mlb, fetch_kalshi_mlb, fetch_polymarket_mlb

logger = logging.getLogger("kalshi_arb.the_odds_api_mlb_shadow")
D = Decimal
ONE = D("1")
API = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _start_ms(value: Any) -> int | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None
    return int(dt.timestamp() * 1000)


def _rule(book: str) -> RuleAudit:
    return RuleAudit(
        official_source=f"{book}_sportsbook_rules_unverified",
        overtime="baseball_extra_innings_unverified",
        postponement="unverified",
        stat_corrections="unverified",
        push_tie="half_point_only_or_moneyline",
        complete=False,
    )


def _key(*, book: str, event: SportsEventIdentity, family: str, subject: str,
         stat: str, comparator: str, line: Decimal | None) -> str:
    return SportsContractIdentity(
        venue=book,
        market_id="shadow",
        event=event,
        market_family=family,
        scope="full_contest",
        subject_type="team" if family != "game_total" else "contest",
        subject=subject,
        stat=stat,
        comparator=comparator,
        line=line,
        rule_audit=_rule(book),
    ).contract_key()


def _prob_cost(decimal_odds: Any) -> Decimal | None:
    price = _d(decimal_odds)
    if price is None or price <= ONE:
        return None
    return ONE / price


def _half(value: Decimal | None) -> bool:
    if value is None:
        return False
    twice = abs(value) * D("2")
    return twice == twice.to_integral_value() and int(twice) % 2 == 1


def _mk_quote(*, book: str, event_id: str, settlement_key: str,
              yes_cost: Decimal, no_cost: Decimal, received_ms: int) -> MarketQuote:
    return MarketQuote(
        venue=f"sportsbook:{book}",
        market_id=event_id,
        settlement_key=settlement_key,
        category="sports_mlb_game",
        yes_cost=yes_cost,
        no_cost=no_cost,
        yes_depth=ONE,
        no_depth=ONE,
        received_ms=received_ms,
        settlement_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
        expected_settlement_minutes=None,
    )


def sportsbook_quotes(events: list[Mapping[str, Any]], *, received_ms: int) -> list[MarketQuote]:
    out: list[MarketQuote] = []
    for raw in events:
        home = canonical_team(raw.get("home_team")); away = canonical_team(raw.get("away_team"))
        start = _start_ms(raw.get("commence_time"))
        event_id = str(raw.get("id") or "")
        if not home or not away or home == away or start is None or not event_id:
            continue
        teams = tuple(sorted((home, away)))
        for bookmaker in raw.get("bookmakers") or []:
            if not isinstance(bookmaker, Mapping):
                continue
            book = str(bookmaker.get("key") or bookmaker.get("title") or "").strip().lower()
            if not book:
                continue
            event = SportsEventIdentity(sport="baseball", league="mlb", participants=teams, scheduled_start_ms=start)
            markets = {str(m.get("key") or ""): m for m in bookmaker.get("markets") or [] if isinstance(m, Mapping)}

            h2h = markets.get("h2h")
            if isinstance(h2h, Mapping):
                prices: dict[str, Decimal] = {}
                for row in h2h.get("outcomes") or []:
                    if not isinstance(row, Mapping): continue
                    team = canonical_team(row.get("name")); cost = _prob_cost(row.get("price"))
                    if team in teams and cost is not None: prices[team] = cost
                if len(prices) == 2:
                    for team in teams:
                        other = teams[1] if teams[0] == team else teams[0]
                        key = _key(book=book, event=event, family="moneyline", subject=team, stat="", comparator="YES", line=None)
                        out.append(_mk_quote(book=book, event_id=f"{event_id}:h2h:{team}", settlement_key=key, yes_cost=prices[team], no_cost=prices[other], received_ms=received_ms))

            spreads = markets.get("spreads")
            if isinstance(spreads, Mapping):
                rows = []
                for row in spreads.get("outcomes") or []:
                    if not isinstance(row, Mapping): continue
                    team = canonical_team(row.get("name")); point = _d(row.get("point")); cost = _prob_cost(row.get("price"))
                    if team in teams and _half(point) and cost is not None: rows.append((team, point, cost))
                for team, point, cost in rows:
                    other = teams[1] if teams[0] == team else teams[0]
                    opp = next((c for t,p,c in rows if t == other and p == -point), None)
                    if opp is None: continue
                    threshold = -point
                    key = _key(book=book, event=event, family="spread", subject=team, stat="runs", comparator="GT", line=threshold)
                    out.append(_mk_quote(book=book, event_id=f"{event_id}:spread:{team}:{point}", settlement_key=key, yes_cost=cost, no_cost=opp, received_ms=received_ms))

            totals = markets.get("totals")
            if isinstance(totals, Mapping):
                by_point: dict[Decimal, dict[str, Decimal]] = {}
                for row in totals.get("outcomes") or []:
                    if not isinstance(row, Mapping): continue
                    point = _d(row.get("point")); cost = _prob_cost(row.get("price")); side = str(row.get("name") or "").strip().lower()
                    if not _half(point) or cost is None or side not in {"over","under"}: continue
                    by_point.setdefault(point, {})[side] = cost
                for point, sides in by_point.items():
                    if set(sides) != {"over","under"}: continue
                    key = _key(book=book, event=event, family="game_total", subject="contest", stat="runs", comparator="GT", line=point)
                    out.append(_mk_quote(book=book, event_id=f"{event_id}:total:{point}", settlement_key=key, yes_cost=sides["over"], no_cost=sides["under"], received_ms=received_ms))
    return out


async def fetch_sportsbooks(http: httpx.AsyncClient, api_key: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    response = await http.get(API, params={
        "apiKey": api_key,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    })
    response.raise_for_status()
    body = response.json()
    rows = [dict(x) for x in body if isinstance(x, Mapping)] if isinstance(body, list) else []
    quota = {
        "remaining": response.headers.get("x-requests-remaining", ""),
        "used": response.headers.get("x-requests-used", ""),
        "last_cost": response.headers.get("x-requests-last", ""),
    }
    return rows, quota


async def run_once(api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        sportsbook_events, gemini, poly_raw, kalshi = await asyncio.gather(
            fetch_sportsbooks(http, api_key), fetch_gemini_mlb(http), fetch_polymarket_mlb(http), fetch_kalshi_mlb(http)
        )
        events, quota = sportsbook_events
        poly = [dict(x) for x in poly_raw]
        direct, errors, direct_keys = await price_counterparts(http, gemini, poly, kalshi)
    received_ms = int(time.time() * 1000)
    retail = sportsbook_quotes(events, received_ms=received_ms)
    direct_quotes = [row.quote for row in direct]
    direct_key_set = {q.settlement_key for q in direct_quotes}
    matched_retail = [q for q in retail if q.settlement_key in direct_key_set]
    locks = _dedupe(find_cross_venue_locks(
        direct_quotes + matched_retail,
        max_quote_skew_ms=70000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    retail_locks = [x for x in locks if str(x.yes_venue).startswith("sportsbook:") or str(x.no_venue).startswith("sportsbook:")]
    result = {
        "status": "THE_ODDS_API_MLB_SHADOW",
        "sportsbook_events": len(events),
        "sportsbook_quotes": len(retail),
        "sportsbook_quotes_matching_direct_keys": len(matched_retail),
        "books": dict(Counter(q.venue for q in retail)),
        "direct_counterpart_keys": direct_keys,
        "direct_priced_quotes": len(direct_quotes),
        "retail_positive_locks": len(retail_locks),
        "top_retail_locks": [x.as_dict() for x in retail_locks[:30]],
        "quota": quota,
        "fetch_error_count": len(errors),
        "rule_gate": "SPORTSBOOK_RULE_AUDIT_REQUIRED_BEFORE_EXECUTION",
        "read_only": True,
        "order_entry": False,
    }
    logger.info("THE_ODDS_API_MLB_SHADOW %s", json.dumps(result, separators=(",", ":"), default=str))
    return result


def _quota_remaining(result: Mapping[str, Any]) -> int | None:
    raw = (result.get("quota") or {}).get("remaining") if isinstance(result.get("quota"), Mapping) else None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


async def supervise(api_key: str, interval_s: float, quota_floor: int) -> None:
    while True:
        started = time.monotonic()
        try:
            result = await run_once(api_key)
            remaining = _quota_remaining(result)
            if remaining is not None and remaining <= quota_floor:
                logger.warning(
                    "THE_ODDS_API_QUOTA_GUARD remaining=%s floor=%s polling_paused=true",
                    remaining,
                    quota_floor,
                )
                while True:
                    await asyncio.sleep(3600.0)
        except Exception:
            logger.exception("THE_ODDS_API_MLB_SHADOW_ERROR")
        await asyncio.sleep(max(5.0, interval_s - (time.monotonic() - started)))


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--quota-floor", type=int, default=50)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY is required")
    if args.once:
        print(json.dumps(asyncio.run(run_once(api_key)), sort_keys=True, default=str))
    else:
        asyncio.run(supervise(api_key, args.interval_seconds, max(0, args.quota_floor)))


if __name__ == "__main__":
    main()
