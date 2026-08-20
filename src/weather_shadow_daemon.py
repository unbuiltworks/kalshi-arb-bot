"""Continuous read-only weather shadow scanner for Railway.

No authentication or order-entry imports are allowed here. The daemon polls public
Kalshi weather markets and emits normalized JSON shadow rows for observation.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone

from src.weather_shadow import WEATHER_SERIES, scan_weather


def run_forever() -> None:
    interval_s = max(30, int(os.getenv("WEATHER_SHADOW_INTERVAL_S", "60")))
    series_raw = os.getenv("WEATHER_SHADOW_SERIES", "").strip()
    series = [s.strip() for s in series_raw.split(",") if s.strip()] or list(WEATHER_SERIES)

    print(json.dumps({
        "event": "WEATHER_SHADOW_START",
        "classification": "SHADOW",
        "order_entry": False,
        "auth": False,
        "series": series,
        "interval_s": interval_s,
        "ts": datetime.now(timezone.utc).isoformat(),
    }, sort_keys=True), flush=True)

    while True:
        started = time.monotonic()
        try:
            rows = scan_weather(series)
            for row in rows:
                print(json.dumps({"event": "WEATHER_SHADOW_ROW", **asdict(row)}, sort_keys=True), flush=True)
            print(json.dumps({
                "event": "WEATHER_SHADOW_SUMMARY",
                "rows": len(rows),
                "settlement_status": "SETTLEMENT_AUDIT_REQUIRED",
                "classification": "SHADOW",
                "order_entry": False,
                "ts": datetime.now(timezone.utc).isoformat(),
            }, sort_keys=True), flush=True)
        except Exception as exc:  # keep observing; never mutate anything on error
            print(json.dumps({
                "event": "WEATHER_SHADOW_ERROR",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "order_entry": False,
                "ts": datetime.now(timezone.utc).isoformat(),
            }, sort_keys=True), flush=True)

        elapsed = time.monotonic() - started
        time.sleep(max(1.0, interval_s - elapsed))


if __name__ == "__main__":
    run_forever()
