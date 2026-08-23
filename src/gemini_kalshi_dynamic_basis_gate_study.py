"""Shadow-only confidence study for a possible dynamic basis-risk gate.

This is research only and cannot authorize trading. It asks whether historical
threshold-safe-direction adverse settlement mismatch is statistically bounded below
an observed quote lock. The live >=10bp rule is not changed by this module.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import json
import logging
import math

import httpx

from src.gemini_kalshi_basis_transition_analyze import analyze_transition
from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import match_settled_true_windows
from src.gemini_kalshi_threshold_dominance_7d import ASSETS, KALSHI_15M_SERIES, _kalshi_series
from src.gemini_kalshi_threshold_dominance_study import classify

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_dynamic_basis_gate_study")


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


def zero_failure_upper_95(n: int) -> Decimal | None:
    if n <= 0:
        return None
    # Exact one-sided Clopper-Pearson upper limit for zero observed failures.
    return D(str(1.0 - math.pow(0.05, 1.0 / n)))


async def run(hours: int, gemini_limit: int, max_window_end_skew_s: float, safety_margin: Decimal) -> None:
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        gemini_task = asyncio.create_task(_gemini_settled(http, gemini_limit))
        kalshi_tasks = {
            asset: asyncio.create_task(_kalshi_series(http, hours=hours, series_ticker=series))
            for asset, series in KALSHI_15M_SERIES.items()
        }
        gemini = await gemini_task
        kalshi_by_asset = {asset: await task for asset, task in kalshi_tasks.items()}

    kalshi = [m for asset in ASSETS for m in kalshi_by_asset.get(asset, [])]
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

    samples = []
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
            samples.append({
                "asset": asset,
                "gap_abs_bps": abs(gap) / strike * D("10000"),
                "adverse": bool(t.get("actual_dominance_breach")),
            })

    rows = []
    for minimum_gap in (D("0"), D("0.5"), D("1"), D("2"), D("3"), D("5"), D("10")):
        qualified = [x for x in samples if x["gap_abs_bps"] >= minimum_gap]
        adverse = sum(1 for x in qualified if x["adverse"])
        upper = zero_failure_upper_95(len(qualified)) if adverse == 0 else None
        minimum_edge = (upper + safety_margin) if upper is not None else None
        row = {
            "minimum_gap_bps": str(minimum_gap),
            "historical_samples": len(qualified),
            "observed_adverse_breaches": adverse,
            "zero_failure_upper_95_probability": str(upper) if upper is not None else None,
            "safety_margin_probability": str(safety_margin),
            "minimum_locked_edge_for_shadow_test": str(minimum_edge) if minimum_edge is not None else None,
            "minimum_locked_edge_cents_per_dollar": str(minimum_edge * D("100")) if minimum_edge is not None else None,
            "live_authorized": False,
        }
        rows.append(row)

    logger.warning("CRYPTO_DYNAMIC_BASIS_GATE_STUDY %s", json.dumps({
        "matched_windows": len(enriched),
        "complete_transitions": len(samples),
        "rows": rows,
        "interpretation": "Research threshold only. A future live policy would additionally require quote-conditioned evidence, safe-direction match, existing freshness/skew/persistence/depth/fee/capital gates, explicit approval, and a separate bounded basis-risk budget.",
        "classification": "DYNAMIC_BASIS_GATE_SHADOW_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":"), default=str))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=336)
    p.add_argument("--gemini-limit", type=int, default=10000)
    p.add_argument("--max-window-end-skew-s", type=float, default=30.0)
    p.add_argument("--safety-margin", default="0.01")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.hours, args.gemini_limit, args.max_window_end_skew_s, D(args.safety_margin)))


if __name__ == "__main__":
    main()
