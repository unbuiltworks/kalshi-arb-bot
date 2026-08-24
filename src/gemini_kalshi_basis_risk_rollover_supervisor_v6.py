"""Continuous v6 basis rollover with bounded concurrent fully-paired risk.

ETH, SOL and XRP run as separate q=1 scan lanes.  A lane may coexist with other
BOTH_FILLED positions awaiting settlement, but v6 permits only one in-flight/unpaired
reservation at a time and caps aggregate open paired risk below the existing $5 loss
budget.  Any new/unreviewed realized loss or unresolved execution incident stops fresh
entry.  The shared guaranteed-arbitrage lock remains unchanged.
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
ASSETS = dynamic_policy.ELIGIBLE_ASSETS


def _lease_minutes() -> int:
    raw = os.getenv(LEASE_MINUTES_ENV, str(DEFAULT_LEASE_MINUTES)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_LEASE_MINUTES
    return max(MIN_LEASE_MINUTES, min(value, MAX_LEASE_MINUTES))


def _campaign_id(now: datetime, asset: str, sequence: int) -> str:
    return f"b6{asset.lower()}{now:%m%d%H%M}{sequence % 100:02d}"


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
    with child_v2._connect() as conn:
        return child_v6.promoted_state(conn)


def _log_state(prefix: str, state: dict[str, Any], reason: str | None = None, *, asset: str | None = None) -> None:
    settled_trades = int(state["realized"]) - int(state.get("no_fill_releases", 0))
    logger.warning(
        "%s asset=%s reservations=%s settled_trades=%s no_fill_releases=%s realized_losses=%s "
        "realized_loss_usd=%s remaining_loss_budget_usd=%s realized_net_pnl=%s "
        "open_paired=%s open_paired_reserved_usd=%s inflight_unpaired=%s reason=%s",
        prefix,
        asset or "ALL",
        state["reservations"],
        settled_trades,
        state.get("no_fill_releases", 0),
        state["realized_losses"],
        state["realized_loss_usd"],
        state["remaining_loss_budget_usd"],
        state["realized_net_pnl"],
        state.get("open_paired_count", 0),
        state.get("open_paired_reserved_usd", 0),
        len(state.get("inflight_or_unpaired", [])),
        reason or "NONE",
    )


async def _run_one_lease(asset: str, sequence: int, lease_minutes: int) -> tuple[int, float, str]:
    now = datetime.now(timezone.utc)
    campaign_id = _campaign_id(now, asset, sequence)
    deadline = now + timedelta(minutes=lease_minutes)
    env = os.environ.copy()
    env[child.BASIS_ARM_ENV] = "YES"
    env[child.BASIS_RECONCILED_ENV] = "YES"
    env[child.BASIS_DEADLINE_ENV] = deadline.isoformat()
    env["CRYPTO_BASIS_RISK_CAMPAIGN_ID"] = campaign_id
    env[dynamic_policy.ASSET_SCOPE_ENV] = asset
    started = time.monotonic()
    logger.warning(
        "CRYPTO_BASIS_RISK_V6_LEASE_START asset=%s campaign_id=%s deadline=%s "
        "execution_sequence=KALSHI_THEN_GEMINI q=1",
        asset, campaign_id, deadline.isoformat(),
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
        "CRYPTO_BASIS_RISK_V6_LEASE_END asset=%s campaign_id=%s exit_code=%s elapsed_s=%.1f",
        asset, campaign_id, code, elapsed,
    )
    return code, elapsed, campaign_id


def _globally_terminal(reason: str) -> bool:
    return reason in {
        "NEW_OR_UNREVIEWED_REALIZED_LOSS_HARD_STOP",
        "APPROVED_LOSS_BUDGET_EXHAUSTED",
        "PROMOTED_PROBE_LIMIT_REACHED",
        "REMAINING_LOSS_BUDGET_CANNOT_COVER_NEXT_PAIRED_POSITION",
    }


async def _asset_loop(asset: str, lease_minutes: int) -> None:
    sequence = 0
    fast_failures = 0
    while True:
        before = _state()
        allowed, reason = child_v6.automatic_rollover_allowed(before)
        if not allowed:
            if _globally_terminal(reason):
                _log_state("CRYPTO_BASIS_RISK_V6_ASSET_STOP", before, reason, asset=asset)
                return
            # Another lane may be in its millisecond-scale execution window or all
            # paired-risk slots may be occupied until the next 15m settlement.
            _log_state("CRYPTO_BASIS_RISK_V6_ASSET_WAIT", before, reason, asset=asset)
            await asyncio.sleep(1.0)
            continue

        sequence += 1
        reservations_before = int(before["reservations"])
        realized_before = int(before["realized"])
        releases_before = int(before.get("no_fill_releases", 0))
        code, elapsed, campaign_id = await _run_one_lease(asset, sequence, lease_minutes)

        after = _state()
        allowed_after, reason_after = child_v6.automatic_rollover_allowed(after)
        _log_state("CRYPTO_BASIS_RISK_V6_POST_LEASE", after, reason_after, asset=asset)
        if not allowed_after and _globally_terminal(reason_after):
            logger.warning(
                "CRYPTO_BASIS_RISK_V6_HALTED asset=%s campaign_id=%s reason=%s",
                asset, campaign_id, reason_after,
            )
            return

        made_reservation = int(after["reservations"]) > reservations_before
        made_resolution = int(after["realized"]) > realized_before
        made_release = int(after.get("no_fill_releases", 0)) > releases_before
        if made_reservation and not made_resolution:
            # A successful BOTH_FILLED trade can intentionally remain unresolved while
            # awaiting settlement.  Anything else must be classified by current state.
            open_ids = {row["trade_id"] for row in after.get("open_paired", [])}
            before_ids = {row["trade_id"] for row in before.get("open_paired", [])}
            if not (open_ids - before_ids):
                logger.critical(
                    "CRYPTO_BASIS_RISK_V6_HALTED asset=%s campaign_id=%s "
                    "reason=RESERVATION_WITHOUT_PAIRED_OR_REALIZED_RESOLUTION",
                    asset, campaign_id,
                )
                return

        if made_release or made_resolution:
            fast_failures = 0
            await asyncio.sleep(1.0 if made_release else 2.0)
            continue

        expected_lease_seconds = lease_minutes * 60
        if code == 0 or elapsed >= expected_lease_seconds * 0.8:
            fast_failures = 0
            await asyncio.sleep(2)
            continue
        fast_failures += 1
        logger.error(
            "CRYPTO_BASIS_RISK_V6_FAST_FAILURE asset=%s campaign_id=%s exit_code=%s elapsed_s=%.1f consecutive=%s",
            asset, campaign_id, code, elapsed, fast_failures,
        )
        if elapsed > FAST_FAILURE_SECONDS:
            fast_failures = 1
        if fast_failures >= MAX_CONSECUTIVE_FAST_FAILURES:
            raise RuntimeError(f"v6 basis rollover hit repeated fast campaign failures for {asset}")
        await asyncio.sleep(10)


async def main_async() -> None:
    _validate_outer_authorization()
    lease_minutes = _lease_minutes()
    initial = _state()
    allowed, reason = child_v6.automatic_rollover_allowed(initial)
    _log_state("CRYPTO_BASIS_RISK_V6_BOOT", initial, reason)
    if not allowed and _globally_terminal(reason):
        return
    logger.warning(
        "CRYPTO_BASIS_RISK_V6_ROLLOVER_ENABLED lease_minutes=%s assets=%s parallel_asset_lanes=%s "
        "q=1 tranche_usd=%s turnover_probe_limit=%s approved_budget_usd=%s "
        "already_realized_loss_usd=%s remaining_loss_budget_usd=%s max_open_paired=%s "
        "min_gap_bps=%s min_locked_profit=%s execution_sequence=KALSHI_THEN_GEMINI "
        "one_inflight_unpaired=true new_loss_hard_stop=true guaranteed_arb=false shared_arb_lock_changed=false",
        lease_minutes,
        ",".join(ASSETS),
        len(ASSETS),
        child.INITIAL_TRANCHE_USD,
        child_v6.PROMOTED_PROBE_LIMIT,
        child.APPROVED_BUDGET,
        initial["realized_loss_usd"],
        initial["remaining_loss_budget_usd"],
        child_v6.MAX_OPEN_PAIRED_RISK,
        dynamic_policy.DYNAMIC_MIN_GAP_BPS,
        dynamic_policy.DYNAMIC_MIN_LOCKED_PROFIT,
    )
    tasks = [
        asyncio.create_task(_asset_loop(asset, lease_minutes), name=f"basis-v6-{asset.lower()}")
        for asset in ASSETS
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
