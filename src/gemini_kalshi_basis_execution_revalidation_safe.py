"""Episode-identity safety wrapper for exact Gemini/Kalshi fill revalidation.

A 50/100ms checkpoint must refer to the same quote-lock episode that opened the plan.
Matching only on direction is insufficient because an episode can close and reopen in
that interval. This wrapper binds both the HOT ActiveEpisode and basis episode state to
the original ``opened_ms`` before any exact-fill result can pass.
"""
from __future__ import annotations

import asyncio
import json
import time

from src import gemini_kalshi_basis_execution_revalidation as exact

basis = exact.basis


def same_original_episode(plan, active_episodes, state) -> bool:
    active = active_episodes.get(plan.direction)
    return bool(
        active is not None
        and state is not None
        and int(active.start_ms) == int(plan.opened_ms)
        and int(state.start_ms) == int(plan.opened_ms)
    )


async def _probe_same_episode(*, feed, plan, active_episodes, delays_ms=(50, 100)) -> None:
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
        post_activation_books = False
        if same_window and g is not None and k is not None:
            checks = exact.same_limits_marketable(plan, g, k)
            gemini_age_ms = max(0, now_ms - int(g.ts_ms))
            kalshi_age_ms = max(0, now_ms - int(k.ts_ms))
            book_skew_ms = abs(int(g.ts_ms) - int(k.ts_ms))
            post_activation_books = bool(
                int(g.ts_ms) >= int(feed.boundary_ms)
                and int(k.ts_ms) >= int(feed.boundary_ms)
            )
            strict_fresh = bool(
                post_activation_books
                and max(gemini_age_ms, kalshi_age_ms) <= 250
                and book_skew_ms <= 100
            )

        state = basis._BASIS_EPISODES.get((plan.asset, plan.direction))
        episode_still_open = same_original_episode(plan, active_episodes, state)
        basis_now = basis.basis_snapshot(plan.asset, now_ms) if same_window else None
        basis2_now = bool(basis_now and basis_now.get("research_proxy_gate_2bp_fresh_1000ms"))
        basis5_now = bool(basis_now and basis_now.get("research_proxy_gate_5bp_fresh_1000ms"))
        whole2 = bool(episode_still_open and state.all_events_gate_2bp)
        whole5 = bool(episode_still_open and state.all_events_gate_5bp)

        exact2 = bool(
            plan.entry_basis_2bp
            and episode_still_open
            and whole2
            and basis2_now
            and strict_fresh
            and checks["both_marketable"]
        )
        exact5 = bool(
            plan.entry_basis_5bp
            and episode_still_open
            and whole5
            and basis5_now
            and strict_fresh
            and checks["both_marketable"]
        )
        exact.COUNTERS[f"checks_{delay_ms}ms"] += 1
        if exact2:
            exact.COUNTERS[f"exact_2bp_{delay_ms}ms"] += 1
        if exact5:
            exact.COUNTERS[f"exact_5bp_{delay_ms}ms"] += 1

        exact.logger.info("CRYPTO_HOT_EXACT_REVALIDATION %s", json.dumps({
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
            "same_original_episode": episode_still_open,
            **checks,
            "post_activation_books": post_activation_books,
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
            "counters": dict(exact.COUNTERS),
            "profit_interpretation": "ENTRY_APPARENT_QUOTE_LOCK_NOT_REALIZED_PNL",
            "official_live_kaiko": False,
            "settlement_status": exact.hot.SETTLEMENT_STATUS,
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))


# The evaluator resolves its global _probe at runtime, so replacing it here applies the
# stronger identity invariant without duplicating or weakening the tested evaluator.
exact._probe = _probe_same_episode


def main() -> None:
    exact.main()


if __name__ == "__main__":
    main()
