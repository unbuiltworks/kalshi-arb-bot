"""Authoritative G/K live supervisor production policy entrypoint.

The wrapper keeps the explicitly authorized cumulative sports ceiling at $20 and
selects the fastest safe Kalshi market-data transport available. Candidate-synced
REST is the production default because Kalshi currently rejects a second
authenticated book connection with HTTP 429. WS-first mode remains opt-in for the
shared-slot/consolidation workstream.

It also replaces the legacy q=1 dual-submit helper with a fractional-fill-safe
variant: any known nonzero non-1 fill hard-halts and attempts an exact-quantity
flatten instead of being misclassified as an unfilled FOK.

Per-epoch funnel instrumentation wraps only reads and gates; it changes no live
execution thresholds or order behavior.
"""
from __future__ import annotations

from decimal import Decimal
import logging
import os

import src.sports_live_budget_guard as budget_guard
import src.sports_gk_autonomous_supervisor as supervisor
from src.kalshi_prop_rest_sync_books import KalshiPropRestSyncBooks
from src.kalshi_prop_ws_exact_books import KalshiPropWsExactBooks
from src.gemini_kalshi_probe_transport_fractional_safe import submit_dual_probe_fractional_safe
from src.sports_gk_funnel_instrumentation import install as install_funnel

D = Decimal
AUTHORIZED_CUMULATIVE_SPORTS_BUDGET = D("20")
WS_ENV = "SPORTS_KALSHI_WS_FIRST"


def install() -> str:
    # The deployed branch contains an obsolete source-level $50 value. Fail safe
    # to the explicitly authorized $20 ceiling in both runtime references.
    budget_guard.CUMULATIVE_BUDGET = AUTHORIZED_CUMULATIVE_SPORTS_BUDGET
    supervisor.CUMULATIVE_BUDGET = AUTHORIZED_CUMULATIVE_SPORTS_BUDGET

    ws_enabled = os.getenv(WS_ENV, "NO").strip().upper() == "YES"
    supervisor.KalshiPropRestBooks = KalshiPropWsExactBooks if ws_enabled else KalshiPropRestSyncBooks

    # _install_unified_families() runs after this entrypoint. Instrument only
    # after it has selected the production Kalshi book class and participation
    # policy so the wrappers observe the exact production funnel.
    original_install_families = supervisor._install_unified_families

    def install_families_with_funnel() -> None:
        original_install_families()
        install_funnel(supervisor.canary)

    supervisor._install_unified_families = install_families_with_funnel

    # Keep the existing fractional-fill-safe mutation transport. Later reporting,
    # budget and capital wrappers compose around this same function.
    supervisor.canary.submit_dual_probe = submit_dual_probe_fractional_safe
    return (
        "AUTHENTICATED_WS_DELTA_PLUS_CANDIDATE_EXACT_REST"
        if ws_enabled
        else "REST_SERIES_PLUS_BOUNDED_GEMINI_TRIGGERED_EXACT_PLUS_FINAL_EXACT"
    )


def main() -> None:
    transport = install()
    logging.getLogger("kalshi_arb.sports_gk_ws_live_entrypoint").warning(
        "SPORTS_GK_LIVE_POLICY cumulative_budget_usd=%s kalshi_transport=%s ws_opt_in=%s fractional_fill_policy=EXACT_QUANTITY_FLATTEN_AND_HALT funnel_telemetry=true",
        AUTHORIZED_CUMULATIVE_SPORTS_BUDGET,
        transport,
        os.getenv(WS_ENV, "NO").strip().upper() == "YES",
    )
    supervisor.main()


if __name__ == "__main__":
    main()
