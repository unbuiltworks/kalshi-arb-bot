"""Continuous v6 basis rollover after the reviewed 2026-08-23 SOL execution incident.

The historical -$0.09 one-leg execution loss remains charged to the existing $5 loss
budget. Continuation is explicitly permitted only because that exact incident has been
reviewed and the execution sequence is now Kalshi-first. Any new/unreviewed realized
loss, unresolved exposure, settlement anomaly, or budget exhaustion stops rollover.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import logging
import os
import sys
import time
from typing import Any

from src import gemini_kalshi_basis_risk_dynamic_policy as dynamic_policy
from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_basis_risk_q1_child_v6 as child_v6

logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_rollover_supervisor_v6")
LEASE_MINUTES_ENV = "CRYPTO_BASIS_RISK_ROLLOVER_LEASE_MINUTES"
DEFAULT_LEASE_MINUTES = 120
MIN_LEASE_MINUTES = 30
MAX_LEASE_MINUTES = 240
MAX_CONSECUTIVE_FAST_FAILURES = 3
FAST_FAILURE_SECONDS = 90


def _lease_minutes() -> int:
    raw = os.getenv(LEASE_MINUTES_ENV, str(DEFAULT_LEASE_MINUTES)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_LEASE_MINUTES
    return max(MIN_LEASE_MINUTES, min(value, MAX_LEASE_MINUTES))


def _campaign_id(now: datetime, sequence: int) -> str:
    return f"b6{now:%m%d%H%M}{sequence % 100:02d}"


def _validate_outer_authorization() -> None:
    if os.getenv(child.BASIS_ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(f"v6 rollover requires {child.BASIS_ARM_ENV}=YES")
    if os.getenv(child.BASIS_RECONCILED_ENV, "NO").strip().upper() != "YES":
        raise PermissionError("v6 rollover requires prior-ledger reconciled opt-in")
    try:
        budget = Decimal(os.getenv(child.BASIS_BUDGET_ENV, "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise PermissionError("v6 rollover requires valid approved budget") from exc
    if budget != child.APPROVED_BUDGET:
        raise PermissionError(f"v6 rollover requires approved budget exactly {child.APPROVED_BUDGET}")
    try:
        min_gap = Decimal(os.getenv(child.BASIS_MIN_GAP_ENV, "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise PermissionError("v6 rollover requires valid basis min-gap") from exc
    if min_gap < dynamic_policy.DYNAMIC_MIN_GAP_BPS:
        raise PermissionError("v6 rollover min-gap below dynamic policy floor")


def _state() -> dict[str, Any]:
    child_v2.assert_prior_ledger_reconciled()
    with child_v2._connect() as conn:
        return child_v6.promoted_state(conn)


def _log_state(prefix: str, state: dict[str, Any], reason: str | None = None) -> None:
    settled_trades = int(state["realized"]) - int(state.get("no_fill_releases", 0))
    logger.warning(
        "%s reservations=%s settled_trades=%s no_fill_releases=%s realized_losses=%s "
        "realized_loss_usd=%s remaining_loss_budget_usd=%s realized_net_pnl=%s "
        "unresolved_reservations=%s unresolved_submissions=%s reason=%s",
        prefix,
        state["reservations"],
        settled_trades,
        state.get("no_fill_releases", 0),
        state["realized_losses"],
        state["realized_loss_usd"],
        state["remaining_loss_budget_usd"],
        state["realized_net_pnl"],
        len(state["unresolved_reservations"]),
        len(state["unresolved_submissions"]),
        reason or "NONE",
    )


async def _run_one_lease(sequence: int, lease_minutes: int) -> tuple[int, float, str]:
    now = datetime.now(timezone.utc)
    campaign_id = _campaign_id(now, sequence)
    deadline = now + timedelta(minutes=lease_minutes)
    env = os.environ.copy()
    env[child.BASIS_ARM_ENV] = "YES"
    env[child.BASIS_RECONCILED_ENV] = "YES"
    env[child.BASIS_DEADLINE_ENV] = deadline.isoformat()
    env["CRYPTO_BASIS_RISK_CAMPAIGN_ID"] = campaign_id
    started = time.monotonic()
    logger.warning(
        "CRYPTO_BASIS_RISK_V6_LEASE_START campaign_id=%s deadline=%s execution_sequence=KALSHI_THEN_GEMINI",
        campaign_id,
        deadline.isoformat(),
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.gemini_kalshi_basis_risk_q1_campaign_v6",
        env=env,
    )
    code = await process.wait()
    elapsed = time.monotonic() - started
    logger.warning(
        "CRYPTO_BASIS_RISK_V6_LEASE_END campaign_id=%s exit_code=%s elapsed_s=%.1f",
        campaign_id, code, elapsed,
    )
    return code, elapsed, campaign_id


async def main_async() -> None:
    _validate_outer_authorization()
    lease_minutes = _lease_minutes()
    sequence = 0
    fast_failures = 0
    initial = _state()
    allowed, reason = child_v6.automatic_rollover_allowed(initial)
    _log_state("CRYPTO_BASIS_RISK_V6_BOOT", initial, reason)
    if not allowed:
        return
    logger.warning(
        "CRYPTO_BASIS_RISK_V6_ROLLOVER_ENABLED lease_minutes=%s q=1 tranche_usd=%s probe_limit=%s "
        "approved_budget_usd=%s already_realized_loss_usd=%s remaining_loss_budget_usd=%s "
        "min_gap_bps=%s min_locked_profit=%s execution_sequence=KALSHI_THEN_GEMINI "
        "new_loss_hard_stop=true one_unresolved=true guaranteed_arb=false shared_arb_lock_changed=false",
        lease_minutes,
        child.INITIAL_TRANCHE_USD,
        child_v6.PROMOTED_PROBE_LIMIT,
        child.APPROVED_BUDGET,
        initial["realized_loss_usd"],
        initial["remaining_loss_budget_usd"],
        dynamic_policy.DYNAMIC_MIN_GAP_BPS,
        dynamic_policy.DYNAMIC_MIN_LOCKED_PROFIT,
    )

    while True:
        before = _state()
        allowed, reason = child_v6.automatic_rollover_allowed(before)
        if not allowed:
            _log_state("CRYPTO_BASIS_RISK_V6_STOP", before, reason)
            return

        sequence += 1
        reservations_before = int(before["reservations"])
        realized_before = int(before["realized"])
        releases_before = int(before.get("no_fill_releases", 0))
        code, elapsed, campaign_id = await _run_one_lease(sequence, lease_minutes)

        after = _state()
        allowed_after, reason_after = child_v6.automatic_rollover_allowed(after)
        _log_state("CRYPTO_BASIS_RISK_V6_POST_LEASE", after, reason_after)
        if not allowed_after:
            logger.warning("CRYPTO_BASIS_RISK_V6_HALTED campaign_id=%s reason=%s", campaign_id, reason_after)
            return

        made_reservation = int(after["reservations"]) > reservations_before
        made_resolution = int(after["realized"]) > realized_before
        made_release = int(after.get("no_fill_releases", 0)) > releases_before
        if made_reservation and not made_resolution:
            logger.critical(
                "CRYPTO_BASIS_RISK_V6_HALTED campaign_id=%s reason=RESERVATION_WITHOUT_RESOLUTION",
                campaign_id,
            )
            return

        if made_release:
            fast_failures = 0
            logger.warning("CRYPTO_BASIS_RISK_V6_NO_FILL_ROLL_NEXT campaign_id=%s", campaign_id)
            await asyncio.sleep(1)
            continue
        if made_resolution:
            fast_failures = 0
            logger.warning(
                "CRYPTO_BASIS_RISK_V6_REALIZED_ROLL_NEXT campaign_id=%s realized_net_pnl=%s",
                campaign_id,
                after["realized_net_pnl"],
            )
            await asyncio.sleep(2)
            continue

        expected_lease_seconds = lease_minutes * 60
        if code == 0 or elapsed >= expected_lease_seconds * 0.8:
            fast_failures = 0
            await asyncio.sleep(2)
            continue
        fast_failures += 1
        logger.error(
            "CRYPTO_BASIS_RISK_V6_FAST_FAILURE campaign_id=%s exit_code=%s elapsed_s=%.1f consecutive=%s",
            campaign_id, code, elapsed, fast_failures,
        )
        if elapsed > FAST_FAILURE_SECONDS:
            fast_failures = 1
        if fast_failures >= MAX_CONSECUTIVE_FAST_FAILURES:
            raise RuntimeError("v6 basis rollover hit repeated fast campaign failures")
        await asyncio.sleep(10)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
