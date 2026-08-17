"""Persistent read-only supervisor for MLB game-line cross-venue research.

Runs independently from the existing MLB prop and crypto collectors.  It uses public
market data only, fsyncs each snapshot to its own journal, and contains no order-entry
or authentication path.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any

from src import strict_mlb_game_snapshot as scanner

logger = logging.getLogger("kalshi_arb.mlb_game_paper_supervisor")


def _write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


async def supervise(*, interval_seconds: float, journal_path: Path, once: bool) -> None:
    logger.info(
        "MLB_GAME_PAPER_SUPERVISOR_START %s",
        json.dumps({
            "interval_seconds": interval_seconds,
            "journal_path": str(journal_path),
            "read_only": True,
            "order_entry": False,
            "actual_order_placed": False,
        }, separators=(",", ":")),
    )
    while True:
        observed_at = datetime.now(timezone.utc).isoformat()
        try:
            result = await scanner.run_once()
            record: dict[str, Any] = {
                "observed_at": observed_at,
                "status": "SCAN_OK",
                "result": result,
                "read_only": True,
                "order_entry": False,
                "actual_order_placed": False,
            }
        except Exception as exc:
            logger.exception("MLB_GAME_PAPER_SCAN_ERROR")
            record = {
                "observed_at": observed_at,
                "status": "SCAN_ERROR",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "read_only": True,
                "order_entry": False,
                "actual_order_placed": False,
            }
        _write_jsonl(journal_path, record)
        summary = record.get("result") or {}
        logger.info(
            "MLB_GAME_PAPER_CYCLE %s",
            json.dumps({
                "observed_at": observed_at,
                "status": record["status"],
                "strict_candidates": summary.get("strict_candidates", 0),
                "book_comparable": summary.get("book_comparable", 0),
                "positive_pairs": summary.get("conservative_positive_pairs", 0),
                "probe_1usd_positive_pairs": summary.get("probe_1usd_positive_pairs", 0),
                "probe_5usd_positive_pairs": summary.get("probe_5usd_positive_pairs", 0),
                "read_only": True,
                "order_entry": False,
                "actual_order_placed": False,
            }, separators=(",", ":")),
        )
        if once:
            return
        await asyncio.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent read-only MLB game-line paper collector")
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument("--journal-path", default="/data/mlb-game-paper.jsonl")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds < 10:
        parser.error("--interval-seconds must be >= 10")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(supervise(interval_seconds=args.interval_seconds, journal_path=Path(args.journal_path), once=args.once))


if __name__ == "__main__":
    main()
