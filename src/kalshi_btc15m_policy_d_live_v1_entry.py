"""Fail-closed production entrypoint for Kalshi BTC15m Policy-D V1.

The underlying release deliberately owns one client order id per market. A
definite create rejection therefore must terminate the session rather than
allow the market loop to reuse that id. Ambiguous transport failures are
already handled by the underlying engine and are not changed here.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from src.auth import KalshiClient as BaseKalshiClient
from src import kalshi_btc15m_policy_d_live_v1 as engine


class DefiniteOrderCreateRejected(RuntimeError):
    def __init__(self, status_code: int, safe_body: dict[str, Any] | None = None) -> None:
        super().__init__(f"Kalshi order create definitely rejected with HTTP {status_code}")
        self.status_code = int(status_code)
        self.safe_body = dict(safe_body or {})


class FailClosedKalshiClient(BaseKalshiClient):
    async def post(self, path: str, **kwargs):
        response = await super().post(path, **kwargs)
        if path == "/portfolio/events/orders" and response.status_code != 201:
            safe: dict[str, Any] = {}
            try:
                body = response.json()
                if isinstance(body, dict):
                    safe = {k: body.get(k) for k in ("error", "message", "code", "status") if k in body}
            except Exception:
                pass
            raise DefiniteOrderCreateRejected(response.status_code, safe)
        return response


def install() -> None:
    engine.KalshiClient = FailClosedKalshiClient


async def run() -> dict[str, Any]:
    install()
    try:
        return await engine.run()
    except DefiniteOrderCreateRejected as exc:
        state = engine.read_state() or {"classification": engine.CLASSIFICATION, "open": True}
        engine.heartbeat(
            state,
            "ORDER_CREATE_DEFINITE_REJECT_HARD_STOP",
            open=True,
            http_status=exc.status_code,
            response=exc.safe_body,
        )
        result = {
            "status": "ORDER_CREATE_DEFINITE_REJECT_HARD_STOP",
            "hard_halt": True,
            "order_entry": True,
            "http_status": exc.status_code,
            "response": exc.safe_body,
        }
        print("KALSHI_POLICY_D_V1_ENTRY=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
        return result


if __name__ == "__main__":
    asyncio.run(run())
