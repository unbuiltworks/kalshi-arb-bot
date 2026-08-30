"""Gemini q10 V5.1 admission refinement.

V5 inherited V1's $5 maximum one-sided *principal* gate. Once V5 continuously
proves a <=30c full-q10 rescue, principal is no longer the loss-at-risk measure; the
legacy cap can reject otherwise rescue-safe books (for example YES 55c / NO 33c).

V5.1 changes only admission, not rescue mechanics:
- retain >=$0.50 modeled maker-pair edge;
- retain the 20c..80c maker-rebate price band;
- require at least $22.10 available before a campaign, enough for the worst normal
  q10 pair debit (~$10.10 including conservative maker fees) plus a $12 cash reserve;
- remove V1's $5 single-leg principal gate;
- standing <=30c rescue, reward, spot, depth, quantity and all V5 circuit breakers
  remain mandatory inside V5 itself.

The patch is process-local and must be installed before calling the V5 runtime. It
does not add any live permission and remains governed by V5's dual-key arm.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Mapping

from src import gemini_q10_production_executor_v5 as v5

MIN_AVAILABLE_V5_1 = D("22.10")
MIN_PAIR_PROFIT = D("0.50")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")


def entry_model_ok(model: Mapping[str, D]) -> bool:
    return bool(
        D(str(model["locked_edge"])) >= MIN_PAIR_PROFIT
        and MIN_PRICE <= D(str(model["yes_quote"])) <= MAX_PRICE
        and MIN_PRICE <= D(str(model["no_quote"])) <= MAX_PRICE
    )


def install() -> None:
    # Dedicated process only. This intentionally replaces V1's legacy principal
    # gate for V5 while leaving V5's standing rescue invariant untouched.
    v5.v1.MIN_AVAILABLE = MIN_AVAILABLE_V5_1
    v5.v1._entry_model_ok = entry_model_ok


async def run() -> None:
    install()
    await v5.run()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run())
