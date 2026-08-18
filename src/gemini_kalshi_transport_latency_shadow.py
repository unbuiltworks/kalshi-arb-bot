"""Read-only transport latency probe for Gemini/Kalshi execution infrastructure.

Measures the steady-state network floor from the current host without submitting,
previewing, canceling, or modifying orders:

* Gemini: ping/pong RTT on the already-authenticated trading WebSocket used by the
  execution transport.
* Kalshi: authenticated GET /exchange/status RTT through the same persistent httpx
  client class used by the order executor.

Order mutation methods are replaced with fail-closed stubs in this process. This
service is telemetry only and cannot authorize trading.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
import inspect
import json
import logging
import statistics
import time
from typing import Any

from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor

logger = logging.getLogger("kalshi_arb.gemini_kalshi_transport_latency_shadow")
SAMPLES: dict[str, deque[float]] = {
    "gemini_ws_ping_ms": deque(maxlen=2000),
    "kalshi_auth_get_ms": deque(maxlen=2000),
}


async def _mutation_forbidden(*args, **kwargs):
    del args, kwargs
    raise RuntimeError("order mutation disabled in transport latency shadow")


# Fail closed even if a future refactor accidentally reaches a mutation method.
GeminiTradingSession.place_fok = _mutation_forbidden  # type: ignore[assignment]
GeminiTradingSession.emergency_flatten_market = _mutation_forbidden  # type: ignore[assignment]
KalshiV2Executor.place = _mutation_forbidden  # type: ignore[assignment]


def _summary(values: deque[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "avg_ms": None, "max_ms": None}
    ordered = sorted(values)
    n = len(ordered)
    p50 = ordered[min(n - 1, max(0, int(0.50 * n) - 1))]
    p95 = ordered[min(n - 1, max(0, int(0.95 * n) - 1))]
    return {
        "n": n,
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "avg_ms": round(statistics.fmean(ordered), 3),
        "max_ms": round(ordered[-1], 3),
    }


async def gemini_ping_rtt_ms(session: GeminiTradingSession, *, timeout_s: float = 2.0) -> float:
    if session.ws is None:
        raise RuntimeError("Gemini trading WebSocket is not connected")
    started = time.perf_counter_ns()
    pong = session.ws.ping()
    # websockets versions differ: ping() can return an awaitable that resolves to a
    # pong waiter, or directly return the pong waiter.
    if inspect.isawaitable(pong):
        pong = await pong
    if inspect.isawaitable(pong):
        await asyncio.wait_for(pong, timeout=timeout_s)
    return (time.perf_counter_ns() - started) / 1_000_000


async def kalshi_get_rtt_ms(executor: KalshiV2Executor) -> float:
    started = time.perf_counter_ns()
    response = await executor.client.get("/exchange/status")
    response.raise_for_status()
    return (time.perf_counter_ns() - started) / 1_000_000


async def _connect_gemini() -> GeminiTradingSession:
    session = GeminiTradingSession()
    started = time.perf_counter_ns()
    await session.connect()
    connect_ms = (time.perf_counter_ns() - started) / 1_000_000
    logger.info(
        "CRYPTO_TRANSPORT_GEMINI_CONNECTED %s",
        json.dumps({
            "authenticated_trading_ws_connect_ms": round(connect_ms, 3),
            "orders_subscription_read_only": True,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    return session


async def run(*, seconds: float, interval_s: float) -> None:
    deadline = None if seconds <= 0 else time.monotonic() + seconds
    gemini = await _connect_gemini()
    kalshi = KalshiV2Executor()
    try:
        # First Kalshi request intentionally records cold-ish setup, then the recurring
        # samples use the same persistent client/connection pool.
        cold_ms = await kalshi_get_rtt_ms(kalshi)
        logger.info(
            "CRYPTO_TRANSPORT_KALSHI_WARMED %s",
            json.dumps({
                "first_authenticated_get_ms": round(cold_ms, 3),
                "endpoint": "/exchange/status",
                "persistent_http_client": True,
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")),
        )

        while deadline is None or time.monotonic() < deadline:
            cycle_started = time.monotonic()
            gemini_ms: float | None = None
            kalshi_ms: float | None = None
            errors: list[str] = []

            try:
                gemini_ms = await gemini_ping_rtt_ms(gemini)
                SAMPLES["gemini_ws_ping_ms"].append(gemini_ms)
            except Exception as exc:
                errors.append(f"gemini:{type(exc).__name__}:{str(exc)[:120]}")
                try:
                    await gemini.close()
                finally:
                    gemini = await _connect_gemini()

            try:
                kalshi_ms = await kalshi_get_rtt_ms(kalshi)
                SAMPLES["kalshi_auth_get_ms"].append(kalshi_ms)
            except Exception as exc:
                errors.append(f"kalshi:{type(exc).__name__}:{str(exc)[:120]}")

            combined_lower_bound_ms = None
            if gemini_ms is not None and kalshi_ms is not None:
                # Concurrent dual submission cannot finish sooner than the slower venue
                # acknowledgement path. Ping/GET are only network-floor proxies, not
                # order-ack guarantees.
                combined_lower_bound_ms = max(gemini_ms, kalshi_ms)

            payload: dict[str, Any] = {
                "gemini_ws_ping_ms": round(gemini_ms, 3) if gemini_ms is not None else None,
                "kalshi_auth_get_ms": round(kalshi_ms, 3) if kalshi_ms is not None else None,
                "concurrent_transport_floor_proxy_ms": (
                    round(combined_lower_bound_ms, 3) if combined_lower_bound_ms is not None else None
                ),
                "gemini_summary": _summary(SAMPLES["gemini_ws_ping_ms"]),
                "kalshi_summary": _summary(SAMPLES["kalshi_auth_get_ms"]),
                "errors": errors,
                "interpretation": "NETWORK_FLOOR_PROXY_NOT_ORDER_ACK_LATENCY",
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }
            logger.info("CRYPTO_TRANSPORT_LATENCY_SAMPLE %s", json.dumps(payload, separators=(",", ":")))

            sleep_s = max(0.0, interval_s - (time.monotonic() - cycle_started))
            if deadline is not None:
                sleep_s = min(sleep_s, max(0.0, deadline - time.monotonic()))
            if sleep_s:
                await asyncio.sleep(sleep_s)
    finally:
        await gemini.close()
        await kalshi.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--interval-s", type=float, default=5.0)
    args = parser.parse_args()
    if args.interval_s < 1.0:
        raise SystemExit("--interval-s must be >=1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger.info(
        "CRYPTO_TRANSPORT_LATENCY_SHADOW_ENABLED gemini=authenticated_ws_ping "
        "kalshi=authenticated_persistent_get interval_s=%s "
        "mutation_methods_disabled=true trade_authorized=false read_only=true order_entry=false",
        args.interval_s,
    )
    asyncio.run(run(seconds=args.seconds, interval_s=args.interval_s))


if __name__ == "__main__":
    main()
