"""One-shot idempotent ARB Command Center correction for gk5-260819-1236-02.

Accounting only. No venue order capability. Records the realized cost of the Kalshi
one-leg fill that was immediately flattened after Gemini FOK returned zero fill.
"""
from __future__ import annotations

import asyncio
import httpx

from src import gemini_kalshi_crypto_live_campaign as base


async def main() -> None:
    trade_id = "gk15m-gk5-260819-1236-02"
    event = {
        "event_id": f"{trade_id}-realized-execution-failure",
        "trade_id": trade_id,
        "event_type": "TRADE_REALIZED",
        "event_ts": "2026-08-19T20:46:16.803000+00:00",
        "strategy": "gemini_kalshi_15m_crypto",
        "mode": "LIVE_PROBE",
        "market": "XRP15M2608192100|KXXRP15M-26AUG191700-00",
        "asset": "XRP",
        "venues": ["Gemini", "Kalshi"],
        "side": "Kalshi YES / Gemini NO primary missed; Kalshi flattened",
        "status": "REALIZED_EXECUTION_FAILURE",
        "quantity": "1",
        "fee": "0.0241",
        "gross_pnl": "-0.0200",
        "net_pnl": "-0.0441",
        "legs_required": 2,
        "legs_filled": 1,
        "metadata": {
            "run_id": "gk5-260819-1236-02",
            "execution_outcome": "ONE_LEG_FILLED",
            "gemini_status": "CANCELED",
            "gemini_reason": "FillOrKillWouldNotFill",
            "kalshi_entry_yes_price": "0.7900",
            "kalshi_entry_fee": "0.0117",
            "kalshi_flatten_yes_price": "0.7700",
            "kalshi_flatten_fee": "0.0124",
            "raw_price_loss": "0.0200",
            "flatten_completed": True,
            "settlement_exposure_after_flatten": False,
            "accounting_correction": True,
        },
    }
    async with httpx.AsyncClient(timeout=20.0) as http:
        await base._post_event(http, event)
    print("GK5_1236_02_LOSS_RECONCILED event_id=" + event["event_id"] + " net_pnl=-0.0441 order_entry=false", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
