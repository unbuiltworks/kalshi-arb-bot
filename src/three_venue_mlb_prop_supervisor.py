"""Persistent read-only supervisor for the fast strict three-venue MLB prop scan."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

from src.three_venue_mlb_prop_safe_scan import run_once

logger = logging.getLogger("kalshi_arb.three_venue_mlb_prop_supervisor")


def _append(path: str, payload: dict[str, Any]) -> None:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str) + "\n")
        handle.flush(); os.fsync(handle.fileno())


async def supervise(interval_seconds: float, journal_path: str | None, once: bool) -> None:
    if interval_seconds < 2:
        raise ValueError("interval_seconds must be >= 2")
    cycle = 0
    while True:
        cycle += 1; started = time.monotonic()
        try:
            result = await run_once()
            envelope = {"ts_utc":datetime.now(timezone.utc).isoformat(),"cycle":cycle,"ok":True,"result":result}
        except Exception as exc:
            envelope = {"ts_utc":datetime.now(timezone.utc).isoformat(),"cycle":cycle,"ok":False,
                        "error":f"{type(exc).__name__}:{str(exc)[:500]}","read_only":True,"order_entry":False}
        envelope["supervisor_cycle_ms"] = round((time.monotonic()-started)*1000, 3)
        logger.info("THREE_VENUE_MLB_PROP_CYCLE %s", json.dumps(envelope,separators=(",",":"),sort_keys=True,default=str))
        if journal_path: _append(journal_path, envelope)
        if once: return
        await asyncio.sleep(max(0.25, interval_seconds - (time.monotonic()-started)))


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--interval-seconds",type=float,default=10.0)
    parser.add_argument("--journal-path",default=""); parser.add_argument("--once",action="store_true")
    args=parser.parse_args(); logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(supervise(args.interval_seconds,args.journal_path or None,args.once))


if __name__ == "__main__": main()
