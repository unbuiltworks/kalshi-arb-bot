"""Print individual >=10bp Gemini/Kalshi 15m threshold-gap transitions.

Public settled data only. This module imports no order transport and cannot trade.
It exists to identify which assets/windows historically satisfied the live basis-risk
canary's fixed >=10bp protective threshold-gap gate.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import json
import logging

import httpx

from src.gemini_kalshi_basis_transition_analyze import analyze_transition
from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import match_settled_true_windows
from src.gemini_kalshi_threshold_dominance_7d import ASSETS, KALSHI_15M_SERIES, _kalshi_series
from src.gemini_kalshi_threshold_dominance_study import classify

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_ge10_transition_rows")


def _dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _d(value):
    try:
        return D(str(value))
    except Exception:
        return None


async def run(hours: int, gemini_limit: int, max_window_end_skew_s: float) -> None:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        gemini_task = asyncio.create_task(_gemini_settled(http, gemini_limit))
        kalshi_tasks = {
            asset: asyncio.create_task(_kalshi_series(http, hours=hours, series_ticker=series))
            for asset, series in KALSHI_15M_SERIES.items()
        }
        gemini = await gemini_task
        kalshi_by_asset = {asset: await task for asset, task in kalshi_tasks.items()}

    kalshi = [market for asset in ASSETS for market in kalshi_by_asset.get(asset, [])]
    matched = match_settled_true_windows(gemini, kalshi, max_window_end_skew_s)
    event_by_ticker = {str(e.get("ticker") or ""): e for e in gemini}
    market_by_ticker = {str(m.get("ticker") or ""): m for m in kalshi}

    enriched = []
    for row in matched:
        event = event_by_ticker.get(str(row.gemini_ticker))
        market = market_by_ticker.get(str(row.kalshi_ticker))
        if event is not None and market is not None:
            enriched.append(classify(row, event, market))

    by_asset = defaultdict(list)
    for row in enriched:
        by_asset[str(row.get("asset") or "").upper()].append(row)

    selected = []
    for asset, rows in by_asset.items():
        ordered = sorted(rows, key=lambda r: _dt(r.get("expiry_utc")) or datetime.min)
        for previous, current in zip(ordered, ordered[1:]):
            transition = analyze_transition(previous, current)
            if not transition.get("evidence_complete"):
                continue
            strike = _d(current.get("gemini_strike"))
            gap = _d(transition.get("current_threshold_gap_value"))
            if strike in (None, D("0")) or gap is None:
                continue
            gap_bps = abs(gap) / strike * D("10000")
            if gap_bps < D("10"):
                continue
            payload = {
                "asset": asset,
                "previous_expiry_utc": previous.get("expiry_utc"),
                "current_expiry_utc": current.get("expiry_utc"),
                "previous_gemini_ticker": previous.get("gemini_ticker"),
                "previous_kalshi_ticker": previous.get("kalshi_ticker"),
                "current_gemini_ticker": current.get("gemini_ticker"),
                "current_kalshi_ticker": current.get("kalshi_ticker"),
                "gemini_strike": current.get("gemini_strike"),
                "kalshi_target": current.get("kalshi_target"),
                "threshold_gap_value": transition.get("current_threshold_gap_value"),
                "threshold_gap_abs_bps": str(gap_bps),
                "dominance_safe_direction": current.get("dominance_safe_direction_shared_value"),
                "previous_terminal_basis_value": transition.get("previous_terminal_basis_value"),
                "inheritance_residual_abs_bps": transition.get("inheritance_residual_abs_bps"),
                "current_terminal_basis_value": transition.get("current_terminal_basis_value"),
                "current_basis_same_sign_as_gap": transition.get("current_basis_same_sign_as_gap"),
                "current_basis_strictly_overshoots_gap": transition.get("current_basis_strictly_overshoots_gap"),
                "actual_dominance_breach": transition.get("actual_dominance_breach"),
                "gemini_outcome": current.get("gemini_outcome"),
                "kalshi_outcome": current.get("kalshi_outcome"),
                "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }
            selected.append(payload)
            logger.warning("CRYPTO_GE10_TRANSITION %s", json.dumps(payload, separators=(",", ":"), default=str))

    counts = defaultdict(int)
    for row in selected:
        counts[row["asset"]] += 1
    logger.warning(
        "CRYPTO_GE10_TRANSITION_SUMMARY %s",
        json.dumps({
            "requested_hours": hours,
            "matched_windows": len(enriched),
            "ge10_transitions": len(selected),
            "by_asset": dict(counts),
            "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":"), default=str),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=168)
    parser.add_argument("--gemini-limit", type=int, default=5000)
    parser.add_argument("--max-window-end-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.hours, args.gemini_limit, args.max_window_end_skew_s))


if __name__ == "__main__":
    main()
