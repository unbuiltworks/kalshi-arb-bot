"""Fail-closed boot wrapper for the parallel live crypto campaign.

A campaign ID is single-process/single-lifetime. If its state file already exists,
a restart or redeploy refuses to arm anything; account reconciliation and a fresh
campaign ID are required.
"""
from __future__ import annotations

import os
from pathlib import Path

from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_crypto_parallel_campaign as parallel


def ensure_fresh_campaign_state(campaign_id: str, state_dir: Path) -> Path:
    path = state_dir / f"{campaign_id}.json"
    if path.exists():
        raise RuntimeError(
            f"parallel campaign state already exists for {campaign_id}; "
            "refusing restart/redeploy without reconciliation and a fresh campaign id"
        )
    return path


def main() -> None:
    campaign_id = base._clean_campaign_id(os.getenv(base.CAMPAIGN_ID_ENV, ""))
    ensure_fresh_campaign_state(campaign_id, parallel.STATE_DIR)
    parallel.main()


if __name__ == "__main__":
    main()
