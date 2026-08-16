"""Prospective read-only proxy calibration for Gemini's Kaiko 15-minute settlement.

Gemini 15m prediction contracts settle against Kaiko reference rates, not Gemini
spot. Kaiko's live benchmark stream is subscription-gated, so this collector records
only information that is actually observable without that subscription:

* Gemini public spot bookTicker midpoint for BTC/USD, ETH/USD, SOL/USD and XRP/USD
* a 60-second piecewise-constant TWAP of that midpoint
* the Gemini prediction contract's published Kaiko strike

At fixed pre-close offsets it logs prospective proxy margins. After the prediction
contract settles, it joins those snapshots to Gemini's actual Kaiko settlementValue
and logs proxy error. This is calibration research only and never authorizes orders.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import time
from typing import Any

import httpx
import websockets

from src.gemini_kalshi_fast_overlap import _gemini_active_crypto, _gemini_meta
from src.gemini_kalshi_live_shadow import GEMINI_WS
from src.gemini_kalshi_predictive_hot import _next_window_end
from src.gemini_kalshi_settlement_agreement import (
    _gemini_outcome,
    _gemini_settled,
    _gemini_up_contract,
)

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kaiko_proxy_calibration")
ASSET_SYMBOL = {"BTC": "btcusd", "ETH": "ethusd", "SOL": "solusd", "XRP": "xrpusd"}
SYMBOL_ASSET = {v: k for k, v in ASSET_SYMBOL.items()}


def _dec(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


@dataclass
class SpotState:
    asset: str
    history: deque[tuple[int, Decimal]] = field(default_factory=deque)
    bid: Decimal | None = None
    ask: Decimal | None = None
    mid: Decimal | None = None
    receive_ms: int | None = None
    exchange_event_ns: int | None = None

    def update(self, *, bid: Decimal, ask: Decimal, receive_ms: int, exchange_event_ns: int | None) -> None:
        mid = (bid + ask) / D("2")
        self.bid, self.ask, self.mid = bid, ask, mid
        self.receive_ms = receive_ms
        self.exchange_event_ns = exchange_event_ns
        self.history.append((receive_ms, mid))
        # Keep enough history for a full 60s TWAP plus a pre-window anchor.
        cutoff = receive_ms - 125_000
        while len(self.history) > 2 and self.history[1][0] < cutoff:
            self.history.popleft()

    def twap(self, *, now_ms: int, window_ms: int = 60_000) -> tuple[Decimal | None, int | None]:
        if not self.history:
            return None, None
        start = now_ms - window_ms
        rows = list(self.history)
        # Find the latest observation at/before the calculation-window start. If the
        # collector started inside the window, report the shorter observed span.
        anchor_idx = 0
        for i, (ts, _price) in enumerate(rows):
            if ts <= start:
                anchor_idx = i
            else:
                break
        rows = rows[anchor_idx:]
        if not rows:
            return None, None

        calc_start = max(start, rows[0][0])
        cursor = calc_start
        # Use the latest known price at calc_start.
        current_price = rows[0][1]
        for ts, price in rows:
            if ts <= calc_start:
                current_price = price
                continue
            if ts > now_ms:
                break
            cursor = max(cursor, calc_start)
            # Integrate prior midpoint until this update.
            break

        # Rebuild the integration cleanly from the value in force at calc_start.
        current_price = rows[0][1]
        for ts, price in rows:
            if ts <= calc_start:
                current_price = price
            else:
                break
        cursor = calc_start
        area = D("0")
        for ts, price in rows:
            if ts <= calc_start:
                continue
            if ts > now_ms:
                break
            dt = ts - cursor
            if dt > 0:
                area += current_price * D(dt)
            current_price = price
            cursor = ts
        if now_ms > cursor:
            area += current_price * D(now_ms - cursor)
        span = now_ms - calc_start
        return ((area / D(span)) if span > 0 else current_price), span


async def spot_stream(states: dict[str, SpotState]) -> None:
    streams = [f"{symbol}@bookTicker" for symbol in ASSET_SYMBOL.values()]
    backoff = 0.25
    while True:
        try:
            async with websockets.connect(GEMINI_WS, open_timeout=5.0) as ws:
                await ws.send(json.dumps({"id": "kaiko-proxy", "method": "subscribe", "params": streams}))
                backoff = 0.25
                async for raw in ws:
                    receive_ms = int(time.time() * 1000)
                    payload = json.loads(raw)
                    if payload.get("status") is not None:
                        if int(payload.get("status")) >= 400:
                            raise RuntimeError(f"Gemini spot WS subscription error: {payload}")
                        continue
                    symbol = str(payload.get("s") or "").lower()
                    asset = SYMBOL_ASSET.get(symbol)
                    if asset is None:
                        continue
                    bid, ask = _dec(payload.get("b")), _dec(payload.get("a"))
                    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
                        continue
                    event_ns = payload.get("E")
                    try:
                        event_ns_i = int(event_ns) if event_ns is not None else None
                    except (TypeError, ValueError):
                        event_ns_i = None
                    states[asset].update(
                        bid=bid, ask=ask, receive_ms=receive_ms, exchange_event_ns=event_ns_i
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("GEMINI_SPOT_PROXY_RECONNECT type=%s error=%s", type(exc).__name__, str(exc)[:240])
            await asyncio.sleep(backoff)
            backoff = min(5.0, backoff * 2)


def _event_strike(event: dict[str, Any]) -> Decimal | None:
    contract = _gemini_up_contract(event)
    if contract is None:
        return None
    strike_obj = contract.get("strike") or {}
    return _dec(strike_obj.get("value") if isinstance(strike_obj, dict) else None)


async def fetch_window_events(http: httpx.AsyncClient, window_end: datetime, *, timeout_s: float = 8.0) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_s
    while True:
        events = await _gemini_active_crypto(http)
        out: dict[str, dict[str, Any]] = {}
        for event in events:
            meta = _gemini_meta(event)
            if meta is None:
                continue
            asset, duration, end = meta
            if duration == 15 and asset in ASSET_SYMBOL and abs((end - window_end).total_seconds()) < 1:
                if _event_strike(event) is not None:
                    out[asset] = event
        if len(out) == len(ASSET_SYMBOL) or time.monotonic() >= deadline:
            return out
        await asyncio.sleep(0.5)


def snapshot_payload(
    *, asset: str, state: SpotState, event: dict[str, Any], window_end: datetime,
    target_seconds_to_end: float,
) -> dict[str, Any] | None:
    strike = _event_strike(event)
    if strike is None or strike == 0 or state.mid is None or state.receive_ms is None:
        return None
    now_ms = int(time.time() * 1000)
    twap60, span_ms = state.twap(now_ms=now_ms, window_ms=60_000)
    mid_margin = (state.mid - strike) / strike * D("10000")
    twap_margin = ((twap60 - strike) / strike * D("10000")) if twap60 is not None else None
    contract = _gemini_up_contract(event) or {}
    strike_obj = contract.get("strike") or {}
    return {
        "asset": asset,
        "window_end_utc": window_end.isoformat(),
        "event_ticker": event.get("ticker"),
        "prediction_symbol": contract.get("instrumentSymbol"),
        "kaiko_source": (event.get("sourceDetails") or event.get("source_details")),
        "strike": str(strike),
        "strike_available_at": strike_obj.get("availableAt") if isinstance(strike_obj, dict) else None,
        "target_seconds_to_end": target_seconds_to_end,
        "actual_seconds_to_end": round(window_end.timestamp() - time.time(), 4),
        "spot_symbol": ASSET_SYMBOL[asset],
        "spot_bid": str(state.bid),
        "spot_ask": str(state.ask),
        "spot_mid": str(state.mid),
        "spot_mid_margin_bps": str(mid_margin),
        "spot_60s_twap": str(twap60) if twap60 is not None else None,
        "spot_60s_twap_margin_bps": str(twap_margin) if twap_margin is not None else None,
        "twap_observed_span_ms": span_ms,
        "spot_quote_receive_ms": state.receive_ms,
        "spot_quote_age_ms": now_ms - state.receive_ms,
        "spot_exchange_event_ns": state.exchange_event_ns,
        "proxy_only": True,
        "official_live_kaiko": False,
        "read_only": True,
        "order_entry": False,
    }


async def settle_and_calibrate(
    http: httpx.AsyncClient,
    *, window_end: datetime,
    snapshots: dict[str, list[dict[str, Any]]],
    timeout_s: float = 90.0,
) -> None:
    await asyncio.sleep(max(0.0, window_end.timestamp() + 2.0 - time.time()))
    deadline = time.monotonic() + timeout_s
    settled_by_asset: dict[str, tuple[float, float | None]] = {}
    while time.monotonic() < deadline and len(settled_by_asset) < len(ASSET_SYMBOL):
        try:
            events = await _gemini_settled(http, 500)
        except Exception as exc:
            logger.warning("GEMINI_PROXY_SETTLEMENT_FETCH_ERROR type=%s error=%s", type(exc).__name__, str(exc)[:200])
            await asyncio.sleep(5.0)
            continue
        for event in events:
            meta = _gemini_meta(event)
            if meta is None:
                continue
            asset, duration, end = meta
            if duration != 15 or asset not in ASSET_SYMBOL or abs((end - window_end).total_seconds()) >= 1:
                continue
            _outcome, _strike, settlement, final_margin_bps = _gemini_outcome(event)
            if settlement is not None:
                settled_by_asset[asset] = (settlement, final_margin_bps)
        if len(settled_by_asset) < len(ASSET_SYMBOL):
            await asyncio.sleep(5.0)

    for asset, rows in snapshots.items():
        settled = settled_by_asset.get(asset)
        if settled is None:
            logger.info("GEMINI_KAIKO_PROXY_UNSETTLED %s", json.dumps({
                "asset": asset, "window_end_utc": window_end.isoformat(), "snapshots": len(rows),
                "read_only": True, "order_entry": False,
            }, separators=(",", ":")))
            continue
        settlement, final_margin_bps = settled
        settlement_d = D(str(settlement))
        for row in rows:
            mid = _dec(row.get("spot_mid"))
            twap = _dec(row.get("spot_60s_twap"))
            mid_err = ((mid - settlement_d) / settlement_d * D("10000")) if mid is not None and settlement_d != 0 else None
            twap_err = ((twap - settlement_d) / settlement_d * D("10000")) if twap is not None and settlement_d != 0 else None
            payload = {
                **row,
                "kaiko_settlement": str(settlement_d),
                "kaiko_final_margin_bps": final_margin_bps,
                "spot_mid_error_vs_kaiko_bps": str(mid_err) if mid_err is not None else None,
                "spot_60s_twap_error_vs_kaiko_bps": str(twap_err) if twap_err is not None else None,
                "calibration_complete": True,
            }
            logger.info("GEMINI_KAIKO_PROXY_CALIBRATION %s", json.dumps(payload, separators=(",", ":"), default=str))


async def run(offsets_s: list[float]) -> None:
    states = {asset: SpotState(asset) for asset in ASSET_SYMBOL}
    spot_task = asyncio.create_task(spot_stream(states), name="gemini-spot-proxy")
    settlement_tasks: set[asyncio.Task] = set()
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            while True:
                window_end = _next_window_end()
                events = await fetch_window_events(http, window_end)
                logger.info("GEMINI_KAIKO_PROXY_WINDOW %s", json.dumps({
                    "window_end_utc": window_end.isoformat(),
                    "assets_with_strike": sorted(events),
                    "offsets_s": offsets_s,
                    "official_live_kaiko": False,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")))
                snapshots: dict[str, list[dict[str, Any]]] = {a: [] for a in ASSET_SYMBOL}

                for offset_s in sorted(offsets_s, reverse=True):
                    target = window_end.timestamp() - offset_s
                    if target <= time.time():
                        continue
                    await asyncio.sleep(max(0.0, target - time.time()))
                    for asset, event in events.items():
                        row = snapshot_payload(
                            asset=asset,
                            state=states[asset],
                            event=event,
                            window_end=window_end,
                            target_seconds_to_end=offset_s,
                        )
                        if row is None:
                            continue
                        snapshots[asset].append(row)
                        logger.info("GEMINI_KAIKO_PROXY_SNAPSHOT %s", json.dumps(row, separators=(",", ":"), default=str))

                # Start the settlement join in parallel; do not delay metadata setup
                # and spot capture for the next 15-minute window.
                task = asyncio.create_task(
                    settle_and_calibrate(http, window_end=window_end, snapshots=snapshots),
                    name=f"kaiko-calibrate-{int(window_end.timestamp())}",
                )
                settlement_tasks.add(task)
                task.add_done_callback(settlement_tasks.discard)
                await asyncio.sleep(max(0.0, window_end.timestamp() + 0.25 - time.time()))
    finally:
        spot_task.cancel()
        await asyncio.gather(spot_task, return_exceptions=True)
        for task in settlement_tasks:
            task.cancel()
        await asyncio.gather(*settlement_tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--offsets-s", nargs="+", type=float,
        default=[300.0, 60.0, 30.0, 10.0, 5.0, 2.0, 1.0, 0.5, 0.2],
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.offsets_s))


if __name__ == "__main__":
    main()
