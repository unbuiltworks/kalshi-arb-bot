"""Public Gemini Prediction Markets settlement-index/fair-value shadow.

Read-only. Uses public event/strike REST endpoints and public indexPrice WebSocket
streams. No credentials, account access, signing, order placement, or cancellation.

The model is deliberately labeled MODEL_FAIR_VALUE: it estimates the probability
that the settlement index finishes above the captured strike using only index
observations available up to the current timestamp and a rolling realized-volatility
estimate. It is research signal, not an arbitrage guarantee.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import time
from typing import Any

import httpx
import websockets

from src.gemini_prediction_maker_shadow import REST, WS, _emit, discover_events
from src.gemini_prediction_maker_shadow_v2 import live_events


@dataclass(frozen=True)
class SourceKey:
    agency: str
    index: str

    @property
    def stream(self) -> str:
        return f"{self.agency}:{self.index}@indexPrice"


@dataclass
class IndexState:
    source: SourceKey
    observations: deque[tuple[int, float]] = field(default_factory=lambda: deque(maxlen=6000))
    messages: int = 0
    parse_errors: int = 0
    last_schema: tuple[str, ...] | None = None

    @property
    def last(self) -> tuple[int, float] | None:
        return self.observations[-1] if self.observations else None


@dataclass(frozen=True)
class LiveContract:
    stream: str
    event_ticker: str
    symbol: str
    asset: str
    horizon_minutes: int
    expiry_ms: int
    source: SourceKey
    strike: float | None
    strike_available_at_ms: int | None


def _f(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


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


def extract_price(payload: Any) -> float | None:
    """Best-effort parser for Gemini's documented third-party price frame.

    Only documented/obvious price field names are accepted. Unknown schemas are
    logged and treated as unmeasurable rather than guessed.
    """
    if not isinstance(payload, dict):
        return None
    for key in ("price", "indexPrice", "index_price", "value", "p"):
        value = _f(payload.get(key))
        if value is not None and value > 0:
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("price", "indexPrice", "index_price", "value", "p"):
            value = _f(data.get(key))
            if value is not None and value > 0:
                return value
    return None


def extract_timestamp_ms(payload: Any) -> int:
    now_ms = int(time.time() * 1000)
    if not isinstance(payload, dict):
        return now_ms
    for key in ("E", "timestamp", "ts", "t"):
        value = _f(payload.get(key))
        if value is None or value <= 0:
            continue
        # Gemini core market-data E is nanoseconds; other feeds may use ms/us/s.
        if value > 1e17:
            return int(value / 1_000_000)
        if value > 1e14:
            return int(value / 1_000)
        if value > 1e11:
            return int(value)
        if value > 1e9:
            return int(value * 1000)
    return now_ms


def rolling_sigma_per_s(observations: deque[tuple[int, float]], *, lookback_seconds: float = 300.0) -> tuple[float | None, int]:
    """Realized log-return sigma normalized to one second, no future data."""
    if len(observations) < 8:
        return None, 0
    cutoff = observations[-1][0] - int(lookback_seconds * 1000)
    rows = [(ts, px) for ts, px in observations if ts >= cutoff and px > 0]
    if len(rows) < 8:
        return None, 0
    normalized: list[float] = []
    for (t0, p0), (t1, p1) in zip(rows, rows[1:]):
        dt = (t1 - t0) / 1000.0
        if dt <= 0 or p0 <= 0 or p1 <= 0:
            continue
        r = math.log(p1 / p0)
        normalized.append(r / math.sqrt(dt))
    if len(normalized) < 6:
        return None, len(normalized)
    mean = sum(normalized) / len(normalized)
    var = sum((x - mean) ** 2 for x in normalized) / max(1, len(normalized) - 1)
    sigma = math.sqrt(max(0.0, var))
    return (sigma if sigma > 0 else None), len(normalized)


def model_up_probability(*, spot: float, strike: float, seconds_remaining: float, sigma_per_s: float) -> float | None:
    if spot <= 0 or strike <= 0 or seconds_remaining <= 0 or sigma_per_s <= 0:
        return None
    scale = sigma_per_s * math.sqrt(seconds_remaining)
    if scale <= 1e-12:
        return 1.0 if spot > strike else 0.0
    z = math.log(spot / strike) / scale
    # Phi(z) via erf. Drift intentionally set to zero for short windows.
    return max(0.0, min(1.0, 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))))


async def fetch_contract_metadata(http: httpx.AsyncClient, event) -> LiveContract | None:
    if event.expiry_ms is None:
        return None
    try:
        detail_response, strike_response = await asyncio.gather(
            http.get(f"{REST}/v1/prediction-markets/events/{event.event_ticker}"),
            http.get(f"{REST}/v1/prediction-markets/events/{event.event_ticker}/strike"),
        )
        detail_response.raise_for_status()
        detail = detail_response.json()
        strike_payload = strike_response.json() if strike_response.status_code == 200 else {}
    except Exception:
        return None
    if not isinstance(detail, dict):
        return None
    source = detail.get("sourceDetails") or {}
    if not isinstance(source, dict):
        return None
    agency = str(source.get("agency") or "").strip()
    index = str(source.get("index") or "").strip()
    if not agency or not index:
        return None
    strike = _f(strike_payload.get("value")) if isinstance(strike_payload, dict) else None
    available = _iso_ms(strike_payload.get("availableAt")) if isinstance(strike_payload, dict) else None
    return LiveContract(
        stream=event.stream,
        event_ticker=event.event_ticker,
        symbol=event.instrument_symbol,
        asset=event.asset,
        horizon_minutes=event.horizon_minutes,
        expiry_ms=event.expiry_ms,
        source=SourceKey(agency=agency, index=index),
        strike=strike,
        strike_available_at_ms=available,
    )


async def stream_index(state: IndexState, stop_at: float) -> None:
    async with websockets.connect(
        WS,
        open_timeout=8,
        ping_interval=15,
        ping_timeout=8,
        max_queue=10000,
        max_size=2**20,
    ) as ws:
        await ws.send(json.dumps({"id": f"index-{state.source.index}", "method": "SUBSCRIBE", "params": [state.source.stream]}))
        while time.time() < stop_at:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, min(1.0, stop_at - time.time())))
            except asyncio.TimeoutError:
                continue
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            # Ignore subscribe acknowledgements.
            if payload.get("status") is not None and payload.get("id") is not None:
                continue
            state.messages += 1
            schema = tuple(sorted(str(k) for k in payload.keys()))
            if state.last_schema != schema:
                state.last_schema = schema
                _emit({
                    "type": "GEMINI_INDEX_SCHEMA",
                    "source_stream": state.source.stream,
                    "keys": list(schema),
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
            price = extract_price(payload)
            if price is None:
                state.parse_errors += 1
                continue
            ts_ms = extract_timestamp_ms(payload)
            # Enforce monotone observation time so no out-of-order future sample
            # can be used by a prior decision point.
            if state.observations and ts_ms < state.observations[-1][0]:
                continue
            state.observations.append((ts_ms, price))


async def run(*, discovery_seconds: float, summary_seconds: float, vol_lookback_seconds: float) -> None:
    index_states: dict[SourceKey, IndexState] = {}
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-fair-value-shadow/1.0"}) as http:
        while True:
            try:
                discovered = await discover_events(http)
                current = live_events(discovered)
                metas_raw = await asyncio.gather(*(fetch_contract_metadata(http, event) for event in current.values()))
                contracts = [m for m in metas_raw if m is not None]
                for meta in contracts:
                    index_states.setdefault(meta.source, IndexState(source=meta.source))

                _emit({
                    "type": "GEMINI_FAIR_VALUE_DISCOVERY",
                    "contracts": len(contracts),
                    "streams": sorted({m.stream for m in contracts}),
                    "index_streams": sorted({m.source.stream for m in contracts}),
                    "strike_ready": sum(m.strike is not None for m in contracts),
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                if not contracts:
                    await asyncio.sleep(2.0)
                    continue

                started = time.time()
                stop_at = started + discovery_seconds
                sources = sorted({m.source for m in contracts}, key=lambda x: x.stream)
                workers = [asyncio.create_task(stream_index(index_states[source], stop_at)) for source in sources]
                next_summary = started + summary_seconds

                while time.time() < stop_at:
                    await asyncio.sleep(max(0.05, min(0.5, next_summary - time.time(), stop_at - time.time())))
                    now = time.time()
                    if now < next_summary:
                        continue
                    now_ms = int(now * 1000)
                    rows: dict[str, dict[str, Any]] = {}
                    for meta in contracts:
                        state = index_states[meta.source]
                        last = state.last
                        sigma, vol_samples = rolling_sigma_per_s(state.observations, lookback_seconds=vol_lookback_seconds)
                        spot = None if last is None else last[1]
                        last_ms = None if last is None else last[0]
                        seconds_remaining = max(0.0, (meta.expiry_ms - now_ms) / 1000.0)
                        fair = None
                        if spot is not None and meta.strike is not None and sigma is not None:
                            fair = model_up_probability(spot=spot, strike=meta.strike, seconds_remaining=seconds_remaining, sigma_per_s=sigma)
                        rows[meta.stream] = {
                            "event_ticker": meta.event_ticker,
                            "symbol": meta.symbol,
                            "source_stream": meta.source.stream,
                            "strike": meta.strike,
                            "strike_available_at_ms": meta.strike_available_at_ms,
                            "index_price": spot,
                            "index_age_ms": None if last_ms is None else max(0, now_ms - last_ms),
                            "seconds_remaining": seconds_remaining,
                            "sigma_per_s": sigma,
                            "vol_samples": vol_samples,
                            "model_fair_up": fair,
                            "model_ready": fair is not None,
                            "index_messages": state.messages,
                            "index_parse_errors": state.parse_errors,
                        }
                    _emit({
                        "type": "GEMINI_FAIR_VALUE_SHADOW_SUMMARY",
                        "by_stream": rows,
                        "model_ready_streams": sum(bool(x["model_ready"]) for x in rows.values()),
                        "streams": sorted(rows),
                        "interpretation": "MODEL_FAIR_VALUE uses only current/past Gemini settlement-index observations and rolling realized volatility; it is not a guaranteed arbitrage value.",
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })
                    next_summary = now + summary_seconds

                results = await asyncio.gather(*workers, return_exceptions=True)
                errors = [r for r in results if isinstance(r, BaseException)]
                if errors:
                    raise RuntimeError(f"{len(errors)} Gemini index workers failed; first={errors[0]!r}")
            except Exception as exc:
                _emit({
                    "type": "GEMINI_FAIR_VALUE_SHADOW_ERROR",
                    "error": type(exc).__name__,
                    "message": str(exc)[:500],
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                await asyncio.sleep(1.0)


def main() -> None:
    p = argparse.ArgumentParser(description="Public Gemini settlement-index fair-value shadow")
    p.add_argument("--discovery-seconds", type=float, default=30.0)
    p.add_argument("--summary-seconds", type=float, default=5.0)
    p.add_argument("--vol-lookback-seconds", type=float, default=300.0)
    args = p.parse_args()
    asyncio.run(run(
        discovery_seconds=max(10.0, args.discovery_seconds),
        summary_seconds=max(2.0, args.summary_seconds),
        vol_lookback_seconds=max(30.0, args.vol_lookback_seconds),
    ))


if __name__ == "__main__":
    main()
