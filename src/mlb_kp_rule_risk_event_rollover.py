"""Continuous supervisor for the event-driven K/P MLB rule-risk q=1 lane.

The underlying lane remains the mutation authority and retains all execution,
reservation, freshness, persistence, depth, fee, participation, and hard-halt
controls.  This wrapper only changes lifecycle behavior:

- a clean completed q=1 cycle is restarted so scanning continues;
- pre-mutation shared-ledger/budget blockers wait and retry safely;
- a hard-halt return code or any unexpected exception stops the supervisor.

It never catches post-reservation execution ambiguity.
"""
from __future__ import annotations

import asyncio
import logging
import os

from src import mlb_kp_rule_risk_event_live_q1 as lane

logger = logging.getLogger("kalshi_arb.mlb_kp_rule_risk_event_rollover")

RESTART_DELAY_S = float(os.getenv("MLB_KP_RULE_RISK_ROLLOVER_DELAY_S", "2"))
HARD_HALT_RC = 70
_SAFE_PRE_MUTATION_PREFIXES = (
    "unresolved sports live trade blocks execution:",
    "shared sports budget exhausted:",
)


def safe_pre_mutation_block(exc: BaseException) -> bool:
    """Only classify known reserve() blockers that occur before any mutation."""
    return isinstance(exc, RuntimeError) and str(exc).startswith(_SAFE_PRE_MUTATION_PREFIXES)


async def run() -> int:
    while True:
        try:
            rc = await lane.run()
        except RuntimeError as exc:
            if not safe_pre_mutation_block(exc):
                raise
            logger.warning("MLB_KP_RULE_RISK_ROLLOVER_BLOCKED reason=%s", str(exc))
            await asyncio.sleep(max(0.1, RESTART_DELAY_S))
            continue

        if rc == HARD_HALT_RC:
            logger.error("MLB_KP_RULE_RISK_ROLLOVER_HARD_HALT rc=%d", rc)
            return rc
        if rc not in (0, None):
            raise RuntimeError(f"unexpected K/P lane return code: {rc}")

        logger.info("MLB_KP_RULE_RISK_ROLLOVER_RESTART clean_cycle=true")
        await asyncio.sleep(max(0.1, RESTART_DELAY_S))


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
