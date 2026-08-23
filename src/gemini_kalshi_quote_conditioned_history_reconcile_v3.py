"""Fast read-only reconciliation of exact historical q=1 quote candidates.

Unlike v1/v2, this does not paginate every settled Kalshi market. It fetches only
the five exact known Kalshi tickers and maps Gemini live instrumentSymbols to their
parent settled events before matching by asset/expiry. Public data only; no order
transport is imported and this module cannot trade.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging

import httpx

from src.gemini_kalshi_fast_overlap import KALSHI_REST
from src.gemini_kalshi_quote_conditioned_history_reconcile import CANDIDATES, payoff_classification
from src.gemini_kalshi_quote_conditioned_history_reconcile_v2 import _symbol_to_event_ticker
from src.gemini_kalshi_settlement_agreement import _gemini_settled, match_settled
from src.gemini_kalshi_threshold_dominance_study import classify

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_quote_conditioned_history_reconcile_v3")


async def _exact_kalshi_markets(http: httpx.AsyncClient) -> list[dict]:
    async def one(ticker: str) -> dict:
        response = await http.get(f"{KALSHI_REST}/markets/{ticker}")
        response.raise_for_status()
        payload = response.json()
        market = payload.get("market") if isinstance(payload, dict) else None
        if not isinstance(market, dict):
            raise RuntimeError(f"missing market payload for {ticker}")
        return market

    unique = sorted({str(c["kalshi_ticker"]) for c in CANDIDATES})
    return await asyncio.gather(*(one(ticker) for ticker in unique))


async def main_async() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(_gemini_settled(http, 10000), _exact_kalshi_markets(http))

    symbol_to_event = _symbol_to_event_ticker(gemini)
    event_by_ticker = {str(e.get("ticker") or ""): e for e in gemini}
    market_by_ticker = {str(m.get("ticker") or ""): m for m in kalshi}
    matched = match_settled(gemini, kalshi, max_expiry_skew_s=30.0)
    rows_by_pair = {(str(r.gemini_ticker), str(r.kalshi_ticker)): r for r in matched}

    resolved: list[dict] = []
    missing: list[dict] = []
    for candidate in CANDIDATES:
        event_ticker = symbol_to_event.get(candidate["gemini_symbol"])
        row = rows_by_pair.get((event_ticker or "", candidate["kalshi_ticker"]))
        event = event_by_ticker.get(event_ticker or "")
        market = market_by_ticker.get(candidate["kalshi_ticker"])
        if row is None or event is None or market is None:
            missing.append({**candidate, "resolved_event_ticker": event_ticker})
            continue

        enriched = classify(row, event, market)
        safe = str(enriched.get("dominance_safe_direction_shared_value") or "")
        payoff = payoff_classification(candidate["direction"], row.gemini_outcome, row.kalshi_outcome)
        payload = {
            **candidate,
            "resolved_event_ticker": event_ticker,
            "asset": row.asset,
            "expiry_utc": row.expiry.isoformat(),
            "gemini_strike": enriched.get("gemini_strike"),
            "kalshi_target": enriched.get("kalshi_target"),
            "threshold_gap_abs_bps": enriched.get("threshold_gap_abs_bps"),
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
            "classification": "QUOTE_CONDITIONED_SETTLEMENT_RECONCILIATION_V3",
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }
        resolved.append(payload)
        logger.warning("CRYPTO_QUOTE_CONDITIONED_HISTORY_ROW_V3 %s", json.dumps(payload, separators=(",", ":"), default=str))

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
    logger.warning("CRYPTO_QUOTE_CONDITIONED_HISTORY_SUMMARY_V3 %s", json.dumps(summary, separators=(",", ":"), default=str))
    if missing:
        raise RuntimeError("some exact historical candidates were not resolved")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
