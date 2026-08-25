"""Continuous zero-order-entry same-Gemini locked-edge census.

This module intentionally reuses the existing complete-pair q1 discovery path. It
collects broad >=1c/2c/3c after-fee snapshots continuously while all live/maker arms
are disabled. The broad snapshot uses REST books and is therefore NOT used to infer
sub-second opportunity lifetime; a WS lifetime/public-print layer is tracked as a
separate instrumentation requirement.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from src import gemini_same_venue_crypto_full_pair_taker as taker


async def run_forever(interval_seconds: float) -> None:
    cycle = 0
    while True:
        cycle += 1
        started = time.monotonic()
        try:
            _candidate, diag = await taker.discover()
            elapsed_ms = round((time.monotonic() - started) * 1000, 3)
            print("GEMINI_EDGE_CENSUS_HEARTBEAT " + json.dumps({
                "cycle": cycle,
                "snapshot_ts_ms": diag.get("snapshot_ts_ms"),
                "scan_elapsed_ms": elapsed_ms,
                "taker_locked_1c_candidates": diag.get("taker_locked_1c_candidates", 0),
                "taker_locked_2c_candidates": diag.get("taker_locked_2c_candidates", 0),
                "taker_locked_3c_candidates": diag.get("taker_locked_3c_candidates", 0),
                "books_requested": diag.get("books_requested", 0),
                "books_ok": diag.get("books_ok", 0),
                "lifetime_measurement": "NOT_FROM_REST_SNAPSHOT",
                "public_print_proxy": "PENDING_WS_INSTRUMENTATION",
                "read_only": True,
                "order_entry": False,
                "realized": False,
            }, separators=(",", ":"), default=str), flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print("GEMINI_EDGE_CENSUS_ERROR " + json.dumps({
                "cycle": cycle,
                "error_type": type(exc).__name__,
                "error": str(exc)[:220],
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, interval_seconds - elapsed))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    args = parser.parse_args()
    asyncio.run(run_forever(max(5.0, args.interval_seconds)))


if __name__ == "__main__":
    main()
