"""Prospective basis annotation for the read-only Gemini/Kalshi HOT scanner.

This shadow layer answers a narrow question: when an executable complementary quote
appears, how far is *observable Gemini spot* from Gemini Prediction's published Kaiko
strike at that exact moment?

It does not treat Gemini spot as the settlement index. Kaiko remains the official
Gemini prediction-market source, so every annotation is explicitly a proxy and no
order-entry authorization is added. The layer uses only Gemini public spot WebSocket
quotes plus Gemini prediction metadata; it adds no Kalshi REST discovery load.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import gemini_kalshi_predictive_hot as predictive
from src.gemini_kaiko_proxy_calibration import (
    ASSET_SYMBOL,
    SpotState,
    _event_strike,
    fetch_window_events,
    spot_stream,
)
from src.gemini_kalshi_predictive_hot import _next_window_end

D = Decimal
hot = predictive.hot
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_annotated_shadow")

_STATES: dict[str, SpotState] = {asset: SpotState(asset) for asset in ASSET_SYMBOL}
# asset -> (window_end_iso, strike, source_details)
_STRIKES: dict[str, tuple[str, Decimal, Any]] = {}
_INSTALLED = False
_ORIGINAL_COLLECT_ALL = hot.collect_all
_ORIGINAL_UPDATE_EPISODES = hot._update_episodes


def latest_at_or_before(
    history: Iterable[tuple[int, Decimal]], target_ms: int
) -> tuple[int, Decimal] | None:
    """Return the newest observed spot midpoint that was knowable by target_ms."""
    best: tuple[int, Decimal] | None = None
    for ts, price in history:
        if ts <= target_ms:
            best = (int(ts), D(price))
        else:
            break
    return best


def twap_at_or_before(
    history: Iterable[tuple[int, Decimal]], target_ms: int, window_ms: int = 60_000
) -> tuple[Decimal | None, int | None]:
    """Piecewise-constant TWAP using only observations at/before target_ms.

    If history starts inside the requested interval, returns a shorter observed span
    rather than fabricating an earlier price.
    """
    rows = [(int(ts), D(price)) for ts, price in history if int(ts) <= target_ms]
    if not rows:
        return None, None
    start = target_ms - int(window_ms)

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
        dt = ts - cursor
        if dt > 0:
            area += current * D(dt)
        current = price
        cursor = ts
    if target_ms > cursor:
        area += current * D(target_ms - cursor)
    span = target_ms - calc_start
    return ((area / D(span)) if span > 0 else current), span


def basis_snapshot(asset: str, candidate_ms: int) -> dict[str, Any] | None:
    """Build a no-lookahead spot-vs-strike proxy snapshot for one HOT candidate."""
    strike_info = _STRIKES.get(asset)
    state = _STATES.get(asset)
    if strike_info is None or state is None:
        return None
    window_end_iso, strike, source_details = strike_info
    if strike == 0:
        return None

    latest = latest_at_or_before(state.history, candidate_ms)
    if latest is None:
        return None
    quote_ms, spot_mid = latest
    quote_age_ms = candidate_ms - quote_ms
    if quote_age_ms < 0:
        return None
    twap60, twap_span_ms = twap_at_or_before(state.history, candidate_ms, 60_000)
    mid_margin_bps = (spot_mid - strike) / strike * D("10000")
    twap_margin_bps = (
        (twap60 - strike) / strike * D("10000")
        if twap60 is not None
        else None
    )
    return {
        "basis_proxy": "GEMINI_PUBLIC_SPOT_BOOK_MID",
        "official_live_kaiko": False,
        "kaiko_strike": str(strike),
        "kaiko_source": source_details,
        "basis_window_end_utc": window_end_iso,
        "spot_mid": str(spot_mid),
        "spot_quote_receive_ms": quote_ms,
        "spot_quote_age_ms": quote_age_ms,
        "spot_mid_margin_bps": str(mid_margin_bps),
        "spot_abs_margin_bps": str(abs(mid_margin_bps)),
        "spot_60s_twap": str(twap60) if twap60 is not None else None,
        "spot_60s_twap_margin_bps": str(twap_margin_bps) if twap_margin_bps is not None else None,
        "twap_observed_span_ms": twap_span_ms,
        "proxy_fresh_250ms": quote_age_ms <= 250,
        "proxy_fresh_1000ms": quote_age_ms <= 1000,
        "research_proxy_gate_2bp_fresh_1000ms": quote_age_ms <= 1000 and abs(mid_margin_bps) >= D("2"),
        "research_proxy_gate_5bp_fresh_1000ms": quote_age_ms <= 1000 and abs(mid_margin_bps) >= D("5"),
    }


async def _basis_metadata_loop() -> None:
    """Keep the current window's published Kaiko strikes in memory.

    Gemini may publish the strike a few seconds after the quarter-hour. Polling is
    Gemini-only and paced; missing metadata simply leaves candidate annotations
    unavailable rather than guessing.
    """
    async with httpx.AsyncClient(timeout=20.0) as http:
        while True:
            window_end = _next_window_end()
            try:
                events = await fetch_window_events(http, window_end, timeout_s=30.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "CRYPTO_HOT_BASIS_METADATA_ERROR type=%s error=%s",
                    type(exc).__name__, str(exc)[:220],
                )
                await asyncio.sleep(2.0)
                continue

            for asset, event in events.items():
                strike = _event_strike(event)
                if strike is None:
                    continue
                _STRIKES[asset] = (
                    window_end.isoformat(),
                    strike,
                    event.get("sourceDetails") or event.get("source_details"),
                )
            logger.info(
                "CRYPTO_HOT_BASIS_CONTEXT %s",
                json.dumps({
                    "window_end_utc": window_end.isoformat(),
                    "assets_with_strike": sorted(events),
                    "proxy": "GEMINI_PUBLIC_SPOT_BOOK_MID",
                    "official_live_kaiko": False,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )
            await asyncio.sleep(max(0.05, window_end.timestamp() + 0.25 - time.time()))


async def _basis_collect_all(**kwargs):
    spot_task = asyncio.create_task(spot_stream(_STATES), name="hot-basis-gemini-spot")
    metadata_task = asyncio.create_task(_basis_metadata_loop(), name="hot-basis-strikes")
    try:
        return await _ORIGINAL_COLLECT_ALL(**kwargs)
    finally:
        spot_task.cancel()
        metadata_task.cancel()
        await asyncio.gather(spot_task, metadata_task, return_exceptions=True)


def _basis_update_episodes(
    *, active, qualifying, now_ms, asset, pair, trigger_venue
) -> None:
    # Emit once, at episode entry, before the underlying function mutates ``active``.
    for direction, (q, candidate) in qualifying.items():
        if direction in active:
            continue
        basis = basis_snapshot(asset, int(now_ms))
        logger.info(
            "CRYPTO_HOT_BASIS_CANDIDATE %s",
            json.dumps({
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
                "basis_context_available": basis is not None,
                **(basis or {}),
                "settlement_status": hot.SETTLEMENT_STATUS,
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str),
        )
    _ORIGINAL_UPDATE_EPISODES(
        active=active,
        qualifying=qualifying,
        now_ms=now_ms,
        asset=asset,
        pair=pair,
        trigger_venue=trigger_venue,
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    hot.collect_all = _basis_collect_all
    hot._update_episodes = _basis_update_episodes
    _INSTALLED = True


def main() -> None:
    install()
    logger.info(
        "CRYPTO_HOT_BASIS_ANNOTATION_ENABLED proxy=gemini_spot official_live_kaiko=false read_only=true order_entry=false"
    )
    predictive.main()


if __name__ == "__main__":
    main()
