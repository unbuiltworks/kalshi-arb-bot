"""Conservative attribution of PMUS positions across L5 strategies.

Classification is bookkeeping only, not trading authorization. Positions
without authenticated origin remain UNKNOWN and must still count against
the account-wide exposure and loss gates.
"""
from decimal import Decimal, InvalidOperation


class AttributionError(ValueError):
    pass


def origin_from_client_order_id(client_order_id):
    if not isinstance(client_order_id, str) or not client_order_id:
        return "unknown"
    if client_order_id.startswith("l5f-"):
        return "btc15m"
    if client_order_id.startswith("b5m-"):
        return "btc5m"
    return "unknown"


def attribute_fill(*, client_order_id, venue_order_id, market, quantity,
                   unit_price, authenticated_order_ids):
    """Only orders verified in our durable order registry get a strategy label.

    The registry maps venue IDs to exact client ID and market. A prefix alone
    cannot establish provenance. Unknowns carry worst-case $1/share basis.
    """
    if not isinstance(authenticated_order_ids, dict):
        raise AttributionError("registry unavailable")
    try:
        qty, px = Decimal(str(quantity)), Decimal(str(unit_price))
    except (InvalidOperation, TypeError, ValueError):
        raise AttributionError("invalid fill") from None
    if not qty.is_finite() or qty < 0 or not px.is_finite() or not 0 <= px <= 1:
        raise AttributionError("invalid fill")
    record = authenticated_order_ids.get(venue_order_id)
    verified = (isinstance(record, dict)
                and record.get("client_order_id") == client_order_id
                and record.get("market") == market
                and isinstance(venue_order_id, str) and bool(venue_order_id))
    origin = origin_from_client_order_id(client_order_id) if verified else "unknown"
    basis = qty * (px if origin != "unknown" else Decimal("1"))
    return {"origin": origin, "quantity": qty, "cost_basis_usd": basis,
            "verified": origin != "unknown", "market": market,
            "venue_order_id": venue_order_id}
