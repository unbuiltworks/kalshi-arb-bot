"""Process-local dynamic policy for the next q=1 settlement-basis live probe.

This does not alter the shared guaranteed-arbitrage settlement-equivalence lock.
It lowers the threshold-gap floor only inside the dedicated basis-risk child while
raising the first-probe quote-edge floor to 7 cents. All existing exact-depth,
freshness, persistence, q=1, capital reservation and $5 hard-budget controls remain.
"""
from __future__ import annotations

from decimal import Decimal
import json
import logging
import os
import sys

from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_rollover_optimizer as rollover_optimizer
from src import gemini_kalshi_target_lifecycle_cache as target_lifecycle_cache
from src import gemini_kalshi_target_freshness_guard as target_freshness_guard
from src import gemini_kalshi_sol_live_canary_v2 as v2
from src.gemini_kalshi_threshold_dominance_study import dominance_profile

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_dynamic_policy")

DYNAMIC_MIN_GAP_BPS = D("0.5")
DYNAMIC_MIN_LOCKED_PROFIT = D("0.07")


def dynamic_dominance_authorization(*, direction, gemini_strike, kalshi_target, min_gap_bps=DYNAMIC_MIN_GAP_BPS):
    g = child._dec(gemini_strike)
    k = child._dec(kalshi_target)
    floor = D(min_gap_bps)
    result = {
        "authorized": False,
        "direction": str(direction),
        "gemini_strike": str(g) if g is not None else None,
        "kalshi_target": str(k) if k is not None else None,
        "min_gap_bps": str(floor),
        "dominance_safe_direction": None,
        "threshold_gap_bps": None,
        "threshold_gap_abs_bps": None,
        "blocker": None,
        "classification": "LIVE_BASIS_RISK_RESEARCH_DYNAMIC_FIRST_PROBE",
        "min_locked_profit": str(DYNAMIC_MIN_LOCKED_PROFIT),
    }
    if floor < DYNAMIC_MIN_GAP_BPS:
        result["blocker"] = "MIN_GAP_BELOW_DYNAMIC_FIRST_PROBE_FLOOR"
        return result
    if g is None or k is None or g == 0:
        result["blocker"] = "MISSING_OR_INVALID_THRESHOLD"
        return result
    profile = dominance_profile(g, k)
    safe = str(profile["dominance_safe_direction_shared_value"])
    gap_abs = child._dec(profile["threshold_gap_abs_bps"])
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


def dynamic_main() -> None:
    min_gap_bps, deadline = child._validate_process_authorization()
    v2._state_dir = child._local_state_dir
    v2._submit_and_persist_result = child._submit_and_persist_context
    original_install = v2.install_canary_v2

    def install_basis_canary() -> None:
        original_install()
        child._install_dominance_probe(min_gap_bps)

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
        "--min-profit", str(DYNAMIC_MIN_LOCKED_PROFIT),
        "--prewarm-lead-s", "1.0",
        "--output-dir", "/tmp/crypto-basis-risk-q1",
    ]
    logger.warning(
        "CRYPTO_BASIS_DYNAMIC_POLICY_ENABLED min_gap_bps=%s min_locked_profit=%s "
        "quantity=1 capital_limit_usd=1 approved_budget_usd=%s guaranteed_arb=false",
        min_gap_bps, DYNAMIC_MIN_LOCKED_PROFIT, child.APPROVED_BUDGET,
    )
    v2.main()


def install() -> None:
    child.DEFAULT_MIN_GAP_BPS = DYNAMIC_MIN_GAP_BPS
    child.dominance_authorization = dynamic_dominance_authorization
    child.main = dynamic_main
    logger.warning(
        "CRYPTO_BASIS_DYNAMIC_POLICY_INSTALLED scope=DEDICATED_BASIS_CHILD "
        "shared_guaranteed_arb_lock_changed=false min_gap_bps=%s min_locked_profit=%s",
        DYNAMIC_MIN_GAP_BPS, DYNAMIC_MIN_LOCKED_PROFIT,
    )
