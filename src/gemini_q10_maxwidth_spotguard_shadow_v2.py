"""Read-only BTC5 q10 reward-farming shadow V2.

This wrapper preserves the proven low-latency Coinbase spot guard + persistent Gemini
book cache while fixing two measurement issues:

1. Gemini liquidity-reward eligibility is independent of the Maker Rebate Program's
   20c-80c fill band. Reward qualification uses the active reward max-distance rule,
   q10 minimum size (fixed by this strategy), valid prediction prices, maker geometry,
   and the existing standing-rescue invariant.
2. BTC5 event discovery is throttled so the 50 ms hot loop does not repeatedly hit the
   public REST discovery path. Book decisions remain websocket-driven.

No private endpoints and no order mutation.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import time
from typing import Any

from src import gemini_q10_maxwidth_spotguard_shadow as base
from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_q10_rescue_policy as rescue
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

DISCOVERY_REFRESH_SECONDS = 1.0


def reward_quote_eligible(*, yes_quote: D, no_quote: D, mid: D, max_distance: D) -> bool:
    """Program eligibility only; deliberately does not apply maker-rebate price band."""
    yes_distance = mid - yes_quote
    no_distance = (D("1") - mid) - no_quote
    return bool(
        D("0") <= yes_distance <= max_distance
        and D("0") <= no_distance <= max_distance
        and D("0") < yes_quote < D("1")
        and D("0") < no_quote < D("1")
    )


def evaluate_v2(*, yes_quote: D, no_quote: D, bid: D, bid_size: D,
                ask: D, ask_size: D, tick: D, max_distance: D) -> dict[str, Any]:
    mid = (bid + ask) / D("2")
    yes_distance = mid - yes_quote
    no_distance = (D("1") - mid) - no_quote
    price_valid = bool(D("0") < yes_quote < D("1") and D("0") < no_quote < D("1"))
    reward_ok = reward_quote_eligible(
        yes_quote=yes_quote,
        no_quote=no_quote,
        mid=mid,
        max_distance=max_distance,
    )
    # Rebate band is informational only. It must never zero a liquidity-reward score.
    rebate_band_ok = bool(
        probe.MIN_PRICE <= yes_quote <= probe.MAX_PRICE
        and probe.MIN_PRICE <= no_quote <= probe.MAX_PRICE
    )
    yes_cross = ask <= yes_quote
    no_cross = (D("1") - bid) <= no_quote
    maker_ok = bool(price_valid and not yes_cross and not no_cross)

    # Near an extreme midpoint a 9c target can round to 0c or 100c. Fee/rescue helpers
    # correctly reject those prices, but a read-only shadow should classify the
    # candidate rather than spam loop exceptions. Fail closed before calling them.
    if not price_valid:
        return {
            "yes_quote": str(yes_quote),
            "no_quote": str(no_quote),
            "yes_distance": str(yes_distance),
            "no_distance": str(no_distance),
            "price_valid": False,
            "reward_ok": False,
            "rebate_band_ok": False,
            "maker_ok": False,
            "yes_cross": yes_cross,
            "no_cross": no_cross,
            "locked_edge": "0",
            "rescue_safe": False,
            "qualified_safe": False,
            "rescue": {"safe": False, "reason": "INVALID_QUOTE_PRICE"},
        }

    locked_edge = (
        base.Q * (D("1") - yes_quote - no_quote)
        - gemini_predictions_maker_fee(yes_quote, base.Q)
        - gemini_predictions_maker_fee(no_quote, base.Q)
    )
    standing = rescue.standing_rescue_check(
        yes_quote=yes_quote,
        no_quote=no_quote,
        quantity=base.Q,
        yes_bid=bid,
        yes_bid_size=bid_size,
        yes_ask=ask,
        yes_ask_size=ask_size,
        max_loss=base.MAX_RESCUE_LOSS,
        increment=tick,
    )
    return {
        "yes_quote": str(yes_quote),
        "no_quote": str(no_quote),
        "yes_distance": str(yes_distance),
        "no_distance": str(no_distance),
        "price_valid": True,
        "reward_ok": reward_ok,
        "rebate_band_ok": rebate_band_ok,
        "maker_ok": maker_ok,
        "yes_cross": yes_cross,
        "no_cross": no_cross,
        "locked_edge": str(locked_edge),
        "rescue_safe": bool(standing.safe),
        "qualified_safe": bool(reward_ok and maker_ok and standing.safe and locked_edge > D("0")),
        "rescue": standing.as_dict(),
    }


async def run() -> None:
    real_discover = probe.base.discover_btc5m
    cached_event = None
    cached_at = 0.0

    async def throttled_discover():
        nonlocal cached_event, cached_at
        now = time.monotonic()
        if cached_event is None or now - cached_at >= DISCOVERY_REFRESH_SECONDS:
            cached_event = await real_discover()
            cached_at = now
        return cached_event

    # Patch only this process. The underlying module remains unchanged for historical
    # reproducibility of the V1 shadow.
    base._evaluate = evaluate_v2
    probe.base.discover_btc5m = throttled_discover
    base.emit(
        "V2_PATCH_ACTIVE",
        liquidity_reward_price_band="0<price<1",
        maker_rebate_band="20c-80c informational only",
        discovery_refresh_seconds=DISCOVERY_REFRESH_SECONDS,
        read_only=True,
        order_entry=False,
    )
    try:
        await base.run()
    finally:
        probe.base.discover_btc5m = real_discover


if __name__ == "__main__":
    asyncio.run(run())
