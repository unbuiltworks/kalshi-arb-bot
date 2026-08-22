"""Prewarm one Gemini authenticated trading WebSocket off the execution path.

Market data must exist before any opportunity can be actionable, so the shared Gemini
market-data connection gets first claim on the venue's handshake budget. Once that
connection is healthy, this module waits a short settle interval and warms exactly one
private trading session per bounded child.

Warmup remains fail-closed: connection failures retry in the background, and an
opportunity that arrives before the socket is ready receives a confirmed zero-fill
rejection. No Kalshi hedge is sent unless Gemini terminally fills. Price, depth,
profit, freshness, skew, survival, arm-token, and staged execution gates are unchanged.

This module also installs read-only near-miss attribution around the existing HOT
evaluator. It recomputes the same small-q candidate set only for diagnostics and emits
30-second distributions of the best modeled after-fee locked profit. It never changes
qualification or order authorization.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import time

from src import gemini_kalshi_micro_live_executor_20bp as micro
from src.gemini_kalshi_probe_transport import GeminiTradingSession, ExecutionResult
from src.gemini_ws_connection_coordination import market_data_ready_event

D = Decimal
_INSTALLED = False
PRIVATE_WS_SETTLE_AFTER_MARKET_DATA_S = 1.5
_NEAR_MISS = {}
_NEAR_MISS_LOG_S = 30.0


class DeferredGeminiTradingSession(GeminiTradingSession):
    """One private session per child, prewarmed only after market data is healthy."""

    def __init__(self) -> None:
        super().__init__()
        self._warm_task: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._closing = False

    async def _connect_once(self) -> None:
        if self.ws is not None:
            return
        async with self._connect_lock:
            if self.ws is None and not self._closing:
                await super().connect()

    async def _warm_loop(self) -> None:
        backoff = 1.0
        attempt = 0
        ready = market_data_ready_event()
        while not self._closing and self.ws is None:
            await ready.wait()
            await asyncio.sleep(PRIVATE_WS_SETTLE_AFTER_MARKET_DATA_S)
            if self._closing:
                return
            if not ready.is_set():
                continue
            attempt += 1
            try:
                await self._connect_once()
                if self.ws is not None:
                    micro.logger.warning(
                        "CRYPTO_GEMINI_TRADING_WS_PREWARM_READY attempts=%s "
                        "after_market_data=true settle_s=%.1f critical_path_handshake=false "
                        "risk_gates_changed=false",
                        attempt,
                        PRIVATE_WS_SETTLE_AFTER_MARKET_DATA_S,
                    )
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                micro.logger.warning(
                    "CRYPTO_GEMINI_TRADING_WS_PREWARM_RETRY attempt=%s type=%s "
                    "error=%s backoff_s=%.1f after_market_data=true order_entry=false",
                    attempt,
                    type(exc).__name__,
                    str(exc)[:160],
                    backoff,
                )
                try:
                    await super().close()
                except Exception:
                    pass
                if not self._closing:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, 10.0)

    async def connect(self) -> None:
        if self.ws is not None:
            return
        if self._warm_task is None or self._warm_task.done():
            self._warm_task = asyncio.create_task(
                self._warm_loop(), name="crypto-gemini-trading-warmup"
            )

    async def _connect_for_order(self) -> None:
        if self.ws is not None:
            return
        if not market_data_ready_event().is_set():
            raise RuntimeError("Gemini market data is not connected")
        if self._connect_lock.locked():
            raise RuntimeError("Gemini trading WS warmup still in progress")
        await self._connect_once()

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

    async def close(self) -> None:
        self._closing = True
        if self._warm_task is not None and self._warm_task is not asyncio.current_task():
            self._warm_task.cancel()
            await asyncio.gather(self._warm_task, return_exceptions=True)
            self._warm_task = None
        await super().close()


def _install_near_miss_attribution() -> None:
    prewarmed_base = micro.exact.safe.base
    original = prewarmed_base._evaluate_event
    if getattr(original, "_crypto_near_miss_wrapped", False):
        return

    def wrapped(**kwargs):
        feed = kwargs.get("feed")
        if feed is not None and feed.gemini is not None and feed.kalshi is not None:
            asset = str(feed.asset).upper()
            state = _NEAR_MISS.setdefault(asset, {
                "evaluations": 0,
                "raw_positive": 0,
                "ge_1c": 0,
                "ge_2c": 0,
                "ge_3c": 0,
                "best": None,
                "last_log": time.monotonic(),
            })
            state["evaluations"] += 1
            try:
                rows = prewarmed_base.hot._small_q_candidates(
                    feed.gemini,
                    feed.kalshi,
                    kalshi_fee=kwargs["kalshi_fee"],
                    max_quote_skew_ms=kwargs["max_quote_skew_ms"],
                    capital_limit=kwargs["capital_limit"],
                )
                if rows:
                    best = max(D(str(candidate.locked_profit)) for _q, candidate in rows)
                    state["raw_positive"] += 1
                    state["best"] = best if state["best"] is None else max(state["best"], best)
                    if best >= D("0.01"):
                        state["ge_1c"] += 1
                    if best >= D("0.02"):
                        state["ge_2c"] += 1
                    if best >= D("0.03"):
                        state["ge_3c"] += 1
            except Exception as exc:
                micro.logger.warning(
                    "CRYPTO_NEAR_MISS_DIAGNOSTIC_ERROR asset=%s type=%s error=%s",
                    asset, type(exc).__name__, str(exc)[:160],
                )
            now = time.monotonic()
            if now - state["last_log"] >= _NEAR_MISS_LOG_S:
                micro.logger.info(
                    "CRYPTO_NEAR_MISS_SUMMARY %s",
                    json.dumps({
                        "asset": asset,
                        "evaluations": state["evaluations"],
                        "raw_positive": state["raw_positive"],
                        "best_after_fee_locked_profit": str(state["best"]) if state["best"] is not None else None,
                        "ge_1c": state["ge_1c"],
                        "ge_2c": state["ge_2c"],
                        "ge_3c": state["ge_3c"],
                        "configured_execution_floor": str(kwargs.get("min_profit")),
                        "diagnostic_only": True,
                        "risk_gates_changed": False,
                        "order_entry": False,
                    }, separators=(",", ":")),
                )
                state.update({
                    "evaluations": 0,
                    "raw_positive": 0,
                    "ge_1c": 0,
                    "ge_2c": 0,
                    "ge_3c": 0,
                    "best": None,
                    "last_log": now,
                })
        return original(**kwargs)

    wrapped._crypto_near_miss_wrapped = True
    prewarmed_base._evaluate_event = wrapped


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    micro.GeminiTradingSession = DeferredGeminiTradingSession
    _install_near_miss_attribution()
    _INSTALLED = True
    micro.logger.warning(
        "CRYPTO_DEFERRED_GEMINI_TRADING_WS_INSTALLED startup_private_ws=after_market_data "
        "single_private_socket_per_child=true connect_failure_zero_fill=true "
        "kalshi_order_after_gemini_fill_only=true near_miss_attribution=true "
        "risk_gates_changed=false"
    )
