"""Authoritative G/K live supervisor production policy entrypoint.

The wrapper keeps the explicitly authorized cumulative sports ceiling at $20 and
selects the fastest safe Kalshi market-data transport available. Candidate-synced
REST is the production default because Kalshi currently rejects a second
authenticated book connection with HTTP 429. WS-first mode remains opt-in for the
shared-slot/consolidation workstream.

The underlying supervisor still owns duplicate protection, durable capital
reservation, participation proof, freshness/skew checks, FOK transport, bounded
flattening and hard-halt behavior.
"""
from __future__ import annotations

from decimal import Decimal
import logging
import os

import src.sports_live_budget_guard as budget_guard
import src.sports_gk_autonomous_supervisor as supervisor
from src.kalshi_prop_rest_sync_books import KalshiPropRestSyncBooks
from src.kalshi_prop_ws_exact_books import KalshiPropWsExactBooks

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
    return (
        "AUTHENTICATED_WS_DELTA_PLUS_CANDIDATE_EXACT_REST"
        if ws_enabled
        else "REST_SERIES_PLUS_BOUNDED_GEMINI_TRIGGERED_EXACT_PLUS_FINAL_EXACT"
    )


def main() -> None:
    transport = install()
    logging.getLogger("kalshi_arb.sports_gk_ws_live_entrypoint").warning(
        "SPORTS_GK_LIVE_POLICY cumulative_budget_usd=%s kalshi_transport=%s ws_opt_in=%s",
        AUTHORIZED_CUMULATIVE_SPORTS_BUDGET,
        transport,
        os.getenv(WS_ENV, "NO").strip().upper() == "YES",
    )
    supervisor.main()


if __name__ == "__main__":
    main()
