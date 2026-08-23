"""Fast read-only reconciliation of exact historical q=1 quote candidates.
Public settled venue data only; this module cannot trade.
"""
from __future__ import annotations
import asyncio, json, logging
from decimal import Decimal
import httpx
from src.gemini_kalshi_fast_overlap import GEMINI_REST, KALSHI_REST
from src.gemini_kalshi_quote_conditioned_history_reconcile import CANDIDATES, payoff_classification
from src.gemini_kalshi_settlement_agreement import match_settled
from src.gemini_kalshi_threshold_dominance_study import classify

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_quote_conditioned_history_reconcile_v2")


def event_ticker(symbol: str) -> str:
    value = str(symbol or "")
    if value.startswith("GEMI-"):
        value = value[5:]
    if value.endswith("-UP"):
        value = value[:-3]
    return value


async def exact_event(http: httpx.AsyncClient, ticker: str) -> dict:
    r = await http.get(f"{GEMINI_REST}/events/{ticker}")
    r.raise_for_status()
    payload = r.json()
    row = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    if not isinstance(row, dict):
        raise RuntimeError(f"missing Gemini event {ticker}")
    return row


async def exact_market(http: httpx.AsyncClient, ticker: str) -> dict:
    r = await http.get(f"{KALSHI_REST}/markets/{ticker}")
    r.raise_for_status()
    payload = r.json()
    row = payload.get("market") if isinstance(payload, dict) else None
    if not isinstance(row, dict):
        raise RuntimeError(f"missing Kalshi market {ticker}")
    return row


async def main_async() -> None:
    g_tickers = sorted({event_ticker(c["gemini_symbol"]) for c in CANDIDATES})
    k_tickers = sorted({c["kalshi_ticker"] for c in CANDIDATES})
    async with httpx.AsyncClient(timeout=30.0) as http:
        g_rows, k_rows = await asyncio.gather(
            asyncio.gather(*(exact_event(http, t) for t in g_tickers)),
            asyncio.gather(*(exact_market(http, t) for t in k_tickers)),
        )
    gemini, kalshi = list(g_rows), list(k_rows)
    event_by_ticker = {str(e.get("ticker") or ""): e for e in gemini}
    market_by_ticker = {str(m.get("ticker") or ""): m for m in kalshi}
    rows_by_pair = {(r.gemini_ticker, r.kalshi_ticker): r for r in match_settled(gemini, kalshi, max_expiry_skew_s=30.0)}
    resolved, missing = [], []
    for c in CANDIDATES:
        gt = event_ticker(c["gemini_symbol"])
        row = rows_by_pair.get((gt, c["kalshi_ticker"]))
        event, market = event_by_ticker.get(gt), market_by_ticker.get(c["kalshi_ticker"])
        if row is None or event is None or market is None:
            missing.append({**c, "resolved_event_ticker": gt})
            continue
        enriched = classify(row, event, market)
        safe = str(enriched.get("dominance_safe_direction_shared_value") or "")
        payoff = payoff_classification(c["direction"], row.gemini_outcome, row.kalshi_outcome)
        p = {
            **c, "resolved_event_ticker": gt, "asset": row.asset, "expiry_utc": row.expiry.isoformat(),
            "gemini_strike": enriched.get("gemini_strike"), "kalshi_target": enriched.get("kalshi_target"),
            "threshold_gap_abs_bps": enriched.get("threshold_gap_abs_bps"), "dominance_safe_direction": safe,
            "candidate_direction_matches_safe": c["direction"] == safe,
            "gemini_outcome": row.gemini_outcome, "kalshi_outcome": row.kalshi_outcome,
            "venue_outcomes_agree": bool(row.agrees), "payoff_classification": payoff,
            "adverse_double_loss": payoff == "ADVERSE_DOUBLE_LOSS", "favorable_double_win": payoff == "FAVORABLE_DOUBLE_WIN",
            "gemini_settlement": enriched.get("gemini_settlement"), "kalshi_expiration_value": enriched.get("kalshi_expiration_value"),
            "kalshi_minus_gemini_final_bps": enriched.get("kalshi_minus_gemini_final_bps"),
            "trade_authorized": False, "read_only": True, "order_entry": False,
        }
        resolved.append(p)
        logger.warning("CRYPTO_QUOTE_CONDITIONED_HISTORY_ROW_V2 %s", json.dumps(p, separators=(",", ":"), default=str))
    safe_rows = [r for r in resolved if r["candidate_direction_matches_safe"]]
    summary = {
        "requested_unique_candidates": len(CANDIDATES), "resolved_unique_candidates": len(resolved), "missing_unique_candidates": len(missing),
        "safe_direction_candidates": len(safe_rows),
        "safe_direction_adverse_double_losses": sum(r["adverse_double_loss"] for r in safe_rows),
        "safe_direction_favorable_double_wins": sum(r["favorable_double_win"] for r in safe_rows),
        "safe_direction_normal_one_wins": sum(r["payoff_classification"] == "NORMAL_ONE_WIN" for r in safe_rows),
        "safe_direction_total_observed_locked_quote_profit": str(sum((D(r["best_observed_locked_quote_profit"]) for r in safe_rows), D("0"))),
        "all_candidate_adverse_double_losses": sum(r["adverse_double_loss"] for r in resolved), "missing": missing,
        "interpretation": "Historical quote-conditioned settlement evidence only; no fills occurred and quoted profit is not realized P&L.",
        "trade_authorized": False, "read_only": True, "order_entry": False,
    }
    logger.warning("CRYPTO_QUOTE_CONDITIONED_HISTORY_SUMMARY_V2 %s", json.dumps(summary, separators=(",", ":"), default=str))
    if missing:
        raise RuntimeError("some exact historical candidates were not resolved")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
