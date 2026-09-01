"""Fill-probability selector v3 with suppressed-candidate activity diagnostics.

Execution admission is intentionally unchanged from v2: a maker candidate still
needs exact-contract recent volume or observed maker-side aggressor flow. The new
information is diagnostic: for candidates that fail that gate, report event-wide
30-minute contract volume, sibling volume, ask distance, queue, liquidity and volume.
That tells us whether a rejected quote is truly dead or simply an untraded strike in
an otherwise active ladder before we change live admission.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

import httpx

from src import gemini_same_venue_crypto_flow_v2 as base
from src import gemini_same_venue_crypto_ladder_scan as scan
from src import gemini_same_venue_crypto_maker_q1_wsfirst as live
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto

D = Decimal


async def discover_flow_ranked_v3() -> tuple[live.base.Candidate | None, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        contracts = live.base._contract_map(events)
        whole_symbols = {s for s, c in contracts.items() if live.base.whole_contract_only(c)}
        books, statuses = await scan._books(http, whole_symbols)
    actionable, joins, telemetry = scan.score(events, books)
    candidates = [c for row in actionable + joins if (c := live.base.candidate_from_row(row, contracts)) is not None]
    events_by_ticker = {str(e.get("ticker") or ""): e for e in events}
    event_symbols = {
        str(e.get("ticker") or ""): [scan._symbol(c) for c in scan.ladder_contracts(e)]
        for e in events
    }
    recent = await base._recent_contract_volume({c.event_ticker for c in candidates}) if candidates else {}

    def event_recent(ticker: str) -> D:
        return sum((recent.get(s, D("0")) for s in event_symbols.get(ticker, [])), D("0"))

    def prescore(c: live.base.Candidate) -> tuple[Any, ...]:
        maker_recent = recent.get(c.maker_symbol, D("0"))
        hedge_recent = recent.get(c.hedge_symbol, D("0"))
        ev_recent = event_recent(c.event_ticker)
        gap = c.maker_ask-c.maker_quote if c.maker_ask is not None else D("1")
        queue = base._queue_ahead(c, books)
        e = events_by_ticker.get(c.event_ticker, {})
        return (-maker_recent, -hedge_recent, -ev_recent, gap, queue,
                -base._event_stat(e, "liquidity"), -c.hedge_depth, c.seconds_to_expiry)

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
        desired = base._d(s.get("buyer_maker_qty")) if c.maker_outcome == "YES" else base._d(s.get("buyer_taker_qty"))
        total_tape = base._d(s.get("total_qty"))
        maker_recent = recent.get(c.maker_symbol, D("0"))
        hedge_recent = recent.get(c.hedge_symbol, D("0"))
        gap = c.maker_ask-c.maker_quote if c.maker_ask is not None else D("1")
        queue = base._queue_ahead(c, books)
        e = events_by_ticker.get(c.event_ticker, {})
        return {
            "maker_side_aggressor_qty_sample": desired,
            "total_trade_qty_sample": total_tape,
            "maker_contract_qty_recent": maker_recent,
            "hedge_contract_qty_recent": hedge_recent,
            "event_contract_qty_recent": event_recent(c.event_ticker),
            "maker_has_ask": c.maker_ask is not None,
            "gap_to_ask": gap,
            "queue_ahead_qty": queue,
            "event_liquidity_usd": base._event_stat(e, "liquidity"),
            "event_volume_usd": base._event_stat(e, "volume"),
        }

    eligible: list[live.base.Candidate] = []
    suppressed: list[live.base.Candidate] = []
    for c in candidates:
        f = features(c)
        if f["maker_side_aggressor_qty_sample"] > 0 or f["maker_contract_qty_recent"] >= base.MIN_RECENT_QTY:
            eligible.append(c)
        else:
            suppressed.append(c)

    def rank(c: live.base.Candidate) -> tuple[Any, ...]:
        f = features(c)
        return (
            -f["maker_side_aggressor_qty_sample"],
            -f["maker_contract_qty_recent"],
            -f["hedge_contract_qty_recent"],
            f["queue_ahead_qty"],
            f["gap_to_ask"],
            -f["event_contract_qty_recent"],
            -f["event_liquidity_usd"],
            -f["event_volume_usd"],
            -c.hedge_depth,
            c.seconds_to_expiry,
        )

    def suppressed_rank(c: live.base.Candidate) -> tuple[Any, ...]:
        f = features(c)
        return (
            -f["event_contract_qty_recent"],
            -f["hedge_contract_qty_recent"],
            0 if f["maker_has_ask"] else 1,
            f["gap_to_ask"],
            f["queue_ahead_qty"],
            -f["event_liquidity_usd"],
            -f["event_volume_usd"],
            -c.hedge_depth,
            c.seconds_to_expiry,
        )

    eligible.sort(key=rank)
    suppressed.sort(key=suppressed_rank)

    def display(c: live.base.Candidate) -> dict[str, Any]:
        f = features(c)
        return {**c.as_dict(), **{k: str(v) if isinstance(v, D) else v for k, v in f.items()}}

    top = [display(c) for c in eligible[:10]]
    suppressed_top = [display(c) for c in suppressed[:10]]
    diag = {
        **telemetry,
        "whole_contract_symbols": len(whole_symbols),
        "whole_books_ok": sum(1 for s in whole_symbols if statuses.get(s) == 200),
        "robust_whole_contract_candidates": len(candidates),
        "recently_traded_candidates": len(eligible),
        "dead_candidates_suppressed": len(suppressed),
        "recent_volume_window_seconds": base.VOLUME_WINDOW_SECONDS,
        "minimum_recent_maker_contract_qty": str(base.MIN_RECENT_QTY),
        "flow_sample_seconds": live.FLOW_SAMPLE_SECONDS,
        "selection_policy": "exact maker flow/volume admission unchanged; event-wide recent volume is diagnostic only",
        "top": top,
        "suppressed_top": suppressed_top,
        "read_only": not live.base.live_armed(),
        "order_entry": False,
        "realized": False,
    }
    live.emit("FLOW_RANK_V3", recently_traded_candidates=len(eligible),
              dead_candidates_suppressed=len(suppressed), top=top[:5],
              suppressed_top=suppressed_top[:5], order_entry=False, realized=False)
    return (eligible[0] if eligible else None), diag
