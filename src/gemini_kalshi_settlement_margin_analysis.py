"""Read-only settlement-basis analysis by final distance from strike.

This asks the execution-relevant question left by raw agreement rate: how often do
Gemini and Kalshi outcomes disagree once Gemini's final settlement is at least N basis
points away from its strike? It uses the corrected true-window matcher (Gemini ticker
endpoint vs Kalshi close_time) and the four series-scoped Kalshi queries only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

import httpx

from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows

logger = logging.getLogger("kalshi_arb.gemini_kalshi_settlement_margin_analysis")
THRESHOLDS = (0.0, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0)
BINS = (
    (0.0, 0.1),
    (0.1, 0.25),
    (0.25, 0.5),
    (0.5, 1.0),
    (1.0, 2.0),
    (2.0, 5.0),
    (5.0, 10.0),
    (10.0, float("inf")),
)


def _summary(rows):
    usable = [r for r in rows if r.gemini_margin_bps is not None]
    disagreements = [r for r in usable if not r.agrees]

    threshold_rows = []
    for threshold in THRESHOLDS:
        cohort = [r for r in usable if abs(float(r.gemini_margin_bps)) >= threshold]
        bad = [r for r in cohort if not r.agrees]
        threshold_rows.append({
            "min_abs_gemini_margin_bps": threshold,
            "matched": len(cohort),
            "agreements": len(cohort) - len(bad),
            "disagreements": len(bad),
            "agreement_rate": ((len(cohort) - len(bad)) / len(cohort)) if cohort else None,
            "by_asset": {
                asset: {
                    "matched": sum(1 for r in cohort if r.asset == asset),
                    "disagreements": sum(1 for r in bad if r.asset == asset),
                }
                for asset in ("BTC", "ETH", "SOL", "XRP")
            },
        })

    bins = []
    for lo, hi in BINS:
        cohort = [
            r for r in usable
            if abs(float(r.gemini_margin_bps)) >= lo
            and (abs(float(r.gemini_margin_bps)) < hi if hi != float("inf") else True)
        ]
        bad = [r for r in cohort if not r.agrees]
        bins.append({
            "abs_margin_bps_bin": f"[{lo},{'inf' if hi == float('inf') else hi})",
            "matched": len(cohort),
            "disagreements": len(bad),
            "agreement_rate": ((len(cohort) - len(bad)) / len(cohort)) if cohort else None,
        })

    bad_abs = sorted(abs(float(r.gemini_margin_bps)) for r in disagreements)
    return {
        "matched_with_margin": len(usable),
        "agreements": len(usable) - len(disagreements),
        "disagreements": len(disagreements),
        "max_abs_margin_bps_among_disagreements": max(bad_abs) if bad_abs else None,
        "disagreement_abs_margin_bps": bad_abs,
        "threshold_cohorts": threshold_rows,
        "margin_bins": bins,
        "window_identity": "GEMINI_TICKER_TIME_VS_KALSHI_CLOSE_TIME",
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
    }


async def run(hours: int, gemini_limit: int, max_expiry_skew_s: float) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled_paced(http, hours),
        )
    rows = match_settled_true_windows(gemini, kalshi, max_expiry_skew_s)
    logger.info(
        "GEMINI_KALSHI_MARGIN_AGREEMENT_SUMMARY %s",
        json.dumps(_summary(rows), separators=(",", ":")),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=2000)
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.hours, args.gemini_limit, args.max_expiry_skew_s))


if __name__ == "__main__":
    main()
