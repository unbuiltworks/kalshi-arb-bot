"""Fail-closed planning primitives for supervised crypto campaign rollover.

This module does not arm trading, mutate Railway, or submit venue orders. It only
proves that a bounded parallel campaign is locally clean enough to *plan* a fresh
campaign namespace. A caller must still perform live account/order reconciliation
before applying the returned environment values.

The safety model is deliberately strict:
- never reuse a campaign id;
- never roll while the prior arm window is still open unless a configured campaign
  cap was actually reached;
- never roll a halted campaign;
- never roll with an active run, local reservation, or unsettled capital;
- never roll a lane that is waiting on execution/settlement or otherwise unresolved;
- return a short-lived fresh arm deadline and a new campaign id, but do not apply it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
from typing import Any


_ALLOWED_TERMINAL_LANE_STATUSES = {
    "IDLE",
    "COMPLETE_MAX_EXECUTION_ATTEMPTS",
    "STOPPED_GLOBAL_HALT",
}
_UNRESOLVED_LANE_STATUSES = {
    "WAITING_EXECUTION",
    "WAITING_SETTLEMENT",
    "WAITING_CAPITAL",
    "WAITING_TRADE_SLOT",
    "HALTED",
}


@dataclass(frozen=True)
class RolloverPlan:
    prior_campaign_id: str
    new_campaign_id: str
    arm_not_after_utc: str
    reason: str

    def env(self) -> dict[str, str]:
        return {
            "CRYPTO_CANARY_CAMPAIGN_ID": self.new_campaign_id,
            "SOL_CANARY_V2_ARM_NOT_AFTER_UTC": self.arm_not_after_utc,
        }


def _as_decimalish_zero(value: Any) -> bool:
    try:
        return float(str(value or "0")) == 0.0
    except (TypeError, ValueError):
        return False


def _parse_deadline(raw: str) -> datetime:
    if not raw or not raw.strip():
        raise ValueError("prior arm deadline is required")
    dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("prior arm deadline must be timezone-aware")
    return dt.astimezone(timezone.utc)


def load_campaign_state(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("campaign state must be a JSON object")
    return payload


def assert_locally_reconciled(state: dict[str, Any]) -> None:
    campaign_id = str(state.get("campaign_id") or "").strip()
    if not campaign_id:
        raise RuntimeError("campaign state is missing campaign_id")
    if bool(state.get("halted")):
        raise RuntimeError(f"campaign {campaign_id} is halted: {state.get('halt_reason')}")

    if state.get("reservations"):
        raise RuntimeError(f"campaign {campaign_id} still has capital reservations")
    if not _as_decimalish_zero(state.get("reserved_capital_usd")):
        raise RuntimeError(f"campaign {campaign_id} still has reserved capital")
    if not _as_decimalish_zero(state.get("unsettled_deployed_usd")):
        raise RuntimeError(f"campaign {campaign_id} still has unsettled deployed capital")

    venue_reserved = state.get("venue_reserved_usd") or {}
    venue_unsettled = state.get("venue_unsettled_usd") or {}
    for venue in ("gemini", "kalshi"):
        if not _as_decimalish_zero(venue_reserved.get(venue)):
            raise RuntimeError(f"campaign {campaign_id} still has {venue} reserved capital")
        if not _as_decimalish_zero(venue_unsettled.get(venue)):
            raise RuntimeError(f"campaign {campaign_id} still has {venue} unsettled capital")

    lanes = state.get("lanes") or {}
    if not isinstance(lanes, dict) or not lanes:
        raise RuntimeError(f"campaign {campaign_id} has no lane state")
    for asset, lane in lanes.items():
        if not isinstance(lane, dict):
            raise RuntimeError(f"campaign {campaign_id} lane {asset} is malformed")
        if lane.get("active_run_id"):
            raise RuntimeError(f"campaign {campaign_id} lane {asset} has active run")
        if not _as_decimalish_zero(lane.get("reserved_usd")):
            raise RuntimeError(f"campaign {campaign_id} lane {asset} still has reserved USD")
        status = str(lane.get("status") or "").strip().upper()
        if status in _UNRESOLVED_LANE_STATUSES or status not in _ALLOWED_TERMINAL_LANE_STATUSES:
            raise RuntimeError(f"campaign {campaign_id} lane {asset} unresolved status={status or 'MISSING'}")


def diagnose_campaign(
    *,
    state: dict[str, Any],
    prior_arm_deadline_utc: str,
    now: datetime | None = None,
    max_completed_trades: int,
    max_execution_attempts: int,
) -> dict[str, Any]:
    """Return a read-only diagnosis of the bounded campaign state.

    This is intended for supervisors/runtime probes. It never plans or applies a
    new arm. `rollover_eligible` means only that persistent local state is clean and
    the bounded campaign has ended; live venue reconciliation is still required.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    deadline = _parse_deadline(prior_arm_deadline_utc)
    completed = int(state.get("completed_total") or 0)
    attempts = int(state.get("execution_attempts_total") or 0)
    deadline_expired = now > deadline
    completed_cap = completed >= max_completed_trades
    attempts_cap = attempts >= max_execution_attempts

    local_error: str | None = None
    try:
        assert_locally_reconciled(state)
        locally_reconciled = True
    except Exception as exc:
        locally_reconciled = False
        local_error = f"{type(exc).__name__}:{str(exc)}"

    if bool(state.get("halted")):
        bounded_state = "HALTED"
    elif completed_cap:
        bounded_state = "COMPLETE_MAX_TRADES"
    elif attempts_cap:
        bounded_state = "COMPLETE_MAX_EXECUTION_ATTEMPTS"
    elif deadline_expired:
        bounded_state = "COMPLETE_ARM_DEADLINE_EXPIRED"
    else:
        bounded_state = "ACTIVE_WITHIN_ARM_WINDOW"

    lanes = state.get("lanes") or {}
    lane_statuses = {
        asset: {
            "status": str((lane or {}).get("status") or "MISSING"),
            "active_run_id": (lane or {}).get("active_run_id"),
            "reserved_usd": str((lane or {}).get("reserved_usd") or "0"),
        }
        for asset, lane in lanes.items()
        if isinstance(lane, dict)
    }

    ended = deadline_expired or completed_cap or attempts_cap
    return {
        "campaign_id": str(state.get("campaign_id") or ""),
        "observed_at_utc": now.isoformat().replace("+00:00", "Z"),
        "arm_not_after_utc": deadline.isoformat().replace("+00:00", "Z"),
        "arm_deadline_expired": deadline_expired,
        "completed_total": completed,
        "max_completed_trades": int(max_completed_trades),
        "execution_attempts_total": attempts,
        "max_execution_attempts": int(max_execution_attempts),
        "halted": bool(state.get("halted")),
        "halt_reason": state.get("halt_reason"),
        "bounded_state": bounded_state,
        "locally_reconciled": locally_reconciled,
        "local_reconciliation_error": local_error,
        "rollover_eligible": bool(ended and locally_reconciled and not state.get("halted")),
        "live_venue_reconciliation_required": True,
        "lanes": lane_statuses,
    }


