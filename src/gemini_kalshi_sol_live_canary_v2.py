"""Persistent, idempotent SOL 15m real-money canary.

This v2 wrapper keeps the proven one-contract Gemini/Kalshi dual-FOK transport but
replaces ephemeral one-shot authorization with a persistent Railway-volume claim.
It is fail-closed unless a volume is mounted, both software live gates are enabled,
and the explicit arm window is still open.

Safety properties:
- SOL only, quantity exactly 1;
- >= $0.03 modeled after-fee locked profit;
- >= 10 bp whole-episode double-boundary clearance;
- exact original-price/depth survival at 50 ms;
- entry/current cross-venue quote skew <= 50 ms;
- planned pair capital <= $1 from the scanner command, hard experiment ceiling $5;
- persistent arm token and atomic persistent attempt claim on a Railway volume;
- deterministic venue client-order IDs derived from one run ID;
- once an attempt is claimed, a restart/redeploy cannot re-arm that run;
- existing ambiguity and one-leg-fill hard-halt/flatten behavior is preserved.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any

from src import gemini_kalshi_micro_live_executor_20bp_safe as safe
from src.gemini_kalshi_live_probe_guard import DEFAULT_MIN_LOCKED_PROFIT, MAX_QUOTE_SKEW_MS
from src.gemini_prediction_auth import verify_authenticated_ws

D = Decimal
executor = safe.executor
readiness = safe.readiness
logger = logging.getLogger("kalshi_arb.gemini_kalshi_sol_live_canary_v2")

CANARY_ASSET = "SOL"
CANARY_MIN_DOUBLE_BOUNDARY_BPS = D("10")
CANARY_EXECUTION_DELAY_MS = 50
CANARY_PLANNED_CAPITAL_LIMIT_USD = D("1")
CANARY_HARD_BUDGET_USD = D("5")
VOLUME_ENV = "RAILWAY_VOLUME_MOUNT_PATH"
ARM_ENV = "SOL_CANARY_V2_ARM"
RUN_ID_ENV = "SOL_CANARY_V2_RUN_ID"
DEFAULT_STATE_DIR = "/data"

_ORIGINAL_BUILD_PAYLOADS = executor.build_original_limit_payloads
_ORIGINAL_SUBMIT_DUAL_PROBE = executor.submit_dual_probe


def strict_execution_gate_10bp_50ms(
    *,
    delay_ms: int,
    readiness_20bp: bool,
    quantity: Decimal,
    locked_profit: Decimal,
    entry_quote_skew_ms: int | None,
    current_quote_skew_ms: int | None,
) -> bool:
    return bool(
        int(delay_ms) == CANARY_EXECUTION_DELAY_MS
        and readiness_20bp
        and D(quantity) == D("1")
        and D(locked_profit) >= DEFAULT_MIN_LOCKED_PROFIT
        and entry_quote_skew_ms is not None
        and int(entry_quote_skew_ms) <= MAX_QUOTE_SKEW_MS
        and current_quote_skew_ms is not None
        and int(current_quote_skew_ms) <= MAX_QUOTE_SKEW_MS
    )


def _clean_run_id(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(raw).strip()).strip("-")
    if not value or len(value) > 32:
        raise ValueError("SOL canary v2 run id must be 1-32 safe characters")
    return value


def _state_dir() -> Path:
    mount = os.getenv(VOLUME_ENV, "").strip()
    if not mount:
        raise RuntimeError("SOL canary v2 requires a Railway persistent volume")
    path = Path(mount)
    if not path.is_absolute():
        raise RuntimeError("Railway volume mount path must be absolute")
    return path


def _paths(run_id: str) -> tuple[Path, Path, Path]:
    root = _state_dir()
    safe_id = _clean_run_id(run_id)
    return (
        root / f"{safe_id}.arm",
        root / f"{safe_id}.attempt.json",
        root / f"{safe_id}.result.json",
    )


def deterministic_client_ids(run_id: str) -> tuple[str, str]:
    safe_id = _clean_run_id(run_id)
    base = f"solc2-{safe_id}"
    return base + "-g", base + "-k"


def _build_deterministic_payloads(plan) -> tuple[dict[str, Any], dict[str, Any]]:
    run_id = _clean_run_id(os.environ[RUN_ID_ENV])
    gemini, kalshi = _ORIGINAL_BUILD_PAYLOADS(plan)
    gemini_id, kalshi_id = deterministic_client_ids(run_id)
    gemini["clientOrderId"] = gemini_id
    kalshi["client_order_id"] = kalshi_id
    return gemini, kalshi


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _persistent_arm_path() -> Path:
    run_id = _clean_run_id(os.environ[RUN_ID_ENV])
    arm, _, _ = _paths(run_id)
    return arm


def _attempt_already_claimed() -> bool:
    run_id = _clean_run_id(os.environ[RUN_ID_ENV])
    _, claim, result = _paths(run_id)
    return claim.exists() or result.exists()


def _persistent_armed_once(live_requested: bool, token_path: Path) -> bool:
    return bool(
        executor.live_probe_authorized(live_requested)
        and token_path == _persistent_arm_path()
        and token_path.is_file()
        and not _attempt_already_claimed()
    )


def _persistent_consume_arm_token(token_path: Path) -> bool:
    """Atomically claim this run on persistent storage before any venue submission."""
    run_id = _clean_run_id(os.environ[RUN_ID_ENV])
    arm, claim, result = _paths(run_id)
    if token_path != arm or not arm.is_file() or claim.exists() or result.exists():
        return False
    claim.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    try:
        payload = {
            "run_id": run_id,
            "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "ATTEMPT_CLAIMED_BEFORE_SUBMISSION",
            "quantity": "1",
            "planned_capital_limit_usd": str(CANARY_PLANNED_CAPITAL_LIMIT_USD),
            "hard_budget_usd": str(CANARY_HARD_BUDGET_USD),
        }
        os.write(fd, json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        arm.unlink()
    except FileNotFoundError:
        pass
    return True


async def _submit_and_persist_result(*args, **kwargs) -> dict[str, Any]:
    run_id = _clean_run_id(os.environ[RUN_ID_ENV])
    _, claim, result_path = _paths(run_id)
    if not claim.is_file():
        raise PermissionError("persistent SOL canary attempt was not claimed")
    result = await _ORIGINAL_SUBMIT_DUAL_PROBE(*args, **kwargs)
    _write_json_atomic(
        result_path,
        {
            "run_id": run_id,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "result": result,
        },
    )
    return result


async def _readonly_authenticated_preflight() -> None:
    kalshi = executor.KalshiV2Executor()
    try:
        report = await executor._preflight(
            kalshi,
            live_requested=False,
            token_path=Path("/tmp/sol-canary-v2-preflight-no-arm"),
        )
        gemini_ws_ok = await verify_authenticated_ws(timeout=5.0)
        gemini_funded = D(str(report.get("gemini_usd_available") or "0")) >= D("1")
        kalshi_funded = D(str(report.get("kalshi_usd_available") or "0")) >= D("1")
        logger.info(
            "SOL_CANARY_V2_AUTH_PREFLIGHT %s",
            json.dumps({
                "gemini_private_rest_authenticated": True,
                "gemini_authenticated_ws": bool(gemini_ws_ok),
                "gemini_terms_accepted": bool(report.get("gemini_terms_accepted")),
                "kalshi_production_authenticated": bool(report.get("kalshi_production_authenticated")),
                "gemini_funded_ge_1usd": gemini_funded,
                "kalshi_funded_ge_1usd": kalshi_funded,
                "order_entry": False,
            }, separators=(",", ":")),
        )
        if not gemini_ws_ok or not gemini_funded or not kalshi_funded:
            raise RuntimeError("SOL canary v2 authenticated/funding preflight failed")
    finally:
        await kalshi.close()


def _maybe_create_persistent_arm(deadline_utc: datetime) -> bool:
    if os.getenv(ARM_ENV, "NO").strip().upper() != "YES":
        return False
    if not executor.live_probe_authorized(executor._LIVE_REQUESTED):
        return False
    if datetime.now(timezone.utc) > deadline_utc:
        logger.warning("SOL_CANARY_V2_ARM_REFUSED reason=deadline_expired deadline=%s", deadline_utc.isoformat())
        return False
    run_id = _clean_run_id(os.environ[RUN_ID_ENV])
    arm, claim, result = _paths(run_id)
    arm.parent.mkdir(parents=True, exist_ok=True)
    if claim.exists() or result.exists():
        logger.warning("SOL_CANARY_V2_ARM_REFUSED reason=run_already_claimed run_id=%s", run_id)
        return False
    if arm.exists():
        return True
    try:
        fd = os.open(arm, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return True
    try:
        os.write(fd, (run_id + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    logger.warning("SOL_CANARY_V2_PERSISTENT_ARM_CREATED run_id=%s path=%s", run_id, arm)
    return True


def install_canary_v2() -> None:
    readiness.MIN_DOUBLE_BOUNDARY_BPS = CANARY_MIN_DOUBLE_BOUNDARY_BPS
    executor.strict_execution_gate = strict_execution_gate_10bp_50ms
    safe.install_safe_executor()
    executor.build_original_limit_payloads = _build_deterministic_payloads
    executor._arm_token_path = _persistent_arm_path
    executor._armed_once = _persistent_armed_once
    executor._consume_arm_token = _persistent_consume_arm_token
    executor.submit_dual_probe = _submit_and_persist_result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--arm-deadline-utc")
    live_args, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]

    run_id = _clean_run_id(os.getenv(RUN_ID_ENV, ""))
    volume = _state_dir()  # Fail closed before private live mode if persistent storage is absent.
    deadline = datetime.fromisoformat(str(live_args.arm_deadline_utc).replace("Z", "+00:00")) if live_args.arm_deadline_utc else datetime(1970, 1, 1, tzinfo=timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    deadline = deadline.astimezone(timezone.utc)

    executor._LIVE_REQUESTED = bool(live_args.live) or os.getenv(ARM_ENV, "NO").strip().upper() == "YES"
    asyncio.run(_readonly_authenticated_preflight())
    install_canary_v2()
    armed = _maybe_create_persistent_arm(deadline)
    gemini_id, kalshi_id = deterministic_client_ids(run_id)
    logger.warning(
        "SOL_CANARY_V2_ENABLED run_id=%s volume=%s live_requested=%s env_gate=%s armed=%s "
        "attempt_already_claimed=%s quantity=1 min_profit=%s min_double_boundary_bps=%s "
        "execution_delay_ms=%s planned_capital_limit_usd=%s hard_budget_usd=%s "
        "gemini_client_order_id=%s kalshi_client_order_id=%s restart_policy_expected=NEVER",
        run_id,
        volume,
        executor._LIVE_REQUESTED,
        executor.live_probe_authorized(executor._LIVE_REQUESTED),
        armed,
        _attempt_already_claimed(),
        DEFAULT_MIN_LOCKED_PROFIT,
        CANARY_MIN_DOUBLE_BOUNDARY_BPS,
        CANARY_EXECUTION_DELAY_MS,
        CANARY_PLANNED_CAPITAL_LIMIT_USD,
        CANARY_HARD_BUDGET_USD,
        gemini_id,
        kalshi_id,
    )
    readiness.guarded.main()


if __name__ == "__main__":
    main()
