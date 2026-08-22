"""Hardened child entrypoint for the first q=1 settlement-basis live probe.

This wrapper does not change the proven dual-FOK transport or any quote/depth/freshness
threshold. It strengthens the isolated basis-risk child with three durable controls:

* authoritative Postgres must show no submitted crypto trade lacking TRADE_REALIZED;
* the first live research tranche is globally limited to one CAPITAL_RESERVED event;
* all ledger writes use the schema-valid LIVE_PROBE mode while metadata/strategy keep
  the activity explicitly classified as settlement-basis research, not guaranteed arb.

The one-probe ceiling is intentionally stricter than the separately approved $5 maximum-
loss research budget. Promotion beyond the first <=$1 q=1 probe requires a deliberate
code/config change after its settlement is reviewed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import logging
import os
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src import gemini_kalshi_basis_risk_q1_child as child

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_child_v2")

LEDGER_MODE = "LIVE_PROBE"
INITIAL_PROBE_LIMIT = 1
LOCK_NAME = "gemini_kalshi_15m_crypto_basis_risk:first_q1_probe"
PRIOR_CRYPTO_STRATEGIES = (
    "gemini_kalshi_15m_crypto",
    "gemini_kalshi_15m_crypto_scale",
    child.STRATEGY,
)


def _connect():
    database_url = os.getenv(child.DATABASE_URL_ENV, "").strip()
    if not database_url:
        raise PermissionError("basis-risk v2 requires DATABASE_URL")
    return psycopg.connect(database_url, connect_timeout=5)


def unresolved_prior_trade_ids(conn) -> list[str]:
    """Return submitted crypto trades that still lack authoritative realization."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT s.trade_id
            FROM public.trade_events AS s
            WHERE s.strategy = ANY(%s)
              AND s.event_type = 'ORDER_SUBMITTED'
              AND NOT EXISTS (
                  SELECT 1
                  FROM public.trade_events AS r
                  WHERE r.trade_id = s.trade_id
                    AND r.event_type = 'TRADE_REALIZED'
              )
            ORDER BY s.trade_id
            """,
            (list(PRIOR_CRYPTO_STRATEGIES),),
        )
        return [str(row[0]) for row in cur.fetchall()]


def assert_prior_ledger_reconciled() -> None:
    """Fail closed unless every prior submitted crypto trade is realized."""
    with _connect() as conn:
        unresolved = unresolved_prior_trade_ids(conn)
    if unresolved:
        raise PermissionError(
            "basis-risk v2 blocked by unresolved prior crypto ledger trades: "
            + ",".join(unresolved[:8])
        )
    logger.warning("CRYPTO_BASIS_RISK_LEDGER_RECONCILED unresolved_prior_trades=0")


def acquire_atomic_capital_reservation(
    *,
    run_id: str,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    context: dict[str, Any],
    modeled_locked_profit: Any,
) -> bool:
    """Atomically reserve the first <=$1 q=1 probe and never auto-reset it.

    The transaction-level advisory lock serializes all v2 reservation attempts. Once
    one CAPITAL_RESERVED row exists for this strategy, every future run id fails closed
    until promotion is explicitly implemented. The approved $5 budget is also checked
    independently so a later promotion cannot silently exceed it.
    """
    trade_id = f"gkbasis-{run_id}"
    event_id = f"{trade_id}-capital-reserved"
    symbol = str(gemini_payload.get("symbol") or "")
    ticker = str(kalshi_payload.get("ticker") or "")
    metadata = {
        "run_id": run_id,
        "classification": "LIVE_BASIS_RISK_RESEARCH_NOT_GUARANTEED_ARB",
        "risk_class": "SETTLEMENT_BASIS_RESEARCH",
        "reserved_usd": str(child.INITIAL_TRANCHE_USD),
        "approved_max_loss_budget_usd": str(child.APPROVED_BUDGET),
        "modeled_locked_profit": str(modeled_locked_profit or ""),
        "single_use_claim": "POSTGRES_ADVISORY_LOCK_PLUS_PRIMARY_KEY",
        "initial_probe_limit": INITIAL_PROBE_LIMIT,
        "basis_risk_context": dict(context),
    }

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (LOCK_NAME,))

            unresolved = unresolved_prior_trade_ids(conn)
            if unresolved:
                logger.critical(
                    "CRYPTO_BASIS_RISK_RESERVATION_BLOCKED unresolved_prior=%s",
                    unresolved[:8],
                )
                conn.rollback()
                return False

            cur.execute(
                """
                SELECT COUNT(*)::int, COALESCE(SUM(amount), 0)::numeric
                FROM public.trade_events
                WHERE strategy = %s
                  AND event_type = 'CAPITAL_RESERVED'
                """,
                (child.STRATEGY,),
            )
            count, reserved = cur.fetchone()
            reserved = D(str(reserved))
            if int(count) >= INITIAL_PROBE_LIMIT:
                logger.critical(
                    "CRYPTO_BASIS_RISK_RESERVATION_BLOCKED reason=INITIAL_PROBE_ALREADY_RESERVED count=%s",
                    count,
                )
                conn.rollback()
                return False
            if reserved + child.INITIAL_TRANCHE_USD > child.APPROVED_BUDGET:
                logger.critical(
                    "CRYPTO_BASIS_RISK_RESERVATION_BLOCKED reason=APPROVED_BUDGET_EXCEEDED reserved=%s",
                    reserved,
                )
                conn.rollback()
                return False

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
                    LEDGER_MODE,
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
    return won


def install() -> None:
    # Patch only the research-only accounting/claim surface. Execution transport and
    # all market microstructure gates remain the original tested child implementation.
    child.MODE = LEDGER_MODE
    child.acquire_atomic_capital_reservation = acquire_atomic_capital_reservation


def main() -> None:
    assert_prior_ledger_reconciled()
    os.environ[child.BASIS_RECONCILED_ENV] = "YES"
    install()
    child.main()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    main()
