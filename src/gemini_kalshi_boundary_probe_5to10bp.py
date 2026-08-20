"""Experimental q=1 Gemini/Kalshi live probe restricted to a 5-<10 bp boundary band.

This cohort is intentionally mutually exclusive with the production >=10 bp core lane.
Only the settlement-boundary gate is relaxed. All execution protections remain:
q=1, >=$0.03 exact after-fee modeled profit, exact 50 ms checkpoint, <=50 ms
cross-venue skew, post-activation/fresh books, same original episode/limits,
Gemini-first staged transport, emergency flatten, and fail-closed hard halt.

Authorization is separate from the core campaign and requires
CRYPTO_BOUNDARY_PROBE_ARM=YES in addition to the existing live-probe software gate.
"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from src import gemini_kalshi_crypto_campaign_child_reliable as child
from src import gemini_kalshi_micro_live_executor_20bp as executor
from src import gemini_kalshi_micro_live_readiness_20bp as readiness
from src import gemini_kalshi_sol_live_canary_v2 as v2

D = Decimal
MIN_BPS = D("5")
MAX_BPS_EXCLUSIVE = D("10")
ARM_ENV = "CRYPTO_BOUNDARY_PROBE_ARM"

_original_augment = readiness.augment_double_boundary


def augment_5to10_band(snapshot: dict[str, Any] | None, *, kalshi_threshold: Decimal | None):
    out = _original_augment(snapshot, kalshi_threshold=kalshi_threshold)
    if out is None:
        return None
    raw = out.get("min_abs_double_boundary_margin_bps")
    try:
        margin = D(str(raw)) if raw is not None else None
    except Exception:
        margin = None
    in_band = bool(
        out.get("proxy_fresh_1000ms")
        and out.get("same_side_of_both_boundaries")
        and margin is not None
        and MIN_BPS <= margin < MAX_BPS_EXCLUSIVE
    )
    # Reuse the existing fail-closed readiness plumbing, but redefine only its
    # boundary predicate for this isolated cohort.
    out["research_double_boundary_gate_20bp_fresh_1000ms"] = in_band
    out["experimental_boundary_band"] = "5_to_lt_10_bp"
    return out


def strict_gate_50ms(**kwargs: Any) -> bool:
    return bool(
        int(kwargs.get("delay_ms", -1)) == 50
        and kwargs.get("readiness_20bp")
        and D(kwargs.get("quantity")) == D("1")
        and D(kwargs.get("locked_profit")) >= D("0.03")
        and kwargs.get("entry_quote_skew_ms") is not None
        and int(kwargs["entry_quote_skew_ms"]) <= 50
        and kwargs.get("current_quote_skew_ms") is not None
        and int(kwargs["current_quote_skew_ms"]) <= 50
    )


def main() -> None:
    if os.getenv(ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError(f"experimental boundary cohort requires {ARM_ENV}=YES")
    if os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() != "YES":
        raise PermissionError("experimental boundary cohort requires live probe environment gate")

    probe_run = os.getenv("CRYPTO_BOUNDARY_PROBE_RUN_ID", "").strip()
    if not probe_run:
        raise RuntimeError("CRYPTO_BOUNDARY_PROBE_RUN_ID is required")
    os.environ[v2.RUN_ID_ENV] = probe_run
    os.environ["LIVE_PROBE_ARM_TOKEN_FILE"] = "/data/crypto-boundary-probe-5to10.arm"

    readiness.MIN_DOUBLE_BOUNDARY_BPS = MIN_BPS
    readiness.augment_double_boundary = augment_5to10_band
    executor.strict_execution_gate = strict_gate_50ms

    child.main()


if __name__ == "__main__":
    main()
