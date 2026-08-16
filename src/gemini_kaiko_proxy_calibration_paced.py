"""Paced launcher for live Gemini->Kaiko proxy calibration.

Gemini can publish the next 15-minute contract before its Kaiko strike value is
populated. The base collector originally gave up after 8 seconds and then skipped the
whole window. This wrapper keeps polling slowly until the strike appears, while
remaining Gemini-only and read-only.
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
        # Metadata is cold-path control data. Two seconds is fast enough to capture
        # a strike long before the earliest T-300 snapshot without hammering Gemini.
        await asyncio.sleep(2.0)


base.fetch_window_events = fetch_window_events_paced


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
