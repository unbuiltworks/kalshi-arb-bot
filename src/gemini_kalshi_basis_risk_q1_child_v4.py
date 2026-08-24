"""Dedicated dynamic-policy q=1 basis child after the first settled live validation.

The first q=1 probe settled +$0.0337 net in the authoritative ledger.  Promotion is
therefore limited to three TOTAL q=1 capital reservations (the completed probe plus at
most two more), one unresolved trade at a time.  The approved $5 research-loss ceiling,
$1 tranche, dominance rule, BTC quarantine, freshness/depth/persistence gates and
shared guaranteed-arbitrage lock are unchanged.
"""
from __future__ import annotations
import logging
from src import gemini_kalshi_basis_risk_dynamic_policy as dynamic_policy
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_basis_risk_q1_child_v3 as child_v3

PROMOTED_TOTAL_PROBE_LIMIT = 3
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_child_v4")


def main() -> None:
    # acquire_atomic_capital_reservation() reads this module global at reservation
    # time, so this process-local promotion keeps the durable Postgres ceiling while
    # allowing exactly two additional q=1 probes after the first settled success.
    child_v2.INITIAL_PROBE_LIMIT = PROMOTED_TOTAL_PROBE_LIMIT
    logger.warning(
        "CRYPTO_BASIS_RISK_PROMOTED total_probe_limit=%s one_unresolved_trade=true "
        "quantity=1 tranche_usd=1 approved_budget_usd=5 budget_increased=false",
        PROMOTED_TOTAL_PROBE_LIMIT,
    )
    dynamic_policy.install()
    child_v3.main()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    main()
