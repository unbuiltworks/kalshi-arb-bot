"""Read-only Gemini private REST authentication diagnostic.

Calls only terms/status in a short burst and prints bounded status classifications.
Never mutates orders, positions, terms, or credentials.
"""
from __future__ import annotations

import asyncio
import json

from src.gemini_prediction_auth import private_rest_request


async def _one(index: int) -> dict:
    response = await private_rest_request(
        "GET",
        "/v1/prediction-markets/terms/status",
        timeout=5.0,
    )
    row = {"index": index, "status_code": response.status_code}
    try:
        body = response.json()
    except Exception:
        body = {}
    if response.status_code == 200:
        if isinstance(body, dict):
            row["hasAcceptedLatest"] = body.get("hasAcceptedLatest")
    elif isinstance(body, dict):
        row["error"] = {
            key: str(body.get(key))[:160]
            for key in ("reason", "message", "result", "error")
            if body.get(key) not in (None, "")
        }
    return row


async def run() -> None:
    # Sequential, intentionally without sleeping: if Gemini's time-based REST mode
    # accepts repeated current-second timestamps as documented, all calls should pass.
    rows = []
    for index in range(8):
        rows.append(await _one(index))
    payload = {
        "read_only": True,
        "order_entry": False,
        "calls": len(rows),
        "ok": sum(1 for row in rows if row["status_code"] == 200),
        "statuses": rows,
    }
    print("GEMINI_AUTH_READONLY_PROBE=" + json.dumps(payload, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    asyncio.run(run())
