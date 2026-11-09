"""Settlement-backed realized economics. Read-only; no synthetic residual prices.

All records must come from venue-confirmed fills, terminal inventory and
settlement. Missing evidence refuses the market rather than filling gaps.
"""
from decimal import Decimal, InvalidOperation

D = Decimal


def _decimal(value):
    try:
        v = D(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError("invalid decimal")
    if not v.is_finite():
        raise ValueError("non-finite decimal")
    return v


def reconcile_settled_market(*, fills, settlement_side, redemption,
                             expected_up_qty, expected_down_qty,
                             unresolved_orders=0, cost_basis_attested=False):
    """Compute settled net from executed cash flows and venue redemption.

    Each fill: side UP/DOWN, action BUY/SELL, quantity, price, fee,
    rebate (actual credited, not estimated). All are required.
    BUY adds inventory and spends cash; SELL reduces inventory and
    receives cash. Reconcile to independently observed terminal quantities.
    Redemption must be a venue-confirmed cash amount.
    """
    if not cost_basis_attested or unresolved_orders:
        return {"eligible": False, "reason": "UNATTESTED_OR_UNRESOLVED"}
    if settlement_side not in ("UP", "DOWN"):
        return {"eligible": False, "reason": "SETTLEMENT_UNCONFIRMED"}
    try:
        qty = {"UP": D("0"), "DOWN": D("0")}
        cash = D("0")
        bought = {"UP": D("0"), "DOWN": D("0")}
        for fill in fills:
            side, action = fill["side"], fill["action"]
            if side not in qty or action not in ("BUY", "SELL"):
                raise ValueError("invalid side or action")
            q, price = _decimal(fill["quantity"]), _decimal(fill["price"])
            fee, rebate = _decimal(fill["fee"]), _decimal(fill["rebate"])
            if q <= 0 or not D("0") <= price <= D("1") or fee < 0 or rebate < 0:
                raise ValueError("invalid fill economics")
            if action == "BUY":
                qty[side] += q
                bought[side] += q
                cash -= q * price
            else:
                qty[side] -= q
                cash += q * price
            if qty[side] < 0:
                raise ValueError("uncovered sale")
            cash += rebate - fee
        if qty["UP"] != _decimal(expected_up_qty) or qty["DOWN"] != _decimal(expected_down_qty):
            return {"eligible": False, "reason": "INVENTORY_MISMATCH"}
        paid = _decimal(redemption)
        if paid < 0:
            raise ValueError("negative redemption")
        # Do not trust settlement-side alone to manufacture a payout.
        if paid != qty[settlement_side]:
            return {"eligible": False, "reason": "REDEMPTION_MISMATCH"}
        net = cash + paid
        return {"eligible": True, "settled_net": str(net),
                "cash_before_redemption": str(cash),
                "redemption": str(paid),
                "terminal_up": str(qty["UP"]), "terminal_down": str(qty["DOWN"]),
                "had_up_fill": bought["UP"] > 0,
                "had_down_fill": bought["DOWN"] > 0,
                "stranded": (qty["UP"] > 0) != (qty["DOWN"] > 0)}
    except (ValueError, KeyError):
        return {"eligible": False, "reason": "INVALID_OR_INCOMPLETE_FILL"}


def aggregate_confirmed_markets(markets):
    """Never divide by all submissions or assume rejected markets are zero."""
    records = list(markets)
    if not records or any(not row.get("eligible") for row in records):
        return {"eligible": False, "reason": "INCOMPLETE_SETTLEMENT_POPULATION"}
    total = sum((_decimal(row["settled_net"]) for row in records), D("0"))
    return {"eligible": True, "market_count": len(records),
            "settled_net": str(total), "net_per_market": str(total / len(records)),
            "stranded_market_count": sum(bool(row["stranded"]) for row in records)}
