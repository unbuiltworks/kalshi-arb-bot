"""Research-only, fail-closed inputs for venue-bound sequential ROUTE B replay.

No order submission, no inferred fills, and no change to production policy.
A missing venue field or noncausal book makes an episode unscorable.
"""
from decimal import Decimal, InvalidOperation
from typing import Mapping

class Unscorable(ValueError):
    """Venue evidence is insufficient for a realized/counterfactual claim."""

def _decimal(value, name):
    if value is None or isinstance(value, bool):
        raise Unscorable("missing " + name)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Unscorable("invalid " + name) from None
    if not result.is_finite():
        raise Unscorable("nonfinite " + name)
    return result

def verified_first_fill(fill: Mapping, market: str):
    """Require venue order/fill identifiers and an actual acquisition price."""
    if not market or fill.get("market") != market:
        raise Unscorable("market mismatch")
    for key in ("order_id", "fill_id", "side"):
        if not isinstance(fill.get(key), str) or not fill[key].strip():
            raise Unscorable("missing " + key)
    if fill["side"] not in ("UP", "DOWN"):
        raise Unscorable("invalid side")
    qty = _decimal(fill.get("qty"), "qty")
    price = _decimal(fill.get("price"), "price")
    fee = _decimal(fill.get("fee"), "fee")
    t = _decimal(fill.get("timestamp"), "timestamp")
    if qty <= 0 or not (0 < price < 1) or fee < 0 or t <= 0:
        raise Unscorable("invalid fill bounds")
    return dict(fill, qty=qty, price=price, fee=fee, timestamp=t)

def causal_book(book: Mapping, decision_t, max_age_s=2):
    """Never turn a future or stale frame into a decision-time quote."""
    observed = _decimal(book.get("observed_t"), "observed_t")
    decision = _decimal(decision_t, "decision_t")
    age = decision - observed
    if age < 0:
        raise Unscorable("future-dated book")
    if age > _decimal(max_age_s, "max_age_s"):
        raise Unscorable("stale book")
    return age

def settled_residual(first_fill: Mapping, settlement: Mapping, market: str):
    """Venue-confirmed residual net of first-fill fee; no placeholder basis."""
    f = verified_first_fill(first_fill, market)
    if settlement.get("market") != market or settlement.get("winner") not in ("UP", "DOWN"):
        raise Unscorable("missing settlement winner")
    if not isinstance(settlement.get("settlement_id"), str) or not settlement["settlement_id"].strip():
        raise Unscorable("missing settlement id")
    payout = Decimal(1) if settlement["winner"] == f["side"] else Decimal(0)
    return f["qty"] * (payout - f["price"]) - f["fee"]

def paired_counterfactual(first_fill: Mapping, complement_price, complement_fee,
                          market: str):
    """Counterfactual only: never label the complement as a venue fill."""
    f = verified_first_fill(first_fill, market)
    price = _decimal(complement_price, "complement_price")
    fee = _decimal(complement_fee, "complement_fee")
    if not (0 < price < 1) or fee < 0:
        raise Unscorable("invalid complement")
    return f["qty"] * (Decimal(1) - f["price"] - price) - f["fee"] - fee
