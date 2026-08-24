"""v6 promoted basis child: bounded Kalshi-first basis execution.

The reviewed 2026-08-23 SOL one-leg loss remains charged against the existing $5
realized-loss budget.  This module now separates *execution risk* from already-paired
settlement-basis risk:

* at most one reservation may be in-flight/unpaired at a time;
* up to three q=1 BOTH_FILLED positions may await settlement concurrently;
* their reserved worst-case basis exposure must fit inside the remaining $5 budget;
* any new/unreviewed realized loss hard-stops fresh reservations;
* the historical probe-count ceiling is raised only as a turnover limit.  The $5 loss
  budget, q=1 tranche and shared guaranteed-arbitrage lock are unchanged.

Execution remains Kalshi-first: Gemini cannot submit unless Kalshi is confirmed filled.
"""
from __future__ import annotations

from decimal import Decimal
import logging
from typing import Any

from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_basis_risk_q1_child_v4 as child_v4
from src import gemini_kalshi_basis_risk_q1_child_v5 as child_v5
from src import gemini_kalshi_basis_risk_thin_first_transport as thin_first

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_child_v6")

# This is a turnover ceiling, not an increase to the approved loss budget.
PROMOTED_PROBE_LIMIT = 200
MAX_OPEN_PAIRED_RISK = 3
REVIEWED_EXECUTION_LOSS = {
    "trade_id": "gkbasis-br0823131801-01",
    "status": "REALIZED_ONE_LEG_SETTLEMENT",
    "net_pnl": D("-0.0900"),
}
_ORIGINAL_PROMOTED_STATE = child_v5.promoted_state


def _open_risk_rows(conn) -> list[dict[str, Any]]:
    """Classify unresolved reservations as paired or still execution-risk-bearing."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                r.trade_id,
                MAX(r.amount)::numeric AS reserved_usd,
                MAX(r.market) AS market,
                BOOL_OR(
                    e.event_type='ORDER_SUBMITTED'
                    AND e.status='BOTH_FILLED'
                    AND COALESCE(e.legs_filled, 0) >= 2
                ) AS both_filled,
                BOOL_OR(e.event_type='ORDER_SUBMITTED') AS submitted
            FROM public.trade_events AS r
            LEFT JOIN public.trade_events AS e ON e.trade_id=r.trade_id
            WHERE r.strategy=%s
              AND r.event_type='CAPITAL_RESERVED'
              AND NOT EXISTS (
                  SELECT 1 FROM public.trade_events AS z
                  WHERE z.trade_id=r.trade_id AND z.event_type='TRADE_REALIZED'
              )
            GROUP BY r.trade_id
            ORDER BY r.trade_id
            """,
            (child.STRATEGY,),
        )
        return [
            {
                "trade_id": str(trade_id),
                "reserved_usd": D(str(reserved or 0)),
                "market": str(market or ""),
                "both_filled": bool(both_filled),
                "submitted": bool(submitted),
            }
            for trade_id, reserved, market, both_filled, submitted in cur.fetchall()
        ]


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

    open_rows = _open_risk_rows(conn)
    paired = [row for row in open_rows if row["both_filled"]]
    inflight = [row for row in open_rows if not row["both_filled"]]
    paired_reserved = sum((row["reserved_usd"] for row in paired), D("0"))

    state.update({
        "reviewed_execution_losses": reviewed,
        "unreviewed_realized_losses": unreviewed,
        "no_fill_releases": no_fill_releases,
        "remaining_loss_budget_usd": child.APPROVED_BUDGET - D(state["realized_loss_usd"]),
        "open_paired": paired,
        "open_paired_count": len(paired),
        "open_paired_reserved_usd": paired_reserved,
        "inflight_or_unpaired": inflight,
        # Replace the older all-unresolved view with the risk-aware split.
        "unresolved_reservations": [row["trade_id"] for row in inflight],
        "unresolved_submissions": [row["trade_id"] for row in inflight if row["submitted"]],
    })
    return state


