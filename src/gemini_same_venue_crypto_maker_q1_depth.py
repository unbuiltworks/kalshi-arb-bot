"""Gemini 100ms partial-depth transport for same-venue q=1 ladder maker.

Uses the documented {symbol}@depth5@100ms periodic snapshot stream for active market
making. Each base executor sibling gets its own public WebSocket, so the task already
knows which symbol a snapshot belongs to even though partial-depth frames do not
carry an event type. Private order/fill/FOK transport remains unchanged.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
import time
from typing import Any

import websockets

from src import gemini_same_venue_crypto_maker_q1 as base
from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_live_shadow import GEMINI_WS

D = Decimal
REST_SECONDS = max(60.0, min(600.0, float(os.getenv("GEMINI_SAME_VENUE_REST_SECONDS", "240"))))


def _levels(rows: Any) -> list[tuple[D, D]]:
    out: list[tuple[D, D]] = []
    for row in rows or []:
        try:
            p, q = D(str(row[0])), D(str(row[1]))
        except Exception:
            continue
        if D("0") < p < D("1") and q > 0:
            out.append((p, q))
    return out


def _snapshot_top(symbol: str, payload: dict[str, Any], ts_ms: int) -> TopOfBook:
    bids = _levels(payload.get("bids"))
    asks = _levels(payload.get("asks"))
    bid = max(bids, key=lambda x: x[0]) if bids else None
    ask = min(asks, key=lambda x: x[0]) if asks else None
    return TopOfBook(
        venue="gemini", market_id=symbol, ts_ms=int(ts_ms),
        yes_bid=bid[0] if bid else D("0"),
        yes_bid_size=bid[1] if bid else D("0"),
        yes_ask=ask[0] if ask else D("1"),
        yes_ask_size=ask[1] if ask else D("0"),
    )


async def _depth_book_stream(symbol: str, kind: str, queue: asyncio.Queue) -> None:
    backoff = 0.25
    while True:
        try:
            async with websockets.connect(GEMINI_WS, open_timeout=5.0) as ws:
                await ws.send(json.dumps({
                    "id": f"ladder-depth-{kind}",
                    "method": "SUBSCRIBE",
                    "params": [f"{symbol}@depth5@100ms"],
                }, separators=(",", ":")))
                backoff = 0.25
                async for raw in ws:
                    payload = json.loads(raw)
                    if payload.get("status") is not None:
                        if int(payload.get("status")) >= 400:
                            raise RuntimeError(f"Gemini depth subscription failed: {payload}")
                        continue
                    if not isinstance(payload.get("bids"), list) or not isinstance(payload.get("asks"), list):
                        continue
                    top = _snapshot_top(symbol, payload, int(time.time() * 1000))
                    try:
                        queue.put_nowait((kind, top))
                    except asyncio.QueueFull:
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        queue.put_nowait((kind, top))
            raise RuntimeError(f"Gemini depth stream ended for {symbol}")
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(5.0, backoff * 2)


async def run():
    base._book_stream = _depth_book_stream
    base.REST_SECONDS = REST_SECONDS
    return await base.run()


if __name__ == "__main__":
    asyncio.run(run())
