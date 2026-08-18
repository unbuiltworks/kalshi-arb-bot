"""Small public Gemini Predictions depth reader for final micro-live revalidation.

Gemini prediction symbols support Binance-style ``<symbol>@depth10@100ms`` public
subscriptions on ``wss://ws.gemini.com``.  This module reads one fresh two-sided
snapshot and converts native YES-space bids/asks into q=1 executable depth.  It has
no authentication and no order-entry path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import time
from typing import Any

import websockets

D = Decimal
GEMINI_WS = "wss://ws.gemini.com"


@dataclass(frozen=True)
class GeminiDepthTop:
    symbol: str
    yes_bid: Decimal
    yes_bid_size: Decimal
    yes_ask: Decimal
    yes_ask_size: Decimal
    received_ms: int

    @property
    def no_ask(self) -> Decimal:
        return D("1") - self.yes_bid

    @property
    def no_ask_size(self) -> Decimal:
        return self.yes_bid_size


def _levels(value: Any) -> list[tuple[Decimal, Decimal]]:
    out: list[tuple[Decimal, Decimal]] = []
    if not isinstance(value, list):
        return out
    for row in value:
        try:
            if isinstance(row, dict):
                price = D(str(row.get("price") or row.get("p")))
                qty = D(str(row.get("quantity") or row.get("qty") or row.get("q")))
            else:
                price = D(str(row[0])); qty = D(str(row[1]))
        except Exception:
            continue
        if D("0") < price < D("1") and qty > 0:
            out.append((price, qty))
    return out


def parse_depth_top(symbol: str, message: dict[str, Any], *, received_ms: int | None = None) -> GeminiDepthTop | None:
    bids = _levels(message.get("bids"))
    asks = _levels(message.get("asks"))
    if not bids or not asks:
        return None
    bid_px, bid_qty = max(bids, key=lambda row: row[0])
    ask_px, ask_qty = min(asks, key=lambda row: row[0])
    if bid_px >= ask_px:
        return None
    return GeminiDepthTop(
        symbol=symbol,
        yes_bid=bid_px,
        yes_bid_size=bid_qty,
        yes_ask=ask_px,
        yes_ask_size=ask_qty,
        received_ms=int(time.time() * 1000) if received_ms is None else int(received_ms),
    )


async def fetch_depth_top(symbol: str, *, timeout: float = 3.0) -> GeminiDepthTop:
    """Return the first fresh valid two-sided depth10 snapshot for one symbol."""
    if not symbol.startswith("GEMI-"):
        raise ValueError("Gemini prediction symbol must start GEMI-")
    async with websockets.connect(GEMINI_WS, open_timeout=timeout, ping_interval=20, ping_timeout=20) as ws:
        request_id = f"depth-{time.time_ns()}"
        await ws.send(json.dumps({
            "id": request_id,
            "method": "SUBSCRIBE",
            "params": [f"{symbol}@depth10@100ms"],
        }, separators=(",", ":")))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            message = json.loads(raw)
            top = parse_depth_top(symbol, message, received_ms=int(time.time() * 1000))
            if top is not None:
                return top
    raise TimeoutError(f"no valid Gemini depth snapshot for {symbol}")
