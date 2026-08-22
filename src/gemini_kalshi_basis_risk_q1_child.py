"""Dedicated q=1 Gemini/Kalshi settlement-basis research child.

This is intentionally NOT the guaranteed-arbitrage lane. It reuses the proven
one-contract v2 FOK transport and all existing microstructure checks, but adds an
independent threshold-dominance authorization gate before the underlying live probe
can consume its persistent arm token.

The original post-incident settlement-equivalence hard stop is not modified. This
module is only reachable from the isolated basis-risk campaign and requires its own
explicit research arm plus the user-approved $5 maximum-loss experiment budget.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import os
import sys
import time
from typing import Any

from src import gemini_kalshi_sol_live_canary_v2 as v2
from src import gemini_kalshi_rollover_optimizer as rollover_optimizer
from src import gemini_kalshi_target_lifecycle_cache as target_lifecycle_cache
from src import gemini_kalshi_target_freshness_guard as target_freshness_guard
from src.gemini_kalshi_threshold_dominance_study import dominance_profile

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_child")

BASIS_ARM_ENV = "CRYPTO_BASIS_RISK_CANARY_ARM"
BASIS_BUDGET_ENV = "CRYPTO_BASIS_RISK_MAX_LOSS_BUDGET_USD"
BASIS_MIN_GAP_ENV = "CRYPTO_BASIS_RISK_MIN_GAP_BPS"
BASIS_DEADLINE_ENV = "CRYPTO_BASIS_RISK_ARM_NOT_AFTER_UTC"
BASIS_RECONCILED_ENV = "CRYPTO_BASIS_RISK_PRIOR_LEDGER_RECONCILED"
APPROVED_BUDGET = D("5")
DEFAULT_MIN_GAP_BPS = D("10")

_LAST_AUTH_CONTEXT: dict[str, Any] | None = None


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def dominance_authorization(
    *,
    direction: str,
    gemini_strike: Any,
    kalshi_target: Any,
    min_gap_bps: Decimal = DEFAULT_MIN_GAP_BPS,
) -> dict[str, Any]:
    """Pure fail-closed authorization for one threshold geometry."""
    g = _dec(gemini_strike)
    k = _dec(kalshi_target)
    floor = D(min_gap_bps)
    result: dict[str, Any] = {
        "authorized": False,
        "direction": str(direction),
        "gemini_strike": str(g) if g is not None else None,
        "kalshi_target": str(k) if k is not None else None,
        "min_gap_bps": str(floor),
        "dominance_safe_direction": None,
        "threshold_gap_bps": None,
        "threshold_gap_abs_bps": None,
        "blocker": None,
        "classification": "LIVE_BASIS_RISK_RESEARCH",
    }
    if floor < D("10"):
        result["blocker"] = "MIN_GAP_BELOW_APPROVED_10BP"
        return result
    if g is None or k is None or g == 0:
        result["blocker"] = "MISSING_OR_INVALID_THRESHOLD"
        return result
    profile = dominance_profile(g, k)
    safe = str(profile["dominance_safe_direction_shared_value"])
    gap_abs = _dec(profile["threshold_gap_abs_bps"])
    result.update({
        "dominance_safe_direction": safe,
        "threshold_gap_bps": profile["threshold_gap_bps"],
        "threshold_gap_abs_bps": profile["threshold_gap_abs_bps"],
    })
    if safe == "SYMMETRIC_EQUAL_THRESHOLD":
        result["blocker"] = "EQUAL_THRESHOLD_NO_PROTECTIVE_GAP"
        return result
    if gap_abs is None or gap_abs < floor:
        result["blocker"] = "PROTECTIVE_GAP_BELOW_MINIMUM"
        return result
    if str(direction) != safe:
        result["blocker"] = "DANGEROUS_THRESHOLD_ORIENTATION"
        return result
    result["authorized"] = True
    result["blocker"] = "NONE"
    return result


def _validate_process_authorization() -> tuple[Decimal, datetime]:
    if os.getenv(BASIS_ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(f"basis-risk child requires {BASIS_ARM_ENV}=YES")
    if os.getenv(BASIS_RECONCILED_ENV, "NO").strip().upper() != "YES":
        raise PermissionError("prior crypto trade ledger must be reconciled before basis-risk order entry")
    budget = D(os.getenv(BASIS_BUDGET_ENV, "0"))
    if budget != APPROVED_BUDGET:
        raise PermissionError(
            f"basis-risk budget must equal explicitly approved ${APPROVED_BUDGET}; got {budget}"
        )
    min_gap = D(os.getenv(BASIS_MIN_GAP_ENV, str(DEFAULT_MIN_GAP_BPS)))
    if min_gap < DEFAULT_MIN_GAP_BPS:
        raise PermissionError("basis-risk minimum threshold gap cannot be below 10 bp")
    deadline_raw = os.getenv(BASIS_DEADLINE_ENV, "").strip()
    if not deadline_raw:
        raise RuntimeError(f"{BASIS_DEADLINE_ENV} is required")
    deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    deadline = deadline.astimezone(timezone.utc)
    if datetime.now(timezone.utc) > deadline:
        raise PermissionError("basis-risk arm deadline expired")
    return min_gap, deadline


def _install_dominance_probe(min_gap_bps: Decimal) -> None:
    """Strengthen the already-installed v2 probe; never relax underlying gates."""
    readiness = v2.readiness
    exact = readiness.exact
    basis = readiness.basis
    original_probe = exact._probe

    async def dominance_probe(*, feed, plan, active_episodes, delays_ms=(50, 100)):
        global _LAST_AUTH_CONTEXT
        key = (plan.asset, plan.direction, int(plan.opened_ms))
        now_ms = int(time.time() * 1000)
        snapshot = basis.basis_snapshot(plan.asset, now_ms)
        target_info = readiness._KALSHI_TARGETS.get(plan.asset)
        target = None
        target_window = None
        if target_info is not None:
            target_window, target_value = target_info
            if snapshot is not None and target_window == snapshot.get("basis_window_end_utc"):
                target = target_value
        g_strike = _dec(snapshot.get("kaiko_strike")) if snapshot else None
        auth = dominance_authorization(
            direction=plan.direction,
            gemini_strike=g_strike,
            kalshi_target=target,
            min_gap_bps=min_gap_bps,
        )
        auth.update({
            "asset": plan.asset,
            "gemini_symbol": plan.gemini_symbol,
            "kalshi_ticker": plan.kalshi_ticker,
            "basis_window_end_utc": snapshot.get("basis_window_end_utc") if snapshot else None,
            "target_window_end_utc": target_window,
            "trade_authorized": bool(auth["authorized"]),
            "order_entry": bool(auth["authorized"]),
            "risk_budget_usd": str(APPROVED_BUDGET),
        })
        logger.warning("CRYPTO_BASIS_RISK_DOMINANCE_GATE %s", json.dumps(auth, separators=(",", ":")))
        if not auth["authorized"]:
            readiness._PLAN_ENTRY_20BP.pop(key, None)
            return None
        _LAST_AUTH_CONTEXT = dict(auth)
        return await original_probe(
            feed=feed,
            plan=plan,
            active_episodes=active_episodes,
            delays_ms=delays_ms,
        )

    exact._probe = dominance_probe


async def _submit_and_persist_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
    run_id = v2._clean_run_id(os.environ[v2.RUN_ID_ENV])
    _, claim, result_path = v2._paths(run_id)
    if not claim.is_file():
        raise PermissionError("persistent basis-risk attempt was not claimed")
    if _LAST_AUTH_CONTEXT is None or not _LAST_AUTH_CONTEXT.get("authorized"):
        raise PermissionError("missing dominance-safe authorization context at submission")

    result = await v2._ORIGINAL_SUBMIT_DUAL_PROBE(*args, **kwargs)
    gemini_payload = dict(args[2]) if len(args) > 2 and isinstance(args[2], dict) else {}
    kalshi_payload = dict(args[3]) if len(args) > 3 and isinstance(args[3], dict) else {}
    v2._write_json_atomic(
        result_path,
        {
            "run_id": run_id,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "gemini_payload": gemini_payload,
            "kalshi_payload": kalshi_payload,
            "modeled_locked_profit": str(kwargs.get("modeled_locked_profit", "")),
            "kalshi_fee_multiplier": str(kwargs.get("kalshi_fee_multiplier", "")),
            "quote_skew_ms": kwargs.get("quote_skew_ms"),
            "basis_risk_context": dict(_LAST_AUTH_CONTEXT),
            "result": result,
        },
    )
    return result


def main() -> None:
    min_gap_bps, deadline = _validate_process_authorization()

    v2._submit_and_persist_result = _submit_and_persist_context
    original_install = v2.install_canary_v2

    def install_basis_canary() -> None:
        original_install()
        _install_dominance_probe(min_gap_bps)

    v2.install_canary_v2 = install_basis_canary

    rollover_optimizer.install()
    target_lifecycle_cache.install()
    target_freshness_guard.install()

    # Map the isolated research authorization into the legacy one-attempt transport
    # only inside this child process. The production arb service is untouched.
    os.environ[v2.ARM_ENV] = "YES"
    os.environ["ENABLE_GEMINI_KALSHI_LIVE_PROBES"] = "YES"

    sys.argv = [
        sys.argv[0],
        "--arm-deadline-utc", deadline.isoformat(),
        "--assets", "BTC", "ETH", "SOL", "XRP",
        "--seconds", "0",
        "--max-quote-skew-ms", "50",
        "--capital-limit", "1",
        "--min-profit", "0.03",
        "--prewarm-lead-s", "1.0",
        "--output-dir", "/tmp/crypto-basis-risk-q1",
    ]
    logger.warning(
        "CRYPTO_BASIS_RISK_CHILD_ENABLED min_gap_bps=%s approved_budget_usd=%s "
        "quantity=1 capital_limit_usd=1 classification=LIVE_BASIS_RISK_RESEARCH",
        min_gap_bps, APPROVED_BUDGET,
    )
    v2.main()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    main()
