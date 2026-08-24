"""Fill-probability selector for same-Gemini q=1 threshold ladders.

The old frontier rank could choose a mathematically valid but dead long-dated book.
This selector keeps the exact structural/fee certification and then ranks only among
candidates with evidence of recent trading: contract share volume over the last
30 minutes and/or maker-side aggressor flow observed on the live trade tape.

Priority is deliberately execution-first:
  live maker-side aggressor qty > recent maker contract qty > recent hedge qty >
  queue/proximity > event liquidity/volume > hedge depth > expiry.
Dead candidates are suppressed rather than seeded merely because the arithmetic works.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import os
import time
from typing import Any, Mapping

import httpx

from src import gemini_same_venue_crypto_ladder_scan as scan
from src import gemini_same_venue_crypto_maker_q1_wsfirst as live
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto
from src.gemini_prediction_auth import private_rest_request

D = Decimal
ONE = D("1")
VOLUME_WINDOW_SECONDS = max(300, min(7200, int(os.getenv("GEMINI_SAME_VENUE_RECENT_VOLUME_S", "1800"))))
VOLUME_CONCURRENCY = max(2, min(8, int(os.getenv("GEMINI_SAME_VENUE_VOLUME_CONCURRENCY", "4"))))
MIN_RECENT_QTY = max(D("0"), D(os.getenv("GEMINI_SAME_VENUE_MIN_RECENT_QTY", "1")))


def _d(value: Any) -> D:
    try:
        return max(D("0"), D(str(value or "0")))
    except Exception:
        return D("0")


async def _event_volume(event_ticker: str, start_ms: int, end_ms: int) -> dict[str, D]:
    errors: list[str] = []
    for attempt in range(live.RECOVERY_ATTEMPTS):
        try:
            response = await private_rest_request(
                "POST", "/v1/prediction-markets/metrics/volume",
                params={"eventTicker": event_ticker, "startTime": start_ms, "endTime": end_ms},
                timeout=live.RECOVERY_TIMEOUT,
            )
            if response.status_code == 200:
                body = response.json()
                rows = body.get("contracts", []) if isinstance(body, Mapping) else []
                return {
                    str(row.get("symbol") or ""): _d(row.get("totalQty"))
                    for row in rows if isinstance(row, Mapping) and row.get("symbol")
                }
            errors.append(f"HTTP{response.status_code}")
        except Exception as exc:
            errors.append(type(exc).__name__)
        if attempt + 1 < live.RECOVERY_ATTEMPTS:
            await asyncio.sleep(min(0.8, 0.15 * (2 ** attempt)))
    live.emit("RECENT_VOLUME_DEGRADED", event_ticker=event_ticker, errors=errors[-8:], order_entry=False, realized=False)
    return {}


async def _recent_contract_volume(event_tickers: set[str]) -> dict[str, D]:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - VOLUME_WINDOW_SECONDS * 1000
    sem = asyncio.Semaphore(VOLUME_CONCURRENCY)
    out: dict[str, D] = {}

    async def one(ticker: str) -> None:
        async with sem:
            out.update(await _event_volume(ticker, start_ms, end_ms))

    await asyncio.gather(*(one(t) for t in sorted(event_tickers)))
    return out


def _event_stat(event: Mapping[str, Any], name: str) -> D:
    return _d(event.get(name))


def _queue_ahead(candidate: live.base.Candidate, books: Mapping[str, Mapping[str, Any]]) -> D:
    book = books.get(candidate.maker_symbol)
    if not isinstance(book, Mapping):
        return D("999999")
    ob = scan.outcome_book(scan.top(book), candidate.maker_outcome)
    bid = ob.get("bid")
    qty = ob.get("bid_qty")
    if not isinstance(bid, D) or not isinstance(qty, D):
        return D("0")
    return qty if bid == candidate.maker_quote else D("0")


async def discover_flow_ranked_v2() -> tuple[live.base.Candidate | None, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        contracts = live.base._contract_map(events)
        whole_symbols = {s for s, c in contracts.items() if live.base.whole_contract_only(c)}
        books, statuses = await scan._books(http, whole_symbols)
    actionable, joins, telemetry = scan.score(events, books)
    candidates = [c for row in actionable + joins if (c := live.base.candidate_from_row(row, contracts)) is not None]
    events_by_ticker = {str(e.get("ticker") or ""): e for e in events}
    recent = await _recent_contract_volume({c.event_ticker for c in candidates}) if candidates else {}

    def prescore(c: live.base.Candidate) -> tuple[Any, ...]:
        maker_recent = recent.get(c.maker_symbol, D("0"))
        hedge_recent = recent.get(c.hedge_symbol, D("0"))
        gap = c.maker_ask-c.maker_quote if c.maker_ask is not None else D("1")
        queue = _queue_ahead(c, books)
        e = events_by_ticker.get(c.event_ticker, {})
        return (-maker_recent, -hedge_recent, gap, queue, -_event_stat(e, "liquidity"), -c.hedge_depth, c.seconds_to_expiry)

    candidates.sort(key=prescore)
    sample_symbols: list[str] = []
    for c in candidates:
        if c.maker_symbol not in sample_symbols:
            sample_symbols.append(c.maker_symbol)
        if len(sample_symbols) >= live.FLOW_MAX_SYMBOLS:
            break
    tape = await live._sample_trade_flow(sample_symbols)

    def features(c: live.base.Candidate) -> dict[str, Any]:
        s = tape.get(c.maker_symbol, {})
        desired = _d(s.get("buyer_maker_qty")) if c.maker_outcome == "YES" else _d(s.get("buyer_taker_qty"))
        total_tape = _d(s.get("total_qty"))
        maker_recent = recent.get(c.maker_symbol, D("0"))
        hedge_recent = recent.get(c.hedge_symbol, D("0"))
        gap = c.maker_ask-c.maker_quote if c.maker_ask is not None else D("1")
        queue = _queue_ahead(c, books)
        e = events_by_ticker.get(c.event_ticker, {})
        return {
            "maker_side_aggressor_qty_sample": desired,
            "total_trade_qty_sample": total_tape,
            "maker_contract_qty_recent": maker_recent,
            "hedge_contract_qty_recent": hedge_recent,
            "gap_to_ask": gap,
            "queue_ahead_qty": queue,
            "event_liquidity_usd": _event_stat(e, "liquidity"),
            "event_volume_usd": _event_stat(e, "volume"),
        }

    eligible: list[live.base.Candidate] = []
    dead = 0
    for c in candidates:
        f = features(c)
        if f["maker_side_aggressor_qty_sample"] > 0 or f["maker_contract_qty_recent"] >= MIN_RECENT_QTY:
            eligible.append(c)
        else:
            dead += 1

    def rank(c: live.base.Candidate) -> tuple[Any, ...]:
        f = features(c)
        # Improving to a fresh safe frontier has no queue ahead. A join is penalized
        # by the exact top quantity already ahead of us.
        return (
            -f["maker_side_aggressor_qty_sample"],
            -f["maker_contract_qty_recent"],
            -f["hedge_contract_qty_recent"],
            f["queue_ahead_qty"],
            f["gap_to_ask"],
            -f["event_liquidity_usd"],
            -f["event_volume_usd"],
            -c.hedge_depth,
            c.seconds_to_expiry,
        )

    eligible.sort(key=rank)
    top: list[dict[str, Any]] = []
    for c in eligible[:10]:
        f = features(c)
        top.append({**c.as_dict(), **{k: str(v) if isinstance(v, D) else v for k, v in f.items()}})
    diag = {
        **telemetry,
        "whole_contract_symbols": len(whole_symbols),
        "whole_books_ok": sum(1 for s in whole_symbols if statuses.get(s) == 200),
        "robust_whole_contract_candidates": len(candidates),
        "recently_traded_candidates": len(eligible),
        "dead_candidates_suppressed": dead,
        "recent_volume_window_seconds": VOLUME_WINDOW_SECONDS,
        "minimum_recent_maker_contract_qty": str(MIN_RECENT_QTY),
        "flow_sample_seconds": live.FLOW_SAMPLE_SECONDS,
        "selection_policy": "live maker-side aggressor flow > 30m maker volume > 30m hedge volume > queue ahead > ask proximity > liquidity > event volume > hedge depth > expiry",
        "top": top,
        "read_only": not live.base.live_armed(),
        "order_entry": False,
        "realized": False,
    }
    live.emit("FLOW_RANK_V2", recently_traded_candidates=len(eligible), dead_candidates_suppressed=dead,
              top=top[:5], order_entry=False, realized=False)
    return (eligible[0] if eligible else None), diag
