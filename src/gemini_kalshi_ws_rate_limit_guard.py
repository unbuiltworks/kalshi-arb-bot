"""Rate-limit-safe Gemini authenticated WebSocket startup for live crypto children.

This module changes no market, settlement, quantity, price, fee, capital, freshness,
skew, or execution-policy gate.  It addresses only connection lifecycle after Gemini
returned HTTP 429 during authenticated WebSocket handshakes.

Two protections are installed explicitly:
1. Child startup uses the existing authenticated REST/funding preflight but defers
   authenticated WebSocket verification to the *actual* long-lived
   ``GeminiTradingSession.connect()``.  This removes a redundant throwaway handshake.
2. The parallel parent globally paces child starts and applies a longer cooldown after
   a provably pre-claim startup failure or a pre-send unhealthy trading socket.  No
   order can be submitted during either condition.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import time
from typing import Any, Awaitable, Callable

from src import gemini_kalshi_crypto_parallel_campaign_safe as safe
from src import gemini_kalshi_sol_live_canary_v2 as v2

logger = logging.getLogger("kalshi_arb.gemini_kalshi_ws_rate_limit_guard")

BASE_START_SPACING_S = 2.0
UNHEALTHY_COOLDOWN_S = 8.0

_CHILD_INSTALLED = False
_PARENT_INSTALLED = False


async def _rest_only_authenticated_preflight() -> None:
    """Run the proven private REST/funding preflight without a throwaway WS connect.

    The actual authenticated trading WebSocket remains mandatory: the live executor
    creates ``GeminiTradingSession`` and awaits ``connect()`` before scanning can place
    any order.  A failed session connect exits before the persistent attempt claim, so
    no order is possible.
    """
    kalshi = v2.executor.KalshiV2Executor()
    try:
        report = await v2.executor._preflight(
            kalshi,
            live_requested=False,
            token_path=Path("/tmp/sol-canary-v2-preflight-no-arm"),
        )
        gemini_funded = v2.D(str(report.get("gemini_usd_available") or "0")) >= v2.D("1")
        kalshi_funded = v2.D(str(report.get("kalshi_usd_available") or "0")) >= v2.D("1")
        v2.logger.info(
            "SOL_CANARY_V2_AUTH_PREFLIGHT %s",
            json.dumps({
                "gemini_private_rest_authenticated": True,
                "gemini_authenticated_ws": "DEFERRED_TO_LONG_LIVED_EXECUTION_SESSION",
                "gemini_terms_accepted": bool(report.get("gemini_terms_accepted")),
                "kalshi_production_authenticated": bool(report.get("kalshi_production_authenticated")),
                "gemini_funded_ge_1usd": gemini_funded,
                "kalshi_funded_ge_1usd": kalshi_funded,
                "redundant_ws_handshake_removed": True,
                "order_entry": False,
            }, separators=(",", ":")),
        )
        if not gemini_funded or not kalshi_funded:
            raise RuntimeError("SOL canary v2 authenticated/funding preflight failed")
    finally:
        await kalshi.close()


def install_child_preflight() -> None:
    global _CHILD_INSTALLED
    if _CHILD_INSTALLED:
        return
    v2._readonly_authenticated_preflight = _rest_only_authenticated_preflight
    _CHILD_INSTALLED = True
    logger.warning(
        "CRYPTO_GEMINI_WS_CHILD_PREFLIGHT_GUARD_INSTALLED "
        "throwaway_authenticated_ws=false long_lived_execution_ws_required=true "
        "persistent_claim_before_orders=true risk_gates_changed=false"
    )


class GlobalChildStartPacer:
    def __init__(self, *, spacing_s: float = BASE_START_SPACING_S, cooldown_s: float = UNHEALTHY_COOLDOWN_S):
        if spacing_s < 0 or cooldown_s < 0:
            raise ValueError("WS pacing intervals must be non-negative")
        self.spacing_s = float(spacing_s)
        self.cooldown_s = float(cooldown_s)
        self._lock = asyncio.Lock()
        self._next_start = 0.0

    async def wait_turn(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_start - now)
            if delay:
                await asyncio.sleep(delay)
            started = time.monotonic()
            self._next_start = max(self._next_start, started) + self.spacing_s

    async def penalize(self) -> None:
        async with self._lock:
            self._next_start = max(self._next_start, time.monotonic() + self.cooldown_s)


_PACER = GlobalChildStartPacer()


def cooldown_reason(record: dict[str, Any]) -> str | None:
    if bool(record.get("startup_retry")):
        return "PRECLAIM_CHILD_STARTUP_FAILURE"
    result = record.get("result") or {}
    gemini = result.get("gemini") or {}
    reason = str(gemini.get("reason") or "")
    if reason == "PRE_SEND_TRADING_WS_UNHEALTHY_RETRY_REQUIRED":
        return reason
    return None


def _wrap_parent_obtain(original: Callable[..., Awaitable[dict[str, Any]]]):
    async def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await _PACER.wait_turn()
        record = await original(*args, **kwargs)
        reason = cooldown_reason(record)
        if reason:
            await _PACER.penalize()
            logger.warning(
                "CRYPTO_GEMINI_WS_GLOBAL_COOLDOWN reason=%s cooldown_s=%s "
                "orders_possible=false risk_gates_changed=false",
                reason, _PACER.cooldown_s,
            )
        return record
    return wrapped


def install_parent_spawn_pacing() -> None:
    global _PARENT_INSTALLED
    if _PARENT_INSTALLED:
        return
    safe._obtain_asset_execution = _wrap_parent_obtain(safe._obtain_asset_execution)
    _PARENT_INSTALLED = True
    logger.warning(
        "CRYPTO_GEMINI_WS_PARENT_PACING_INSTALLED global_start_spacing_s=%s "
        "unhealthy_cooldown_s=%s parallel_handshake_storm_prevented=true "
        "risk_gates_changed=false",
        _PACER.spacing_s, _PACER.cooldown_s,
    )
