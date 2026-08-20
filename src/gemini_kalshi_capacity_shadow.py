"""Read-only capacity sizing for production-shaped Gemini/Kalshi 15m opportunities.

This module asks a narrow scale question without changing any live execution guard:
when a q=5 opportunity passes the existing 10bp / 50ms research shape, how much size
could the *same prices* conservatively support at q=5, q=10, and q=25?

For each size we recompute exact Gemini/Kalshi taker fees, require >=3c after-fee
profit per contract, require displayed Gemini top-level depth, and measure cumulative
Kalshi depth through an at-most-2c fee-aware YES-space hedge cushion.  It imports no
order transport.  ``trade_authorized`` and ``order_entry`` are always false.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any, Iterable

from src import gemini_kalshi_micro_live_readiness_20bp as readiness
from src import gemini_kalshi_rollover_optimizer as rollover
from src import gemini_kalshi_target_lifecycle_cache as target_cache
from src import gemini_kalshi_target_freshness_guard as target_freshness
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_hedge_cushion import kalshi_cumulative_depth
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

D = Decimal
Q_TRIGGER = D("5")
SIZES = (D("5"), D("10"), D("25"))
MIN_PROFIT_PER_CONTRACT = D("0.03")
MIN_TRIGGER_TOTAL_PROFIT = D("0.15")
MAX_CUSHION_CENTS = 2
MAX_QUOTE_SKEW_MS = 50
MAX_BOOK_AGE_MS = 250
CENT = D("0.01")
MIN_PRICE = D("0.01")
MAX_PRICE = D("0.99")

logger = logging.getLogger("kalshi_arb.gemini_kalshi_capacity_shadow")
exact = readiness.exact
basis = readiness.basis
_ENTRY_SKEW: dict[tuple[str, str, int], int] = {}
_FEE_MULTIPLIER: dict[str, Decimal] = {}
_COUNTERS: dict[str, int] = {
    "trigger_plans": 0,
    "gate_rejected": 0,
    "missing_fee_multiplier": 0,
    "no_full_kalshi_book": 0,
    "capacity_checkpoints": 0,
    "max_q_0": 0,
    "max_q_5": 0,
    "max_q_10": 0,
    "max_q_25": 0,
}
_INSTALLED = False


@dataclass(frozen=True)
class CapacityEconomics:
    quantity: Decimal
    gemini_price: Decimal
    kalshi_yes_price: Decimal
    kalshi_contract_cost: Decimal
    gemini_fee: Decimal
    kalshi_fee: Decimal
    gemini_capital: Decimal
    kalshi_capital: Decimal
    total_capital: Decimal
    total_profit: Decimal
    profit_per_contract: Decimal


def _kalshi_side_and_yes_price(direction: str, kalshi_limit: Decimal) -> tuple[str, Decimal]:
    if direction == "KALSHI_YES_GEMINI_NO":
        return "bid", D(kalshi_limit)
    if direction == "GEMINI_YES_KALSHI_NO":
        return "ask", D("1") - D(kalshi_limit)
    raise ValueError(f"unsupported direction={direction!r}")


def capacity_economics(
    *,
    direction: str,
    gemini_price: Decimal,
    kalshi_yes_price: Decimal,
    quantity: Decimal,
    kalshi_fee_multiplier: Decimal,
) -> CapacityEconomics:
    """Exact fee-aware complementary economics for arbitrary positive integer q."""
    q = D(quantity)
    gp = D(gemini_price)
    kp = D(kalshi_yes_price)
    mult = D(kalshi_fee_multiplier)
    if q <= 0 or q != q.to_integral_value():
        raise ValueError("capacity quantity must be a positive integer")
    if not (MIN_PRICE <= gp <= MAX_PRICE and MIN_PRICE <= kp <= MAX_PRICE):
        raise ValueError("capacity prices must be within 1c..99c")

    if direction == "KALSHI_YES_GEMINI_NO":
        kalshi_contract_cost = kp
    elif direction == "GEMINI_YES_KALSHI_NO":
        kalshi_contract_cost = D("1") - kp
    else:
        raise ValueError(f"unsupported direction={direction!r}")

    gfee = gemini_predictions_taker_fee(gp, q)
    kfee = kalshi_quadratic_taker_fee(kp, q, fee_multiplier=mult)
    gcap = q * gp + gfee
    kcap = q * kalshi_contract_cost + kfee
    total_capital = gcap + kcap
    total_profit = q - total_capital
    return CapacityEconomics(
        quantity=q,
        gemini_price=gp,
        kalshi_yes_price=kp,
        kalshi_contract_cost=kalshi_contract_cost,
        gemini_fee=gfee,
        kalshi_fee=kfee,
        gemini_capital=gcap,
        kalshi_capital=kcap,
        total_capital=total_capital,
        total_profit=total_profit,
        profit_per_contract=total_profit / q,
    )


def best_capacity_cushion(
    *,
    direction: str,
    gemini_price: Decimal,
    original_kalshi_yes_price: Decimal,
    quantity: Decimal,
    kalshi_fee_multiplier: Decimal,
    min_profit_per_contract: Decimal = MIN_PROFIT_PER_CONTRACT,
    max_cushion_cents: int = MAX_CUSHION_CENTS,
) -> tuple[Decimal, CapacityEconomics, int]:
    """Spend at most ``max_cushion_cents`` while preserving the per-contract floor."""
    side, _ = _kalshi_side_and_yes_price(direction, D("1") - D(original_kalshi_yes_price) if direction == "GEMINI_YES_KALSHI_NO" else D(original_kalshi_yes_price))
    original = D(original_kalshi_yes_price)
    floor = D(min_profit_per_contract)
    if max_cushion_cents < 0:
        raise ValueError("max cushion cents cannot be negative")

    best_price = original
    best = capacity_economics(
        direction=direction,
        gemini_price=D(gemini_price),
        kalshi_yes_price=original,
        quantity=D(quantity),
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    if best.profit_per_contract < floor:
        raise ValueError("original capacity economics are below per-contract floor")

    best_cents = 0
    for cents in range(1, max_cushion_cents + 1):
        candidate = original + CENT * cents if side == "bid" else original - CENT * cents
        if candidate < MIN_PRICE or candidate > MAX_PRICE:
            break
        economics = capacity_economics(
            direction=direction,
            gemini_price=D(gemini_price),
            kalshi_yes_price=candidate,
            quantity=D(quantity),
            kalshi_fee_multiplier=D(kalshi_fee_multiplier),
        )
        if economics.profit_per_contract >= floor:
            best_price = candidate
            best = economics
            best_cents = cents
        else:
            break
    return best_price, best, best_cents


def evaluate_capacity_sizes(
    *,
    direction: str,
    gemini_price: Decimal,
    original_kalshi_yes_price: Decimal,
    gemini_available_size: Decimal,
    kalshi_book: Any,
    kalshi_fee_multiplier: Decimal,
    sizes: Iterable[Decimal] = SIZES,
) -> list[dict[str, Any]]:
    """Return conservative same-price support rows for each requested quantity."""
    rows: list[dict[str, Any]] = []
    side = "bid" if direction == "KALSHI_YES_GEMINI_NO" else "ask"
    for raw_q in sizes:
        q = D(raw_q)
        row: dict[str, Any] = {"quantity": str(q)}
        try:
            original = capacity_economics(
                direction=direction,
                gemini_price=D(gemini_price),
                kalshi_yes_price=D(original_kalshi_yes_price),
                quantity=q,
                kalshi_fee_multiplier=D(kalshi_fee_multiplier),
            )
        except Exception as exc:
            row.update({"supported": False, "reason": f"economics_error:{type(exc).__name__}"})
            rows.append(row)
            continue

        economics_ok = original.profit_per_contract >= MIN_PROFIT_PER_CONTRACT
        if economics_ok:
            cushioned_price, cushioned, cushion_cents = best_capacity_cushion(
                direction=direction,
                gemini_price=D(gemini_price),
                original_kalshi_yes_price=D(original_kalshi_yes_price),
                quantity=q,
                kalshi_fee_multiplier=D(kalshi_fee_multiplier),
            )
        else:
            cushioned_price, cushioned, cushion_cents = D(original_kalshi_yes_price), original, 0

        gemini_depth_ok = D(gemini_available_size) >= q
        original_depth = kalshi_cumulative_depth(
            kalshi_book,
            side=side,
            yes_limit=D(original_kalshi_yes_price),
            quantity=q,
        )
        cushioned_depth = kalshi_cumulative_depth(
            kalshi_book,
            side=side,
            yes_limit=cushioned_price,
            quantity=q,
        )
        kalshi_depth_ok = bool(cushioned_depth["fillable"])
        supported = bool(economics_ok and gemini_depth_ok and kalshi_depth_ok)
        if not economics_ok:
            reason = "profit_per_contract_below_3c"
        elif not gemini_depth_ok:
            reason = "gemini_displayed_depth_insufficient"
        elif not kalshi_depth_ok:
            reason = "kalshi_cumulative_depth_insufficient"
        else:
            reason = "supported"

        row.update({
            "supported": supported,
            "reason": reason,
            "original_profit": str(original.total_profit),
            "original_profit_per_contract": str(original.profit_per_contract),
            "cushioned_profit": str(cushioned.total_profit),
            "cushioned_profit_per_contract": str(cushioned.profit_per_contract),
            "gemini_capital": str(cushioned.gemini_capital),
            "kalshi_capital": str(cushioned.kalshi_capital),
            "total_capital": str(cushioned.total_capital),
            "gemini_displayed_size": str(D(gemini_available_size)),
            "gemini_depth_ok": gemini_depth_ok,
            "original_kalshi_yes_limit": str(D(original_kalshi_yes_price)),
            "cushioned_kalshi_yes_limit": str(cushioned_price),
            "cushion_cents": cushion_cents,
            "original_kalshi_depth": original_depth,
            "cushioned_kalshi_depth": cushioned_depth,
            "kalshi_depth_ok": kalshi_depth_ok,
            "rescued_by_cushion": bool(not original_depth["fillable"] and cushioned_depth["fillable"]),
        })
        rows.append(row)
    return rows


def _capture_plan(original):
    def wrapped(**kwargs):
        plan = original(**kwargs)
        candidate = kwargs.get("candidate")
        skew = getattr(candidate, "quote_skew_ms", None)
        if skew is not None:
            _ENTRY_SKEW[(plan.asset, plan.direction, int(plan.opened_ms))] = int(skew)
        return plan
    return wrapped


def _checkpoint_trigger_gate(feed, plan, active_episodes, now_ms: int) -> tuple[bool, dict[str, Any]]:
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    entry_boundary = bool(readiness._PLAN_ENTRY_20BP.get(key, False))
    entry_skew = _ENTRY_SKEW.get(key)
    g, k = feed.gemini, feed.kalshi
    same_window = bool(
        feed.active
        and feed.pair.gemini_symbol == plan.gemini_symbol
        and feed.pair.kalshi_ticker == plan.kalshi_ticker
    )
    g_age = k_age = current_skew = None
    post_activation = strict_fresh = False
    gemini_price_ok = gemini_q5_depth_ok = False
    if same_window and g is not None and k is not None:
        if plan.direction == "GEMINI_YES_KALSHI_NO":
            gemini_price_ok = g.yes_ask <= plan.gemini_limit
            gemini_q5_depth_ok = g.yes_ask_size >= Q_TRIGGER
        elif plan.direction == "KALSHI_YES_GEMINI_NO":
            gemini_price_ok = g.no_ask <= plan.gemini_limit
            gemini_q5_depth_ok = g.no_ask_size >= Q_TRIGGER
        g_age = max(0, now_ms - int(g.ts_ms))
        k_age = max(0, now_ms - int(k.ts_ms))
        current_skew = abs(int(g.ts_ms) - int(k.ts_ms))
        post_activation = bool(
            int(g.ts_ms) >= int(feed.boundary_ms) and int(k.ts_ms) >= int(feed.boundary_ms)
        )
        strict_fresh = bool(
            post_activation
            and max(g_age, k_age) <= MAX_BOOK_AGE_MS
            and current_skew <= MAX_QUOTE_SKEW_MS
        )

    state = basis._BASIS_EPISODES.get((plan.asset, plan.direction))
    same_episode = readiness.guarded.same_original_episode(plan, active_episodes, state)
    snapshot = basis.basis_snapshot(plan.asset, now_ms) if same_window else None
    boundary_now = bool(snapshot and snapshot.get("research_double_boundary_gate_20bp_fresh_1000ms"))
    boundary_whole = bool(same_episode and state and getattr(state, "all_events_gate_20bp", False))
    ok = bool(
        D(plan.quantity) == Q_TRIGGER
        and D(plan.entry_locked_profit) >= MIN_TRIGGER_TOTAL_PROFIT
        and entry_skew is not None and entry_skew <= MAX_QUOTE_SKEW_MS
        and current_skew is not None and current_skew <= MAX_QUOTE_SKEW_MS
        and entry_boundary and same_episode and boundary_whole and boundary_now
        and strict_fresh and gemini_price_ok and gemini_q5_depth_ok
    )
    return ok, {
        "entry_quote_skew_ms": entry_skew,
        "current_quote_skew_ms": current_skew,
        "entry_double_boundary_10bp": entry_boundary,
        "same_window": same_window,
        "same_original_episode": same_episode,
        "double_boundary_10bp_held_through_episode": boundary_whole,
        "double_boundary_10bp_now": boundary_now,
        "post_activation_books": post_activation,
        "strict_fresh_250ms_and_50ms_skew": strict_fresh,
        "gemini_price_ok": gemini_price_ok,
        "gemini_q5_depth_ok": gemini_q5_depth_ok,
        "gemini_age_ms": g_age,
        "kalshi_age_ms": k_age,
    }


async def _probe(*, feed, plan, active_episodes, delays_ms=(50, 100)) -> None:
    del delays_ms
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    _COUNTERS["trigger_plans"] += 1
    try:
        if D(plan.quantity) != Q_TRIGGER:
            return
        await asyncio.sleep(0.050)
        now_ms = int(time.time() * 1000)
        gate_ok, gate = _checkpoint_trigger_gate(feed, plan, active_episodes, now_ms)
        if not gate_ok:
            _COUNTERS["gate_rejected"] += 1
            return

        mult = _FEE_MULTIPLIER.get(plan.asset)
        if mult is None:
            _COUNTERS["missing_fee_multiplier"] += 1
            return
        book = rollover._HUB._books.get(plan.kalshi_ticker)
        ready = plan.kalshi_ticker in rollover._HUB._snapshot_ready
        if book is None or not ready:
            _COUNTERS["no_full_kalshi_book"] += 1
            return

        g = feed.gemini
        if plan.direction == "GEMINI_YES_KALSHI_NO":
            gemini_size = D(g.yes_ask_size)
        else:
            gemini_size = D(g.no_ask_size)
        side, original_yes = _kalshi_side_and_yes_price(plan.direction, D(plan.kalshi_limit))
        rows = evaluate_capacity_sizes(
            direction=plan.direction,
            gemini_price=D(plan.gemini_limit),
            original_kalshi_yes_price=original_yes,
            gemini_available_size=gemini_size,
            kalshi_book=book,
            kalshi_fee_multiplier=mult,
        )
        supported = [D(row["quantity"]) for row in rows if row.get("supported")]
        max_supported_q = max(supported) if supported else D("0")
        _COUNTERS["capacity_checkpoints"] += 1
        _COUNTERS[f"max_q_{int(max_supported_q)}"] = _COUNTERS.get(f"max_q_{int(max_supported_q)}", 0) + 1

        logger.info(
            "CRYPTO_CAPACITY_CHECKPOINT %s",
            json.dumps(
                {
                    "asset": plan.asset,
                    "direction": plan.direction,
                    "opened_ms": int(plan.opened_ms),
                    "gemini_symbol": plan.gemini_symbol,
                    "kalshi_ticker": plan.kalshi_ticker,
                    "trigger_quantity": str(plan.quantity),
                    "trigger_entry_locked_profit": str(plan.entry_locked_profit),
                    "sizes": rows,
                    "max_supported_q": str(max_supported_q),
                    "kalshi_side": side,
                    **gate,
                    "capacity_interpretation": "READ_ONLY_SAME_PRICE_DISPLAYED_DEPTH_NOT_REALIZED_PNL",
                    "risk_gates_changed": False,
                    "trade_authorized": False,
                    "read_only": True,
                    "order_entry": False,
                },
                separators=(",", ":"),
                default=str,
            ),
        )
        logger.info(
            "CRYPTO_CAPACITY_SUMMARY %s",
            json.dumps(
                {
                    "counters": dict(_COUNTERS),
                    "sizes_tested": [str(q) for q in SIZES],
                    "min_profit_per_contract": str(MIN_PROFIT_PER_CONTRACT),
                    "max_cushion_cents": MAX_CUSHION_CENTS,
                    "risk_gates_changed": False,
                    "trade_authorized": False,
                    "read_only": True,
                    "order_entry": False,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    finally:
        _ENTRY_SKEW.pop(key, None)
        readiness._PLAN_ENTRY_20BP.pop(key, None)


def install_shadow() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    readiness.MIN_DOUBLE_BOUNDARY_BPS = D("10")
    rollover.install()
    target_cache.install()
    target_freshness.install()
    readiness.install()
    exact._plan_from_books = _capture_plan(exact._plan_from_books)

    prewarmed = exact.safe.base
    original_collect = prewarmed.collect_asset_prewarmed

    async def capture_fee(**kwargs):
        fee_meta = kwargs.get("fee_meta")
        asset = str(kwargs.get("asset") or "").upper()
        if fee_meta is not None and asset:
            _FEE_MULTIPLIER[asset] = D(str(fee_meta.fee_multiplier))
        return await original_collect(**kwargs)

    prewarmed.collect_asset_prewarmed = capture_fee
    exact._probe = _probe
    _INSTALLED = True
    logger.warning(
        "CRYPTO_CAPACITY_SHADOW_INSTALLED trigger_q=5 sizes=5,10,25 min_profit_per_contract=0.03 "
        "double_boundary_min_bps=10 checkpoint_ms=50 max_quote_skew_ms=50 max_cushion_cents=2 "
        "full_kalshi_depth=true risk_gates_changed=false trade_authorized=false order_entry=false"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    install_shadow()
    readiness.guarded.main()


if __name__ == "__main__":
    main()
