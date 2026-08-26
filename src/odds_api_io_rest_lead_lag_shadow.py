"""Read-only Odds-API.io REST lead/lag shadow for MLB mainlines.

Purpose
-------
Measure whether a fast sportsbook reference leads direct Kalshi/Polymarket books
long enough to create executable single-leg EV.  This module intentionally does
not place orders and does not treat Odds-API.io's ``updatedAt`` as bookmaker
source ground truth.

The paid Starter REST limit is 5,000 requests/hour.  We use exactly one
``/v3/odds/multi`` request per poll, batch up to 10 matched events per request,
and rotate batches when more than 10 matched events exist.  The response already
contains every market returned by the selected bookmakers, so ML/Spread/Totals do
not consume separate requests.

A one-time historical probe checks whether Odds-API.io can support the Apr-2026
backtest.  All HTTP errors are logged without request URLs so API keys cannot leak
through exception text.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Any, Mapping

import httpx

from src import odds_api_io_mlb_hot_shadow as base

logger = logging.getLogger("kalshi_arb.odds_api_io_rest_lead_lag_shadow")

REST_MULTI = "https://api.odds-api.io/v3/odds/multi"
REST_HIST_EVENTS = "https://api.odds-api.io/v3/historical/events"
REST_HIST_ODDS = "https://api.odds-api.io/v3/historical/odds"
REST_MOVEMENTS = "https://api.odds-api.io/v3/odds/movements"

DEFAULT_BOOKMAKERS = "Circa,Pinnacle,Betfair Exchange,FanDuel,DraftKings"
DEFAULT_POLL_SECONDS = 1.0
MAX_EVENTS_PER_REQUEST = 10
RATE_LIMIT_SAFETY_FLOOR = 250

# Metadata is kept outside MarketQuote because the universal quote type should not
# inherit provider-specific semantics.  Keys use canonical sportsbook book names.
_quote_meta: dict[tuple[str, str], dict[str, Any]] = {}
_original_retail_payload = base._retail_quote_payload


def _bookmakers() -> list[str]:
    raw = os.getenv("ODDS_API_IO_BOOKMAKERS", DEFAULT_BOOKMAKERS)
    out: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        value = token.strip()
        if value and value.lower() not in seen:
            seen.add(value.lower())
            out.append(value)
    return out[:5]


def _poll_seconds() -> float:
    try:
        value = float(os.getenv("ODDS_API_IO_REST_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))
    except ValueError:
        value = DEFAULT_POLL_SECONDS
    # One request per second = 3,600/hour, leaving ~1,400/hour headroom for
    # discovery and diagnostics on a 5,000/hour account.
    return max(1.0, value)


def _market_name(raw: Any) -> str:
    name = str(raw or "").strip()
    lowered = name.lower()
    if lowered in {"moneyline", "match winner", "h2h"}:
        return "ML"
    if lowered in {"spread", "spreads", "handicap"}:
        return "Spread"
    if lowered in {"total", "totals", "over/under"}:
        return "Totals"
    return name


def _updated_ms(value: Any) -> int | None:
    return base._market_updated_ms(value)


def _event_batches(store: base.OddsIoStore) -> list[list[str]]:
    """Return only events represented in the direct prediction-market universe."""
    eligible_event_keys = {
        "|".join(str(key).split("|")[:3])
        for key in store.eligible_keys
        if len(str(key).split("|")) >= 3
    }
    now_ms = int(time.time() * 1000)
    rows: list[tuple[int, str]] = []
    for event_id, event in store.events.items():
        ident = base._event_identity(event)
        if ident is None or ident.event_key() not in eligible_event_keys:
            continue
        # Keep recently-started games because prediction-market updates may still
        # arrive while they are transitioning out of pregame; keep the next day of
        # scheduled events so the logger is ready before first pitch.
        if ident.scheduled_start_ms < now_ms - 6 * 3600 * 1000:
            continue
        if ident.scheduled_start_ms > now_ms + 30 * 3600 * 1000:
            continue
        rows.append((ident.scheduled_start_ms, str(event_id)))
    rows.sort()
    ids = [event_id for _, event_id in rows]
    return [ids[i:i + MAX_EVENTS_PER_REQUEST] for i in range(0, len(ids), MAX_EVENTS_PER_REQUEST)]


def _safe_rate_headers(response: httpx.Response, store: base.OddsIoStore) -> None:
    store.rest_requests += 1
    store.rest_remaining = response.headers.get("x-ratelimit-remaining", "")
    store.rest_limit = response.headers.get("x-ratelimit-limit", "")
    store.rest_reset = response.headers.get("x-ratelimit-reset", "")


def _retail_payload(q: Any) -> dict[str, Any]:
    payload = _original_retail_payload(q)
    venue = str(getattr(q, "venue", ""))
    book = base._retail_book_name(venue.split(":", 1)[1] if ":" in venue else venue)
    meta = _quote_meta.get((str(getattr(q, "settlement_key", "")), book))
    if meta:
        payload.update(meta)
    return payload


base._retail_quote_payload = _retail_payload


async def _history_probe(http: httpx.AsyncClient, api_key: str, books: list[str]) -> None:
    """Tiny read-only probe: can April-2026 MLB history support timestamped study?"""
    params = {
        "apiKey": api_key,
        "sport": "baseball",
        "league": "usa-mlb",
        "from": "2026-04-19T00:00:00Z",
        "to": "2026-04-20T00:00:00Z",
    }
    try:
        response = await http.get(REST_HIST_EVENTS, params=params)
    except Exception as exc:
        logger.warning("ODDS_API_IO_HISTORY_PROBE transport_error=%s", type(exc).__name__)
        return
    if response.status_code != 200:
        logger.warning("ODDS_API_IO_HISTORY_PROBE stage=events http_status=%s", response.status_code)
        return
    body = response.json()
    events = [x for x in body if isinstance(x, Mapping)] if isinstance(body, list) else []
    logger.info("ODDS_API_IO_HISTORY_PROBE %s", json.dumps({
        "stage": "events",
        "http_status": response.status_code,
        "event_count": len(events),
        "range": ["2026-04-19T00:00:00Z", "2026-04-20T00:00:00Z"],
        "read_only": True,
    }, separators=(",", ":")))
    if not events or not books:
        return
    event_id = str(events[0].get("id") or "")
    if not event_id:
        return

    chosen_book = None
    hist_payload: Mapping[str, Any] | None = None
    for book in books:
        try:
            response = await http.get(REST_HIST_ODDS, params={
                "apiKey": api_key,
                "eventId": event_id,
                "bookmakers": book,
            })
        except Exception:
            continue
        if response.status_code != 200:
            continue
        parsed = response.json()
        if isinstance(parsed, Mapping):
            chosen_book = book
            hist_payload = parsed
            break
    if chosen_book is None or hist_payload is None:
        logger.info("ODDS_API_IO_HISTORY_PROBE %s", json.dumps({
            "stage": "historical_odds",
            "event_id": event_id,
            "usable_book_found": False,
            "read_only": True,
        }, separators=(",", ":")))
        return

    markets = hist_payload.get("bookmakers")
    market_rows = []
    if isinstance(markets, Mapping):
        raw_rows = markets.get(chosen_book)
        if isinstance(raw_rows, list):
            market_rows = [x for x in raw_rows if isinstance(x, Mapping)]
    has_embedded_timeseries = any(
        isinstance(row.get("movements"), list) or isinstance(row.get("history"), list)
        for row in market_rows
    )
    logger.info("ODDS_API_IO_HISTORY_PROBE %s", json.dumps({
        "stage": "historical_odds",
        "event_id": event_id,
        "book": chosen_book,
        "market_count": len(market_rows),
        "embedded_timeseries": has_embedded_timeseries,
        "interpretation": "TIMESERIES_PRESENT" if has_embedded_timeseries else "CLOSING_SNAPSHOT_ONLY_IN_RESPONSE",
        "read_only": True,
    }, separators=(",", ":")))

    try:
        response = await http.get(REST_MOVEMENTS, params={
            "apiKey": api_key,
            "eventId": event_id,
            "bookmaker": chosen_book,
            "market": "ML",
        })
    except Exception as exc:
        logger.warning("ODDS_API_IO_HISTORY_PROBE stage=movements transport_error=%s", type(exc).__name__)
        return
    if response.status_code != 200:
        logger.info("ODDS_API_IO_HISTORY_PROBE %s", json.dumps({
            "stage": "movements",
            "event_id": event_id,
            "book": chosen_book,
            "http_status": response.status_code,
            "read_only": True,
        }, separators=(",", ":")))
        return
    payload = response.json()
    moves = payload.get("movements") if isinstance(payload, Mapping) else None
    moves = moves if isinstance(moves, list) else []
    timestamps: list[int] = []
    for move in moves:
        if not isinstance(move, Mapping):
            continue
        try:
            timestamps.append(int(float(move.get("timestamp"))))
        except (TypeError, ValueError):
            pass
    logger.info("ODDS_API_IO_HISTORY_PROBE %s", json.dumps({
        "stage": "movements",
        "event_id": event_id,
        "book": chosen_book,
        "http_status": response.status_code,
        "movement_count": len(moves),
        "timestamp_min": min(timestamps) if timestamps else None,
        "timestamp_max": max(timestamps) if timestamps else None,
        "read_only": True,
    }, separators=(",", ":")))


async def run_odds_io_rest(
    store: base.OddsIoStore,
    *,
    api_key: str,
    league_slug: str,
    markets: str,
    freshness_ms: int,
) -> None:
    del league_slug, markets  # event discovery already scoped the universe
    books = _bookmakers()
    poll_s = _poll_seconds()
    signatures: dict[tuple[str, str, str], str] = {}
    counters: Counter[str] = Counter()
    primed = False
    batch_cursor = 0

    async with httpx.AsyncClient(timeout=5.0) as http:
        await _history_probe(http, api_key, books)
        while True:
            batches = _event_batches(store)
            if not batches:
                store.connected = True
                logger.info("ODDS_API_IO_REST_NO_MATCHED_EVENTS read_only=true")
                await asyncio.sleep(5.0)
                continue
            batch = batches[batch_cursor % len(batches)]
            batch_cursor += 1
            started = time.perf_counter()
            receipt_ms = int(time.time() * 1000)
            try:
                response = await http.get(REST_MULTI, params={
                    "apiKey": api_key,
                    "eventIds": ",".join(batch),
                    "bookmakers": ",".join(books),
                })
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                counters["transport_errors"] += 1
                store.connected = False
                logger.warning(
                    "ODDS_API_IO_REST_POLL_ERROR type=%s requests=%s",
                    type(exc).__name__, store.rest_requests,
                )
                await asyncio.sleep(poll_s)
                continue

            rtt_ms = round((time.perf_counter() - started) * 1000, 3)
            _safe_rate_headers(response, store)
            counters[f"http_{response.status_code}"] += 1

            if response.status_code == 429:
                store.connected = False
                logger.warning("ODDS_API_IO_REST_RATE_LIMIT %s", json.dumps({
                    "http_status": 429,
                    "requests_this_process": store.rest_requests,
                    "remaining": store.rest_remaining,
                    "limit": store.rest_limit,
                    "reset": store.rest_reset,
                    "action": "EXPLICIT_429_NO_LEAD_INFERENCE",
                    "read_only": True,
                }, separators=(",", ":")))
                await asyncio.sleep(max(5.0, poll_s))
                continue
            if response.status_code != 200:
                store.connected = False
                logger.warning(
                    "ODDS_API_IO_REST_POLL_HTTP_ERROR status=%s requests=%s",
                    response.status_code, store.rest_requests,
                )
                await asyncio.sleep(poll_s)
                continue

            store.connected = True
            try:
                remaining = int(store.rest_remaining)
            except (TypeError, ValueError):
                remaining = 5000
            if remaining <= RATE_LIMIT_SAFETY_FLOOR:
                counters["rate_floor_sleeps"] += 1
                logger.warning("ODDS_API_IO_REST_RATE_FLOOR %s", json.dumps({
                    "remaining": remaining,
                    "floor": RATE_LIMIT_SAFETY_FLOOR,
                    "reset": store.rest_reset,
                    "action": "SLEEP_60S",
                    "read_only": True,
                }, separators=(",", ":")))
                await asyncio.sleep(60.0)
                continue

            parsed = response.json()
            rows = [x for x in parsed if isinstance(x, Mapping)] if isinstance(parsed, list) else []
            counters["polls"] += 1
            counters["events_returned"] += len(rows)
            moves_this_poll = 0

            for event in rows:
                event_id = str(event.get("id") or "")
                if not event_id:
                    continue
                raw_books = event.get("bookmakers")
                if not isinstance(raw_books, Mapping):
                    continue
                for book_raw, raw_markets in raw_books.items():
                    book = base._retail_book_name(book_raw)
                    if not book or not isinstance(raw_markets, list):
                        continue
                    for raw_market in raw_markets:
                        if not isinstance(raw_market, Mapping):
                            continue
                        normalized = dict(raw_market)
                        normalized["name"] = _market_name(normalized.get("name"))
                        if str(normalized["name"]).lower() not in {"ml", "spread", "totals"}:
                            continue
                        signature = json.dumps({
                            "name": normalized.get("name"),
                            "updatedAt": normalized.get("updatedAt"),
                            "odds": normalized.get("odds"),
                        }, sort_keys=True, separators=(",", ":"), default=str)
                        sig_key = (event_id, book, str(normalized.get("name")))
                        prior = signatures.get(sig_key)
                        if prior == signature:
                            continue
                        signatures[sig_key] = signature

                        provider_updated_ms = _updated_ms(normalized.get("updatedAt"))
                        age_ms = None if provider_updated_ms is None else receipt_ms - provider_updated_ms
                        event_row = store.events.get(event_id)
                        if event_row is None:
                            continue
                        quote_rows = base._quotes_from_market(
                            event_id=event_id,
                            event=event_row,
                            book_raw=book_raw,
                            market=normalized,
                            received_ms=receipt_ms,
                            freshness_ms=max(freshness_ms, 5000),
                        )
                        for q in quote_rows:
                            _quote_meta[(q.settlement_key, book)] = {
                                "provider_updated_ms": provider_updated_ms,
                                "provider_reported_age_ms": age_ms,
                                "provider_receipt_ms": receipt_ms,
                                "provider_request_rtt_ms": rtt_ms,
                                "provider_timestamp_ground_truth": False,
                                "provider_timestamp_status": "ODDS_API_IO_CAPTURE_TIMESTAMP_UNVERIFIED_VS_BOOK_SOURCE",
                            }

                        store.ingest({
                            "type": "updated" if primed else "created",
                            "id": event_id,
                            "bookie": book_raw,
                            "timestamp": None if provider_updated_ms is None else provider_updated_ms / 1000.0,
                            "markets": [normalized],
                        }, freshness_ms=max(freshness_ms, 5000))

                        if primed:
                            moves_this_poll += 1
                            counters["market_moves"] += 1
                            logger.info("ODDS_API_IO_REST_MOVE %s", json.dumps({
                                "event_id": event_id,
                                "book": str(book_raw),
                                "market": normalized.get("name"),
                                "provider_updated_ms": provider_updated_ms,
                                "receipt_ms": receipt_ms,
                                "provider_reported_age_ms": age_ms,
                                "request_rtt_ms": rtt_ms,
                                "batch_size": len(batch),
                                "batch_count": len(batches),
                                "effective_event_poll_s": poll_s * len(batches),
                                "requests_this_process": store.rest_requests,
                                "rate_remaining": store.rest_remaining,
                                "rate_limit": store.rest_limit,
                                "provider_timestamp_ground_truth": False,
                                "read_only": True,
                                "order_entry": False,
                            }, separators=(",", ":"), default=str))

            if not primed:
                # Initial snapshots establish state but are not line movements.
                while True:
                    try:
                        store.updates.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                primed = True
                logger.info("ODDS_API_IO_REST_PRIMED %s", json.dumps({
                    "matched_events": sum(len(x) for x in batches),
                    "batch_count": len(batches),
                    "effective_event_poll_s": poll_s * len(batches),
                    "bookmakers_requested": books,
                    "requests_per_hour_target": round(3600.0 / poll_s, 2),
                    "rate_limit": store.rest_limit,
                    "rate_remaining": store.rest_remaining,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":"), default=str))

            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(0.0, poll_s - elapsed))


# base.run_session resolves this global at call time.  Replace only the retail
# transport; direct prediction-market feeds and evaluation remain unchanged.
base.run_odds_io_ws = run_odds_io_rest


if __name__ == "__main__":
    base.main()
