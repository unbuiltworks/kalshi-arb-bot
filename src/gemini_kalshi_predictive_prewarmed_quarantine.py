"""Startup-snapshot quarantine for the continuous prewarmed HOT scanner.

A process that starts mid-window receives fresh venue snapshots almost immediately.
Those snapshots can be close in local receive time without proving the displayed
cross-venue prices coexisted long enough to execute. Quarantine only the initial
active feed for 500 ms. Predictively prewarmed rollover feeds are unaffected because
they have already been running before activation.
"""
from __future__ import annotations

import json
import time

from src import gemini_kalshi_predictive_prewarmed_safe as safe

base = safe.base
hot = safe.hot
_STARTUP_QUARANTINE_MS = 500
_original_start_feed = base._start_feed
_original_evaluate = safe._evaluate_event_sequential


async def _start_feed_quarantined(*, asset, pair, stats, active):
    feed = await _original_start_feed(asset=asset, pair=pair, stats=stats, active=active)
    now_ms = int(time.time() * 1000)
    feed.evaluation_eligible_ms = now_ms + _STARTUP_QUARANTINE_MS if active else 0
    if active:
        base.logger.info(
            "CRYPTO_HOT_STARTUP_QUARANTINE %s",
            json.dumps({
                "asset": asset,
                "until_ms": feed.evaluation_eligible_ms,
                "duration_ms": _STARTUP_QUARANTINE_MS,
                "window_end_utc": pair.window_end.isoformat(),
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")),
        )
    return feed


def _evaluate_event_quarantined(**kwargs):
    feed = kwargs["feed"]
    event = kwargs["event"]
    venue, receive_ms, exchange_ms, top, _trigger_mono_ns = event
    eligible_ms = int(getattr(feed, "evaluation_eligible_ms", 0) or 0)
    if receive_ms < eligible_ms:
        # Preserve exact event-order state even while economics output is suppressed.
        if venue == "gemini":
            feed.eval_gemini = top
        else:
            feed.eval_kalshi = top
            feed.eval_latest_k_exchange_ts = exchange_ms
        return
    return _original_evaluate(**kwargs)


base._start_feed = _start_feed_quarantined
base._evaluate_event = _evaluate_event_quarantined


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
