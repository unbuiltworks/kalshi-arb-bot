"""Persistent read-only supervisor for MLB Kalshi <-> Polymarket prop evidence.

This process intentionally has no authentication or order-entry path. It repeatedly
runs the strict v5 public-book/participation snapshot and appends the complete result
to a JSONL journal so candidate discovery and near-trade evidence survive Railway
restarts.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any

from src import strict_mlb_prop_snapshot_v2 as discovery
from src import strict_mlb_prop_snapshot_v5 as scanner

logger = logging.getLogger("kalshi_arb.mlb_prop_paper_supervisor")


def _write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _configure_logging() -> None:
    """Keep normal read-only telemetry on stdout so Railway severity stays truthful.

    ``logging.basicConfig`` defaults to stderr. Railway classifies stderr records as
    error-severity even when the Python record itself is INFO, which made healthy
    HTTP 200s and SCAN_OK cycles look like failures in service telemetry. Route the
    root handler to stdout; real exceptions remain distinguishable by their message
    and Python log level without polluting the transport severity.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        stream=sys.stdout,
        force=True,
    )


async def supervise(
    *,
    interval_seconds: float,
    journal_path: Path,
    once: bool,
    future_horizon_hours: float,
) -> None:
    # Midnight-ET runs need to see evening games more than 18 hours away. Keep the
    # same strict game/start matching, but make discovery wide enough to cover the
    # entire upcoming MLB day. This affects discovery only, never execution.
    discovery.FUTURE_HORIZON = timedelta(hours=future_horizon_hours)
    logger.info(
        "MLB_PROP_PAPER_SUPERVISOR_START %s",
        json.dumps(
            {
                "status": "PAPER_SUPERVISOR_START",
                "read_only": True,
                "order_entry": False,
                "actual_order_placed": False,
                "interval_seconds": interval_seconds,
                "future_horizon_hours": future_horizon_hours,
                "journal_path": str(journal_path),
            },
            separators=(",", ":"),
        ),
    )

    while True:
        observed_at = datetime.now(timezone.utc).isoformat()
        try:
            result = await scanner.run_once()
            record: dict[str, Any] = {
                "observed_at": observed_at,
                "status": "SCAN_OK",
                "read_only": True,
                "order_entry": False,
                "actual_order_placed": False,
                "result": result,
            }
        except Exception as exc:  # keep evidence collection alive; never fail open
            logger.exception("MLB_PROP_PAPER_SCAN_ERROR")
            record = {
                "observed_at": observed_at,
                "status": "SCAN_ERROR",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "read_only": True,
                "order_entry": False,
                "actual_order_placed": False,
            }

        _write_jsonl(journal_path, record)
        summary = record.get("result") or {}
        logger.info(
            "MLB_PROP_PAPER_CYCLE %s",
            json.dumps(
                {
                    "observed_at": observed_at,
                    "status": record["status"],
                    "strict_candidates": summary.get("total_strict_candidates", 0),
                    "participation_cleared": summary.get("participation_cleared_candidates", 0),
                    "book_comparable": summary.get("book_comparable", 0),
                    "positive_pairs": summary.get("conservative_positive_pairs", 0),
                    "paper_probe_candidates": len(summary.get("paper_probe_candidates") or []),
                    "ready_probe_packets": len(summary.get("ready_probe_packets") or []),
                    "read_only": True,
                    "order_entry": False,
                    "actual_order_placed": False,
                },
                separators=(",", ":"),
            ),
        )

        if once:
            return
        await asyncio.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent read-only MLB prop paper evidence collector")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--future-horizon-hours", type=float, default=36.0)
    parser.add_argument("--journal-path", default="/data/mlb-prop-paper.jsonl")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds < 5:
        parser.error("--interval-seconds must be >= 5")
    if not 18 <= args.future_horizon_hours <= 48:
        parser.error("--future-horizon-hours must be between 18 and 48")
    _configure_logging()
    asyncio.run(
        supervise(
            interval_seconds=args.interval_seconds,
            journal_path=Path(args.journal_path),
            once=args.once,
            future_horizon_hours=args.future_horizon_hours,
        )
    )


if __name__ == "__main__":
    main()
