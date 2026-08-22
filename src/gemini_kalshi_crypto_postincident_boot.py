"""Post-incident boot for the q=1 parallel Gemini/Kalshi campaign.

Adds reliability layers around the existing safe parallel supervisor and, when live
campaign env is configured, continuously chains bounded campaigns. Each successor is
created only after the prior campaign is safely terminal and passes strict local plus
live venue reconciliation. Any halt, unresolved claim, resting order, malformed state,
or ambiguous venue read fails closed and stops the process.

A separate settlement-equivalence arm is required because price/boundary agreement is
not proof that Gemini Prediction and Kalshi settle from the same authoritative index.
After a production BTC trade on 2026-08-22 filled both legs but settled to opposite
venue outcomes, live order entry must remain fail-closed until exact settlement-source
and rule equivalence is independently certified.
"""
from __future__ import annotations

import json
import logging
import os

from src import gemini_kalshi_crypto_benign_deadline as benign_deadline
from src import gemini_kalshi_crypto_campaign_child_reliable as child
from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_crypto_parallel_safe_boot as safe_boot
from src import gemini_kalshi_crypto_supervised_recovery as recovery
from src import gemini_kalshi_incident_accounting as incident_accounting
from src import gemini_kalshi_preclaim_child_exit_retry as preclaim_retry
from src import gemini_kalshi_ws_rate_limit_guard as ws_rate_limit_guard

logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_postincident_boot")
SETTLEMENT_EQUIVALENCE_ENV = "CRYPTO_GK_SETTLEMENT_EQUIVALENCE_CERTIFIED"


def _continuous_mode() -> bool:
    campaign = os.getenv(base.CAMPAIGN_ID_ENV, "").strip()
    deadline = os.getenv(recovery.DEADLINE_ENV, "").strip()
    return bool(campaign and deadline)


def _require_settlement_equivalence() -> None:
    if os.getenv(SETTLEMENT_EQUIVALENCE_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(
            "Gemini/Kalshi live crypto disabled: exact settlement-source equivalence "
            f"must be certified before setting {SETTLEMENT_EQUIVALENCE_ENV}=YES"
        )


def main() -> None:
    if os.getenv(child.HEDGE_CUSHION_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(
            "post-incident live campaign requires CRYPTO_KALSHI_HEDGE_CUSHION=YES"
        )
    _require_settlement_equivalence()

    incident_accounting.install()
    preclaim_retry.install()
    ws_rate_limit_guard.install_parent_spawn_pacing()
    benign_deadline.install()

    continuous = _continuous_mode()
    while True:
        plan = recovery.prepare_if_ended()
        if plan is not None:
            logger.warning(
                "CRYPTO_SUPERVISED_ROLLOVER %s",
                json.dumps(plan, separators=(",", ":"), default=str),
            )

        safe_boot.main()

        if not continuous:
            return
        # Returning from a configured live bounded campaign is not treated as a
        # successful end-state by this wrapper. The next loop must prove that the
        # persisted campaign is terminal and clean before a successor can start.
        logger.warning(
            "CRYPTO_BOUNDED_CAMPAIGN_RETURNED campaign_id=%s checking_successor=true",
            os.getenv(base.CAMPAIGN_ID_ENV, ""),
        )


if __name__ == "__main__":
    main()
