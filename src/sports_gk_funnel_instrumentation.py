"""Non-invasive per-epoch diagnostics for the G/K live canary.

This module only wraps existing read/gate functions. It does not change quote
freshness, skew, participation, economics, revalidation, quantity, payloads, or
submission behavior. The purpose is to make MLB_GK_MULTI_NO_ATTEMPT explainable
before any production gate is reconsidered.

The capacity observer deliberately looks past the canary's q=1 cap using the same
fresh top-of-book objects and fee functions already supplied to ``evaluate_pair``.
It reports what *would* satisfy the 3c/contract floor at fixed q tiers, but never
changes ``max_quantity`` passed to the real evaluator and never labels the result
execution-eligible.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import json
import logging
from typing import Any

from src.sports_capacity_math import CapacityDirection, CapacityLeg, evaluate_direction

D = Decimal
logger = logging.getLogger("kalshi_arb.sports_gk_funnel")


def install(canary: Any) -> None:
    if getattr(canary, "_sports_gk_funnel_installed", False):
        return
    canary._sports_gk_funnel_installed = True

    stats: Counter[str] = Counter()
    family_participation_rejects: Counter[str] = Counter()
    capacity_latest: dict[str, dict[str, Any]] = {}
    capacity_peak: dict[str, dict[str, Any]] = {}
    original_run = canary.run
    original_next_update = canary._next_update
    original_participation = canary._participation_ok
    original_fresh = canary._fresh
    original_evaluate = canary.evaluate_pair
    original_same_limits = canary._same_original_limits
    original_validate = canary.validate_exact_probe_payloads
    original_gemini_books = canary.GeminiPropBooks
    original_kalshi_books = canary.EventingKalshiCrossBooks

    class InstrumentedGeminiBooks(original_gemini_books):
        def quote(self, *args: Any, **kwargs: Any) -> Any:
            stats["gemini_quote_calls"] += 1
            out = super().quote(*args, **kwargs)
            if out is None:
                stats["gemini_quote_missing_or_stale"] += 1
            else:
                stats["gemini_quote_present"] += 1
            return out

    class InstrumentedKalshiBooks(original_kalshi_books):
        async def quote(self, *args: Any, **kwargs: Any) -> Any:
            stats["kalshi_quote_calls"] += 1
            out = await super().quote(*args, **kwargs)
            if out is None:
                stats["kalshi_quote_missing_or_stale"] += 1
            else:
                stats["kalshi_quote_present"] += 1
            return out

    async def next_update(*args: Any, **kwargs: Any) -> Any:
        out = await original_next_update(*args, **kwargs)
        stats["trigger_updates"] += 1
        return out

    def participation(row: Any, games: dict[Any, Any]):
        stats["participation_checks"] += 1
        ok, proof, status = original_participation(row, games)
        if ok:
            stats["participation_pass"] += 1
        else:
            stats["participation_reject"] += 1
            family_participation_rejects[str(getattr(row, "family", "unknown"))] += 1
            if str(status or "").lower() != "live":
                stats["participation_not_live"] += 1
            else:
                stats["participation_live_but_unproven"] += 1
        return ok, proof, status

    def fresh(g: Any, k: Any, now_ms: int) -> bool:
        stats["fresh_checks"] += 1
        ok = original_fresh(g, k, now_ms)
        if ok:
            stats["fresh_pass"] += 1
        else:
            stats["fresh_reject"] += 1
            try:
                stats["fresh_g_age_ms_sum"] += max(0, now_ms - int(g.ts_ms))
                stats["fresh_k_age_ms_sum"] += max(0, now_ms - int(k.ts_ms))
                stats["fresh_skew_ms_sum"] += abs(int(g.ts_ms) - int(k.ts_ms))
            except Exception:
                pass
        return ok

    def observe_capacity(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """Read-only capacity telemetry; failures must never affect the canary."""
        try:
            if len(args) < 2:
                return
            g, k = args[0], args[1]
            gemini_fee = kwargs.get("gemini_fee")
            kalshi_fee = kwargs.get("kalshi_fee")
            if not callable(gemini_fee) or not callable(kalshi_fee):
                return

            directions = (
                CapacityDirection(
                    "GEMINI_YES_KALSHI_NO",
                    f"{g.market_id}|{k.market_id}",
                    (
                        CapacityLeg("gemini", str(g.market_id), "YES", D(g.yes_ask), D(g.yes_ask_size), gemini_fee),
                        CapacityLeg("kalshi", str(k.market_id), "NO", D(k.no_ask), D(k.no_ask_size), kalshi_fee),
                    ),
                ),
                CapacityDirection(
                    "KALSHI_YES_GEMINI_NO",
                    f"{g.market_id}|{k.market_id}",
                    (
                        CapacityLeg("kalshi", str(k.market_id), "YES", D(k.yes_ask), D(k.yes_ask_size), kalshi_fee),
                        CapacityLeg("gemini", str(g.market_id), "NO", D(g.no_ask), D(g.no_ask_size), gemini_fee),
                    ),
                ),
            )
            for direction in directions:
                result = evaluate_direction(
                    direction,
                    min_profit_per_contract=D(str(canary.MIN_PROFIT)),
                )
                marker = f"{direction.settlement_key}|{direction.name}"
                capacity_latest[marker] = result
                tier = int(result.get("largest_qualifying_tier") or 0)
                if tier > 0:
                    stats["capacity_pair_direction_pass"] += 1
                for tier_row in result.get("tiers") or []:
                    q = int(tier_row.get("quantity") or 0)
                    status = str(tier_row.get("status") or "UNKNOWN").lower()
                    stats[f"capacity_q{q}_{status}"] += 1

                previous = capacity_peak.get(marker)
                prev_profit = D(str((previous or {}).get("largest_qualifying_locked_profit") or "0"))
                profit = D(str(result.get("largest_qualifying_locked_profit") or "0"))
                prev_tier = int((previous or {}).get("largest_qualifying_tier") or 0)
                if previous is None or tier > prev_tier or (tier == prev_tier and profit > prev_profit):
                    capacity_peak[marker] = result
        except Exception:
            stats["capacity_observer_error"] += 1

    def evaluate(*args: Any, **kwargs: Any):
        stats["economics_checks"] += 1
        observe_capacity(args, kwargs)
        out = original_evaluate(*args, **kwargs)
        stats["raw_economic_candidates"] += len(out)
        qualifying = [
            c for c in out
            if c.locked_profit >= canary.MIN_PROFIT and c.net_cost <= canary.HARD_BUDGET_USD
        ]
        if qualifying:
            stats["economics_pass"] += 1
            stats["qualifying_economic_candidates"] += len(qualifying)
        else:
            stats["economics_reject"] += 1
        return out

    def same_limits(*args: Any, **kwargs: Any) -> bool:
        stats["same_limit_checks"] += 1
        ok = original_same_limits(*args, **kwargs)
        stats["same_limit_pass" if ok else "same_limit_reject"] += 1
        return ok

    def validate(*args: Any, **kwargs: Any):
        stats["payload_validation_checks"] += 1
        out = original_validate(*args, **kwargs)
        if out >= canary.MIN_PROFIT:
            stats["payload_validation_pass"] += 1
        else:
            stats["payload_validation_reject"] += 1
        return out

    async def run(*args: Any, **kwargs: Any) -> int:
        stats.clear()
        family_participation_rejects.clear()
        capacity_latest.clear()
        capacity_peak.clear()
        rc: int | None = None
        error_type: str | None = None
        try:
            rc = await original_run(*args, **kwargs)
            return rc
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            fresh_rejects = stats.get("fresh_reject", 0)
            peak_rows = sorted(
                capacity_peak.values(),
                key=lambda row: D(str(row.get("largest_qualifying_locked_profit") or "0")),
                reverse=True,
            )
            peak_positive = [row for row in peak_rows if int(row.get("largest_qualifying_tier") or 0) > 0]
            peak_profit_sum = sum(
                (D(str(row.get("largest_qualifying_locked_profit") or "0")) for row in peak_positive),
                D("0"),
            )
            payload = {
                "run_id": canary._run_id() if hasattr(canary, "_run_id") else None,
                "rc": rc,
                "error_type": error_type,
                "stats": dict(stats),
                "participation_rejects_by_family": dict(family_participation_rejects),
                "fresh_reject_avg_g_age_ms": (
                    stats.get("fresh_g_age_ms_sum", 0) / fresh_rejects if fresh_rejects else None
                ),
                "fresh_reject_avg_k_age_ms": (
                    stats.get("fresh_k_age_ms_sum", 0) / fresh_rejects if fresh_rejects else None
                ),
                "fresh_reject_avg_skew_ms": (
                    stats.get("fresh_skew_ms_sum", 0) / fresh_rejects if fresh_rejects else None
                ),
                "capacity_observed": {
                    "tiers": [1, 5, 10, 25, 50, 100],
                    "unique_pair_directions": len(capacity_peak),
                    "pair_directions_with_3c_capacity": len(peak_positive),
                    "sum_per_pair_peak_locked_profit": str(peak_profit_sum),
                    "top_peak_rows": peak_positive[:10],
                    "latest_rows": list(capacity_latest.values())[:10],
                    "execution_eligible": False,
                    "read_only": True,
                    "warning": (
                        "Per-epoch peak observations are not simultaneous, not daily capacity, and not realized P&L. "
                        "They are used only to size future certified canaries."
                    ),
                },
                "behavior_changed": False,
            }
            logger.warning("MLB_GK_MULTI_FUNNEL %s", json.dumps(payload, separators=(",", ":"), default=str))

    canary.GeminiPropBooks = InstrumentedGeminiBooks
    canary.EventingKalshiCrossBooks = InstrumentedKalshiBooks
    canary._next_update = next_update
    canary._participation_ok = participation
    canary._fresh = fresh
    canary.evaluate_pair = evaluate
    canary._same_original_limits = same_limits
    canary.validate_exact_probe_payloads = validate
    canary.run = run
