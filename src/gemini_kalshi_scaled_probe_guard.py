"""Pure q1-q5 economics and payload validation for bounded crypto scale canaries.

No network or order-entry code lives here.  The module keeps the live q=1 guard
unchanged and provides a separate, explicit scale path that can validate up to five
contracts while preserving a minimum after-fee profit *per contract*.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

D = Decimal
MIN_PRICE = D("0.01")
MAX_PRICE = D("0.99")
MAX_SCALE_QUANTITY = D("5")
DEFAULT_MIN_PROFIT_PER_CONTRACT = D("0.03")
MAX_QUOTE_SKEW_MS = 50
MODEL_RECOMPUTE_TOLERANCE = D("0.000001")


def _quantity_from_payloads(gemini_payload: dict[str, Any], kalshi_payload: dict[str, Any]) -> D:
    gq = D(str(gemini_payload.get("quantity")))
    kq = D(str(kalshi_payload.get("count")))
    if gq != kq:
        raise ValueError(f"venue payload quantities differ gemini={gq} kalshi={kq}")
    if gq != gq.to_integral_value() or not (D("1") <= gq <= MAX_SCALE_QUANTITY):
        raise ValueError(f"scaled canary quantity must be an integer from 1 to {MAX_SCALE_QUANTITY}")
    return gq


def recompute_scaled_payload_locked_profit(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    kalshi_fee_multiplier: Decimal,
) -> D:
    if str(gemini_payload.get("side")) != "BUY":
        raise ValueError("Gemini scale leg must BUY an outcome")
    if str(gemini_payload.get("type")) != "LIMIT":
        raise ValueError("Gemini scale leg must be LIMIT")
    if str(gemini_payload.get("timeInForce")) != "FOK":
        raise ValueError("Gemini scale leg must be FOK")
    if str(kalshi_payload.get("time_in_force")) != "fill_or_kill":
        raise ValueError("Kalshi scale leg must be fill_or_kill")
    q = _quantity_from_payloads(gemini_payload, kalshi_payload)
    gemini_price = D(str(gemini_payload.get("price")))
    kalshi_yes_price = D(str(kalshi_payload.get("price")))
    if not (MIN_PRICE <= gemini_price <= MAX_PRICE):
        raise ValueError("Gemini payload price outside prediction range")
    if not (MIN_PRICE <= kalshi_yes_price <= MAX_PRICE):
        raise ValueError("Kalshi payload price outside prediction range")
    outcome = str(gemini_payload.get("eventOutcome") or "")
    kalshi_side = str(kalshi_payload.get("side") or "")
    if outcome == "YES" and kalshi_side == "ask":
        kalshi_contract_cost = D("1") - kalshi_yes_price
    elif outcome == "NO" and kalshi_side == "bid":
        kalshi_contract_cost = kalshi_yes_price
    else:
        raise ValueError("scaled payload legs are not complementary outcomes")
    gemini_fee = gemini_predictions_taker_fee(gemini_price, q)
    kalshi_fee = kalshi_quadratic_taker_fee(kalshi_yes_price, q, fee_multiplier=D(kalshi_fee_multiplier))
    return q - q * gemini_price - q * kalshi_contract_cost - gemini_fee - kalshi_fee


def validate_scaled_probe_payloads(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *, modeled_locked_profit: Decimal, kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int, required_quantity: Decimal | None = None,
    min_profit_per_contract: Decimal = DEFAULT_MIN_PROFIT_PER_CONTRACT,
) -> D:
    q = _quantity_from_payloads(gemini_payload, kalshi_payload)
    if required_quantity is not None and q != D(required_quantity):
        raise ValueError(f"scaled canary requires quantity={D(required_quantity)} got={q}")
    if int(quote_skew_ms) > MAX_QUOTE_SKEW_MS:
        raise ValueError("quote skew exceeds 50ms scale-canary ceiling")
    recomputed = recompute_scaled_payload_locked_profit(gemini_payload, kalshi_payload, kalshi_fee_multiplier=D(kalshi_fee_multiplier))
    if abs(recomputed - D(modeled_locked_profit)) > MODEL_RECOMPUTE_TOLERANCE:
        raise ValueError(f"scaled outbound economics do not match modeled profit modeled={modeled_locked_profit} recomputed={recomputed}")
    floor = D(min_profit_per_contract)
    if floor <= 0:
        raise ValueError("minimum profit per contract must be positive")
    if recomputed / q < floor:
        raise ValueError(f"scaled after-fee profit per contract below floor q={q} total={recomputed} floor_per_contract={floor}")
    return recomputed


def scale_payload_quantities(gemini_payload: dict[str, Any], kalshi_payload: dict[str, Any], *, quantity: Decimal) -> tuple[dict[str, Any], dict[str, Any]]:
    q = D(quantity)
    if q != q.to_integral_value() or not (D("1") <= q <= MAX_SCALE_QUANTITY):
        raise ValueError(f"quantity must be an integer from 1 to {MAX_SCALE_QUANTITY}")
    g = deepcopy(gemini_payload); k = deepcopy(kalshi_payload)
    g["quantity"] = str(q.to_integral_value()); k["count"] = str(q.quantize(D("0.00")))
    return g, k
