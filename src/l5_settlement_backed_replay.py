"""Settlement-backed realized economics. Read-only; no synthetic residual prices.

All records must come from venue-confirmed fills, terminal inventory and
settlement. Missing evidence refuses the market rather than filling gaps.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

D = Decimal

#: Money is reported at a FIXED scale, and that is a correctness property
#: rather than cosmetics. `str(Decimal)` preserves the scale the arithmetic
#: happened to produce, so the SAME economics reported a different string
#: depending on how the inputs were spelled: a fee of "0.01" gave
#: "-0.073" where "0.0100" would have given "-0.0730". Two of this
#: module's own tests asserted the four-place form and three asserted the
#: raw form, which is how the inconsistency surfaced -- CI run
#: 42669331096.
#:
#: Four places is the finest meaningful unit here: prices are quoted in
#: cents and quantities to a hundredth, so a price x quantity product needs
#: four. Internal arithmetic stays EXACT; only the reported strings are
#: quantized, so no intermediate precision is lost.
MONEY_PLACES = D("0.0001")


def _money(value):
    """A money Decimal as a fixed-scale string. Banker's rounding."""
    return str(D(value).quantize(MONEY_PLACES, rounding=ROUND_HALF_EVEN))


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
        return {"eligible": True, "settled_net": _money(net),
                "cash_before_redemption": _money(cash),
                "redemption": _money(paid),
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
            "settled_net": _money(total),
            # A DIVISION NEEDS THE QUANTIZE MOST: total/len is routinely
            # non-terminating, and str() of that is 28 significant digits.
            "net_per_market": _money(total / len(records)),
            "stranded_market_count": sum(bool(row["stranded"]) for row in records)}


def compare_settled_strategies(*, baseline, candidate, identical_market_ids):
    """Compare *confirmed* settled outcomes for the same market population.

    Refuse simulated candidate fills or missing settlements. The no-trade
    comparator is zero realized net; no counterfactual fill is manufactured.
    Each mapping is market_id -> reconcile_settled_market result.
    """
    ids = list(identical_market_ids)
    if not ids or len(ids) != len(set(ids)):
        return {"eligible": False, "reason": "INVALID_MARKET_POPULATION"}
    if set(baseline) != set(ids) or set(candidate) != set(ids):
        return {"eligible": False, "reason": "MARKET_POPULATION_MISMATCH"}
    b = aggregate_confirmed_markets([baseline[i] for i in ids])
    c = aggregate_confirmed_markets([candidate[i] for i in ids])
    if not b["eligible"] or not c["eligible"]:
        return {"eligible": False, "reason": "UNCONFIRMED_SETTLEMENT"}
    bnet, cnet = _decimal(b["settled_net"]), _decimal(c["settled_net"])
    return {"eligible": True, "market_count": len(ids),
            "baseline_settled_net": _money(bnet),
            "candidate_settled_net": _money(cnet),
            "delta_settled_net": _money(cnet - bnet),
            "no_trade_settled_net": _money(D("0")),
            "candidate_beats_no_trade": cnet > 0,
            "candidate_beats_baseline": cnet > bnet}
