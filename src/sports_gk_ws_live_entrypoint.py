"""Authoritative G/K live supervisor entrypoint with WS-first Kalshi books.

This wrapper makes two production policy choices explicit without weakening the
existing q=1 execution gates:
- cap cumulative sports G/K live-probe capital at the user-authorized $20;
- use authenticated Kalshi orderbook_delta WebSockets for hot discovery while
  retaining candidate-only exact depth=1 REST revalidation.

The underlying supervisor still owns duplicate protection, durable capital
reservation, participation proof, freshness/skew checks, FOK transport, bounded
flattening and hard-halt behavior.
"""
from __future__ import annotations

from decimal import Decimal
import logging

import src.sports_live_budget_guard as budget_guard
import src.sports_gk_autonomous_supervisor as supervisor
from src.kalshi_prop_ws_exact_books import KalshiPropWsExactBooks

D = Decimal
AUTHORIZED_CUMULATIVE_SPORTS_BUDGET = D("20")


def install() -> None:
    # The deployed branch currently contains an obsolete source-level $50 value.
    # Fail safe to the explicitly authorized $20 ceiling in both modules that hold
    # a reference to the runtime constant.
    budget_guard.CUMULATIVE_BUDGET = AUTHORIZED_CUMULATIVE_SPORTS_BUDGET
    supervisor.CUMULATIVE_BUDGET = AUTHORIZED_CUMULATIVE_SPORTS_BUDGET

    # The duplicate shadow consumer is paused; the authoritative live supervisor
    # gets the authenticated Kalshi WS and keeps exact REST for candidate rechecks.
    supervisor.KalshiPropRestBooks = KalshiPropWsExactBooks


def main() -> None:
    logging.getLogger("kalshi_arb.sports_gk_ws_live_entrypoint").warning(
        "SPORTS_GK_WS_LIVE_POLICY cumulative_budget_usd=%s kalshi_transport=%s",
        AUTHORIZED_CUMULATIVE_SPORTS_BUDGET,
        "AUTHENTICATED_WS_DELTA_PLUS_CANDIDATE_EXACT_REST",
    )
    install()
    supervisor.main()


if __name__ == "__main__":
    main()
