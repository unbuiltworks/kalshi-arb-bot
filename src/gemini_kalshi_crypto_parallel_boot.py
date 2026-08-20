"""Fail-closed state guard for parallel live crypto campaigns.

A campaign ID is single-process/single-lifetime. If its state file already exists,
a restart or redeploy refuses to arm anything; account reconciliation and a fresh
campaign ID are required.
"""
from __future__ import annotations

from pathlib import Path


def ensure_fresh_campaign_state(campaign_id: str, state_dir: Path) -> Path:
    path = state_dir / f"{campaign_id}.json"
    if path.exists():
        raise RuntimeError(
            f"parallel campaign state already exists for {campaign_id}; "
            "refusing restart/redeploy without reconciliation and a fresh campaign id"
        )
    return path
