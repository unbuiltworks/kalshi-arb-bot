"""Low-latency active-window runner for the read-only Polymarket maker econ shadow."""
from __future__ import annotations

import argparse
import time

import httpx

from src.polymarket_maker_econ_shadow import (
    MarketState,
    USER_AGENT,
    _emit,
    fetch_book,
    fetch_reward_configs,
    market_snapshot,
    summarize,
)
from src.polymarket_temporal_live_shadow import discover_markets


def is_active_window(market, now_ms: int) -> bool:
    if market.end_ts_ms is None:
        return False
    start_ms = market.end_ts_ms - market.horizon_minutes * 60 * 1000
    return start_ms <= now_ms < market.end_ts_ms


def run(*, poll_seconds: float, discovery_seconds: float, reward_seconds: float, summary_seconds: float, quote_size: float, min_rest_seconds: float) -> None:
    states: dict[str, MarketState] = {}
    rewards = {}
    next_discovery = 0.0
    next_rewards = 0.0
    next_summary = 0.0

    with httpx.Client(timeout=8.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:
        while True:
            cycle_start = time.time()
            now_ms = int(cycle_start * 1000)

            if cycle_start >= next_rewards:
                try:
                    rewards = fetch_reward_configs(http)
                    _emit({
                        "type": "POLY_MAKER_REWARD_CONFIG_REFRESH",
                        "configured_markets": len(rewards),
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })
                except Exception as exc:
                    _emit({"type": "POLY_MAKER_REWARD_CONFIG_ERROR", "error": type(exc).__name__, "message": str(exc)[:300], "shadow_only": True})
                next_rewards = cycle_start + reward_seconds

            if cycle_start >= next_discovery:
                try:
                    discovered = discover_markets(http, horizons=(5, 15))
                    active = [m for m in discovered if is_active_window(m, now_ms)]
                    for market in active:
                        if market.condition_id not in states:
                            states[market.condition_id] = MarketState(market=market)
                            cfg = rewards.get(market.condition_id)
                            _emit({
                                "type": "POLY_MAKER_ACTIVE_MARKET",
                                "stream": market.stream.name,
                                "market_id": market.condition_id,
                                "question": market.question,
                                "end_ts_ms": market.end_ts_ms,
                                "reward_configured": cfg is not None,
                                "reward_min_size": None if cfg is None else cfg.min_size,
                                "reward_max_spread_cents": None if cfg is None else cfg.max_spread_cents,
                                "reward_daily_pool": None if cfg is None else cfg.total_daily_rate,
                                "shadow_only": True,
                                "order_entry": False,
                                "realized_pnl": 0.0,
                            })
                    next_discovery = cycle_start + discovery_seconds
                except Exception as exc:
                    _emit({"type": "POLY_MAKER_DISCOVERY_ERROR", "error": type(exc).__name__, "message": str(exc)[:300], "shadow_only": True})
                    next_discovery = cycle_start + min(5.0, discovery_seconds)

            active_states = [s for s in states.values() if not s.closed and is_active_window(s.market, now_ms)]
            for state in active_states:
                market = state.market
                try:
                    up_book = fetch_book(http, market.up_token_id)
                    down_book = fetch_book(http, market.down_token_id)
                    cfg = rewards.get(market.condition_id)
                    effective_size = max(quote_size, cfg.min_size if cfg else 0.0)
                    for evt in market_snapshot(
                        state=state,
                        cfg=cfg,
                        up_book=up_book,
                        down_book=down_book,
                        quote_size=effective_size,
                        now_ms=now_ms,
                        min_rest_ms=int(min_rest_seconds * 1000),
                    ):
                        _emit(evt)
                except Exception as exc:
                    _emit({
                        "type": "POLY_MAKER_BOOK_ERROR",
                        "stream": market.stream.name,
                        "market_id": market.condition_id,
                        "error": type(exc).__name__,
                        "message": str(exc)[:300],
                        "shadow_only": True,
                    })

            for state in states.values():
                if not state.closed and state.market.end_ts_ms is not None and now_ms >= state.market.end_ts_ms:
                    state.closed = True
                    _emit({
                        "type": "POLY_MAKER_MARKET_ENDED",
                        "stream": state.market.stream.name,
                        "market_id": state.market.condition_id,
                        "up_qty": state.up_qty,
                        "down_qty": state.down_qty,
                        "matched_qty": state.matched_qty(),
                        "pair_cost": state.pair_cost(),
                        "cross_through_candidates": state.cross_through_candidates,
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })

            if cycle_start >= next_summary:
                payload = summarize(states, rewards)
                payload["active_market_count"] = len(active_states)
                payload["cycle_elapsed_ms"] = (time.time() - cycle_start) * 1000.0
                _emit(payload)
                next_summary = cycle_start + summary_seconds

            elapsed = time.time() - cycle_start
            time.sleep(max(0.02, poll_seconds - elapsed))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--poll-seconds", type=float, default=0.5)
    p.add_argument("--discovery-seconds", type=float, default=15.0)
    p.add_argument("--reward-seconds", type=float, default=60.0)
    p.add_argument("--summary-seconds", type=float, default=10.0)
    p.add_argument("--quote-size", type=float, default=10.0)
    p.add_argument("--min-rest-seconds", type=float, default=3.5)
    args = p.parse_args()
    run(
        poll_seconds=max(0.05, args.poll_seconds),
        discovery_seconds=max(5.0, args.discovery_seconds),
        reward_seconds=max(15.0, args.reward_seconds),
        summary_seconds=max(5.0, args.summary_seconds),
        quote_size=max(0.01, args.quote_size),
        min_rest_seconds=max(0.0, args.min_rest_seconds),
    )


if __name__ == "__main__":
    main()
