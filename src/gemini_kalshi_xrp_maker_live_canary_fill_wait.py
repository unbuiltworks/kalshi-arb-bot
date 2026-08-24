"""Wait briefly for a realistically fillable partial-safe XRP maker window.

This wrapper does not weaken any canary economics or freshness gate. It only refuses
to call a maker price actionable unless the existing partial-safe quote is at least
the current Gemini best bid. The underlying canary still owns capital reservation,
one-attempt idempotency, first-fill freeze, exact hedge/flatten, and hard-halt logic.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
import time
from typing import Any

from src import gemini_kalshi_xrp_maker_live_canary as canary

D = Decimal
_ORIGINAL_CHOOSE_QUOTE = canary.choose_quote
WAIT_SECONDS = float(os.getenv("CRYPTO_MAKER_FILL_WAIT_SECONDS", "90"))
RETRY_SECONDS = float(os.getenv("CRYPTO_MAKER_FILL_RETRY_SECONDS", "1.5"))


def fillable_choose_quote(**kwargs: Any):
    quote = _ORIGINAL_CHOOSE_QUOTE(**kwargs)
    if quote is None:
        return None
    if D(str(quote["maker_quote"])) < D(str(kwargs["maker_best_bid"])):
        return None
    return quote


async def main() -> dict[str, Any]:
    if not canary.live_armed():
        result = await canary.run_once()
        print("CRYPTO_XRP_MAKER_FILL_WAIT " + json.dumps({
            "status": "DISARMED_SINGLE_PREFLIGHT",
            "underlying_status": result.get("status"),
            "order_entry": bool(result.get("order_entry")),
        }, separators=(",", ":")), flush=True)
        return result

    canary.choose_quote = fillable_choose_quote
    deadline = time.monotonic() + max(1.0, WAIT_SECONDS)
    attempts = 0
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        attempts += 1
        last = await canary.run_once()
        status = str(last.get("status") or "")
        if bool(last.get("order_entry")):
            print("CRYPTO_XRP_MAKER_FILL_WAIT " + json.dumps({
                "status": "LIVE_CAMPAIGN_ATTEMPTED",
                "attempts": attempts,
                "underlying_status": status,
                "order_entry": True,
            }, separators=(",", ":")), flush=True)
            return last
        if status.startswith("HARD_HALT") or status in {
            "DUPLICATE_CAMPAIGN_BLOCKED",
            "MAKER_FILL_QUANTITY_AMBIGUOUS_HARD_HALT",
            "ZERO_FILL_CANCEL_AMBIGUOUS_HARD_HALT",
        }:
            return last
        if not canary.live_armed():
            return last
        await asyncio.sleep(max(0.25, RETRY_SECONDS))

    print("CRYPTO_XRP_MAKER_FILL_WAIT " + json.dumps({
        "status": "NO_FILLABLE_PARTIAL_SAFE_WINDOW_WITHIN_WAIT",
        "attempts": attempts,
        "last_underlying_status": last.get("status"),
        "order_entry": False,
        "realized": False,
    }, separators=(",", ":")), flush=True)
    return last


if __name__ == "__main__":
    asyncio.run(main())
