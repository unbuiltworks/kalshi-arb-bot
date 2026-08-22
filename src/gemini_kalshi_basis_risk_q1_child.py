"""Dedicated q=1 Gemini/Kalshi settlement-basis research child.

This is intentionally NOT the guaranteed-arbitrage lane. It reuses the proven
one-contract v2 FOK transport and all existing microstructure checks, but adds an
independent threshold-dominance authorization gate before order submission.

The production settlement-equivalence hard stop is untouched. This isolated child
requires its own arm, the explicitly approved $5 research budget, a >=10 bp protective
threshold gap, and an atomically acquired CAPITAL_RESERVED row in the authoritative
Postgres trade ledger before any venue order can be submitted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

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
DATABASE_URL_ENV = "DATABASE_URL"
APPROVED_BUDGET = D("5")
DEFAULT_MIN_GAP_BPS = D("10")
INITIAL_TRANCHE_USD = D("1")
LOCAL_STATE_DIR = Path("/tmp/crypto-basis-risk-state")
STRATEGY = "gemini_kalshi_15m_crypto_basis_risk"
MODE = "LIVE_BASIS_RISK_CANARY"

_LAST_AUTH_CONTEXT: dict[str, Any] | None = None


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _local_state_dir() -> Path:
    """Ephemeral mechanics only; durable single-use ownership lives in Postgres."""
    LOCAL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return LOCAL_STATE_DIR


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
    if not os.getenv(DATABASE_URL_ENV, "").strip():
        raise PermissionError("basis-risk child requires DATABASE_URL for atomic durable claim")
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


def acquire_atomic_capital_reservation(
    *,
    run_id: str,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    context: dict[str, Any],
    modeled_locked_profit: Any,
) -> bool:
    """Atomically win the one-attempt claim in the existing authoritative ledger.

    INSERT ... ON CONFLICT DO NOTHING RETURNING makes exactly one process the winner.
    A restart/redeploy with the same run id sees the existing primary key and must
    refuse before calling the venue transport.
    """
    database_url = os.environ[DATABASE_URL_ENV]
    trade_id = f"gkbasis-{run_id}"
    event_id = f"{trade_id}-capital-reserved"
    symbol = str(gemini_payload.get("symbol") or "")
    ticker = str(kalshi_payload.get("ticker") or "")
    metadata = {
        "run_id": run_id,
        "classification": "LIVE_BASIS_RISK_RESEARCH_NOT_GUARANTEED_ARB",
        "risk_class": "SETTLEMENT_BASIS_RESEARCH",
        "reserved_usd": str(INITIAL_TRANCHE_USD),
        "approved_max_loss_budget_usd": str(APPROVED_BUDGET),
        "modeled_locked_profit": str(modeled_locked_profit or ""),
        "single_use_claim": "ATOMIC_POSTGRES_PRIMARY_KEY",
        "basis_risk_context": dict(context),
    }
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.trade_events (
                    event_id, trade_id, event_type, event_ts, strategy, mode,
                    market, asset, venues, status, quantity, amount, fee,
                    legs_required, legs_filled, opportunity_net_profit, metadata
                ) VALUES (
                    %s, %s, 'CAPITAL_RESERVED', %s, %s, %s,
                    %s, %s, %s, 'CAPITAL_RESERVED', 1, %s, 0,
                    2, 0, %s, %s
                )
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
                """,
                (
                    event_id,
                    trade_id,
                    datetime.now(timezone.utc),
                    STRATEGY,
                    MODE,
                    f"{symbol}|{ticker}",
                    str(context.get("asset") or ""),
                    ["Gemini", "Kalshi"],
                    INITIAL_TRANCHE_USD,
                    _dec(modeled_locked_profit),
                    Jsonb(metadata),
                ),
            )
            won = cur.fetchone() is not None
        conn.commit()
    return won


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
    _, local_claim, result_path = v2._paths(run_id)
    if not local_claim.is_file():
        raise PermissionError("local v2 basis-risk attempt was not claimed")
    if _LAST_AUTH_CONTEXT is None or not _LAST_AUTH_CONTEXT.get("authorized"):
        raise PermissionError("missing dominance-safe authorization context at submission")

    gemini_payload = dict(args[2]) if len(args) > 2 and isinstance(args[2], dict) else {}
    kalshi_payload = dict(args[3]) if len(args) > 3 and isinstance(args[3], dict) else {}
    won = acquire_atomic_capital_reservation(
        run_id=run_id,
        gemini_payload=gemini_payload,
        kalshi_payload=kalshi_payload,
        context=dict(_LAST_AUTH_CONTEXT),
        modeled_locked_profit=kwargs.get("modeled_locked_profit"),
    )
    if not won:
        raise PermissionError(
            f"durable basis-risk claim already exists for {run_id}; refusing duplicate submission"
        )
    logger.critical(
        "CRYPTO_BASIS_RISK_DURABLE_CLAIM_WON run_id=%s reserved_usd=%s order_submission_next=true",
        run_id, INITIAL_TRANCHE_USD,
    )

    result = await v2._ORIGINAL_SUBMIT_DUAL_PROBE(*args, **kwargs)
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

    # Local files coordinate parent/child mechanics in one container. The atomic
    # Postgres CAPITAL_RESERVED primary key is the cross-restart single-use claim.
    v2._state_dir = _local_state_dir
    v2._submit_and_persist_result = _submit_and_persist_context
    original_install = v2.install_canary_v2

    def install_basis_canary() -> None:
        original_install()
        _install_dominance_probe(min_gap_bps)

    v2.install_canary_v2 = install_basis_canary

    rollover_optimizer.install()
    target_lifecycle_cache.install()
    target_freshness_guard.install()

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
        "quantity=1 capital_limit_usd=1 durable_claim=postgres_capital_reserved "
        "classification=LIVE_BASIS_RISK_RESEARCH",
        min_gap_bps, APPROVED_BUDGET,
    )
    v2.main()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    main()
