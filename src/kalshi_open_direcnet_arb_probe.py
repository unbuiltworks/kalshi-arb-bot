"""Read-only fee-inclusive arbitrage probe for open Kalshi DIRECNET events.

Kalshi documents directional collateral as a monotonic hedge: in an event with
ordered "above X" markets, buying YES at a lower threshold and NO at a higher
threshold guarantees at least one $1 payout.  This probe scans only exchange-
marked DIRECNET events and, initially, only market groups whose strike metadata
proves a common numeric monotonic predicate.  Anything ambiguous is inventoried
but never priced as an arb.

No authentication, account state, order transport, or write endpoint is used.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
from itertools import combinations
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
    last: httpx.Response | None = None
    for attempt in range(8):
        response = await http.get(url, **kwargs)
        last = response
        if response.status_code == 429:
            retry = _d(response.headers.get("retry-after"))
            delay = float(retry) if retry is not None and retry > 0 else min(10.0, 0.5 * (2 ** attempt))
            await asyncio.sleep(max(0.25, delay))
            continue
        if 500 <= response.status_code < 600 and attempt < 7:
            await asyncio.sleep(min(5.0, 0.25 * (2 ** attempt)))
            continue
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, Mapping) else {}
    assert last is not None
    last.raise_for_status()
    return {}


async def _open_direcnet_events(http: httpx.AsyncClient) -> tuple[list[dict[str, Any]], bool, int]:
    out: list[dict[str, Any]] = []
    cursor = ""
    exhausted = False
    pages = 0
    for _ in range(150):
        params: dict[str, Any] = {
            "limit": 200,
            "status": "open",
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor
        body = await _get_json(http, f"{KALSHI}/events", params=params)
        pages += 1
        for raw in body.get("events") or []:
            if not isinstance(raw, Mapping):
                continue
            event = dict(raw)
            if str(event.get("collateral_return_type") or "").strip().upper() == "DIRECNET":
                out.append(event)
        cursor = str(body.get("cursor") or "")
        if not cursor:
            exhausted = True
            break
        await asyncio.sleep(0.20)
    return out, exhausted, pages


async def _series_fees(
    http: httpx.AsyncClient, series: list[str]
) -> tuple[dict[str, tuple[str, Decimal]], list[dict[str, str]]]:
    sem = asyncio.Semaphore(4)
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
                errors.append({"series": ticker, "error": f"{type(exc).__name__}:{str(exc)[:180]}"})
                return ticker, None

    pairs = await asyncio.gather(*(one(ticker) for ticker in series))
    return {ticker: meta for ticker, meta in pairs if meta is not None}, errors


def _event_fee(event: Mapping[str, Any], series_meta: tuple[str, Decimal]) -> tuple[str, Decimal] | None:
    series_type, series_multiplier = series_meta
    override_type = str(event.get("fee_type_override") or "").strip()
    override_mult_raw = event.get("fee_multiplier_override")
    fee_type = override_type or series_type
    multiplier = _d(override_mult_raw) if override_mult_raw is not None else series_multiplier
    if fee_type not in {"quadratic", "quadratic_with_maker_fees"} or multiplier is None or multiplier < 0:
        return None
    return fee_type, multiplier


def _strike_signature(market: Mapping[str, Any]) -> tuple[str, Decimal] | None:
    """Return a comparable monotonic predicate and numeric strike, or fail closed.

    The first executable scope is deliberately narrow: `greater` markets with a
    numeric floor strike.  Other strike shapes are inventoried separately until
    their ordering semantics are explicitly audited.
    """
    strike_type = str(market.get("strike_type") or "").strip().lower()
    floor = _d(market.get("floor_strike"))
    if strike_type == "greater" and floor is not None:
        return "greater", floor
    return None


def _safe_leg(market: Mapping[str, Any], *, side: str, strike: Decimal, ask: Decimal, fee: Decimal) -> dict[str, Any]:
    return {
        "ticker": str(market.get("ticker") or ""),
        "title": str(market.get("title") or market.get("subtitle") or market.get("yes_sub_title") or "")[:180],
        "side": side,
        "strike": str(strike),
        "ask": str(ask),
        "taker_fee_q1": str(fee),
    }


def _candidate(
    event: Mapping[str, Any],
    lower_market: Mapping[str, Any],
    lower_strike: Decimal,
    lower_book: Any,
    higher_market: Mapping[str, Any],
    higher_strike: Decimal,
    higher_book: Any,
    multiplier: Decimal,
) -> dict[str, Any]:
    lower_fee = kalshi_quadratic_taker_fee(lower_book.yes_ask, D("1"), fee_multiplier=multiplier)
    higher_fee = kalshi_quadratic_taker_fee(higher_book.no_ask, D("1"), fee_multiplier=multiplier)
    cost = lower_book.yes_ask + lower_fee + higher_book.no_ask + higher_fee
    return {
        "event_ticker": str(event.get("event_ticker") or ""),
        "series": str(event.get("series_ticker") or ""),
        "category": str(event.get("category") or ""),
        "event_title": str(event.get("title") or "")[:180],
        "collateral_return_type": "DIRECNET",
        "predicate": "greater",
        "guaranteed_payout": "1",
        "all_in_cost": str(cost),
        "locked_profit": str(D("1") - cost),
        "legs": [
            _safe_leg(lower_market, side="YES", strike=lower_strike, ask=lower_book.yes_ask, fee=lower_fee),
            _safe_leg(higher_market, side="NO", strike=higher_strike, ask=higher_book.no_ask, fee=higher_fee),
        ],
    }


def _event_candidates(event: Mapping[str, Any], multiplier: Decimal) -> tuple[list[dict[str, Any]], dict[str, int]]:
    morphology: Counter[str] = Counter()
    eligible: list[tuple[Decimal, Mapping[str, Any], Any]] = []
    for raw in event.get("markets") or []:
        if not isinstance(raw, Mapping):
            continue
        strike_type = str(raw.get("strike_type") or "NONE").strip().lower() or "none"
        floor = _d(raw.get("floor_strike"))
        cap = _d(raw.get("cap_strike"))
        morphology[f"strike={strike_type}|floor={floor is not None}|cap={cap is not None}"] += 1
        sig = _strike_signature(raw)
        if sig is None:
            continue
        _predicate, strike = sig
        book = kalshi_nested_top_book(raw)
        if book is None or book.yes_depth < D("1") or book.no_depth < D("1"):
            continue
        eligible.append((strike, raw, book))

    # Duplicate strikes are ambiguous for directional ordering. Drop the event's
    # duplicated strike rows rather than selecting one arbitrarily.
    strike_counts = Counter(strike for strike, _, _ in eligible)
    eligible = [row for row in eligible if strike_counts[row[0]] == 1]
    eligible.sort(key=lambda row: (row[0], str(row[1].get("ticker") or "")))

    out: list[dict[str, Any]] = []
    for (lo_strike, lo_market, lo_book), (hi_strike, hi_market, hi_book) in combinations(eligible, 2):
        if lo_strike >= hi_strike:
            continue
        out.append(_candidate(
            event, lo_market, lo_strike, lo_book,
            hi_market, hi_strike, hi_book, multiplier,
        ))
    return out, dict(morphology)


async def probe() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=45.0) as http:
        events, cursor_exhausted, pages = await _open_direcnet_events(http)
        series = sorted({str(event.get("series_ticker") or "") for event in events if event.get("series_ticker")})
        fee_meta, fee_errors = await _series_fees(http, series)

    candidates: list[dict[str, Any]] = []
    morphology: Counter[str] = Counter()
    eligible_events = 0
    fee_blocked_events = 0
    events_with_comparable_pairs = 0
    category_series: Counter[str] = Counter()

    for event in events:
        series_ticker = str(event.get("series_ticker") or "")
        category_series[f"{str(event.get('category') or '')}|{series_ticker}"] += 1
        meta = fee_meta.get(series_ticker)
        if meta is None:
            fee_blocked_events += 1
            continue
        resolved = _event_fee(event, meta)
        if resolved is None:
            fee_blocked_events += 1
            continue
        eligible_events += 1
        _fee_type, multiplier = resolved
        rows, event_morph = _event_candidates(event, multiplier)
        morphology.update(event_morph)
        if rows:
            events_with_comparable_pairs += 1
            candidates.extend(rows)

    candidates.sort(key=lambda row: (-D(row["locked_profit"]), row["event_ticker"]))
    positive = [row for row in candidates if D(row["locked_profit"]) > 0]
    nearest = sorted(candidates, key=lambda row: (D(row["all_in_cost"]) - D("1"), row["event_ticker"]))

    return {
        "mode": "KALSHI_OPEN_DIRECNET_ARB_PROBE",
        "direcnet_events": len(events),
        "pages_scanned": pages,
        "cursor_exhausted": cursor_exhausted,
        "direcnet_series": len(series),
        "fee_eligible_events": eligible_events,
        "fee_blocked_events": fee_blocked_events,
        "events_with_comparable_greater_pairs": events_with_comparable_pairs,
        "candidate_two_leg_hedges_priced": len(candidates),
        "positive_fee_inclusive_directional_arbs": len(positive),
        "top_positive": positive[:30],
        "nearest_to_positive": nearest[:30],
        "strike_morphology_counts": dict(morphology.most_common(50)),
        "top_category_series": dict(category_series.most_common(50)),
        "fee_errors": fee_errors[:30],
        "guarantee_basis": "KALSHI_DIRECNET_LOWER_GREATER_YES_PLUS_HIGHER_GREATER_NO_PAYS_AT_LEAST_ONE_DOLLAR",
        "pricing_scope": "Q1_TAKER_FEES_INCLUDED_NUMERIC_GREATER_FLOOR_STRIKES_ONLY",
        "read_only": True,
        "authenticated": False,
        "order_entry": False,
    }


def main() -> None:
    print(
        "KALSHI_OPEN_DIRECNET_ARB "
        + json.dumps(asyncio.run(probe()), sort_keys=True, separators=(",", ":"), default=str)
    )


if __name__ == "__main__":
    main()
