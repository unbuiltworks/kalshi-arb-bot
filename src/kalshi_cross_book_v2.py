"""Read-only Kalshi two-sided book stream for cross-exchange shadow scanning.

Important: this subscription explicitly requests the legacy two-scale order-book
view (`use_yes_price=false`). In that view YES levels are YES prices and NO levels
are NO prices, so the executable asks are unambiguous:

    YES ask = 1 - best NO bid
    NO ask  = 1 - best YES bid

This module contains no order-entry code. If Kalshi removes the legacy scale in a
future API release, this module must be updated before execution is ever enabled.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import random
import time
from typing import Any
from urllib.parse import urlparse

import websockets
from websockets.asyncio.client import ClientConnection

from config.settings import API_KEY_ID, WS_PING_INTERVAL_S, WS_URL
from src.auth import auth_headers, load_private_key

logger = logging.getLogger("kalshi_arb.cross_book_v2")


@dataclass(frozen=True)
class TwoSidedQuote:
    ticker: str
    yes_ask: Decimal
    yes_depth: Decimal
    no_ask: Decimal
    no_depth: Decimal
    received_ms: int
    exchange_ts_ms: int | None
    seq: int | None


class KalshiCrossBooks:
    def __init__(
        self,
        tickers: list[str] | tuple[str, ...],
        *,
        ws_url: str | None = None,
        api_key_id: str | None = None,
        private_key_path: str | None = None,
    ) -> None:
        self.tickers = tuple(dict.fromkeys(tickers))
        if not self.tickers:
            raise ValueError("at least one Kalshi ticker is required")
        self.ws_url = ws_url or WS_URL
        self.api_key_id = api_key_id or API_KEY_ID
        self.private_key = load_private_key(private_key_path)
        self._yes_bids = {ticker: {} for ticker in self.tickers}
        self._no_bids = {ticker: {} for ticker in self.tickers}
        self._received_ms: dict[str, int] = {}
        self._exchange_ts_ms: dict[str, int | None] = {}
        self._initialized: set[str] = set()
        self._last_seq: int | None = None
        self._lock = asyncio.Lock()
        self._running = False
        self._ws: ClientConnection | None = None

    @property
    def initialized_count(self) -> int:
        return len(self._initialized)

    def _headers(self) -> dict[str, str]:
        return auth_headers(
            self.private_key,
            "GET",
            urlparse(self.ws_url).path,
            self.api_key_id,
        )

    async def run(self) -> None:
        self._running = True
        # Census safety: do not turn a venue-side 429 into a reconnect storm. The
        # previous 250ms->5s loop could itself prolong connection throttling when
        # several read-only services shared the account. Start at 5s and cap at
        # 60s with jitter; successful connection resets the backoff.
        backoff = 5.0
        while self._running:
            try:
                await self._run_once()
                backoff = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Kalshi cross-book stream reset: %s; retry_s=%.1f", exc, backoff)
            if not self._running:
                return
            await self._invalidate_all()
            await asyncio.sleep(backoff + random.uniform(0.0, min(2.0, backoff * 0.10)))
            backoff = min(backoff * 2, 60.0)

    async def _run_once(self) -> None:
        async with websockets.connect(
            self.ws_url,
            additional_headers=self._headers(),
            ping_interval=WS_PING_INTERVAL_S,
        ) as ws:
            self._ws = ws
            await ws.send(json.dumps({
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_tickers": list(self.tickers),
                    # We need NO levels in NO-price units for reciprocal ask math.
                    "use_yes_price": False,
                },
            }))
            logger.info("Kalshi WS subscribed to %d matched ticker(s)", len(self.tickers))
            async for raw in ws:
                if not self._running:
                    break
                msg = json.loads(raw)
                typ = msg.get("type")
                if typ in {"orderbook_snapshot", "orderbook_delta"}:
                    self._validate_sequence(msg)
                if typ == "orderbook_snapshot":
                    await self.apply_snapshot(msg)
                elif typ == "orderbook_delta":
                    await self.apply_delta(msg)
                elif typ == "error":
                    raise RuntimeError(f"Kalshi WS error: {msg.get('msg')}")

    def _validate_sequence(self, msg: dict[str, Any]) -> None:
        seq = _int_or_none(msg.get("seq"))
        if seq is None:
            return
        if self._last_seq is not None and seq != self._last_seq + 1:
            raise RuntimeError(
                f"orderbook sequence gap: previous={self._last_seq} current={seq}"
            )
        self._last_seq = seq

    @staticmethod
    def _levels(body: dict[str, Any], side: str) -> list[list[str]]:
        return body.get(f"{side}_dollars_fp") or body.get(f"{side}_dollars") or []

    async def apply_snapshot(self, msg: dict[str, Any]) -> None:
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker", ""))
        if ticker not in self._yes_bids:
            return
        yes: dict[Decimal, Decimal] = {}
        no: dict[Decimal, Decimal] = {}
        for side, target in (("yes", yes), ("no", no)):
            for price_raw, count_raw in self._levels(body, side):
                price = Decimal(str(price_raw))
                count = Decimal(str(count_raw))
                if count > 0:
                    target[price] = count
        now = int(time.time() * 1000)
        async with self._lock:
            self._yes_bids[ticker] = yes
            self._no_bids[ticker] = no
            self._received_ms[ticker] = now
            self._exchange_ts_ms[ticker] = _int_or_none(body.get("ts_ms"))
            self._initialized.add(ticker)

    async def apply_delta(self, msg: dict[str, Any]) -> None:
        body = msg.get("msg", msg)
        ticker = str(body.get("market_ticker", ""))
        side = str(body.get("side", "")).lower()
        if ticker not in self._yes_bids or side not in {"yes", "no"}:
            return
        price_raw = body.get("price_dollars")
        delta_raw = body.get("delta_fp")
        if price_raw is None or delta_raw is None:
            raise RuntimeError(f"malformed orderbook_delta for {ticker}: {body}")
        price = Decimal(str(price_raw))
        delta = Decimal(str(delta_raw))
        async with self._lock:
            if ticker not in self._initialized:
                return
            book = self._yes_bids[ticker] if side == "yes" else self._no_bids[ticker]
            new_count = book.get(price, Decimal("0")) + delta
            if new_count > 0:
                book[price] = new_count
            else:
                book.pop(price, None)
            self._received_ms[ticker] = int(time.time() * 1000)
            self._exchange_ts_ms[ticker] = _int_or_none(body.get("ts_ms"))

    async def quote(self, ticker: str, *, max_age_ms: int = 30000) -> TwoSidedQuote | None:
        now = int(time.time() * 1000)
        async with self._lock:
            if ticker not in self._initialized:
                return None
            received = self._received_ms.get(ticker)
            if received is None or now - received > max_age_ms:
                return None
            yes = self._yes_bids.get(ticker) or {}
            no = self._no_bids.get(ticker) or {}
            if not yes or not no:
                return None
            best_yes_bid = max(yes)
            best_no_bid = max(no)
            return TwoSidedQuote(
                ticker=ticker,
                yes_ask=Decimal("1") - best_no_bid,
                yes_depth=no[best_no_bid],
                no_ask=Decimal("1") - best_yes_bid,
                no_depth=yes[best_yes_bid],
                received_ms=received,
                exchange_ts_ms=self._exchange_ts_ms.get(ticker),
                seq=self._last_seq,
            )

    async def close(self) -> None:
        self._running = False
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _invalidate_all(self) -> None:
        async with self._lock:
            self._initialized.clear()
            self._received_ms.clear()
            self._exchange_ts_ms.clear()
            self._last_seq = None
            for book in self._yes_bids.values():
                book.clear()
            for book in self._no_bids.values():
                book.clear()


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None