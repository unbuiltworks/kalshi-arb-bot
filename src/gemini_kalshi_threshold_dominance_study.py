"""Read-only Gemini/Kalshi 15m threshold-dominance and settlement-basis study.

The two venues can have different 15-minute reference thresholds even before their
settlement indexes (Gemini/Kaiko vs Kalshi/CF Benchmarks) are considered.  A same-
asset/same-window opposite-side pair is therefore not automatically complementary.

For every settled matched window this study records:
* Gemini strike and final settlement value;
* Kalshi target and, when supplied by the public API, expiration value;
* signed threshold gap and its size in basis points;
* which opposite-side orientation is dominance-safe under a shared settlement value;
* whether an observed venue-outcome disagreement is already explained by the
  different thresholds, without requiring any cross-index divergence; and
* numeric Kalshi-vs-Gemini final-value basis when both public values are available.

This module uses public market data only.  It imports no order transport, credentials,
or live execution path and can never place an order.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
import logging
from typing import Any

import httpx

from src.gemini_kalshi_fast_overlap import _gemini_source
from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_threshold_dominance_study")
ASSETS = ("BTC", "ETH", "SOL", "XRP")
DIR_KYES_GNO = "KALSHI_YES_GEMINI_NO"
DIR_GYES_KNO = "GEMINI_YES_KALSHI_NO"


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def kalshi_target(market: dict[str, Any]) -> Decimal | None:
    """Extract the one numeric threshold used by the binary market, fail closed."""
    strikes = [
        value
        for value in (_dec(market.get("floor_strike")), _dec(market.get("cap_strike")))
        if value is not None
    ]
    unique = sorted(set(strikes))
    functional = _dec(market.get("functional_strike"))
    if len(unique) == 1:
        return unique[0]
    if functional is not None and (not unique or functional in unique):
        return functional
    return None


def threshold_gap_bps(gemini_strike: Decimal, kalshi_target_value: Decimal) -> Decimal:
    """Signed K-target minus G-strike gap, normalized to the Gemini strike."""
    if gemini_strike == 0:
        raise ValueError("Gemini strike must be nonzero")
    return (kalshi_target_value - gemini_strike) / gemini_strike * D("10000")


def dominance_profile(gemini_strike: Decimal, kalshi_target_value: Decimal) -> dict[str, Any]:
    """Return orientation protected by threshold ordering under one shared final value.

    If K target > G strike, G-YES + K-NO always has at least one winner under a
    shared final value; the gap is a both-win interval.  K-YES + G-NO is the opposite:
    the gap is a both-lose interval.  Reverse the orientations when K target < G strike.
    """
    gap = kalshi_target_value - gemini_strike
    gap_bps = threshold_gap_bps(gemini_strike, kalshi_target_value)
    if gap > 0:
        safe, dangerous = DIR_GYES_KNO, DIR_KYES_GNO
    elif gap < 0:
        safe, dangerous = DIR_KYES_GNO, DIR_GYES_KNO
    else:
        safe, dangerous = "SYMMETRIC_EQUAL_THRESHOLD", "SYMMETRIC_EQUAL_THRESHOLD"
    return {
        "threshold_gap": str(gap),
        "threshold_gap_bps": str(gap_bps),
        "threshold_gap_abs_bps": str(abs(gap_bps)),
        "dominance_safe_direction_shared_value": safe,
        "dangerous_gap_direction_shared_value": dangerous,
    }


def _kalshi_outcome_from_value(value: Decimal, target: Decimal) -> str:
    # Kalshi 15m markets in this lane use a greater-than/at-least floor threshold.
    return "yes" if value >= target else "no"


def _disagreement_orientation(g_out: str, k_out: str) -> dict[str, str | None]:
    if g_out == "yes" and k_out == "no":
        return {
            "observed_disagreement": "GEMINI_YES_KALSHI_NO",
            "both_lose_orientation": DIR_KYES_GNO,
            "both_win_orientation": DIR_GYES_KNO,
        }
    if g_out == "no" and k_out == "yes":
        return {
            "observed_disagreement": "GEMINI_NO_KALSHI_YES",
            "both_lose_orientation": DIR_GYES_KNO,
            "both_win_orientation": DIR_KYES_GNO,
        }
    return {
        "observed_disagreement": None,
        "both_lose_orientation": None,
        "both_win_orientation": None,
    }


def classify(row: Any, event: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    g_strike = _dec(getattr(row, "gemini_strike", None))
    g_final = _dec(getattr(row, "gemini_settlement", None))
    k_target = kalshi_target(market)
    k_final = _dec(market.get("expiration_value"))
    agency, index = _gemini_source(event)

    payload: dict[str, Any] = {
        **row.as_dict(),
        "kalshi_target": str(k_target) if k_target is not None else None,
        "kalshi_expiration_value": str(k_final) if k_final is not None else None,
        "kalshi_strike_type": market.get("strike_type"),
        "gemini_source_agency": agency,
        "gemini_source_index": index,
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    payload.update(_disagreement_orientation(row.gemini_outcome, row.kalshi_outcome))

    if g_strike is not None and k_target is not None:
        payload.update(dominance_profile(g_strike, k_target))
    else:
        payload.update({
            "threshold_gap": None,
            "threshold_gap_bps": None,
            "threshold_gap_abs_bps": None,
            "dominance_safe_direction_shared_value": None,
            "dangerous_gap_direction_shared_value": None,
        })

    predicted_k = None
    threshold_only_explains = None
    requires_index_divergence = None
    if g_final is not None and k_target is not None:
        predicted_k = _kalshi_outcome_from_value(g_final, k_target)
        if not row.agrees:
            threshold_only_explains = predicted_k == row.kalshi_outcome
            requires_index_divergence = not threshold_only_explains
    payload["kalshi_outcome_using_gemini_final_shared_value"] = predicted_k
    payload["threshold_only_explains_observed_disagreement"] = threshold_only_explains
    payload["index_divergence_required_to_explain_observed_disagreement"] = requires_index_divergence

    if g_final not in (None, D("0")) and k_final is not None:
        basis_bps = (k_final - g_final) / g_final * D("10000")
        payload["kalshi_minus_gemini_final_value"] = str(k_final - g_final)
        payload["kalshi_minus_gemini_final_bps"] = str(basis_bps)
    else:
        payload["kalshi_minus_gemini_final_value"] = None
        payload["kalshi_minus_gemini_final_bps"] = None

    safe = payload.get("dominance_safe_direction_shared_value")
    both_lose = payload.get("both_lose_orientation")
    payload["observed_disagreement_harms_dominance_safe_direction"] = bool(
        not row.agrees and safe not in (None, "SYMMETRIC_EQUAL_THRESHOLD") and both_lose == safe
    )
    return payload


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_asset: dict[str, dict[str, int]] = defaultdict(lambda: {
        "matched": 0,
        "agreed": 0,
        "disagreed": 0,
        "threshold_only_explained": 0,
        "index_divergence_required": 0,
        "unknown": 0,
    })
    threshold_explained = 0
    index_required = 0
    unknown = 0
    safe_direction_harmed = 0
    gaps: list[Decimal] = []

    for row in rows:
        asset = str(row.get("asset") or "").upper()
        bucket = by_asset[asset]
        bucket["matched"] += 1
        if row.get("agrees"):
            bucket["agreed"] += 1
        else:
            bucket["disagreed"] += 1
            explained = row.get("threshold_only_explains_observed_disagreement")
            if explained is True:
                threshold_explained += 1
                bucket["threshold_only_explained"] += 1
            elif explained is False:
                index_required += 1
                bucket["index_divergence_required"] += 1
            else:
                unknown += 1
                bucket["unknown"] += 1
            if row.get("observed_disagreement_harms_dominance_safe_direction"):
                safe_direction_harmed += 1
        gap = _dec(row.get("threshold_gap_abs_bps"))
        if gap is not None:
            gaps.append(gap)

    disagreements = threshold_explained + index_required + unknown
    return {
        "matched_15m_windows": len(rows),
        "agreements": len(rows) - disagreements,
        "disagreements": disagreements,
        "threshold_only_explained_disagreements": threshold_explained,
        "index_divergence_required_disagreements": index_required,
        "unknown_disagreement_cause": unknown,
        "observed_disagreements_harming_dominance_safe_direction": safe_direction_harmed,
        "by_asset": {asset: by_asset.get(asset, {}) for asset in ASSETS},
        "max_abs_threshold_gap_bps": str(max(gaps)) if gaps else None,
        "median_abs_threshold_gap_bps": str(sorted(gaps)[len(gaps)//2]) if gaps else None,
        "interpretation": (
            "threshold_only_explained means the Gemini final value itself produces the Kalshi "
            "reported outcome when evaluated against Kalshi's different target; no index "
            "divergence is needed to explain that outcome disagreement"
        ),
        "classification": "THRESHOLD_AND_INDEX_BASIS_RESEARCH_ONLY",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }


async def run(hours: int, gemini_limit: int, max_window_end_skew_s: float) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled_paced(http, hours),
        )

    matched = match_settled_true_windows(gemini, kalshi, max_window_end_skew_s)
    event_by_ticker = {str(e.get("ticker") or ""): e for e in gemini}
    market_by_ticker = {str(m.get("ticker") or ""): m for m in kalshi}
    enriched: list[dict[str, Any]] = []
    for row in matched:
        event = event_by_ticker.get(str(row.gemini_ticker))
        market = market_by_ticker.get(str(row.kalshi_ticker))
        if event is None or market is None:
            continue
        payload = classify(row, event, market)
        enriched.append(payload)
        if not row.agrees:
            logger.warning("CRYPTO_THRESHOLD_DISAGREEMENT %s", json.dumps(payload, separators=(",", ":"), default=str))

    logger.info(
        "CRYPTO_THRESHOLD_DOMINANCE_SUMMARY %s",
        json.dumps(summarize(enriched), separators=(",", ":"), default=str),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=2500)
    parser.add_argument("--max-window-end-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    if not 1 <= args.hours <= 168:
        raise SystemExit("--hours must be 1..168")
    if not 100 <= args.gemini_limit <= 10000:
        raise SystemExit("--gemini-limit must be 100..10000")
    if not 0 <= args.max_window_end_skew_s <= 120:
        raise SystemExit("--max-window-end-skew-s must be 0..120")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger.info(
        "CRYPTO_THRESHOLD_DOMINANCE_STUDY_ENABLED assets=BTC,ETH,SOL,XRP "
        "trade_authorized=false read_only=true order_entry=false"
    )
    asyncio.run(run(args.hours, args.gemini_limit, args.max_window_end_skew_s))


if __name__ == "__main__":
    main()