def automatic_rollover_allowed(state: dict[str, Any]) -> tuple[bool, str]:
    if state.get("inflight_or_unpaired"):
        return False, "INFLIGHT_OR_UNPAIRED_EXECUTION_RISK"
    if state.get("unreviewed_realized_losses"):
        return False, "NEW_OR_UNREVIEWED_REALIZED_LOSS_HARD_STOP"
    remaining = D(state["remaining_loss_budget_usd"])
    if remaining <= 0:
        return False, "APPROVED_LOSS_BUDGET_EXHAUSTED"
    if int(state["reservations"]) >= PROMOTED_PROBE_LIMIT:
        return False, "PROMOTED_PROBE_LIMIT_REACHED"
    if int(state.get("open_paired_count", 0)) >= MAX_OPEN_PAIRED_RISK:
        return False, "OPEN_PAIRED_RISK_CAP_REACHED"
    if D(state.get("open_paired_reserved_usd", 0)) + child.INITIAL_TRANCHE_USD > remaining:
        return False, "REMAINING_LOSS_BUDGET_CANNOT_COVER_NEXT_PAIRED_POSITION"
    if child.INITIAL_TRANCHE_USD > child.APPROVED_BUDGET:
        return False, "PER_PROBE_TRANCHE_EXCEEDS_APPROVED_LOSS_BUDGET"
    return True, "OK"


def unresolved_execution_risk_trade_ids(conn) -> list[str]:
    """For child boot: paired positions may await settlement; unpaired ones may not."""
    return [row["trade_id"] for row in _open_risk_rows(conn) if not row["both_filled"]]


def assert_prior_ledger_reconciled_allow_paired() -> None:
    child_v2._LEDGER_RECONCILED = False
    with child_v2._connect() as conn:
        state = promoted_state(conn)
    if state["inflight_or_unpaired"]:
        raise PermissionError(
            "basis-risk v6 blocked by in-flight/unpaired prior risk: "
            + ",".join(row["trade_id"] for row in state["inflight_or_unpaired"][:8])
        )
    paired = D(state["open_paired_reserved_usd"])
    if paired > D(state["remaining_loss_budget_usd"]):
        raise PermissionError("basis-risk v6 open paired risk exceeds remaining approved loss budget")
    child_v2._LEDGER_RECONCILED = True
    logger.warning(
        "CRYPTO_BASIS_RISK_LEDGER_RECONCILED open_paired=%s open_paired_reserved_usd=%s "
        "inflight_unpaired=0 remaining_loss_budget_usd=%s",
        state["open_paired_count"], paired, state["remaining_loss_budget_usd"],
    )


def install() -> None:
    # v5 reservation resolves these globals at runtime.  Increase only the turnover
    # limit and replace its state/policy with the paired-risk-aware versions.
    child_v5.PROMOTED_PROBE_LIMIT = PROMOTED_PROBE_LIMIT
    child_v5.promoted_state = promoted_state
    child_v5.automatic_rollover_allowed = automatic_rollover_allowed
    child_v2.unresolved_prior_trade_ids = unresolved_execution_risk_trade_ids
    child_v2.assert_prior_ledger_reconciled = assert_prior_ledger_reconciled_allow_paired
    child_v5.install()

    # Patch only the executor reference used by this isolated basis child.
    child.v2.executor.submit_dual_probe = thin_first.submit_kalshi_first_probe
    logger.warning(
        "CRYPTO_BASIS_RISK_V6_INSTALLED execution_sequence=KALSHI_THEN_GEMINI "
        "reviewed_execution_loss_usd=0.09 approved_loss_budget_usd=%s q=1 "
        "max_open_paired=%s turnover_probe_limit=%s one_inflight_unpaired=true "
        "new_loss_hard_stop=true shared_arb_lock_changed=false",
        child.APPROVED_BUDGET, MAX_OPEN_PAIRED_RISK, PROMOTED_PROBE_LIMIT,
    )


def main() -> None:
    install()
    child_v4.main()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    main()
