"""Single-attempt q=5 Gemini/Kalshi scale canary, fail-closed by default.

This is deliberately separate from the proven q=1 child.  It requires three software
opt-ins (the existing live gate, persistent canary arm, and CRYPTO_Q5_SCALE_ARM), a
persistent Railway volume, >=$5 available on each venue, q=5 exact depth/economics,
>=3c modeled after-fee profit per contract, 10bp whole-episode boundary clearance,
and exact 50ms survival.  Gemini fills first; Kalshi receives a fee-aware <=2c hedge
cushion only after a full Gemini fill.  Known unhedged quantity is flattened exactly.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import sys
from typing import Any

from src import gemini_kalshi_crypto_parallel_campaign as parallel
from src import gemini_kalshi_gemini_ws_recovery as gemini_ws_recovery
from src import gemini_kalshi_rollover_optimizer as rollover_optimizer
from src import gemini_kalshi_target_freshness_guard as target_freshness_guard
from src import gemini_kalshi_target_lifecycle_cache as target_lifecycle_cache
from src import gemini_kalshi_sol_live_canary_v2 as v2
from src.gemini_kalshi_scaled_probe_guard import DEFAULT_MIN_PROFIT_PER_CONTRACT
from src.gemini_kalshi_scaled_staged_probe_transport import submit_scaled_gemini_first_probe

D = Decimal
Q = D("5")
MIN_TOTAL_PROFIT = Q * DEFAULT_MIN_PROFIT_PER_CONTRACT
CAPITAL_LIMIT = D("5")
HARD_BUDGET = D("5")
EXECUTION_DELAY_MS = 50
MIN_DOUBLE_BOUNDARY_BPS = D("10")
Q5_ARM_ENV = "CRYPTO_Q5_SCALE_ARM"
DEADLINE_ENV = "SOL_CANARY_V2_ARM_NOT_AFTER_UTC"

executor = v2.executor
safe = v2.safe
readiness = v2.readiness
logger = logging.getLogger("kalshi_arb.gemini_kalshi_q5_live_canary")


def strict_execution_gate_q5(
    *, delay_ms: int, readiness_20bp: bool, quantity: Decimal, locked_profit: Decimal,
    entry_quote_skew_ms: int | None, current_quote_skew_ms: int | None,
) -> bool:
    q = D(quantity)
    profit = D(locked_profit)
    return bool(
        int(delay_ms) == EXECUTION_DELAY_MS
        and readiness_20bp
        and q == Q
        and profit >= MIN_TOTAL_PROFIT
        and profit / q >= DEFAULT_MIN_PROFIT_PER_CONTRACT
        and entry_quote_skew_ms is not None and int(entry_quote_skew_ms) <= 50
        and current_quote_skew_ms is not None and int(current_quote_skew_ms) <= 50
    )


def _build_q5_deterministic_payloads(plan) -> tuple[dict[str, Any], dict[str, Any]]:
    if D(plan.quantity) != Q:
        raise ValueError(f"q5 payload builder requires quantity=5 got={plan.quantity}")
    if D(plan.entry_capital) > CAPITAL_LIMIT:
        raise ValueError(f"q5 planned capital exceeds $5 actual={plan.entry_capital}")
    if D(plan.entry_locked_profit) < MIN_TOTAL_PROFIT:
        raise ValueError(f"q5 total modeled profit below {MIN_TOTAL_PROFIT}")

    if plan.direction == "KALSHI_YES_GEMINI_NO":
        gemini_outcome = "NO"
        gemini_price = D(plan.gemini_limit)
        kalshi_side = "bid"
        kalshi_yes_price = D(plan.kalshi_limit)
    elif plan.direction == "GEMINI_YES_KALSHI_NO":
        gemini_outcome = "YES"
        gemini_price = D(plan.gemini_limit)
        kalshi_side = "ask"
        kalshi_yes_price = D("1") - D(plan.kalshi_limit)
    else:
        raise ValueError(f"unsupported direction={plan.direction}")

    if not (D("0.01") <= gemini_price <= D("0.99")):
        raise ValueError("q5 Gemini limit outside prediction range")
    if not (D("0.01") <= kalshi_yes_price <= D("0.99")):
        raise ValueError("q5 Kalshi YES-space limit outside prediction range")

    run_id = v2._clean_run_id(os.environ[v2.RUN_ID_ENV])
    gemini_id, kalshi_id = v2.deterministic_client_ids(run_id)
    gemini = {
        "symbol": plan.gemini_symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
        "price": str(gemini_price), "quantity": "5", "eventOutcome": gemini_outcome,
        "clientOrderId": gemini_id,
    }
    kalshi = {
        "ticker": plan.kalshi_ticker, "client_order_id": kalshi_id, "side": kalshi_side,
        "count": "5.00", "price": str(kalshi_yes_price), "time_in_force": "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross", "post_only": False,
        "cancel_order_on_pause": True, "reduce_only": False, "subaccount": 0,
        "exchange_index": 0,
    }
    return gemini, kalshi


def _consume_q5_arm(token_path) -> bool:
    """Atomically claim a q5 run before any venue submission."""
    run_id = v2._clean_run_id(os.environ[v2.RUN_ID_ENV])
    arm, claim, result = v2._paths(run_id)
    if token_path != arm or not arm.is_file() or claim.exists() or result.exists():
        return False
    claim.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    try:
        payload = {
            "run_id": run_id,
            "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "Q5_ATTEMPT_CLAIMED_BEFORE_SUBMISSION",
            "quantity": "5",
            "planned_capital_limit_usd": "5",
            "hard_budget_usd": "5",
            "min_profit_per_contract_usd": str(DEFAULT_MIN_PROFIT_PER_CONTRACT),
        }
        os.write(fd, json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        arm.unlink()
    except FileNotFoundError:
        pass
    return True


async def _submit_and_persist_q5(*args: Any, **kwargs: Any) -> dict[str, Any]:
    run_id = v2._clean_run_id(os.environ[v2.RUN_ID_ENV])
    _, claim, result_path = v2._paths(run_id)
    if not claim.is_file():
        raise PermissionError("persistent q5 attempt was not claimed")

    result = await submit_scaled_gemini_first_probe(
        *args,
        required_quantity=Q,
        min_profit_per_contract=DEFAULT_MIN_PROFIT_PER_CONTRACT,
        max_cushion_cents=2,
        use_hedge_cushion=True,
        **kwargs,
    )
    gemini_payload = dict(args[2]) if len(args) > 2 and isinstance(args[2], dict) else {}
    original_kalshi_payload = dict(args[3]) if len(args) > 3 and isinstance(args[3], dict) else {}
    effective_kalshi_payload = dict(original_kalshi_payload)
    cushion = result.get("hedge_cushion") or {}
    if cushion.get("enabled") and cushion.get("cushioned_yes_price") is not None:
        effective_kalshi_payload["price"] = str(cushion["cushioned_yes_price"])

    v2._write_json_atomic(
        result_path,
        {
            "run_id": run_id,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "quantity": "5",
            "gemini_payload": gemini_payload,
            "kalshi_payload": effective_kalshi_payload,
            "original_kalshi_payload": original_kalshi_payload,
            "modeled_locked_profit": str(kwargs.get("modeled_locked_profit", "")),
            "kalshi_fee_multiplier": str(kwargs.get("kalshi_fee_multiplier", "")),
            "quote_skew_ms": kwargs.get("quote_skew_ms"),
            "execution_policy": "GEMINI_FIRST_SCALED_TERMINAL_THEN_KALSHI",
            "result": result,
        },
    )
    return result


async def _q5_authenticated_preflight() -> dict[str, D]:
    await v2._readonly_authenticated_preflight()
    balances = await parallel._live_venue_balances()
    if balances["gemini"] < D("5") or balances["kalshi"] < D("5"):
        raise RuntimeError(
            f"q5 canary requires >=$5 available on each venue gemini={balances['gemini']} kalshi={balances['kalshi']}"
        )
    logger.warning(
        "CRYPTO_Q5_BALANCE_PREFLIGHT gemini_available=%s kalshi_available=%s required_each=5 order_entry=false",
        balances["gemini"], balances["kalshi"],
    )
    return balances


def install_q5_canary() -> None:
    readiness.MIN_DOUBLE_BOUNDARY_BPS = MIN_DOUBLE_BOUNDARY_BPS
    executor.strict_execution_gate = strict_execution_gate_q5
    safe.install_safe_executor()
    executor.build_original_limit_payloads = _build_q5_deterministic_payloads
    executor._arm_token_path = v2._persistent_arm_path
    executor._armed_once = v2._persistent_armed_once
    executor._consume_arm_token = _consume_q5_arm
    executor.submit_dual_probe = _submit_and_persist_q5


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    if os.getenv(Q5_ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(f"q5 canary requires explicit {Q5_ARM_ENV}=YES")
    if os.getenv(v2.ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError("q5 canary requires SOL_CANARY_V2_ARM=YES")
    if os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() != "YES":
        raise PermissionError("q5 canary requires ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES")

    deadline_raw = os.getenv(DEADLINE_ENV, "").strip()
    if not deadline_raw:
        raise RuntimeError(f"{DEADLINE_ENV} is required")
    deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    if datetime.now(timezone.utc) > deadline:
        raise RuntimeError("q5 canary arm deadline already expired")

    # Persistent volume and safe run-id validation happen before any authenticated live session.
    run_id = v2._clean_run_id(os.getenv(v2.RUN_ID_ENV, ""))
    volume = v2._state_dir()
    executor._LIVE_REQUESTED = True
    asyncio.run(_q5_authenticated_preflight())
    gemini_ws_recovery.install()
    install_q5_canary()
    armed = v2._maybe_create_persistent_arm(deadline)
    if not armed:
        raise PermissionError("q5 persistent arm was not created")

    rollover_optimizer.install()
    target_lifecycle_cache.install()
    target_freshness_guard.install()

    logger.warning(
        "CRYPTO_Q5_CANARY_ENABLED run_id=%s volume=%s quantity=5 min_total_profit=%s "
        "min_profit_per_contract=%s min_double_boundary_bps=10 execution_delay_ms=50 "
        "capital_limit=5 hard_budget=5 hedge_cushion_max_cents=2 restart_policy_expected=NEVER",
        run_id, volume, MIN_TOTAL_PROFIT, DEFAULT_MIN_PROFIT_PER_CONTRACT,
    )

    sys.argv = [
        sys.argv[0],
        "--assets", "BTC", "ETH", "SOL", "XRP",
        "--seconds", "0",
        "--max-quote-skew-ms", "50",
        "--capital-limit", "5",
        "--min-profit", str(MIN_TOTAL_PROFIT),
        "--prewarm-lead-s", "1.0",
        "--output-dir", "/tmp/crypto-q5-canary",
    ]
    readiness.guarded.main()


if __name__ == "__main__":
    main()
