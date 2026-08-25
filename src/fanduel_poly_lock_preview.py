"""One-shot authenticated revalidation of current FanDuel <-> Polymarket MLB locks.

Mutation-free: starts from the corrected physical-event scanner, re-fetches the
relevant FanDuel quote and exact Polymarket public book, recomputes q=1 all-in
economics, then performs only authenticated market retrieval and ``orders.preview``.
It NEVER calls ``orders.create``. Preview qualification does not waive the separate
settlement-rule audit and is never counted as realized profit.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
from typing import Any, Mapping

import httpx
from polymarket_us import PolymarketUS

from src.four_venue_threshold_middle_shadow import run_once as run_four_venue
from src.strict_mlb_prop_snapshot import poly_top_book, polymarket_fee
from src.the_odds_api_mlb_shadow import fetch_sportsbooks, sportsbook_quotes

D = Decimal
ONE = D("1")
TARGET = D("0.03")
BASE = "https://gateway.polymarket.us"


def _opposite(side: str) -> str:
    return "NO" if side.upper() == "YES" else "YES"


def _actual_poly_side(market_id: str, semantic_action: str) -> tuple[str, str]:
    slug, sep, source_side = str(market_id).rpartition("#")
    if not sep or source_side not in {"YES", "NO"}:
        raise ValueError(f"bad normalized Polymarket market id: {market_id}")
    action = semantic_action.upper()
    if action not in {"YES", "NO"}:
        raise ValueError(f"bad semantic action: {semantic_action}")
    return slug, source_side if action == "YES" else _opposite(source_side)


def _native_ask(book: Any, side: str) -> tuple[D, D]:
    if side == "YES":
        return D(str(book.yes_ask)), D(str(book.yes_depth))
    return D(str(book.no_ask)), D(str(book.no_depth))


def _preview_payload(slug: str, side: str, native: D) -> dict[str, Any]:
    yes_px = native if side == "YES" else ONE - native
    return {
        "marketSlug": slug,
        "intent": "ORDER_INTENT_BUY_LONG" if side == "YES" else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(yes_px), "currency": "USD"},
        "quantity": 1,
        "tif": "TIME_IN_FORCE_FILL_OR_KILL",
        "participateDontInitiate": False,
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        "synchronousExecution": True,
        "maxBlockTime": "2",
    }


def _unwrap_market(resp: Mapping[str, Any]) -> Mapping[str, Any]:
    row = resp.get("market")
    return row if isinstance(row, Mapping) else resp


def _emit_result(scan: Mapping[str, Any], checked: list[dict[str, Any]], *, sdk_exit_stage: str = "") -> dict[str, Any]:
    result = {
        "status": "FANDUEL_POLY_LOCK_PREVIEW_ONLY",
        "physical_date_surviving_locks": len(scan.get("top_same_threshold_positive_locks_math_only") or []),
        "fanduel_poly_rows_checked": len(checked),
        "preview_qualified_rule_audit_required": sum(
            1 for row in checked if row.get("status") == "PREVIEW_QUALIFIED_RULE_AUDIT_REQUIRED"
        ),
        "rows": checked,
        "sdk_exit_stage": sdk_exit_stage,
        "mutation_performed": False,
        "order_entry": False,
        "realized_pnl_usd": "0",
    }
    print("FANDUEL_POLY_LOCK_PREVIEW " + json.dumps(result, separators=(",", ":"), default=str), flush=True)
    return result


async def _public_poly_book(slug: str) -> Any | None:
    async with httpx.AsyncClient(timeout=6.0) as http:
        response = await http.get(f"{BASE}/v1/markets/{slug}/book")
        if response.status_code != 200:
            return None
        return poly_top_book(response.json() if response.content else {})


async def main_async() -> dict[str, Any]:
    odds_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    poly_key = os.getenv("POLYMARKET_KEY_ID", "").strip()
    poly_secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not odds_key or not poly_key or not poly_secret:
        raise RuntimeError("required referenced credentials missing")

    scan = await run_four_venue(odds_key)
    locks = [
        row for row in (scan.get("top_same_threshold_positive_locks_math_only") or [])
        if isinstance(row, Mapping)
        and {str(row.get("yes_venue")), str(row.get("no_venue"))} == {"polymarket", "sportsbook:fanduel"}
    ]
    print(
        "FANDUEL_POLY_PREVIEW_STAGE "
        + json.dumps({"stage": "LOCKS_SELECTED", "candidate_count": len(locks)}, separators=(",", ":")),
        flush=True,
    )

    # Do not construct/close the authenticated SDK when there is nothing to
    # preview. Besides avoiding unnecessary auth work, this guarantees every
    # economically dry run emits its terminal verifier record.
    if not locks:
        return _emit_result(scan, [])

    async with httpx.AsyncClient(timeout=20.0) as http:
        events, _quota = await fetch_sportsbooks(http, odds_key)
    fd_quotes = {q.market_id: q for q in sportsbook_quotes(events, received_ms=0) if q.venue == "sportsbook:fanduel"}
    print(
        "FANDUEL_POLY_PREVIEW_STAGE "
        + json.dumps({"stage": "FANDUEL_REFRESHED", "quote_count": len(fd_quotes)}, separators=(",", ":")),
        flush=True,
    )

    try:
        client = PolymarketUS(key_id=poly_key, secret_key=poly_secret, timeout=8.0)
    except SystemExit as exc:
        checked = [{"status": "SDK_INIT_SYSTEM_EXIT", "exit_code": getattr(exc, "code", None), "order_entry": False, "realized": False}]
        return _emit_result(scan, checked, sdk_exit_stage="client_init")

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
                checked.append({"status": "FANDUEL_QUOTE_GONE", "slug": slug, "poly_side": poly_side, "fanduel_market_id": fd_market_id})
                continue
            fd_cost = fd.yes_cost if fd_action == "YES" else fd.no_cost

            book = await _public_poly_book(slug)
            if book is None:
                checked.append({"status": "POLY_BOOK_GONE", "slug": slug, "poly_side": poly_side})
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
                        preview = await asyncio.to_thread(client.orders.preview, {"request": _preview_payload(slug, poly_side, raw_poly)})
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
                "all_in_cost": str(all_in),
                "locked_profit": str(locked),
                "economics_ok_3c": economics_ok,
                "market_active": meta.get("active"),
                "market_closed": meta.get("closed"),
                "market_archived": meta.get("archived"),
                "authenticated_market_ok": market_ok,
                "authenticated_preview_ok": preview_ok,
                "preview_error_type": preview_error,
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

    return _emit_result(scan, checked, sdk_exit_stage=sdk_exit_stage)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
