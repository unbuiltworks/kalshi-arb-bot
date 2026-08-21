"""Post-incident boot for the q=1 parallel Gemini/Kalshi campaign.

Adds four reliability layers before launching the existing safe parallel supervisor:
1. confirmed emergency-flatten ledger accounting;
2. a required, explicitly enabled fee-aware Kalshi hedge cushion that preserves the
   existing >=3c after-fee floor;
3. bounded retry for child startup exits that provably occurred before the persistent
   attempt claim (and therefore before any venue submission); and
4. global pacing/backoff for authenticated Gemini trading-WebSocket child starts.

If the configured bounded campaign has expired, startup may also perform a strict
supervised rollover. Rollover is fail-closed and requires clean persistent state,
zero unresolved attempt claims, successful authenticated venue balance reads, and
zero resting prediction-market orders on both venues. It only refreshes the campaign
id/deadline in-process; every existing live gate remains required.
"""
from __future__ import annotations

import json
import logging
import os

from src import gemini_kalshi_crypto_campaign_child_reliable as child
from src import gemini_kalshi_crypto_parallel_safe_boot as safe_boot
from src import gemini_kalshi_crypto_supervised_recovery as recovery
from src import gemini_kalshi_incident_accounting as incident_accounting
from src import gemini_kalshi_preclaim_child_exit_retry as preclaim_retry
from src import gemini_kalshi_ws_rate_limit_guard as ws_rate_limit_guard

logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_postincident_boot")


def main() -> None:
    if os.getenv(child.HEDGE_CUSHION_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(
            "post-incident live campaign requires CRYPTO_KALSHI_HEDGE_CUSHION=YES"
        )

    rollover = recovery.prepare_if_expired()
    if rollover is not None:
        logger.warning(
            "CRYPTO_SUPERVISED_ROLLOVER %s",
            json.dumps(rollover, separators=(",", ":"), default=str),
        )

    incident_accounting.install()
    # Install the preclaim classifier first so the outer WS pacing wrapper can see
    # safe startup-retry records and impose a global cooldown across all asset lanes.
    preclaim_retry.install()
    ws_rate_limit_guard.install_parent_spawn_pacing()
    safe_boot.main()


if __name__ == "__main__":
    main()
