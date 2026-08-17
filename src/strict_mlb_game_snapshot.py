"""Read-only cross-venue MLB game-line snapshot.

Discovers exact normal-completion moneyline, half-run spread, and half-run total
predicates across Polymarket US and Kalshi.  The scanner prices public books, applies
conservative fee accounting, and measures top-of-book capacity without ever exposing
an order-entry path.

Important: venue postponement/cancellation policies are not identical.  Every match
therefore remains RULE_EQUIVALENT_NORMAL_COMPLETION_ONLY and execution_eligible=false.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
import logging
from typing import Any, Mapping

import httpx

from src import strict_mlb_prop_snapshot as economics
from src import strict_mlb_prop_snapshot_v4 as capped
from src.strict_mlb_game_adapters import (
    GAME_FAMILY_SERIES,
    GameCandidate,
    _v2_type,
    discover_game_candidates,
)

logger = logging.getLogger("kalshi_arb.strict_mlb_game_snapshot")
POLY = economics.POLY
KALSHI = economics.KALSHI
PRICE_CONCURRENCY = 12
CAPACITY_Q = (1, 2, 5, 10, 25, 50, 100)
RULE_STATUS = "RULE_EQUIVALENT_NORMAL_COMPLETION_ONLY"


async def _fetch_json(http: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    response = await http.get(url, **kwargs)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


async def _optional(http: httpx.AsyncClient, url: str, **kwargs: Any) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return await _fetch_json(http, url, **kwargs), None
    except Exception as exc:
        return None, f"{type(exc).__name__}:{str(exc)[:180]}"


async def fetch_poly_events(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    body = await _fetch_json(
        http,
        f"{POLY}/v2/leagues/mlb/events",
        params={"limit": 1000, "active": "true", "closed": "false"},
    )
    return [dict(row) for row in body.get("events") or [] if isinstance(row, Mapping)]


async def fetch_kalshi_events(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    async def one(series: str) -> list[dict[str, Any]]:
        body = await _fetch_json(
            http,
            f"{KALSHI}/events",
            params={
                "limit": 200,
                "status": "open",
                "series_ticker": series,
                "with_nested_markets": "true",
            },
        )
        return [dict(row) for row in body.get("events") or [] if isinstance(row, Mapping)]

    groups = await asyncio.gather(*(one(series) for series in GAME_FAMILY_SERIES.values()))
    return [row for group in groups for row in group]


def invert_book(book: economics.TopBook) -> economics.TopBook:
    """Express the complement as YES while preserving executable top-level depth."""
    return economics.TopBook(
        yes_ask=book.no_ask,
        yes_depth=book.no_depth,
        no_ask=book.yes_ask,
        no_depth=book.yes_depth,
    )


def exact_q_plan(
    poly: economics.TopBook,
    kalshi: economics.TopBook,
    *,
    kalshi_fee_multiplier: Decimal,
    q: int,
) -> dict[str, Any] | None:
    """Best positive paired plan at exactly q using current displayed top level."""
    if q <= 0:
        return None
    rows: list[dict[str, Any]] = []
    directions = (
        ("POLY_YES_KALSHI_NO", poly.yes_ask, poly.yes_depth, kalshi.no_ask, kalshi.no_depth),
        ("KALSHI_YES_POLY_NO", kalshi.yes_ask, kalshi.yes_depth, poly.no_ask, poly.no_depth),
    )
    for direction, first_px, first_depth, second_px, second_depth in directions:
        if int(min(first_depth, second_depth)) < q:
            continue
        if direction == "POLY_YES_KALSHI_NO":
            poly_px, kalshi_px = first_px, second_px
        else:
            kalshi_px, poly_px = first_px, second_px
        p_fee = economics.polymarket_fee(q, poly_px)
        k_fee = economics.conservative_kalshi_fee(q, kalshi_px, kalshi_fee_multiplier)
        cost = Decimal(q) * (poly_px + kalshi_px) + p_fee + k_fee
        profit = Decimal(q) - cost
        rows.append({
            "direction": direction,
            "quantity": q,
            "poly_price": str(poly_px),
            "kalshi_price": str(kalshi_px),
            "poly_fee": str(p_fee),
            "kalshi_fee_upper_bound": str(k_fee),
            "total_cost_upper_bound": str(cost),
            "conservative_locked_profit": str(profit),
            "conservative_roi": str(profit / cost) if cost > 0 else "0",
            "top_level_depth_sufficient": True,
        })
    positive = [row for row in rows if Decimal(row["conservative_locked_profit"]) > 0]
    return max(positive, key=lambda row: Decimal(row["conservative_locked_profit"])) if positive else None


def capacity_ladder(
    poly: economics.TopBook,
    kalshi: economics.TopBook,
    *,
    kalshi_fee_multiplier: Decimal,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for q in CAPACITY_Q:
        plan = exact_q_plan(poly, kalshi, kalshi_fee_multiplier=kalshi_fee_multiplier, q=q)
        rows.append({"quantity": q, "positive_plan": plan})
    return rows


def _candidate_diag(candidate: GameCandidate) -> dict[str, Any]:
    return candidate.as_dict()


def _poly_shape_sample(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping) or _v2_type(market) not in {"MONEYLINE", "SPREAD", "TOTAL"}:
                continue
            sides = []
            for side in market.get("marketSides") or []:
                if not isinstance(side, Mapping):
                    continue
                team = side.get("team") if isinstance(side.get("team"), Mapping) else {}
                sides.append({
                    "long": side.get("long"),
                    "description": side.get("description"),
                    "identifier": side.get("identifier"),
                    "team_abbreviation": team.get("abbreviation"),
                    "team_name": team.get("name"),
                })
            out.append({
                "event_slug": event.get("slug"),
                "event_title": event.get("title"),
                "event_start": event.get("startTime") or event.get("startDate"),
                "game_id": market.get("gameId"),
                "slug": market.get("slug"),
                "question": market.get("question"),
                "title": market.get("title"),
                "sportsMarketType": market.get("sportsMarketType"),
                "sportsMarketTypeV2": market.get("sportsMarketTypeV2"),
                "line": market.get("line"),
                "marketSides": sides,
            })
            if len(out) >= 9:
                return out
    return out


def _kalshi_shape_sample(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            out.append({
                "series_ticker": event.get("series_ticker"),
                "event_ticker": event.get("event_ticker"),
                "event_title": event.get("title"),
                "event_sub_title": event.get("sub_title"),
                "strike_date": event.get("strike_date"),
                "ticker": market.get("ticker"),
                "title": market.get("title"),
                "subtitle": market.get("subtitle"),
                "yes_sub_title": market.get("yes_sub_title"),
                "no_sub_title": market.get("no_sub_title"),
                "close_time": market.get("close_time"),
            })
            if len(out) >= 9:
                return out
    return out


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        poly_events, kalshi_events = await asyncio.gather(fetch_poly_events(http), fetch_kalshi_events(http))
        candidates = discover_game_candidates(poly_events, kalshi_events)

        series_meta: dict[str, dict[str, Any]] = {}
        for series in sorted(set(GAME_FAMILY_SERIES.values())):
            body, error = await _optional(http, f"{KALSHI}/series/{series}")
            if body is not None and error is None:
                series_meta[series] = dict(body.get("series") or {})

        semaphore = asyncio.Semaphore(PRICE_CONCURRENCY)

        async def price(candidate: GameCandidate) -> dict[str, Any]:
            async with semaphore:
                poly_body, poly_error = await _optional(http, f"{POLY}/v1/markets/{candidate.poly_slug}/book")
                kalshi_body, kalshi_error = await _optional(
                    http,
                    f"{KALSHI}/markets/{candidate.kalshi_ticker}/orderbook",
                    params={"depth": 1},
                )
            base_row: dict[str, Any] = {
                **_candidate_diag(candidate),
                "settlement_equivalent_now": False,
                "execution_eligible": False,
                "read_only": True,
                "order_entry": False,
            }
            if poly_body is None or kalshi_body is None:
                base_row.update({
                    "book_comparable": False,
                    "fetch_error": ";".join(x for x in (poly_error, kalshi_error) if x) or "book_missing",
                    "positive_conservative_plan": None,
                    "probe_1usd": None,
                    "probe_5usd": None,
                    "capacity_ladder": [],
                })
                return base_row

            pbook = economics.poly_top_book(poly_body)
            kbook = economics.kalshi_top_book(kalshi_body)
            if pbook is not None and candidate.poly_invert:
                pbook = invert_book(pbook)
            series = GAME_FAMILY_SERIES[candidate.family]
            meta = series_meta.get(series) or {}
            fee_type = str(meta.get("fee_type") or "")
            multiplier = economics._d(meta.get("fee_multiplier"))
            plan = probe1 = probe5 = None
            ladder: list[dict[str, Any]] = []
            if (
                pbook is not None
                and kbook is not None
                and fee_type in {"quadratic", "quadratic_with_maker_fees"}
                and multiplier is not None
                and Decimal("0") <= multiplier <= Decimal("100")
            ):
                plan = economics.best_conservative_plan(pbook, kbook, kalshi_fee_multiplier=multiplier)
                probe1 = capped.best_capped_plan(pbook, kbook, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("1"))
                probe5 = capped.best_capped_plan(pbook, kbook, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("5"))
                ladder = capacity_ladder(pbook, kbook, kalshi_fee_multiplier=multiplier)

            base_row.update({
                "kalshi_fee_type": fee_type,
                "kalshi_fee_multiplier": str(multiplier) if multiplier is not None else None,
                "book_comparable": pbook is not None and kbook is not None,
                "fetch_error": None,
                "positive_conservative_plan": plan,
                "probe_1usd": probe1,
                "probe_5usd": probe5,
                "capacity_ladder": ladder,
            })
            return base_row

        priced = await asyncio.gather(*(price(candidate) for candidate in candidates)) if candidates else []

    poly_types = Counter()
    for event in poly_events:
        for market in event.get("markets") or []:
            if isinstance(market, Mapping):
                typ = _v2_type(market)
                if typ in {"MONEYLINE", "SPREAD", "TOTAL", "PROP"}:
                    poly_types[typ] += 1
    kalshi_counts = Counter(str(event.get("series_ticker") or "") for event in kalshi_events)
    family_counts = Counter(candidate.family for candidate in candidates)
    positive = [row for row in priced if row.get("positive_conservative_plan")]
    probe1 = [row for row in priced if row.get("probe_1usd")]
    probe5 = [row for row in priced if row.get("probe_5usd")]
    failures = [row for row in priced if row.get("fetch_error")]

    def top(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda row: Decimal(row[field]["conservative_locked_profit"]),
            reverse=True,
        )[:20]

    result = {
        "status": "MLB_GAME_PAPER_SNAPSHOT",
        "poly_active_events": len(poly_events),
        "poly_market_type_counts": dict(poly_types),
        "kalshi_event_counts": dict(kalshi_counts),
        "strict_candidates": len(candidates),
        "strict_candidates_by_family": dict(family_counts),
        "book_comparable": sum(bool(row.get("book_comparable")) for row in priced),
        "fetch_failures": len(failures),
        "conservative_positive_pairs": len(positive),
        "probe_1usd_positive_pairs": len(probe1),
        "probe_5usd_positive_pairs": len(probe5),
        "top_positive_pairs": top(positive, "positive_conservative_plan") if positive else [],
        "top_probe_1usd": top(probe1, "probe_1usd") if probe1 else [],
        "top_probe_5usd": top(probe5, "probe_5usd") if probe5 else [],
        "poly_shape_sample": _poly_shape_sample(poly_events),
        "kalshi_shape_sample": _kalshi_shape_sample(kalshi_events),
        "rule_status": RULE_STATUS,
        "rule_warning": (
            "Normal completed-game predicates are matched strictly, but venue postponement/cancellation windows differ. "
            "No candidate is execution-eligible in this research service."
        ),
        "capacity_note": (
            "capacity_ladder uses displayed top-of-book only; it does not assume liquidity beyond the best level."
        ),
        "execution_eligible_pairs": 0,
        "read_only": True,
        "order_entry": False,
    }
    logger.info("STRICT_MLB_GAME_SNAPSHOT %s", json.dumps(result, separators=(",", ":"), default=str))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    print(json.dumps(asyncio.run(run_once()), sort_keys=True, default=str))


if __name__ == "__main__":
    main()
