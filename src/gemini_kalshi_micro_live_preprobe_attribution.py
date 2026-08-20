"""Read-only q=1 pre-probe attribution for the production-shaped 10bp/50ms lane.

This diagnostic intentionally imports no order transport.  It preserves the existing
quote-lock scanner and exact-plan creation path, but records why >=3c q=1 candidates
do not reach the exact 50ms readiness checkpoint.  The internal readiness module keeps
its historical ``20bp`` field names; this wrapper changes the configured threshold to
10bp and labels every emitted diagnostic explicitly as 10bp/50ms.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
import logging
import time
from typing import Any

from src import gemini_kalshi_micro_live_readiness_20bp as readiness

D = Decimal
exact = readiness.exact
basis = readiness.basis
hot = exact.hot
logger = logging.getLogger("kalshi_arb.gemini_kalshi_micro_live_preprobe_attribution")

MIN_PROFIT = D("0.03")
MIN_DOUBLE_BOUNDARY_BPS = D("10")
MAX_QUOTE_SKEW_MS = 50
MAX_BOOK_AGE_MS = 250
SUMMARY_INTERVAL_MS = 10_000

_ORIGINAL_EVALUATE = exact._evaluate_with_revalidation
_COUNTERS: Counter[str] = Counter()
_PLAN_KEYS: set[tuple[str, str, int]] = set()
_ENTRY_SKEW: dict[tuple[str, str, int], int] = {}
_LAST_SUMMARY_MS = 0
_INSTALLED = False


def _emit_summary(now_ms: int) -> None:
    global _LAST_SUMMARY_MS
    if now_ms - _LAST_SUMMARY_MS < SUMMARY_INTERVAL_MS:
        return
    _LAST_SUMMARY_MS = now_ms
    logger.info(
        "CRYPTO_Q1_PREPROBE_SUMMARY %s",
        json.dumps(
            {
                "counters": dict(_COUNTERS),
                "shape": "q1_10bp_50ms",
                "minimum_profit": str(MIN_PROFIT),
                "minimum_double_boundary_bps": str(MIN_DOUBLE_BOUNDARY_BPS),
                "max_quote_skew_ms": MAX_QUOTE_SKEW_MS,
                "risk_gates_changed": False,
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _capture_plan(original):
    def wrapped(**kwargs):
        plan = original(**kwargs)
        key = (plan.asset, plan.direction, int(plan.opened_ms))
        _PLAN_KEYS.add(key)
        candidate = kwargs.get("candidate")
        skew = getattr(candidate, "quote_skew_ms", None)
        if skew is not None:
            _ENTRY_SKEW[key] = int(skew)
        return plan

    return wrapped


def _best_threshold_candidates(g, k, *, kalshi_fee, max_quote_skew_ms, capital_limit, min_profit):
    best: dict[str, tuple[Any, Any]] = {}
    for q, candidate in hot._small_q_candidates(
        g,
        k,
        kalshi_fee=kalshi_fee,
        max_quote_skew_ms=max_quote_skew_ms,
        capital_limit=capital_limit,
    ):
        prior = best.get(candidate.direction)
        if prior is None or candidate.locked_profit > prior[1].locked_profit:
            best[candidate.direction] = (q, candidate)
    return {
        direction: item
        for direction, item in best.items()
        if D(item[1].locked_profit) >= D(min_profit)
    }


def _evaluate_with_attribution(
    *,
    feed,
    event,
    kalshi_fee,
    max_quote_skew_ms,
    capital_limit,
    min_profit,
    active_episodes,
    fh,
) -> None:
    before = set(active_episodes)
    _ORIGINAL_EVALUATE(
        feed=feed,
        event=event,
        kalshi_fee=kalshi_fee,
        max_quote_skew_ms=max_quote_skew_ms,
        capital_limit=capital_limit,
        min_profit=min_profit,
        active_episodes=active_episodes,
        fh=fh,
    )

    event_ms = int(event[1])
    if event_ms < int(feed.boundary_ms):
        _COUNTERS["pre_activation_events"] += 1
        _emit_summary(event_ms)
        return

    g = getattr(feed, "eval_gemini", None)
    k = getattr(feed, "eval_kalshi", None)
    if g is None or k is None:
        _COUNTERS["post_activation_missing_eval_book"] += 1
        _emit_summary(event_ms)
        return

    try:
        qualifying = _best_threshold_candidates(
            g,
            k,
            kalshi_fee=kalshi_fee,
            max_quote_skew_ms=max_quote_skew_ms,
            capital_limit=capital_limit,
            min_profit=min_profit,
        )
    except Exception:
        _COUNTERS["candidate_recompute_error"] += 1
        _emit_summary(event_ms)
        return

    for direction, (q, candidate) in qualifying.items():
        _COUNTERS["raw_threshold_candidates"] += 1
        if D(q) != D("1"):
            _COUNTERS["raw_threshold_non_q1_best"] += 1
        skew = getattr(candidate, "quote_skew_ms", None)
        if skew is None:
            _COUNTERS["raw_threshold_entry_skew_missing"] += 1
        elif int(skew) > MAX_QUOTE_SKEW_MS:
            _COUNTERS["raw_threshold_entry_skew_gt_50ms"] += 1

        if direction in before:
            _COUNTERS["raw_threshold_existing_episode"] += 1
            continue
        active = active_episodes.get(direction)
        if active is None:
            _COUNTERS["raw_threshold_episode_not_opened"] += 1
            continue

        opened_ms = int(active.start_ms)
        key = (feed.asset, direction, opened_ms)
        if key in _PLAN_KEYS:
            _COUNTERS["preprobe_plan_scheduled"] += 1
            continue

        snapshot = basis.basis_snapshot(feed.asset, event_ms)
        if snapshot is None:
            _COUNTERS["preprobe_reject_missing_basis_snapshot"] += 1
        elif not snapshot.get("research_proxy_gate_2bp_fresh_1000ms"):
            _COUNTERS["preprobe_reject_proxy_basis_entry"] += 1
        else:
            _COUNTERS["preprobe_reject_unknown_after_episode_open"] += 1
            logger.info(
                "CRYPTO_Q1_PREPROBE_ATTRIBUTION %s",
                json.dumps(
                    {
                        "asset": feed.asset,
                        "direction": direction,
                        "opened_ms": opened_ms,
                        "reason": "unknown_after_episode_open",
                        "quantity": str(q),
                        "locked_profit": str(candidate.locked_profit),
                        "entry_quote_skew_ms": skew,
                        "shape": "q1_10bp_50ms",
                        "trade_authorized": False,
                        "read_only": True,
                        "order_entry": False,
                    },
                    separators=(",", ":"),
                    default=str,
                ),
            )

    _emit_summary(event_ms)


def _checkpoint_reason(
    *,
    entry10: bool,
    entry_skew_ms: int | None,
    same_window: bool,
    same_episode: bool,
    whole10: bool,
    gate10_now: bool,
    post_activation_books: bool,
    gemini_age_ms: int | None,
    kalshi_age_ms: int | None,
    current_skew_ms: int | None,
    checks: dict[str, bool],
    quantity: Decimal,
    locked_profit: Decimal,
) -> str:
    if not entry10:
        return "entry_double_boundary_lt_10bp_or_missing"
    if entry_skew_ms is None:
        return "entry_quote_skew_missing"
    if entry_skew_ms > MAX_QUOTE_SKEW_MS:
        return "entry_quote_skew_gt_50ms"
    if not same_window:
        return "market_window_changed"
    if not same_episode:
        return "original_episode_closed_or_reopened"
    if not whole10:
        return "double_boundary_not_held_through_episode"
    if not gate10_now:
        return "current_double_boundary_lt_10bp_or_missing"
    if not post_activation_books:
        return "book_timestamp_before_activation"
    if gemini_age_ms is None or kalshi_age_ms is None:
        return "book_age_missing"
    if max(gemini_age_ms, kalshi_age_ms) > MAX_BOOK_AGE_MS:
        return "book_stale_gt_250ms"
    if current_skew_ms is None:
        return "current_quote_skew_missing"
    if current_skew_ms > MAX_QUOTE_SKEW_MS:
        return "current_quote_skew_gt_50ms"
    if not checks["gemini_price_ok"]:
        return "gemini_original_limit_no_longer_marketable"
    if not checks["gemini_depth_ok"]:
        return "gemini_q1_depth_missing"
    if not checks["kalshi_price_ok"]:
        return "kalshi_original_limit_no_longer_marketable"
    if not checks["kalshi_depth_ok"]:
        return "kalshi_q1_depth_missing"
    if quantity != D("1"):
        return "quantity_not_q1"
    if locked_profit < MIN_PROFIT:
        return "profit_below_3c"
    return "ready_50ms"


async def _probe_with_attribution(*, feed, plan, active_episodes, delays_ms=(50, 100)) -> None:
    del delays_ms
    key = (plan.asset, plan.direction, int(plan.opened_ms))
    entry10 = bool(readiness._PLAN_ENTRY_20BP.get(key, False))
    entry_skew_ms = _ENTRY_SKEW.get(key)
    try:
        await asyncio.sleep(0.050)
        now_ms = int(time.time() * 1000)
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
        gemini_age_ms = kalshi_age_ms = current_skew_ms = None
        post_activation_books = False
        if same_window and g is not None and k is not None:
            checks = exact.same_limits_marketable(plan, g, k)
            gemini_age_ms = max(0, now_ms - int(g.ts_ms))
            kalshi_age_ms = max(0, now_ms - int(k.ts_ms))
            current_skew_ms = abs(int(g.ts_ms) - int(k.ts_ms))
            post_activation_books = bool(
                int(g.ts_ms) >= int(feed.boundary_ms)
                and int(k.ts_ms) >= int(feed.boundary_ms)
            )

        state = basis._BASIS_EPISODES.get((plan.asset, plan.direction))
        same_episode = readiness.guarded.same_original_episode(plan, active_episodes, state)
        snapshot = basis.basis_snapshot(plan.asset, now_ms) if same_window else None
        gate10_now = bool(
            snapshot and snapshot.get("research_double_boundary_gate_20bp_fresh_1000ms")
        )
        whole10 = bool(
            same_episode and state and getattr(state, "all_events_gate_20bp", False)
        )
        reason = _checkpoint_reason(
            entry10=entry10,
            entry_skew_ms=entry_skew_ms,
            same_window=same_window,
            same_episode=same_episode,
            whole10=whole10,
            gate10_now=gate10_now,
            post_activation_books=post_activation_books,
            gemini_age_ms=gemini_age_ms,
            kalshi_age_ms=kalshi_age_ms,
            current_skew_ms=current_skew_ms,
            checks=checks,
            quantity=D(plan.quantity),
            locked_profit=D(plan.entry_locked_profit),
        )
        _COUNTERS["checkpoint_50ms"] += 1
        _COUNTERS[f"checkpoint_reason_{reason}"] += 1

        logger.info(
            "CRYPTO_Q1_PREPROBE_ATTRIBUTION %s",
            json.dumps(
                {
                    "asset": plan.asset,
                    "direction": plan.direction,
                    "opened_ms": int(plan.opened_ms),
                    "delay_ms": 50,
                    "reason": reason,
                    "ready_50ms": reason == "ready_50ms",
                    "quantity": str(plan.quantity),
                    "entry_locked_quote_profit": str(plan.entry_locked_profit),
                    "entry_quote_skew_ms": entry_skew_ms,
                    "current_quote_skew_ms": current_skew_ms,
                    "entry_double_boundary_10bp": entry10,
                    "same_window": same_window,
                    "same_original_episode": same_episode,
                    "double_boundary_10bp_held_through_episode": whole10,
                    "double_boundary_10bp_now": gate10_now,
                    "post_activation_books": post_activation_books,
                    "gemini_age_ms": gemini_age_ms,
                    "kalshi_age_ms": kalshi_age_ms,
                    **checks,
                    "shape": "q1_10bp_50ms",
                    "risk_gates_changed": False,
                    "trade_authorized": False,
                    "read_only": True,
                    "order_entry": False,
                },
                separators=(",", ":"),
                default=str,
            ),
        )
        _emit_summary(now_ms)
    finally:
        _PLAN_KEYS.discard(key)
        _ENTRY_SKEW.pop(key, None)
        readiness._PLAN_ENTRY_20BP.pop(key, None)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # Reuse the proven readiness stack, changing only the read-only boundary threshold.
    readiness.MIN_DOUBLE_BOUNDARY_BPS = MIN_DOUBLE_BOUNDARY_BPS
    readiness.install()

    # Preserve its exact plan creation, then observe the same candidate skew and plan id.
    exact._plan_from_books = _capture_plan(exact._plan_from_books)
    exact._probe = _probe_with_attribution
    exact._evaluate_with_revalidation = _evaluate_with_attribution
    _INSTALLED = True

    logger.warning(
        "CRYPTO_Q1_PREPROBE_ATTRIBUTION_INSTALLED q=1 min_profit=0.03 "
        "double_boundary_min_bps=10 checkpoint_ms=50 entry_and_current_max_skew_ms=50 "
        "trade_authorized=false read_only=true order_entry=false"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    install()
    readiness.guarded.main()


if __name__ == "__main__":
    main()
