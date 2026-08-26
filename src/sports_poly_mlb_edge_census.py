"""Continuous zero-order-entry same-Polymarket MLB locked-edge census.

Reuses the existing current MLB discovery, participation certification and public
book fetchers. This entry point deliberately does NOT construct an authenticated
Polymarket client, run orphan cleanup, reserve capital, preview orders, or create
orders.

For each participation-certified monotone threshold pair L < H, the q1 taker/taker
portfolio BUY YES(L) + BUY NO(H) has a deterministic payout floor of $1 under the
already-certified same-family identity. We measure displayed immediate executable
cost after the audited current Polymarket US taker fees at >=1c/2c/3c.

These broad public-book snapshots measure incidence/depth. They are not used to
claim sub-second lifetime; WS/event-driven lifetime instrumentation remains a
separate census requirement in ARB_EDGE_CENSUS_2026-08.md.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, ROUND_FLOOR
import json
import time
from typing import Any, Mapping

from src.sports_poly_current_fees import taker_fee
from src.sports_poly_direct_mlb_campaign_q1 import discover_direct_mlb_pairs
from src.sports_poly_mlb_participation_gate import certify_pairs
from src.sports_poly_structural_monotone import fetch_books
import src.sports_kp_poly_relay_campaign_q1_v4 as v4

D = Decimal
ONE = D("1")
THRESHOLDS = (D("0.01"), D("0.02"), D("0.03"))


def _count(rows: list[dict[str, Any]], threshold: D) -> int:
    return sum(D(str(row["locked_profit"])) >= threshold for row in rows)


def _top(rows: list[dict[str, Any]], threshold: D, limit: int = 8) -> list[dict[str, Any]]:
    return [row for row in rows if D(str(row["locked_profit"])) >= threshold][:limit]


async def snapshot() -> dict[str, Any]:
    snapshot_ts_ms = int(time.time() * 1000)
    feed, pairs, discovery = await discover_direct_mlb_pairs()
    ready_pairs, _certs, cert_summary = await certify_pairs(feed, pairs)
    slugs = {slug for pair in ready_pairs for slug in pair}
    books, quote_health = await fetch_books(slugs, concurrency=6) if slugs else ({}, {})

    rows: list[dict[str, Any]] = []
    for low_slug, high_slug in ready_pairs:
        low_book = books.get(low_slug)
        high_book = books.get(high_slug)
        if low_book is None or high_book is None:
            continue
        try:
            low_yes_ask, low_yes_depth = v4._ask(low_book, "YES")
            high_no_ask, high_no_depth = v4._ask(high_book, "NO")
            low_yes_ask = D(str(low_yes_ask))
            high_no_ask = D(str(high_no_ask))
            low_yes_depth = D(str(low_yes_depth))
            high_no_depth = D(str(high_no_depth))
        except Exception:
            continue
        if low_yes_depth < ONE or high_no_depth < ONE:
            continue
        fee_low = taker_fee(1, low_yes_ask)
        fee_high = taker_fee(1, high_no_ask)
        all_in = low_yes_ask + high_no_ask + fee_low + fee_high
        locked = ONE - all_in
        if locked < D("0.01"):
            continue
        common_depth = min(low_yes_depth, high_no_depth)
        conservative_q1_units = int(common_depth.to_integral_value(rounding=ROUND_FLOOR))
        rows.append({
            "edge_key": f"{low_slug}|{high_slug}|LOW_YES+HIGH_NO",
            "snapshot_ts_ms": snapshot_ts_ms,
            "low_slug": low_slug,
            "high_slug": high_slug,
            "low_yes_ask": str(low_yes_ask),
            "low_yes_depth": str(low_yes_depth),
            "high_no_ask": str(high_no_ask),
            "high_no_depth": str(high_no_depth),
            "low_taker_fee_q1": str(fee_low),
            "high_taker_fee_q1": str(fee_high),
            "all_in_cost_q1": str(all_in),
            "locked_profit": str(locked),
            "visible_common_depth": str(common_depth),
            "conservative_q1_units_at_top": conservative_q1_units,
            "classification": "TAKER_LOCKED_CENSUS_OBSERVATION",
            "pair_scope": "PARTICIPATION_CERTIFIED_SAME_POLYMARKET_MLB_MONOTONE",
            "order_entry": False,
            "realized": False,
        })

    rows.sort(key=lambda row: (-D(str(row["locked_profit"])), -D(str(row["visible_common_depth"]))))
    out = {
        "snapshot_ts_ms": snapshot_ts_ms,
        "strict_markets": len(feed),
        "strict_pairs": len(pairs),
        "participation_ready_pairs": len(ready_pairs),
        "books_requested": len(slugs),
        "discovery": discovery,
        "certification": cert_summary,
        "quote_health": quote_health,
        "taker_locked_1c_candidates": _count(rows, D("0.01")),
        "taker_locked_2c_candidates": _count(rows, D("0.02")),
        "taker_locked_3c_candidates": _count(rows, D("0.03")),
        "census_top_1c": _top(rows, D("0.01")),
        "census_top_2c": _top(rows, D("0.02")),
        "census_top_3c": _top(rows, D("0.03")),
        "census_thresholds_after_fee": ["0.01", "0.02", "0.03"],
        "capacity_note": "visible top depth only; overlapping pairs sharing a book must not be summed as independent final capacity",
        "lifetime_note": "public broad-book snapshot; not valid for sub-second lifetime measurement",
        "authenticated_client_constructed": False,
        "capital_reserved": False,
        "order_preview": False,
        "order_entry": False,
        "realized": False,
    }
    print("SPORTS_POLY_EDGE_CENSUS_SNAPSHOT " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


async def run_forever(interval_seconds: float) -> None:
    cycle = 0
    while True:
        cycle += 1
        started = time.monotonic()
        try:
            out = await snapshot()
            print("SPORTS_POLY_EDGE_CENSUS_HEARTBEAT " + json.dumps({
                "cycle": cycle,
                "scan_elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                "participation_ready_pairs": out.get("participation_ready_pairs", 0),
                "taker_locked_1c_candidates": out.get("taker_locked_1c_candidates", 0),
                "taker_locked_2c_candidates": out.get("taker_locked_2c_candidates", 0),
                "taker_locked_3c_candidates": out.get("taker_locked_3c_candidates", 0),
                "lifetime_measurement": "NOT_FROM_BROAD_SNAPSHOT",
                "public_print_proxy": "PENDING_WS_INSTRUMENTATION",
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str), flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print("SPORTS_POLY_EDGE_CENSUS_ERROR " + json.dumps({
                "cycle": cycle,
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, interval_seconds - elapsed))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    args = parser.parse_args()
    asyncio.run(run_forever(max(5.0, args.interval_seconds)))


if __name__ == "__main__":
    main()
