"""Non-invasive per-epoch diagnostics for the G/K live canary.

This module only wraps existing read/gate functions. It does not change quote
freshness, skew, participation, economics, revalidation, quantity, payloads, or
submission behavior. The purpose is to make MLB_GK_MULTI_NO_ATTEMPT explainable
before any production gate is reconsidered.
"""
from __future__ import annotations

from collections import Counter
import json
import logging
from typing import Any

logger = logging.getLogger("kalshi_arb.sports_gk_funnel")


def install(canary: Any) -> None:
    if getattr(canary, "_sports_gk_funnel_installed", False):
        return
    canary._sports_gk_funnel_installed = True

    stats: Counter[str] = Counter()
    family_participation_rejects: Counter[str] = Counter()
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

    def evaluate(*args: Any, **kwargs: Any):
        stats["economics_checks"] += 1
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
