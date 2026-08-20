"""Read-only shadow for spending excess edge on a more reliable Kalshi hedge.

The observer is intentionally conditioned on the SAME q=1 / >=3c / 10bp / 50ms
readiness gate used by the bounded live canary.  It never submits an order.  When a
candidate passes that gate it computes the most aggressive fee-aware Kalshi limit that
still guarantees >=3c using the original Gemini limit, then observes the production
shared Kalshi order book for 0/10/20/30/50/75/100ms.

The key metric is ``rescued_by_cushion``: the original FOK limit has <1 contract of
cumulative resting depth while the safe cushioned limit has >=1.  This directly tests
the failure mode seen in the BTC canary (Kalshi FOK insufficient resting volume).
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
import logging
import time
import uuid
from typing import Any

from src import gemini_kalshi_micro_live_readiness_20bp as readiness
from src import gemini_kalshi_rollover_optimizer as rollover
from src import gemini_kalshi_target_lifecycle_cache as target_cache
from src import gemini_kalshi_target_freshness_guard as target_freshness
from src.gemini_kalshi_hedge_cushion import (
    best_fee_aware_kalshi_hedge,
    kalshi_cumulative_depth,
)
from src.gemini_kalshi_live_probe_guard import DEFAULT_MIN_LOCKED_PROFIT, MAX_QUOTE_SKEW_MS

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_hedge_cushion_shadow")
exact = readiness.exact
basis = readiness.basis

_ENTRY_SKEW_MS: dict[tuple[str, str, int], int] = {}
_FEE_MULTIPLIER: dict[str, Decimal] = {}
_COUNTERS: Counter[str] = Counter()
_SAMPLE_DELAYS_MS = (0, 10, 20, 30, 50, 75, 100)
_INSTALLED = False


def _payloads(plan) -> tuple[dict[str, Any], dict[str, Any]]:
    cid = f"shadow-cushion-{uuid.uuid4().hex[:16]}"
    if plan.direction == "KALSHI_YES_GEMINI_NO":
        gemini_outcome = "NO"
        gemini_price = D(plan.gemini_limit)
        kalshi_side = "bid"
        kalshi_yes_price = D(plan.kalshi_limit)
    elif plan.direction == "GEMINI_YES_KALSHI_NO":
        gemini_outcome = "YES"
        gemini_price = D(plan.gemini_limit)
        kalshi_side = "ask"
        kalshi_yes_price = D("1") - D(plan.kalshi_limit)
    else:
        raise ValueError(plan.direction)
    return (
        {
            "symbol": plan.gemini_symbol,
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "FOK",
            "price": str(gemini_price),
            "quantity": "1",
            "eventOutcome": gemini_outcome,
            "clientOrderId": cid + "-g",
        },
        {
            "ticker": plan.kalshi_ticker,
            "client_order_id": cid + "-k",
            "side": kalshi_side,
            "count": "1.00",
            "price": str(kalshi_yes_price),
            "time_in_force": "fill_or_kill",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": True,
            "reduce_only": False,
            "subaccount": 0,
            "exchange_index": 0,
        },
    )


def _capture_plan(original):
    def capture(**kwargs):
        plan = original(**kwargs)
        candidate = kwargs.get("candidate")
        skew = getattr(candidate, "quote_skew_ms", None)
        if skew is not None:
            _ENTRY_SKEW_MS[(plan.asset, plan.direction, int(plan.opened_ms))] = int(skew)
        return plan
    return capture


def _same_live_gate(*, feed, plan, active_episodes, now_ms: int) -> tuple[bool, dict[str, Any]]:
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    entry_boundary = bool(readiness._PLAN_ENTRY_20BP.get(key, False))
    entry_skew = _ENTRY_SKEW_MS.get(key)
    g, k = feed.gemini, feed.kalshi
    same_window = bool(
        feed.active
        and feed.pair.gemini_symbol == plan.gemini_symbol
        and feed.pair.kalshi_ticker == plan.kalshi_ticker
    )
    checks = {
        "gemini_price_ok": False,
        "gemini_depth_ok": False,
        "kalshi_price_ok": False,
        "kalshi_depth_ok": False,
        "both_marketable": False,
    }
    gemini_age_ms = kalshi_age_ms = current_skew = None
    post_activation = strict_fresh = False
    if same_window and g is not None and k is not None:
        checks = exact.same_limits_marketable(plan, g, k)
        gemini_age_ms = max(0, now_ms - int(g.ts_ms))
        kalshi_age_ms = max(0, now_ms - int(k.ts_ms))
        current_skew = abs(int(g.ts_ms) - int(k.ts_ms))
        post_activation = bool(int(g.ts_ms) >= int(feed.boundary_ms) and int(k.ts_ms) >= int(feed.boundary_ms))
        strict_fresh = bool(
            post_activation
            and max(gemini_age_ms, kalshi_age_ms) <= 250
            and current_skew <= MAX_QUOTE_SKEW_MS
        )

    state = basis._BASIS_EPISODES.get((plan.asset, plan.direction))
    same_episode = readiness.guarded.same_original_episode(plan, active_episodes, state)
    snapshot = basis.basis_snapshot(plan.asset, now_ms) if same_window else None
    boundary_now = bool(snapshot and snapshot.get("research_double_boundary_gate_20bp_fresh_1000ms"))
    boundary_whole = bool(same_episode and state and getattr(state, "all_events_gate_20bp", False))
    actionable = bool(
        D(plan.quantity) == D("1")
        and D(plan.entry_locked_profit) >= DEFAULT_MIN_LOCKED_PROFIT
        and entry_skew is not None
        and entry_skew <= MAX_QUOTE_SKEW_MS
        and current_skew is not None
        and current_skew <= MAX_QUOTE_SKEW_MS
        and entry_boundary
        and same_episode
        and boundary_whole
        and boundary_now
        and strict_fresh
        and checks["both_marketable"]
    )
    return actionable, {
        "entry_quote_skew_ms": entry_skew,
        "current_quote_skew_ms": current_skew,
        "entry_double_boundary_10bp": entry_boundary,
        "same_window": same_window,
        "same_original_episode": same_episode,
        **checks,
        "post_activation_books": post_activation,
        "gemini_age_ms": gemini_age_ms,
        "kalshi_age_ms": kalshi_age_ms,
        "strict_fresh_250ms_and_50ms_skew": strict_fresh,
        "double_boundary_10bp_now": boundary_now,
        "double_boundary_10bp_held_through_episode": boundary_whole,
        "min_abs_double_boundary_margin_bps_now": (
            snapshot.get("min_abs_double_boundary_margin_bps") if snapshot else None
        ),
    }


async def _sample_depth(*, plan, cushion, gate_ms: int) -> None:
    previous = 0
    rescued_any = False
    old_failed_any = False
    for delay in _SAMPLE_DELAYS_MS:
        if delay > previous:
            await asyncio.sleep((delay - previous) / 1000)
        previous = delay
        book = rollover._HUB._books.get(plan.kalshi_ticker)
        snapshot_ready = plan.kalshi_ticker in rollover._HUB._snapshot_ready
        if book is None or not snapshot_ready:
            _COUNTERS["samples_no_book"] += 1
            logger.info("CRYPTO_HEDGE_CUSHION_SAMPLE %s", json.dumps({
                "asset": plan.asset,
                "direction": plan.direction,
                "opened_ms": int(plan.opened_ms),
                "sample_after_actionable_ms": delay,
                "snapshot_ready": False,
                "old_fillable_q1": False,
                "cushioned_fillable_q1": False,
                "rescued_by_cushion": False,
                "order_entry": False,
            }, separators=(",", ":")))
            continue

        old = kalshi_cumulative_depth(
            book,
            side=cushion.side,
            yes_limit=cushion.original_yes_price,
            quantity=D("1"),
        )
        widened = kalshi_cumulative_depth(
            book,
            side=cushion.side,
            yes_limit=cushion.cushioned_yes_price,
            quantity=D("1"),
        )
        rescued = bool(not old["fillable"] and widened["fillable"])
        rescued_any = rescued_any or rescued
        old_failed_any = old_failed_any or not old["fillable"]
        _COUNTERS["samples"] += 1
        _COUNTERS["old_fillable_samples"] += int(bool(old["fillable"]))
        _COUNTERS["cushioned_fillable_samples"] += int(bool(widened["fillable"]))
        _COUNTERS["rescued_samples"] += int(rescued)
        if rescued:
            _COUNTERS[f"rescued_at_{delay}ms"] += 1
        logger.info("CRYPTO_HEDGE_CUSHION_SAMPLE %s", json.dumps({
            "asset": plan.asset,
            "direction": plan.direction,
            "opened_ms": int(plan.opened_ms),
            "gate_observed_ms": gate_ms,
            "sample_after_actionable_ms": delay,
            "kalshi_ticker": plan.kalshi_ticker,
            "snapshot_ready": True,
            "original_yes_limit": str(cushion.original_yes_price),
            "cushioned_yes_limit": str(cushion.cushioned_yes_price),
            "cushion_cents": cushion.cushion_cents,
            "old_depth": old,
            "cushioned_depth": widened,
            "old_fillable_q1": bool(old["fillable"]),
            "cushioned_fillable_q1": bool(widened["fillable"]),
            "rescued_by_cushion": rescued,
            "risk_gates_changed": False,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

    _COUNTERS["episodes_old_failed_any_sample"] += int(old_failed_any)
    _COUNTERS["episodes_rescued_any_sample"] += int(rescued_any)
    logger.info("CRYPTO_HEDGE_CUSHION_SUMMARY %s", json.dumps({
        "counters": dict(_COUNTERS),
        "risk_gates_changed": False,
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":"), sort_keys=True))


async def _probe_cushion(*, feed, plan, active_episodes, delays_ms=(50, 100)) -> None:
    del delays_ms
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    try:
        await asyncio.sleep(0.050)
        now_ms = int(time.time() * 1000)
        actionable, gate = _same_live_gate(
            feed=feed, plan=plan, active_episodes=active_episodes, now_ms=now_ms
        )
        if not actionable:
            _COUNTERS["gate_rejected"] += 1
            return

        fee_multiplier = _FEE_MULTIPLIER.get(plan.asset)
        if fee_multiplier is None:
            _COUNTERS["missing_fee_multiplier"] += 1
            logger.warning("CRYPTO_HEDGE_CUSHION_SKIP asset=%s reason=missing_fee_multiplier", plan.asset)
            return
        gemini_payload, kalshi_payload = _payloads(plan)
        try:
            cushion = best_fee_aware_kalshi_hedge(
                gemini_payload,
                kalshi_payload,
                kalshi_fee_multiplier=fee_multiplier,
                profit_floor=DEFAULT_MIN_LOCKED_PROFIT,
            )
        except Exception as exc:
            _COUNTERS["economics_rejected"] += 1
            logger.warning(
                "CRYPTO_HEDGE_CUSHION_SKIP asset=%s reason=economics type=%s error=%s",
                plan.asset, type(exc).__name__, str(exc)[:180],
            )
            return

        _COUNTERS["production_gate_actionable"] += 1
        _COUNTERS["cushion_available"] += int(cushion.has_cushion)
        _COUNTERS[f"asset_{plan.asset}_actionable"] += 1
        logger.info("CRYPTO_HEDGE_CUSHION_ACTIONABLE %s", json.dumps({
            "asset": plan.asset,
            "direction": plan.direction,
            "opened_ms": int(plan.opened_ms),
            "gemini_symbol": plan.gemini_symbol,
            "kalshi_ticker": plan.kalshi_ticker,
            "quantity": "1",
            "entry_locked_quote_profit": str(plan.entry_locked_profit),
            "original_recomputed_locked_profit": str(cushion.original_locked_profit),
            "minimum_locked_profit": str(DEFAULT_MIN_LOCKED_PROFIT),
            "cushioned_locked_profit": str(cushion.cushioned_locked_profit),
            "edge_spent": str(cushion.edge_spent),
            "kalshi_side": cushion.side,
            "original_kalshi_yes_limit": str(cushion.original_yes_price),
            "cushioned_kalshi_yes_limit": str(cushion.cushioned_yes_price),
            "cushion_cents": cushion.cushion_cents,
            **gate,
            "risk_gates_changed": False,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":"), default=str))
        await _sample_depth(plan=plan, cushion=cushion, gate_ms=now_ms)
    finally:
        _ENTRY_SKEW_MS.pop(key, None)


def install_shadow() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Match the production canary's threshold while keeping the whole composition
    # research-only.  Naming inside the inherited readiness module remains "20bp",
    # but the numeric threshold is explicitly 10 bp, exactly as in canary v2.
    readiness.MIN_DOUBLE_BOUNDARY_BPS = D("10")
    rollover.install()
    target_cache.install()
    target_freshness.install()
    readiness.install()

    # Capture the entry skew from the exact candidate that opened each plan.
    exact._plan_from_books = _capture_plan(exact._plan_from_books)

    # Capture the same live Kalshi fee multiplier used by candidate economics.
    prewarmed_base = exact.safe.base
    original_collect_asset = prewarmed_base.collect_asset_prewarmed

    async def collect_asset_capture_fee(**kwargs):
        fee_meta = kwargs.get("fee_meta")
        asset = str(kwargs.get("asset") or "").upper()
        if fee_meta is not None and asset:
            _FEE_MULTIPLIER[asset] = D(str(fee_meta.fee_multiplier))
        return await original_collect_asset(**kwargs)

    prewarmed_base.collect_asset_prewarmed = collect_asset_capture_fee
    exact._probe = _probe_cushion
    _INSTALLED = True
    logger.warning(
        "CRYPTO_HEDGE_CUSHION_SHADOW_INSTALLED production_gate=10bp_50ms min_profit=0.03 q=1 "
        "samples_ms=0,10,20,30,50,75,100 full_kalshi_depth=true risk_gates_changed=false "
        "trade_authorized=false order_entry=false"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    install_shadow()
    readiness.guarded.main()


if __name__ == "__main__":
    main()
