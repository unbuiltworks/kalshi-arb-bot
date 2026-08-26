"""Read-only MLB new-listing dislocation census.

Hypothesis: newly listed Kalshi/Polymarket markets can open away from an
independent sharp sportsbook fair before professional liquidity converges.

This collector deliberately counts a dislocation only when all of the following
exist at observation time:
  * a venue-native listing timestamp,
  * an independent Circa fair value,
  * an executable top-of-book ask,
  * positive displayed depth,
  * exact/conservative venue fees.

No empty book, midpoint, indicative price, or stale remembered quote is credited.
No order-entry code is imported or implemented.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any, Iterable, Mapping

import httpx

from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.mlb_game_side_normalization import NormalizedSide, canonical_team, normalize_polymarket_mlb
from src.strict_mlb_prop_snapshot import (
    KALSHI,
    POLY,
    TopBook,
    conservative_kalshi_fee,
    kalshi_top_book,
    polymarket_fee,
    poly_top_book,
)
from src.three_venue_pricing_core import orient_book
from src.universal_three_venue_sports import fetch_kalshi_mlb, fetch_polymarket_mlb

logger = logging.getLogger("kalshi_arb.new_listing_dislocation_shadow")
D = Decimal
ONE = D("1")
ODDS_EVENTS = "https://api.odds-api.io/v3/events"
ODDS_MULTI = "https://api.odds-api.io/v3/odds/multi"
Q_LEVELS = (1, 5, 10, 25, 50)


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _dt_ms(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 10_000_000_000:
            return int(v)
        if v > 1_000_000_000:
            return int(v * 1000)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None
    return int(dt.timestamp() * 1000)


def _listing_ms(market: Mapping[str, Any], event: Mapping[str, Any], venue: str) -> tuple[int | None, str | None]:
    market_fields = (
        ("open_time", "market.open_time"),
        ("openTime", "market.openTime"),
        ("createdAt", "market.createdAt"),
        ("created_at", "market.created_at"),
        ("creationTime", "market.creationTime"),
        ("created_time", "market.created_time"),
    )
    event_fields = (
        ("open_time", "event.open_time"),
        ("openTime", "event.openTime"),
        ("createdAt", "event.createdAt"),
        ("created_at", "event.created_at"),
        ("creationTime", "event.creationTime"),
        ("created_time", "event.created_time"),
    )
    preferred = market_fields if venue in {"kalshi", "polymarket"} else market_fields
    for key, label in preferred:
        value = _dt_ms(market.get(key))
        if value is not None:
            return value, label
    for key, label in event_fields:
        value = _dt_ms(event.get(key))
        if value is not None:
            return value, label
    return None, None


def _age_bucket(age_ms: int) -> str | None:
    if age_ms < 0:
        return None
    minute = 60_000
    if age_ms < minute:
        return "0-1m"
    if age_ms < 5 * minute:
        return "1-5m"
    if age_ms < 15 * minute:
        return "5-15m"
    if age_ms < 60 * minute:
        return "15-60m"
    if age_ms < 24 * 60 * minute:
        return "1-24h"
    return None


def _teams(values: Iterable[Any]) -> tuple[str, str] | None:
    found: list[str] = []
    for value in values:
        team = canonical_team(value)
        if team and team not in found:
            found.append(team)
    return tuple(sorted(found[:2])) if len(found) >= 2 else None


def _side_teams(side: NormalizedSide) -> tuple[str, str]:
    return tuple(sorted(str(x).upper() for x in side.identity.event.participants))  # type: ignore[return-value]


def _semantic(side: NormalizedSide) -> tuple[str, str, str, str]:
    ident = side.identity
    subject = ident.subject.upper()
    if ident.market_family == "game_total":
        subject = "CONTEST"
    line = "" if ident.line is None else format(ident.line.normalize(), "f")
    return (ident.market_family, subject, ident.comparator, line)


def _fair_key(family: str, subject: str, comparator: str, line: Decimal | None) -> tuple[str, str, str, str]:
    token = "" if line is None else format(line.normalize(), "f")
    return (family, subject.upper(), comparator, token)


def _prob(decimal_odds: Any) -> Decimal | None:
    value = _d(decimal_odds)
    if value is None or value <= ONE:
        return None
    return ONE / value


def _devig_two(a: Decimal | None, b: Decimal | None) -> tuple[Decimal, Decimal] | None:
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    total = a + b
    if total <= 0:
        return None
    return a / total, b / total


def _market_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"ml", "moneyline", "match winner", "h2h"}:
        return "ML"
    if text in {"spread", "spreads", "handicap"}:
        return "Spread"
    if text in {"total", "totals", "over/under"}:
        return "Totals"
    return str(value or "")


def _circa_fairs(event: Mapping[str, Any]) -> dict[tuple[str, str, str, str], Decimal]:
    home = canonical_team(event.get("home"))
    away = canonical_team(event.get("away"))
    if not home or not away or home == away:
        return {}
    books = event.get("bookmakers")
    if not isinstance(books, Mapping):
        return {}
    raw_markets = None
    for raw_book, markets in books.items():
        if "circa" in "".join(ch for ch in str(raw_book).lower() if ch.isalnum()):
            raw_markets = markets
            break
    if not isinstance(raw_markets, list):
        return {}
    out: dict[tuple[str, str, str, str], Decimal] = {}
    for market in raw_markets:
        if not isinstance(market, Mapping):
            continue
        name = _market_name(market.get("name"))
        odds_rows = market.get("odds")
        if not isinstance(odds_rows, list):
            continue
        if name == "ML":
            for row in odds_rows:
                if not isinstance(row, Mapping):
                    continue
                pair = _devig_two(_prob(row.get("home")), _prob(row.get("away")))
                if pair is None:
                    continue
                ph, pa = pair
                out[_fair_key("moneyline", home, "YES", None)] = ph
                out[_fair_key("moneyline", away, "YES", None)] = pa
                break
        elif name == "Spread":
            for row in odds_rows:
                if not isinstance(row, Mapping):
                    continue
                point = _d(row.get("hdp"))
                pair = _devig_two(_prob(row.get("home")), _prob(row.get("away")))
                if point is None or pair is None:
                    continue
                ph, pa = pair
                out[_fair_key("spread", home, "GT", -point)] = ph
                out[_fair_key("spread", away, "GT", point)] = pa
        elif name == "Totals":
            for row in odds_rows:
                if not isinstance(row, Mapping):
                    continue
                point = _d(row.get("hdp"))
                pair = _devig_two(_prob(row.get("over")), _prob(row.get("under")))
                if point is None or pair is None:
                    continue
                over, under = pair
                point = abs(point)
                out[_fair_key("game_total", "CONTEST", "GT", point)] = over
                out[_fair_key("game_total", "CONTEST", "LT", point)] = under
    return out


async def _json(http: httpx.AsyncClient, url: str, **kwargs: Any) -> tuple[Any, httpx.Response]:
    response = await http.get(url, **kwargs)
    response.raise_for_status()
    return response.json(), response


async def _circa_reference(
    http: httpx.AsyncClient,
    api_key: str,
    wanted_teams: set[tuple[str, str]],
) -> tuple[dict[tuple[str, str], list[tuple[int, dict[tuple[str, str, str, str], Decimal]]]], dict[str, Any]]:
    body, event_response = await _json(http, ODDS_EVENTS, params={
        "apiKey": api_key,
        "sport": "baseball",
        "league": "usa-mlb",
        "status": "pending,live",
    })
    events = [x for x in body if isinstance(x, Mapping)] if isinstance(body, list) else []
    candidates: list[Mapping[str, Any]] = []
    for event in events:
        teams = _teams((event.get("home"), event.get("away")))
        if teams in wanted_teams and event.get("id"):
            candidates.append(event)
    event_ids = [str(x.get("id")) for x in candidates]
    returned: list[Mapping[str, Any]] = []
    remaining = event_response.headers.get("x-ratelimit-remaining", "")
    for i in range(0, len(event_ids), 10):
        ids = event_ids[i:i + 10]
        body, response = await _json(http, ODDS_MULTI, params={
            "apiKey": api_key,
            "eventIds": ",".join(ids),
            "bookmakers": "Circa",
        })
        remaining = response.headers.get("x-ratelimit-remaining", remaining)
        if isinstance(body, list):
            returned.extend(x for x in body if isinstance(x, Mapping))
    out: dict[tuple[str, str], list[tuple[int, dict[tuple[str, str, str, str], Decimal]]]] = defaultdict(list)
    for event in returned:
        teams = _teams((event.get("home"), event.get("away")))
        start = _dt_ms(event.get("date"))
        fairs = _circa_fairs(event)
        if teams is None or start is None or not fairs:
            continue
        out[teams].append((start, fairs))
    for rows in out.values():
        rows.sort(key=lambda x: x[0])
    return dict(out), {
        "odds_events": len(events),
        "matched_odds_events": len(candidates),
        "circa_events_returned": len(returned),
        "rate_remaining": remaining,
    }


def _select_fairs(
    side: NormalizedSide,
    fair_events: dict[tuple[str, str], list[tuple[int, dict[tuple[str, str, str, str], Decimal]]]],
) -> tuple[dict[tuple[str, str, str, str], Decimal] | None, str]:
    rows = fair_events.get(_side_teams(side), [])
    if not rows:
        return None, "NO_CIRCA_EVENT"
    target = side.identity.event.scheduled_start_ms
    near = [(abs(start - target), fairs) for start, fairs in rows if abs(start - target) <= 18 * 3600 * 1000]
    if not near:
        return None, "NO_CIRCA_EVENT_WITHIN_18H"
    near.sort(key=lambda x: x[0])
    # Multiple same-team games in the same day are unsafe to infer through a
    # known timestamp-normalization investigation. Fail closed on doubleheaders.
    if len(near) > 1:
        return None, "AMBIGUOUS_SAME_TEAM_MULTI_EVENT"
    return near[0][1], "MATCHED_UNIQUE_EVENT"


async def _top_books(
    http: httpx.AsyncClient,
    poly_ids: set[str],
    kalshi_ids: set[str],
) -> tuple[dict[str, TopBook], dict[str, TopBook]]:
    sem = asyncio.Semaphore(20)

    async def poly_one(market_id: str) -> tuple[str, TopBook | None]:
        async with sem:
            try:
                body, _ = await _json(http, f"{POLY}/v1/markets/{market_id}/book")
            except Exception:
                return market_id, None
            return market_id, poly_top_book(body if isinstance(body, dict) else {})

    async def kalshi_one(ticker: str) -> tuple[str, TopBook | None]:
        async with sem:
            try:
                body, _ = await _json(http, f"{KALSHI}/markets/{ticker}/orderbook", params={"depth": 1})
            except Exception:
                return ticker, None
            return ticker, kalshi_top_book(body if isinstance(body, dict) else {})

    p_rows, k_rows = await asyncio.gather(
        asyncio.gather(*(poly_one(x) for x in sorted(poly_ids))),
        asyncio.gather(*(kalshi_one(x) for x in sorted(kalshi_ids))),
    )
    return (
        {key: book for key, book in p_rows if book is not None},
        {key: book for key, book in k_rows if book is not None},
    )


async def _kalshi_multipliers(http: httpx.AsyncClient, series: set[str]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for ticker in sorted(x for x in series if x):
        try:
            body, _ = await _json(http, f"{KALSHI}/series/{ticker}")
        except Exception:
            continue
        row = body.get("series") if isinstance(body, Mapping) and isinstance(body.get("series"), Mapping) else {}
        multiplier = _d(row.get("fee_multiplier"))
        fee_type = str(row.get("fee_type") or "")
        if multiplier is not None and fee_type in {"quadratic", "quadratic_with_maker_fees"}:
            out[ticker] = multiplier
    return out


def _q_curve(
    venue: str,
    price: Decimal,
    depth: Decimal,
    fair: Decimal,
    multiplier: Decimal | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for q in Q_LEVELS:
        if depth < D(q):
            out[str(q)] = {"available": False, "displayed_depth": str(depth)}
            continue
        if venue == "polymarket":
            fee = polymarket_fee(q, price)
        elif venue == "kalshi" and multiplier is not None:
            fee = conservative_kalshi_fee(q, price, multiplier)
        else:
            out[str(q)] = {"available": False, "reason": "FEE_METADATA_MISSING"}
            continue
        avg_cost = price + fee / D(q)
        edge = fair - avg_cost
        out[str(q)] = {
            "available": True,
            "price": str(price),
            "fee_total": str(fee),
            "all_in_avg_cost": str(avg_cost),
            "fair_probability": str(fair),
            "edge_per_contract": str(edge),
            "modeled_total_ev": str(edge * D(q)),
            "displayed_depth": str(depth),
        }
    return out


def _best_side(
    venue: str,
    side: NormalizedSide,
    book: TopBook,
    fair_yes: Decimal,
    multiplier: Decimal | None,
) -> dict[str, Any] | None:
    oriented = orient_book(book, side.side)
    candidates: list[dict[str, Any]] = []
    for direction, price, depth, fair in (
        ("YES", oriented.yes_ask, oriented.yes_depth, fair_yes),
        ("NO", oriented.no_ask, oriented.no_depth, ONE - fair_yes),
    ):
        if depth <= 0:
            continue
        curve = _q_curve(venue, price, depth, fair, multiplier)
        q1 = curve.get("1") or {}
        if not q1.get("available"):
            continue
        edge = D(str(q1["edge_per_contract"]))
        candidates.append({
            "direction": direction,
            "raw_executable_price": str(price),
            "displayed_depth": str(depth),
            "fair_probability": str(fair),
            "edge_per_contract_q1": str(edge),
            "quantity_curve": curve,
        })
    if not candidates:
        return None
    return max(candidates, key=lambda row: D(str(row["edge_per_contract_q1"])))


async def observe_once(http: httpx.AsyncClient, api_key: str, state: dict[str, Any]) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    poly_events, kalshi_events = await asyncio.gather(fetch_polymarket_mlb(http), fetch_kalshi_mlb(http))
    poly_sides = normalize_polymarket_mlb(poly_events)
    kalshi_sides = normalize_kalshi_mlb_eastern(kalshi_events)

    poly_raw: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for event in poly_events:
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            market_id = str(market.get("slug") or market.get("id") or "")
            if market_id:
                poly_raw[market_id] = (event, market)
    kalshi_raw: dict[str, tuple[Mapping[str, Any], Mapping[str, Any], str]] = {}
    for event in kalshi_events:
        series = str(event.get("series_ticker") or "")
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            ticker = str(market.get("ticker") or "")
            if ticker:
                kalshi_raw[ticker] = (event, market, series)

    recent: list[tuple[str, NormalizedSide, int, str]] = []
    counters: Counter[str] = Counter()
    for venue, sides, raw_map in (
        ("polymarket", poly_sides, poly_raw),
        ("kalshi", kalshi_sides, kalshi_raw),
    ):
        for side in sides:
            raw = raw_map.get(side.venue_market_id)
            if raw is None:
                continue
            event, market = raw[0], raw[1]
            listed_ms, source = _listing_ms(market, event, venue)
            if listed_ms is None or source is None:
                counters[f"{venue}_missing_listing_time"] += 1
                continue
            bucket = _age_bucket(now_ms - listed_ms)
            if bucket is None:
                continue
            recent.append((venue, side, listed_ms, source))

    wanted_teams = {_side_teams(side) for _, side, _, _ in recent}
    fair_events, fair_diag = await _circa_reference(http, api_key, wanted_teams)
    poly_ids = {side.venue_market_id for venue, side, _, _ in recent if venue == "polymarket"}
    kalshi_ids = {side.venue_market_id for venue, side, _, _ in recent if venue == "kalshi"}
    pbooks, kbooks = await _top_books(http, poly_ids, kalshi_ids)
    series_needed = {
        kalshi_raw[x][2] for x in kalshi_ids if x in kalshi_raw and kalshi_raw[x][2]
    }
    multipliers = await _kalshi_multipliers(http, series_needed)

    best_by_market: dict[tuple[str, str, str], dict[str, Any]] = {}
    for venue, side, listed_ms, source in recent:
        bucket = _age_bucket(now_ms - listed_ms)
        if bucket is None:
            continue
        book = pbooks.get(side.venue_market_id) if venue == "polymarket" else kbooks.get(side.venue_market_id)
        if book is None:
            counters[f"{venue}_no_executable_two_sided_book"] += 1
            continue
        fairs, fair_status = _select_fairs(side, fair_events)
        if fairs is None:
            counters[f"fair_{fair_status.lower()}"] += 1
            continue
        fair_yes = fairs.get(_semantic(side))
        if fair_yes is None:
            counters["fair_semantic_missing"] += 1
            continue
        multiplier = None
        if venue == "kalshi":
            raw = kalshi_raw.get(side.venue_market_id)
            multiplier = multipliers.get(raw[2]) if raw is not None else None
            if multiplier is None:
                counters["kalshi_fee_metadata_missing"] += 1
                continue
        best = _best_side(venue, side, book, fair_yes, multiplier)
        if best is None:
            counters[f"{venue}_not_executable_after_fee"] += 1
            continue
        row = {
            "venue": venue,
            "market_id": side.venue_market_id,
            "listing_ms": listed_ms,
            "listing_time_source": source,
            "listing_age_ms": now_ms - listed_ms,
            "listing_age_bucket": bucket,
            "market_family": side.identity.market_family,
            "subject": side.identity.subject,
            "comparator": side.identity.comparator,
            "line": None if side.identity.line is None else str(side.identity.line),
            "scheduled_start_ms": side.identity.event.scheduled_start_ms,
            "circa_match_status": fair_status,
            **best,
            "execution_requirement": "REAL_TOP_OF_BOOK_AND_POSITIVE_DISPLAYED_DEPTH",
            "read_only": True,
            "order_entry": False,
        }
        key = (venue, side.venue_market_id, bucket)
        prior = best_by_market.get(key)
        if prior is None or D(str(row["edge_per_contract_q1"])) > D(str(prior["edge_per_contract_q1"])):
            best_by_market[key] = row

    maxima: dict[str, Decimal] = state.setdefault("max_edge", {})
    unique_markets: set[str] = state.setdefault("unique_markets", set())
    bucket_counts: Counter[str] = state.setdefault("bucket_counts", Counter())
    edge_bins: Counter[str] = state.setdefault("edge_bins", Counter())
    for key, row in best_by_market.items():
        key_token = "|".join(key)
        edge = D(str(row["edge_per_contract_q1"]))
        unique_markets.add(f"{row['venue']}|{row['market_id']}")
        bucket_counts[str(row["listing_age_bucket"])] += 1
        maxima[key_token] = max(edge, maxima.get(key_token, D("-1")))
        if edge >= D("0.01"):
            edge_bins[">=1c"] += 1
        if edge >= D("0.02"):
            edge_bins[">=2c"] += 1
        if edge >= D("0.03"):
            edge_bins[">=3c"] += 1
        logger.info("NEW_LISTING_EXECUTABLE_OBSERVATION %s", json.dumps(row, separators=(",", ":"), default=str))

    summary = {
        "status": "NEW_LISTING_DISLOCATION_SHADOW",
        "poll_ts_ms": now_ms,
        "raw_events": {"polymarket": len(poly_events), "kalshi": len(kalshi_events)},
        "recent_normalized_sides": len(recent),
        "recent_unique_markets": len({(v, s.venue_market_id) for v, s, _, _ in recent}),
        "executable_market_bucket_observations_this_poll": len(best_by_market),
        "unique_executable_markets_since_start": len(unique_markets),
        "bucket_observation_counts_since_start": dict(bucket_counts),
        "edge_bin_observation_counts_since_start": dict(edge_bins),
        "diagnostics_this_poll": dict(counters),
        "circa": fair_diag,
        "dislocation_definition": "EXECUTABLE_TOP_BOOK_WITH_POSITIVE_DEPTH_VS_DEVIGGED_CIRCA_FAIR_AFTER_FEES",
        "empty_books_count_as_edge": False,
        "capacity_status": "MEASUREMENT_ONLY_NOT_YET_DECISION_GRADE",
        "read_only": True,
        "order_entry": False,
    }
    logger.info("NEW_LISTING_DISLOCATION_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))
    return summary


async def run(args: argparse.Namespace) -> None:
    api_key = os.getenv("ODDS_API_IO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ODDS_API_IO_API_KEY is required")
    state: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=20.0) as http:
        while True:
            started = time.monotonic()
            try:
                await observe_once(http, api_key, state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("NEW_LISTING_DISLOCATION_ERROR type=%s", type(exc).__name__)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(1.0, args.poll_seconds - elapsed))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    if args.poll_seconds < 15:
        raise SystemExit("poll-seconds must be >=15")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
