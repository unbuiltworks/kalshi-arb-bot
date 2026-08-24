"""Hardened child entrypoint for the first q=1 settlement-basis live probe.

This wrapper does not change the proven dual-FOK transport or any quote/depth/freshness
threshold. It strengthens the isolated basis-risk child with durable controls:

* authoritative Postgres must show no submitted crypto trade lacking TRADE_REALIZED;
* the first live research tranche is globally limited to one CAPITAL_RESERVED event;
* all ledger writes use schema-valid LIVE_PROBE while metadata/strategy explicitly mark
  settlement-basis research rather than guaranteed arbitrage; and
* only this dedicated child process receives a basis-risk authorization function. The
  shared guaranteed-arbitrage settlement-equivalence code lock remains untouched.

The one-probe ceiling is intentionally stricter than the separately approved $5 maximum-
loss research budget. Promotion beyond the first <=$1 q=1 probe requires a deliberate
code/config change after its settlement is reviewed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
import os
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src import gemini_kalshi_basis_risk_marketdata as marketdata
from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_live_service as live_service
from src import gemini_kalshi_probe_transport as transport

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

_LEDGER_RECONCILED = False


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
    global _LEDGER_RECONCILED
    _LEDGER_RECONCILED = False
    with _connect() as conn:
        unresolved = unresolved_prior_trade_ids(conn)
    if unresolved:
        raise PermissionError(
            "basis-risk v2 blocked by unresolved prior crypto ledger trades: "
            + ",".join(unresolved[:8])
        )
    _LEDGER_RECONCILED = True
    logger.warning("CRYPTO_BASIS_RISK_LEDGER_RECONCILED unresolved_prior_trades=0")


def _env_decimal(name: str) -> Decimal | None:
    try:
        value = Decimal(os.getenv(name, "").strip())
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _basis_deadline_valid() -> bool:
    raw = os.getenv(child.BASIS_DEADLINE_ENV, "").strip()
    if not raw:
        return False
    try:
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) <= deadline.astimezone(timezone.utc)


def basis_transport_authorized(cli_live: bool) -> bool:
    """Authorize transport only inside the explicitly approved basis-risk process.

    This is deliberately NOT installed in ``gemini_kalshi_live_probe_guard``. The
    shared arb lock therefore remains false for q1/q5/maker and every other process.
    The basis child must have independently reconciled Postgres in this process, carry
    the exact approved $5 budget and >=10bp minimum, remain inside its short deadline,
    and retain both the basis-specific and legacy software opt-ins used by the proven
    transport. Durable one-probe ownership is still acquired immediately before send.
    """
    budget = _env_decimal(child.BASIS_BUDGET_ENV)
    min_gap = _env_decimal(child.BASIS_MIN_GAP_ENV)
    return bool(
        _LEDGER_RECONCILED
        and bool(cli_live)
        and os.getenv(child.BASIS_ARM_ENV, "NO").strip().upper() == "YES"
        and os.getenv(child.BASIS_RECONCILED_ENV, "NO").strip().upper() == "YES"
        and budget == child.APPROVED_BUDGET
        and min_gap is not None
        and min_gap >= child.DEFAULT_MIN_GAP_BPS
        and _basis_deadline_valid()
        and os.getenv(child.v2.ARM_ENV, "NO").strip().upper() == "YES"
        and os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() == "YES"
    )


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
    # Patch only this dedicated basis-risk process. Shared source-level authorization
    # remains SETTLEMENT_EQUIVALENCE_PROVEN=False for every guaranteed-arb path.
    child.MODE = LEDGER_MODE
    child.acquire_atomic_capital_reservation = acquire_atomic_capital_reservation
    child.v2.executor.live_probe_authorized = basis_transport_authorized
    transport.live_probe_authorized = basis_transport_authorized
    live_service.live_probe_authorized = basis_transport_authorized
    marketdata.install()
    logger.warning(
        "CRYPTO_BASIS_RISK_TRANSPORT_AUTH_INSTALLED scope=PROCESS_LOCAL shared_arb_lock_changed=false"
    )


def main() -> None:
    assert_prior_ledger_reconciled()
    os.environ[child.BASIS_RECONCILED_ENV] = "YES"
    install()
    child.main()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    main()
