"""Settlement-research-only branch entrypoint.

Railway may launch ``python -m src`` even when a custom start command is staged.
On this isolated branch, make that fallback read-only by routing it to the historical
Gemini/Kalshi settlement agreement study instead of the trading bot.
"""
from __future__ import annotations

import asyncio
import logging

from src.gemini_kalshi_settlement_agreement import run


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
asyncio.run(run(hours=48, gemini_limit=2000, max_expiry_skew_s=30.0))
