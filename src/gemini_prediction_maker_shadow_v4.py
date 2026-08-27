"""Read-only Gemini Predictions maker shadow using dedicated depth5@100ms feeds.

One public WebSocket connection per currently-live BTC/ETH/SOL/XRP x 5m/15m
contract. Gemini documents depth5@100ms as a periodic top-five snapshot, so this
avoids dependence on event-driven BBO traffic or multiplexed-symbol framing.
No credentials, account data, signing, order entry, or cancellation code.
"""
from __future__ import annotations

import argparse
import asyncio
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


def top_from_partial_depth(data: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(data, dict):
        return None
    bids = data.get("bids")
    asks = data.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list):
        return None
    parsed_bids: list[tuple[float, float]] = []
    parsed_asks: list[tuple[float, float]] = []
    for row in bids:
        try:
            price, qty = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 < price < 1 and qty > 0:
            parsed_bids.append((price, qty))
    for row in asks:
        try:
            price, qty = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 < price < 1 and qty > 0:
            parsed_asks.append((price, qty))
    if not parsed_bids or not parsed_asks:
        return None
    bid, bid_size = max(parsed_bids, key=lambda x: x[0])
    ask, ask_size = min(parsed_asks, key=lambda x: x[0])
    if not 0 < bid < ask < 1:
        return None
    return bid, bid_size, ask, ask_size


async def stream_symbol(
    *,
    symbol: str,
    state: EventState,
    reward_config: dict[str, Any],
    reward_event: RewardEvent | None,
    quote_size: float,
    min_rest_seconds: float,
    quote_refresh_seconds: float,
    stop_at: float,
) -> None:
    async with websockets.connect(
        WS,
        open_timeout=8,
        ping_interval=15,
        ping_timeout=8,
        max_queue=10000,
        max_size=2**20,
    ) as ws:
        await ws.send(json.dumps({
            "id": f"depth-{symbol}",
            "method": "SUBSCRIBE",
            "params": [f"{symbol}@depth5@100ms"],
        }))
        while time.time() < stop_at:
            timeout = max(0.1, min(1.0, stop_at - time.time()))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            try:
                data = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            top = top_from_partial_depth(data)
            if top is None:
                continue
            bid, bid_size, ask, ask_size = top
            for evt in process_book_tick(
                state=state,
                bid=bid,
                ask=ask,
                bid_size=bid_size,
                ask_size=ask_size,
                reward_config=reward_config,
                reward_event=reward_event,
                quote_size=quote_size,
                min_rest_ms=int(min_rest_seconds * 1000),
                max_quote_age_ms=int(quote_refresh_seconds * 1000),
                event_time_ns=None,
            ):
                _emit(evt)


async def run(*, quote_size: float, min_rest_seconds: float, quote_refresh_seconds: float, discovery_seconds: float, summary_seconds: float) -> None:
    states: dict[str, EventState] = {}
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-partial-depth-shadow/4.0"}) as http:
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

                _emit({
                    "type": "GEMINI_PARTIAL_DEPTH_MAKER_DISCOVERY",
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

                started = time.time()
                stop_at = started + discovery_seconds
                tasks = [
                    asyncio.create_task(stream_symbol(
                        symbol=symbol,
                        state=states[symbol],
                        reward_config=reward_config,
                        reward_event=relevant_rewards.get(states[symbol].event.event_ticker),
                        quote_size=quote_size,
                        min_rest_seconds=min_rest_seconds,
                        quote_refresh_seconds=quote_refresh_seconds,
                        stop_at=stop_at,
                    ))
                    for symbol in active_symbols
                ]

                next_summary = started + summary_seconds
                while time.time() < stop_at:
                    await asyncio.sleep(max(0.05, min(0.5, next_summary - time.time(), stop_at - time.time())))
                    now = time.time()
                    if now >= next_summary:
                        payload = summary(
                            {symbol: states[symbol] for symbol in active_symbols if symbol in states},
                            relevant_rewards,
                            started,
                        )
                        payload["type"] = "GEMINI_PARTIAL_DEPTH_MAKER_SHADOW_SUMMARY"
                        payload["feed"] = "8x depth5@100ms"
                        _emit(payload)
                        next_summary = now + summary_seconds

                results = await asyncio.gather(*tasks, return_exceptions=True)
                errors = [r for r in results if isinstance(r, BaseException)]
                if errors:
                    raise RuntimeError(f"{len(errors)} Gemini depth workers failed; first={errors[0]!r}")
            except Exception as exc:
                _emit({
                    "type": "GEMINI_PARTIAL_DEPTH_MAKER_SHADOW_ERROR",
                    "error": type(exc).__name__,
                    "message": str(exc)[:500],
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                await asyncio.sleep(1.0)


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only Gemini dedicated 100ms partial-depth maker shadow")
    p.add_argument("--quote-size", type=float, default=10.0)
    p.add_argument("--min-rest-seconds", type=float, default=1.0)
    p.add_argument("--quote-refresh-seconds", type=float, default=8.0)
    p.add_argument("--discovery-seconds", type=float, default=30.0)
    p.add_argument("--summary-seconds", type=float, default=10.0)
    args = p.parse_args()
    asyncio.run(run(
        quote_size=max(0.01, args.quote_size),
        min_rest_seconds=max(0.0, args.min_rest_seconds),
        quote_refresh_seconds=max(1.0, args.quote_refresh_seconds),
        discovery_seconds=max(10.0, args.discovery_seconds),
        summary_seconds=max(2.0, args.summary_seconds),
    ))


if __name__ == "__main__":
    main()
