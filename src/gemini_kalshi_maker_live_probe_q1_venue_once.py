"""Venue-idempotent one-shot helpers for the q=1 maker LIVE_PROBE.

Railway deployments do not need a persistent volume for the first live mechanics
probe. We use an ephemeral per-process arm/claim plus Kalshi's documented
client_order_id deduplication. The live deployment is immediately disarmed after
it starts, restartPolicy is NEVER, and the maker order receives a short exchange-
side expiration so an orphaned resting order cannot persist indefinitely.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_live_probe_q1_safe as safe

STATE_ROOT = Path("/tmp/maker-live-probe-venue-once")
ORDER_TTL_S = 20


def _paths(run_id: str) -> tuple[Path, Path, Path]:
    safe_id = base._clean_run_id(run_id)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    return (
        STATE_ROOT / f"maker-{safe_id}.arm",
        STATE_ROOT / f"maker-{safe_id}.attempt.json",
        STATE_ROOT / f"maker-{safe_id}.result.json",
    )


def maybe_create_arm(run_id: str) -> Path | None:
    if not base._live_software_authorized():
        return None
    deadline = base._parse_deadline(os.getenv(base.DEADLINE_ENV, ""))
    if datetime.now(timezone.utc) > deadline:
        raise RuntimeError("maker live probe arm deadline has expired")
    arm, claim, result = _paths(run_id)
    if claim.exists() or result.exists():
        raise RuntimeError("maker live probe run id was already claimed/completed in this process")
    if not arm.exists():
        fd = os.open(arm, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (run_id + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    return arm


def claim_before_order(run_id: str) -> Path:
    arm, claim, result = _paths(run_id)
    if result.exists() or claim.exists() or not arm.exists():
        raise PermissionError("maker live probe one-shot authorization unavailable")
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PermissionError("maker live probe run already claimed") from exc
    try:
        payload = {
            "run_id": run_id,
            "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "EPHEMERAL_PROCESS_CLAIM_BEFORE_KALSHI_POST",
            "asset": base.ASSET,
            "quantity_max": "1",
            "settlement_status": base.SETTLEMENT_STATUS,
            "venue_idempotency": "KALSHI_CLIENT_ORDER_ID",
        }
        os.write(fd, json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        arm.unlink()
    except FileNotFoundError:
        pass
    return result


def maker_payload_with_expiry(*, ticker, direction, maker_price, client_order_id):
    payload = base.kalshi_maker_payload(
        ticker=ticker,
        direction=direction,
        maker_price=maker_price,
        client_order_id=client_order_id,
    )
    # Exchange-side kill switch. The local rest loop is only 10s; 20s leaves
    # enough room for cancel/reconcile while bounding any orphan after a crash.
    payload["expiration_time"] = int(time.time()) + ORDER_TTL_S
    return payload


def install() -> None:
    # safe.run_once resolves these globals at runtime.
    safe._maybe_create_arm = maybe_create_arm
    safe._claim_before_order = claim_before_order
    safe.kalshi_maker_payload = maker_payload_with_expiry
