"""Promoted q=1 basis-risk child after the first live probe settled profitably.

This is still settlement-basis research, not guaranteed arbitrage. Promotion is bounded
by the user's already-approved $5 maximum-loss research budget and remains stricter than
that budget:

* q=1 and <=$1 reserved capital per probe;
* at most five lifetime CAPITAL_RESERVED rows for this strategy;
* exactly one unresolved/reserved probe at a time;
* every prior reservation must be authoritatively TRADE_REALIZED before another claim;
* any realized loss hard-stops all future automatic reservations;
* total reserved capital can never exceed the approved $5 research budget;
* all v4 dynamic dominance/economics/freshness/depth/FOK gates remain unchanged.

The shared guaranteed-arbitrage settlement-equivalence lock is untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any

from psycopg.types.json import Jsonb

from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_basis_risk_q1_child_v4 as child_v4

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_child_v5")

PROMOTED_PROBE_LIMIT = 5
LOCK_NAME = child_v2.LOCK_NAME


def promoted_state(conn) -> dict[str, Any]:
    """Return authoritative promotion state from Postgres."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE event_type='CAPITAL_RESERVED')::int AS reservations,
                COALESCE(SUM(amount) FILTER (WHERE event_type='CAPITAL_RESERVED'), 0)::numeric AS reserved_usd,
                COUNT(*) FILTER (WHERE event_type='TRADE_REALIZED')::int AS realized,
                COUNT(*) FILTER (WHERE event_type='TRADE_REALIZED' AND net_pnl < 0)::int AS realized_losses,
                COALESCE(SUM(CASE WHEN event_type='TRADE_REALIZED' AND net_pnl < 0 THEN -net_pnl ELSE 0 END), 0)::numeric AS realized_loss_usd,
                COALESCE(SUM(CASE WHEN event_type='TRADE_REALIZED' THEN net_pnl ELSE 0 END), 0)::numeric AS realized_net_pnl
            FROM public.trade_events
            WHERE strategy=%s
            """,
            (child.STRATEGY,),
        )
        reservations, reserved_usd, realized, realized_losses, realized_loss_usd, realized_net_pnl = cur.fetchone()
        cur.execute(
            """
            SELECT DISTINCT r.trade_id
            FROM public.trade_events AS r
            WHERE r.strategy=%s
              AND r.event_type='CAPITAL_RESERVED'
              AND NOT EXISTS (
                  SELECT 1 FROM public.trade_events AS z
                  WHERE z.trade_id=r.trade_id AND z.event_type='TRADE_REALIZED'
              )
            ORDER BY r.trade_id
            """,
            (child.STRATEGY,),
        )
        unresolved_reservations = [str(row[0]) for row in cur.fetchall()]
        unresolved_submissions = child_v2.unresolved_prior_trade_ids(conn)
    return {
        "reservations": int(reservations or 0),
        "reserved_usd": D(str(reserved_usd or 0)),
        "realized": int(realized or 0),
        "realized_losses": int(realized_losses or 0),
        "realized_loss_usd": D(str(realized_loss_usd or 0)),
        "realized_net_pnl": D(str(realized_net_pnl or 0)),
        "unresolved_reservations": unresolved_reservations,
        "unresolved_submissions": unresolved_submissions,
    }


def automatic_rollover_allowed(state: dict[str, Any]) -> tuple[bool, str]:
    if state["unresolved_reservations"]:
        return False, "UNRESOLVED_RESERVATION"
    if state["unresolved_submissions"]:
        return False, "UNRESOLVED_SUBMISSION"
    if int(state["realized_losses"]) > 0:
        return False, "REALIZED_LOSS_HARD_STOP"
    if D(state["realized_loss_usd"]) >= child.APPROVED_BUDGET:
        return False, "APPROVED_LOSS_BUDGET_EXHAUSTED"
    if int(state["reservations"]) >= PROMOTED_PROBE_LIMIT:
        return False, "PROMOTED_PROBE_LIMIT_REACHED"
    if D(state["reserved_usd"]) + child.INITIAL_TRANCHE_USD > child.APPROVED_BUDGET:
        return False, "APPROVED_RESERVATION_BUDGET_EXCEEDED"
    return True, "OK"


def acquire_promoted_capital_reservation(
    *,
    run_id: str,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    context: dict[str, Any],
    modeled_locked_profit: Any,
) -> bool:
    """Atomically claim the next sequential <=$1 probe, never overlapping risk."""
    trade_id = f"gkbasis-{run_id}"
    event_id = f"{trade_id}-capital-reserved"
    symbol = str(gemini_payload.get("symbol") or "")
    ticker = str(kalshi_payload.get("ticker") or "")

    with child_v2._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (LOCK_NAME,))
            state = promoted_state(conn)
            allowed, reason = automatic_rollover_allowed(state)
            if not allowed:
                logger.critical(
                    "CRYPTO_BASIS_RISK_PROMOTED_RESERVATION_BLOCKED reason=%s reservations=%s realized=%s losses=%s unresolved_reservations=%s unresolved_submissions=%s",
                    reason,
                    state["reservations"],
                    state["realized"],
                    state["realized_losses"],
                    state["unresolved_reservations"][:5],
                    state["unresolved_submissions"][:5],
                )
                conn.rollback()
                return False

            metadata = {
                "run_id": run_id,
                "classification": "LIVE_BASIS_RISK_RESEARCH_NOT_GUARANTEED_ARB",
                "risk_class": "SETTLEMENT_BASIS_RESEARCH",
                "reserved_usd": str(child.INITIAL_TRANCHE_USD),
                "approved_max_loss_budget_usd": str(child.APPROVED_BUDGET),
                "modeled_locked_profit": str(modeled_locked_profit or ""),
                "single_use_claim": "POSTGRES_ADVISORY_LOCK_PLUS_PRIMARY_KEY",
                "promoted_probe_limit": PROMOTED_PROBE_LIMIT,
                "automatic_rollover": True,
                "prior_realized_probes": state["realized"],
                "prior_realized_net_pnl": str(state["realized_net_pnl"]),
                "hard_stop_on_any_realized_loss": True,
                "basis_risk_context": dict(context),
            }
            cur.execute(
                """
                INSERT INTO public.trade_events (
                    event_id, trade_id, event_type, event_ts, strategy, mode,
                    market, asset, venues, status, quantity, amount, fee,
                    legs_required, legs_filled, opportunity_net_profit, metadata
                ) VALUES (
                    %s, %s, 'CAPITAL_RESERVED', %s, %s, %s,
                    %s, %s, %s, 'CAPITAL_RESERVED', 1, %s, 0,
                    2, 0, %s, %s
                )
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
                """,
                (
                    event_id,
                    trade_id,
                    datetime.now(timezone.utc),
                    child.STRATEGY,
                    child_v2.LEDGER_MODE,
                    f"{symbol}|{ticker}",
                    str(context.get("asset") or ""),
                    ["Gemini", "Kalshi"],
                    child.INITIAL_TRANCHE_USD,
                    child._dec(modeled_locked_profit),
                    Jsonb(metadata),
                ),
            )
            won = cur.fetchone() is not None
        if won:
            conn.commit()
        else:
            conn.rollback()
    if won:
        logger.critical(
            "CRYPTO_BASIS_RISK_PROMOTED_CLAIM_WON run_id=%s probe_number=%s reserved_usd=%s order_submission_next=true",
            run_id,
            int(state["reservations"]) + 1,
            child.INITIAL_TRANCHE_USD,
        )
    return won


def install() -> None:
    # v4 -> v3 -> v2.main() eventually calls v2.install(). Replacing the module-level
    # function here causes that install to bind only this promoted reservation policy.
    child_v2.acquire_atomic_capital_reservation = acquire_promoted_capital_reservation
    logger.warning(
        "CRYPTO_BASIS_RISK_V5_PROMOTION_INSTALLED probe_limit=%s q=1 tranche_usd=%s approved_budget_usd=%s one_unresolved=true hard_stop_on_any_loss=true shared_arb_lock_changed=false",
        PROMOTED_PROBE_LIMIT,
        child.INITIAL_TRANCHE_USD,
        child.APPROVED_BUDGET,
    )


def main() -> None:
    install()
    child_v4.main()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    main()
