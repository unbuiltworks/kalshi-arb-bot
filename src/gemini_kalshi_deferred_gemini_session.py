"""Defer Gemini's authenticated trading WebSocket until actual order submission.

Public market data can scan continuously without consuming an authenticated trading
socket. The live execution path still requires Gemini authentication, but it connects
only after all existing price/depth/profit/boundary/freshness/skew/survival gates pass.
If that pre-send connection is rate-limited, return a confirmed zero-fill rejection;
the staged transport therefore sends no Kalshi hedge order and the bounded campaign
may retry safely.
"""
from __future__ import annotations

import time

from src import gemini_kalshi_micro_live_executor_20bp as micro
from src.gemini_kalshi_probe_transport import GeminiTradingSession, ExecutionResult

_INSTALLED = False


class DeferredGeminiTradingSession(GeminiTradingSession):
    async def connect(self) -> None:
        # Deliberately do not open the private WS during scanner startup.
        return None

    async def _connect_for_order(self) -> None:
        if self.ws is None:
            await super().connect()

    async def place_fok(self, params: dict, *, terminal_timeout: float = 2.0) -> ExecutionResult:
        if self.ws is None:
            started = time.perf_counter_ns()
            try:
                await self._connect_for_order()
            except Exception as exc:
                done = time.perf_counter_ns()
                return ExecutionResult(
                    venue="gemini",
                    client_order_id=str(params.get("clientOrderId") or ""),
                    filled_quantity="0",
                    average_fill_price=None,
                    fee_paid=None,
                    status="REJECTED",
                    reason=f"AUTH_WS_CONNECT_FAILED:{type(exc).__name__}:{str(exc)[:160]}",
                    local_send_ns=started,
                    local_done_ns=done,
                    exchange_ts_ns=None,
                    request_latency_ms=(done - started) / 1_000_000,
                    ack_latency_ms=None,
                    raw_request_status=None,
                    reconciled=False,
                )
        return await super().place_fok(params, terminal_timeout=terminal_timeout)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    micro.GeminiTradingSession = DeferredGeminiTradingSession
    _INSTALLED = True
    micro.logger.warning(
        "CRYPTO_DEFERRED_GEMINI_TRADING_WS_INSTALLED startup_private_ws=false "
        "connect_at_actionable_trade=true connect_failure_zero_fill=true "
        "kalshi_order_after_gemini_fill_only=true risk_gates_changed=false"
    )
