"""One-shot public-API reconciliation for the first live XRP canary trade.

Read-only: no credentials, account reads, or order transport.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from src.gemini_kalshi_settlement_agreement import _gemini_outcome, _gemini_settled
from src.gemini_kalshi_settlement_ledger_shadow import _kalshi_settled_series_paced

TARGET_GEMINI_EVENT = "XRP15M2608191515"
TARGET_GEMINI_SYMBOL = "GEMI-XRP15M2608191515-UP"
TARGET_KALSHI = "KXXRP15M-26AUG191115-15"


def _is_target_gemini(event: dict) -> bool:
    if str(event.get("ticker") or "").upper() == TARGET_GEMINI_EVENT:
        return True
    for contract in event.get("contracts") or []:
        if not isinstance(contract, dict):
            continue
        if str(contract.get("instrumentSymbol") or "").upper() == TARGET_GEMINI_SYMBOL:
            return True
    return False


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini_rows = await _gemini_settled(http, 1000)
        kalshi_rows = await _kalshi_settled_series_paced(
            http,
            hours=8,
            series_ticker="KXXRP15M",
        )

    gemini = next((row for row in gemini_rows if _is_target_gemini(row)), None)
    kalshi = next((row for row in kalshi_rows if str(row.get("ticker") or "") == TARGET_KALSHI), None)

    payload = {
        "target_gemini_event": TARGET_GEMINI_EVENT,
        "target_gemini_symbol": TARGET_GEMINI_SYMBOL,
        "target_kalshi_ticker": TARGET_KALSHI,
        "gemini_found": gemini is not None,
        "kalshi_found": kalshi is not None,
        "gemini_rows_scanned": len(gemini_rows),
        "kalshi_rows_scanned": len(kalshi_rows),
        "read_only": True,
        "order_entry": False,
    }
    if gemini is not None:
        outcome, strike, settlement, margin_bps = _gemini_outcome(gemini)
        payload.update({
            "gemini_event_ticker": gemini.get("ticker"),
            "gemini_outcome": outcome,
            "gemini_strike": strike,
            "gemini_settlement": settlement,
            "gemini_margin_bps": margin_bps,
        })
    if kalshi is not None:
        payload.update({
            "kalshi_outcome": str(kalshi.get("result") or "").lower() or None,
            "kalshi_settlement_value": kalshi.get("settlement_value"),
            "kalshi_settled_time": kalshi.get("settled_time") or kalshi.get("settlement_ts"),
        })

    print("XRP_LIVE_SETTLEMENT_RECONCILE " + json.dumps(payload, separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
