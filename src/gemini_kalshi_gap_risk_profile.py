"""Read-only threshold-gap risk profile for Gemini/Kalshi 15m transitions.

Measures historical settlement-basis behavior by asset and threshold-gap bucket.
No order transport is imported; this module cannot authorize or submit trades.
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
logger = logging.getLogger("kalshi_arb.gemini_kalshi_gap_risk_profile")


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


def bucket(bps: Decimal) -> str:
    if bps >= D("10"):
        return "ge_10bp"
    if bps >= D("5"):
        return "5_to_lt_10bp"
    if bps >= D("3"):
        return "3_to_lt_5bp"
    if bps >= D("2"):
        return "2_to_lt_3bp"
    if bps >= D("1"):
        return "1_to_lt_2bp"
    return "lt_1bp"


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

    stats = defaultdict(lambda: defaultdict(lambda: {
        "transitions": 0,
        "same_adverse_sign": 0,
        "strict_basis_overshoots": 0,
        "actual_dominance_breaches": 0,
        "venue_outcome_disagreements": 0,
        "safe_direction_matches_gap": 0,
    }))
    max_gap = defaultdict(lambda: D("0"))
    transition_count = 0

    for asset, rows in by_asset.items():
        ordered = sorted(rows, key=lambda r: _dt(r.get("expiry_utc")) or datetime.min)
        for previous, current in zip(ordered, ordered[1:]):
            t = analyze_transition(previous, current)
            if not t.get("evidence_complete"):
                continue
            strike = _d(current.get("gemini_strike"))
            gap = _d(t.get("current_threshold_gap_value"))
            if strike in (None, D("0")) or gap is None:
                continue
            gap_bps = abs(gap) / strike * D("10000")
            max_gap[asset] = max(max_gap[asset], gap_bps)
            name = bucket(gap_bps)
            s = stats[asset][name]
            s["transitions"] += 1
            s["same_adverse_sign"] += int(bool(t.get("current_basis_same_sign_as_gap")))
            s["strict_basis_overshoots"] += int(bool(t.get("current_basis_strictly_overshoots_gap")))
            s["actual_dominance_breaches"] += int(bool(t.get("actual_dominance_breach")))
            g_out = str(current.get("gemini_outcome") or "").lower()
            k_out = str(current.get("kalshi_outcome") or "").lower()
            s["venue_outcome_disagreements"] += int(bool(g_out and k_out and g_out != k_out))
            safe = str(current.get("dominance_safe_direction_shared_value") or "")
            expected = "GEMINI_YES_KALSHI_NO" if gap > 0 else "KALSHI_YES_GEMINI_NO" if gap < 0 else ""
            s["safe_direction_matches_gap"] += int(bool(expected and safe == expected))
            transition_count += 1

    payload = {
        "requested_hours": hours,
        "matched_windows": len(enriched),
        "complete_transitions": transition_count,
        "max_gap_abs_bps_by_asset": {k: str(v) for k, v in sorted(max_gap.items())},
        "by_asset_gap_bucket": {asset: dict(buckets) for asset, buckets in sorted(stats.items())},
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    logger.warning("CRYPTO_GAP_RISK_PROFILE %s", json.dumps(payload, separators=(",", ":"), default=str))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=336)
    p.add_argument("--gemini-limit", type=int, default=10000)
    p.add_argument("--max-window-end-skew-s", type=float, default=30.0)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.hours, args.gemini_limit, args.max_window_end_skew_s))


if __name__ == "__main__":
    main()
