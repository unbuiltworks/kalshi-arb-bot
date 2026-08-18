"""K/G/P micro-live entrypoint with exact final-book revalidation.

Discovery is never execution pricing. For each explicitly approved pair this wrapper
refreshes the exact involved books concurrently immediately before the base q=1
execution guard runs:

* Gemini Predictions: public ``<symbol>@depth10@100ms`` WebSocket depth.
* Kalshi: public ``GET /markets/{ticker}/orderbook?depth=1`` plus current series
  fee metadata.
* Polymarket US: public ``GET /v1/markets/{slug}/book``.

Rows that fail final refresh stay on their older/discovery depth source. The execution
guard then rejects them on freshness or the explicit Gemini discovery-only gate.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import time
from typing import Any

import httpx

from src.gemini_prediction_depth import fetch_depth_top
from src.sports_kgp_micro_guard import RuleApproval
from src.strict_mlb_prop_adapters import FAMILY_SERIES
from src.strict_mlb_prop_snapshot import (
    TopBook,
    conservative_kalshi_fee,
    kalshi_top_book,
    poly_top_book,
    polymarket_fee,
)
from src.three_venue_pricing_core import gemini_one_contract_fee
from src import sports_kgp_micro_service as base

D = Decimal
ONE = D("1")
KALSHI_PUBLIC = "https://external-api.kalshi.com/trade-api/v2"
POLY_PUBLIC = "https://gateway.polymarket.us"
SUPPORTED_KALSHI_FEE_TYPES = frozenset({"quadratic", "quadratic_with_maker_fees"})


def _approved_markets(approvals: dict[str, list[RuleApproval]]) -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    for key, rows in approvals.items():
        for approval in rows:
            for venue in approval.venues:
                out.setdefault((key, venue), set()).add(approval.market_for(venue))
    return out


def _orient(book: TopBook, source_side: str) -> tuple[D, D, D, D] | None:
    if source_side in {"YES", "CONTRACT"}:
        return D(book.yes_ask), D(book.no_ask), D(book.yes_depth), D(book.no_depth)
    if source_side == "NO":
        return D(book.no_ask), D(book.yes_ask), D(book.no_depth), D(book.yes_depth)
    return None


def _apply_gemini_top(row: dict[str, Any], top: Any) -> dict[str, Any]:
    """Replace one Gemini discovery row with canonical q=1 depth economics."""
    source_side = str(row.get("source_side") or "")
    if source_side in {"YES", "CONTRACT"}:
        raw_yes = D(top.yes_ask)
        raw_no = ONE - D(top.yes_bid)
        yes_depth = D(top.yes_ask_size)
        no_depth = D(top.yes_bid_size)
    elif source_side == "NO":
        raw_yes = ONE - D(top.yes_bid)
        raw_no = D(top.yes_ask)
        yes_depth = D(top.yes_bid_size)
        no_depth = D(top.yes_ask_size)
    else:
        return dict(row)
    yes_fee = gemini_one_contract_fee(raw_yes)
    no_fee = gemini_one_contract_fee(raw_no)
    out = dict(row)
    out.update({
        "raw_yes_ask": str(raw_yes),
        "raw_no_ask": str(raw_no),
        "yes_fee": str(yes_fee),
        "no_fee": str(no_fee),
        "all_in_yes_cost": str(raw_yes + yes_fee),
        "all_in_no_cost": str(raw_no + no_fee),
        "yes_depth": str(min(ONE, yes_depth)),
        "no_depth": str(min(ONE, no_depth)),
        "received_ms": int(top.received_ms),
        "depth_source": "gemini_depth10_100ms_final",
    })
    return out


def _apply_poly_book(row: dict[str, Any], book: TopBook, received_ms: int) -> dict[str, Any]:
    oriented = _orient(book, str(row.get("source_side") or ""))
    if oriented is None:
        return dict(row)
    raw_yes, raw_no, yes_depth, no_depth = oriented
    yes_fee = polymarket_fee(1, raw_yes)
    no_fee = polymarket_fee(1, raw_no)
    out = dict(row)
    out.update({
        "raw_yes_ask": str(raw_yes),
        "raw_no_ask": str(raw_no),
        "yes_fee": str(yes_fee),
        "no_fee": str(no_fee),
        "all_in_yes_cost": str(raw_yes + yes_fee),
        "all_in_no_cost": str(raw_no + no_fee),
        "yes_depth": str(min(ONE, yes_depth)),
        "no_depth": str(min(ONE, no_depth)),
        "received_ms": int(received_ms),
        "depth_source": "polymarket_public_book_final",
    })
    return out


def _apply_kalshi_book(
    row: dict[str, Any],
    book: TopBook,
    received_ms: int,
    fee_multiplier: D,
) -> dict[str, Any]:
    oriented = _orient(book, str(row.get("source_side") or ""))
    if oriented is None:
        return dict(row)
    raw_yes, raw_no, yes_depth, no_depth = oriented
    yes_fee = conservative_kalshi_fee(1, raw_yes, fee_multiplier)
    no_fee = conservative_kalshi_fee(1, raw_no, fee_multiplier)
    out = dict(row)
    out.update({
        "raw_yes_ask": str(raw_yes),
        "raw_no_ask": str(raw_no),
        "yes_fee": str(yes_fee),
        "no_fee": str(no_fee),
        "all_in_yes_cost": str(raw_yes + yes_fee),
        "all_in_no_cost": str(raw_no + no_fee),
        "yes_depth": str(min(ONE, yes_depth)),
        "no_depth": str(min(ONE, no_depth)),
        "received_ms": int(received_ms),
        "depth_source": "kalshi_public_orderbook_depth1_final",
    })
    return out


async def _fetch_poly(http: httpx.AsyncClient, slug: str) -> tuple[TopBook, int]:
    response = await http.get(f"{POLY_PUBLIC}/v1/markets/{slug}/book")
    received_ms = int(time.time() * 1000)
    response.raise_for_status()
    book = poly_top_book(response.json())
    if book is None:
        raise ValueError("Polymarket final book is not two-sided")
    return book, received_ms


async def _fetch_kalshi(
    http: httpx.AsyncClient,
    ticker: str,
    family: str,
) -> tuple[TopBook, int, D]:
    series_ticker = FAMILY_SERIES.get(family)
    if not series_ticker:
        raise ValueError("Kalshi family has no exact series mapping")
    book_request = http.get(f"{KALSHI_PUBLIC}/markets/{ticker}/orderbook", params={"depth": 1})
    series_request = http.get(f"{KALSHI_PUBLIC}/series/{series_ticker}")
    book_response, series_response = await asyncio.gather(book_request, series_request)
    received_ms = int(time.time() * 1000)
    book_response.raise_for_status(); series_response.raise_for_status()
    book = kalshi_top_book(book_response.json())
    if book is None:
        raise ValueError("Kalshi final book is not two-sided")
    series = series_response.json().get("series") or {}
    fee_type = str(series.get("fee_type") or "").lower()
    if fee_type not in SUPPORTED_KALSHI_FEE_TYPES:
        raise ValueError(f"unsupported Kalshi fee_type={fee_type or 'missing'}")
    fee_multiplier = D(str(series.get("fee_multiplier")))
    if not fee_multiplier.is_finite() or fee_multiplier < 0:
        raise ValueError("invalid Kalshi fee multiplier")
    return book, received_ms, fee_multiplier


async def refresh_approved_execution_books(
    result: dict[str, Any],
    approvals: dict[str, list[RuleApproval]],
) -> dict[str, Any]:
    approved = _approved_markets(approvals)
    if not approved:
        return result
    output = deepcopy(result)
    rows = output.get("positive_quote_audit", [])
    assessments = {
        str(row.get("settlement_key") or ""): row
        for row in output.get("rule_audit", {}).get("top_assessments", [])
        if isinstance(row, dict) and row.get("settlement_key")
    }

    async with httpx.AsyncClient(timeout=2.5) as http:
        tasks: dict[tuple[str, str, str], asyncio.Task] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("settlement_key") or "")
            venue = str(row.get("venue") or "")
            market = str(row.get("market_id") or "").split("#", 1)[0]
            if market not in approved.get((key, venue), set()):
                continue
            marker = (key, venue, market)
            if venue == "gemini":
                tasks[marker] = asyncio.create_task(fetch_depth_top(market, timeout=2.0))
            elif venue == "polymarket":
                tasks[marker] = asyncio.create_task(_fetch_poly(http, market))
            elif venue == "kalshi":
                family = str(assessments.get(key, {}).get("family") or "")
                tasks[marker] = asyncio.create_task(_fetch_kalshi(http, market, family))

        fresh: dict[tuple[str, str, str], Any] = {}
        failures: dict[str, int] = {"gemini": 0, "polymarket": 0, "kalshi": 0}
        for marker, task in tasks.items():
            try:
                fresh[marker] = await task
            except Exception:
                failures[marker[1]] = failures.get(marker[1], 0) + 1

    replaced: dict[str, int] = {"gemini": 0, "polymarket": 0, "kalshi": 0}
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        key = str(row.get("settlement_key") or "")
        venue = str(row.get("venue") or "")
        market = str(row.get("market_id") or "").split("#", 1)[0]
        value = fresh.get((key, venue, market))
        if value is None:
            continue
        if venue == "gemini":
            rows[idx] = _apply_gemini_top(row, value)
        elif venue == "polymarket":
            book, received_ms = value
            rows[idx] = _apply_poly_book(row, book, received_ms)
        elif venue == "kalshi":
            book, received_ms, multiplier = value
            rows[idx] = _apply_kalshi_book(row, book, received_ms, multiplier)
        else:
            continue
        replaced[venue] += 1

    audit = output.setdefault("execution_revalidation", {})
    audit["requests"] = len(tasks)
    audit["rows_replaced_by_venue"] = replaced
    audit["failures_by_venue"] = failures
    audit["all_books_are_final_execution_sources"] = all(
        str(row.get("depth_source") or "").endswith("_final")
        for row in rows
        if isinstance(row, dict)
        and str(row.get("market_id") or "").split("#", 1)[0]
        in approved.get((str(row.get("settlement_key") or ""), str(row.get("venue") or "")), set())
    )
    return output


# Backwards-compatible helper name used by early unit tests.
async def refresh_approved_gemini_depth(result: dict[str, Any], approvals: dict[str, list[RuleApproval]]) -> dict[str, Any]:
    return await refresh_approved_execution_books(result, approvals)


async def run_with_depth(
    *,
    live_requested: bool,
    once: bool,
    interval_seconds: float,
    approvals_path: Path,
    token_path: Path,
    log_path: Path,
) -> int:
    approvals = base.load_approvals(approvals_path)
    original_run_once = base.scan.run_once

    async def wrapped_run_once() -> dict[str, Any]:
        result = await original_run_once()
        return await refresh_approved_execution_books(result, approvals)

    base.scan.run_once = wrapped_run_once
    try:
        return await base.run(
            live_requested=live_requested,
            once=once,
            interval_seconds=interval_seconds,
            approvals_path=approvals_path,
            token_path=token_path,
            log_path=log_path,
        )
    finally:
        base.scan.run_once = original_run_once


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--approvals", type=Path, default=base.DEFAULT_APPROVALS)
    parser.add_argument("--arm-token", type=Path, default=base.DEFAULT_ARM_TOKEN)
    parser.add_argument("--log", type=Path, default=base.DEFAULT_LOG)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_with_depth(
        live_requested=args.live,
        once=args.once,
        interval_seconds=max(0.25, args.interval_seconds),
        approvals_path=args.approvals,
        token_path=args.arm_token,
        log_path=args.log,
    )))


if __name__ == "__main__":
    main()
