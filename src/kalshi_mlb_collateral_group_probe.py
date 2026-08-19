"""Read-only Kalshi MLB collateral-return arbitrage probe.

Kalshi documents guaranteed collateral for related markets inside one event:
- mutually exclusive groups: buying NO in two distinct markets guarantees at
  least one $1 payout because at most one YES can occur;
- directional groups: a lower-threshold YES plus a higher-threshold NO guarantees
  at least one $1 payout.

This probe measures q=1 fee-inclusive costs using only public event/series data.
It imports no order transport and never authenticates.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from decimal import Decimal
from itertools import combinations
import json
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.three_venue_mlb_price_scan import _kalshi_series
from src.three_venue_mlb_snapshot_pricing import (
    _kalshi_markets,
    _series_multipliers,
    kalshi_nested_top_book,
)
from src.universal_three_venue_sports import fetch_kalshi_mlb

D = Decimal


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _q1_fee(price: Decimal, multiplier: Decimal) -> Decimal:
    return kalshi_quadratic_taker_fee(price, D("1"), fee_multiplier=multiplier)


def _market_row(market: Mapping[str, Any], book: Any, multiplier: Decimal) -> dict[str, Any]:
    ticker = str(market.get("ticker") or "")
    return {
        "ticker": ticker,
        "title": str(market.get("title") or market.get("subtitle") or ticker)[:180],
        "yes_ask": book.yes_ask,
        "yes_depth": book.yes_depth,
        "no_ask": book.no_ask,
        "no_depth": book.no_depth,
        "yes_fee": _q1_fee(book.yes_ask, multiplier),
        "no_fee": _q1_fee(book.no_ask, multiplier),
        "strike_type": str(market.get("strike_type") or "").lower(),
        "floor_strike": _d(market.get("floor_strike")),
        "cap_strike": _d(market.get("cap_strike")),
        "primary_participant_key": str(market.get("primary_participant_key") or ""),
    }


def _public_candidate(kind: str, event: Mapping[str, Any], legs: list[dict[str, Any]], cost: Decimal, payout: Decimal) -> dict[str, Any]:
    return {
        "kind": kind,
        "event_ticker": str(event.get("event_ticker") or ""),
        "series": str(event.get("series_ticker") or ""),
        "event_title": str(event.get("title") or "")[:180],
        "collateral_return_type": str(event.get("collateral_return_type") or ""),
        "mutually_exclusive": bool(event.get("mutually_exclusive")),
        "legs": legs,
        "guaranteed_payout": str(payout),
        "all_in_cost": str(cost),
        "locked_profit": str(payout - cost),
    }


def _mutually_exclusive_candidates(event: Mapping[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not bool(event.get("mutually_exclusive")):
        return []
    out: list[dict[str, Any]] = []
    for left, right in combinations(rows, 2):
        if left["no_depth"] < D("1") or right["no_depth"] < D("1"):
            continue
        cost = left["no_ask"] + left["no_fee"] + right["no_ask"] + right["no_fee"]
        out.append(_public_candidate(
            "MUTUALLY_EXCLUSIVE_TWO_NO",
            event,
            [
                {"ticker": left["ticker"], "side": "NO", "ask": str(left["no_ask"]), "fee": str(left["no_fee"])},
                {"ticker": right["ticker"], "side": "NO", "ask": str(right["no_ask"]), "fee": str(right["no_fee"])},
            ],
            cost,
            D("1"),
        ))
    return out


def _directional_total_candidates(event: Mapping[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conservative directional scan: only MLB TOTAL events with numeric greater strikes."""
    if str(event.get("series_ticker") or "") != "KXMLBTOTAL":
        return []
    collateral = str(event.get("collateral_return_type") or "").lower()
    if "direction" not in collateral:
        return []
    eligible = [row for row in rows if row["strike_type"] == "greater" and row["floor_strike"] is not None]
    eligible.sort(key=lambda row: (row["floor_strike"], row["ticker"]))
    out: list[dict[str, Any]] = []
    for lower, higher in combinations(eligible, 2):
        if lower["floor_strike"] >= higher["floor_strike"]:
            continue
        if lower["yes_depth"] < D("1") or higher["no_depth"] < D("1"):
            continue
        cost = lower["yes_ask"] + lower["yes_fee"] + higher["no_ask"] + higher["no_fee"]
        out.append(_public_candidate(
            "DIRECTIONAL_LOWER_YES_HIGHER_NO",
            event,
            [
                {"ticker": lower["ticker"], "side": "YES", "strike": str(lower["floor_strike"]), "ask": str(lower["yes_ask"]), "fee": str(lower["yes_fee"])},
                {"ticker": higher["ticker"], "side": "NO", "strike": str(higher["floor_strike"]), "ask": str(higher["no_ask"]), "fee": str(higher["no_fee"])},
            ],
            cost,
            D("1"),
        ))
    return out


