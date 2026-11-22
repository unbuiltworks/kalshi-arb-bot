"""Conservative public Gemini Predictions fair-value feature shadow.

Read-only. This is a research feature generator, not an execution signal.
Compared with v1 it avoids treating one-second changes in a smoothed 60-second
settlement index as the full terminal uncertainty. It combines the original
consecutive-return volatility with a lagged RMS volatility estimate and reports
its calibration state explicitly.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import time
from typing import Any

import httpx

from src.gemini_prediction_fair_value_shadow import (
    IndexState,
    SourceKey,
    fetch_contract_metadata,
    model_up_probability,
    rolling_sigma_per_s,
    stream_index,
)
from src.gemini_prediction_maker_shadow import _emit, discover_events
from src.gemini_prediction_maker_shadow_v2 import live_events


def lagged_rms_sigma_per_s(
    observations,
    *,
    lookback_seconds: float = 300.0,
    lag_seconds: float = 30.0,
) -> tuple[float | None, int]:
    """RMS log-return volatility using lagged observations.

    The Gemini settlement index is a rolling/smoothed reference. Consecutive
    one-second updates can therefore understate uncertainty. This estimator uses
    approximately `lag_seconds` changes and intentionally does not demean them,
    so sustained short-window movement contributes to the risk estimate.
    Overlapping returns make this a conservative research feature, not an
    independently-sampled statistical estimator.
    """
    if len(observations) < 12:
        return None, 0
    end_ms = observations[-1][0]
    cutoff_ms = end_ms - int((lookback_seconds + lag_seconds) * 1000)
    rows = [(ts, px) for ts, px in observations if ts >= cutoff_ms and px > 0]
    if len(rows) < 12:
        return None, 0

    lag_ms = max(1000, int(lag_seconds * 1000))
    lookback_cutoff = end_ms - int(lookback_seconds * 1000)
    vals: list[float] = []
    j = 0
    for i in range(1, len(rows)):
        ti, pi = rows[i]
        if ti < lookback_cutoff:
            continue
        target = ti - lag_ms
        while j + 1 < i and rows[j + 1][0] <= target:
            j += 1
        tj, pj = rows[j]
        dt = (ti - tj) / 1000.0
        if dt < lag_seconds * 0.75 or dt > lag_seconds * 1.75 or pj <= 0:
            continue
        r = math.log(pi / pj)
        vals.append(r / math.sqrt(dt))

    if len(vals) < 12:
        return None, len(vals)
    rms = math.sqrt(sum(x * x for x in vals) / len(vals))
    return (rms if rms > 0 else None), len(vals)


def conservative_sigma(observations, *, lookback_seconds: float, lag_seconds: float) -> tuple[float | None, dict[str, Any]]:
    consecutive, consecutive_n = rolling_sigma_per_s(observations, lookback_seconds=lookback_seconds)
    lagged, lagged_n = lagged_rms_sigma_per_s(
        observations,
        lookback_seconds=lookback_seconds,
        lag_seconds=lag_seconds,
    )
    usable = [x for x in (consecutive, lagged) if x is not None and x > 0]
    sigma = max(usable) if usable else None
    return sigma, {
        "sigma_consecutive": consecutive,
        "sigma_consecutive_samples": consecutive_n,
        "sigma_lagged_rms": lagged,
        "sigma_lagged_samples": lagged_n,
        "sigma_used": sigma,
        "sigma_method": "max(consecutive_std,lagged_rms)",
    }


async def run(*, discovery_seconds: float, summary_seconds: float, vol_lookback_seconds: float, lag_seconds: float) -> None:
    index_states: dict[SourceKey, IndexState] = {}
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-fair-value-shadow-v2/1.0"}) as http:
        while True:
            try:
                discovered = await discover_events(http)
                current = live_events(discovered)
                metas_raw = await asyncio.gather(*(fetch_contract_metadata(http, event) for event in current.values()))
                contracts = [m for m in metas_raw if m is not None]
                for meta in contracts:
                    index_states.setdefault(meta.source, IndexState(source=meta.source))

                _emit({
                    "type": "GEMINI_FAIR_VALUE_V2_DISCOVERY",
                    "contracts": len(contracts),
                    "streams": sorted({m.stream for m in contracts}),
                    "index_streams": sorted({m.source.stream for m in contracts}),
                    "strike_ready": sum(m.strike is not None for m in contracts),
                    "calibration_state": "UNVALIDATED_FEATURE_ONLY",
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
                        sigma, sigma_meta = conservative_sigma(
                            state.observations,
                            lookback_seconds=vol_lookback_seconds,
                            lag_seconds=lag_seconds,
                        )
                        spot = None if last is None else last[1]
                        last_ms = None if last is None else last[0]
                        age_ms = None if last_ms is None else max(0, now_ms - last_ms)
                        seconds_remaining = max(0.0, (meta.expiry_ms - now_ms) / 1000.0)
                        raw_fair = None
                        if spot is not None and meta.strike is not None and sigma is not None and age_ms is not None and age_ms <= 10000:
                            raw_fair = model_up_probability(
                                spot=spot,
                                strike=meta.strike,
                                seconds_remaining=seconds_remaining,
                                sigma_per_s=sigma,
                            )
                        rows[meta.stream] = {
                            "event_ticker": meta.event_ticker,
                            "symbol": meta.symbol,
                            "source_stream": meta.source.stream,
                            "strike": meta.strike,
                            "strike_available_at_ms": meta.strike_available_at_ms,
                            "index_price": spot,
                            "index_age_ms": age_ms,
                            "seconds_remaining": seconds_remaining,
                            **sigma_meta,
                            "model_fair_up": raw_fair,
                            "model_ready": raw_fair is not None,
                            "actionable": False,
                            "calibration_state": "UNVALIDATED_FEATURE_ONLY",
                            "index_messages": state.messages,
                            "index_parse_errors": state.parse_errors,
                        }
                    _emit({
                        "type": "GEMINI_FAIR_VALUE_V2_SHADOW_SUMMARY",
                        "by_stream": rows,
                        "model_ready_streams": sum(bool(x["model_ready"]) for x in rows.values()),
                        "actionable_streams": 0,
                        "streams": sorted(rows),
                        "calibration_state": "UNVALIDATED_FEATURE_ONLY",
                        "interpretation": "Feature-only fair probability. Uses current/past settlement-index data with conservative lagged volatility; must be empirically calibrated on resolved windows before quote skew or live use.",
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
                    "type": "GEMINI_FAIR_VALUE_V2_SHADOW_ERROR",
                    "error": type(exc).__name__,
                    "message": str(exc)[:500],
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                await asyncio.sleep(1.0)


def main() -> None:
    p = argparse.ArgumentParser(description="Conservative public Gemini settlement-index fair-value feature shadow")
    p.add_argument("--discovery-seconds", type=float, default=30.0)
    p.add_argument("--summary-seconds", type=float, default=5.0)
    p.add_argument("--vol-lookback-seconds", type=float, default=300.0)
    p.add_argument("--lag-seconds", type=float, default=30.0)
    args = p.parse_args()
    asyncio.run(run(
        discovery_seconds=max(10.0, args.discovery_seconds),
        summary_seconds=max(2.0, args.summary_seconds),
        vol_lookback_seconds=max(60.0, args.vol_lookback_seconds),
        lag_seconds=max(10.0, args.lag_seconds),
    ))


if __name__ == "__main__":
    main()
