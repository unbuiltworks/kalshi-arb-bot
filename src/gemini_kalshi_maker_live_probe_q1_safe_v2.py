"""Final hardened entrypoint for the q=1 maker LIVE_PROBE.

Pins the original shadow candidate function before the live wrapper temporarily
installs its stricter selector, avoiding any recursive monkeypatch behavior.
"""
from __future__ import annotations

import asyncio
import json
import logging

from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_live_probe_q1_safe as safe

logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_live_probe_q1_safe_v2")
_ORIGINAL_CANDIDATE = base.candidate_from_books


def candidate_from_books(*, gemini, kalshi, fee_meta, now_ms: int):
    candidate = _ORIGINAL_CANDIDATE(
        gemini=gemini, kalshi=kalshi, fee_meta=fee_meta, now_ms=now_ms
    )
    if candidate is None:
        return None
    if candidate.maker_price < candidate.best_bid:
        return None
    return candidate


# safe.run_once resolves this global at runtime, then installs it into base only for
# the duration of candidate discovery. This function itself always calls the pinned
# pre-live original above.
safe.candidate_from_books = candidate_from_books

# Re-export for unit tests and operator inspection.
kalshi_maker_payload = safe.kalshi_maker_payload
gemini_hedge_payload = safe.gemini_hedge_payload
TARGET_PROFIT = safe.TARGET_PROFIT
QTY = safe.QTY


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    result = asyncio.run(safe.run_once())
    logger.warning("MAKER_LIVE_PROBE_FINAL_RESULT %s", json.dumps(result, separators=(",", ":"), default=str))
    if result.get("hedge", {}).get("hard_halt"):
        raise SystemExit(70)


if __name__ == "__main__":
    main()
