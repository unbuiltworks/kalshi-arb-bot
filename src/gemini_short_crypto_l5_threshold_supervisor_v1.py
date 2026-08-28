"""Bounded Gemini BTC 5m + 15m l5-threshold q=1 live supervisor.

This is an intentionally small live probe of the simplest hypothesis suggested
by the observed l5 activity: when the market itself moves one outcome above a
60% implied-probability threshold, bias the resting maker quote one tick toward
that favored side while continuing to quote the opposite side.

The proven Gemini inventory canaries still own actual order placement, fill
handling, cancellation, durable reservation, and authoritative settlement.
This wrapper only changes *when* a campaign is eligible and, when safe, improves
the favored quote by one passive tick.

Hard code-level ceilings for v1:
- BTC 5m + BTC 15m only;
- q=1 maximum per outcome in each campaign;
- one unresolved campaign at a time;
- maximum one executed campaign per horizon / two total;
- maximum 60-minute order-entry window;
- cumulative settled trading-loss stop at -$1.00;
- both initial quotes must be inside Gemini's 20c-80c maker-rebate band;
- conservative matched-pair edge must remain >=1c after modeled maker fees;
- maker rebates/rewards are never counted as realized trading P&L;
- ambiguous reconciliation or child exception hard-stops;
- no automatic restart.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
import os
import time
from typing import Any, Callable

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv5
from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3 as v5
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base5
from src import gemini_short_crypto_btc15m_inventory_maker_canary_q1 as inv15
from src import gemini_short_crypto_btc15m_inventory_maker_canary_q1_v3 as v15
from src import gemini_short_crypto_btc15m_maker_canary_q1 as base15
from src import gemini_short_crypto_inventory_local_budget as budget5
from src import gemini_short_crypto_dual_horizon_settlement_reconcile_v2 as settle
from src import gemini_short_crypto_local_budget as local_base
from src.gemini_l5_threshold_model import (
    DEFAULT_PAIR_FLOOR,
    DEFAULT_THRESHOLD,
    QuotePlan,
    maker_rebate_estimate,
    plan_threshold_quotes,
)

LIVE_ENV = "GEMINI_L5_THRESHOLD_LIVE_Q1"
SESSION_ID_ENV = "GEMINI_L5_THRESHOLD_SESSION_ID"
SESSION_ARM_ENV = "GEMINI_L5_THRESHOLD_SESSION_ARM"

HARD_MAX_RUNTIME_S = 60 * 60
HARD_MAX_EXECUTED_TOTAL = 2
HARD_MAX_EXECUTED_PER_HORIZON = 1
HARD_MAX_REALIZED_LOSS = D("1.00")
SETTLEMENT_TIMEOUT_S = 22 * 60
SETTLEMENT_POLL_S = 5.0
IDLE_POLL_S = 2.0
VALID_OPEN_STATUSES = {
    "MATCHED_INVENTORY_NOT_SETTLED",
    "ONE_SIDED_OR_IMBALANCED_INVENTORY_NOT_SETTLED",
    "FILL_SIGNAL_RECONCILIATION_PENDING",
}


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_L5_THRESHOLD_V1 "
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
    return f"l5v1-{_clean(session_id, 8)}-{horizon}-{int(attempt):03d}"


def loss_cap_hit(pnl: D) -> bool:
    return D(str(pnl)) <= -HARD_MAX_REALIZED_LOSS


def settlement_pending(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "settlement not posted yet",
            "missing exact canary order history",
            "open reservation has zero authoritative fills",
        )
    )


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
    raise RuntimeError("l5-threshold-v1 settlement timeout with unresolved reservation")


def _plan(bid: D, ask: D, tick: D) -> QuotePlan:
    return plan_threshold_quotes(
        bid,
        ask,
        threshold=DEFAULT_THRESHOLD,
        pair_floor=DEFAULT_PAIR_FLOOR,
        price_increment=tick,
    )


async def _ready_5m() -> dict[str, Any] | None:
    event = await base5.discover_btc5m()
    if event is None or event.seconds_to_expiry < inv5.MIN_SECONDS_TO_EXPIRY + v5.LIVE_ENTRY_BUFFER_S:
        return None
    bid, _, ask, _ = await v5.rest_fresh_top(event.symbol)
    plan = _plan(bid, ask, event.price_increment)
    if not plan.actionable_live_q1:
        return None
    return {"horizon": "5m", "event": event, "plan": plan, "bid": bid, "ask": ask}


async def _ready_15m() -> dict[str, Any] | None:
    event = await base15.discover_btc15m()
    if event is None or event.seconds_to_expiry < inv15.MIN_SECONDS_TO_EXPIRY + v15.LIVE_ENTRY_BUFFER_S:
        return None
    bid, _, ask, _ = await v15.rest_fresh_top(event.symbol)
    plan = _plan(bid, ask, event.price_increment)
    if not plan.actionable_live_q1:
        return None
    return {"horizon": "15m", "event": event, "plan": plan, "bid": bid, "ask": ask}


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


def _choose(ready: list[dict[str, Any]], counts: dict[str, int]) -> dict[str, Any] | None:
    if not ready:
        return None
    # Prefer the strongest threshold signal, then the under-sampled horizon.
    return sorted(
        ready,
        key=lambda r: (
            -r["plan"].favored_probability,
            counts[r["horizon"]],
            0 if r["horizon"] == "15m" else 1,
        ),
    )[0]


def _patched_pair_model(plan: QuotePlan) -> Callable[[D, D], dict[str, D]]:
    # The child performs a second fresh book read immediately before mutation.
    # Recompute from that fresh book; never reuse readiness prices.
    def model(bid: D, ask: D) -> dict[str, D]:
        fresh = plan_threshold_quotes(
            bid,
            ask,
            threshold=plan.threshold,
            pair_floor=plan.pair_floor,
            price_increment=D("0.01"),
        )
        return fresh.as_model_dict()

    return model


async def _run_child(horizon: str, cid: str, plan: QuotePlan) -> None:
    if horizon == "5m":
        original = base5.pair_model
        try:
            base5.fresh_top = v5.rest_fresh_top
            base5.pair_model = _patched_pair_model(plan)
            os.environ[inv5.LIVE_ENV] = "YES"
            os.environ[inv5.CANARY_ID_ENV] = cid
            os.environ[inv5.CANARY_ARM_ENV] = cid
            await inv5.run()
        finally:
            base5.pair_model = original
        return
    if horizon == "15m":
        original = base15.pair_model
        try:
            base15.fresh_top = v15.rest_fresh_top
            base15.pair_model = _patched_pair_model(plan)
            os.environ[inv15.LIVE_ENV] = "YES"
            os.environ[inv15.CANARY_ID_ENV] = cid
            os.environ[inv15.CANARY_ARM_ENV] = cid
            await inv15.run()
        finally:
            base15.pair_model = original
        return
    raise RuntimeError(f"unsupported horizon {horizon}")


def _modeled_rebate_for_result(plan: QuotePlan, result: dict[str, Any]) -> dict[str, str]:
    yes_fill = D(str(result.get("yes_fill") or "0"))
    no_fill = D(str(result.get("no_fill") or "0"))
    yes_raw, yes_rounded = maker_rebate_estimate(plan.yes_quote, yes_fill)
    no_raw, no_rounded = maker_rebate_estimate(plan.no_quote, no_fill)
    return {
        "yes_raw": str(yes_raw),
        "no_raw": str(no_raw),
        "raw_total": str(yes_raw + no_raw),
        "yes_cent_rounded": str(yes_rounded),
        "no_cent_rounded": str(no_rounded),
        "cent_rounded_total": str(yes_rounded + no_rounded),
    }


async def run() -> None:
    if not live_armed():
        emit("DISARMED", order_entry=False, realized_pnl="0")
        return
    if local_base.local_open_state() is not None:
        raise RuntimeError("l5-threshold-v1 start blocked by unresolved local reservation")
    await budget5.assert_clean_account()

    session_id = _clean(os.environ[SESSION_ID_ENV], 18)
    started = time.monotonic()
    session_realized = D("0")
    attempts = 0
    counts = {"5m": 0, "15m": 0}
    emit(
        "START",
        session_id=session_id,
        threshold=str(DEFAULT_THRESHOLD),
        pair_floor=str(DEFAULT_PAIR_FLOOR),
        order_entry_window_seconds=HARD_MAX_RUNTIME_S,
        max_executed_total=HARD_MAX_EXECUTED_TOTAL,
        max_executed_per_horizon=HARD_MAX_EXECUTED_PER_HORIZON,
        max_realized_loss=str(HARD_MAX_REALIZED_LOSS),
        q_per_outcome=1,
        serialized=True,
        maker_rebate_included_in_trading_pnl=False,
        liquidity_rewards_eligible_at_q1=False,
        strategy="MARKET_IMPLIED_THRESHOLD_PLUS_ONE_TICK_SKEW",
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

        choice = _choose(await _readiness(counts), counts)
        if choice is None:
            emit(
                "WAITING_FOR_THRESHOLD",
                threshold=str(DEFAULT_THRESHOLD),
                rebate_band="0.20-0.80",
                counts=counts,
                realized_pnl=str(session_realized),
                order_entry=False,
            )
            await asyncio.sleep(IDLE_POLL_S)
            continue

        horizon = str(choice["horizon"])
        event = choice["event"]
        plan: QuotePlan = choice["plan"]
        attempts += 1
        cid = child_id(session_id, horizon, attempts)
        emit(
            "CAMPAIGN_ATTEMPT_START",
            attempt=attempts,
            horizon=horizon,
            child_canary_id=cid,
            market=event.ticker,
            seconds_to_expiry=event.seconds_to_expiry,
            yes_mid=str(plan.yes_mid),
            no_mid=str(plan.no_mid),
            favored_side=plan.favored_side,
            favored_probability=str(plan.favored_probability),
            shadow_target_units=plan.shadow_target_units,
            live_units=1,
            quote_skew_ticks=plan.quote_skew_ticks,
            planned_yes_quote=str(plan.yes_quote),
            planned_no_quote=str(plan.no_quote),
            planned_pair_edge=str(plan.modeled_edge),
            q1_modeled_rebate_rounded_total=str(plan.q1_yes_rebate_rounded + plan.q1_no_rebate_rounded),
            counts=counts,
            realized_pnl=str(session_realized),
            order_entry=True,
        )

        try:
            await _run_child(horizon, cid, plan)
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
            raise RuntimeError(f"l5-threshold-v1 ambiguous local status: {status}")

        counts[horizon] += 1
        result = await _wait_for_settlement()
        if result.get("horizon") != horizon:
            raise RuntimeError(
                f"settlement horizon mismatch expected={horizon} got={result.get('horizon')}"
            )
        pnl = D(str(result.get("net_profit") or "0"))
        session_realized += pnl
        modeled_rebate = _modeled_rebate_for_result(plan, result)
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
            campaign_trading_net_profit=str(pnl),
            session_realized_trading_pnl=str(session_realized),
            modeled_maker_rebate=modeled_rebate,
            maker_rebate_certified_realized=False,
            liquidity_reward_certified_realized=False,
            favored_side_at_entry=plan.favored_side,
            favored_probability_at_entry=str(plan.favored_probability),
            local_reservation_cleared=result.get("local_reservation_cleared"),
            order_entry=False,
        )
        if not result.get("local_reservation_cleared"):
            raise RuntimeError("l5-threshold-v1 settlement reconciliation did not clear reservation")
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
