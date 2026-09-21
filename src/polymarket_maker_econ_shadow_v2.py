"""Parallel active-window runner for reward-aware Polymarket maker shadow.

Read-only public market data only. No order authority.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import Any

import httpx

from src.polymarket_maker_econ_shadow import (
    CLOB_API,
    MarketState,
    RewardConfig,
    USER_AGENT,
    _emit,
    _f,
    fetch_book,
    market_snapshot,
    summarize,
)
from src.polymarket_temporal_live_shadow import discover_markets


def is_active_window(market, now_ms: int) -> bool:
    if market.end_ts_ms is None:
        return False
    start_ms = market.end_ts_ms - market.horizon_minutes * 60 * 1000
    return start_ms <= now_ms < market.end_ts_ms


def fetch_reward_config(http: httpx.Client, condition_id: str) -> RewardConfig | None:
    response = http.get(f"{CLOB_API}/rewards/markets/{condition_id}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") or [] if isinstance(payload, dict) else []
    if not rows or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    configs = row.get("rewards_config") or []
    rate = _f(row.get("total_daily_rate"), 0.0)
    if rate <= 0:
        rate = sum(_f(x.get("rate_per_day"), 0.0) for x in configs if isinstance(x, dict))
    return RewardConfig(
        condition_id=condition_id,
        max_spread_cents=max(0.0, _f(row.get("rewards_max_spread"), 0.0)),
        min_size=max(0.0, _f(row.get("rewards_min_size"), 0.0)),
        total_daily_rate=max(0.0, rate),
    )


def fetch_active_rewards(http: httpx.Client, condition_ids: list[str]) -> dict[str, RewardConfig]:
    out: dict[str, RewardConfig] = {}
    with ThreadPoolExecutor(max_workers=min(16, max(1, len(condition_ids)))) as pool:
        futures = {pool.submit(fetch_reward_config, http, cid): cid for cid in condition_ids}
        for future in as_completed(futures):
            cid = futures[future]
            try:
                cfg = future.result()
            except Exception:
                cfg = None
            if cfg is not None:
                out[cid] = cfg
    return out


def fetch_books_parallel(http: httpx.Client, states: list[MarketState]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    jobs: dict[Any, tuple[str, str]] = {}
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(24, max(1, len(states) * 2))) as pool:
        for state in states:
            cid = state.market.condition_id
            jobs[pool.submit(fetch_book, http, state.market.up_token_id)] = (cid, "up")
            jobs[pool.submit(fetch_book, http, state.market.down_token_id)] = (cid, "down")
        for future in as_completed(jobs):
            cid, side = jobs[future]
            try:
                results.setdefault(cid, {})[side] = future.result()
            except Exception as exc:
                _emit({
                    "type": "POLY_MAKER_BOOK_ERROR",
                    "market_id": cid,
                    "side": side,
                    "error": type(exc).__name__,
                    "message": str(exc)[:200],
                    "shadow_only": True,
                })
    return {
        cid: (parts["up"], parts["down"])
        for cid, parts in results.items()
        if "up" in parts and "down" in parts
    }


def run(*, poll_seconds: float, discovery_seconds: float, reward_seconds: float, summary_seconds: float, quote_size: float, min_rest_seconds: float) -> None:
    states: dict[str, MarketState] = {}
    rewards: dict[str, RewardConfig] = {}
    active_ids: set[str] = set()
    next_discovery = 0.0
    next_rewards = 0.0
    next_summary = 0.0

    with httpx.Client(timeout=4.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:
        while True:
            cycle_start = time.time()
            now_ms = int(cycle_start * 1000)

            if cycle_start >= next_discovery:
                try:
                    discovered = discover_markets(http, horizons=(5, 15))
                    active = [m for m in discovered if is_active_window(m, now_ms)]
                    new_active_ids = {m.condition_id for m in active}
                    for market in active:
                        if market.condition_id not in states:
                            states[market.condition_id] = MarketState(market=market)
                    active_ids = new_active_ids
                    next_discovery = cycle_start + discovery_seconds
                    next_rewards = 0.0  # refresh config immediately when window rolls
                except Exception as exc:
                    _emit({"type": "POLY_MAKER_DISCOVERY_ERROR", "error": type(exc).__name__, "message": str(exc)[:250], "shadow_only": True})
                    next_discovery = cycle_start + min(5.0, discovery_seconds)

            active_states = [states[cid] for cid in active_ids if cid in states]

            if cycle_start >= next_rewards and active_states:
                t0 = time.time()
                rewards = fetch_active_rewards(http, [s.market.condition_id for s in active_states])
                _emit({
                    "type": "POLY_MAKER_ACTIVE_REWARD_REFRESH",
                    "active_markets": len(active_states),
                    "reward_configured_markets": len(rewards),
                    "visible_daily_pools": sum(x.total_daily_rate for x in rewards.values()),
                    "elapsed_ms": (time.time() - t0) * 1000.0,
                    "configs": {
                        s.market.stream.name: None if s.market.condition_id not in rewards else {
                            "daily_pool": rewards[s.market.condition_id].total_daily_rate,
                            "min_size": rewards[s.market.condition_id].min_size,
                            "max_spread_cents": rewards[s.market.condition_id].max_spread_cents,
                        }
                        for s in active_states
                    },
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                next_rewards = cycle_start + reward_seconds

            books = fetch_books_parallel(http, active_states) if active_states else {}
            sample_ms = int(time.time() * 1000)
            for state in active_states:
                pair = books.get(state.market.condition_id)
                if pair is None:
                    continue
                cfg = rewards.get(state.market.condition_id)
                effective_size = max(quote_size, cfg.min_size if cfg else 0.0)
                for evt in market_snapshot(
                    state=state,
                    cfg=cfg,
                    up_book=pair[0],
                    down_book=pair[1],
                    quote_size=effective_size,
                    now_ms=sample_ms,
                    min_rest_ms=int(min_rest_seconds * 1000),
                ):
                    _emit(evt)

            for state in states.values():
                if not state.closed and state.market.end_ts_ms is not None and sample_ms >= state.market.end_ts_ms:
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
                        "quote_samples": state.quote_samples,
                        "eligible_samples": state.eligible_samples,
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
    p.add_argument("--discovery-seconds", type=float, default=10.0)
    p.add_argument("--reward-seconds", type=float, default=30.0)
    p.add_argument("--summary-seconds", type=float, default=10.0)
    p.add_argument("--quote-size", type=float, default=10.0)
    p.add_argument("--min-rest-seconds", type=float, default=3.5)
    args = p.parse_args()
    run(
        poll_seconds=max(0.05, args.poll_seconds),
        discovery_seconds=max(5.0, args.discovery_seconds),
        reward_seconds=max(10.0, args.reward_seconds),
        summary_seconds=max(5.0, args.summary_seconds),
        quote_size=max(0.01, args.quote_size),
        min_rest_seconds=max(0.0, args.min_rest_seconds),
    )


if __name__ == "__main__":
    main()
