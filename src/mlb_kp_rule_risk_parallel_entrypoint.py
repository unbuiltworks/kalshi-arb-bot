"""Production policy wrapper for the live K/P MLB prop rule-risk lane.

This keeps every existing execution invariant in ``mlb_kp_rule_risk_live_q1``
and only fixes portfolio-level throughput policy:

* a confirmed two-leg hedge awaiting settlement does not serialize unrelated q=1
  opportunities;
* ambiguous, one-leg, reserved-only, or otherwise unresolved exposure still
  blocks all new mutation;
* an exact Polymarket/Kalshi candidate pair that has already filled two legs is
  permanently duplicate-blocked, even across process restarts;
* the shared cumulative live sports ceiling remains the environment-authorized
  value (production is explicitly set to $20 in Railway).

The underlying lane remains classified as LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB.
"""
from __future__ import annotations

from decimal import Decimal
import logging
from typing import Any

from src import mlb_kp_rule_risk_live_q1 as core
from src import mlb_kp_rule_risk_event_rollover as rollover

logger = logging.getLogger("kalshi_arb.mlb_kp_rule_risk_parallel_entrypoint")
D = Decimal

_TERMINAL = {
    "REALIZED",
    "TRADE_REALIZED",
    "SETTLED",
    "CAPITAL_RELEASED",
    "SETTLED_COMPATIBLE",
}
_DANGEROUS = {
    "AMBIGUOUS_SUBMISSION",
    "AMBIGUOUS",
    "ONE_LEG_FILLED",
    "FIRST_LEG_FILLED",
    "SECOND_LEG_NOT_CONFIRMED",
    "HARD_HALT",
}
_DUPLICATE_PREFIX = "duplicate K/P settlement key blocks execution:"


def _d(value: Any) -> D:
    try:
        return D(str(value or "0"))
    except Exception:
        return D("0")


def _contains_hard_halt(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "hard_halt_required" and bool(child):
                return True
            if _contains_hard_halt(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_hard_halt(child) for child in value)
    return False


def _safe_unresolved(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Block naked/ambiguous exposure, not fully hedged settlement latency."""
    for row in core._shared_rows(rows):
        status = str(row.get("status") or "").upper()
        if status in _TERMINAL:
            continue

        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        legs_filled = int(row.get("legs_filled") or 0)

        # Confirmed two-leg exposure is already economically hedged. It still
        # contributes to the shared capital ceiling through _budget_used().
        if legs_filled >= 2 and status not in _DANGEROUS and not _contains_hard_halt(metadata):
            continue

        if status in _DANGEROUS or _contains_hard_halt(metadata):
            return row

        actual = _d(row.get("capital_filled"))
        reserved = _d(metadata.get("reserved_usd"))
        if actual > 0 or reserved > 0:
            return row
    return None


def _candidate_pair(value: Any) -> tuple[str, str] | None:
    """Find an exact K/P venue-id pair in nested ledger metadata."""
    if isinstance(value, dict):
        poly = value.get("poly_slug")
        kalshi = value.get("kalshi_ticker")
        if poly and kalshi:
            return str(poly), str(kalshi)
        for child in value.values():
            found = _candidate_pair(child)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _candidate_pair(child)
            if found is not None:
                return found
    return None


def _already_filled(rows: list[dict[str, Any]], *, poly_slug: str, kalshi_ticker: str) -> str | None:
    target = (poly_slug, kalshi_ticker)
    for row in rows:
        if str(row.get("mode") or "") not in {"LIVE", "LIVE_PROBE"}:
            continue
        if int(row.get("legs_filled") or 0) < 2:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if _candidate_pair(metadata) == target:
            return str(row.get("trade_id") or "unknown")
    return None


_original_reserve = core.reserve


async def _reserve_with_duplicate_guard(plan: core.DirectPlan, trade_id: str) -> None:
    rows = await core._trades()
    prior = _already_filled(
        rows,
        poly_slug=plan.candidate.poly_slug,
        kalshi_ticker=plan.candidate.kalshi_ticker,
    )
    if prior is not None:
        raise RuntimeError(
            f"{_DUPLICATE_PREFIX} prior_trade_id={prior} "
            f"poly={plan.candidate.poly_slug} kalshi={plan.candidate.kalshi_ticker}"
        )
    await _original_reserve(plan, trade_id)


def install() -> None:
    core._unresolved = _safe_unresolved
    core.reserve = _reserve_with_duplicate_guard
    if _DUPLICATE_PREFIX not in rollover._SAFE_PRE_MUTATION_PREFIXES:
        rollover._SAFE_PRE_MUTATION_PREFIXES = (
            *rollover._SAFE_PRE_MUTATION_PREFIXES,
            _DUPLICATE_PREFIX,
        )


def main() -> None:
    install()
    logger.warning(
        "MLB_KP_PARALLEL_POLICY fully_hedged_awaiting_settlement=allowed "
        "ambiguous_or_one_leg=blocked duplicate_exact_pair=blocked shared_budget=%s",
        core.CUMULATIVE_BUDGET,
    )
    rollover.main()


if __name__ == "__main__":
    main()
