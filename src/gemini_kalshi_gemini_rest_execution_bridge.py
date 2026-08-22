"""Use Gemini REST FOK only when the live private trading WS is unavailable.

This is an execution-transport bridge, not a gate change. The existing persistent WS
remains preferred. The bridge is installed after the deferred/self-healing session so
it can inspect that exact session's health without another handshake.
"""
from __future__ import annotations

from typing import Any

from src import gemini_kalshi_deferred_gemini_session as deferred
from src import gemini_kalshi_gemini_rest_fok_fallback as rest_fok
from src import gemini_kalshi_micro_live_executor_20bp as micro
from src.gemini_kalshi_probe_transport import GeminiTradingSession, ExecutionResult

_INSTALLED = False
_ORIGINAL_DEFERRED_PLACE = deferred.DeferredGeminiTradingSession.place_fok


async def place_fok_with_rest_bridge(
    self: deferred.DeferredGeminiTradingSession,
    params: dict[str, Any],
    *,
    terminal_timeout: float = 2.0,
) -> ExecutionResult:
    if self._socket_healthy_local():
        return await _ORIGINAL_DEFERRED_PLACE(
            self,
            params,
            terminal_timeout=terminal_timeout,
        )

    self._ensure_warm_task()
    micro.logger.warning(
        "CRYPTO_GEMINI_REST_FOK_FALLBACK_TRIGGERED symbol=%s quantity=%s "
        "private_ws_healthy=false gemini_first=true kalshi_not_yet_sent=true "
        "ambiguity_hard_halts=true risk_gates_changed=false",
        str(params.get("symbol") or ""),
        str(params.get("quantity") or ""),
    )
    result = await rest_fok.place_primary_fok(params, timeout=terminal_timeout)
    micro.logger.warning(
        "CRYPTO_GEMINI_REST_FOK_FALLBACK_RESULT status=%s filled_quantity=%s "
        "reason=%s http_status=%s",
        result.status,
        result.filled_quantity,
        result.reason,
        result.raw_request_status,
    )
    return result


async def emergency_flatten_with_rest_bridge(
    self: deferred.DeferredGeminiTradingSession,
    original_params: dict[str, Any],
    *,
    timeout: float = 2.0,
) -> ExecutionResult:
    if self._socket_healthy_local():
        return await GeminiTradingSession.emergency_flatten_market(
            self,
            original_params,
            timeout=timeout,
        )

    self._ensure_warm_task()
    micro.logger.critical(
        "CRYPTO_GEMINI_REST_EMERGENCY_FLATTEN_TRIGGERED symbol=%s quantity=1 "
        "private_ws_healthy=false aggressive_sell_limit=0.01 ambiguity_hard_halts=true",
        str(original_params.get("symbol") or ""),
    )
    return await rest_fok.emergency_flatten_fok(original_params, timeout=timeout)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    deferred.DeferredGeminiTradingSession.place_fok = place_fok_with_rest_bridge
    deferred.DeferredGeminiTradingSession.emergency_flatten_market = emergency_flatten_with_rest_bridge
    _INSTALLED = True
    micro.logger.warning(
        "CRYPTO_GEMINI_REST_EXECUTION_BRIDGE_INSTALLED ws_preferred=true "
        "rest_primary_fok_when_ws_unhealthy=true rest_emergency_flatten=true "
        "q1_only=true ambiguity_hard_halts=true gemini_first=true risk_gates_changed=false"
    )
