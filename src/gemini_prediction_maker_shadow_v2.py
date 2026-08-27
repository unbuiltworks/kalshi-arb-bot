"""Live-window wrapper for the read-only Gemini Predictions maker shadow.

Keeps at most one currently-live contract per BTC/ETH/SOL/XRP x 5m/15m stream.
No credentials, signing, order entry, cancellation, or account endpoints.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

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


def live_events(events, now_ms: int | None = None):
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    chosen = {}
    for symbol, event in events.items():
        if event.expiry_ms is None:
            continue
        start_ms = event.expiry_ms - event.horizon_minutes * 60_000
        if not (start_ms <= now < event.expiry_ms):
            continue
        previous = chosen.get(event.stream)
        if previous is None or (event.expiry_ms or 0) < (previous.expiry_ms or 0):
            chosen[event.stream] = event
    return {event.instrument_symbol: event for event in chosen.values()}


async def run(*, quote_size: float, min_rest_seconds: float, quote_refresh_seconds: float, discovery_seconds: float, summary_seconds: float) -> None:
    states: dict[str, EventState] = {}
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-live-maker-shadow/2.0"}) as http:
        while True:
            try:
                discovered = await discover_events(http)
                events = live_events(discovered)
                reward_config, rewards, rebate_rates = await fetch_reward_state(http)
                for symbol, event in events.items():
                    state = states.get(symbol)
                    if state is None:
                        states[symbol] = EventState(event=event)
                active_symbols = sorted(events)
                relevant_rewards = {
                    ticker: reward
                    for ticker, reward in rewards.items()
                    if ticker in {event.event_ticker for event in events.values()}
                }
                _emit({
                    "type": "GEMINI_MAKER_LIVE_DISCOVERY",
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
                    await asyncio.sleep(max(1.0, min(discovery_seconds, 5.0)))
                    continue

                connected_at = time.time()
                next_discovery = connected_at + discovery_seconds
                next_summary = connected_at + summary_seconds
                async with websockets.connect(
                    WS,
                    open_timeout=8,
                    ping_interval=15,
                    ping_timeout=8,
                    max_queue=10000,
                    max_size=2**20,
                ) as ws:
                    streams = []
                    for symbol in active_symbols:
                        streams.extend([f"{symbol}@bookTicker", f"{symbol}@trade"])
                    await ws.send(json.dumps({"id": "arb-live-shadow-2", "method": "SUBSCRIBE", "params": streams}))

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
                            if isinstance(data, dict):
                                symbol = str(data.get("s") or "")
                                state = states.get(symbol)
                                if state is not None and symbol in events and all(k in data for k in ("b", "B", "a", "A")):
                                    try:
                                        bid = float(data["b"])
                                        ask = float(data["a"])
                                        bid_size = float(data["B"])
                                        ask_size = float(data["A"])
                                    except (TypeError, ValueError):
                                        bid = ask = -1.0
                                        bid_size = ask_size = 0.0
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
                            _emit(summary({symbol: states[symbol] for symbol in active_symbols if symbol in states}, relevant_rewards, connected_at))
                            next_summary = now + summary_seconds
                        if now >= next_discovery:
                            break
            except Exception as exc:
                _emit({
                    "type": "GEMINI_MAKER_LIVE_SHADOW_ERROR",
                    "error": type(exc).__name__,
                    "message": str(exc)[:500],
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                await asyncio.sleep(2.0)


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only Gemini live-window maker/reward shadow")
    p.add_argument("--quote-size", type=float, default=10.0)
    p.add_argument("--min-rest-seconds", type=float, default=1.0)
    p.add_argument("--quote-refresh-seconds", type=float, default=8.0)
    p.add_argument("--discovery-seconds", type=float, default=10.0)
    p.add_argument("--summary-seconds", type=float, default=10.0)
    args = p.parse_args()
    asyncio.run(run(
        quote_size=max(0.01, args.quote_size),
        min_rest_seconds=max(0.0, args.min_rest_seconds),
        quote_refresh_seconds=max(1.0, args.quote_refresh_seconds),
        discovery_seconds=max(2.0, args.discovery_seconds),
        summary_seconds=max(2.0, args.summary_seconds),
    ))


if __name__ == "__main__":
    main()
