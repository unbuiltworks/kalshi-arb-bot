"""Read-only Polymarket short-crypto maker shadow using the public CLOB WebSocket.

No credentials, signing, order entry, cancellation, or account reads. REST is used
only for market/reward discovery. The hot market-data path is the public market
WebSocket. BBO state is sampled at a fixed cadence so reward eligibility is not
accidentally weighted by message frequency.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import time
from typing import Any

import httpx
import websockets

from src.polymarket_maker_econ_shadow import MarketState, RewardConfig, _emit, market_snapshot, summarize
from src.polymarket_maker_econ_shadow_v2 import fetch_active_rewards, is_active_window
from src.polymarket_temporal_live_shadow import PolyBinaryMarket, discover_markets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
USER_AGENT = "arb-poly-maker-ws-shadow/1.0"


@dataclass
class TokenTop:
    best_bid: float | None = None
    best_bid_size: float | None = None
    best_ask: float | None = None
    best_ask_size: float | None = None
    last_update_ms: int | None = None
    book_messages: int = 0
    price_change_messages: int = 0
    trade_messages: int = 0

    def as_book(self) -> dict[str, float | None]:
        return {
            "best_bid": self.best_bid,
            "best_bid_size": self.best_bid_size,
            "best_ask": self.best_ask,
            "best_ask_size": self.best_ask_size,
        }


def _float(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _top_from_levels(rows: Any, *, want_max: bool) -> tuple[float | None, float | None]:
    parsed: list[tuple[float, float]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            p, q = _float(row.get("price")), _float(row.get("size"))
            if p is not None and q is not None and 0 <= p <= 1 and q > 0:
                parsed.append((p, q))
    if not parsed:
        return None, None
    return (max(parsed, key=lambda x: x[0]) if want_max else min(parsed, key=lambda x: x[0]))


def _update_book(top: TokenTop, msg: dict[str, Any]) -> None:
    bid, bid_size = _top_from_levels(msg.get("bids"), want_max=True)
    ask, ask_size = _top_from_levels(msg.get("asks"), want_max=False)
    top.best_bid, top.best_bid_size = bid, bid_size
    top.best_ask, top.best_ask_size = ask, ask_size
    top.last_update_ms = int(_float(msg.get("timestamp")) or time.time() * 1000)
    top.book_messages += 1


def _update_price_change(tops: dict[str, TokenTop], msg: dict[str, Any]) -> None:
    ts = int(_float(msg.get("timestamp")) or time.time() * 1000)
    changes = msg.get("price_changes") or msg.get("changes") or []
    if not isinstance(changes, list):
        return
    for change in changes:
        if not isinstance(change, dict):
            continue
        asset_id = str(change.get("asset_id") or "")
        top = tops.get(asset_id)
        if top is None:
            continue
        new_bid = _float(change.get("best_bid"))
        new_ask = _float(change.get("best_ask"))
        if new_bid is not None:
            if top.best_bid != new_bid:
                top.best_bid_size = None
            top.best_bid = new_bid
        if new_ask is not None:
            if top.best_ask != new_ask:
                top.best_ask_size = None
            top.best_ask = new_ask
        top.last_update_ms = ts
        top.price_change_messages += 1


async def heartbeat(ws, stop_at: float) -> None:
    while time.time() < stop_at:
        await asyncio.sleep(10.0)
        if time.time() >= stop_at:
            return
        await ws.send("PING")


async def receive_market_data(
    *,
    asset_ids: list[str],
    tops: dict[str, TokenTop],
    stop_at: float,
    counters: dict[str, int],
) -> None:
    async with websockets.connect(
        WS_URL,
        open_timeout=8,
        ping_interval=None,
        max_queue=50000,
        max_size=2**22,
    ) as ws:
        await ws.send(json.dumps({
            "type": "market",
            "assets_ids": asset_ids,
            "custom_feature_enabled": True,
        }))
        hb = asyncio.create_task(heartbeat(ws, stop_at))
        try:
            while time.time() < stop_at:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(2.0, max(0.1, stop_at - time.time())))
                except asyncio.TimeoutError:
                    continue
                if raw == "PONG":
                    counters["pong"] = counters.get("pong", 0) + 1
                    continue
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    counters["unparsed"] = counters.get("unparsed", 0) + 1
                    continue
                messages = payload if isinstance(payload, list) else [payload]
                for msg in messages:
                    if not isinstance(msg, dict):
                        continue
                    counters["messages"] = counters.get("messages", 0) + 1
                    et = str(msg.get("event_type") or "")
                    if et == "book":
                        token = str(msg.get("asset_id") or "")
                        top = tops.get(token)
                        if top is not None:
                            _update_book(top, msg)
                    elif et == "price_change":
                        _update_price_change(tops, msg)
                    elif et == "best_bid_ask":
                        token = str(msg.get("asset_id") or "")
                        top = tops.get(token)
                        if top is not None:
                            top.best_bid = _float(msg.get("best_bid"))
                            top.best_ask = _float(msg.get("best_ask"))
                            top.last_update_ms = int(_float(msg.get("timestamp")) or time.time() * 1000)
                            top.price_change_messages += 1
                    elif et == "last_trade_price":
                        token = str(msg.get("asset_id") or "")
                        top = tops.get(token)
                        if top is not None:
                            top.trade_messages += 1
                        counters["trades"] = counters.get("trades", 0) + 1
                    elif et == "market_resolved":
                        counters["resolved"] = counters.get("resolved", 0) + 1
        finally:
            hb.cancel()
            await asyncio.gather(hb, return_exceptions=True)


def _fresh(top: TokenTop, now_ms: int, max_age_ms: int) -> bool:
    return (
        top.last_update_ms is not None
        and now_ms - top.last_update_ms <= max_age_ms
        and top.best_bid is not None
        and top.best_ask is not None
        and 0 < top.best_bid < top.best_ask < 1
    )


async def run(
    *,
    sample_seconds: float,
    discovery_seconds: float,
    reward_seconds: float,
    summary_seconds: float,
    quote_size: float,
    min_rest_seconds: float,
    max_book_age_seconds: float,
) -> None:
    states: dict[str, MarketState] = {}
    rewards: dict[str, RewardConfig] = {}
    last_reward_refresh = 0.0

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http_async:
        while True:
            try:
                # The existing discovery helpers are synchronous; keep their brief REST work off the event loop.
                def discover_sync() -> list[PolyBinaryMarket]:
                    with httpx.Client(timeout=8.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:
                        return discover_markets(http, horizons=(5, 15))

                discovered = await asyncio.to_thread(discover_sync)
                now_ms = int(time.time() * 1000)
                active = [m for m in discovered if is_active_window(m, now_ms)]
                active_ids = {m.condition_id for m in active}
                for market in active:
                    if market.condition_id not in states:
                        states[market.condition_id] = MarketState(market=market)

                if time.time() - last_reward_refresh >= reward_seconds and active:
                    def rewards_sync() -> dict[str, RewardConfig]:
                        with httpx.Client(timeout=8.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:
                            return fetch_active_rewards(http, [m.condition_id for m in active])
                    rewards = await asyncio.to_thread(rewards_sync)
                    last_reward_refresh = time.time()

                token_to_market: dict[str, tuple[str, str]] = {}
                for market in active:
                    token_to_market[market.up_token_id] = (market.condition_id, "UP")
                    token_to_market[market.down_token_id] = (market.condition_id, "DOWN")
                tops = {token: TokenTop() for token in token_to_market}
                asset_ids = sorted(tops)
                counters: dict[str, int] = {}

                _emit({
                    "type": "POLY_MAKER_WS_DISCOVERY",
                    "active_markets": len(active),
                    "active_tokens": len(asset_ids),
                    "streams": sorted({m.stream.name for m in active}),
                    "reward_configured_markets": sum(1 for m in active if m.condition_id in rewards),
                    "visible_daily_pools": sum(rewards[m.condition_id].total_daily_rate for m in active if m.condition_id in rewards),
                    "feed": "public_clob_ws_market",
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                if not asset_ids:
                    await asyncio.sleep(2.0)
                    continue

                started = time.time()
                stop_at = started + discovery_seconds
                receiver = asyncio.create_task(receive_market_data(asset_ids=asset_ids, tops=tops, stop_at=stop_at, counters=counters))
                next_sample = started
                next_summary = started + summary_seconds
                max_age_ms = int(max_book_age_seconds * 1000)

                while time.time() < stop_at:
                    now = time.time()
                    if now >= next_sample:
                        sample_ms = int(now * 1000)
                        for cid in active_ids:
                            state = states.get(cid)
                            if state is None:
                                continue
                            up = tops.get(state.market.up_token_id)
                            down = tops.get(state.market.down_token_id)
                            if up is None or down is None or not _fresh(up, sample_ms, max_age_ms) or not _fresh(down, sample_ms, max_age_ms):
                                continue
                            cfg = rewards.get(cid)
                            effective_size = max(quote_size, cfg.min_size if cfg else 0.0)
                            for evt in market_snapshot(
                                state=state,
                                cfg=cfg,
                                up_book=up.as_book(),
                                down_book=down.as_book(),
                                quote_size=effective_size,
                                now_ms=sample_ms,
                                min_rest_ms=int(min_rest_seconds * 1000),
                            ):
                                evt["feed"] = "public_clob_ws_market"
                                _emit(evt)
                        next_sample = now + sample_seconds

                    if now >= next_summary:
                        active_states = {cid: states[cid] for cid in active_ids if cid in states}
                        payload = summarize(active_states, rewards)
                        payload["type"] = "POLY_MAKER_WS_SHADOW_SUMMARY"
                        payload["feed"] = "public_clob_ws_market"
                        payload["ws_messages"] = counters.get("messages", 0)
                        payload["trade_messages"] = counters.get("trades", 0)
                        payload["fresh_tokens"] = sum(1 for top in tops.values() if _fresh(top, int(now * 1000), max_age_ms))
                        payload["active_tokens"] = len(tops)
                        payload["connected_seconds"] = now - started
                        _emit(payload)
                        next_summary = now + summary_seconds

                    await asyncio.sleep(0.02)

                results = await asyncio.gather(receiver, return_exceptions=True)
                errors = [x for x in results if isinstance(x, BaseException)]
                if errors:
                    raise RuntimeError(f"Polymarket WS receiver failed: {errors[0]!r}")
            except Exception as exc:
                _emit({
                    "type": "POLY_MAKER_WS_SHADOW_ERROR",
                    "error": type(exc).__name__,
                    "message": str(exc)[:500],
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                await asyncio.sleep(1.0)


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only Polymarket public-WS maker economics shadow")
    p.add_argument("--sample-seconds", type=float, default=0.25)
    p.add_argument("--discovery-seconds", type=float, default=30.0)
    p.add_argument("--reward-seconds", type=float, default=30.0)
    p.add_argument("--summary-seconds", type=float, default=10.0)
    p.add_argument("--quote-size", type=float, default=10.0)
    p.add_argument("--min-rest-seconds", type=float, default=1.0)
    p.add_argument("--max-book-age-seconds", type=float, default=3.0)
    args = p.parse_args()
    asyncio.run(run(
        sample_seconds=max(0.05, args.sample_seconds),
        discovery_seconds=max(10.0, args.discovery_seconds),
        reward_seconds=max(10.0, args.reward_seconds),
        summary_seconds=max(2.0, args.summary_seconds),
        quote_size=max(0.01, args.quote_size),
        min_rest_seconds=max(0.0, args.min_rest_seconds),
        max_book_age_seconds=max(0.5, args.max_book_age_seconds),
    ))


if __name__ == "__main__":
    main()
