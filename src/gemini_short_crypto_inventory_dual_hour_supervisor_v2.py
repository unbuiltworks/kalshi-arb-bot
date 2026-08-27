"""One-hour serialized Gemini BTC 5m + 15m inventory-maker supervisor v2.

v2 preserves the original hard live limits and switches settlement accounting to
exact order history + Gemini event resolution, so fully matched complete sets do
not deadlock waiting for a positions/settled row that may not exist.

Hard code-level ceilings:
- BTC 5m and BTC 15m only;
- q=1 max per outcome in each child canary;
- never more than one unresolved campaign across both horizons;
- max 2 executed campaigns per horizon / 4 total;
- order-entry window max 60 minutes;
- shared cumulative settled trading-loss stop at -$1.00;
- maker rebates/rewards excluded from session trading P&L;
- any ambiguous state, reconciliation mismatch, or child exception hard-stops.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
import os
import time
from typing import Any

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv5
from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3 as v5
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base5
from src import gemini_short_crypto_btc15m_inventory_maker_canary_q1 as inv15
from src import gemini_short_crypto_btc15m_inventory_maker_canary_q1_v3 as v15
from src import gemini_short_crypto_btc15m_maker_canary_q1 as base15
from src import gemini_short_crypto_inventory_local_budget as budget5
from src import gemini_short_crypto_dual_horizon_settlement_reconcile_v2 as settle
from src import gemini_short_crypto_local_budget as local_base

LIVE_ENV = "GEMINI_SHORT_CRYPTO_DUAL_HOUR_V2_LIVE_Q1"
SESSION_ID_ENV = "GEMINI_SHORT_CRYPTO_DUAL_HOUR_V2_SESSION_ID"
SESSION_ARM_ENV = "GEMINI_SHORT_CRYPTO_DUAL_HOUR_V2_SESSION_ARM"

HARD_MAX_RUNTIME_S = 60 * 60
HARD_MAX_EXECUTED_TOTAL = 4
HARD_MAX_EXECUTED_PER_HORIZON = 2
HARD_MAX_REALIZED_LOSS = D("1.00")
SETTLEMENT_TIMEOUT_S = 22 * 60
SETTLEMENT_POLL_S = 5.0
IDLE_POLL_S = 3.0
VALID_OPEN_STATUSES = {
    "MATCHED_INVENTORY_NOT_SETTLED",
    "ONE_SIDED_OR_IMBALANCED_INVENTORY_NOT_SETTLED",
}


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_BTC_DUAL_HOUR_V2 "
        + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str),
        flush=True,
    )


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    sid = os.getenv(SESSION_ID_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and sid and os.getenv(SESSION_ARM_ENV, "").strip() == sid)


def _clean(value: str, limit: int = 18) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:limit]
    return out or "session"


def child_id(session_id: str, horizon: str, attempt: int) -> str:
    return f"dh2-{_clean(session_id, 9)}-{horizon}-{int(attempt):03d}"


def loss_cap_hit(pnl: D) -> bool:
    return D(str(pnl)) <= -HARD_MAX_REALIZED_LOSS


def settlement_pending(exc: BaseException) -> bool:
    return "settlement not posted yet" in str(exc).lower()


async def _wait_for_settlement() -> dict[str, Any]:
    deadline = time.monotonic() + SETTLEMENT_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            return await settle.reconcile()
        except RuntimeError as exc:
            if not settlement_pending(exc):
                raise
            emit(
                "WAITING_FOR_SETTLEMENT",
                seconds_remaining=max(0, int(deadline - time.monotonic())),
                order_entry=False,
            )
            await asyncio.sleep(SETTLEMENT_POLL_S)
    raise RuntimeError("dual-hour-v2 settlement timeout with unresolved reservation")


async def _ready_5m() -> dict[str, Any] | None:
    event = await base5.discover_btc5m()
    if event is None or event.seconds_to_expiry < inv5.MIN_SECONDS_TO_EXPIRY + v5.LIVE_ENTRY_BUFFER_S:
        return None
    bid, _, ask, _ = await v5.rest_fresh_top(event.symbol)
    model = base5.pair_model(bid, ask)
    if model["modeled_edge"] < inv5.ENTRY_PAIR_EDGE:
        return None
    return {"horizon": "5m", "event": event, "edge": model["modeled_edge"]}


async def _ready_15m() -> dict[str, Any] | None:
    event = await base15.discover_btc15m()
    if event is None or event.seconds_to_expiry < inv15.MIN_SECONDS_TO_EXPIRY + v15.LIVE_ENTRY_BUFFER_S:
        return None
    bid, _, ask, _ = await v15.rest_fresh_top(event.symbol)
    model = base15.pair_model(bid, ask)
    if model["modeled_edge"] < inv15.ENTRY_PAIR_EDGE:
        return None
    return {"horizon": "15m", "event": event, "edge": model["modeled_edge"]}


async def _readiness(counts: dict[str, int]) -> list[dict[str, Any]]:
    tasks = []
    names = []
    if counts["5m"] < HARD_MAX_EXECUTED_PER_HORIZON:
        tasks.append(_ready_5m())
        names.append("5m")
    if counts["15m"] < HARD_MAX_EXECUTED_PER_HORIZON:
        tasks.append(_ready_15m())
        names.append("15m")
    if not tasks:
        return []
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    ready: list[dict[str, Any]] = []
    for name, item in zip(names, raw):
        if isinstance(item, Exception):
            emit("READINESS_ERROR", horizon=name, exception_type=type(item).__name__, order_entry=False)
            continue
        if item is not None:
            ready.append(item)
    return ready


def _choose(
    ready: list[dict[str, Any]], counts: dict[str, int], last_horizon: str | None
) -> dict[str, Any] | None:
    if not ready:
        return None
    ordered = sorted(
        ready,
        key=lambda r: (
            counts[r["horizon"]],
            0 if r["horizon"] != last_horizon else 1,
            0 if r["horizon"] == "15m" else 1,
        ),
    )
    return ordered[0]


async def _run_child(horizon: str, cid: str) -> None:
    if horizon == "5m":
        base5.fresh_top = v5.rest_fresh_top
        os.environ[inv5.LIVE_ENV] = "YES"
        os.environ[inv5.CANARY_ID_ENV] = cid
        os.environ[inv5.CANARY_ARM_ENV] = cid
        await inv5.run()
        return
    if horizon == "15m":
        base15.fresh_top = v15.rest_fresh_top
        os.environ[inv15.LIVE_ENV] = "YES"
        os.environ[inv15.CANARY_ID_ENV] = cid
        os.environ[inv15.CANARY_ARM_ENV] = cid
        await inv15.run()
        return
    raise RuntimeError(f"unsupported horizon {horizon}")


async def run() -> None:
    if not live_armed():
        emit("DISARMED", order_entry=False, realized_pnl="0")
        return
    if local_base.local_open_state() is not None:
        raise RuntimeError("dual-hour-v2 start blocked by unresolved local reservation")
    await budget5.assert_clean_account()

    session_id = _clean(os.environ[SESSION_ID_ENV], 18)
    started = time.monotonic()
    session_realized = D("0")
    attempts = 0
    counts = {"5m": 0, "15m": 0}
    last_horizon: str | None = None
    emit(
        "START",
        session_id=session_id,
        order_entry_window_seconds=HARD_MAX_RUNTIME_S,
        max_executed_total=HARD_MAX_EXECUTED_TOTAL,
        max_executed_per_horizon=HARD_MAX_EXECUTED_PER_HORIZON,
        max_realized_loss=str(HARD_MAX_REALIZED_LOSS),
        q_per_outcome=1,
        serialized=True,
        maker_rebate_included=False,
        order_entry=True,
    )

    while time.monotonic() - started < HARD_MAX_RUNTIME_S:
        total = counts["5m"] + counts["15m"]
        if total >= HARD_MAX_EXECUTED_TOTAL:
            emit("STOP", reason="MAX_EXECUTED_TOTAL", counts=counts, realized_pnl=str(session_realized), order_entry=False)
            return
        if loss_cap_hit(session_realized):
            emit("STOP", reason="REALIZED_LOSS_CAP", counts=counts, realized_pnl=str(session_realized), order_entry=False)
            return
        await budget5.assert_clean_account()

        choice = _choose(await _readiness(counts), counts, last_horizon)
        if choice is None:
            emit("WAITING_FOR_ELIGIBLE_HORIZON", counts=counts, realized_pnl=str(session_realized), order_entry=False)
            await asyncio.sleep(IDLE_POLL_S)
            continue

        horizon = str(choice["horizon"])
        attempts += 1
        cid = child_id(session_id, horizon, attempts)
        event = choice["event"]
        emit(
            "CAMPAIGN_ATTEMPT_START",
            attempt=attempts,
            horizon=horizon,
            child_canary_id=cid,
            market=event.ticker,
            seconds_to_expiry=event.seconds_to_expiry,
            observed_edge=str(choice["edge"]),
            counts=counts,
            realized_pnl=str(session_realized),
            order_entry=True,
        )
        try:
            await _run_child(horizon, cid)
        except Exception as exc:
            emit(
                "HARD_HALT",
                reason="CHILD_EXCEPTION",
                horizon=horizon,
                exception_type=type(exc).__name__,
                child_canary_id=cid,
                order_entry=True,
            )
            raise

        local = local_base.local_open_state()
        if local is None:
            emit(
                "CAMPAIGN_NO_OPEN_INVENTORY",
                horizon=horizon,
                child_canary_id=cid,
                counts=counts,
                realized_pnl=str(session_realized),
                order_entry=False,
            )
            last_horizon = horizon
            await asyncio.sleep(1.0)
            continue

        status = str(local.get("status") or "")
        if status not in VALID_OPEN_STATUSES:
            emit(
                "HARD_HALT",
                reason="AMBIGUOUS_LOCAL_STATUS",
                horizon=horizon,
                status=status,
                trade_id=local.get("trade_id"),
                order_entry=False,
            )
            raise RuntimeError(f"dual-hour-v2 ambiguous local status: {status}")

        counts[horizon] += 1
        result = await _wait_for_settlement()
        if result.get("horizon") != horizon:
            raise RuntimeError(
                f"settlement horizon mismatch expected={horizon} got={result.get('horizon')}"
            )
        pnl = D(str(result.get("net_profit") or "0"))
        session_realized += pnl
        emit(
            "CAMPAIGN_SETTLED",
            horizon=horizon,
            counts=counts,
            trade_id=result.get("trade_id"),
            yes_fill=result.get("yes_fill"),
            no_fill=result.get("no_fill"),
            matched_quantity=result.get("matched_quantity"),
            residual_quantity=result.get("residual_quantity"),
            resolution_side=result.get("resolution_side"),
            maker_fees=result.get("maker_fees"),
            campaign_net_profit=str(pnl),
            session_realized_pnl=str(session_realized),
            maker_rebate_included=False,
            local_reservation_cleared=result.get("local_reservation_cleared"),
            order_entry=False,
        )
        if not result.get("local_reservation_cleared"):
            raise RuntimeError("dual-hour-v2 settlement reconciliation did not clear reservation")
        last_horizon = horizon
        if loss_cap_hit(session_realized):
            emit("STOP", reason="REALIZED_LOSS_CAP", counts=counts, realized_pnl=str(session_realized), order_entry=False)
            return

    emit(
        "STOP",
        reason="MAX_ORDER_ENTRY_RUNTIME",
        attempts=attempts,
        counts=counts,
        realized_pnl=str(session_realized),
        order_entry=False,
    )


if __name__ == "__main__":
    asyncio.run(run())
