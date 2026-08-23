"""Read-only threshold/direction audit of the nine Aug-22 realized q=1 live probes.

Uses public settled Gemini/Kalshi endpoints only.  It cannot submit, preview, cancel,
or modify orders.  The purpose is to test whether a deterministic threshold-direction
rule would have rejected the observed index-mismatch loss while retaining profitable
fills.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx

from src.gemini_kalshi_fast_overlap import _gemini_meta
from src.gemini_kalshi_quote_conditioned_history_reconcile_v2 import exact_event, exact_market, event_ticker
from src.gemini_kalshi_settlement_agreement import _gemini_outcome, _kalshi_outcome
from src.gemini_kalshi_threshold_dominance_study import classify

logger = logging.getLogger("kalshi_arb.gemini_kalshi_realized_trade_geometry_audit")

TRADES = [
    {"trade_id":"gk15m-gk15m-08211925-a1b2-07","asset":"XRP","gemini_symbol":"GEMI-XRP15M2608220000-UP","kalshi_ticker":"KXXRP15M-26AUG212000-00","direction":"GEMINI_YES_KALSHI_NO","realized_net_pnl":"0.088800"},
    {"trade_id":"gk15m-gk15m-08220050-2197-02","asset":"BTC","gemini_symbol":"GEMI-BTC15M2608220100-UP","kalshi_ticker":"KXBTC15M-26AUG212100-00","direction":"GEMINI_YES_KALSHI_NO","realized_net_pnl":"0.033100"},
    {"trade_id":"gk15m-gk15m-08220342-ba61-01","asset":"BTC","gemini_symbol":"GEMI-BTC15M2608220415-UP","kalshi_ticker":"KXBTC15M-26AUG220015-15","direction":"GEMINI_YES_KALSHI_NO","realized_net_pnl":"0.045000"},
    {"trade_id":"gk15m-gk15m-08220437-bb9d-01","asset":"BTC","gemini_symbol":"GEMI-BTC15M2608220500-UP","kalshi_ticker":"KXBTC15M-26AUG220100-00","direction":"GEMINI_YES_KALSHI_NO","realized_net_pnl":"0.044500"},
    {"trade_id":"gk15m-gk15m-08220437-bb9d-02","asset":"XRP","gemini_symbol":"GEMI-XRP15M2608220515-UP","kalshi_ticker":"KXXRP15M-26AUG220115-15","direction":"KALSHI_YES_GEMINI_NO","realized_net_pnl":"0.098300"},
    {"trade_id":"gk15m-gk15m-08220515-54da-01","asset":"BTC","gemini_symbol":"GEMI-BTC15M2608220530-UP","kalshi_ticker":"KXBTC15M-26AUG220130-30","direction":"KALSHI_YES_GEMINI_NO","realized_net_pnl":"-0.885300"},
    {"trade_id":"gk15m-gk15m-08220536-f6c9-03","asset":"SOL","gemini_symbol":"GEMI-SOL15M2608220615-UP","kalshi_ticker":"KXSOL15M-26AUG220215-15","direction":"GEMINI_YES_KALSHI_NO","realized_net_pnl":"0.044200"},
    {"trade_id":"gk15m-gk15m-08220615-c225-01","asset":"SOL","gemini_symbol":"GEMI-SOL15M2608220630-UP","kalshi_ticker":"KXSOL15M-26AUG220230-30","direction":"GEMINI_YES_KALSHI_NO","realized_net_pnl":"0.034700"},
    {"trade_id":"gk15m-gk15m-08220615-c225-02","asset":"ETH","gemini_symbol":"GEMI-ETH15M2608220645-UP","kalshi_ticker":"KXETH15M-26AUG220245-45","direction":"GEMINI_YES_KALSHI_NO","realized_net_pnl":"0.049600"},
]


def payoff(direction: str, g_out: str, k_out: str) -> str:
    if direction == "GEMINI_YES_KALSHI_NO":
        wins = int(g_out == "yes") + int(k_out == "no")
    else:
        wins = int(k_out == "yes") + int(g_out == "no")
    return "DOUBLE_WIN" if wins == 2 else "DOUBLE_LOSS" if wins == 0 else "ONE_WIN"


async def main_async() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        pairs = await asyncio.gather(*(
            asyncio.gather(exact_event(http, event_ticker(t["gemini_symbol"])), exact_market(http, t["kalshi_ticker"]))
            for t in TRADES
        ))

    rows = []
    for t, (event, market) in zip(TRADES, pairs):
        meta = _gemini_meta(event)
        g_out, g_strike, g_settlement, g_margin = _gemini_outcome(event)
        k_out = _kalshi_outcome(market)
        if meta is None or g_out is None or k_out is None:
            raise RuntimeError(f"unresolved public settlement for {t['trade_id']}")
        asset, _, expiry = meta
        base = SimpleNamespace(
            asset=asset, expiry=expiry, gemini_ticker=event_ticker(t["gemini_symbol"]),
            kalshi_ticker=t["kalshi_ticker"], gemini_outcome=g_out, kalshi_outcome=k_out,
            agrees=(g_out == k_out), gemini_strike=g_strike, gemini_settlement=g_settlement,
            gemini_margin_bps=g_margin,
        )
        e = classify(base, event, market)
        safe = str(e.get("dominance_safe_direction_shared_value") or "")
        row = {
            **t,
            "gemini_strike": e.get("gemini_strike"),
            "kalshi_target": e.get("kalshi_target"),
            "threshold_gap_bps": e.get("threshold_gap_bps"),
            "threshold_gap_abs_bps": e.get("threshold_gap_abs_bps"),
            "dominance_safe_direction": safe,
            "direction_matches_safe": t["direction"] == safe,
            "gemini_outcome": g_out,
            "kalshi_outcome": k_out,
            "venue_outcomes_agree": g_out == k_out,
            "payoff": payoff(t["direction"], g_out, k_out),
            "gemini_settlement": e.get("gemini_settlement"),
            "kalshi_expiration_value": e.get("kalshi_expiration_value"),
            "kalshi_minus_gemini_final_bps": e.get("kalshi_minus_gemini_final_bps"),
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }
        rows.append(row)
        logger.warning("CRYPTO_REALIZED_GEOMETRY_ROW %s", json.dumps(row, separators=(",", ":"), default=str))

    loser = [r for r in rows if float(r["realized_net_pnl"]) < 0]
    winners = [r for r in rows if float(r["realized_net_pnl"]) > 0]
    summary = {
        "trades": len(rows),
        "winners": len(winners),
        "losers": len(loser),
        "safe_direction_winners": sum(r["direction_matches_safe"] for r in winners),
        "safe_direction_losers": sum(r["direction_matches_safe"] for r in loser),
        "unsafe_direction_winners": sum(not r["direction_matches_safe"] for r in winners),
        "unsafe_direction_losers": sum(not r["direction_matches_safe"] for r in loser),
        "double_loss_rows": [r["trade_id"] for r in rows if r["payoff"] == "DOUBLE_LOSS"],
        "read_only": True,
        "order_entry": False,
    }
    logger.warning("CRYPTO_REALIZED_GEOMETRY_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
