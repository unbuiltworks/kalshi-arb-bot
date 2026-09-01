"""Read-only Gemini private REST authentication diagnostic.

Calls only terms/status and prints a bounded error classification. Never mutates
orders, positions, terms, or credentials.
"""
from __future__ import annotations

import asyncio
import json

from src.gemini_prediction_auth import private_rest_request


async def run() -> None:
    response = await private_rest_request(
        "GET",
        "/v1/prediction-markets/terms/status",
        timeout=5.0,
    )
    payload = {"status_code": response.status_code, "read_only": True, "order_entry": False}
    if response.status_code != 200:
        try:
            body = response.json()
        except Exception:
            body = {"message": response.text[:240]}
        if isinstance(body, dict):
            payload["error"] = {
                key: str(body.get(key))[:240]
                for key in ("reason", "message", "result", "error")
                if body.get(key) not in (None, "")
            }
    else:
        try:
            body = response.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            payload["hasAcceptedLatest"] = body.get("hasAcceptedLatest")
    print("GEMINI_AUTH_READONLY_PROBE=" + json.dumps(payload, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    asyncio.run(run())
