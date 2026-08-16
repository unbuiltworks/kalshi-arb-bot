"""Activation-safe prewarmed HOT scanner with prospective settlement-basis annotation.

This is a read-only research composition of two independently validated pieces:
1. the double-buffered Gemini/Kalshi 15-minute scanner with event-order and exact
   activation-timestamp gating; and
2. a Gemini-public-spot proxy compared with Gemini Prediction's published Kaiko
   strike at the exact time each quote-lock episode is observable.

Gemini spot is NOT the official settlement index. Kaiko remains the Gemini prediction
market source, so 2 bp / 5 bp fields are research cohorts only and never authorize an
order. The proxy context uses Gemini public data only and adds no Kalshi discovery
traffic. Every episode tracks the *entire* positive interval: if basis context becomes
stale, disappears, or dips inside a threshold on any qualifying event, the corresponding
``all_events`` gate fails closed.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import json
import logging
import time
from typing import Any

import httpx
import websockets

from src import gemini_kalshi_predictive_prewarmed_safe as prewarmed
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto, _gemini_meta
from src.gemini_kalshi_live_shadow import GEMINI_WS
from src.gemini_kalshi_predictive_hot import _next_window_end
from src.gemini_kalshi_settlement_agreement import _gemini_up_contract

hot = prewarmed.hot
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_prewarmed_basis_shadow")
ASSET_SYMBOL = {"BTC": "btcusd", "ETH": "ethusd", "SOL": "solusd", "XRP": "xrpusd"}
SYMBOL_ASSET = {symbol: asset for asset, symbol in ASSET_SYMBOL.items()}


@dataclass
class SpotState:
    asset: str
    history: deque[tuple[int, Decimal]] = field(default_factory=deque)

    def update(self, *, bid: Decimal, ask: Decimal, receive_ms: int) -> None:
        self.history.append((int(receive_ms), (bid + ask) / D("2")))
        cutoff = int(receive_ms) - 125_000
        while len(self.history) > 2 and self.history[1][0] < cutoff:
            self.history.popleft()


@dataclass
class BasisEpisodeState:
    asset: str
    direction: str
    start_ms: int
    observations: int = 0
    entry_abs_margin_bps: Decimal | None = None
    min_abs_margin_bps: Decimal | None = None
    max_spot_quote_age_ms: int | None = None
    entry_gate_2bp: bool = False
    entry_gate_5bp: bool = False
    all_events_fresh_1000ms: bool = True
    all_events_gate_2bp: bool = True
    all_events_gate_5bp: bool = True

    def observe(self, snapshot: dict[str, Any] | None) -> None:
        self.observations += 1
        if snapshot is None:
            self.all_events_fresh_1000ms = False
            self.all_events_gate_2bp = False
            self.all_events_gate_5bp = False
            return
        abs_margin = D(str(snapshot["spot_abs_margin_bps"]))
        age = int(snapshot["spot_quote_age_ms"])
        fresh = bool(snapshot["proxy_fresh_1000ms"])
        gate2 = bool(snapshot["research_proxy_gate_2bp_fresh_1000ms"])
        gate5 = bool(snapshot["research_proxy_gate_5bp_fresh_1000ms"])
        if self.entry_abs_margin_bps is None:
            self.entry_abs_margin_bps = abs_margin
            self.entry_gate_2bp = gate2
            self.entry_gate_5bp = gate5
        self.min_abs_margin_bps = (
            abs_margin
            if self.min_abs_margin_bps is None
            else min(self.min_abs_margin_bps, abs_margin)
        )
        self.max_spot_quote_age_ms = age if self.max_spot_quote_age_ms is None else max(self.max_spot_quote_age_ms, age)
        self.all_events_fresh_1000ms = self.all_events_fresh_1000ms and fresh
        self.all_events_gate_2bp = self.all_events_gate_2bp and gate2
        self.all_events_gate_5bp = self.all_events_gate_5bp and gate5


_STATES: dict[str, SpotState] = {asset: SpotState(asset) for asset in ASSET_SYMBOL}
# asset -> (window_end_iso, strike, source_details)
_STRIKES: dict[str, tuple[str, Decimal, Any]] = {}
_BASIS_EPISODES: dict[tuple[str, str], BasisEpisodeState] = {}
_ORIGINAL_COLLECT_ALL = hot.collect_all
_ORIGINAL_UPDATE_EPISODES = hot._update_episodes
_ORIGINAL_CLOSE_MISSING_EPISODES = hot._close_missing_episodes
_INSTALLED = False


def _dec(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def latest_at_or_before(history, target_ms: int) -> tuple[int, Decimal] | None:
    """Newest spot midpoint knowable at ``target_ms``; never uses a future quote."""
    best = None
    for ts, price in history:
        if int(ts) <= int(target_ms):
            best = (int(ts), D(price))
        else:
            break
    return best


def twap_at_or_before(history, target_ms: int, window_ms: int = 60_000) -> tuple[Decimal | None, int | None]:
    """Piecewise-constant TWAP using only observations at/before ``target_ms``."""
    rows = [(int(ts), D(price)) for ts, price in history if int(ts) <= int(target_ms)]
    if not rows:
        return None, None
    start = int(target_ms) - int(window_ms)
    anchor_idx = 0
    for i, (ts, _price) in enumerate(rows):
        if ts <= start:
            anchor_idx = i
        else:
            break
    rows = rows[anchor_idx:]
    calc_start = max(start, rows[0][0])
    current = rows[0][1]
    for ts, price in rows:
        if ts <= calc_start:
            current = price
        else:
            break
    cursor = calc_start
    area = D("0")
    for ts, price in rows:
        if ts <= calc_start:
            continue
        if ts > cursor:
            area += current * D(ts - cursor)
        current = price
        cursor = ts
    if target_ms > cursor:
        area += current * D(target_ms - cursor)
    span = int(target_ms) - calc_start
    return ((area / D(span)) if span > 0 else current), span


def _event_strike(event: dict[str, Any]) -> Decimal | None:
    contract = _gemini_up_contract(event)
    if contract is None:
        return None
    strike = contract.get("strike") or {}
    return _dec(strike.get("value")) if isinstance(strike, dict) else None


async def _fetch_window_events(http: httpx.AsyncClient, window_end, timeout_s: float = 30.0) -> dict[str, dict[str, Any]]:
    """Gemini-only paced metadata wait; absent strikes fail closed."""
    deadline = time.monotonic() + timeout_s
    while True:
        rows = await _gemini_active_crypto(http)
        out: dict[str, dict[str, Any]] = {}
        for event in rows:
            meta = _gemini_meta(event)
            if meta is None:
                continue
            asset, duration, end = meta
            if (
                duration == 15
                and asset in ASSET_SYMBOL
                and abs((end - window_end).total_seconds()) < 1
                and _event_strike(event) is not None
            ):
                out[asset] = event
        if len(out) == len(ASSET_SYMBOL) or time.monotonic() >= deadline:
            return out
        await asyncio.sleep(0.5)


async def _spot_stream() -> None:
    streams = [f"{symbol}@bookTicker" for symbol in ASSET_SYMBOL.values()]
    backoff = 0.25
    while True:
        try:
            async with websockets.connect(GEMINI_WS, open_timeout=5.0) as ws:
                await ws.send(json.dumps({"id": "prewarm-basis-spot", "method": "subscribe", "params": streams}))
                backoff = 0.25
                async for raw in ws:
                    receive_ms = int(time.time() * 1000)
                    payload = json.loads(raw)
                    if payload.get("status") is not None:
                        if int(payload.get("status")) >= 400:
                            raise RuntimeError(f"Gemini spot WS subscription error: {payload}")
                        continue
                    asset = SYMBOL_ASSET.get(str(payload.get("s") or "").lower())
                    if asset is None:
                        continue
                    bid, ask = _dec(payload.get("b")), _dec(payload.get("a"))
                    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
                        continue
                    _STATES[asset].update(bid=bid, ask=ask, receive_ms=receive_ms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("CRYPTO_HOT_BASIS_SPOT_RECONNECT type=%s error=%s", type(exc).__name__, str(exc)[:220])
            await asyncio.sleep(backoff)
            backoff = min(5.0, backoff * 2)


async def _metadata_loop() -> None:
    async with httpx.AsyncClient(timeout=20.0) as http:
        while True:
            window_end = _next_window_end()
            try:
                events = await _fetch_window_events(http, window_end, timeout_s=30.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("CRYPTO_HOT_BASIS_METADATA_ERROR type=%s error=%s", type(exc).__name__, str(exc)[:220])
                await asyncio.sleep(2.0)
                continue
            _STRIKES.clear()
            for asset, event in events.items():
                strike = _event_strike(event)
                if strike is not None:
                    _STRIKES[asset] = (
                        window_end.isoformat(),
                        strike,
                        event.get("sourceDetails") or event.get("source_details"),
                    )
            logger.info("CRYPTO_HOT_BASIS_CONTEXT %s", json.dumps({
                "window_end_utc": window_end.isoformat(),
                "assets_with_strike": sorted(_STRIKES),
                "proxy": "GEMINI_PUBLIC_SPOT_BOOK_MID",
                "official_live_kaiko": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")))
            await asyncio.sleep(max(0.05, window_end.timestamp() + 0.25 - time.time()))


def basis_snapshot(asset: str, candidate_ms: int) -> dict[str, Any] | None:
    strike_info = _STRIKES.get(asset)
    state = _STATES.get(asset)
    if strike_info is None or state is None:
        return None
    window_end_iso, strike, source_details = strike_info
    if strike == 0:
        return None
    latest = latest_at_or_before(state.history, int(candidate_ms))
    if latest is None:
        return None
    quote_ms, spot_mid = latest
    quote_age_ms = int(candidate_ms) - quote_ms
    if quote_age_ms < 0:
        return None
    twap60, twap_span_ms = twap_at_or_before(state.history, int(candidate_ms), 60_000)
    margin = (spot_mid - strike) / strike * D("10000")
    twap_margin = ((twap60 - strike) / strike * D("10000")) if twap60 is not None else None
    return {
        "basis_proxy": "GEMINI_PUBLIC_SPOT_BOOK_MID",
        "official_live_kaiko": False,
        "kaiko_strike": str(strike),
        "kaiko_source": source_details,
        "basis_window_end_utc": window_end_iso,
        "spot_mid": str(spot_mid),
        "spot_quote_receive_ms": quote_ms,
        "spot_quote_age_ms": quote_age_ms,
        "spot_mid_margin_bps": str(margin),
        "spot_abs_margin_bps": str(abs(margin)),
        "spot_60s_twap": str(twap60) if twap60 is not None else None,
        "spot_60s_twap_margin_bps": str(twap_margin) if twap_margin is not None else None,
        "twap_observed_span_ms": twap_span_ms,
        "proxy_fresh_250ms": quote_age_ms <= 250,
        "proxy_fresh_1000ms": quote_age_ms <= 1000,
        "research_proxy_gate_2bp_fresh_1000ms": quote_age_ms <= 1000 and abs(margin) >= D("2"),
        "research_proxy_gate_5bp_fresh_1000ms": quote_age_ms <= 1000 and abs(margin) >= D("5"),
    }


async def _collect_all_with_basis(**kwargs):
    spot_task = asyncio.create_task(_spot_stream(), name="prewarm-basis-gemini-spot")
    metadata_task = asyncio.create_task(_metadata_loop(), name="prewarm-basis-metadata")
    try:
        return await _ORIGINAL_COLLECT_ALL(**kwargs)
    finally:
        spot_task.cancel()
        metadata_task.cancel()
        await asyncio.gather(spot_task, metadata_task, return_exceptions=True)


def _update_episodes_with_basis(*, active, qualifying, now_ms, asset, pair, trigger_venue) -> None:
    # Observe basis on every qualifying event, not just episode entry. This lets the
    # close record require a threshold to have held throughout the whole interval.
    for direction, (q, candidate) in qualifying.items():
        key = (asset, direction)
        is_new = direction not in active or key not in _BASIS_EPISODES
        state = _BASIS_EPISODES.get(key)
        if state is None:
            state = BasisEpisodeState(asset=asset, direction=direction, start_ms=int(now_ms))
            _BASIS_EPISODES[key] = state
        snapshot = basis_snapshot(asset, int(now_ms))
        state.observe(snapshot)
        if is_new:
            logger.info("CRYPTO_HOT_BASIS_CANDIDATE %s", json.dumps({
                "asset": asset,
                "direction": direction,
                "q": q,
                "candidate_receive_ms": int(now_ms),
                "trigger_venue": trigger_venue,
                "gemini_symbol": pair.gemini_symbol,
                "kalshi_ticker": pair.kalshi_ticker,
                "capital": str(candidate.net_cost),
                "locked_quote_profit": str(candidate.locked_profit),
                "fees": str(candidate.fees),
                "quote_skew_ms": candidate.quote_skew_ms,
                "basis_context_available": snapshot is not None,
                **(snapshot or {}),
                "settlement_status": hot.SETTLEMENT_STATUS,
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
    _ORIGINAL_UPDATE_EPISODES(
        active=active,
        qualifying=qualifying,
        now_ms=now_ms,
        asset=asset,
        pair=pair,
        trigger_venue=trigger_venue,
    )


def _close_missing_with_basis(*, active, qualifying, now_ms, asset, pair) -> None:
    # Capture close metrics before the underlying HOT function removes ActiveEpisode.
    closing = [direction for direction in active if direction not in qualifying]
    for direction in closing:
        key = (asset, direction)
        state = _BASIS_EPISODES.pop(key, None)
        ep = active.get(direction)
        if ep is None:
            continue
        duration_ms = max(0, int(now_ms) - int(ep.start_ms))
        logger.info("CRYPTO_HOT_BASIS_EPISODE_CLOSE %s", json.dumps({
            "asset": asset,
            "direction": direction,
            "start_ms": int(ep.start_ms),
            "end_ms": int(now_ms),
            "duration_ms": duration_ms,
            "survives_50ms": duration_ms >= 50,
            "survives_100ms": duration_ms >= 100,
            "best_locked_quote_profit": str(ep.best_profit),
            "best_capital": str(ep.best_capital),
            "best_q": ep.best_q,
            "basis_observations": state.observations if state else 0,
            "entry_abs_spot_margin_bps": str(state.entry_abs_margin_bps) if state and state.entry_abs_margin_bps is not None else None,
            "min_abs_spot_margin_bps_during_episode": str(state.min_abs_margin_bps) if state and state.min_abs_margin_bps is not None else None,
            "max_spot_quote_age_ms_during_episode": state.max_spot_quote_age_ms if state else None,
            "entry_gate_2bp": state.entry_gate_2bp if state else False,
            "entry_gate_5bp": state.entry_gate_5bp if state else False,
            "all_events_fresh_1000ms": state.all_events_fresh_1000ms if state else False,
            "all_events_gate_2bp": state.all_events_gate_2bp if state else False,
            "all_events_gate_5bp": state.all_events_gate_5bp if state else False,
            "research_2bp_50ms_survivor": bool(state and state.all_events_gate_2bp and duration_ms >= 50),
            "research_2bp_100ms_survivor": bool(state and state.all_events_gate_2bp and duration_ms >= 100),
            "research_5bp_50ms_survivor": bool(state and state.all_events_gate_5bp and duration_ms >= 50),
            "research_5bp_100ms_survivor": bool(state and state.all_events_gate_5bp and duration_ms >= 100),
            "gemini_symbol": pair.gemini_symbol,
            "kalshi_ticker": pair.kalshi_ticker,
            "settlement_status": hot.SETTLEMENT_STATUS,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
    _ORIGINAL_CLOSE_MISSING_EPISODES(
        active=active,
        qualifying=qualifying,
        now_ms=now_ms,
        asset=asset,
        pair=pair,
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    hot.collect_all = _collect_all_with_basis
    hot._update_episodes = _update_episodes_with_basis
    hot._close_missing_episodes = _close_missing_with_basis
    _INSTALLED = True


def main() -> None:
    install()
    logger.info(
        "CRYPTO_HOT_PREWARMED_BASIS_MODE_ENABLED activation_gate=true sequential_eval=true proxy=gemini_spot official_live_kaiko=false read_only=true order_entry=false"
    )
    prewarmed.main()


if __name__ == "__main__":
    main()
