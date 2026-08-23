"""Fast read-only reconciliation of recorded historical q=1 quote pairs.
Public settled venue data only; this module cannot trade.
"""
from __future__ import annotations
import asyncio, json, logging
from decimal import Decimal
from types import SimpleNamespace
import httpx
from src.gemini_kalshi_fast_overlap import GEMINI_REST, KALSHI_REST, _gemini_meta
from src.gemini_kalshi_quote_conditioned_history_reconcile import CANDIDATES, payoff_classification
from src.gemini_kalshi_settlement_agreement import _gemini_outcome, _kalshi_outcome
from src.gemini_kalshi_threshold_dominance_study import classify

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_quote_conditioned_history_reconcile_v2")


def event_ticker(symbol: str) -> str:
    v = str(symbol or "")
    if v.startswith("GEMI-"): v = v[5:]
    if v.endswith("-UP"): v = v[:-3]
    return v


async def exact_event(http: httpx.AsyncClient, ticker: str) -> dict:
    r = await http.get(f"{GEMINI_REST}/events/{ticker}")
    r.raise_for_status()
    p = r.json()
    if isinstance(p, dict) and isinstance(p.get("event"), dict): row = p["event"]
    elif isinstance(p, dict) and isinstance(p.get("data"), dict): row = p["data"]
    else: row = p
    if not isinstance(row, dict): raise RuntimeError(f"missing Gemini event {ticker}")
    return row


async def exact_market(http: httpx.AsyncClient, ticker: str) -> dict:
    r = await http.get(f"{KALSHI_REST}/markets/{ticker}")
    r.raise_for_status()
    p = r.json(); row = p.get("market") if isinstance(p, dict) else None
    if not isinstance(row, dict): raise RuntimeError(f"missing Kalshi market {ticker}")
    return row


async def main_async() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        fetched = await asyncio.gather(*(
            asyncio.gather(exact_event(http, event_ticker(c["gemini_symbol"])), exact_market(http, c["kalshi_ticker"]))
            for c in CANDIDATES
        ))
    resolved, missing = [], []
    for c, pair in zip(CANDIDATES, fetched):
        event, market = pair
        gt = event_ticker(c["gemini_symbol"])
        meta = _gemini_meta(event)
        g_out, g_strike, g_settlement, g_margin = _gemini_outcome(event)
        k_out = _kalshi_outcome(market)
        if meta is None or g_out is None or k_out is None:
            missing.append({**c, "resolved_event_ticker": gt, "event_keys": sorted(event.keys()), "market_result": market.get("result")})
            continue
        asset, _, expiry = meta
        row = SimpleNamespace(
            asset=asset, expiry=expiry, gemini_ticker=gt, kalshi_ticker=c["kalshi_ticker"],
            gemini_outcome=g_out, kalshi_outcome=k_out, agrees=(g_out == k_out),
            gemini_strike=g_strike, gemini_settlement=g_settlement, gemini_margin_bps=g_margin,
            as_dict=lambda: {
                "asset": asset, "expiry_utc": expiry.isoformat(), "gemini_ticker": gt,
                "kalshi_ticker": c["kalshi_ticker"], "gemini_outcome": g_out, "kalshi_outcome": k_out,
                "agrees": g_out == k_out, "gemini_strike": g_strike,
                "gemini_settlement": g_settlement, "gemini_margin_bps": g_margin,
            },
        )
        enriched = classify(row, event, market)
        safe = str(enriched.get("dominance_safe_direction_shared_value") or "")
        payoff = payoff_classification(c["direction"], g_out, k_out)
        p = {
            **c, "resolved_event_ticker": gt, "asset": asset, "expiry_utc": expiry.isoformat(),
            "gemini_strike": enriched.get("gemini_strike"), "kalshi_target": enriched.get("kalshi_target"),
            "threshold_gap_abs_bps": enriched.get("threshold_gap_abs_bps"), "dominance_safe_direction": safe,
            "candidate_direction_matches_safe": c["direction"] == safe,
            "gemini_outcome": g_out, "kalshi_outcome": k_out, "venue_outcomes_agree": g_out == k_out,
            "payoff_classification": payoff, "adverse_double_loss": payoff == "ADVERSE_DOUBLE_LOSS",
            "favorable_double_win": payoff == "FAVORABLE_DOUBLE_WIN",
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
    if missing: raise RuntimeError("some exact historical candidates were not resolved")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())

if __name__ == "__main__": main()
