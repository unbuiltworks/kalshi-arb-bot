"""Continuously supervise bounded single-child BTC/ETH/SOL/XRP live campaigns.

This wrapper exists to keep the proven one-process/four-asset scanner running without
reintroducing the multi-child WebSocket connection storm. Every successor campaign is
fresh and bounded. Rollover is fail-closed: the prior campaign must be terminal with no
active run or pending capital, no unresolved persistent attempt claims, and live venue
reconciliation must show sufficient balances and zero resting orders on both venues.

A narrowly-defined exception handles a child that was marked HALTED_FAIL_CLOSED only
because its arm deadline expired before any persistent attempt claim/result existed.
That state proves there was no venue submission from the active run, so the stale
active_run_id may be treated as a clean deadline terminal for rollover purposes. Any
claim, result, pending capital, different halt reason, or other ambiguity still blocks.

No execution gate is changed here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import secrets
from pathlib import Path

from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_crypto_supervised_recovery as recovery
from src import gemini_kalshi_sol_live_canary_v2 as v2

logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_sequential_continuous")
DEADLINE_ENV = "SOL_CANARY_V2_ARM_NOT_AFTER_UTC"
TERMINAL_STATUSES = {
    "COMPLETE_MAX_TRADES",
    "COMPLETE_MAX_EXECUTION_ATTEMPTS",
    "COMPLETE_BUDGET_RESERVE",
    "COMPLETE_DEADLINE",
}


def _deadline(raw: str) -> datetime:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise RuntimeError("campaign deadline must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _load_prior_state(campaign_id: str) -> tuple[Path, dict]:
    path = base._state_path(campaign_id)
    if not path.is_file():
        raise RuntimeError(f"sequential campaign state missing: {path}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if str(state.get("campaign_id") or "") != campaign_id:
        raise RuntimeError("sequential campaign state id mismatch")
    return path, state


def _benign_deadline_halt_without_submission(state: dict) -> bool:
    """True only for the observed deadline halt with provably zero submission state."""
    if str(state.get("status") or "") != "HALTED_FAIL_CLOSED":
        return False
    reason = str(state.get("halt_reason") or "")
    if not reason.startswith("TimeoutError: campaign arm deadline reached before execution"):
        return False
    if state.get("pending_capital_usd") not in (None, "", "0", 0, 0.0):
        return False
    run_id = str(state.get("active_run_id") or "").strip()
    if not run_id:
        return False
    _arm, claim, result = v2._paths(run_id)
    # A claim means a child crossed the persistent submission boundary. A result may
    # encode a completed/ambiguous execution. Either requires normal reconciliation.
    return not claim.exists() and not result.exists()


def _assert_local_terminal(campaign_id: str, state: dict) -> None:
    status = str(state.get("status") or "")
    benign_deadline_halt = _benign_deadline_halt_without_submission(state)
    if status not in TERMINAL_STATUSES and not benign_deadline_halt:
        raise RuntimeError(f"sequential campaign not safely terminal status={status or 'MISSING'}")
    if state.get("active_run_id") and not benign_deadline_halt:
        raise RuntimeError("sequential campaign still has active_run_id")
    if state.get("pending_capital_usd") not in (None, "", "0", 0, 0.0):
        raise RuntimeError("sequential campaign still has pending capital")
    unresolved = recovery._unresolved_claims(v2._state_dir(), campaign_id)
    if unresolved:
        raise RuntimeError(f"sequential campaign has unresolved attempt claims: {unresolved}")


def _fresh_campaign_id(now: datetime) -> str:
    # <=24 chars, compatible with base._clean_campaign_id.
    for _ in range(20):
        candidate = f"gk15m-{now.strftime('%m%d%H%M')}-{secrets.token_hex(2)}"
        candidate = base._clean_campaign_id(candidate)
        if not base._state_path(candidate).exists():
            return candidate
    raise RuntimeError("unable to generate fresh sequential campaign id")


async def _rollover(prior_campaign_id: str) -> dict:
    _path, state = _load_prior_state(prior_campaign_id)
    _assert_local_terminal(prior_campaign_id, state)
    venue = await recovery._live_reconcile()
    now = datetime.now(timezone.utc)
    next_id = _fresh_campaign_id(now)
    next_deadline = now + timedelta(minutes=30)
    os.environ[base.CAMPAIGN_ID_ENV] = next_id
    os.environ[DEADLINE_ENV] = next_deadline.isoformat().replace("+00:00", "Z")
    os.environ[v2.ARM_ENV] = "YES"
    return {
        "prior_campaign_id": prior_campaign_id,
        "prior_status": state.get("status"),
        "prior_trades": len(state.get("attempts") or []),
        "prior_execution_attempts": int(state.get("execution_attempt_count") or 0),
        "prior_deadline_no_submission": _benign_deadline_halt_without_submission(state),
        "new_campaign_id": next_id,
        "arm_not_after_utc": os.environ[DEADLINE_ENV],
        "venue_reconciliation": venue,
        "order_entry": False,
        "risk_gates_changed": False,
    }


async def run() -> None:
    if os.getenv(v2.ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError("continuous sequential campaign requires explicit arm")
    if os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() != "YES":
        raise PermissionError("continuous sequential campaign requires live probe gate")

    while True:
        campaign_id = base._clean_campaign_id(os.getenv(base.CAMPAIGN_ID_ENV, ""))
        deadline_raw = os.getenv(DEADLINE_ENV, "").strip()
        if not deadline_raw:
            raise RuntimeError(f"{DEADLINE_ENV} is required")
        if datetime.now(timezone.utc) > _deadline(deadline_raw):
            # An expired configured campaign may only be advanced if its persisted
            # sequential state proves it ended cleanly.
            plan = await _rollover(campaign_id)
            logger.warning(
                "CRYPTO_SEQUENTIAL_SUPERVISED_ROLLOVER %s",
                json.dumps(plan, separators=(",", ":"), default=str),
            )
            campaign_id = str(plan["new_campaign_id"])

        logger.warning(
            "CRYPTO_SEQUENTIAL_CONTINUOUS_START campaign_id=%s assets=BTC,ETH,SOL,XRP "
            "one_child=true bounded=true risk_gates_changed=false",
            campaign_id,
        )
        await reliable.run()

        prior = base._clean_campaign_id(os.getenv(base.CAMPAIGN_ID_ENV, ""))
        plan = await _rollover(prior)
        logger.warning(
            "CRYPTO_SEQUENTIAL_SUPERVISED_ROLLOVER %s",
            json.dumps(plan, separators=(",", ":"), default=str),
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
