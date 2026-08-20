"""Pinned one-asset read-only scanner using the live canary market-data stack.

No persistent arm layer is installed and executor live_requested is forced false, so
order entry is impossible. The 10bp/50ms decision logic is retained for telemetry.
"""
from __future__ import annotations

import os
import sys

from src import gemini_kalshi_sol_live_canary_v2 as v2
from src import gemini_kalshi_micro_live_executor_20bp_safe as safe
from src import gemini_kalshi_rollover_optimizer as rollover_optimizer
from src import gemini_kalshi_target_lifecycle_cache as target_lifecycle_cache
from src import gemini_kalshi_target_freshness_guard as target_freshness_guard
from src import gemini_kalshi_gate_attribution as gate_attribution


def main() -> None:
    asset = os.getenv("CRYPTO_CANARY_ASSET", "").strip().upper()
    if asset not in {"BTC", "ETH", "SOL", "XRP"}:
        raise ValueError(f"unsupported shadow asset {asset}")
    seconds = os.getenv("CRYPTO_PARALLEL_SHADOW_CHILD_SECONDS", "30")

    v2.readiness.MIN_DOUBLE_BOUNDARY_BPS = v2.CANARY_MIN_DOUBLE_BOUNDARY_BPS
    v2.executor.strict_execution_gate = v2.strict_execution_gate_10bp_50ms
    v2.executor._LIVE_REQUESTED = False
    os.environ["ENABLE_GEMINI_KALSHI_LIVE_PROBES"] = "NO"

    safe.install_safe_executor()
    gate_attribution.install(v2.executor.logger)
    rollover_optimizer.install()
    target_lifecycle_cache.install()
    target_freshness_guard.install()

    sys.argv = [
        sys.argv[0],
        "--assets", asset,
        "--seconds", seconds,
        "--max-quote-skew-ms", "50",
        "--capital-limit", "1",
        "--min-profit", "0.03",
        "--prewarm-lead-s", "1.0",
        "--output-dir", "/tmp/crypto-parallel-shadow",
    ]
    v2.readiness.guarded.main()


if __name__ == "__main__":
    main()
