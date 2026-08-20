"""Venue-idempotent one-shot helpers for the q=1 maker LIVE_PROBE."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time

from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_live_probe_q1_safe as safe

STATE_ROOT = Path("/tmp/maker-live-probe-venue-once")
ORDER_TTL_S = 20
logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_live_probe_q1_venue_once")


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
    payload["expiration_time"] = int(time.time()) + ORDER_TTL_S
    return payload


async def wait_candidate_safe(*, pair, fee_meta, timeout_s: float = 120.0):
    """Same live candidate gate as base, but timeout is a clean result and diagnostic."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=50_000)
    stats = base.MakerStats(asset=base.ASSET)
    tasks = [
        asyncio.create_task(base._gemini_stream(pair.gemini_symbol, queue, stats)),
        asyncio.create_task(base._kalshi_stream(pair.kalshi_ticker, queue, stats)),
    ]
    g = None
    k = None
    counters = {"events": 0, "missing_pair": 0, "skew_rejects": 0, "stale_rejects": 0, "economic_rejects": 0}
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                kind, receive_ms, payload = await asyncio.wait_for(
                    queue.get(), timeout=min(1.0, max(0.01, remaining))
                )
            except asyncio.TimeoutError:
                if time.monotonic() >= deadline:
                    break
                continue
            counters["events"] += 1
            if kind == "gemini":
                g = payload
            elif kind == "kalshi":
                k = payload
            if g is None or k is None:
                counters["missing_pair"] += 1
                continue
            now_ms = int(time.time() * 1000)
            if abs(g.ts_ms - k.ts_ms) > base.MAX_OPEN_SNAPSHOT_SKEW_MS:
                counters["skew_rejects"] += 1
                continue
            if max(now_ms - g.ts_ms, now_ms - k.ts_ms) > base.MAX_OPEN_BOOK_AGE_MS:
                counters["stale_rejects"] += 1
                continue
            candidate = base.candidate_from_books(gemini=g, kalshi=k, fee_meta=fee_meta, now_ms=now_ms)
            if candidate is None:
                counters["economic_rejects"] += 1
                continue
            logger.warning("MAKER_LIVE_PROBE_CANDIDATE_WAIT_SUMMARY %s", json.dumps({
                **counters,
                "found": True,
                "open_snapshot_skew_ms": abs(g.ts_ms - k.ts_ms),
                "max_open_snapshot_skew_ms": base.MAX_OPEN_SNAPSHOT_SKEW_MS,
                "target_profit": str(base.TARGET_PROFIT),
                "policy": base.POLICY,
            }, separators=(",", ":")))
            return candidate, g, k, queue, tasks
        logger.warning("MAKER_LIVE_PROBE_CANDIDATE_WAIT_SUMMARY %s", json.dumps({
            **counters,
            "found": False,
            "max_open_snapshot_skew_ms": base.MAX_OPEN_SNAPSHOT_SKEW_MS,
            "target_profit": str(base.TARGET_PROFIT),
            "policy": base.POLICY,
        }, separators=(",", ":")))
        return None, g, k, queue, tasks
    except Exception:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def install() -> None:
    safe._maybe_create_arm = maybe_create_arm
    safe._claim_before_order = claim_before_order
    safe.kalshi_maker_payload = maker_payload_with_expiry
    base._wait_candidate = wait_candidate_safe
