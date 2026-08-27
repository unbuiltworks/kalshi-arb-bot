"""Reward-aware Polymarket crypto maker economics shadow.

READ ONLY. This module uses public Gamma/CLOB GET endpoints only. It never signs,
posts, modifies, or cancels orders and has no credential path.

Purpose: measure the economics suggested by the l5 public wallet:
- quote both outcomes passively,
- measure liquidity-reward eligibility/raw score,
- conservatively flag when the market trades through a hypothetical resting bid,
- track hypothetical two-sided inventory and pair cost.

Shadow fill events are CROSS_THROUGH CANDIDATES, not confirmed fills. Queue priority,
cancel latency, maker/taker role, and actual reward share remain unobserved until a
separately authorized live canary exists.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
import time
from typing import Any

import httpx

from src.polymarket_temporal_live_shadow import (
    CLOB_API,
    PolyBinaryMarket,
    discover_markets,
)

USER_AGENT = "arb-maker-econ-shadow/1.0"


@dataclass(frozen=True)
class RewardConfig:
    condition_id: str
    max_spread_cents: float
    min_size: float
    total_daily_rate: float


@dataclass
class RestingQuote:
    price: float
    size: float
    placed_at_ms: int
    side: str
    active: bool = True


@dataclass
class MarketState:
    market: PolyBinaryMarket
    up_quote: RestingQuote | None = None
    down_quote: RestingQuote | None = None
    up_qty: float = 0.0
    down_qty: float = 0.0
    up_cost: float = 0.0
    down_cost: float = 0.0
    quote_samples: int = 0
    eligible_samples: int = 0
    raw_score_sum: float = 0.0
    cross_through_candidates: int = 0
    cross_through_shares: float = 0.0
    last_up_ask: float | None = None
    last_down_ask: float | None = None
    closed: bool = False

    def matched_qty(self) -> float:
        return min(self.up_qty, self.down_qty)

    def avg_up(self) -> float | None:
        return self.up_cost / self.up_qty if self.up_qty > 0 else None

    def avg_down(self) -> float | None:
        return self.down_cost / self.down_qty if self.down_qty > 0 else None

    def pair_cost(self) -> float | None:
        a, b = self.avg_up(), self.avg_down()
        return None if a is None or b is None else a + b


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def _book_levels(payload: dict[str, Any]) -> dict[str, float | None]:
    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []
    for raw, dest in ((payload.get("bids") or [], bids), (payload.get("asks") or [], asks)):
        for row in raw:
            if not isinstance(row, dict):
                continue
            p, q = _f(row.get("price"), -1.0), _f(row.get("size"), 0.0)
            if 0 <= p <= 1 and q > 0:
                dest.append((p, q))
    best_bid = max(bids, default=(None, None), key=lambda x: x[0])
    best_ask = min(asks, default=(None, None), key=lambda x: x[0])
    return {
        "best_bid": best_bid[0],
        "best_bid_size": best_bid[1],
        "best_ask": best_ask[0],
        "best_ask_size": best_ask[1],
    }


def fetch_book(http: httpx.Client, token_id: str) -> dict[str, float | None]:
    response = http.get(f"{CLOB_API}/book", params={"token_id": token_id})
    response.raise_for_status()
    payload = response.json()
    return _book_levels(payload if isinstance(payload, dict) else {})


def fetch_reward_configs(http: httpx.Client) -> dict[str, RewardConfig]:
    out: dict[str, RewardConfig] = {}
    cursor: str | None = None
    for _ in range(20):
        params: dict[str, Any] = {"page_size": 500}
        if cursor:
            params["next_cursor"] = cursor
        response = http.get(f"{CLOB_API}/rewards/markets/current", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            break
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("condition_id") or "").strip()
            if not cid:
                continue
            configs = row.get("rewards_config") or []
            rate = _f(row.get("total_daily_rate"), 0.0)
            if rate <= 0:
                rate = sum(_f(x.get("rate_per_day"), 0.0) for x in configs if isinstance(x, dict))
            out[cid] = RewardConfig(
                condition_id=cid,
                max_spread_cents=max(0.0, _f(row.get("rewards_max_spread"), 0.0)),
                min_size=max(0.0, _f(row.get("rewards_min_size"), 0.0)),
                total_daily_rate=max(0.0, rate),
            )
        cursor = str(payload.get("next_cursor") or "")
        if not cursor or cursor == "LTE=":
            break
    return out


def order_score(*, max_spread_cents: float, midpoint: float, price: float, size: float) -> float:
    """Polymarket raw S(v,s)*size score for one hypothetical order.

    This intentionally stops before normalization versus other makers, so it is
    not a dollar reward estimate.
    """
    v = max_spread_cents
    if v <= 0 or size <= 0:
        return 0.0
    spread_cents = abs(midpoint - price) * 100.0
    if spread_cents > v:
        return 0.0
    return ((v - spread_cents) / v) ** 2 * size


def two_sided_raw_score(
    *,
    cfg: RewardConfig,
    up_mid: float,
    down_mid: float,
    up_price: float,
    down_price: float,
    size: float,
) -> tuple[float, bool]:
    up = order_score(
        max_spread_cents=cfg.max_spread_cents,
        midpoint=up_mid,
        price=up_price,
        size=size,
    )
    down = order_score(
        max_spread_cents=cfg.max_spread_cents,
        midpoint=down_mid,
        price=down_price,
        size=size,
    )
    eligible = size >= cfg.min_size and up > 0 and down > 0
    return (min(up, down) if eligible else 0.0), eligible


def choose_resting_bid(book: dict[str, float | None]) -> float | None:
    """Use current best bid; no inside-market assumption and no queue-priority claim."""
    bid = book.get("best_bid")
    ask = book.get("best_ask")
    if bid is None or ask is None:
        return None
    bid_f, ask_f = float(bid), float(ask)
    if not (0 < bid_f < ask_f < 1):
        return None
    return bid_f


def midpoint(book: dict[str, float | None]) -> float | None:
    bid, ask = book.get("best_bid"), book.get("best_ask")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def _maybe_cross_through(
    *,
    state: MarketState,
    quote: RestingQuote | None,
    current_ask: float | None,
    token_side: str,
    now_ms: int,
    min_rest_ms: int,
) -> dict[str, Any] | None:
    if quote is None or not quote.active or current_ask is None:
        return None
    if now_ms - quote.placed_at_ms < min_rest_ms:
        return None
    # Strictly through our bid, not merely equal, to reduce queue-priority optimism.
    if float(current_ask) >= quote.price:
        return None
    quote.active = False
    q = quote.size
    cost = q * quote.price
    if token_side == "UP":
        state.up_qty += q
        state.up_cost += cost
    else:
        state.down_qty += q
        state.down_cost += cost
    state.cross_through_candidates += 1
    state.cross_through_shares += q
    return {
        "type": "POLY_MAKER_CROSS_THROUGH_CANDIDATE",
        "stream": state.market.stream.name,
        "market_id": state.market.condition_id,
        "side": token_side,
        "quote_price": quote.price,
        "quote_size": q,
        "current_ask": current_ask,
        "rest_ms": now_ms - quote.placed_at_ms,
        "up_qty": state.up_qty,
        "down_qty": state.down_qty,
        "matched_qty": state.matched_qty(),
        "pair_cost": state.pair_cost(),
        "warning": "Cross-through is a conservative fill candidate, not a confirmed fill; queue priority is unobserved.",
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }


def market_snapshot(
    *,
    state: MarketState,
    cfg: RewardConfig | None,
    up_book: dict[str, float | None],
    down_book: dict[str, float | None],
    quote_size: float,
    now_ms: int,
    min_rest_ms: int,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    up_ask = up_book.get("best_ask")
    down_ask = down_book.get("best_ask")
    for side, quote, ask in (
        ("UP", state.up_quote, up_ask),
        ("DOWN", state.down_quote, down_ask),
    ):
        evt = _maybe_cross_through(
            state=state,
            quote=quote,
            current_ask=None if ask is None else float(ask),
            token_side=side,
            now_ms=now_ms,
            min_rest_ms=min_rest_ms,
        )
        if evt:
            events.append(evt)

    up_bid = choose_resting_bid(up_book)
    down_bid = choose_resting_bid(down_book)
    up_mid = midpoint(up_book)
    down_mid = midpoint(down_book)
    if None in (up_bid, down_bid, up_mid, down_mid):
        return events

    # Replace our hypothetical quote whenever price changes or after a fill candidate.
    if state.up_quote is None or not state.up_quote.active or state.up_quote.price != up_bid:
        state.up_quote = RestingQuote(float(up_bid), quote_size, now_ms, "UP")
    if state.down_quote is None or not state.down_quote.active or state.down_quote.price != down_bid:
        state.down_quote = RestingQuote(float(down_bid), quote_size, now_ms, "DOWN")

    raw_score, eligible = (0.0, False)
    if cfg is not None:
        raw_score, eligible = two_sided_raw_score(
            cfg=cfg,
            up_mid=float(up_mid),
            down_mid=float(down_mid),
            up_price=float(up_bid),
            down_price=float(down_bid),
            size=quote_size,
        )
    state.quote_samples += 1
    state.eligible_samples += int(eligible)
    state.raw_score_sum += raw_score
    state.last_up_ask = None if up_ask is None else float(up_ask)
    state.last_down_ask = None if down_ask is None else float(down_ask)

    return events


def summarize(states: dict[str, MarketState], rewards: dict[str, RewardConfig]) -> dict[str, Any]:
    rows = list(states.values())
    by_stream: dict[str, dict[str, float]] = {}
    for s in rows:
        name = s.market.stream.name
        agg = by_stream.setdefault(name, {
            "markets": 0.0,
            "quote_samples": 0.0,
            "eligible_samples": 0.0,
            "raw_score_sum": 0.0,
            "cross_through_candidates": 0.0,
            "cross_through_shares": 0.0,
            "hypothetical_matched_qty": 0.0,
            "reward_daily_rate_visible": 0.0,
        })
        agg["markets"] += 1
        agg["quote_samples"] += s.quote_samples
        agg["eligible_samples"] += s.eligible_samples
        agg["raw_score_sum"] += s.raw_score_sum
        agg["cross_through_candidates"] += s.cross_through_candidates
        agg["cross_through_shares"] += s.cross_through_shares
        agg["hypothetical_matched_qty"] += s.matched_qty()
        cfg = rewards.get(s.market.condition_id)
        if cfg:
            agg["reward_daily_rate_visible"] += cfg.total_daily_rate
    return {
        "type": "POLY_MAKER_ECON_SHADOW_SUMMARY",
        "market_count": len(rows),
        "reward_configured_markets": sum(1 for s in rows if s.market.condition_id in rewards),
        "quote_samples": sum(s.quote_samples for s in rows),
        "eligible_samples": sum(s.eligible_samples for s in rows),
        "cross_through_candidates": sum(s.cross_through_candidates for s in rows),
        "cross_through_shares": sum(s.cross_through_shares for s in rows),
        "hypothetical_matched_qty": sum(s.matched_qty() for s in rows),
        "by_stream": by_stream,
        "interpretation": "Reward score is raw/un-normalized; cross-through events are not confirmed fills; reward_daily_rate_visible is pool configuration, not our earnings.",
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }


def run(*, poll_seconds: float, discovery_seconds: float, reward_seconds: float, summary_seconds: float, quote_size: float, min_rest_seconds: float) -> None:
    states: dict[str, MarketState] = {}
    rewards: dict[str, RewardConfig] = {}
    next_discovery = 0.0
    next_rewards = 0.0
    next_summary = 0.0

    with httpx.Client(timeout=12.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:
        while True:
            now = time.time()
            now_ms = int(now * 1000)
            if now >= next_rewards:
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
                next_rewards = now + reward_seconds

            if now >= next_discovery:
                try:
                    markets = discover_markets(http, horizons=(5, 15))
                    for market in markets:
                        if market.condition_id not in states:
                            states[market.condition_id] = MarketState(market=market)
                            cfg = rewards.get(market.condition_id)
                            _emit({
                                "type": "POLY_MAKER_MARKET_DISCOVERED",
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
                    next_discovery = now + discovery_seconds
                except Exception as exc:
                    _emit({"type": "POLY_MAKER_DISCOVERY_ERROR", "error": type(exc).__name__, "message": str(exc)[:300], "shadow_only": True})
                    next_discovery = now + min(5.0, discovery_seconds)

            for cid, state in list(states.items()):
                market = state.market
                if state.closed:
                    continue
                if market.end_ts_ms is not None and now_ms >= market.end_ts_ms:
                    state.closed = True
                    _emit({
                        "type": "POLY_MAKER_MARKET_ENDED",
                        "stream": market.stream.name,
                        "market_id": cid,
                        "up_qty": state.up_qty,
                        "down_qty": state.down_qty,
                        "matched_qty": state.matched_qty(),
                        "pair_cost": state.pair_cost(),
                        "cross_through_candidates": state.cross_through_candidates,
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })
                    continue
                try:
                    up_book = fetch_book(http, market.up_token_id)
                    down_book = fetch_book(http, market.down_token_id)
                    cfg = rewards.get(cid)
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
                        "market_id": cid,
                        "error": type(exc).__name__,
                        "message": str(exc)[:300],
                        "shadow_only": True,
                    })

            if now >= next_summary:
                _emit(summarize(states, rewards))
                next_summary = now + summary_seconds
            time.sleep(max(0.1, poll_seconds))


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only reward-aware Polymarket maker economics shadow")
    p.add_argument("--poll-seconds", type=float, default=0.5)
    p.add_argument("--discovery-seconds", type=float, default=20.0)
    p.add_argument("--reward-seconds", type=float, default=60.0)
    p.add_argument("--summary-seconds", type=float, default=10.0)
    p.add_argument("--quote-size", type=float, default=10.0)
    p.add_argument("--min-rest-seconds", type=float, default=3.5)
    args = p.parse_args()
    run(
        poll_seconds=args.poll_seconds,
        discovery_seconds=args.discovery_seconds,
        reward_seconds=args.reward_seconds,
        summary_seconds=args.summary_seconds,
        quote_size=max(0.01, args.quote_size),
        min_rest_seconds=max(0.0, args.min_rest_seconds),
    )


if __name__ == "__main__":
    main()
