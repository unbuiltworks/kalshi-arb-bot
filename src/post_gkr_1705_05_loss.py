"""One-shot idempotent ARB Command Center correction for gkr-260819-1705-05.

This is an accounting-only utility. It places no venue orders and only posts the
settled one-leg execution loss that occurred on 2026-08-19 22:54 ET.
"""
from __future__ import annotations

import asyncio
import httpx

from src import gemini_kalshi_crypto_live_campaign as base


async def main() -> None:
    trade_id = "gk15m-gkr-260819-1705-05"
    event = {
        "event_id": f"{trade_id}-realized-execution-failure",
        "trade_id": trade_id,
        "event_type": "TRADE_REALIZED",
        "event_ts": "2026-08-20T03:00:00+00:00",
        "strategy": "gemini_kalshi_15m_crypto",
        "mode": "LIVE_PROBE",
        "market": "BTC15M2608200300|KXBTC15M-26AUG192300-00",
        "asset": "BTC",
        "venues": ["Gemini", "Kalshi"],
        "side": "Gemini YES / Kalshi NO hedge rejected",
        "status": "REALIZED_EXECUTION_FAILURE",
        "quantity": "1",
        "fee": "0.0200",
        "gross_pnl": "-0.3100",
        "net_pnl": "-0.3300",
        "legs_required": 2,
        "legs_filled": 1,
        "metadata": {
            "run_id": "gkr-260819-1705-05",
            "execution_outcome": "ONE_LEG_FILLED",
            "actual_capital": "0.3300",
            "settlement_payout": "0",
            "gemini_symbol": "GEMI-BTC15M2608200300-UP",
            "gemini_outcome": "no",
            "gemini_entry_price": "0.3100",
            "gemini_entry_fee": "0.0200",
            "kalshi_ticker": "KXBTC15M-26AUG192300-00",
            "kalshi_status": "REJECTED",
            "kalshi_reason": "fill_or_kill_insufficient_resting_volume",
            "emergency_flatten_status": "REJECTED",
            "emergency_flatten_reason": "InvalidOrderType",
            "settlement_source": "GEMINI_SETTLED_POSITION_RECONCILIATION",
            "accounting_correction": True,
        },
    }
    async with httpx.AsyncClient(timeout=20.0) as http:
        await base._post_event(http, event)
    print("GKR_1705_05_LOSS_RECONCILED event_id=" + event["event_id"] + " net_pnl=-0.3300 order_entry=false", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
