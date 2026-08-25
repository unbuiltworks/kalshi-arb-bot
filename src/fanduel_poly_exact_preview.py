"""Focused FanDuel <-> Polymarket MLB full-game exact-lock verifier.

Covers moneyline, half-run spreads, and half-run game totals. Discovery fetches
only Polymarket books whose normalized settlement keys are present at FanDuel,
then fail-closes on exact MLB Eastern physical game date. A candidate must retain
>=3c q=1 after an immediate FanDuel refresh and exact Polymarket book refresh
before authenticated ``orders.preview`` is attempted.

FanDuel is manual-only. This module never calls ``orders.create`` and never
classifies preview-only economics as realized profit.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
import time
from typing import Any, Mapping

import httpx
from polymarket_us import PolymarketUS

from src.fanduel_poly_lock_preview import (
    _actual_poly_side,
    _native_ask,
    _preview_payload,
    _public_poly_book,
    _unwrap_market,
)
from src.four_venue_threshold_middle_shadow import _physical_event_date, _sportsbook_start_map
from src.mlb_game_side_normalization import NormalizedSide, normalize_polymarket_mlb
from src.polymarket_scope_guard import full_game_only_events
from src.strict_mlb_prop_snapshot import POLY, poly_top_book, polymarket_fee
from src.the_odds_api_mlb_shadow import fetch_sportsbooks, sportsbook_quotes
from src.three_venue_mlb_price_scan import _dedupe, _get
from src.three_venue_pricing_core import PricedQuote, make_priced_quote
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_three_venue_sports import fetch_polymarket_mlb

D = Decimal
ONE = D("1")
TARGET = D("0.03")
FAMILIES = {"moneyline", "spread", "game_total"}


def _unique_sides(rows: list[NormalizedSide]) -> list[NormalizedSide]:
    out: list[NormalizedSide] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row.venue_market_id, row.side, row.contract_key)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


async def _exact_scan(odds_key: str) -> dict[str, Any]:
    """Price only exact FanDuel settlement keys on Polymarket."""
    async with httpx.AsyncClient(timeout=20.0) as http:
        sportsbook_result, poly_raw = await asyncio.gather(
            fetch_sportsbooks(http, odds_key),
            fetch_polymarket_mlb(http),
        )
        events, quota = sportsbook_result
        poly = full_game_only_events(poly_raw)

        received_ms = int(time.time() * 1000)
        fd_quotes = [q for q in sportsbook_quotes(events, received_ms=received_ms) if q.venue == "sportsbook:fanduel"]
        fd_keys = {q.settlement_key for q in fd_quotes}
        sides = _unique_sides([
            side for side in normalize_polymarket_mlb(poly)
            if side.identity.scope == "full_contest"
            and side.identity.market_family in FAMILIES
            and side.contract_key in fd_keys
        ])
        pids = sorted({side.venue_market_id for side in sides})
        sem = asyncio.Semaphore(20)
        presults = await asyncio.gather(*(
            _get(http, sem, f"{POLY}/v1/markets/{market_id}/book") for market_id in pids
        ))

    errors: list[dict[str, str]] = []
    books: dict[str, tuple[Any, int]] = {}
    for market_id, (body, book_received_ms, error) in zip(pids, presults):
        book = poly_top_book(body or {}) if body is not None else None
        if book is None:
            errors.append({"venue": "polymarket", "market_id": market_id, "error": error or "book_unparseable"})
        else:
            books[market_id] = (book, book_received_ms)

    priced: list[tuple[PricedQuote, NormalizedSide]] = []
    for side in sides:
        item = books.get(side.venue_market_id)
        if item is None:
            continue
        book, book_received_ms = item
        row = make_priced_quote(side, book, received_ms=book_received_ms, depth_source="public_book_top_level_exact_fd_key")
        if row is not None:
            priced.append((row, side))

    start_by_event_id = _sportsbook_start_map(events)
    date_by_market: dict[str, str] = {}
    for priced_row, side in priced:
        date_by_market[priced_row.quote.market_id] = _physical_event_date(
            side.venue_market_id, side.identity.event.scheduled_start_ms
        )
    for quote in fd_quotes:
        event_id = quote.market_id.split(":", 1)[0]
        start_ms = start_by_event_id.get(event_id, 0)
        date_by_market[quote.market_id] = _physical_event_date(quote.market_id, start_ms)

    raw_locks = _dedupe(find_cross_venue_locks(
        [row.quote for row, _side in priced] + fd_quotes,
        max_quote_skew_ms=70_000,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    ))
    valid: list[Any] = []
    rejected = 0
    for lock in raw_locks:
        yes_date = date_by_market.get(str(lock.yes_market_id), "")
        no_date = date_by_market.get(str(lock.no_market_id), "")
        if not yes_date or not no_date or yes_date != no_date:
            rejected += 1
            continue
        if {str(lock.yes_venue), str(lock.no_venue)} != {"polymarket", "sportsbook:fanduel"}:
            continue
        if D(str(lock.unit_profit)) < TARGET:
            continue
        valid.append(lock)
    valid.sort(key=lambda row: D(str(row.unit_profit)), reverse=True)

    return {
        "status": "FANDUEL_POLY_EXACT_FULLGAME_DISCOVERY",
        "scope": "MLB_FULL_GAME_MONEYLINE_SPREAD_TOTAL",
        "sportsbook_events": len(events),
        "polymarket_full_game_events": len(poly),
        "fanduel_quotes": len(fd_quotes),
        "fanduel_exact_keys": len(fd_keys),
        "matching_polymarket_sides": len(sides),
        "polymarket_books_requested": len(pids),
        "polymarket_priced_quotes": len(priced),
        "raw_positive_locks": len(raw_locks),
        "cross_date_or_unresolved_rejected": rejected,
        "candidate_count_3c_plus": len(valid),
        "candidates": [row.as_dict() for row in valid[:20]],
        "fetch_error_count": len(errors),
        "fetch_errors": errors[:20],
        "quota": quota,
        "physical_event_gate": "EXACT_MLB_EASTERN_DATE_REQUIRED_MARKET_ID_DATE_PREFERRED",
        "min_q1_profit": str(TARGET),
        "rule_gate": "SPORTSBOOK_RULE_AUDIT_REQUIRED_BEFORE_EXECUTION",
        "fanduel_transport": "MANUAL_ONLY",
        "read_only": True,
        "order_entry": False,
        "realized_pnl_usd": "0",
    }


def _emit(scan: Mapping[str, Any], checked: list[dict[str, Any]], *, sdk_exit_stage: str = "") -> dict[str, Any]:
    result = {
        "status": "FANDUEL_POLY_EXACT_PREVIEW_ONLY",
        "scope": scan.get("scope"),
        "candidate_count_3c_plus_discovery": scan.get("candidate_count_3c_plus", 0),
        "fanduel_poly_rows_checked": len(checked),
        "preview_qualified_rule_audit_required": sum(
            1 for row in checked if row.get("status") == "PREVIEW_QUALIFIED_RULE_AUDIT_REQUIRED"
        ),
        "polymarket_books_requested": scan.get("polymarket_books_requested"),
        "polymarket_priced_quotes": scan.get("polymarket_priced_quotes"),
        "fanduel_quotes": scan.get("fanduel_quotes"),
        "cross_date_or_unresolved_rejected": scan.get("cross_date_or_unresolved_rejected"),
        "fetch_error_count": scan.get("fetch_error_count"),
        "quota": scan.get("quota"),
        "rows": checked,
        "sdk_exit_stage": sdk_exit_stage,
        "mutation_performed": False,
        "fanduel_transport": "MANUAL_ONLY",
        "order_entry": False,
        "realized_pnl_usd": "0",
    }
    print("FANDUEL_POLY_EXACT_PREVIEW " + json.dumps(result, separators=(",", ":"), default=str), flush=True)
    return result


async def main_async() -> dict[str, Any]:
    odds_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    poly_key = os.getenv("POLYMARKET_KEY_ID", "").strip()
    poly_secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not odds_key or not poly_key or not poly_secret:
        raise RuntimeError("required referenced credentials missing")

    print("FANDUEL_POLY_EXACT_STAGE " + json.dumps({"stage": "EXACT_SCAN_START"}, separators=(",", ":")), flush=True)
    scan = await _exact_scan(odds_key)
    locks = [row for row in (scan.get("candidates") or []) if isinstance(row, Mapping)]
    print("FANDUEL_POLY_EXACT_STAGE " + json.dumps({
        "stage": "LOCKS_SELECTED",
        "candidate_count": len(locks),
        "polymarket_books_requested": scan.get("polymarket_books_requested"),
        "polymarket_priced_quotes": scan.get("polymarket_priced_quotes"),
        "fanduel_quotes": scan.get("fanduel_quotes"),
        "fetch_error_count": scan.get("fetch_error_count"),
        "cross_date_rejected": scan.get("cross_date_or_unresolved_rejected"),
        "quota": scan.get("quota"),
    }, separators=(",", ":"), default=str), flush=True)
    if not locks:
        return _emit(scan, [])

    # A second FanDuel pull is deliberate. Discovery data is never trusted for
    # actionability; the matching retail quote must still exist immediately
    # before the authenticated Polymarket preview.
    async with httpx.AsyncClient(timeout=20.0) as http:
        events, refresh_quota = await fetch_sportsbooks(http, odds_key)
    fd_quotes = {
        q.market_id: q for q in sportsbook_quotes(events, received_ms=int(time.time() * 1000))
        if q.venue == "sportsbook:fanduel"
    }
    print("FANDUEL_POLY_EXACT_STAGE " + json.dumps({
        "stage": "FANDUEL_REFRESHED", "quote_count": len(fd_quotes), "quota": refresh_quota
    }, separators=(",", ":"), default=str), flush=True)

    try:
        client = PolymarketUS(key_id=poly_key, secret_key=poly_secret, timeout=8.0)
    except SystemExit as exc:
        return _emit(scan, [{
            "status": "SDK_INIT_SYSTEM_EXIT",
            "exit_code": getattr(exc, "code", None),
            "order_entry": False,
            "realized": False,
        }], sdk_exit_stage="client_init")

    checked: list[dict[str, Any]] = []
    sdk_exit_stage = ""
    try:
        seen: set[tuple[str, str, str]] = set()
        for lock in locks[:10]:
            if str(lock.get("yes_venue")) == "polymarket":
                poly_market_id, semantic_action = str(lock.get("yes_market_id") or ""), "YES"
                fd_market_id, fd_action = str(lock.get("no_market_id") or ""), "NO"
            else:
                poly_market_id, semantic_action = str(lock.get("no_market_id") or ""), "NO"
                fd_market_id, fd_action = str(lock.get("yes_market_id") or ""), "YES"

            try:
                slug, poly_side = _actual_poly_side(poly_market_id, semantic_action)
            except Exception as exc:
                checked.append({"status": "BAD_POLY_SIDE_MAPPING", "error_type": type(exc).__name__})
                continue
            dedupe_key = (slug, poly_side, fd_market_id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            fd = fd_quotes.get(fd_market_id)
            if fd is None:
                checked.append({
                    "status": "FANDUEL_QUOTE_GONE",
                    "settlement_key": str(lock.get("settlement_key") or ""),
                    "slug": slug,
                    "poly_side": poly_side,
                    "fanduel_market_id": fd_market_id,
                    "discovery_unit_profit": str(lock.get("unit_profit") or ""),
                    "order_entry": False,
                    "realized": False,
                })
                continue
            fd_cost = fd.yes_cost if fd_action == "YES" else fd.no_cost

            book = await _public_poly_book(slug)
            if book is None:
                checked.append({
                    "status": "POLY_BOOK_GONE",
                    "settlement_key": str(lock.get("settlement_key") or ""),
                    "slug": slug,
                    "poly_side": poly_side,
                    "discovery_unit_profit": str(lock.get("unit_profit") or ""),
                    "order_entry": False,
                    "realized": False,
                })
                continue
            raw_poly, poly_depth = _native_ask(book, poly_side)
            fee = polymarket_fee(1, raw_poly)
            all_in = raw_poly + fee + D(str(fd_cost))
            locked = ONE - all_in
            economics_ok = poly_depth >= ONE and locked >= TARGET

            meta: Mapping[str, Any] = {}
            market_ok = False
            preview_ok = False
            preview_error = ""
            if economics_ok:
                try:
                    exact = await asyncio.to_thread(client.markets.retrieve_by_slug, slug)
                    meta = _unwrap_market(exact if isinstance(exact, Mapping) else {})
                    market_ok = not (
                        meta.get("active") is False
                        or meta.get("closed") is True
                        or meta.get("archived") is True
                    ) and str(meta.get("slug") or slug) == slug
                    if market_ok:
                        preview = await asyncio.to_thread(
                            client.orders.preview,
                            {"request": _preview_payload(slug, poly_side, raw_poly)},
                        )
                        preview_ok = bool(preview)
                except SystemExit as exc:
                    preview_error = "SystemExit"
                    sdk_exit_stage = "market_or_preview"
                    checked.append({
                        "status": "SDK_PREVIEW_SYSTEM_EXIT",
                        "slug": slug,
                        "poly_side": poly_side,
                        "exit_code": getattr(exc, "code", None),
                        "order_entry": False,
                        "realized": False,
                    })
                    break
                except Exception as exc:
                    preview_error = type(exc).__name__

            checked.append({
                "status": "PREVIEW_QUALIFIED_RULE_AUDIT_REQUIRED" if economics_ok and market_ok and preview_ok else "NOT_PREVIEW_QUALIFIED",
                "settlement_key": str(lock.get("settlement_key") or ""),
                "slug": slug,
                "poly_side": poly_side,
                "poly_raw_ask": str(raw_poly),
                "poly_fee": str(fee),
                "poly_q1_depth": str(poly_depth),
                "fanduel_market_id": fd_market_id,
                "fanduel_semantic_action": fd_action,
                "fanduel_implied_cost": str(fd_cost),
                "discovery_unit_cost": str(lock.get("unit_cost") or ""),
                "discovery_unit_profit": str(lock.get("unit_profit") or ""),
                "discovery_quote_skew_ms": lock.get("quote_skew_ms"),
                "refreshed_all_in_cost": str(all_in),
                "refreshed_locked_profit": str(locked),
                "economics_ok_3c": economics_ok,
                "market_active": meta.get("active"),
                "market_closed": meta.get("closed"),
                "market_archived": meta.get("archived"),
                "authenticated_market_ok": market_ok,
                "authenticated_preview_ok": preview_ok,
                "preview_error_type": preview_error,
                "rule_audit_required": True,
                "fanduel_manual_only": True,
                "order_entry": False,
                "realized": False,
            })
    finally:
        try:
            client.close()
        except SystemExit as exc:
            sdk_exit_stage = sdk_exit_stage or "client_close"
            checked.append({
                "status": "SDK_CLOSE_SYSTEM_EXIT",
                "exit_code": getattr(exc, "code", None),
                "order_entry": False,
                "realized": False,
            })

    return _emit(scan, checked, sdk_exit_stage=sdk_exit_stage)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
