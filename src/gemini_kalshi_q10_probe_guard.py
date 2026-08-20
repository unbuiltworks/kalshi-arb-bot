"""Pure q=10 economics and capital requirements for future crypto scale work.

This module has no network or order-entry code.  It deliberately does not widen the
live q<=5 guard.  It validates an exact ten-contract complementary Gemini/Kalshi pair,
recomputes venue fees at q=10, enforces >=3c after-fee profit per contract and <=50ms
quote skew, and calculates the actual worst-case cash requirement on each venue before
any future q=10 submission could be considered.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

D = Decimal
Q10 = D("10")
MIN_PRICE = D("0.01")
MAX_PRICE = D("0.99")
MIN_PROFIT_PER_CONTRACT = D("0.03")
MAX_QUOTE_SKEW_MS = 50
MODEL_RECOMPUTE_TOLERANCE = D("0.000001")


@dataclass(frozen=True)
class Q10Economics:
    quantity: Decimal
    gemini_price: Decimal
    kalshi_yes_price: Decimal
    kalshi_contract_cost: Decimal
    gemini_fee: Decimal
    kalshi_fee: Decimal
    gemini_required_cash: Decimal
    kalshi_required_cash: Decimal
    total_required_cash: Decimal
    locked_profit: Decimal
    profit_per_contract: Decimal


def _q_from_payloads(gemini_payload: dict[str, Any], kalshi_payload: dict[str, Any]) -> Decimal:
    gq = D(str(gemini_payload.get("quantity")))
    kq = D(str(kalshi_payload.get("count")))
    if gq != kq:
        raise ValueError(f"q10 venue payload quantities differ gemini={gq} kalshi={kq}")
    if gq != Q10:
        raise ValueError(f"q10 path requires exactly {Q10} contracts got={gq}")
    return gq


def q10_economics(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    kalshi_fee_multiplier: Decimal,
) -> Q10Economics:
    """Recompute exact q=10 complementary P&L and per-venue cash requirements."""
    q = _q_from_payloads(gemini_payload, kalshi_payload)
    if str(gemini_payload.get("side")) != "BUY":
        raise ValueError("q10 Gemini leg must BUY an outcome")
    if str(gemini_payload.get("type")) != "LIMIT":
        raise ValueError("q10 Gemini leg must be LIMIT")
    if str(gemini_payload.get("timeInForce")) != "FOK":
        raise ValueError("q10 Gemini leg must be FOK")
    if str(kalshi_payload.get("time_in_force")) != "fill_or_kill":
        raise ValueError("q10 Kalshi leg must be fill_or_kill")

    gp = D(str(gemini_payload.get("price")))
    kp = D(str(kalshi_payload.get("price")))
    if not (MIN_PRICE <= gp <= MAX_PRICE):
        raise ValueError("q10 Gemini price outside prediction range")
    if not (MIN_PRICE <= kp <= MAX_PRICE):
        raise ValueError("q10 Kalshi YES-space price outside prediction range")

    outcome = str(gemini_payload.get("eventOutcome") or "").upper()
    kside = str(kalshi_payload.get("side") or "").lower()
    if outcome == "YES" and kside == "ask":
        kcost = D("1") - kp
    elif outcome == "NO" and kside == "bid":
        kcost = kp
    else:
        raise ValueError("q10 payload legs are not complementary outcomes")

    gfee = gemini_predictions_taker_fee(gp, q)
    kfee = kalshi_quadratic_taker_fee(kp, q, fee_multiplier=D(kalshi_fee_multiplier))
    gcash = q * gp + gfee
    kcash = q * kcost + kfee
    total = gcash + kcash
    profit = q - total
    return Q10Economics(
        quantity=q,
        gemini_price=gp,
        kalshi_yes_price=kp,
        kalshi_contract_cost=kcost,
        gemini_fee=gfee,
        kalshi_fee=kfee,
        gemini_required_cash=gcash,
        kalshi_required_cash=kcash,
        total_required_cash=total,
        locked_profit=profit,
        profit_per_contract=profit / q,
    )


def validate_q10_probe_payloads(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
    min_profit_per_contract: Decimal = MIN_PROFIT_PER_CONTRACT,
) -> Q10Economics:
    """Fail closed unless exact q=10 payloads preserve the configured floor."""
    if int(quote_skew_ms) > MAX_QUOTE_SKEW_MS:
        raise ValueError("q10 quote skew exceeds 50ms ceiling")
    economics = q10_economics(
        gemini_payload,
        kalshi_payload,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    if abs(economics.locked_profit - D(modeled_locked_profit)) > MODEL_RECOMPUTE_TOLERANCE:
        raise ValueError(
            "q10 outbound economics do not match modeled profit "
            f"modeled={modeled_locked_profit} recomputed={economics.locked_profit}"
        )
    floor = D(min_profit_per_contract)
    if floor <= 0:
        raise ValueError("q10 minimum profit per contract must be positive")
    if economics.profit_per_contract < floor:
        raise ValueError(
            "q10 after-fee profit per contract below floor "
            f"total={economics.locked_profit} per_contract={economics.profit_per_contract} floor={floor}"
        )
    return economics


def q10_reservation_requirements(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    kalshi_fee_multiplier: Decimal,
) -> dict[str, Decimal]:
    """Return actual per-venue cash that must be reserved before the first leg sends."""
    e = q10_economics(
        gemini_payload,
        kalshi_payload,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    return {
        "gemini": e.gemini_required_cash,
        "kalshi": e.kalshi_required_cash,
        "pair_total": e.total_required_cash,
    }
