"""Paced launcher for live Gemini->Kaiko proxy calibration.

Gemini can publish the next 15-minute contract before its Kaiko strike value is
populated, and settled prediction metadata can lag the measurement endpoint by more
than 90 seconds. This wrapper therefore:

- polls active metadata slowly until all four Kaiko strikes appear;
- keeps the post-close settlement join alive for up to 10 minutes.

It remains Gemini-only and read-only.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

import httpx

from src import gemini_kaiko_proxy_calibration as base
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto, _gemini_meta


async def fetch_window_events_paced(
    http: httpx.AsyncClient,
    window_end: datetime,
    *,
    timeout_s: float = 180.0,
) -> dict[str, dict]:
    deadline = time.monotonic() + max(8.0, timeout_s)
    attempt = 0
    while True:
        attempt += 1
        events = await _gemini_active_crypto(http)
        out: dict[str, dict] = {}
        for event in events:
            meta = _gemini_meta(event)
            if meta is None:
                continue
            asset, duration, end = meta
            if (
                duration == 15
                and asset in base.ASSET_SYMBOL
                and abs((end - window_end).total_seconds()) < 1
                and base._event_strike(event) is not None
            ):
                out[asset] = event
        if len(out) == len(base.ASSET_SYMBOL):
            base.logger.info(
                "GEMINI_KAIKO_STRIKES_READY window_end=%s attempts=%d assets=%s",
                window_end.isoformat(), attempt, sorted(out),
            )
            return out
        if time.monotonic() >= deadline:
            base.logger.warning(
                "GEMINI_KAIKO_STRIKE_WAIT_TIMEOUT window_end=%s attempts=%d assets=%s",
                window_end.isoformat(), attempt, sorted(out),
            )
            return out
        # Cold-path control data: two seconds is fast enough without hammering Gemini.
        await asyncio.sleep(2.0)


_original_settle_and_calibrate = base.settle_and_calibrate


async def settle_and_calibrate_long(http, *, window_end, snapshots, timeout_s: float = 600.0):
    return await _original_settle_and_calibrate(
        http,
        window_end=window_end,
        snapshots=snapshots,
        timeout_s=max(600.0, timeout_s),
    )


base.fetch_window_events = fetch_window_events_paced
base.settle_and_calibrate = settle_and_calibrate_long


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
