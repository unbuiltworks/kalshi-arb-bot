"""v6 promoted basis child: Kalshi-first execution after reviewed SOL one-leg loss.

The 2026-08-23 SOL incident is explicitly reviewed here, not erased: -$0.09 remains
charged against the existing $5 realized-loss budget. v6 permits continuation only
when that exact historical execution-loss row is the sole realized loss. Any new loss,
unknown loss row, unresolved exposure, or budget exhaustion blocks future reservations.

Execution is basis-process-only Kalshi-first: Gemini cannot submit unless the Kalshi
q=1 FOK is already confirmed filled. Shared guaranteed-arb authorization is untouched.
"""
from __future__ import annotations

from decimal import Decimal
import logging
from typing import Any

from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v4 as child_v4
from src import gemini_kalshi_basis_risk_q1_child_v5 as child_v5
from src import gemini_kalshi_basis_risk_thin_first_transport as thin_first

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_child_v6")

PROMOTED_PROBE_LIMIT = child_v5.PROMOTED_PROBE_LIMIT
REVIEWED_EXECUTION_LOSS = {
    "trade_id": "gkbasis-br0823131801-01",
    "status": "REALIZED_ONE_LEG_SETTLEMENT",
    "net_pnl": D("-0.0900"),
}
_ORIGINAL_PROMOTED_STATE = child_v5.promoted_state


def promoted_state(conn) -> dict[str, Any]:
    state = _ORIGINAL_PROMOTED_STATE(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT trade_id, status, net_pnl
            FROM public.trade_events
            WHERE strategy=%s
              AND event_type='TRADE_REALIZED'
              AND net_pnl < 0
            ORDER BY event_ts, trade_id
            """,
            (child.STRATEGY,),
        )
        losses = [
            {"trade_id": str(t), "status": str(s), "net_pnl": D(str(p))}
            for t, s, p in cur.fetchall()
        ]
        cur.execute(
            """
            SELECT COUNT(*)::int
            FROM public.trade_events
            WHERE strategy=%s
              AND event_type='TRADE_REALIZED'
              AND status='NO_FILL_RELEASED'
            """,
            (child.STRATEGY,),
        )
        no_fill_releases = int(cur.fetchone()[0] or 0)

    reviewed = []
    unreviewed = []
    for row in losses:
        if (
            row["trade_id"] == REVIEWED_EXECUTION_LOSS["trade_id"]
            and row["status"] == REVIEWED_EXECUTION_LOSS["status"]
            and row["net_pnl"] == REVIEWED_EXECUTION_LOSS["net_pnl"]
        ):
            reviewed.append(row)
        else:
            unreviewed.append(row)
    state["reviewed_execution_losses"] = reviewed
    state["unreviewed_realized_losses"] = unreviewed
    state["no_fill_releases"] = no_fill_releases
    state["remaining_loss_budget_usd"] = child.APPROVED_BUDGET - D(state["realized_loss_usd"])
    return state


def automatic_rollover_allowed(state: dict[str, Any]) -> tuple[bool, str]:
    if state["unresolved_reservations"]:
        return False, "UNRESOLVED_RESERVATION"
    if state["unresolved_submissions"]:
        return False, "UNRESOLVED_SUBMISSION"
    if state.get("unreviewed_realized_losses"):
        return False, "NEW_OR_UNREVIEWED_REALIZED_LOSS_HARD_STOP"
    if D(state["realized_loss_usd"]) >= child.APPROVED_BUDGET:
        return False, "APPROVED_LOSS_BUDGET_EXHAUSTED"
    if int(state["reservations"]) >= PROMOTED_PROBE_LIMIT:
        return False, "PROMOTED_PROBE_LIMIT_REACHED"
    if child.INITIAL_TRANCHE_USD > child.APPROVED_BUDGET:
        return False, "PER_PROBE_TRANCHE_EXCEEDS_APPROVED_LOSS_BUDGET"
    return True, "OK"


def install() -> None:
    # v5 reservation function resolves these globals at runtime; patch them first so
    # every future reservation uses the reviewed-loss-aware state and policy.
    child_v5.promoted_state = promoted_state
    child_v5.automatic_rollover_allowed = automatic_rollover_allowed
    child_v5.install()

    # Patch only the executor module reference used by this isolated basis child.
    child.v2.executor.submit_dual_probe = thin_first.submit_kalshi_first_probe
    logger.warning(
        "CRYPTO_BASIS_RISK_V6_INSTALLED execution_sequence=KALSHI_THEN_GEMINI "
        "reviewed_execution_loss_usd=0.09 approved_loss_budget_usd=%s "
        "new_loss_hard_stop=true shared_arb_lock_changed=false",
        child.APPROVED_BUDGET,
    )


def main() -> None:
    install()
    child_v4.main()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    main()
