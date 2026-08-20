"""Read-only q=5 scale shadow using the production 10bp/50ms safety shape.

A q=5 candidate must enter with >=$0.15 modeled after-fee profit (>=3c/contract),
remain in the same quote/boundary episode for 50ms, keep fresh <=50ms-skew books,
and retain Gemini q=5 marketability.  At the exact checkpoint we compare cumulative
Kalshi q=5 depth at the original price vs the fee-aware 0-2c cushioned limit.

No order transport is imported and order_entry is always false.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from decimal import Decimal

from src import gemini_kalshi_micro_live_readiness_20bp as readiness
from src import gemini_kalshi_rollover_optimizer as rollover
from src import gemini_kalshi_target_lifecycle_cache as target_cache
from src import gemini_kalshi_target_freshness_guard as target_freshness
from src.gemini_kalshi_hedge_cushion import kalshi_cumulative_depth
from src.gemini_kalshi_scaled_hedge_cushion import best_scaled_fee_aware_kalshi_hedge
from src.gemini_kalshi_scaled_probe_guard import recompute_scaled_payload_locked_profit

D = Decimal
Q = D("5")
MIN_TOTAL_PROFIT = D("0.15")
MAX_QUOTE_SKEW_MS = 50
logger = logging.getLogger("kalshi_arb.gemini_kalshi_q5_scale_shadow")
exact = readiness.exact
basis = readiness.basis
_ENTRY_SKEW = {}
_FEE_MULTIPLIER = {}
_COUNTERS = Counter()
_INSTALLED = False


def _payloads(plan):
    cid = f"shadow-q5-{uuid.uuid4().hex[:16]}"
    if D(plan.quantity) != Q:
        raise ValueError(f"q5 shadow requires plan quantity=5 got={plan.quantity}")
    if plan.direction == "KALSHI_YES_GEMINI_NO":
        outcome = "NO"; kside = "bid"; kprice = D(plan.kalshi_limit)
    elif plan.direction == "GEMINI_YES_KALSHI_NO":
        outcome = "YES"; kside = "ask"; kprice = D("1") - D(plan.kalshi_limit)
    else:
        raise ValueError(plan.direction)
    g = {
        "symbol": plan.gemini_symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
        "price": str(D(plan.gemini_limit)), "quantity": "5", "eventOutcome": outcome,
        "clientOrderId": cid + "-g",
    }
    k = {
        "ticker": plan.kalshi_ticker, "client_order_id": cid + "-k", "side": kside,
        "count": "5.00", "price": str(kprice), "time_in_force": "fill_or_kill",
    }
    return g, k


def _capture_plan(original):
    def wrapped(**kwargs):
        plan = original(**kwargs)
        cand = kwargs.get("candidate")
        skew = getattr(cand, "quote_skew_ms", None)
        if skew is not None:
            _ENTRY_SKEW[(plan.asset, plan.direction, int(plan.opened_ms))] = int(skew)
        return plan
    return wrapped


def _checkpoint_non_kalshi_depth_gate(feed, plan, active_episodes, now_ms):
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    entry_boundary = bool(readiness._PLAN_ENTRY_20BP.get(key, False))
    entry_skew = _ENTRY_SKEW.get(key)
    g, k = feed.gemini, feed.kalshi
    same_window = bool(
        feed.active and feed.pair.gemini_symbol == plan.gemini_symbol
        and feed.pair.kalshi_ticker == plan.kalshi_ticker
    )
    g_age = k_age = current_skew = None
    post = fresh = False
    gemini_price_ok = gemini_depth_ok = False
    if same_window and g is not None and k is not None:
        if plan.direction == "GEMINI_YES_KALSHI_NO":
            gemini_price_ok = g.yes_ask <= plan.gemini_limit
            gemini_depth_ok = g.yes_ask_size >= Q
        elif plan.direction == "KALSHI_YES_GEMINI_NO":
            gemini_price_ok = g.no_ask <= plan.gemini_limit
            gemini_depth_ok = g.no_ask_size >= Q
        g_age = max(0, now_ms - int(g.ts_ms)); k_age = max(0, now_ms - int(k.ts_ms))
        current_skew = abs(int(g.ts_ms) - int(k.ts_ms))
        post = bool(int(g.ts_ms) >= int(feed.boundary_ms) and int(k.ts_ms) >= int(feed.boundary_ms))
        fresh = bool(post and max(g_age, k_age) <= 250 and current_skew <= MAX_QUOTE_SKEW_MS)

    state = basis._BASIS_EPISODES.get((plan.asset, plan.direction))
    same_episode = readiness.guarded.same_original_episode(plan, active_episodes, state)
    snap = basis.basis_snapshot(plan.asset, now_ms) if same_window else None
    boundary_now = bool(snap and snap.get("research_double_boundary_gate_20bp_fresh_1000ms"))
    boundary_whole = bool(same_episode and state and getattr(state, "all_events_gate_20bp", False))
    ok = bool(
        D(plan.quantity) == Q
        and D(plan.entry_locked_profit) >= MIN_TOTAL_PROFIT
        and entry_skew is not None and entry_skew <= MAX_QUOTE_SKEW_MS
        and current_skew is not None and current_skew <= MAX_QUOTE_SKEW_MS
        and entry_boundary and same_episode and boundary_whole and boundary_now and fresh
        and gemini_price_ok and gemini_depth_ok
    )
    return ok, {
        "entry_quote_skew_ms": entry_skew, "current_quote_skew_ms": current_skew,
        "entry_double_boundary_10bp": entry_boundary, "same_window": same_window,
        "same_original_episode": same_episode, "gemini_price_ok": gemini_price_ok,
        "gemini_depth_q5_ok": gemini_depth_ok, "post_activation_books": post,
        "gemini_age_ms": g_age, "kalshi_age_ms": k_age,
        "strict_fresh_250ms_and_50ms_skew": fresh,
        "double_boundary_10bp_now": boundary_now,
        "double_boundary_10bp_held_through_episode": boundary_whole,
        "min_abs_double_boundary_margin_bps_now": snap.get("min_abs_double_boundary_margin_bps") if snap else None,
    }


async def _probe(*, feed, plan, active_episodes, delays_ms=(50, 100)):
    del delays_ms
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    try:
        if D(plan.quantity) != Q:
            _COUNTERS["non_q5_plan"] += 1
            return
        await asyncio.sleep(0.050)
        now_ms = int(time.time() * 1000)
        non_depth_ok, gate = _checkpoint_non_kalshi_depth_gate(feed, plan, active_episodes, now_ms)
        if not non_depth_ok:
            _COUNTERS["gate_rejected"] += 1
            return
        mult = _FEE_MULTIPLIER.get(plan.asset)
        if mult is None:
            _COUNTERS["missing_fee_multiplier"] += 1
            return
        gp, kp = _payloads(plan)
        original_profit = recompute_scaled_payload_locked_profit(gp, kp, kalshi_fee_multiplier=mult)
        if original_profit < MIN_TOTAL_PROFIT:
            _COUNTERS["economics_rejected"] += 1
            return
        cushion = best_scaled_fee_aware_kalshi_hedge(
            gp, kp, kalshi_fee_multiplier=mult, min_profit_per_contract=D("0.03"), max_cushion_cents=2,
        )
        book = rollover._HUB._books.get(plan.kalshi_ticker)
        ready = plan.kalshi_ticker in rollover._HUB._snapshot_ready
        if book is None or not ready:
            _COUNTERS["no_full_book"] += 1
            return
        original_depth = kalshi_cumulative_depth(
            book, side=cushion.side, yes_limit=cushion.original_yes_price, quantity=Q,
        )
        cushioned_depth = kalshi_cumulative_depth(
            book, side=cushion.side, yes_limit=cushion.cushioned_yes_price, quantity=Q,
        )
        original_fillable = bool(original_depth["fillable"])
        cushioned_fillable = bool(cushioned_depth["fillable"])
        rescued = bool(not original_fillable and cushioned_fillable)
        _COUNTERS["q5_50ms_non_depth_gate"] += 1
        _COUNTERS["q5_original_fillable"] += int(original_fillable)
        _COUNTERS["q5_cushioned_fillable"] += int(cushioned_fillable)
        _COUNTERS["q5_rescued_by_cushion"] += int(rescued)
        _COUNTERS[f"asset_{plan.asset}_q5_checkpoint"] += 1
        logger.info("CRYPTO_Q5_SCALE_CHECKPOINT %s", json.dumps({
            "asset": plan.asset, "direction": plan.direction, "opened_ms": int(plan.opened_ms),
            "gemini_symbol": plan.gemini_symbol, "kalshi_ticker": plan.kalshi_ticker,
            "quantity": "5", "entry_locked_quote_profit": str(plan.entry_locked_profit),
            "entry_profit_per_contract": str(D(plan.entry_locked_profit) / Q),
            "original_recomputed_locked_profit": str(original_profit),
            "cushioned_locked_profit": str(cushion.cushioned_locked_profit),
            "cushioned_profit_per_contract": str(cushion.cushioned_locked_profit / Q),
            "cushion_cents": cushion.cushion_cents, "kalshi_side": cushion.side,
            "original_kalshi_yes_limit": str(cushion.original_yes_price),
            "cushioned_kalshi_yes_limit": str(cushion.cushioned_yes_price),
            "original_depth": original_depth, "cushioned_depth": cushioned_depth,
            "original_q5_fillable": original_fillable, "cushioned_q5_fillable": cushioned_fillable,
            "rescued_by_cushion": rescued, **gate,
            "risk_gates_changed": False, "trade_authorized": False,
            "read_only": True, "order_entry": False,
        }, separators=(",", ":"), default=str))
        logger.info("CRYPTO_Q5_SCALE_SUMMARY %s", json.dumps({
            "counters": dict(_COUNTERS), "risk_gates_changed": False,
            "trade_authorized": False, "read_only": True, "order_entry": False,
        }, separators=(",", ":"), sort_keys=True))
    finally:
        _ENTRY_SKEW.pop(key, None)
        readiness._PLAN_ENTRY_20BP.pop(key, None)


def install_shadow():
    global _INSTALLED
    if _INSTALLED:
        return
    readiness.MIN_DOUBLE_BOUNDARY_BPS = D("10")
    rollover.install(); target_cache.install(); target_freshness.install(); readiness.install()
    exact._plan_from_books = _capture_plan(exact._plan_from_books)
    prewarmed = exact.safe.base
    original = prewarmed.collect_asset_prewarmed

    async def capture_fee(**kwargs):
        fm = kwargs.get("fee_meta"); asset = str(kwargs.get("asset") or "").upper()
        if fm is not None and asset:
            _FEE_MULTIPLIER[asset] = D(str(fm.fee_multiplier))
        return await original(**kwargs)

    prewarmed.collect_asset_prewarmed = capture_fee
    exact._probe = _probe
    _INSTALLED = True
    logger.warning(
        "CRYPTO_Q5_SCALE_SHADOW_INSTALLED production_shape=10bp_50ms q=5 min_total_profit=0.15 "
        "min_profit_per_contract=0.03 max_cushion_cents=2 full_kalshi_depth=true "
        "risk_gates_changed=false trade_authorized=false order_entry=false"
    )


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    install_shadow()
    readiness.guarded.main()


if __name__ == "__main__":
    main()
