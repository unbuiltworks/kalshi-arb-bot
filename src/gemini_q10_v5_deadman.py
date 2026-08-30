"""Independent dead-man watchdog for Gemini q10 V5.

The watchdog shares only the durable state directory and credentials with the live
executor. It does not create fresh campaigns. When a live campaign heartbeat becomes
stale it invokes the separately armed startup-recovery path, which cancels only the
known campaign orders and, only when exact residual state is provable, attempts V5's
bounded FOK rescue.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os

from src import gemini_q10_production_executor_v5 as v5
from src import gemini_q10_v5_recovery as recovery

WATCHDOG_ARM_ENV = "GEMINI_Q10_V5_WATCHDOG_ARM"
STALE_SECONDS_ENV = "GEMINI_Q10_V5_WATCHDOG_STALE_SECONDS"
POLL_SECONDS_ENV = "GEMINI_Q10_V5_WATCHDOG_POLL_SECONDS"
STALE_SECONDS_DEFAULT = 3.0
POLL_SECONDS_DEFAULT = 0.25


def watchdog_armed() -> bool:
    aid = os.getenv(v5.v1.ARM_ID_ENV, "").strip()
    return bool(v5.live_armed() and aid and os.getenv(WATCHDOG_ARM_ENV, "").strip() == aid)


def _positive_float(name: str, default: float, *, low: float, high: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not (low <= value <= high):
        raise RuntimeError(f"{name} must be between {low} and {high}")
    return value


def stale_seconds() -> float:
    return _positive_float(STALE_SECONDS_ENV, STALE_SECONDS_DEFAULT, low=0.5, high=30.0)


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
    state = v5.v1._read_state()
    if not recovery._is_open_state(state):
        return {"status":"IDLE_NO_OPEN_CAMPAIGN", "intervened":False}
    age = heartbeat_age_seconds(state)
    if age is not None and age <= stale_seconds():
        return {"status":"HEALTHY_HEARTBEAT", "intervened":False, "heartbeat_age_s":round(age,3)}
    if not watchdog_armed():
        return {"status":"STALE_HEARTBEAT_WATCHDOG_NOT_ARMED", "intervened":False, "heartbeat_age_s":age}
    if not recovery.recovery_armed():
        return {"status":"STALE_HEARTBEAT_RECOVERY_NOT_ARMED", "intervened":False, "heartbeat_age_s":age}

    result = await recovery.recover()
    return {
        "status":"DEADMAN_INTERVENTION",
        "intervened":True,
        "heartbeat_age_s":age,
        "recovery":result,
    }


async def run() -> None:
    while True:
        result = await check_once()
        print("GEMINI_Q10_V5_DEADMAN=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
        if result["status"] in {"IDLE_NO_OPEN_CAMPAIGN", "DEADMAN_INTERVENTION"}:
            return
        await asyncio.sleep(poll_seconds())


if __name__ == "__main__":
    asyncio.run(run())
