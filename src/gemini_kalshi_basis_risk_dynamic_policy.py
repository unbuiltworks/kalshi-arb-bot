"""Process-local dynamic policy for q=1 settlement-basis live probes.

The shared guaranteed-arbitrage settlement-equivalence lock is unchanged.  The
basis-risk service keeps the >=3c after-fee floor, exact depth/freshness/skew gates,
q=1 sizing, BTC quarantine and the existing $5 loss budget.  Supervisors may scope a
child to one of ETH/SOL/XRP so independent asset lanes can scan concurrently without
competing for the same first candidate.
"""
from __future__ import annotations

from decimal import Decimal
import logging
import os
import sys

from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_q1_near_miss_telemetry as near_miss
from src import gemini_kalshi_rollover_optimizer as rollover_optimizer
from src import gemini_kalshi_target_lifecycle_cache as target_lifecycle_cache
from src import gemini_kalshi_target_freshness_guard as target_freshness_guard
from src import gemini_kalshi_sol_live_canary_v2 as v2
from src.gemini_kalshi_threshold_dominance_study import dominance_profile

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_dynamic_policy")

DYNAMIC_MIN_GAP_BPS = D("0.5")
DYNAMIC_MIN_LOCKED_PROFIT = D("0.03")
ELIGIBLE_ASSETS = ("ETH", "SOL", "XRP")
ASSET_SCOPE_ENV = "CRYPTO_BASIS_ASSETS"
KALSHI_REST_POLL_INTERVAL_ENV = "CRYPTO_BASIS_KALSHI_REST_POLL_INTERVAL_S"
DEFAULT_KALSHI_REST_POLL_INTERVAL_S = D("0.18")
MIN_KALSHI_REST_POLL_INTERVAL_S = D("0.05")
MAX_KALSHI_REST_POLL_INTERVAL_S = D("0.25")


def configured_assets() -> tuple[str, ...]:
    raw = os.getenv(ASSET_SCOPE_ENV, "").strip()
    if not raw:
        return ELIGIBLE_ASSETS
    requested = []
    for token in raw.replace(";", ",").split(","):
        asset = token.strip().upper()
        if not asset:
            continue
        if asset not in ELIGIBLE_ASSETS:
            raise PermissionError(f"basis-risk asset scope contains ineligible asset {asset}")
        if asset not in requested:
            requested.append(asset)
    if not requested:
        raise PermissionError("basis-risk asset scope resolved to no eligible assets")
    return tuple(requested)


def configured_kalshi_rest_poll_interval_s() -> Decimal:
    raw = os.getenv(KALSHI_REST_POLL_INTERVAL_ENV, str(DEFAULT_KALSHI_REST_POLL_INTERVAL_S)).strip()
    try:
        value = D(raw)
    except Exception:
        value = DEFAULT_KALSHI_REST_POLL_INTERVAL_S
    if not value.is_finite():
        value = DEFAULT_KALSHI_REST_POLL_INTERVAL_S
    return max(MIN_KALSHI_REST_POLL_INTERVAL_S, min(value, MAX_KALSHI_REST_POLL_INTERVAL_S))


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
    assets = configured_assets()
    v2._state_dir = child._local_state_dir
    v2._submit_and_persist_result = child._submit_and_persist_context
    original_install = v2.install_canary_v2

    def install_basis_canary() -> None:
        original_install()
        child._install_dominance_probe(min_gap_bps)

    v2.install_canary_v2 = install_basis_canary
    poll_interval_s = configured_kalshi_rest_poll_interval_s()
    os.environ[KALSHI_REST_POLL_INTERVAL_ENV] = str(poll_interval_s)
    near_miss.install()
    rollover_optimizer.install()
    target_lifecycle_cache.install()
    target_freshness_guard.install()
    os.environ[v2.ARM_ENV] = "YES"
    os.environ["ENABLE_GEMINI_KALSHI_LIVE_PROBES"] = "YES"
    sys.argv = [
        sys.argv[0],
        "--arm-deadline-utc", deadline.isoformat(),
        "--assets", *assets,
        "--seconds", "0",
        "--max-quote-skew-ms", "50",
        "--capital-limit", "1",
        "--min-profit", str(DYNAMIC_MIN_LOCKED_PROFIT),
        "--prewarm-lead-s", "1.0",
        "--output-dir", "/tmp/crypto-basis-risk-q1",
    ]
    logger.warning(
        "CRYPTO_BASIS_DYNAMIC_POLICY_ENABLED min_gap_bps=%s min_locked_profit=%s "
        "eligible_assets=%s scoped_assets=%s btc_quarantined=true quantity=1 capital_limit_usd=1 "
        "approved_budget_usd=%s guaranteed_arb=false near_miss_telemetry=true "
        "kalshi_discovery=EXACT_REST_DEPTH1 poll_interval_ms=%s quote_timestamp=HTTP_REQUEST_START",
        min_gap_bps,
        DYNAMIC_MIN_LOCKED_PROFIT,
        ",".join(ELIGIBLE_ASSETS),
        ",".join(assets),
        child.APPROVED_BUDGET,
        int(poll_interval_s * D("1000")),
    )
    v2.main()


def install() -> None:
    child.DEFAULT_MIN_GAP_BPS = DYNAMIC_MIN_GAP_BPS
    child.dominance_authorization = dynamic_dominance_authorization
    child.main = dynamic_main
    logger.warning(
        "CRYPTO_BASIS_DYNAMIC_POLICY_INSTALLED scope=DEDICATED_BASIS_CHILD "
        "shared_guaranteed_arb_lock_changed=false min_gap_bps=%s min_locked_profit=%s "
        "eligible_assets=%s btc_quarantined=true near_miss_telemetry=true kalshi_rest_poll_ms=%s",
        DYNAMIC_MIN_GAP_BPS,
        DYNAMIC_MIN_LOCKED_PROFIT,
        ",".join(ELIGIBLE_ASSETS),
        int(configured_kalshi_rest_poll_interval_s() * D("1000")),
    )
