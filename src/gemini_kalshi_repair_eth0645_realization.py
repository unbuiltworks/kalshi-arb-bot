"""One-shot canonical ledger repair for the settled 2026-08-22 06:45 UTC ETH pair.

The execution/fill events already exist in the authoritative ledger, but the campaign
failed to append its deterministic TRADE_REALIZED event.  This script re-verifies both
public venue settlements, reconstructs costs only from the already-recorded fills, and
uses the existing campaign accounting/ingest path.  It has no order-entry code.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging

import httpx

from src import gemini_kalshi_crypto_live_campaign as base

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_repair_eth0645_realization")

RUN_ID = "gk15m-08220615-c225-02"
GEMINI_SYMBOL = "GEMI-ETH15M2608220645-UP"
KALSHI_TICKER = "KXETH15M-26AUG220245-45"
EXPECTED_GEMINI_OUTCOME = "yes"
EXPECTED_KALSHI_OUTCOME = "yes"


def execution_record() -> dict:
    # These values are copied from the existing authoritative FILL rows.
    return {
        "run_id": RUN_ID,
        "gemini_payload": {
            "symbol": GEMINI_SYMBOL,
            "eventOutcome": "YES",
        },
        "kalshi_payload": {
            "ticker": KALSHI_TICKER,
            "side": "ask",  # purchased Kalshi NO
        },
        "result": {
            "outcome": "BOTH_FILLED",
            "gemini": {
                "status": "FILLED",
                "filled_quantity": "1",
                "average_fill_price": "0.75",
                "fee_paid": "0.0100",
            },
            "kalshi": {
                "status": "FILLED",
                "filled_quantity": "1",
                "average_fill_price": "0.82",  # YES-space fill price
                "fee_paid": "0.010400",
            },
        },
    }


async def run() -> None:
    record = execution_record()
    costs = base._costs(record)
    if costs["capital"] != D("0.950400"):
        raise RuntimeError(f"unexpected reconstructed capital {costs['capital']}")

    async with httpx.AsyncClient(timeout=20.0) as http:
        settlement = await base._official_settlement(
            http,
            gemini_symbol=GEMINI_SYMBOL,
            kalshi_ticker=KALSHI_TICKER,
        )
        if settlement["gemini_outcome"] != EXPECTED_GEMINI_OUTCOME:
            raise RuntimeError(f"unexpected Gemini outcome {settlement['gemini_outcome']}")
        if settlement["kalshi_outcome"] != EXPECTED_KALSHI_OUTCOME:
            raise RuntimeError(f"unexpected Kalshi outcome {settlement['kalshi_outcome']}")
        if not settlement["venue_outcomes_agree"]:
            raise RuntimeError("ETH0645 venues unexpectedly disagree; refusing ledger repair")

        net_pnl = await base._emit_realized_event(
            http,
            run_id=RUN_ID,
            record=record,
            costs=costs,
            settlement=settlement,
        )
    if net_pnl != D("0.049600"):
        raise RuntimeError(f"unexpected repaired net P&L {net_pnl}")
    logger.warning(
        "CRYPTO_ETH0645_LEDGER_REPAIR_COMPLETE %s",
        json.dumps({
            "trade_id": f"gk15m-{RUN_ID}",
            "event_id": f"gk15m-{RUN_ID}-realized",
            "gemini_outcome": EXPECTED_GEMINI_OUTCOME,
            "kalshi_outcome": EXPECTED_KALSHI_OUTCOME,
            "capital_usd": str(costs["capital"]),
            "net_pnl_usd": str(net_pnl),
            "order_entry": False,
        }, separators=(",", ":")),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
