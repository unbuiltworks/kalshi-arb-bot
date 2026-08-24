"""Request-efficient wrapper for the strict read-only NFL G/K price scan.

The strict matcher/economics live in ``sports_nfl_gk_price_scan``.  This module
changes only request ordering: price embedded Gemini books first, then request
Kalshi depth only for exact counterpart keys that actually have a usable Gemini
quote.  A shared pacer bounds Kalshi book request starts to reduce HTTP 429s.

No authentication or order-entry path exists here.  Rule certification remains
open and every result stays read-only/non-executable.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any, Mapping

import httpx

import src.sports_nfl_gk_price_scan as core

D = Decimal
logger = logging.getLogger("kalshi_arb.sports_nfl_gk_price_scan_efficient")
DEFAULT_KALSHI_BOOK_RPS = D("10")


class _StartPacer:
    def __init__(self, rps: D) -> None:
        if rps <= 0:
            raise ValueError("rps must be positive")
        self._interval = 1.0 / float(rps)
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._next = max(now, self._next) + self._interval


def _rps() -> D:
    raw = os.getenv("SPORTS_NFL_KALSHI_BOOK_RPS", str(DEFAULT_KALSHI_BOOK_RPS)).strip()
    try:
        value = D(raw)
    except Exception as exc:
        raise ValueError("SPORTS_NFL_KALSHI_BOOK_RPS must be numeric") from exc
    if value < D("1") or value > D("20"):
        raise ValueError("SPORTS_NFL_KALSHI_BOOK_RPS must be between 1 and 20")
    return value


def _kalshi_rows_for_priced_keys(
    kalshi_by_key: Mapping[str, list[core.KalshiCandidate]],
    priced_keys: set[str],
) -> list[core.KalshiCandidate]:
    return [row for key in sorted(priced_keys) for row in kalshi_by_key.get(key, [])]


async def run_once() -> dict[str, Any]:
    fetch_health: Counter[str] = Counter()
    started = time.monotonic()
    rps = _rps()

    async with httpx.AsyncClient(timeout=12.0) as http:
        gbody, gemini_received = await core._get(
            http,
            f"{core.GEMINI}/v1/prediction-markets/events",
            fetch_health,
            params={"status": "active", "sport": "american_football", "limit": 500, "offset": 0},
        )
        gemini_events = [
            dict(x)
            for x in ((gbody or {}).get("data") or (gbody or {}).get("events") or [])
            if isinstance(x, Mapping)
        ]
        gemini_rows, gcontracts = core._gemini_rows(gemini_events)
        gindex = core._gemini_match_index(gemini_rows)

        events_by_series: dict[str, list[dict[str, Any]]] = {}
        for series in core.SERIES_FAMILY:
            body, _ = await core._get(
                http,
                f"{core.KALSHI}/events",
                fetch_health,
                params={
                    "limit": 200,
                    "status": "open",
                    "series_ticker": series,
                    "with_nested_markets": "true",
                },
            )
            events_by_series[series] = [
                dict(x) for x in ((body or {}).get("events") or []) if isinstance(x, Mapping)
            ]
            await asyncio.sleep(0.30)

        kalshi_rows, match_stats = core._kalshi_rows(events_by_series, gindex)
        gemini_by_key: dict[str, list[Any]] = defaultdict(list)
        kalshi_by_key: dict[str, list[core.KalshiCandidate]] = defaultdict(list)
        for row in gemini_rows:
            gemini_by_key[row.contract_key].append(row)
        for row in kalshi_rows:
            kalshi_by_key[row.side.contract_key].append(row)
        keys = sorted(set(gemini_by_key) & set(kalshi_by_key))
        wanted_g = [row for key in keys for row in gemini_by_key[key]]

        # High-leverage change: embedded Gemini books are already in the event
        # response.  Do not spend a Kalshi orderbook request on a counterpart key
        # unless at least one exact Gemini side has a usable priced quote now.
        priced: list[Any] = []
        gemini_priced_keys: set[str] = set()
        for row in wanted_g:
            book = core.gemini_contract_book(gcontracts.get(row.venue_market_id, {}))
            if book is None:
                continue
            priced_row = core.make_priced_quote(
                row,
                book,
                received_ms=gemini_received,
                depth_source="gemini_active_event_best_bid_ask_q1",
                category="sports_nfl_game",
            )
            if priced_row is not None:
                priced.append(priced_row)
                gemini_priced_keys.add(row.contract_key)

        wanted_k = _kalshi_rows_for_priced_keys(kalshi_by_key, gemini_priced_keys)

        fee_multiplier: dict[str, D] = {}
        for series in core.SERIES_FAMILY:
            body, _ = await core._get(http, f"{core.KALSHI}/series/{series}", fetch_health)
            meta = (body or {}).get("series") if isinstance((body or {}).get("series"), Mapping) else {}
            multiplier = core._d(meta.get("fee_multiplier"))
            fee_type = str(meta.get("fee_type") or "")
            if (
                multiplier is not None
                and D("0") <= multiplier <= D("100")
                and fee_type in {"quadratic", "quadratic_with_maker_fees"}
            ):
                fee_multiplier[series] = multiplier

        sem = asyncio.Semaphore(3)
        pacer = _StartPacer(rps)
        book_health: Counter[str] = Counter()

        async def one(candidate: core.KalshiCandidate):
            async with sem:
                await pacer.wait()
                body, received = await core._get(
                    http,
                    f"{core.KALSHI}/markets/{candidate.side.venue_market_id}/orderbook",
                    book_health,
                    params={"depth": 1},
                )
                return candidate, body, received

        book_results = await asyncio.gather(*(one(row) for row in wanted_k))

    kalshi_priced = 0
    for candidate, body, received in book_results:
        book = core.kalshi_top_book(body or {}) if body is not None else None
        if book is None:
            book_health["unparseable_book"] += 1
            continue
        priced_row = core.make_priced_quote(
            candidate.side,
            book,
            received_ms=received,
            kalshi_fee_multiplier=fee_multiplier.get(candidate.series),
            depth_source="kalshi_public_depth1",
            category="sports_nfl_game",
        )
        if priced_row is not None:
            priced.append(priced_row)
            kalshi_priced += 1

    positive = core.find_cross_venue_locks(
        [row.quote for row in priced],
        min_unit_profit=D("0"),
        max_quote_skew_ms=core.MAX_QUOTE_SKEW_MS,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    )
    floor = [row for row in positive if row.unit_profit >= core.MIN_FLOOR]
    by_family: Counter[str] = Counter()
    key_identity: dict[str, Any] = {}
    for row in wanted_g:
        key_identity.setdefault(row.contract_key, row.identity)
    for key in keys:
        ident = key_identity.get(key)
        if ident is not None:
            by_family[ident.market_family] += 1

    skipped = max(0, sum(len(kalshi_by_key[key]) for key in keys) - len(wanted_k))
    result = {
        "status": "SPORTS_NFL_GK_Q1_PRICE_SCAN",
        "scanner_variant": "GEMINI_FIRST_KALSHI_REQUEST_FILTER_V1",
        "gemini_football_events": len(gemini_events),
        "gemini_nfl_normalized_contracts": len(gemini_rows),
        "kalshi_events": {k: len(v) for k, v in events_by_series.items()},
        "kalshi_normalized_candidates": len(kalshi_rows),
        "match_stats": dict(match_stats),
        "exact_counterpart_keys": len(keys),
        "exact_counterpart_keys_by_family": dict(by_family),
        "gemini_priced_counterpart_keys": len(gemini_priced_keys),
        "priced_quotes": len(priced),
        "priced_quotes_by_venue": dict(Counter(row.quote.venue for row in priced)),
        "kalshi_books_requested": len(wanted_k),
        "kalshi_books_skipped_no_gemini_book": skipped,
        "kalshi_books_priced": kalshi_priced,
        "kalshi_book_request_rps_limit": str(rps),
        "fetch_health": dict(fetch_health),
        "book_health": dict(book_health),
        "positive_q1_locks": len(positive),
        "floor_3c_q1_locks": len(floor),
        "top_positive_locks": [row.as_dict() for row in positive[:30]],
        "top_floor_3c_locks": [row.as_dict() for row in floor[:30]],
        "quote_skew_limit_ms": core.MAX_QUOTE_SKEW_MS,
        "identity_confidence": "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
        "rule_gate": "NFL_GEMINI_KALSHI_FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
        "cycle_ms": round((time.monotonic() - started) * 1000, 3),
    }
    logger.warning("SPORTS_NFL_GK_Q1_PRICE_SCAN %s", json.dumps(result, separators=(",", ":"), default=str))
    return result
