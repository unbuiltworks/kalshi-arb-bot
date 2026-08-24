"""Read-only production preflight for v6 basis rollover. No order entry."""
from __future__ import annotations

import json
import logging

from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_basis_risk_q1_child_v6 as child_v6
from src import gemini_kalshi_live_probe_guard as shared_guard

logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_v6_preflight")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    child_v2.assert_prior_ledger_reconciled()
    with child_v2._connect() as conn:
        state = child_v6.promoted_state(conn)
    allowed, reason = child_v6.automatic_rollover_allowed(state)
    if state["unresolved_reservations"] or state["unresolved_submissions"]:
        raise RuntimeError("v6 preflight found unresolved exposure")
    if len(state["reviewed_execution_losses"]) != 1 or state["unreviewed_realized_losses"]:
        raise RuntimeError("v6 preflight reviewed-loss classification mismatch")
    if shared_guard.SETTLEMENT_EQUIVALENCE_PROVEN is not False:
        raise RuntimeError("shared guaranteed-arb lock unexpectedly changed")
    if shared_guard.live_probe_authorized(True):
        raise RuntimeError("shared guaranteed-arb authorization unexpectedly enabled")
    logger.warning(
        "CRYPTO_BASIS_RISK_V6_PREFLIGHT %s",
        json.dumps({
            "allowed": allowed,
            "reason": reason,
            "reservations": state["reservations"],
            "settled_or_released": state["realized"],
            "no_fill_releases": state["no_fill_releases"],
            "realized_losses": state["realized_losses"],
            "realized_loss_usd": str(state["realized_loss_usd"]),
            "realized_net_pnl": str(state["realized_net_pnl"]),
            "remaining_loss_budget_usd": str(state["remaining_loss_budget_usd"]),
            "reviewed_execution_loss_trade_ids": [r["trade_id"] for r in state["reviewed_execution_losses"]],
            "unresolved_reservations": state["unresolved_reservations"],
            "unresolved_submissions": state["unresolved_submissions"],
            "execution_sequence_next": "KALSHI_THEN_GEMINI",
            "shared_arb_lock_changed": False,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    if not allowed:
        raise PermissionError(f"v6 preflight blocked: {reason}")


if __name__ == "__main__":
    main()
