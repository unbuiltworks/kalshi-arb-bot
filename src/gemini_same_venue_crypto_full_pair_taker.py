"""Complete-pair discovery for same-Gemini q=1 FOK/FOK threshold arbitrage.

For every exact same-event/source/expiry HI threshold pair L < H:
BUY YES(L) + BUY NO(H) has a deterministic payout floor of $1. Adjacency is not
required. This module is discovery only; execution/recovery remains owned by the
hardened taker q1 transport.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

import httpx

from src import gemini_same_venue_crypto_ladder_scan as scan
from src import gemini_same_venue_crypto_taker_q1 as base
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto

D = Decimal
ONE = D("1")
TARGET = D("0.03")


async def discover() -> tuple[dict[str, Any] | None, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        strict = [(event, scan.ladder_contracts(event)) for event in events]
        symbols = {
            scan._symbol(c)
            for _, contracts in strict
            for c in contracts
            if base.live.base.whole_contract_only(c)
        }
        books, statuses = await scan._books(http, symbols)

    rows: list[dict[str, Any]] = []
    adjacent_count = 0
    pair_count = 0
    for event, contracts0 in strict:
        contracts = [c for c in contracts0 if base.live.base.whole_contract_only(c)]
        if len(contracts) < 2:
            continue
        adjacent_count += len(contracts) - 1
        pairs = scan.monotone_pairs(contracts)
        pair_count += len(pairs)
        for low, high in pairs:
            ls, hs = scan._symbol(low), scan._symbol(high)
            lb, hb = books.get(ls), books.get(hs)
            if not isinstance(lb, Mapping) or not isinstance(hb, Mapping):
                continue
            lo = scan.outcome_book(scan.top(lb), "YES")
            hi = scan.outcome_book(scan.top(hb), "NO")
            la, ld = lo.get("ask"), lo.get("ask_qty")
            ha, hd = hi.get("ask"), hi.get("ask_qty")
            if not all(isinstance(x, D) for x in (la, ld, ha, hd)):
                continue
            assert isinstance(la, D) and isinstance(ld, D) and isinstance(ha, D) and isinstance(hd, D)
            if ld < ONE or hd < ONE:
                continue
            locked = base._taker_profit(la, ha)
            if locked < TARGET:
                continue
            rows.append({
                "event_ticker": str(event.get("ticker") or ""),
                "event_title": str(event.get("title") or ""),
                "expiry": str(low.get("_expiry") or ""),
                "source": str(low.get("_source") or ""),
                "low_symbol": ls,
                "high_symbol": hs,
                "low_strike": str(low.get("_strike")),
                "high_strike": str(high.get("_strike")),
                "threshold_gap": str(D(str(high.get("_strike"))) - D(str(low.get("_strike")))),
                "low_yes_ask": str(la),
                "low_yes_depth": str(ld),
                "high_no_ask": str(ha),
                "high_no_depth": str(hd),
                "locked_profit": str(locked),
                "event_volume": str(event.get("volume") or "0"),
                "event_liquidity": str(event.get("liquidity") or "0"),
                "classification": base.CLASSIFICATION,
                "guaranteed_payout_floor_before_fees": "1",
                "pair_scope": "ALL_CERTIFIED_L_LT_H",
                "realized": False,
            })

    rows.sort(key=lambda r: (
        -D(str(r["locked_profit"])),
        -min(D(str(r["low_yes_depth"])), D(str(r["high_no_depth"]))),
        -D(str(r.get("event_liquidity") or "0")),
        -D(str(r.get("event_volume") or "0")),
    ))
    diag = {
        "strict_adjacent_pairs": adjacent_count,
        "strict_monotone_pairs": pair_count,
        "books_requested": len(symbols),
        "books_ok": sum(1 for s in symbols if statuses.get(s) == 200),
        "taker_taker_3c_candidates": len(rows),
        "pair_scope": "ALL_CERTIFIED_L_LT_H",
        "top": rows[:5],
        "order_entry": False,
        "realized": False,
    }
    base.live.emit("TAKER_TAKER_SCAN", **diag)
    return (rows[0] if rows else None), diag
