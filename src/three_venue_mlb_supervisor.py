"""Persistent read-only supervisor for the scope-safe three-venue MLB scanner.

This process deliberately has no authenticated clients and no order-entry path. It
re-runs the scope-safe identity + q=1 pricing scan on a short cadence, journals each
cycle when a path is supplied, and keeps going through transient API failures.
"""
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

from src.three_venue_mlb_safe_scan import run_once

logger = logging.getLogger("kalshi_arb.three_venue_mlb_supervisor")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str) + "\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


async def supervise(*, interval_seconds: float, journal_path: str | None, once: bool = False) -> None:
    if interval_seconds < 1.0:
        raise ValueError("interval_seconds must be >= 1")

    cycle = 0
    while True:
        cycle += 1
        started = time.monotonic()
        envelope: dict[str, Any]
        try:
            result = await run_once()
            envelope = {
                "ts_utc": _utc_now(),
                "cycle": cycle,
                "ok": True,
                "result": result,
            }
        except Exception as exc:  # supervisor must survive transient venue/API faults
            envelope = {
                "ts_utc": _utc_now(),
                "cycle": cycle,
                "ok": False,
                "error": f"{type(exc).__name__}:{str(exc)[:500]}",
                "read_only": True,
                "order_entry": False,
            }

        envelope["cycle_duration_ms"] = round((time.monotonic() - started) * 1000, 3)
        logger.info(
            "THREE_VENUE_MLB_SUPERVISOR_CYCLE %s",
            json.dumps(envelope, separators=(",", ":"), sort_keys=True, default=str),
        )
        if journal_path:
            _append_jsonl(journal_path, envelope)

        if once:
            return

        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.25, interval_seconds - elapsed))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent read-only Kalshi/Gemini/Polymarket MLB scanner")
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--journal-path", default="")
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    args = _parser().parse_args()
    asyncio.run(
        supervise(
            interval_seconds=args.interval_seconds,
            journal_path=args.journal_path or None,
            once=args.once,
        )
    )


if __name__ == "__main__":
    main()
