"""Independent dead-man watchdog for Gemini adaptive maker V5.3."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os

from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_v5_3_recovery as recovery53
from src import gemini_q10_v5_2_deadman as deadman52

WATCHDOG_ARM_ENV = deadman52.WATCHDOG_ARM_ENV
STALE_SECONDS_ENV = deadman52.STALE_SECONDS_ENV
POLL_SECONDS_ENV = deadman52.POLL_SECONDS_ENV
STALE_SECONDS_DEFAULT = 15.0
POLL_SECONDS_DEFAULT = 0.25


def watchdog_armed() -> bool:
    v53.install()
    aid = os.getenv(v53.v5.v1.ARM_ID_ENV, "").strip()
    return bool(
        v53.v5.live_armed()
        and aid
        and os.getenv(WATCHDOG_ARM_ENV, "").strip() == aid
    )


def _positive_float(name: str, default: float, *, low: float, high: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not (low <= value <= high):
        raise RuntimeError(f"{name} must be between {low} and {high}")
    return value


def stale_seconds() -> float:
    return _positive_float(STALE_SECONDS_ENV, STALE_SECONDS_DEFAULT, low=0.5, high=60.0)


def poll_seconds() -> float:
    return _positive_float(POLL_SECONDS_ENV, POLL_SECONDS_DEFAULT, low=0.05, high=5.0)


def heartbeat_age_seconds(state: dict | None, *, now: datetime | None = None) -> float | None:
    if not state:
        return None
    raw = state.get("heartbeat_at_utc")
    if not raw:
        return float("inf")
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return max(0.0, (ref - ts).total_seconds())


async def check_once() -> dict:
    v53.install()
    recovery53.install()
    state = v53.v5.v1._read_state()
    if not recovery53.recovery52.recovery._is_open_state(state):
        return {"status": "IDLE_NO_OPEN_CAMPAIGN", "intervened": False}
    age = heartbeat_age_seconds(state)
    if age is not None and age <= stale_seconds():
        return {
            "status": "HEALTHY_HEARTBEAT",
            "intervened": False,
            "heartbeat_age_s": round(age, 3),
        }
    if not watchdog_armed():
        return {
            "status": "STALE_HEARTBEAT_WATCHDOG_NOT_ARMED",
            "intervened": False,
            "heartbeat_age_s": age,
        }
    if not recovery53.recovery52.recovery.recovery_armed():
        return {
            "status": "STALE_HEARTBEAT_RECOVERY_NOT_ARMED",
            "intervened": False,
            "heartbeat_age_s": age,
        }

    report, _, _, _ = await recovery53.audit()
    if (
        report.exact_residual
        and report.residual_quantity == v53.ZERO
        and report.active_known_orders == 0
        and report.account_positions == 0
        and report.account_orders == 0
    ):
        return {
            "status": "STALE_HEARTBEAT_NO_DIRECTIONAL_RESIDUAL",
            "intervened": False,
            "heartbeat_age_s": age,
            "audit": report.as_dict(),
        }

    result = await recovery53.recover()
    return {
        "status": "DEADMAN_INTERVENTION",
        "intervened": True,
        "heartbeat_age_s": age,
        "recovery": result,
    }


async def run() -> None:
    terminal = {
        "IDLE_NO_OPEN_CAMPAIGN",
        "DEADMAN_INTERVENTION",
        "STALE_HEARTBEAT_NO_DIRECTIONAL_RESIDUAL",
    }
    loop = asyncio.get_running_loop()
    last_status = None
    last_print = 0.0
    while True:
        try:
            result = await check_once()
        except Exception as exc:
            result = {
                "status": "WATCHDOG_CHECK_ERROR",
                "intervened": False,
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:240],
            }
        now = loop.time()
        status = result.get("status")
        if status != last_status or now - last_print >= 30.0:
            print(
                "GEMINI_ADAPTIVE_MAKER_V5_3_DEADMAN="
                + json.dumps(result, separators=(",", ":"), default=str),
                flush=True,
            )
            last_status = status
            last_print = now
        if status in terminal:
            return
        await asyncio.sleep(poll_seconds())


if __name__ == "__main__":
    asyncio.run(run())
