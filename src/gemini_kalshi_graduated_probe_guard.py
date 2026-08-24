"""Fail-closed payload guard for a future graduated Gemini/Kalshi sports probe.

This module contains no network calls and cannot place orders.  The existing q=1
live guard remains untouched.  Promotion above q=1 is deliberately limited to q=5
and requires every quantity-safety gate, including a complete rule fingerprint.

The economic floor scales with quantity: a q=5 pair must lock at least $0.15 after
modeled fees, not merely $0.03 total.
"""
from __future__ import annotations

from decimal import Decimal
import uuid
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.sports_dynamic_quantity import QuantitySafetyGates

D = Decimal
ONE = D("1")
Q5 = D("5")
ALLOWED_QUANTITIES = frozenset({ONE, Q5})
MIN_PROFIT_PER_CONTRACT = D("0.03")
MAX_QUOTE_SKEW_MS = 50
MODEL_RECOMPUTE_TOLERANCE = D("0.000001")


def _q(value: Any) -> D:
    out = D(str(value))
    if out not in ALLOWED_QUANTITIES:
        raise ValueError("graduated probe quantity must be exactly 1 or 5")
    return out


def _require_gates(gates: QuantitySafetyGates) -> None:
    failures = gates.failures()
    if failures:
        raise ValueError("quantity safety gate failed: " + ",".join(failures))
    # Keep this explicit even though rule_fingerprint_certified is included in
    # ``failures`` so future edits cannot accidentally make q5 a rule-risk probe.
    if not gates.rule_fingerprint_certified:
        raise ValueError("complete rule fingerprint required for graduated quantity")


def validate_graduated_candidate(
    *,
    quantity: Decimal,
    locked_profit: Decimal,
    quote_skew_ms: int,
    authorized_max_quantity: int,
    gates: QuantitySafetyGates,
) -> None:
    q = _q(quantity)
    if authorized_max_quantity not in {1, 5}:
        raise ValueError("authorized_max_quantity must be exactly 1 or 5")
    if q > D(authorized_max_quantity):
        raise ValueError("quantity exceeds explicit promotion authorization")
    _require_gates(gates)
    if D(locked_profit) < MIN_PROFIT_PER_CONTRACT * q:
        raise ValueError("modeled after-fee locked profit is below 3 cents per contract")
    if int(quote_skew_ms) > MAX_QUOTE_SKEW_MS:
        raise ValueError("quote skew exceeds 50ms graduated-probe ceiling")


def build_leg_payloads(
    *,
    direction: str,
    quantity: Decimal,
    authorized_max_quantity: int,
    gates: QuantitySafetyGates,
    gemini_symbol: str,
    kalshi_ticker: str,
    gemini_yes_bid: Decimal,
    gemini_yes_ask: Decimal,
    kalshi_yes_bid: Decimal,
    kalshi_yes_ask: Decimal,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build exact q=1/q=5 FOK payloads after promotion gates pass."""
    q = _q(quantity)
    if q > D(authorized_max_quantity):
        raise ValueError("quantity exceeds explicit promotion authorization")
    _require_gates(gates)

    client_id = f"gk-grad-{uuid.uuid4().hex[:20]}"
    if direction == "KALSHI_YES_GEMINI_NO":
        gemini_price = D("1") - D(gemini_yes_bid)
        kalshi_price = D(kalshi_yes_ask)
        gemini_outcome = "NO"
        kalshi_side = "bid"
    elif direction == "GEMINI_YES_KALSHI_NO":
        gemini_price = D(gemini_yes_ask)
        kalshi_price = D(kalshi_yes_bid)
        gemini_outcome = "YES"
        kalshi_side = "ask"
    else:
        raise ValueError(f"unsupported direction={direction}")

    if not (D("0.01") <= gemini_price <= D("0.99")):
        raise ValueError("Gemini executable price outside supported prediction range")
    if not (D("0.01") <= kalshi_price <= D("0.99")):
        raise ValueError("Kalshi executable YES-space price outside supported range")

    quantity_text = format(q, "f")
    gemini = {
        "symbol": gemini_symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "FOK",
        "price": str(gemini_price),
        "quantity": quantity_text,
        "eventOutcome": gemini_outcome,
        "clientOrderId": client_id + "-g",
    }
    kalshi = {
        "ticker": kalshi_ticker,
        "client_order_id": client_id + "-k",
        "side": kalshi_side,
        "count": quantity_text,
        "price": str(kalshi_price),
        "time_in_force": "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }
    return gemini, kalshi


def recompute_payload_locked_profit(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    kalshi_fee_multiplier: Decimal,
) -> tuple[D, D]:
    if str(gemini_payload.get("side")) != "BUY":
        raise ValueError("Gemini graduated leg must BUY an outcome")
    if str(gemini_payload.get("type")) != "LIMIT" or str(gemini_payload.get("timeInForce")) != "FOK":
        raise ValueError("Gemini graduated leg must be LIMIT FOK")
    if str(kalshi_payload.get("time_in_force")) != "fill_or_kill":
        raise ValueError("Kalshi graduated leg must be fill_or_kill")

    gq = _q(gemini_payload.get("quantity"))
    kq = _q(kalshi_payload.get("count"))
    if gq != kq:
        raise ValueError("outbound leg quantities do not match")
    q = gq

    gemini_price = D(str(gemini_payload.get("price")))
    kalshi_yes_price = D(str(kalshi_payload.get("price")))
    if not (D("0.01") <= gemini_price <= D("0.99")):
        raise ValueError("Gemini payload price outside prediction range")
    if not (D("0.01") <= kalshi_yes_price <= D("0.99")):
        raise ValueError("Kalshi payload price outside prediction range")

    outcome = str(gemini_payload.get("eventOutcome") or "")
    kalshi_side = str(kalshi_payload.get("side") or "")
    if outcome == "YES" and kalshi_side == "ask":
        kalshi_contract_cost = ONE - kalshi_yes_price
    elif outcome == "NO" and kalshi_side == "bid":
        kalshi_contract_cost = kalshi_yes_price
    else:
        raise ValueError("payload legs are not complementary outcomes")

    gemini_fee = gemini_predictions_taker_fee(gemini_price, q)
    kalshi_fee = kalshi_quadratic_taker_fee(
        kalshi_yes_price,
        q,
        fee_multiplier=D(kalshi_fee_multiplier),
    )
    locked = q - (q * gemini_price) - (q * kalshi_contract_cost) - gemini_fee - kalshi_fee
    return q, locked


def validate_exact_graduated_payloads(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
    authorized_max_quantity: int,
    gates: QuantitySafetyGates,
) -> Decimal:
    q, recomputed = recompute_payload_locked_profit(
        gemini_payload,
        kalshi_payload,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    if abs(recomputed - D(modeled_locked_profit)) > MODEL_RECOMPUTE_TOLERANCE:
        raise ValueError("outbound payload economics do not match modeled locked profit")
    validate_graduated_candidate(
        quantity=q,
        locked_profit=recomputed,
        quote_skew_ms=int(quote_skew_ms),
        authorized_max_quantity=authorized_max_quantity,
        gates=gates,
    )
    return recomputed
