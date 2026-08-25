"""Read-only AWS transport latency probe for ARB.

This is the no-credential first gate for comparing an EC2 host with the existing
Railway US-East transport measurements. It performs no order mutations and uses
only public endpoints:

* Gemini: WebSocket ping/pong RTT on the same public host used by the existing
  transport shadow when private Gemini credentials are absent.
* Kalshi: persistent HTTPS GET RTT to /exchange/status.

If the AWS host materially beats Railway on these network floors, run the existing
authenticated transport harness next with credentials provisioned locally on EC2.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
import inspect
import json
import os
import platform
import statistics
import time
from typing import Any

import httpx
import websockets

from src.gemini_prediction_auth import GEMINI_WS

KALSHI_STATUS = "https://external-api.kalshi.com/trade-api/v2/exchange/status"
SAMPLES: dict[str, deque[float]] = {
    "gemini_ws_ping_ms": deque(maxlen=5000),
    "kalshi_public_get_ms": deque(maxlen=5000),
}


def _summary(values: deque[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "n": 0,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "avg_ms": None,
            "max_ms": None,
        }
    ordered = sorted(values)
    n = len(ordered)

    def q(frac: float) -> float:
        idx = min(n - 1, max(0, int(frac * n) - 1))
        return ordered[idx]

    return {
        "n": n,
        "p50_ms": round(q(0.50), 3),
        "p95_ms": round(q(0.95), 3),
        "p99_ms": round(q(0.99), 3),
        "avg_ms": round(statistics.fmean(ordered), 3),
        "max_ms": round(ordered[-1], 3),
    }


def _ws_from_connection(connection: Any):
    return getattr(connection, "ws", connection)


async def _gemini_ping_ms(connection: Any, timeout_s: float = 2.0) -> float:
    ws = _ws_from_connection(connection)
    started = time.perf_counter_ns()
    pong = ws.ping()
    if inspect.isawaitable(pong):
        pong = await pong
    if inspect.isawaitable(pong):
        await asyncio.wait_for(pong, timeout=timeout_s)
    return (time.perf_counter_ns() - started) / 1_000_000


async def _kalshi_get_ms(client: httpx.AsyncClient) -> float:
    started = time.perf_counter_ns()
    response = await client.get(KALSHI_STATUS)
    response.raise_for_status()
    return (time.perf_counter_ns() - started) / 1_000_000


async def _aws_metadata() -> dict[str, Any]:
    """Best-effort IMDSv2 metadata. Never requests IAM credentials."""
    out: dict[str, Any] = {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    base = "http://169.254.169.254/latest"
    try:
        async with httpx.AsyncClient(timeout=0.4) as client:
            token = await client.put(
                f"{base}/api/token",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            )
            if token.status_code != 200:
                return out
            headers = {"X-aws-ec2-metadata-token": token.text}
            for field, path in (
                ("instance_id", "meta-data/instance-id"),
                ("instance_type", "meta-data/instance-type"),
                ("availability_zone", "meta-data/placement/availability-zone"),
            ):
                r = await client.get(f"{base}/{path}", headers=headers)
                if r.status_code == 200:
                    out[field] = r.text
            az = out.get("availability_zone")
            if isinstance(az, str) and len(az) > 1:
                out["region_guess"] = az[:-1]
    except Exception:
        pass
    return out


async def run(*, seconds: float, interval_s: float) -> None:
    metadata = await _aws_metadata()
    print("AWS_TRANSPORT_HOST " + json.dumps(metadata, separators=(",", ":")), flush=True)

    gemini = await websockets.connect(GEMINI_WS, open_timeout=5.0)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(3.0),
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        http2=True,
    ) as kalshi:
        # Warm both connections before samples count.
        await _gemini_ping_ms(gemini)
        await _kalshi_get_ms(kalshi)

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            cycle = time.monotonic()
            errors: list[str] = []
            gemini_ms: float | None = None
            kalshi_ms: float | None = None

            try:
                gemini_ms = await _gemini_ping_ms(gemini)
                SAMPLES["gemini_ws_ping_ms"].append(gemini_ms)
            except Exception as exc:
                errors.append(f"gemini:{type(exc).__name__}:{str(exc)[:120]}")
                try:
                    await gemini.close()
                except Exception:
                    pass
                gemini = await websockets.connect(GEMINI_WS, open_timeout=5.0)

            try:
                kalshi_ms = await _kalshi_get_ms(kalshi)
                SAMPLES["kalshi_public_get_ms"].append(kalshi_ms)
            except Exception as exc:
                errors.append(f"kalshi:{type(exc).__name__}:{str(exc)[:120]}")

            payload = {
                "gemini_ws_ping_ms": round(gemini_ms, 3) if gemini_ms is not None else None,
                "kalshi_public_get_ms": round(kalshi_ms, 3) if kalshi_ms is not None else None,
                "gemini_summary": _summary(SAMPLES["gemini_ws_ping_ms"]),
                "kalshi_summary": _summary(SAMPLES["kalshi_public_get_ms"]),
                "errors": errors,
                "interpretation": "PUBLIC_NETWORK_FLOOR_NOT_ORDER_ACK_LATENCY",
                "read_only": True,
                "order_entry": False,
            }
            print("AWS_TRANSPORT_SAMPLE " + json.dumps(payload, separators=(",", ":")), flush=True)
            await asyncio.sleep(max(0.0, interval_s - (time.monotonic() - cycle)))

    try:
        await gemini.close()
    except Exception:
        pass

    final = {
        "host": metadata,
        "gemini_summary": _summary(SAMPLES["gemini_ws_ping_ms"]),
        "kalshi_summary": _summary(SAMPLES["kalshi_public_get_ms"]),
        "railway_reference": {
            "gemini_p50_ms": 3.8,
            "gemini_p95_ms": 6.3,
            "kalshi_auth_get_p50_ms": 18.1,
            "kalshi_auth_get_p95_ms": 20.9,
            "caveat": "Railway Kalshi reference is authenticated GET; AWS first gate is public GET",
        },
        "next_gate": (
            "Run src.gemini_kalshi_transport_latency_shadow with locally provisioned Kalshi "
            "credentials if AWS public network floor is competitive."
        ),
        "read_only": True,
        "order_entry": False,
    }
    print("AWS_TRANSPORT_FINAL " + json.dumps(final, separators=(",", ":")), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--interval-s", type=float, default=1.0)
    args = parser.parse_args()
    if args.seconds < 10:
        raise SystemExit("--seconds must be >=10")
    if args.interval_s < 0.25:
        raise SystemExit("--interval-s must be >=0.25")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    asyncio.run(run(seconds=args.seconds, interval_s=args.interval_s))


if __name__ == "__main__":
    main()
