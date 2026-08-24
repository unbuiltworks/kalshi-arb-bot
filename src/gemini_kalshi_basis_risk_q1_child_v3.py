"""Basis-risk q1 child with best-effort quote-conditioned gate telemetry.

This wrapper does not change any trading authorization, quote/depth/freshness gate,
threshold rule, capital limit, or transport. It observes the already-existing
threshold-dominance probe and writes one deduplicated non-P&L ledger row per
asset/window/direction/blocker. Database/telemetry failure is swallowed and cannot
change the underlying probe result.

It also installs read-only Gemini-maker/Kalshi-taker feasibility telemetry before the
normal taker/taker >=3c gate. That diagnostic cannot submit or authorize an order.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
from typing import Any

from psycopg.types.json import Jsonb

from src import gemini_kalshi_basis_risk_maker_shadow as maker_shadow
from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_q1_child_v3")
EVENT_TYPE = "BASIS_RISK_GATE_OBSERVATION"
STATUS = "DIAGNOSTIC_ONLY"
_ORIGINAL_INSTALL_DOMINANCE = child._install_dominance_probe


def _safe_token(value: Any) -> str:
    return str(value or "").strip()


def _observation_payload(*, plan: Any, min_gap_bps: Decimal) -> dict[str, Any]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    snapshot = child.v2.readiness.basis.basis_snapshot(plan.asset, now_ms)
    target_info = child.v2.readiness._KALSHI_TARGETS.get(plan.asset)
    target = None
    target_window = None
    if target_info is not None:
        target_window, target_value = target_info
        if snapshot is not None and target_window == snapshot.get("basis_window_end_utc"):
            target = target_value
    g_strike = child._dec(snapshot.get("kaiko_strike")) if snapshot else None
    auth = child.dominance_authorization(
        direction=plan.direction,
        gemini_strike=g_strike,
        kalshi_target=target,
        min_gap_bps=min_gap_bps,
    )
    auth.update({
        "asset": plan.asset,
        "gemini_symbol": plan.gemini_symbol,
        "kalshi_ticker": plan.kalshi_ticker,
        "basis_window_end_utc": snapshot.get("basis_window_end_utc") if snapshot else None,
        "target_window_end_utc": target_window,
        "classification": "LIVE_BASIS_RISK_RESEARCH_DIAGNOSTIC",
        "guaranteed_arb": False,
        "trade_authorized_by_telemetry": False,
        "order_entry_by_telemetry": False,
    })
    return auth


def _identity(payload: dict[str, Any]) -> tuple[str, str]:
    raw = "|".join([
        _safe_token(payload.get("asset")),
        _safe_token(payload.get("basis_window_end_utc")),
        _safe_token(payload.get("direction")),
        _safe_token(payload.get("blocker")),
        _safe_token(payload.get("gemini_symbol")),
        _safe_token(payload.get("kalshi_ticker")),
    ])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"gkbasisobs-{digest}", digest


def persist_observation_best_effort(payload: dict[str, Any]) -> bool:
    """Write diagnostic evidence without ever influencing execution behavior."""
    try:
        event_id, digest = _identity(payload)
        market = f"{_safe_token(payload.get('gemini_symbol'))}|{_safe_token(payload.get('kalshi_ticker'))}"
        with child_v2._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.trade_events (
                        event_id, trade_id, event_type, event_ts, strategy, mode,
                        market, asset, venues, status, quantity, amount, fee,
                        gross_pnl, net_pnl, legs_required, legs_filled,
                        opportunity_net_profit, metadata
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, 0, 0, 0,
                        NULL, NULL, 0, 0,
                        NULL, %s
                    )
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    (
                        event_id,
                        f"basis-observation-{digest}",
                        EVENT_TYPE,
                        datetime.now(timezone.utc),
                        child.STRATEGY,
                        child_v2.LEDGER_MODE,
                        market,
                        _safe_token(payload.get("asset")),
                        ["Gemini", "Kalshi"],
                        STATUS,
                        Jsonb(payload),
                    ),
                )
                inserted = cur.fetchone() is not None
            conn.commit()
        return bool(inserted)
    except Exception as exc:  # telemetry must never become a trading dependency
        logger.warning("CRYPTO_BASIS_RISK_GATE_TELEMETRY_FAILED %s", type(exc).__name__)
        return False


def _install_dominance_probe_with_telemetry(min_gap_bps: Decimal) -> None:
    _ORIGINAL_INSTALL_DOMINANCE(min_gap_bps)
    maker_shadow.install(min_gap_bps=min_gap_bps)
    exact = child.v2.readiness.exact
    original_probe = exact._probe

    async def observed_probe(*, feed, plan, active_episodes, delays_ms=(50, 100)):
        try:
            payload = _observation_payload(plan=plan, min_gap_bps=min_gap_bps)
            persist_observation_best_effort(payload)
            logger.info(
                "CRYPTO_BASIS_RISK_GATE_TELEMETRY %s",
                json.dumps(payload, separators=(",", ":"), default=str),
            )
        except Exception as exc:
            logger.warning("CRYPTO_BASIS_RISK_GATE_TELEMETRY_OBSERVE_FAILED %s", type(exc).__name__)
        return await original_probe(
            feed=feed,
            plan=plan,
            active_episodes=active_episodes,
            delays_ms=delays_ms,
        )

    exact._probe = observed_probe


def install() -> None:
    child._install_dominance_probe = _install_dominance_probe_with_telemetry
    logger.warning(
        "CRYPTO_BASIS_RISK_GATE_TELEMETRY_INSTALLED event_type=%s execution_gates_changed=false",
        EVENT_TYPE,
    )


def main() -> None:
    install()
    child_v2.main()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    main()
