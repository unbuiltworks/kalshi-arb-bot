"""Persistent Gemini prediction-market depth-5 cache for V5.3.

V5.2 keeps only top-of-book, which is insufficient once our own improved MOC quote
becomes the displayed top. V5.3 retains the public depth ladder so standing rescue
checks can subtract known owned resting quantity and fall through to the next real
external level instead of either counting our own liquidity or declaring zero depth.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal as D
import json
import time
from typing import Any, Mapping

import websockets

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base

ZERO = D("0")
ONE = D("1")


def _parse_side(rows: Any, *, reverse: bool) -> tuple[tuple[D, D], ...]:
    if not isinstance(rows, list):
        return ()
    aggregated: dict[D, D] = {}
    for row in rows:
        try:
            price = D(str(row[0]))
            quantity = D(str(row[1]))
        except Exception:
            continue
        if not (ZERO < price < ONE) or quantity <= ZERO:
            continue
        aggregated[price] = aggregated.get(price, ZERO) + quantity
    ordered = sorted(aggregated.items(), key=lambda x: x[0], reverse=reverse)
    return tuple((price, quantity) for price, quantity in ordered[:5])


def parse_depth5(data: Any) -> tuple[tuple[tuple[D, D], ...], tuple[tuple[D, D], ...]] | None:
    if not isinstance(data, Mapping):
        return None
    bids = _parse_side(data.get("bids"), reverse=True)
    asks = _parse_side(data.get("asks"), reverse=False)
    if not bids or not asks or bids[0][0] >= asks[0][0]:
        return None
    return bids, asks


def _subtract_level(
    levels: tuple[tuple[D, D], ...], *, price: D | None, quantity: D
) -> tuple[tuple[D, D], ...]:
    if price is None or quantity <= ZERO:
        return levels
    target = D(str(price))
    remaining_to_remove = D(str(quantity))
    out: list[tuple[D, D]] = []
    for level_price, level_qty in levels:
        qty = level_qty
        if level_price == target and remaining_to_remove > ZERO:
            removed = min(qty, remaining_to_remove)
            qty -= removed
            remaining_to_remove -= removed
        if qty > ZERO:
            out.append((level_price, qty))
    return tuple(out)


@dataclass(frozen=True)
class DepthSnapshot:
    bids: tuple[tuple[D, D], ...]
    asks: tuple[tuple[D, D], ...]
    age_ms: float
    received_ns: int
    sequence: int

    @property
    def bid(self) -> D:
        return self.bids[0][0]

    @property
    def bid_size(self) -> D:
        return self.bids[0][1]

    @property
    def ask(self) -> D:
        return self.asks[0][0]

    @property
    def ask_size(self) -> D:
        return self.asks[0][1]

    def externalized(
        self,
        *,
        own_yes_price: D | None = None,
        own_yes_quantity: D = ZERO,
        own_no_price: D | None = None,
        own_no_quantity: D = ZERO,
    ) -> "DepthSnapshot | None":
        """Remove known owned resting BUY liquidity from the public depth ladder.

        A YES BUY rests on the public YES bid side at its native price. A NO BUY at
        price p transforms to public YES ask price 1-p. If removing owned quantity
        exhausts the top level, the next public depth level becomes the rescue top.
        """
        bids = _subtract_level(
            self.bids,
            price=None if own_yes_price is None else D(str(own_yes_price)),
            quantity=D(str(own_yes_quantity)),
        )
        no_as_yes_ask = None if own_no_price is None else ONE - D(str(own_no_price))
        asks = _subtract_level(
            self.asks,
            price=no_as_yes_ask,
            quantity=D(str(own_no_quantity)),
        )
        if not bids or not asks or bids[0][0] >= asks[0][0]:
            return None
        return DepthSnapshot(
            bids=bids,
            asks=asks,
            age_ms=self.age_ms,
            received_ns=self.received_ns,
            sequence=self.sequence,
        )


class GeminiDepth5Cache:
    def __init__(self, symbol: str, *, stale_ms: int = 750) -> None:
        self.symbol = symbol
        self.stale_ms = int(stale_ms)
        self._depth: tuple[tuple[tuple[D, D], ...], tuple[tuple[D, D], ...]] | None = None
        self._received_ns: int | None = None
        self._sequence = 0
        self._updated = asyncio.Event()
        self.connected = False
        self.last_error: str | None = None

    def snapshot(self, *, now_ns: int | None = None) -> DepthSnapshot | None:
        if self._depth is None or self._received_ns is None:
            return None
        now = time.monotonic_ns() if now_ns is None else int(now_ns)
        age_ms = max(0.0, (now - self._received_ns) / 1_000_000)
        bids, asks = self._depth
        return DepthSnapshot(bids, asks, age_ms, self._received_ns, self._sequence)

    async def wait_fresh(self, *, timeout: float = 2.0, max_age_ms: int | None = None) -> DepthSnapshot:
        max_age = self.stale_ms if max_age_ms is None else int(max_age_ms)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snap = self.snapshot()
            if snap is not None and snap.age_ms <= max_age:
                return snap
            self._updated.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._updated.wait(), timeout=min(remaining, 0.25))
            except asyncio.TimeoutError:
                pass
        snap = self.snapshot()
        age = None if snap is None else round(snap.age_ms, 3)
        raise RuntimeError(f"no fresh persistent Gemini depth5: symbol={self.symbol} age_ms={age}")

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 0.10
        while not stop.is_set():
            try:
                async with websockets.connect(
                    base.WS,
                    open_timeout=3.0,
                    ping_interval=15,
                    ping_timeout=8,
                    close_timeout=1,
                    max_queue=5000,
                ) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "id": f"v53-depth-{self.symbol}",
                                "method": "SUBSCRIBE",
                                "params": [f"{self.symbol}@depth5@100ms"],
                            }
                        )
                    )
                    self.connected = True
                    self.last_error = None
                    backoff = 0.10
                    while not stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        try:
                            data: Any = json.loads(raw)
                        except Exception:
                            continue
                        depth = parse_depth5(data)
                        if depth is None:
                            continue
                        self._depth = depth
                        self._received_ns = time.monotonic_ns()
                        self._sequence += 1
                        self._updated.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.last_error = f"{type(exc).__name__}:{str(exc)[:160]}"
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(2.0, backoff * 2)
        self.connected = False
