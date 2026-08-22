"""Candidate-specific wrapper for the bounded q=1 same-Gemini combo live canary.

Reuses the already-tested execution, idempotency, TTL, cancel, flatten, and
hard-halt logic from gemini_extreme_combo_live_canary. Live mutation remains
impossible unless the base canary's two independent campaign arms match.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import src.gemini_extreme_combo_live_canary as canary

canary.COMBO = "GEMI-CMB-260823-5067A7AC7F468773-CMB"
canary.HEDGES = [
    {
        "symbol": "GEMI-EPL-2608221400-CRY-EVE-M-CRY",
        "outcome": "YES",
        "max_price": Decimal("0.02"),
    },
    {
        "symbol": "GEMI-F1-DUTGP-WIN-20260823-ALB",
        "outcome": "YES",
        "max_price": Decimal("0.02"),
    },
]
canary.COMBO_MAKER_PRICE = Decimal("0.90")
canary.TARGET_LOCK = Decimal("0.03")
canary.QTY = Decimal("1")


if __name__ == "__main__":
    asyncio.run(canary.run_once())
