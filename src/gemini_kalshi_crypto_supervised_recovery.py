"""Fail-closed supervised rollover for bounded crypto campaigns.

This module never weakens live gates. It may replace only the campaign id and arm
expiry in the current process after proving the prior persistent campaign is clean,
there are no unresolved local attempt claims, both venue balance reads succeed, and
both venues report zero resting prediction-market orders. Any ambiguity raises.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from src import gemini_kalshi_crypto_campaign_rollover as rollover
from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_crypto_parallel_campaign as p
from src import gemini_kalshi_sol_live_canary_v2 as v2
from src.gemini_kalshi_probe_transport import KalshiV2Executor
from src.gemini_prediction_auth import private_rest_request

DEADLINE_ENV = "SOL_CANARY_V2_ARM_NOT_AFTER_UTC"


def _parse_deadline(raw: str) -> datetime:
    if not raw.strip():
        raise RuntimeError(f"{DEADLINE_ENV} is required")
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise RuntimeError("campaign arm deadline must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _unresolved_claims(root: Path, campaign_id: str) -> list[str]:
    unresolved: list[str] = []
    for claim in root.glob(f"{campaign_id}-*.attempt.json"):
        result = claim.with_name(claim.name.replace(".attempt.json", ".result.json"))
        if not result.is_file():
            unresolved.append(claim.name)
    return sorted(unresolved)


def _orders(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise RuntimeError("order-list response must be an object")
    rows = payload.get("orders")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise RuntimeError("order-list orders field must be a list")
    return [row for row in rows if isinstance(row, dict)]


async def _live_reconcile() -> dict[str, Any]:
    balances = await p._live_venue_balances()
    if any(value < p.VENUE_RESERVE_PER_ATTEMPT for value in balances.values()):
        raise RuntimeError(f"insufficient venue balance for rollover: {balances}")

    gemini = await private_rest_request(
        "POST",
        "/v1/prediction-markets/orders/active",
        params={"limit": 100, "offset": 0},
        timeout=5.0,
    )
    gemini.raise_for_status()
    gemini_orders = _orders(gemini.json())

    kalshi = KalshiV2Executor()
    try:
        response = await kalshi.client.get(
            "/portfolio/orders", params={"status": "resting", "limit": 100}
        )
        response.raise_for_status()
        kalshi_orders = _orders(response.json())
    finally:
        await kalshi.close()

    if gemini_orders or kalshi_orders:
        raise RuntimeError(
            "refusing campaign rollover with resting orders "
            f"gemini={len(gemini_orders)} kalshi={len(kalshi_orders)}"
        )
    return {
        "gemini_available_usd": str(balances["gemini"]),
        "kalshi_available_usd": str(balances["kalshi"]),
        "gemini_resting_orders": 0,
        "kalshi_resting_orders": 0,
    }


def prepare_if_ended() -> dict[str, Any] | None:
    """Prepare a fresh campaign iff the configured campaign is safely terminal.

    A brand-new configured campaign with no state file is allowed to proceed. A
    persisted campaign that is still active is never silently reused after a process
    return/restart; that condition fails closed. Terminal means deadline expired or a
    configured trade/attempt cap was actually reached.
    """
    campaign_raw = os.getenv(base.CAMPAIGN_ID_ENV, "").strip()
    deadline_raw = os.getenv(DEADLINE_ENV, "").strip()
    if not campaign_raw and not deadline_raw:
        return None
    if not campaign_raw or not deadline_raw:
        raise RuntimeError("campaign id and arm deadline must be configured together")

    campaign_id = base._clean_campaign_id(campaign_raw)
    deadline = _parse_deadline(deadline_raw)
    now = datetime.now(timezone.utc)
    state_path = p.STATE_DIR / f"{campaign_id}.json"

    if not state_path.is_file():
        if now > deadline:
            raise RuntimeError(f"expired campaign state missing: {state_path}")
        return None

    state = rollover.load_campaign_state(state_path)
    diagnosis = rollover.diagnose_campaign(
        state=state,
        prior_arm_deadline_utc=deadline_raw,
        now=now,
        max_completed_trades=p.MAX_COMPLETED_TRADES,
        max_execution_attempts=p.MAX_EXECUTION_ATTEMPTS,
    )
    if diagnosis["bounded_state"] == "ACTIVE_WITHIN_ARM_WINDOW":
        raise RuntimeError(
            "persisted campaign is still active; refusing implicit resume: "
            + json.dumps(diagnosis, separators=(",", ":"), default=str)
        )
    if not diagnosis["rollover_eligible"]:
        raise RuntimeError(
            "terminal campaign is not locally rollover-eligible: "
            + json.dumps(diagnosis, separators=(",", ":"), default=str)
        )

    claim_root = v2._state_dir()
    unresolved = _unresolved_claims(claim_root, campaign_id)
    if unresolved:
        raise RuntimeError(f"unresolved persistent attempt claims: {unresolved}")

    venue = asyncio.run(_live_reconcile())
    plan = rollover.plan_rollover(
        state=state,
        prior_arm_deadline_utc=deadline_raw,
        now=now,
        max_completed_trades=p.MAX_COMPLETED_TRADES,
        max_execution_attempts=p.MAX_EXECUTION_ATTEMPTS,
        arm_window_minutes=30,
        state_dir=p.STATE_DIR,
    )
    os.environ[base.CAMPAIGN_ID_ENV] = plan.new_campaign_id
    os.environ[DEADLINE_ENV] = plan.arm_not_after_utc
    return {
        "prior_campaign_id": campaign_id,
        "new_campaign_id": plan.new_campaign_id,
        "arm_not_after_utc": plan.arm_not_after_utc,
        "reason": plan.reason,
        "venue_reconciliation": venue,
        "order_entry": False,
        "next_step": "existing postincident boot retains all live gates",
    }


def prepare_if_expired() -> dict[str, Any] | None:
    """Backward-compatible alias retained for existing callers/tests."""
    return prepare_if_ended()
