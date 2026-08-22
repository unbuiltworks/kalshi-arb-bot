"""Hard guardrails and payload builders for Gemini/Kalshi crypto probes.

No network calls exist in this module. It cannot place an order. The purpose is to
freeze the dangerous parts of live experiments before wiring transport.

INCIDENT LOCK: a production BTC trade on 2026-08-22 filled both legs but Gemini and
Kalshi settled opposite outcomes. Until exact settlement-source/index/averaging/
rounding/exception equivalence is proven, every Gemini/Kalshi crypto live transport
must remain disabled at this shared authorization layer. This is intentionally a
code-level lock, not an environment toggle, so alternate boot paths cannot bypass it.
"""

from __future__ import annotations

from decimal import Decimal
import os
import uuid

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

D = Decimal
MAX_PROBE_QUANTITY = D("1")
DEFAULT_MIN_LOCKED_PROFIT = D("0.03")
MAX_QUOTE_SKEW_MS = 50
MODEL_RECOMPUTE_TOLERANCE = D("0.000001")

# Hard production incident lock. Re-enable only by reviewed code change after exact
# settlement equivalence is independently certified for the traded contract family.
SETTLEMENT_EQUIVALENCE_PROVEN = False


def live_probe_authorized(cli_live: bool) -> bool:
    """Require opt-ins plus the code-level settlement-equivalence certification."""
    return (
        SETTLEMENT_EQUIVALENCE_PROVEN
        and bool(cli_live)
        and os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO") == "YES"
    )


def validate_probe_candidate(*, quantity: Decimal, locked_profit: Decimal, quote_skew_ms: int) -> None:
    if quantity != MAX_PROBE_QUANTITY:
        raise ValueError("first live probe is hard-capped at exactly 1 contract")
    if locked_profit < DEFAULT_MIN_LOCKED_PROFIT:
        raise ValueError("modeled after-fee locked profit is below 3 cents")
    if quote_skew_ms > MAX_QUOTE_SKEW_MS:
        raise ValueError("quote skew exceeds 50ms live-probe ceiling")


def recompute_payload_locked_profit(
    gemini_payload: dict,
    kalshi_payload: dict,
    *,
    kalshi_fee_multiplier: Decimal,
) -> Decimal:
    """Recompute one-contract complementary P&L from the exact payload prices."""
    if str(gemini_payload.get("side")) != "BUY":
        raise ValueError("first Gemini probe leg must BUY an outcome")
    if str(gemini_payload.get("type")) != "LIMIT":
        raise ValueError("first Gemini probe leg must be LIMIT")
    if str(gemini_payload.get("timeInForce")) != "FOK":
        raise ValueError("first Gemini probe leg must be FOK")
    if D(str(gemini_payload.get("quantity"))) != D("1"):
        raise ValueError("first Gemini probe leg must be exactly 1 contract")
    if str(kalshi_payload.get("time_in_force")) != "fill_or_kill":
        raise ValueError("first Kalshi probe leg must be fill_or_kill")
    if D(str(kalshi_payload.get("count"))) != D("1"):
        raise ValueError("first Kalshi probe leg must be exactly 1 contract")

    gemini_price = D(str(gemini_payload.get("price")))
    kalshi_yes_price = D(str(kalshi_payload.get("price")))
    if not (D("0.01") <= gemini_price <= D("0.99")):
        raise ValueError("Gemini payload price outside prediction range")
    if not (D("0.01") <= kalshi_yes_price <= D("0.99")):
        raise ValueError("Kalshi payload price outside prediction range")

    outcome = str(gemini_payload.get("eventOutcome") or "")
    kalshi_side = str(kalshi_payload.get("side") or "")
    if outcome == "YES" and kalshi_side == "ask":
        kalshi_contract_cost = D("1") - kalshi_yes_price
    elif outcome == "NO" and kalshi_side == "bid":
        kalshi_contract_cost = kalshi_yes_price
    else:
        raise ValueError("payload legs are not the required complementary outcomes")

    gemini_fee = gemini_predictions_taker_fee(gemini_price, D("1"))
    kalshi_fee = kalshi_quadratic_taker_fee(
        kalshi_yes_price,
        D("1"),
        fee_multiplier=D(kalshi_fee_multiplier),
    )
    return D("1") - gemini_price - kalshi_contract_cost - gemini_fee - kalshi_fee


def validate_exact_probe_payloads(
    gemini_payload: dict,
    kalshi_payload: dict,
    *,
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
) -> Decimal:
    """Fail closed unless outbound payload economics equal the shadow candidate."""
    recomputed = recompute_payload_locked_profit(
        gemini_payload,
        kalshi_payload,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    if abs(recomputed - D(modeled_locked_profit)) > MODEL_RECOMPUTE_TOLERANCE:
        raise ValueError("outbound payload economics do not match modeled locked profit")
    validate_probe_candidate(
        quantity=D("1"),
        locked_profit=recomputed,
        quote_skew_ms=int(quote_skew_ms),
    )
    return recomputed


def build_leg_payloads(
    *,
    direction: str,
    gemini_symbol: str,
    kalshi_ticker: str,
    gemini_yes_bid: Decimal,
    gemini_yes_ask: Decimal,
    kalshi_yes_bid: Decimal,
    kalshi_yes_ask: Decimal,
) -> tuple[dict, dict]:
    """Build one-contract FOK payloads at the observed executable top-of-book."""
    client_id = f"gk-probe-{uuid.uuid4().hex[:20]}"
    if direction == "KALSHI_YES_GEMINI_NO":
        gemini_price = D("1") - gemini_yes_bid
        kalshi_price = kalshi_yes_ask
        gemini_outcome = "NO"
        kalshi_side = "bid"
    elif direction == "GEMINI_YES_KALSHI_NO":
        gemini_price = gemini_yes_ask
        kalshi_price = kalshi_yes_bid
        gemini_outcome = "YES"
        kalshi_side = "ask"
    else:
        raise ValueError(f"unsupported direction={direction}")

    if not (D("0.01") <= gemini_price <= D("0.99")):
        raise ValueError("Gemini executable price outside supported prediction range")
    if not (D("0.01") <= kalshi_price <= D("0.99")):
        raise ValueError("Kalshi executable YES-space price outside supported range")

    gemini = {
        "symbol": gemini_symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "FOK",
        "price": str(gemini_price),
        "quantity": "1",
        "eventOutcome": gemini_outcome,
        "clientOrderId": client_id + "-g",
    }
    kalshi = {
        "ticker": kalshi_ticker,
        "client_order_id": client_id + "-k",
        "side": kalshi_side,
        "count": "1.00",
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
