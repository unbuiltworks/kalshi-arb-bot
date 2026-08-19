"""Safety guard for Kalshi 15m lifecycle target cache.

A floor strike observed before a new 15-minute window may later be changed by a
``metadata_updated`` lifecycle event. This guard prevents any cached target whose
receive timestamp predates the current 15-minute boundary from being used after that
boundary. It does not infer a strike or alter execution thresholds.
"""
from __future__ import annotations

import time
from typing import Any

from src import gemini_kalshi_target_lifecycle_cache as cache

WINDOW_MS = 15 * 60 * 1000
_INSTALLED = False


def current_boundary_ms(now_ms: int | None = None) -> int:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    return (now // WINDOW_MS) * WINDOW_MS


def row_is_current_window(row: tuple[Any, ...] | None, now_ms: int | None = None) -> bool:
    if row is None or len(row) < 3:
        return False
    try:
        received_ms = int(row[2])
    except (TypeError, ValueError):
        return False
    return received_ms >= current_boundary_ms(now_ms)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = cache._target_for_ticker

    def _fresh_target_for_ticker(ticker: str):
        row = original(ticker)
        return row if row_is_current_window(row) else None

    cache._target_for_ticker = _fresh_target_for_ticker
    _INSTALLED = True
    cache.logger.warning(
        "CRYPTO_TARGET_FRESHNESS_GUARD_INSTALLED rule=received_at_or_after_current_15m_boundary risk_gates_changed=false"
    )
