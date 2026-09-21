"""Read-only Gemini Predictions maker/reward shadow using 100ms L2 depth.

This is the preferred execution-quality shadow: one live contract per
BTC/ETH/SOL/XRP x 5m/15m stream, public `depth@100ms` with an initial depth
snapshot, and local order books. No credentials, signing, account reads,
orders, cancellations, or mutation methods exist here.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import time
from typing import Any

import httpx
import websockets

from src.gemini_prediction_maker_shadow import (
    WS,
    EventState,
    RewardEvent,
    _emit,
    discover_events,
    fetch_reward_state,
    process_book_tick,
    summary,
)
from src.gemini_prediction_maker_shadow_v2 import live_events


@dataclass
class LocalBook:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    last_u: int | None = None
    initialized: bool = False

    def apply_levels(self, side: dict[float, float], levels: Any) -> None:
        if not isinstance(levels, list):
            return
        for level in levels:
            if not isinstance(level, (list, tuple)) or len(level) < 2:
                continue
            try:
                price = float(level[0])
                qty = float(level[1])
            except (TypeError, ValueError):
                continue
            if not (0 < price < 1) or qty < 0:
                continue
            if qty == 0:
                side.pop(price, None)
            else:
                side[price] = qty

    def apply(self, msg: dict[str, Any]) -> bool:
        try:
            first_u = int(msg.get("U"))
            last_u = int(msg.get("u"))
        except (TypeError, ValueError):
            return False

        # With snapshot=N the first frame for each stream is a complete top-N
        # snapshot encoded as a depthUpdate. Subsequent frames are deltas.
        if not self.initialized:
            self.bids.clear()
            self.asks.clear()
            self.apply_levels(self.bids, msg.get("b"))
            self.apply_levels(self.asks, msg.get("a"))
            self.last_u = last_u
            self.initialized = True
            return True

        if self.last_u is not None and first_u > self.last_u + 1:
            return False
        if self.last_u is not None and last_u <= self.last_u:
            return True
        self.apply_levels(self.bids, msg.get("b"))
        self.apply_levels(self.asks, msg.get("a"))
        self.last_u = last_u
        return True

    def top(self) -> tuple[float, float, float, float] | None:
        positive_bids = [(p, q) for p, q in self.bids.items() if q > 0]
        positive_asks = [(p, q) for p, q in self.asks.items() if q > 0]
        if not positive_bids or not positive_asks:
            return None
        bid, bid_size = max(positive_bids, key=lambda x: x[0])
        ask, ask_size = min(positive_asks, key=lambda x: x[0])
        if not 0 < bid < ask < 1:
            return None
        return bid, bid_size, ask, ask_size


async def run(*, quote_size: float, min_rest_seconds: float, quote_refresh_seconds: float, discovery_seconds: float, summary_seconds: float) -> None:
    states: dict[str, EventState] = {}
    books: dict[str, LocalBook] = {}

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-depth-maker-shadow/3.0"}) as http:
        while True:
            try:
                discovered = await discover_events(http)
                events = live_events(discovered)
                reward_config, rewards, rebate_rates = await fetch_reward_state(http)
                active_symbols = sorted(events)
                live_tickers = {event.event_ticker for event in events.values()}
                relevant_rewards = {ticker: reward for ticker, reward in rewards.items() if ticker in live_tickers}

                for symbol, event in events.items():
                    if symbol not in states:
                        states[symbol] = EventState(event=event)
                    if symbol not in books:
                        books[symbol] = LocalBook()

                _emit({
                    "type": "GEMINI_DEPTH_MAKER_DISCOVERY",
                    "discovered_short_crypto_symbols": len(discovered),
                    "live_symbols": len(active_symbols),
                    "streams": sorted({event.stream for event in events.values()}),
                    "live": {
                        event.stream: {
                            "event_ticker": event.event_ticker,
                            "symbol": event.instrument_symbol,
                            "expiry_ms": event.expiry_ms,
                            "daily_pool_usd": relevant_rewards.get(event.event_ticker, RewardEvent("", 0.0, 0)).daily_pool_usd,
                        }
                        for event in events.values()
                    },
                    "reward_config": reward_config,
                    "maker_rebate_rate_schedule": rebate_rates,
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                if not active_symbols:
                    await asyncio.sleep(2.0)
                    continue

                # Force a fresh initial snapshot on every reconnect. The server
                # sends the first depthUpdate frame as the requested top-20 book.
                for symbol in active_symbols:
                    books[symbol] = LocalBook()

                connected_at = time.time()
                next_discovery = connected_at + discovery_seconds
                next_summary = connected_at + summary_seconds
                url = f"{WS}?snapshot=20"
                async with websockets.connect(
                    url,
                    open_timeout=8,
                    ping_interval=15,
                    ping_timeout=8,
                    max_queue=20000,
                    max_size=2**20,
                ) as ws:
                    streams = [f"{symbol}@depth@100ms" for symbol in active_symbols]
                    await ws.send(json.dumps({"id": "arb-depth-shadow-3", "method": "SUBSCRIBE", "params": streams}))

                    while True:
                        timeout = max(0.1, min(next_discovery, next_summary) - time.time())
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        except asyncio.TimeoutError:
                            raw = None
                        now = time.time()

                        if raw is not None:
                            try:
                                data = json.loads(raw)
                            except json.JSONDecodeError:
                                data = None
                            if isinstance(data, dict) and data.get("e") == "depthUpdate":
                                symbol = str(data.get("s") or "")
                                state = states.get(symbol)
                                book = books.get(symbol)
                                if state is not None and book is not None and symbol in events:
                                    if not book.apply(data):
                                        raise RuntimeError(f"Gemini depth sequence gap for {symbol}")
                                    top = book.top()
                                    if top is not None:
                                        bid, bid_size, ask, ask_size = top
                                        for evt in process_book_tick(
                                            state=state,
                                            bid=bid,
                                            ask=ask,
                                            bid_size=bid_size,
                                            ask_size=ask_size,
                                            reward_config=reward_config,
                                            reward_event=relevant_rewards.get(state.event.event_ticker),
                                            quote_size=quote_size,
                                            min_rest_ms=int(min_rest_seconds * 1000),
                                            max_quote_age_ms=int(quote_refresh_seconds * 1000),
                                            event_time_ns=int(data.get("E")) if data.get("E") is not None else None,
                                        ):
                                            _emit(evt)

                        if now >= next_summary:
                            payload = summary(
                                {symbol: states[symbol] for symbol in active_symbols if symbol in states},
                                relevant_rewards,
                                connected_at,
                            )
                            payload["type"] = "GEMINI_DEPTH_MAKER_SHADOW_SUMMARY"
                            payload["initialized_books"] = sum(1 for symbol in active_symbols if books.get(symbol) and books[symbol].initialized)
                            payload["feed"] = "depth@100ms+snapshot20"
                            _emit(payload)
                            next_summary = now + summary_seconds
                        if now >= next_discovery:
                            break
            except Exception as exc:
                _emit({
                    "type": "GEMINI_DEPTH_MAKER_SHADOW_ERROR",
                    "error": type(exc).__name__,
                    "message": str(exc)[:500],
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                await asyncio.sleep(1.0)


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only Gemini 100ms-depth prediction maker shadow")
    p.add_argument("--quote-size", type=float, default=10.0)
    p.add_argument("--min-rest-seconds", type=float, default=1.0)
    p.add_argument("--quote-refresh-seconds", type=float, default=8.0)
    p.add_argument("--discovery-seconds", type=float, default=15.0)
    p.add_argument("--summary-seconds", type=float, default=10.0)
    args = p.parse_args()
    asyncio.run(run(
        quote_size=max(0.01, args.quote_size),
        min_rest_seconds=max(0.0, args.min_rest_seconds),
        quote_refresh_seconds=max(1.0, args.quote_refresh_seconds),
        discovery_seconds=max(5.0, args.discovery_seconds),
        summary_seconds=max(2.0, args.summary_seconds),
    ))


if __name__ == "__main__":
    main()
