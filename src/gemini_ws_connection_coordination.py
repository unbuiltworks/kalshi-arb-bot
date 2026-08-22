"""Process-local coordination for the crypto lane's Gemini WebSocket handshakes.

Market data is required before an opportunity can be actionable, so it gets first
claim on a Gemini connection.  The private execution session waits for this event
before prewarming.  This module contains no credentials and grants no order authority.
"""
from __future__ import annotations

import asyncio

_MARKET_DATA_READY: asyncio.Event | None = None


def market_data_ready_event() -> asyncio.Event:
    global _MARKET_DATA_READY
    if _MARKET_DATA_READY is None:
        _MARKET_DATA_READY = asyncio.Event()
    return _MARKET_DATA_READY


def mark_market_data_ready() -> None:
    market_data_ready_event().set()


def mark_market_data_unready() -> None:
    market_data_ready_event().clear()
