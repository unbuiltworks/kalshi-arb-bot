"""One-off authoritative settlement + P&L repair for gkbasis-br0823131801-01.

No order entry. Reads the official Gemini event and Kalshi market, computes P&L from
only the confirmed Gemini fill, and inserts one idempotent TRADE_REALIZED event into
the canonical Neon ledger. The paired Kalshi order is known to have filled zero.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging

import httpx
from psycopg.types.json import Jsonb

from src import gemini_kalshi_crypto_live_campaign as campaign
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_basis_risk_q1_child as child

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_orphan_settlement")

TRADE_ID = "gkbasis-br0823131801-01"
EVENT_ID = f"{TRADE_ID}-realized"
GEMINI_SYMBOL = "GEMI-SOL15M2608231330-UP"
KALSHI_TICKER = "KXSOL15M-26AUG230930-30"
GEMINI_SIDE = "no"
GEMINI_PRICE = D("0.0800")
GEMINI_FEE = D("0.0100")
KALSHI_FILLED_QTY = D("0")


async def main_async() -> None:
    async with httpx.AsyncClient(timeout=10.0) as http:
        settlement = await campaign._official_settlement(
            http,
            gemini_symbol=GEMINI_SYMBOL,
            kalshi_ticker=KALSHI_TICKER,
        )

    gemini_outcome = str(settlement["gemini_outcome"]).lower()
    payout = D("1") if gemini_outcome == GEMINI_SIDE else D("0")
    raw_cost = GEMINI_PRICE
    capital = GEMINI_PRICE + GEMINI_FEE
    gross_pnl = payout - raw_cost
    net_pnl = payout - capital
    status = "REALIZED_ONE_LEG_SETTLEMENT"

    metadata = {
        "classification": "LIVE_BASIS_RISK_RESEARCH_ONE_LEG_REALIZED",
        "guaranteed_arb": False,
        "orphan_recovery": True,
        "gemini_symbol": GEMINI_SYMBOL,
        "kalshi_ticker": KALSHI_TICKER,
        "gemini_side": GEMINI_SIDE,
        "gemini_fill_price": str(GEMINI_PRICE),
        "gemini_fee": str(GEMINI_FEE),
        "kalshi_filled_quantity": str(KALSHI_FILLED_QTY),
        "settlement": settlement,
        "payout": str(payout),
        "capital": str(capital),
    }

    with child_v2._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.trade_events (
                    event_id, trade_id, event_type, event_ts, strategy, mode,
                    market, asset, venues, status, quantity, amount, fee,
                    gross_pnl, net_pnl, legs_required, legs_filled, metadata
                ) VALUES (
                    %s, %s, 'TRADE_REALIZED', %s, %s, %s,
                    %s, 'SOL', %s, %s, 1, %s, %s,
                    %s, %s, 2, 1, %s
                )
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
                """,
                (
                    EVENT_ID,
                    TRADE_ID,
                    datetime.now(timezone.utc),
                    child.STRATEGY,
                    child_v2.LEDGER_MODE,
                    f"SOL15M2608231330|{KALSHI_TICKER}",
                    ["Gemini", "Kalshi"],
                    status,
                    capital,
                    GEMINI_FEE,
                    gross_pnl,
                    net_pnl,
                    Jsonb(metadata),
                ),
            )
            inserted = cur.fetchone() is not None
        conn.commit()

    logger.critical(
        "CRYPTO_BASIS_ORPHAN_SETTLEMENT_REALIZED %s",
        json.dumps({
            "trade_id": TRADE_ID,
            "inserted": inserted,
            "gemini_outcome": gemini_outcome,
            "kalshi_outcome": settlement["kalshi_outcome"],
            "venue_outcomes_agree": settlement["venue_outcomes_agree"],
            "gross_pnl": str(gross_pnl),
            "net_pnl": str(net_pnl),
            "capital": str(capital),
            "order_entry": False,
        }, separators=(",", ":")),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