async def probe() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        events = await fetch_kalshi_mlb(http)
        market_to_series = _kalshi_series(events)
        series = sorted({value for value in market_to_series.values() if value})
        errors: list[dict[str, str]] = []
        multipliers = await _series_multipliers(http, asyncio.Semaphore(8), series, errors)

    metadata_counts: Counter[str] = Counter()
    mutually_exclusive_events = 0
    all_candidates: list[dict[str, Any]] = []
    events_priced = 0
    markets_priced = 0
    for event in events:
        series_ticker = str(event.get("series_ticker") or "")
        collateral = str(event.get("collateral_return_type") or "") or "NONE"
        metadata_counts[f"{series_ticker}|{collateral}|mutual={bool(event.get('mutually_exclusive'))}"] += 1
        if bool(event.get("mutually_exclusive")):
            mutually_exclusive_events += 1
        multiplier = multipliers.get(series_ticker)
        if multiplier is None:
            continue
        rows: list[dict[str, Any]] = []
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            book = kalshi_nested_top_book(market)
            if book is None:
                continue
            rows.append(_market_row(market, book, multiplier))
        if not rows:
            continue
        events_priced += 1
        markets_priced += len(rows)
        all_candidates.extend(_mutually_exclusive_candidates(event, rows))
        all_candidates.extend(_directional_total_candidates(event, rows))

    all_candidates.sort(key=lambda row: (D(row["all_in_cost"]), row["event_ticker"], row["kind"]))
    positive = [row for row in all_candidates if D(row["locked_profit"]) > 0]
    best = all_candidates[0] if all_candidates else None
    return {
        "mode": "KALSHI_MLB_COLLATERAL_GROUP_PROBE",
        "raw_events": len(events),
        "events_priced": events_priced,
        "markets_priced": markets_priced,
        "mutually_exclusive_events": mutually_exclusive_events,
        "event_metadata_counts": dict(sorted(metadata_counts.items())),
        "candidate_structures_priced": len(all_candidates),
        "positive_fee_inclusive_group_arbs": len(positive),
        "best_all_in_cost": None if best is None else best["all_in_cost"],
        "best_locked_profit": None if best is None else best["locked_profit"],
        "positive_rows": positive[:30],
        "nearest_rows": all_candidates[:30],
        "fee_metadata_errors": errors[:20],
        "guarantee_basis": "KALSHI_EVENT_COLLATERAL_RETURN_METADATA_AND_DOCUMENTED_GROUP_LOGIC",
        "scope_note": "directional scan restricted to KXMLBTOTAL greater-strike groups until other rank semantics are separately verified",
        "read_only": True,
        "authenticated": False,
        "order_entry": False,
    }


def main() -> None:
    print(
        "KALSHI_MLB_COLLATERAL_GROUP "
        + json.dumps(asyncio.run(probe()), sort_keys=True, separators=(",", ":"), default=str)
    )


if __name__ == "__main__":
    main()
