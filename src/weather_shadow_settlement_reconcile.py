"""Fail-closed settlement reconciliation for read-only weather PAPER/SHADOW evidence.

This module never submits orders.  It joins one synthetic paper fill to one later
*official* weather settlement only when exact market/date/source provenance agrees.
The goal is to measure realized paper P/L without letting forecast-model EV or a
changed recurring Series template stand in for terminal settlement truth.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


_ALLOWED_PROVIDERS = {"NWS", "THE_WEATHER_COMPANY"}


def _strict_true(value: Any) -> bool:
    return value is True


def _decimal(value: Any) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def reconcile_weather_paper_fill(
    *,
    shadow_row: dict[str, Any],
    paper_fill: dict[str, Any],
    settlement: dict[str, Any],
) -> dict[str, Any]:
    """Return settlement-adjusted paper P/L or fail closed with explicit blockers.

    Required invariants:
    - the originating row is weather PAPER/SHADOW and never live-ready;
    - exact market ticker/date match across row, synthetic fill and settlement;
    - the settlement provider equals the exact-market provider certified at quote time;
    - terminal finality/correction review is explicitly complete;
    - synthetic fill side, quantity, price and modeled fee are numerically coherent.
    """
    blockers: list[str] = []

    if shadow_row.get("category") != "weather" or shadow_row.get("classification") not in {"PAPER", "SHADOW"}:
        blockers.append("NOT_WEATHER_PAPER_SHADOW")
    if shadow_row.get("order_entry") is not False or shadow_row.get("live_probe_ready") is not False:
        blockers.append("ORIGIN_NOT_READ_ONLY")

    market_ticker = str(shadow_row.get("market_ticker") or "").strip().upper()
    market_date = str(shadow_row.get("market_date") or "").strip()
    fill_ticker = str(paper_fill.get("market_ticker") or "").strip().upper()
    fill_date = str(paper_fill.get("market_date") or "").strip()
    settle_ticker = str(settlement.get("market_ticker") or "").strip().upper()
    settle_date = str(settlement.get("market_date") or "").strip()
    if not market_ticker or fill_ticker != market_ticker or settle_ticker != market_ticker:
        blockers.append("EXACT_MARKET_MISMATCH")
    if not market_date or fill_date != market_date or settle_date != market_date:
        blockers.append("EXACT_MARKET_DATE_MISMATCH")

    certified_provider = str(shadow_row.get("market_settlement_source_provider") or "").strip().upper()
    settlement_provider = str(settlement.get("settlement_source_provider") or "").strip().upper()
    if certified_provider not in _ALLOWED_PROVIDERS or settlement_provider != certified_provider:
        blockers.append("SETTLEMENT_SOURCE_MISMATCH")

    if not _strict_true(shadow_row.get("settlement_source_regime_verified")):
        blockers.append("QUOTE_TIME_SETTLEMENT_REGIME_UNVERIFIED")
    if not _strict_true(settlement.get("official_source_verified")):
        blockers.append("OFFICIAL_SETTLEMENT_SOURCE_UNVERIFIED")
    if not _strict_true(settlement.get("finality_verified")):
        blockers.append("SETTLEMENT_FINALITY_UNVERIFIED")
    if not _strict_true(settlement.get("correction_window_checked")):
        blockers.append("CORRECTION_WINDOW_UNVERIFIED")

    outcome = str(settlement.get("outcome") or "").strip().upper()
    if outcome not in {"YES", "NO"}:
        blockers.append("SETTLEMENT_OUTCOME_UNVERIFIED")

    side = str(paper_fill.get("side") or "").strip().upper()
    qty = paper_fill.get("quantity")
    price_cents = paper_fill.get("price_cents")
    fee_cents = paper_fill.get("modeled_fee_cents")
    if side not in {"YES", "NO"}:
        blockers.append("PAPER_FILL_SIDE_INVALID")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        blockers.append("PAPER_FILL_QUANTITY_INVALID")
    if not isinstance(price_cents, int) or isinstance(price_cents, bool) or not 1 <= price_cents <= 99:
        blockers.append("PAPER_FILL_PRICE_INVALID")
    fee = _decimal(fee_cents)
    if fee is None or fee < 0:
        blockers.append("PAPER_FILL_FEE_INVALID")

    blockers = sorted(set(blockers))
    if blockers:
        return {
            "reconciled": False,
            "blockers": blockers,
            "category": "weather",
            "classification": "PAPER",
            "market_ticker": market_ticker or None,
            "market_date": market_date or None,
            "paper_realized_pnl_cents": None,
            "read_only": True,
            "order_entry": False,
            "live_probe_ready": False,
        }

    cost_cents = Decimal(price_cents * qty) + fee
    payout_cents = Decimal(100 * qty) if side == outcome else Decimal(0)
    pnl_cents = payout_cents - cost_cents
    return {
        "reconciled": True,
        "blockers": [],
        "category": "weather",
        "classification": "PAPER",
        "market_ticker": market_ticker,
        "market_date": market_date,
        "side": side,
        "quantity": qty,
        "paper_cost_cents": str(cost_cents),
        "paper_payout_cents": str(payout_cents),
        "paper_realized_pnl_cents": str(pnl_cents),
        "settlement_outcome": outcome,
        "settlement_source_provider": settlement_provider,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
