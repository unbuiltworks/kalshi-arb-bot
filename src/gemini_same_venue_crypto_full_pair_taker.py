"""Complete-pair discovery for same-Gemini q=1 FOK/FOK threshold arbitrage.

For every exact same-event/source/expiry HI threshold pair L < H:
BUY YES(L) + BUY NO(H) has a deterministic payout floor of $1. Adjacency is not
required. Every contract admitted by ladder_contracts is q=1-compatible: its
minimum is <=1 and its quantity increment divides 1 exactly.

Unlike the resting maker lane, this immediate lane does NOT require quantityMinimum
and quantityIncrement both equal 1. Gemini documents FOK as full-fill-or-full-cancel
with no partial fills, so q=1 is safe on fractional-grid contracts as long as q=1 is
valid for the instrument. Execution/recovery remains owned by the hardened taker q1
transport and still fail-closes on any unexpected state.

August 2026 edge-census instrumentation is read-only and does not widen execution
admission. It records >=1c / >=2c / >=3c after-fee snapshots and common displayed
top depth. Only >=3c rows remain eligible for the existing q1 executor.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR
import time
from typing import Any, Mapping

import httpx

from src import gemini_same_venue_crypto_ladder_scan as scan
from src import gemini_same_venue_crypto_taker_q1 as base
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto

D = Decimal
ONE = D("1")
TARGET = D("0.03")
CENSUS_THRESHOLDS = (D("0.01"), D("0.02"), D("0.03"))


def _threshold_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "1c": sum(D(str(r["locked_profit"])) >= D("0.01") for r in rows),
        "2c": sum(D(str(r["locked_profit"])) >= D("0.02") for r in rows),
        "3c": sum(D(str(r["locked_profit"])) >= D("0.03") for r in rows),
    }


def _top_at(rows: list[dict[str, Any]], threshold: D, limit: int = 8) -> list[dict[str, Any]]:
    return [r for r in rows if D(str(r["locked_profit"])) >= threshold][:limit]


async def discover() -> tuple[dict[str, Any] | None, dict[str, Any]]:
    snapshot_ts_ms = int(time.time() * 1000)
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        # ladder_contracts already enforces exact same source/expiry within the event
        # and q1 compatibility (_q1_ok). Do not apply maker-only whole-contract rules.
        strict = [(event, scan.ladder_contracts(event)) for event in events]
        symbols = {scan._symbol(c) for _, contracts in strict for c in contracts}
        books, statuses = await scan._books(http, symbols)

    census_rows: list[dict[str, Any]] = []
    adjacent_count = 0
    pair_count = 0
    contract_count = 0
    fractional_grid_contracts = 0
    for event, contracts in strict:
        if len(contracts) < 2:
            continue
        contract_count += len(contracts)
        fractional_grid_contracts += sum(1 for c in contracts if not base.live.base.whole_contract_only(c))
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
            if locked < D("0.01"):
                continue
            common_depth = min(ld, hd)
            conservative_q1_units = int(common_depth.to_integral_value(rounding=ROUND_FLOOR))
            census_rows.append({
                "edge_key": f"{str(event.get('ticker') or '')}|{ls}|{hs}",
                "snapshot_ts_ms": snapshot_ts_ms,
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
                "visible_common_depth": str(common_depth),
                "conservative_q1_units_at_top": conservative_q1_units,
                "locked_profit": str(locked),
                "event_volume": str(event.get("volume") or "0"),
                "event_liquidity": str(event.get("liquidity") or "0"),
                "classification": "TAKER_LOCKED_CENSUS_OBSERVATION",
                "guaranteed_payout_floor_before_fees": "1",
                "pair_scope": "ALL_Q1_COMPATIBLE_CERTIFIED_L_LT_H",
                "order_entry": False,
                "realized": False,
            })

    census_rows.sort(key=lambda r: (
        -D(str(r["locked_profit"])),
        -D(str(r["visible_common_depth"])),
        -D(str(r.get("event_liquidity") or "0")),
        -D(str(r.get("event_volume") or "0")),
    ))
    execution_rows = [r for r in census_rows if D(str(r["locked_profit"])) >= TARGET]
    counts = _threshold_counts(census_rows)
    diag = {
        "snapshot_ts_ms": snapshot_ts_ms,
        "q1_compatible_contracts": contract_count,
        "fractional_grid_contracts_included_for_fok": fractional_grid_contracts,
        "strict_adjacent_pairs": adjacent_count,
        "strict_monotone_pairs": pair_count,
        "books_requested": len(symbols),
        "books_ok": sum(1 for s in symbols if statuses.get(s) == 200),
        "taker_locked_1c_candidates": counts["1c"],
        "taker_locked_2c_candidates": counts["2c"],
        "taker_locked_3c_candidates": counts["3c"],
        # Backward-compatible field used by existing execution diagnostics.
        "taker_taker_3c_candidates": counts["3c"],
        "pair_scope": "ALL_Q1_COMPATIBLE_CERTIFIED_L_LT_H",
        "census_thresholds_after_fee": ["0.01", "0.02", "0.03"],
        "census_top_1c": _top_at(census_rows, D("0.01")),
        "census_top_2c": _top_at(census_rows, D("0.02")),
        "census_top_3c": _top_at(census_rows, D("0.03")),
        "capacity_note": "visible depth only; do not sum overlapping pairs as independent final capacity",
        "lifetime_note": "REST broad snapshot; not valid for sub-second lifetime measurement",
        "order_entry": False,
        "realized": False,
    }
    base.live.emit("EDGE_CENSUS_LOCKED_SNAPSHOT", **diag)
    base.live.emit("TAKER_TAKER_SCAN", **diag)
    return (execution_rows[0] if execution_rows else None), diag
