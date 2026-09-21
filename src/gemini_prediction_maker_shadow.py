"""Read-only Gemini Predictions short-crypto maker/reward shadow.

Public market/reward REST + public WebSocket only. No credentials, account reads,
order placement, signing, cancellation, or mutation code exists in this module.

Tracks BTC/ETH/SOL/XRP 5m and 15m UP contracts. Gemini market data is normalized
in YES space. A hypothetical BUY NO quote at q is represented in YES space as a
sell/ask at (1-q).

Shadow cross-through events are fill CANDIDATES only. The hypothetical order is
not actually present in Gemini's queue, so queue priority is unobservable.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import re
import time
from typing import Any

import httpx
import websockets

REST = "https://api.gemini.com"
WS = "wss://ws.gemini.com"
ASSETS = {"BTC", "ETH", "SOL", "XRP"}
EVENT_RE = re.compile(r"^(BTC|ETH|SOL|XRP)(05M|15M)(\d{10})$")


@dataclass(frozen=True)
class GeminiEvent:
    event_ticker: str
    instrument_symbol: str
    asset: str
    horizon_minutes: int
    title: str
    expiry_ms: int | None
    quantity_minimum: float
    price_increment: float

    @property
    def stream(self) -> str:
        return f"gemini:{self.asset}:{self.horizon_minutes}m"


@dataclass(frozen=True)
class RewardEvent:
    event_ticker: str
    daily_pool_usd: float
    qualifying_maker_count: int


@dataclass
class Quote:
    outcome: str
    price: float
    placed_ms: int
    size: float


@dataclass
class EventState:
    event: GeminiEvent
    yes_quote: Quote | None = None
    no_quote: Quote | None = None
    yes_qty: float = 0.0
    no_qty: float = 0.0
    yes_cost: float = 0.0
    no_cost: float = 0.0
    ticks: int = 0
    eligible_ticks: int = 0
    score_time_integral: float = 0.0
    eligible_ms: int = 0
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None
    last_score: float = 0.0
    cross_through_candidates: int = 0
    ws_messages: int = 0

    @property
    def matched_qty(self) -> float:
        return min(self.yes_qty, self.no_qty)

    @property
    def avg_yes(self) -> float | None:
        return self.yes_cost / self.yes_qty if self.yes_qty else None

    @property
    def avg_no(self) -> float | None:
        return self.no_cost / self.no_qty if self.no_qty else None

    @property
    def pair_cost(self) -> float | None:
        if self.avg_yes is None or self.avg_no is None:
            return None
        return self.avg_yes + self.avg_no


def _f(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


async def discover_events(http: httpx.AsyncClient) -> dict[str, GeminiEvent]:
    out: dict[str, GeminiEvent] = {}
    offset = 0
    while offset < 5000:
        response = await http.get(
            f"{REST}/v1/prediction-markets/events",
            params=[("status", "active"), ("category", "Crypto"), ("limit", "500"), ("offset", str(offset))],
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or [] if isinstance(payload, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            match = EVENT_RE.match(ticker)
            if not match:
                continue
            asset = match.group(1)
            horizon = 5 if match.group(2) == "05M" else 15
            for contract in row.get("contracts") or []:
                if not isinstance(contract, dict):
                    continue
                symbol = str(contract.get("instrumentSymbol") or "").strip()
                status = str(contract.get("marketState") or contract.get("status") or "").lower()
                if not symbol or status not in {"open", "active"}:
                    continue
                # New short-duration contracts use one UP/YES-space instrument.
                if not symbol.upper().endswith("-UP"):
                    continue
                out[symbol] = GeminiEvent(
                    event_ticker=ticker,
                    instrument_symbol=symbol,
                    asset=asset,
                    horizon_minutes=horizon,
                    title=str(row.get("title") or ticker),
                    expiry_ms=_iso_ms(contract.get("expiryDate") or row.get("expiryDate")),
                    quantity_minimum=max(0.01, _f(contract.get("quantityMinimum"), 1.0)),
                    price_increment=max(0.0001, _f(contract.get("priceIncrement"), 0.01)),
                )
        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        total = int((pagination or {}).get("total") or len(rows))
        offset += len(rows)
        if not rows or offset >= total:
            break
    return out


async def fetch_reward_state(http: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, RewardEvent], dict[str, Any]]:
    config: dict[str, Any] = {}
    reward_events: dict[str, RewardEvent] = {}
    rebate_rates: dict[str, Any] = {}

    try:
        response = await http.get(f"{REST}/v1/prediction-markets/liquidity-rewards/config")
        if response.status_code == 200 and isinstance(response.json(), dict):
            config = response.json()
    except Exception:
        pass

    offset = 0
    while offset < 5000:
        try:
            response = await http.get(
                f"{REST}/v1/prediction-markets/liquidity-rewards/events",
                params={"category": "Crypto", "sort": "daily_pool_desc", "limit": 100, "offset": offset},
            )
            if response.status_code != 200:
                break
            payload = response.json()
        except Exception:
            break
        rows = payload.get("events") or [] if isinstance(payload, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("event_ticker") or "").strip().upper()
            if EVENT_RE.match(ticker):
                reward_events[ticker] = RewardEvent(
                    event_ticker=ticker,
                    daily_pool_usd=max(0.0, _f(row.get("daily_pool_usd"))),
                    qualifying_maker_count=max(0, int(row.get("qualifying_maker_count") or 0)),
                )
        pagination = payload.get("pagination") or {}
        total = int(pagination.get("total") or len(rows))
        offset += len(rows)
        if not rows or offset >= total:
            break

    try:
        response = await http.get(
            f"{REST}/v1/prediction-markets/maker-rebate/rates",
            params={"category": "Crypto"},
        )
        if response.status_code == 200 and isinstance(response.json(), dict):
            rebate_rates = response.json()
    except Exception:
        pass

    return config, reward_events, rebate_rates


def snapshot_score(*, max_spread_cents: float, bid: float, ask: float, size: float) -> tuple[float, bool]:
    """Approximate Gemini published liquidity score for a symmetric top-of-book pair.

    Gemini publishes the structure (quadratic spread weight × size × two-sided
    multiplier), but this is our local approximation and not venue-authoritative
    normalized reward share.
    """
    if not (0 < bid < ask < 1) or max_spread_cents <= 0 or size < 10:
        return 0.0, False
    midpoint = (bid + ask) / 2.0
    distance_cents = max(midpoint - bid, ask - midpoint) * 100.0
    if distance_cents > max_spread_cents:
        return 0.0, False
    spread_weight = ((max_spread_cents - distance_cents) / max_spread_cents) ** 2
    capped_size = min(size, 250.0)
    return spread_weight * capped_size * 1.5, True


def _update_time_integral(state: EventState, now_ms: int, new_score: float, eligible: bool) -> None:
    if state.first_seen_ms is None:
        state.first_seen_ms = now_ms
    if state.last_seen_ms is not None:
        dt = max(0, now_ms - state.last_seen_ms)
        state.score_time_integral += state.last_score * (dt / 1000.0)
        if state.last_score > 0:
            state.eligible_ms += dt
    state.last_seen_ms = now_ms
    state.last_score = new_score if eligible else 0.0


def _quote_pair(state: EventState, bid: float, ask: float, size: float, now_ms: int) -> None:
    state.yes_quote = Quote("YES", bid, now_ms, size)
    state.no_quote = Quote("NO", 1.0 - ask, now_ms, size)


def process_book_tick(
    *,
    state: EventState,
    bid: float,
    ask: float,
    bid_size: float,
    ask_size: float,
    reward_config: dict[str, Any],
    reward_event: RewardEvent | None,
    quote_size: float,
    min_rest_ms: int,
    max_quote_age_ms: int,
    event_time_ns: int | None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    now_ms = int(time.time() * 1000)
    if not (0 < bid < ask < 1):
        return events

    effective_size = max(quote_size, state.event.quantity_minimum, 10.0)
    max_spread = _f(reward_config.get("max_spread_cents"), 10.0) if reward_config.get("enabled", True) else 0.0
    score, eligible = snapshot_score(max_spread_cents=max_spread, bid=bid, ask=ask, size=effective_size)
    _update_time_integral(state, now_ms, score, eligible)
    state.ticks += 1
    state.eligible_ticks += int(eligible)
    state.ws_messages += 1

    if state.yes_quote is None or state.no_quote is None:
        _quote_pair(state, bid, ask, effective_size, now_ms)
        return events

    # YES buy fills if the observed ask trades strictly through our rested YES bid.
    yes_rest = now_ms - state.yes_quote.placed_ms
    if yes_rest >= min_rest_ms and ask < state.yes_quote.price:
        q = state.yes_quote.size
        state.yes_qty += q
        state.yes_cost += q * state.yes_quote.price
        state.cross_through_candidates += 1
        events.append({
            "type": "GEMINI_MAKER_CROSS_THROUGH_CANDIDATE",
            "stream": state.event.stream,
            "event_ticker": state.event.event_ticker,
            "symbol": state.event.instrument_symbol,
            "outcome": "YES",
            "quote_price": state.yes_quote.price,
            "current_yes_ask": ask,
            "quote_size": q,
            "rest_ms": yes_rest,
            "matched_qty": state.matched_qty,
            "pair_cost": state.pair_cost,
            "event_time_ns": event_time_ns,
            "reward_pool_usd": None if reward_event is None else reward_event.daily_pool_usd,
            "shadow_only": True,
            "order_entry": False,
            "realized_pnl": 0.0,
            "warning": "Cross-through candidate only; hypothetical order was not in Gemini queue.",
        })
        _quote_pair(state, bid, ask, effective_size, now_ms)
        return events

    # BUY NO at q is equivalent in YES-space to an ask at (1-q).
    no_rest = now_ms - state.no_quote.placed_ms
    yes_equiv_ask = 1.0 - state.no_quote.price
    if no_rest >= min_rest_ms and bid > yes_equiv_ask:
        q = state.no_quote.size
        state.no_qty += q
        state.no_cost += q * state.no_quote.price
        state.cross_through_candidates += 1
        events.append({
            "type": "GEMINI_MAKER_CROSS_THROUGH_CANDIDATE",
            "stream": state.event.stream,
            "event_ticker": state.event.event_ticker,
            "symbol": state.event.instrument_symbol,
            "outcome": "NO",
            "quote_price": state.no_quote.price,
            "current_yes_bid": bid,
            "yes_equivalent_ask": yes_equiv_ask,
            "quote_size": q,
            "rest_ms": no_rest,
            "matched_qty": state.matched_qty,
            "pair_cost": state.pair_cost,
            "event_time_ns": event_time_ns,
            "reward_pool_usd": None if reward_event is None else reward_event.daily_pool_usd,
            "shadow_only": True,
            "order_entry": False,
            "realized_pnl": 0.0,
            "warning": "Cross-through candidate only; hypothetical order was not in Gemini queue.",
        })
        _quote_pair(state, bid, ask, effective_size, now_ms)
        return events

    # Refresh stale hypothetical quotes to current top-of-book so reward uptime
    # does not depend on an indefinitely stale phantom order.
    oldest = min(state.yes_quote.placed_ms, state.no_quote.placed_ms)
    if now_ms - oldest >= max_quote_age_ms:
        _quote_pair(state, bid, ask, effective_size, now_ms)

    return events


def summary(states: dict[str, EventState], rewards: dict[str, RewardEvent], connected_at: float) -> dict[str, Any]:
    rows = list(states.values())
    by_stream: dict[str, dict[str, float]] = {}
    for s in rows:
        agg = by_stream.setdefault(s.event.stream, {
            "events": 0.0,
            "ws_messages": 0.0,
            "eligible_ticks": 0.0,
            "ticks": 0.0,
            "cross_through_candidates": 0.0,
            "matched_qty": 0.0,
            "visible_daily_pool_usd": 0.0,
            "qualifying_makers": 0.0,
        })
        agg["events"] += 1
        agg["ws_messages"] += s.ws_messages
        agg["eligible_ticks"] += s.eligible_ticks
        agg["ticks"] += s.ticks
        agg["cross_through_candidates"] += s.cross_through_candidates
        agg["matched_qty"] += s.matched_qty
        r = rewards.get(s.event.event_ticker)
        if r:
            agg["visible_daily_pool_usd"] += r.daily_pool_usd
            agg["qualifying_makers"] += r.qualifying_maker_count
    return {
        "type": "GEMINI_MAKER_SHADOW_SUMMARY",
        "active_symbols": len(rows),
        "streams": sorted(by_stream),
        "by_stream": by_stream,
        "ws_messages": sum(s.ws_messages for s in rows),
        "eligible_ticks": sum(s.eligible_ticks for s in rows),
        "ticks": sum(s.ticks for s in rows),
        "cross_through_candidates": sum(s.cross_through_candidates for s in rows),
        "matched_qty": sum(s.matched_qty for s in rows),
        "visible_daily_pool_usd": sum(rewards.get(s.event.event_ticker, RewardEvent("", 0, 0)).daily_pool_usd for s in rows),
        "connected_seconds": time.time() - connected_at,
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
        "interpretation": "Public WS/reward shadow. Reward score is local/un-normalized and cross-through candidates are not confirmed fills.",
    }


async def run(*, quote_size: float, min_rest_seconds: float, quote_refresh_seconds: float, discovery_seconds: float, summary_seconds: float) -> None:
    states: dict[str, EventState] = {}
    reward_config: dict[str, Any] = {}
    rewards: dict[str, RewardEvent] = {}
    rebate_rates: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-maker-shadow/1.0"}) as http:
        while True:
            try:
                events = await discover_events(http)
                reward_config, rewards, rebate_rates = await fetch_reward_state(http)
                for symbol, event in events.items():
                    if symbol not in states:
                        states[symbol] = EventState(event=event)
                active_symbols = sorted(events)
                _emit({
                    "type": "GEMINI_MAKER_DISCOVERY",
                    "active_symbols": len(active_symbols),
                    "streams": sorted({events[x].stream for x in active_symbols}),
                    "reward_config": reward_config,
                    "reward_events": {
                        t: {"daily_pool_usd": r.daily_pool_usd, "qualifying_maker_count": r.qualifying_maker_count}
                        for t, r in rewards.items()
                        if t in {e.event_ticker for e in events.values()}
                    },
                    "maker_rebate_rate_schedule": rebate_rates,
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                if not active_symbols:
                    await asyncio.sleep(max(2.0, discovery_seconds))
                    continue

                connected_at = time.time()
                next_discovery = connected_at + discovery_seconds
                next_summary = connected_at + summary_seconds
                async with websockets.connect(WS, open_timeout=8, ping_interval=15, ping_timeout=8, max_queue=10000) as ws:
                    streams = []
                    for symbol in active_symbols:
                        streams.extend([f"{symbol}@bookTicker", f"{symbol}@trade"])
                    await ws.send(json.dumps({"id": "arb-shadow-1", "method": "SUBSCRIBE", "params": streams}))
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
                                # bookTicker has b/B/a/A and no e field.
                                if state is not None and all(k in data for k in ("b", "B", "a", "A")):
                                    bid = _f(data.get("b"), -1)
                                    ask = _f(data.get("a"), -1)
                                    for evt in process_book_tick(
                                        state=state,
                                        bid=bid,
                                        ask=ask,
                                        bid_size=_f(data.get("B")),
                                        ask_size=_f(data.get("A")),
                                        reward_config=reward_config,
                                        reward_event=rewards.get(state.event.event_ticker),
                                        quote_size=quote_size,
                                        min_rest_ms=int(min_rest_seconds * 1000),
                                        max_quote_age_ms=int(quote_refresh_seconds * 1000),
                                        event_time_ns=int(data.get("E")) if data.get("E") is not None else None,
                                    ):
                                        _emit(evt)
                        if now >= next_summary:
                            _emit(summary({k: states[k] for k in active_symbols if k in states}, rewards, connected_at))
                            next_summary = now + summary_seconds
                        if now >= next_discovery:
                            break
            except Exception as exc:
                _emit({
                    "type": "GEMINI_MAKER_SHADOW_ERROR",
                    "error": type(exc).__name__,
                    "message": str(exc)[:500],
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                await asyncio.sleep(2.0)


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only Gemini Predictions maker/reward shadow")
    p.add_argument("--quote-size", type=float, default=10.0)
    p.add_argument("--min-rest-seconds", type=float, default=1.0)
    p.add_argument("--quote-refresh-seconds", type=float, default=8.0)
    p.add_argument("--discovery-seconds", type=float, default=20.0)
    p.add_argument("--summary-seconds", type=float, default=10.0)
    args = p.parse_args()
    asyncio.run(run(
        quote_size=max(0.01, args.quote_size),
        min_rest_seconds=max(0.0, args.min_rest_seconds),
        quote_refresh_seconds=max(1.0, args.quote_refresh_seconds),
        discovery_seconds=max(5.0, args.discovery_seconds),
        summary_seconds=max(5.0, args.summary_seconds),
    ))


if __name__ == "__main__":
    main()