def plan_rollover(
    *,
    state: dict[str, Any],
    prior_arm_deadline_utc: str,
    now: datetime | None = None,
    max_completed_trades: int,
    max_execution_attempts: int,
    arm_window_minutes: int = 30,
    state_dir: Path | None = None,
) -> RolloverPlan:
    """Return a fresh bounded campaign plan after local fail-closed checks.

    Live venue reconciliation is intentionally outside this function. The caller
    must verify open orders/fills/balances before applying the returned environment.
    """
    assert_locally_reconciled(state)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    deadline = _parse_deadline(prior_arm_deadline_utc)

    completed = int(state.get("completed_total") or 0)
    attempts = int(state.get("execution_attempts_total") or 0)
    deadline_expired = now > deadline
    completed_cap = completed >= max_completed_trades
    attempts_cap = attempts >= max_execution_attempts
    if not (deadline_expired or completed_cap or attempts_cap):
        raise RuntimeError("prior campaign is still within its arm window and below all campaign caps")

    if not 5 <= arm_window_minutes <= 60:
        raise ValueError("rollover arm window must be between 5 and 60 minutes")

    reason = (
        "deadline_expired" if deadline_expired else
        "completed_trade_cap" if completed_cap else
        "execution_attempt_cap"
    )
    stamp = now.strftime("%y%m%d-%H%M%S")
    prior = str(state["campaign_id"]).strip()
    new_campaign_id = f"gk15m-roll-{stamp}-{secrets.token_hex(3)}"
    if new_campaign_id == prior:
        raise RuntimeError("generated campaign id unexpectedly reused prior id")

    if state_dir is not None and (state_dir / f"{new_campaign_id}.json").exists():
        raise RuntimeError("generated campaign id already has persistent state")

    new_deadline = now + timedelta(minutes=arm_window_minutes)
    return RolloverPlan(
        prior_campaign_id=prior,
        new_campaign_id=new_campaign_id,
        arm_not_after_utc=new_deadline.isoformat().replace("+00:00", "Z"),
        reason=reason,
    )
