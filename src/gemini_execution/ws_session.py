"""The authenticated WebSocket order session -- RECOVERED, not reinvented.

This is `GeminiMakerSession` from src/gemini_kalshi_xrp_maker_live_canary.py
(commit 2bc6c04c), the session every accepted order on these credentials was
placed through. It is restored rather than rewritten because a transport that
has actually worked against a venue is evidence, and a fresh one would not be.

WHAT CHANGED, AND WHY EACH CHANGE IS A NARROWING:

  it lives in src/gemini_execution/  so the repo-wide isolation test still
                                     holds: exactly one package may reach a
                                     mutation surface, and `order.place` is
                                     now named as one of those surfaces.
  request() is ARM-GATED             on mutation methods only. Reads and the
                                     orders@account subscription work while
                                     disarmed, because reconciliation has to
                                     work when placing does not.
  order.cancel_session is REFUSED    it is a blanket cancel. The recovered
                                     code fell back to it when it had no
                                     order id; this canary places exactly one
                                     order, knows its id, and reports
                                     OUTSTANDING rather than cancelling
                                     everything on the account.

WHAT DELIBERATELY DID NOT CHANGE: the handshake (authenticated_ws_connect),
the request/response correlation by `id`, the orderUpdate event queue, and
the ack contract that acceptance is status 200. Signing is untouched and
still lives in gemini_prediction_auth.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional

from src.gemini_execution import arming, spec


class WsRefused(RuntimeError):
    """Refused before the frame reached the socket."""


class GeminiMakerSession:
    def __init__(self, *, id_prefix: str = "l5-canary") -> None:
        self.ws = None
        self.reader: Optional[asyncio.Task] = None
        self.responses: dict[str, asyncio.Future] = {}
        self.events: asyncio.Queue = asyncio.Queue()
        self.lock = asyncio.Lock()
        self.id_prefix = id_prefix
        self.sent: list[dict[str, Any]] = []

    async def connect(self) -> None:
        from src.gemini_prediction_auth import authenticated_ws_connect
        self.ws = await authenticated_ws_connect(timeout=5.0)
        self.reader = asyncio.create_task(self._read())
        ack = await self.request(spec.WS_SUBSCRIBE_METHOD,
                                 [spec.WS_ORDERS_CHANNEL], timeout=5.0)
        if int(ack.get("status", 0)) != spec.WS_ACK_OK:
            raise WsRefused("orders@account subscription failed: %s"
                            % ack.get("status"))

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

    def frame(self, method: str, params: Any, *,
              request_id: Optional[str] = None) -> dict[str, Any]:
        """Exactly what goes on the wire. Built here so it can be rendered
        without a socket, and so nothing can send a shape this never saw."""
        rid = request_id or "%s-%s" % (self.id_prefix, uuid.uuid4().hex)
        return {"id": rid, "method": method, "params": params}

    async def request(self, method: str, params: Any, *,
                      timeout: float = 3.0,
                      now: Optional[float] = None) -> dict[str, Any]:
        if method == spec.WS_CANCEL_SESSION_METHOD:
            raise WsRefused(
                "order.cancel_session is a blanket cancel; this canary "
                "cancels the one order it placed or reports OUTSTANDING")
        if method in spec.WS_MUTATION_METHODS:
            # structural: no arm, no frame. Reads stay available disarmed
            # because reconciliation must work when placing does not.
            arming.require(now)
        if self.ws is None:
            raise WsRefused("session not connected")
        msg = self.frame(method, params)
        rid = msg["id"]
        fut = asyncio.get_running_loop().create_future()
        self.responses[rid] = fut
        try:
            async with self.lock:
                await self.ws.send(json.dumps(msg, separators=(",", ":")))
                self.sent.append(msg)
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self.responses.pop(rid, None)
