"""Exact-limit execution revalidation for the prewarmed basis-aware crypto shadow.

For each newly opened >=3c quote-lock episode whose entry has a fresh >=2bp spot
proxy margin, capture the exact Gemini/Kalshi leg limits and quantity. At 50ms and
100ms, re-check whether those *same original limit orders* are simultaneously
marketable with sufficient displayed top-level depth, fresh books, and a basis gate
that has remained valid throughout the still-open episode.

This is read-only fillability research. It submits, previews, cancels, and modifies no
orders. Gemini public spot is still only a proxy for the official Kaiko settlement
index, so no result authorizes trading.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any

from src import gemini_kalshi_prewarmed_basis_shadow as basis

safe = basis.prewarmed
base = safe.base
hot = basis.hot
D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_execution_revalidation")
_SAFE_EVALUATE = safe._evaluate_event_sequential
_PROBES: set[asyncio.Task[Any]] = set()
COUNTERS = {
    "opens_2bp": 0,
    "opens_5bp": 0,
    "checks_50ms": 0,
    "checks_100ms": 0,
    "exact_2bp_50ms": 0,
    "exact_2bp_100ms": 0,
    "exact_5bp_50ms": 0,
    "exact_5bp_100ms": 0,
}


@dataclass(frozen=True)
class ExactLimitPlan:
    asset: str
    direction: str
    quantity: Decimal
    opened_ms: int
    gemini_symbol: str
    kalshi_ticker: str
    gemini_limit: Decimal
    kalshi_limit: Decimal
    entry_locked_profit: Decimal
    entry_capital: Decimal
    entry_basis_2bp: bool
    entry_basis_5bp: bool


def _plan_from_books(*, asset: str, pair, direction: str, quantity: Decimal, candidate, g, k, opened_ms: int, snapshot) -> ExactLimitPlan:
    if direction == "GEMINI_YES_KALSHI_NO":
        gemini_limit = g.yes_ask
        kalshi_limit = k.no_ask
    elif direction == "KALSHI_YES_GEMINI_NO":
        gemini_limit = g.no_ask
        kalshi_limit = k.yes_ask
    else:
        raise ValueError(direction)
    return ExactLimitPlan(
        asset=asset,
        direction=direction,
        quantity=D(quantity),
        opened_ms=int(opened_ms),
        gemini_symbol=pair.gemini_symbol,
        kalshi_ticker=pair.kalshi_ticker,
        gemini_limit=D(gemini_limit),
        kalshi_limit=D(kalshi_limit),
        entry_locked_profit=D(candidate.locked_profit),
        entry_capital=D(candidate.net_cost),
        entry_basis_2bp=bool(snapshot and snapshot.get("research_proxy_gate_2bp_fresh_1000ms")),
        entry_basis_5bp=bool(snapshot and snapshot.get("research_proxy_gate_5bp_fresh_1000ms")),
    )


def same_limits_marketable(plan: ExactLimitPlan, g, k) -> dict[str, bool]:
    q = D(plan.quantity)
    if plan.direction == "GEMINI_YES_KALSHI_NO":
        gemini_price_ok = g.yes_ask <= plan.gemini_limit
        gemini_depth_ok = g.yes_ask_size >= q
        kalshi_price_ok = k.no_ask <= plan.kalshi_limit
        kalshi_depth_ok = k.no_ask_size >= q
    elif plan.direction == "KALSHI_YES_GEMINI_NO":
        gemini_price_ok = g.no_ask <= plan.gemini_limit
        gemini_depth_ok = g.no_ask_size >= q
        kalshi_price_ok = k.yes_ask <= plan.kalshi_limit
        kalshi_depth_ok = k.yes_ask_size >= q
    else:
        raise ValueError(plan.direction)
    return {
        "gemini_price_ok": gemini_price_ok,
        "gemini_depth_ok": gemini_depth_ok,
        "kalshi_price_ok": kalshi_price_ok,
        "kalshi_depth_ok": kalshi_depth_ok,
        "both_marketable": gemini_price_ok and gemini_depth_ok and kalshi_price_ok and kalshi_depth_ok,
    }


async def _probe(*, feed, plan: ExactLimitPlan, active_episodes: dict[str, Any], delays_ms=(50, 100)) -> None:
    previous = 0
    for delay_ms in delays_ms:
        await asyncio.sleep(max(0, delay_ms - previous) / 1000)
        previous = delay_ms
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
        strict_fresh = False
        book_skew_ms = None
        gemini_age_ms = None
        kalshi_age_ms = None
        if same_window and g is not None and k is not None:
            checks = same_limits_marketable(plan, g, k)
            gemini_age_ms = max(0, now_ms - int(g.ts_ms))
            kalshi_age_ms = max(0, now_ms - int(k.ts_ms))
            book_skew_ms = abs(int(g.ts_ms) - int(k.ts_ms))
            strict_fresh = max(gemini_age_ms, kalshi_age_ms) <= 250 and book_skew_ms <= 100

        state = basis._BASIS_EPISODES.get((plan.asset, plan.direction))
        episode_still_open = plan.direction in active_episodes and state is not None
        basis_now = basis.basis_snapshot(plan.asset, now_ms) if same_window else None
        basis2_now = bool(basis_now and basis_now.get("research_proxy_gate_2bp_fresh_1000ms"))
        basis5_now = bool(basis_now and basis_now.get("research_proxy_gate_5bp_fresh_1000ms"))
        whole2 = bool(state and state.all_events_gate_2bp)
        whole5 = bool(state and state.all_events_gate_5bp)

        exact2 = bool(plan.entry_basis_2bp and episode_still_open and whole2 and basis2_now and strict_fresh and checks["both_marketable"])
        exact5 = bool(plan.entry_basis_5bp and episode_still_open and whole5 and basis5_now and strict_fresh and checks["both_marketable"])
        COUNTERS[f"checks_{delay_ms}ms"] += 1
        if exact2:
            COUNTERS[f"exact_2bp_{delay_ms}ms"] += 1
        if exact5:
            COUNTERS[f"exact_5bp_{delay_ms}ms"] += 1

        logger.info("CRYPTO_HOT_EXACT_REVALIDATION %s", json.dumps({
            "asset": plan.asset,
            "direction": plan.direction,
            "opened_ms": plan.opened_ms,
            "delay_ms": delay_ms,
            "quantity": str(plan.quantity),
            "gemini_symbol": plan.gemini_symbol,
            "kalshi_ticker": plan.kalshi_ticker,
            "original_gemini_limit": str(plan.gemini_limit),
            "original_kalshi_limit": str(plan.kalshi_limit),
            "entry_locked_quote_profit": str(plan.entry_locked_profit),
            "entry_capital": str(plan.entry_capital),
            "entry_basis_2bp": plan.entry_basis_2bp,
            "entry_basis_5bp": plan.entry_basis_5bp,
            "same_window": same_window,
            "episode_still_open": episode_still_open,
            **checks,
            "gemini_age_ms": gemini_age_ms,
            "kalshi_age_ms": kalshi_age_ms,
            "book_skew_ms": book_skew_ms,
            "strict_fresh_250ms": strict_fresh,
            "basis_2bp_now": basis2_now,
            "basis_5bp_now": basis5_now,
            "basis_2bp_held_through_episode": whole2,
            "basis_5bp_held_through_episode": whole5,
            "exact_2bp_fillable": exact2,
            "exact_5bp_fillable": exact5,
            "counters": dict(COUNTERS),
            "profit_interpretation": "ENTRY_APPARENT_QUOTE_LOCK_NOT_REALIZED_PNL",
            "official_live_kaiko": False,
            "settlement_status": hot.SETTLEMENT_STATUS,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))


def _evaluate_with_revalidation(*, feed, event, kalshi_fee, max_quote_skew_ms, capital_limit, min_profit, active_episodes, fh) -> None:
    before = set(active_episodes)
    _SAFE_EVALUATE(
        feed=feed,
        event=event,
        kalshi_fee=kalshi_fee,
        max_quote_skew_ms=max_quote_skew_ms,
        capital_limit=capital_limit,
        min_profit=min_profit,
        active_episodes=active_episodes,
        fh=fh,
    )
    if int(event[1]) < int(feed.boundary_ms):
        return
    g = getattr(feed, "eval_gemini", None)
    k = getattr(feed, "eval_kalshi", None)
    if g is None or k is None:
        return
    all_small = hot._small_q_candidates(
        g, k,
        kalshi_fee=kalshi_fee,
        max_quote_skew_ms=max_quote_skew_ms,
        capital_limit=capital_limit,
    )
    best = {}
    for q, candidate in all_small:
        prior = best.get(candidate.direction)
        if prior is None or candidate.locked_profit > prior[1].locked_profit:
            best[candidate.direction] = (q, candidate)
    qualifying = {d: item for d, item in best.items() if item[1].locked_profit >= min_profit}
    for direction, (q, candidate) in qualifying.items():
        if direction in before or direction not in active_episodes:
            continue
        snapshot = basis.basis_snapshot(feed.asset, int(event[1]))
        if not snapshot or not snapshot.get("research_proxy_gate_2bp_fresh_1000ms"):
            continue
        plan = _plan_from_books(
            asset=feed.asset,
            pair=feed.pair,
            direction=direction,
            quantity=D(q),
            candidate=candidate,
            g=g,
            k=k,
            opened_ms=int(event[1]),
            snapshot=snapshot,
        )
        COUNTERS["opens_2bp"] += 1
        if plan.entry_basis_5bp:
            COUNTERS["opens_5bp"] += 1
        task = asyncio.get_running_loop().create_task(
            _probe(feed=feed, plan=plan, active_episodes=active_episodes),
            name=f"exact-revalidate-{feed.asset}-{direction}-{plan.opened_ms}",
        )
        _PROBES.add(task)
        task.add_done_callback(_PROBES.discard)


def install() -> None:
    basis.install()
    base._evaluate_event = _evaluate_with_revalidation


def main() -> None:
    install()
    logger.info("CRYPTO_HOT_EXACT_REVALIDATION_ENABLED delays_ms=50,100 basis_entry_min=2bp no_orders=true")
    safe.main()


if __name__ == "__main__":
    main()
