"""Reconcile unique historical exact-revalidated q=1 quote candidates.

Public settled venue data only. The candidate list below comes from the Aug 18
CRYPTO_HOT_EXACT_REVALIDATION shadow logs and is deliberately deduplicated by
Gemini/Kalshi 15-minute contract pair. Each selected candidate had a 50ms check with
q=1 exact depth/marketability and timing compatible with the current <=100ms age / <=50ms
skew live gates. This module imports no order transport and cannot trade.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging

import httpx

from src.gemini_kalshi_settlement_agreement import _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows
from src.gemini_kalshi_threshold_dominance_study import classify

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_quote_conditioned_history_reconcile")

CANDIDATES = [
    {
        "gemini_symbol": "GEMI-BTC15M2608181815-UP",
        "kalshi_ticker": "KXBTC15M-26AUG181415-15",
        "direction": "KALSHI_YES_GEMINI_NO",
        "best_observed_locked_quote_profit": "0.0400",
        "source": "exact_2bp_fillable_50ms",
    },
    {
        "gemini_symbol": "GEMI-BTC15M2608181845-UP",
        "kalshi_ticker": "KXBTC15M-26AUG181445-45",
        "direction": "GEMINI_YES_KALSHI_NO",
        "best_observed_locked_quote_profit": "0.0300",
        "source": "exact_2bp_fillable_50ms",
    },
    {
        "gemini_symbol": "GEMI-BTC15M2608181900-UP",
        "kalshi_ticker": "KXBTC15M-26AUG181500-00",
        "direction": "KALSHI_YES_GEMINI_NO",
        "best_observed_locked_quote_profit": "0.0700",
        "source": "exact_2bp_fillable_50ms",
    },
    {
        "gemini_symbol": "GEMI-BTC15M2608181915-UP",
        "kalshi_ticker": "KXBTC15M-26AUG181515-15",
        "direction": "GEMINI_YES_KALSHI_NO",
        "best_observed_locked_quote_profit": "0.0700",
        "source": "exact_2bp_fillable_50ms",
    },
    {
        "gemini_symbol": "GEMI-BTC15M2608182000-UP",
        "kalshi_ticker": "KXBTC15M-26AUG181600-00",
        "direction": "GEMINI_YES_KALSHI_NO",
        "best_observed_locked_quote_profit": "0.1000",
        "source": "exact_2bp_fillable_50ms",
    },
]


def payoff_classification(direction: str, gemini_outcome: str, kalshi_outcome: str) -> str:
    direction = direction.upper()
    g = gemini_outcome.lower()
    k = kalshi_outcome.lower()
    if direction == "GEMINI_YES_KALSHI_NO":
        if g == "yes" and k == "no":
            return "FAVORABLE_DOUBLE_WIN"
        if g == "no" and k == "yes":
            return "ADVERSE_DOUBLE_LOSS"
        return "NORMAL_ONE_WIN"
    if direction == "KALSHI_YES_GEMINI_NO":
        if k == "yes" and g == "no":
            return "FAVORABLE_DOUBLE_WIN"
        if k == "no" and g == "yes":
            return "ADVERSE_DOUBLE_LOSS"
        return "NORMAL_ONE_WIN"
    return "UNKNOWN_DIRECTION"


async def main_async() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, 10000),
            _kalshi_settled_paced(http, 168),
        )

    matched = match_settled_true_windows(gemini, kalshi, 30.0)
    event_by_ticker = {str(e.get("ticker") or ""): e for e in gemini}
    market_by_ticker = {str(m.get("ticker") or ""): m for m in kalshi}
    rows_by_pair = {(str(r.gemini_ticker), str(r.kalshi_ticker)): r for r in matched}

    resolved = []
    missing = []
    for candidate in CANDIDATES:
        key = (candidate["gemini_symbol"], candidate["kalshi_ticker"])
        row = rows_by_pair.get(key)
        event = event_by_ticker.get(candidate["gemini_symbol"])
        market = market_by_ticker.get(candidate["kalshi_ticker"])
        if row is None or event is None or market is None:
            missing.append(candidate)
            continue
        enriched = classify(row, event, market)
        safe = str(enriched.get("dominance_safe_direction_shared_value") or "")
        payoff = payoff_classification(candidate["direction"], row.gemini_outcome, row.kalshi_outcome)
        gap = enriched.get("threshold_gap_abs_bps")
        payload = {
            **candidate,
            "asset": row.asset,
            "expiry_utc": row.expiry_utc,
            "gemini_strike": enriched.get("gemini_strike"),
            "kalshi_target": enriched.get("kalshi_target"),
            "threshold_gap_abs_bps": gap,
            "dominance_safe_direction": safe,
            "candidate_direction_matches_safe": candidate["direction"] == safe,
            "gemini_outcome": row.gemini_outcome,
            "kalshi_outcome": row.kalshi_outcome,
            "venue_outcomes_agree": bool(row.agrees),
            "payoff_classification": payoff,
            "adverse_double_loss": payoff == "ADVERSE_DOUBLE_LOSS",
            "favorable_double_win": payoff == "FAVORABLE_DOUBLE_WIN",
            "gemini_settlement": enriched.get("gemini_settlement"),
            "kalshi_expiration_value": enriched.get("kalshi_expiration_value"),
            "kalshi_minus_gemini_final_bps": enriched.get("kalshi_minus_gemini_final_bps"),
            "classification": "QUOTE_CONDITIONED_SETTLEMENT_RECONCILIATION",
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }
        resolved.append(payload)
        logger.warning("CRYPTO_QUOTE_CONDITIONED_HISTORY_ROW %s", json.dumps(payload, separators=(",", ":"), default=str))

    safe_rows = [r for r in resolved if r["candidate_direction_matches_safe"]]
    safe_adverse = [r for r in safe_rows if r["adverse_double_loss"]]
    safe_favorable = [r for r in safe_rows if r["favorable_double_win"]]
    summary = {
        "requested_unique_candidates": len(CANDIDATES),
        "resolved_unique_candidates": len(resolved),
        "missing_unique_candidates": len(missing),
        "safe_direction_candidates": len(safe_rows),
        "safe_direction_adverse_double_losses": len(safe_adverse),
        "safe_direction_favorable_double_wins": len(safe_favorable),
        "safe_direction_normal_one_wins": len(safe_rows) - len(safe_adverse) - len(safe_favorable),
        "safe_direction_total_observed_locked_quote_profit": str(sum((D(r["best_observed_locked_quote_profit"]) for r in safe_rows), D("0"))),
        "all_candidate_adverse_double_losses": sum(1 for r in resolved if r["adverse_double_loss"]),
        "missing": missing,
        "interpretation": "Historical quote-conditioned settlement evidence only; no fills occurred and quoted profit is not realized P&L.",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    logger.warning("CRYPTO_QUOTE_CONDITIONED_HISTORY_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))
    if missing:
        raise RuntimeError("some exact historical candidates were not resolved")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
