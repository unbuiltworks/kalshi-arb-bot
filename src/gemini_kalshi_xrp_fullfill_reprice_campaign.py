"""Bounded full-q1-optimized XRP maker campaign.

This wrapper intentionally relaxes only the *pre-fill admission quantity assumption*
from 0.25 to 1.00 contract so the maker quote can move closer to the market. It does
NOT relax the post-fill safety behavior: the first actual Gemini execution freezes
and cancels the remainder; exact observed fill quantity/fees are then re-evaluated
against the same >=3c/contract floor. Any fractional fill that does not still meet
that floor is flattened immediately and the campaign hard-stops.

Accordingly this is LIVE RESEARCH RISK, not guaranteed arbitrage. The existing
Command Center $1 reservation and $5 cumulative realized-loss ceiling are unchanged.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
from typing import Any

from src import gemini_kalshi_xrp_maker_reprice_campaign as reprice
from src import gemini_kalshi_xrp_maker_triggered_canary as triggered

D = Decimal
FULL_FILL_ADMISSION_QTY = D(os.getenv("CRYPTO_MAKER_FULLFILL_ADMISSION_QTY", "1.00"))
if FULL_FILL_ADMISSION_QTY != D("1.00"):
    raise RuntimeError("full-fill campaign requires CRYPTO_MAKER_FULLFILL_ADMISSION_QTY=1.00")


def emit(stage: str, **fields: Any) -> None:
    print(
        "CRYPTO_XRP_FULLFILL_REPRICE "
        + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str),
        flush=True,
    )


async def run() -> dict[str, Any]:
    original = triggered.MIN_SAFE_FIRST_FILL
    triggered.MIN_SAFE_FIRST_FILL = FULL_FILL_ADMISSION_QTY
    try:
        result = await reprice.run()
        emit(
            "RESULT",
            admission_quantity=str(FULL_FILL_ADMISSION_QTY),
            post_fill_exact_economics_required=True,
            fractional_fill_policy="CANCEL_REMAINDER_THEN_HEDGE_IF_3C_ELSE_FLATTEN_HARD_HALT",
            realized_loss_ceiling_changed=False,
            guaranteed_arbitrage=False,
            underlying_status=result.get("status"),
            order_entry=bool(result.get("order_entry")),
            realized=False,
        )
        return result
    finally:
        triggered.MIN_SAFE_FIRST_FILL = original


if __name__ == "__main__":
    asyncio.run(run())
