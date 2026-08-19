"""Deadline-gated entrypoint for the one-shot MLB G/K live rule-risk canary.

Fails closed unless MLB_GK_PROP_ARM_NOT_AFTER_UTC is present and in the future.
The underlying watch duration is capped to the remaining authorization window so
a process started just before expiry cannot continue hunting indefinitely.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import logging
import os

from src import mlb_gk_prop_live_rule_risk_canary as canary

logger = logging.getLogger("kalshi_arb.mlb_gk_prop_live_rule_risk_canary_deadline")


def _deadline() -> datetime:
    raw = os.getenv("MLB_GK_PROP_ARM_NOT_AFTER_UTC", "").strip()
    if not raw:
        raise RuntimeError("MLB_GK_PROP_ARM_NOT_AFTER_UTC is required")
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise RuntimeError("MLB_GK_PROP_ARM_NOT_AFTER_UTC must be timezone-aware")
    return value.astimezone(timezone.utc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--watch-seconds", type=float, default=14400.0)
    args = parser.parse_args()

    deadline = _deadline()
    now = datetime.now(timezone.utc)
    remaining = (deadline - now).total_seconds()
    if remaining <= 0:
        raise SystemExit("MLB live rule-risk arm deadline has expired")
    if not args.live:
        raise SystemExit("MLB live rule-risk canary requires --live")

    watch_seconds = min(max(1.0, args.watch_seconds), remaining)
    logger.warning(
        "MLB_GK_RULE_RISK_DEADLINE_GATE deadline_utc=%s remaining_seconds=%.1f watch_seconds=%.1f",
        deadline.isoformat(), remaining, watch_seconds,
    )
    raise SystemExit(asyncio.run(canary.run(live_requested=True, watch_seconds=watch_seconds)))


if __name__ == "__main__":
    main()
