"""Continuously roll short-lived basis-risk q=1 campaign leases.

This makes the canary operationally continuous without creating an indefinite stale
execution token. Each child campaign gets a fresh short campaign id and arm deadline.
Before every lease the supervisor re-reads the authoritative Postgres ledger and fails
closed if there is unresolved exposure, any realized loss, or the approved five-probe /
$5 research ceiling has been reached.

This supervisor never changes the guaranteed-arbitrage settlement-equivalence lock.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import sys
import time
from typing import Any

from src import gemini_kalshi_basis_risk_dynamic_policy as dynamic_policy
from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_basis_risk_q1_child_v5 as child_v5

logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_rollover_supervisor")

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
    # Keep comfortably under the base executor's 24-character campaign-id ceiling.
    return f"br{now:%m%d%H%M}{sequence % 100:02d}"


def _validate_outer_authorization() -> None:
    if os.getenv(child.BASIS_ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(f"rollover supervisor requires {child.BASIS_ARM_ENV}=YES")
    if os.getenv(child.BASIS_RECONCILED_ENV, "NO").strip().upper() != "YES":
        raise PermissionError("rollover supervisor requires prior-ledger reconciled opt-in")
    if os.getenv(child.BASIS_BUDGET_ENV, "").strip() != str(child.APPROVED_BUDGET):
        raise PermissionError(f"rollover supervisor requires approved budget exactly {child.APPROVED_BUDGET}")
    try:
        min_gap = child.D(os.getenv(child.BASIS_MIN_GAP_ENV, ""))
    except Exception as exc:
        raise PermissionError("rollover supervisor requires valid basis min-gap") from exc
    if min_gap < dynamic_policy.DYNAMIC_MIN_GAP_BPS:
        raise PermissionError("rollover supervisor min-gap below dynamic policy floor")


def _state() -> dict[str, Any]:
    child_v2.assert_prior_ledger_reconciled()
    with child_v2._connect() as conn:
        return child_v5.promoted_state(conn)


def _log_state(prefix: str, state: dict[str, Any], reason: str | None = None) -> None:
    logger.warning(
        "%s reservations=%s realized=%s realized_losses=%s realized_loss_usd=%s realized_net_pnl=%s unresolved_reservations=%s unresolved_submissions=%s reason=%s",
        prefix,
        state["reservations"],
        state["realized"],
        state["realized_losses"],
        state["realized_loss_usd"],
        state["realized_net_pnl"],
        len(state["unresolved_reservations"]),
        len(state["unresolved_submissions"]),
        reason or "NONE",
    )


async def _run_one_lease(sequence: int, lease_minutes: int) -> tuple[int, float, str, datetime]:
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
        "CRYPTO_BASIS_RISK_ROLLOVER_LEASE_START campaign_id=%s deadline=%s lease_minutes=%s guaranteed_arb=false",
        campaign_id,
        deadline.isoformat(),
        lease_minutes,
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.gemini_kalshi_basis_risk_q1_campaign_v5",
        env=env,
    )
    code = await process.wait()
    elapsed = time.monotonic() - started
    logger.warning(
        "CRYPTO_BASIS_RISK_ROLLOVER_LEASE_END campaign_id=%s exit_code=%s elapsed_s=%.1f",
        campaign_id,
        code,
        elapsed,
    )
    return code, elapsed, campaign_id, deadline


async def main_async() -> None:
    _validate_outer_authorization()
    lease_minutes = _lease_minutes()
    sequence = 0
    fast_failures = 0
    logger.warning(
        "CRYPTO_BASIS_RISK_ROLLOVER_ENABLED lease_minutes=%s q=1 tranche_usd=%s probe_limit=%s approved_budget_usd=%s min_gap_bps=%s min_locked_profit=%s hard_stop_on_any_loss=true one_unresolved=true guaranteed_arb=false shared_arb_lock_changed=false",
        lease_minutes,
        child.INITIAL_TRANCHE_USD,
        child_v5.PROMOTED_PROBE_LIMIT,
        child.APPROVED_BUDGET,
        dynamic_policy.DYNAMIC_MIN_GAP_BPS,
        dynamic_policy.DYNAMIC_MIN_LOCKED_PROFIT,
    )

    while True:
        state_before = _state()
        allowed, reason = child_v5.automatic_rollover_allowed(state_before)
        if not allowed:
            _log_state("CRYPTO_BASIS_RISK_ROLLOVER_STOP", state_before, reason)
            return

        sequence += 1
        reservations_before = int(state_before["reservations"])
        realized_before = int(state_before["realized"])
        code, elapsed, campaign_id, deadline = await _run_one_lease(sequence, lease_minutes)

        state_after = _state()
        allowed_after, reason_after = child_v5.automatic_rollover_allowed(state_after)
        _log_state("CRYPTO_BASIS_RISK_ROLLOVER_POST_LEASE", state_after, reason_after)

        # Any unresolved reservation/submission or any realized loss must stop here.
        if not allowed_after:
            logger.warning(
                "CRYPTO_BASIS_RISK_ROLLOVER_HALTED campaign_id=%s reason=%s",
                campaign_id,
                reason_after,
            )
            return

        made_reservation = int(state_after["reservations"]) > reservations_before
        made_realization = int(state_after["realized"]) > realized_before
        if made_reservation and not made_realization:
            # This should already be caught as UNRESOLVED_RESERVATION, but retain an
            # explicit belt-and-suspenders guard.
            logger.critical(
                "CRYPTO_BASIS_RISK_ROLLOVER_HALTED campaign_id=%s reason=RESERVATION_WITHOUT_REALIZATION",
                campaign_id,
            )
            return

        # Successful settled probe: immediately roll to a fresh lease while the
        # promoted limits still allow it. No stale campaign token is reused.
        if made_realization:
            fast_failures = 0
            logger.warning(
                "CRYPTO_BASIS_RISK_ROLLOVER_PROMOTE_NEXT campaign_id=%s realized_total=%s realized_net_pnl=%s",
                campaign_id,
                state_after["realized"],
                state_after["realized_net_pnl"],
            )
            await asyncio.sleep(2)
            continue

        # A lease that lasted most of its authorized window and found no trade is a
        # normal expiration; roll immediately with a new id/deadline. Fast nonzero
        # exits are treated as infrastructure faults to avoid a hot crash loop.
        expected_lease_seconds = lease_minutes * 60
        if code == 0 or elapsed >= expected_lease_seconds * 0.8:
            fast_failures = 0
            await asyncio.sleep(2)
            continue

        fast_failures += 1
        logger.error(
            "CRYPTO_BASIS_RISK_ROLLOVER_FAST_FAILURE campaign_id=%s exit_code=%s elapsed_s=%.1f consecutive=%s",
            campaign_id,
            code,
            elapsed,
            fast_failures,
        )
        if elapsed > FAST_FAILURE_SECONDS:
            fast_failures = 1
        if fast_failures >= MAX_CONSECUTIVE_FAST_FAILURES:
            raise RuntimeError("basis-risk rollover hit repeated fast campaign failures")
        await asyncio.sleep(10)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
