"""Bounded Gemini BTC5m L5 canary over the venue-confirmed WS transport.

Prepared only. Nothing in this module submits unless submit_once() is called
with an explicit non-empty owner authorization id. There is no CLI auto-submit.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from src.gemini_prediction_auth import authenticated_ws_connect, private_rest_request

QTY = "0.01"
ORDER_TYPE = "LIMIT"
TIF = "MOC"
MAX_ORDERS = 1
MAX_RESIDUAL = "0.01"


class GeminiMakerSession:
    """Reviewed historical transport shape, isolated from strategy logic."""
    def __init__(self) -> None:
        self.ws = None
        self.reader: asyncio.Task | None = None
        self.responses: dict[str, asyncio.Future] = {}
        self.events: asyncio.Queue = asyncio.Queue()
        self.lock = asyncio.Lock()

    async def connect(self) -> None:
        self.ws = await authenticated_ws_connect(timeout=5.0)
        self.reader = asyncio.create_task(self._read())
        ack = await self.request("SUBSCRIBE", ["orders@account"], timeout=5.0)
        if int(ack.get("status", 0)) != 200:
            raise RuntimeError("Gemini orders@account subscription failed")

    async def close(self) -> None:
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def _read(self) -> None:
        assert self.ws is not None
        async for raw in self.ws:
            msg = json.loads(raw)
            rid = str(msg.get("id") or "")
            if rid:
                fut = self.responses.get(rid)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
                continue
            if msg.get("e") == "orderUpdate":
                await self.events.put(msg)

    async def request(self, method: str, params: Any, *, timeout: float = 4.0) -> dict:
        if self.ws is None:
            raise RuntimeError("Gemini maker session not connected")
        rid = "l5-" + uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.responses[rid] = fut
        try:
            async with self.lock:
                await self.ws.send(json.dumps(
                    {"id": rid, "method": method, "params": params},
                    separators=(",", ":")))
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self.responses.pop(rid, None)


def order_params(preflight: Mapping[str, Any], client_order_id: str) -> dict:
    """Translate the already-selected L5 surface rung into Gemini's WS dialect."""
    if not preflight.get("ok"):
        raise ValueError("fresh preflight is not OK")
    symbol = str(preflight["instrument"])
    price = str(preflight["limit_price"])
    if not symbol.startswith("GEMI-BTC05M"):
        raise ValueError("BTC5m only")
    if str(preflight.get("quantity")) != QTY:
        raise ValueError("quantity envelope changed")
    if not preflight.get("restability", {}).get("restable"):
        raise ValueError("order would not rest")
    return {"symbol": symbol, "side": "BUY", "type": ORDER_TYPE,
            "timeInForce": TIF, "price": price, "quantity": QTY,
            "eventOutcome": "YES", "clientOrderId": client_order_id[:36]}


def sanitized_ws_frame(preflight: Mapping[str, Any]) -> dict:
    """Exact user-reviewable frame, with only the request id/client id sanitized."""
    return {"id": "<request-id>", "method": "order.place",
            "params": order_params(preflight, "<owner-auth>-l5ws")}


def _consume_latch(root: Path, authorization_id: str, intent: Mapping[str, Any]) -> Path:
    """Durable one-shot latch. O_EXCL means a consumed authorization never repeats."""
    if not authorization_id.strip():
        raise PermissionError("explicit owner authorization id required")
    root.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in authorization_id if c.isalnum() or c in "-_")[:80]
    p = root / ("latch_" + safe + ".json")
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"authorization_id": authorization_id, "consumed_utc": time.time(),
                   "intent": dict(intent), "status": "CONSUMED_BEFORE_MUTATION"}, fh)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    return p


async def _order_history(client_id: str, symbol: str) -> dict | None:
    now_ms = int(time.time() * 1000)
    for status in ("filled", "cancelled"):
        r = await private_rest_request(
            "POST", "/v1/prediction-markets/orders/history",
            params={"status": status, "symbol": symbol, "limit": 100,
                    "from": now_ms - 120000, "to": now_ms + 2000}, timeout=8.0)
        if r.status_code != 200:
            continue
        body = r.json()
        for row in body.get("orders", []) if isinstance(body, Mapping) else []:
            if str(row.get("clientOrderId") or "") == client_id:
                return dict(row)
    return None


async def submit_once(*, authorization_id: str, latch_root: str,
                      fresh_preflight: Mapping[str, Any]) -> dict:
    """One order maximum. Explicit owner authorization is mandatory.

    Latch + intent are durable BEFORE authenticated order.place. Reconciliation
    uses the venue's order history, never a theoretical fill model.
    """
    client_id = ("l5ws-" + authorization_id)[:36]
    params = order_params(fresh_preflight, client_id)
    intent = {"method": "order.place", "params": params, "max_orders": MAX_ORDERS,
              "max_residual": MAX_RESIDUAL, "maker_only": True}
    latch = _consume_latch(Path(latch_root), authorization_id, intent)
    session = GeminiMakerSession()
    try:
        await session.connect()
        ack = await session.request("order.place", params, timeout=4.0)
        accepted = int(ack.get("status", 0)) == 200
        row = await _order_history(client_id, params["symbol"]) if accepted else None
        return {"status": "SUBMITTED" if accepted else "REJECTED",
                "ack_status": ack.get("status"), "clientOrderId": client_id,
                "latch": str(latch), "venue_order": row,
                "realized": False}
    finally:
        await session.close()
