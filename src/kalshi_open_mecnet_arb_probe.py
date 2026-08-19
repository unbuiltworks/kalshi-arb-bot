"""Read-only all-market Kalshi mutually-exclusive collateral arb probe.

Scans open events explicitly marked mutually exclusive with collateral return type
MECNET.  For any k distinct NO positions in such an event, at most one can lose,
so the settlement payout is at least k-1 dollars.  The probe chooses the cheapest
fee-inclusive NO positions for each basket size and reports only public data.

No authentication, account state, order transport, or write endpoint is used.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.three_venue_mlb_snapshot_pricing import kalshi_nested_top_book
from src.universal_three_venue_sports import KALSHI

D = Decimal


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


async def _get_json(http: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    response = await http.get(url, **kwargs)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


async def _open_events(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = ""
    # The live Events endpoint currently accepts 200 with nested markets; 1000
    # returns HTTP 400 even though other list endpoints support larger pages.
    for _ in range(50):
        params: dict[str, Any] = {
            "limit": 200,
            "status": "open",
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor
        body = await _get_json(http, f"{KALSHI}/events", params=params)
        rows = [dict(row) for row in body.get("events") or [] if isinstance(row, Mapping)]
        out.extend(rows)
        cursor = str(body.get("cursor") or "")
        if not cursor:
            break
    return out


async def _series_fee_metadata(http: httpx.AsyncClient, series: list[str]) -> tuple[dict[str, tuple[str, Decimal]], list[dict[str, str]]]:
    sem = asyncio.Semaphore(12)
    errors: list[dict[str, str]] = []

    async def one(ticker: str) -> tuple[str, tuple[str, Decimal] | None]:
        async with sem:
            try:
                body = await _get_json(http, f"{KALSHI}/series/{ticker}")
                row = body.get("series") or {}
                fee_type = str(row.get("fee_type") or "").strip()
                multiplier = _d(row.get("fee_multiplier"))
                if fee_type not in {"quadratic", "quadratic_with_maker_fees"} or multiplier is None or multiplier < 0:
                    raise ValueError(f"unmapped_fee:{fee_type}:{multiplier}")
                return ticker, (fee_type, multiplier)
            except Exception as exc:
                errors.append({"series": ticker, "error": f"{type(exc).__name__}:{str(exc)[:160]}"})
                return ticker, None

    pairs = await asyncio.gather(*(one(ticker) for ticker in series))
    return {ticker: meta for ticker, meta in pairs if meta is not None}, errors


def _event_multiplier(event: Mapping[str, Any], series_meta: tuple[str, Decimal]) -> tuple[str, Decimal] | None:
    series_type, series_multiplier = series_meta
    override_type = str(event.get("fee_type_override") or "").strip()
    raw_override_multiplier = event.get("fee_multiplier_override")
    fee_type = override_type or series_type
    multiplier = _d(raw_override_multiplier) if raw_override_multiplier is not None else series_multiplier
    if fee_type not in {"quadratic", "quadratic_with_maker_fees"} or multiplier is None or multiplier < 0:
        return None
    return fee_type, multiplier


def _safe_leg(market: Mapping[str, Any], all_in_no: Decimal, no_fee: Decimal, no_ask: Decimal) -> dict[str, Any]:
    return {
        "ticker": str(market.get("ticker") or ""),
        "title": str(market.get("title") or market.get("subtitle") or market.get("yes_sub_title") or "")[:160],
        "no_ask": str(no_ask),
        "no_fee_q1": str(no_fee),
        "all_in_no_q1": str(all_in_no),
    }


def _basket_rows(event: Mapping[str, Any], multiplier: Decimal) -> list[dict[str, Any]]:
    legs: list[tuple[Decimal, dict[str, Any]]] = []
    for market in event.get("markets") or []:
        if not isinstance(market, Mapping):
            continue
        book = kalshi_nested_top_book(market)
        if book is None or book.no_depth < D("1"):
            continue
        fee = kalshi_quadratic_taker_fee(book.no_ask, D("1"), fee_multiplier=multiplier)
        all_in = book.no_ask + fee
        legs.append((all_in, _safe_leg(market, all_in, fee, book.no_ask)))
    legs.sort(key=lambda item: (item[0], item[1]["ticker"]))
    if len(legs) < 2:
        return []

    out: list[dict[str, Any]] = []
    cumulative = D("0")
    chosen: list[dict[str, Any]] = []
    for index, (cost, leg) in enumerate(legs, start=1):
        cumulative += cost
        chosen.append(leg)
        if index < 2:
            continue
        guaranteed = D(index - 1)
        profit = guaranteed - cumulative
        out.append({
            "event_ticker": str(event.get("event_ticker") or ""),
            "series": str(event.get("series_ticker") or ""),
            "category": str(event.get("category") or ""),
            "event_title": str(event.get("title") or "")[:180],
            "collateral_return_type": str(event.get("collateral_return_type") or ""),
            "basket_legs": index,
            "guaranteed_payout": str(guaranteed),
            "all_in_cost": str(cumulative),
            "locked_profit": str(profit),
            "legs": list(chosen),
        })
    return out


async def probe() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=45.0) as http:
        events = await _open_events(http)
        eligible = [
            event for event in events
            if bool(event.get("mutually_exclusive"))
            and str(event.get("collateral_return_type") or "").strip().upper() == "MECNET"
            and len(event.get("markets") or []) >= 2
        ]
        series = sorted({str(event.get("series_ticker") or "") for event in eligible if event.get("series_ticker")})
        fee_meta, fee_errors = await _series_fee_metadata(http, series)

    candidates: list[dict[str, Any]] = []
    fee_blocked_events = 0
    for event in eligible:
        series_ticker = str(event.get("series_ticker") or "")
        meta = fee_meta.get(series_ticker)
        if meta is None:
            fee_blocked_events += 1
            continue
        resolved = _event_multiplier(event, meta)
        if resolved is None:
            fee_blocked_events += 1
            continue
        _fee_type, multiplier = resolved
        candidates.extend(_basket_rows(event, multiplier))

    candidates.sort(key=lambda row: (-D(row["locked_profit"]), row["basket_legs"], row["event_ticker"]))
    positive = [row for row in candidates if D(row["locked_profit"]) > 0]
    positive_two_leg = [row for row in positive if int(row["basket_legs"]) == 2]
    positive_small = [row for row in positive if int(row["basket_legs"]) <= 3]
    nearest = sorted(candidates, key=lambda row: (D(row["all_in_cost"]) - D(row["guaranteed_payout"]), row["basket_legs"], row["event_ticker"]))
    metadata_counts = Counter(
        f"{str(event.get('category') or '')}|{str(event.get('series_ticker') or '')}"
        for event in eligible
    )
    return {
        "mode": "KALSHI_OPEN_MECNET_ARB_PROBE",
        "open_events_seen": len(events),
        "mecnet_events": len(eligible),
        "mecnet_series": len(series),
        "fee_blocked_events": fee_blocked_events,
        "fee_errors": fee_errors[:30],
        "candidate_basket_sizes_priced": len(candidates),
        "positive_fee_inclusive_baskets": len(positive),
        "positive_two_leg_baskets": len(positive_two_leg),
        "positive_two_or_three_leg_baskets": len(positive_small),
        "top_positive": positive[:25],
        "top_positive_small": positive_small[:25],
        "nearest_to_positive": nearest[:25],
        "eligible_category_series_counts": dict(metadata_counts.most_common(40)),
        "guarantee_basis": "KALSHI_MECNET_AT_MOST_ONE_YES_SO_K_NO_POSITIONS_PAY_AT_LEAST_K_MINUS_ONE",
        "pricing_scope": "Q1_TAKER_FEES_INCLUDED",
        "read_only": True,
        "authenticated": False,
        "order_entry": False,
    }


def main() -> None:
    print(
        "KALSHI_OPEN_MECNET_ARB "
        + json.dumps(asyncio.run(probe()), sort_keys=True, separators=(",", ":"), default=str)
    )


if __name__ == "__main__":
    main()
