"""What a capital or inventory limit actually refused, priced.

WHY THIS EXISTS. The engine already records THAT a submission was refused:
`CASH_COMMITMENT`, `RESIDUAL_ABOVE_CAP`, `MAX_LIVE_ORDERS` and the exposure
refusals all surface in `L5_LIVE_DECISION`. What no record carries is what
the refusal COST, and without that a limit cannot be argued about. A limit
that refuses nothing profitable is free; a limit that refuses matched
surplus is paying for safety with throughput, and the owner is entitled to
see the price before being asked to change it.

FOUR NUMBERS PER REFUSAL, deliberately separate because they answer
different questions:

  expected_matched_surplus_usd   what the PAIR would net if BOTH legs filled
                                 -- the upside the limit declined
  additional_obligation_usd      what the cash gate would have charged, on
                                 its own convention: a dollar a share for a
                                 BUY_SHORT leg, q * px otherwise
  completion_distance_ticks      how far the complement's touch sits from a
                                 permissible completion price. EVIDENCE
                                 about reachability, NOT a probability
  downside_if_stranded_usd       the cash lost if this leg fills and never
                                 completes -- the reason the limit exists

NOTHING HERE IS A PROBABILITY, A FILL OR A REALISED FIGURE. The surplus is
conditional on two fills that the venue has not granted and that no book
snapshot can promise. `completion_distance_ticks` is a distance on a price
grid; the venue publishes no per-order queue position, so a reachable price
is not a fill. These are inputs to a decision about limits, not a forecast.
"""
from decimal import Decimal as D
from typing import Any, Dict, Optional

from src.l5_two_sided_probe import (MAKER_THETA, _d, completion_ceiling,
                                    fee, touch_own_price, wire_price)

#: the engine's own refusal strings, mapped to the limit the owner sets
BLOCKED_BY = {
    "CASH_COMMITMENT": "CASH_ENVELOPE",
    "RESIDUAL_CAP": "RESIDUAL_LIMIT",
    "RESIDUAL_ABOVE_CAP": "RESIDUAL_LIMIT",
    "MAX_LIVE_ORDERS": "ORDER_COUNT",
    "ACCOUNT_EXPOSURE_REFUSED": "EXPOSURE_CAP",
    "EXPOSURE_UNREADABLE": "EXPOSURE_CAP",
    "EXPOSURE_ENUMERATION_FAILED": "EXPOSURE_CAP",
}


def classify(reason: Any) -> Optional[str]:
    """Which owner-set limit a refusal belongs to, or None if it is not one.

    Matched on the PREFIX, because the engine's reasons carry their numbers
    (`CASH_COMMITMENT:3.1>3.0`). A reason this does not recognise returns
    None rather than a guess: an unclassified refusal is not evidence about
    a limit and must not be counted as one.
    """
    r = str(reason or "")
    for k, v in BLOCKED_BY.items():
        if r == k or r.startswith(k + ":"):
            return v
    return None


def capital_block_record(*, reason: Any, side: str, price: Any, qty: Any,
                         bid: Any, ask: Any, tick: Any = "0.01",
                         held_qty: Any = 0, held_cost_usd: Any = 0,
                         deficient_qty: Any = 0,
                         deficient_cost_usd: Any = 0,
                         residual_budget_usd: Any = None,
                         is_buy_short: Optional[bool] = None
                         ) -> Optional[Dict[str, Any]]:
    """Price one refusal, or None when it is not an owner-set limit.

    `side` is the leg that was refused and `price` its own-outcome price.
    `held_*` is what the market already holds. Unreadable inputs leave their
    field None rather than guessing: a missing number is reported as missing,
    because a limit argued from a fabricated figure is worse than one argued
    from none.
    """
    blocked = classify(reason)
    if blocked is None:
        return None
    q = _d(qty)
    px = _d(price)
    b, a = _d(bid), _d(ask)
    tk = _d(tick) or D("0.01")
    out: Dict[str, Any] = {
        "blocked_by": blocked, "reason": str(reason)[:120],
        "side": str(side), "own_price": (None if px is None else str(px)),
        "qty": (None if q is None else str(q)),
    }
    # THE CASH GATE'S OWN CONVENTION, not a restatement of it: a BUY_SHORT
    # leg is charged a dollar a share whatever its price, which is why a
    # dearer DOWN quote costs the envelope nothing extra.
    short = (str(side).upper() == "DOWN") if is_buy_short is None \
        else bool(is_buy_short)
    out["is_buy_short"] = bool(short)
    out["additional_obligation_usd"] = (
        None if q is None else str(q if short else
                                   (q * px if px is not None else q)))
    # the cash genuinely at risk if this leg fills alone and never completes
    out["downside_if_stranded_usd"] = (
        None if (q is None or px is None) else str(q * px))
    # what the PAIR would net if both legs filled, the complement taken at
    # the touch it would have to pay now
    comp_side = "DOWN" if str(side).upper() == "UP" else "UP"
    touch = (None if (b is None or a is None)
             else touch_own_price(comp_side, bid=b, ask=a))
    if q is not None and px is not None and touch is not None:
        pair = px + touch
        f = fee(MAKER_THETA, q, px) + fee(MAKER_THETA, q, touch)
        out["complement_touch_own_px"] = str(touch)
        out["pair_cost_if_both_fill"] = str(pair)
        out["expected_matched_surplus_usd"] = str(q * (D(1) - pair) - f)
    else:
        out["complement_touch_own_px"] = (None if touch is None
                                          else str(touch))
        out["pair_cost_if_both_fill"] = None
        out["expected_matched_surplus_usd"] = None
    # REACHABILITY EVIDENCE, not a probability. How far the complement's
    # touch sits from the dearest price a completion of this inventory would
    # be permitted to pay.
    ceil = None
    if _d(held_qty) and residual_budget_usd is not None:
        ceil = completion_ceiling(
            held_side=str(side), held_qty=held_qty,
            held_cost_usd=held_cost_usd, deficient_qty=deficient_qty,
            deficient_cost_usd=deficient_cost_usd,
            complete_qty=(q if q is not None else held_qty),
            residual_budget_usd=residual_budget_usd, tick=tk)
    out["completion_ceiling"] = (None if ceil is None else str(ceil))
    out["completion_distance_ticks"] = (
        None if (ceil is None or touch is None) else str((touch - ceil) / tk))
    out["wire_price"] = (None if px is None else str(wire_price(side, px)))
    out["labels"] = (
        "REPORTING ONLY. expected_matched_surplus_usd is conditional on BOTH "
        "legs filling and is not a forecast, a probability or a realised "
        "figure. completion_distance_ticks is a distance on the price grid: "
        "the venue publishes no per-order queue position, so a reachable "
        "price is not a fill. POSITIVE completion_distance_ticks means the "
        "complement's touch sits ABOVE the ceiling and no permissible "
        "completion is reachable there; NEGATIVE is headroom under it. "
        "additional_obligation_usd follows the cash gate's own convention, "
        "a dollar a share for a BUY_SHORT leg.")
    return out


# ================== HOW MUCH OF THE COMMITTED CASH IS ACTUALLY AT RISK
#
# THE QUESTION THIS ANSWERS, and it is the one the throughput argument turns
# on. The cash gate charges `cash_committed`, which is every unsettled
# fill's outlay. It does not distinguish a MATCHED pair from an UNMATCHED
# leg, and economically those are nothing alike:
#
#   a matched UP+DOWN pair redeems EXACTLY $1.00 whichever way the window
#   resolves, so its worst case is `1 - basis` -- a GAIN. It cannot lose.
#
#   an unmatched leg redeems $1.00 or $0.00, so its worst case is its whole
#   basis.
#
# Both are the same number to `cash_committed`, because that gate measures
# CASH OUT and the cash really has left the account until settlement. That
# is correct for a cash bound. It is the wrong shape for a WORST-CASE
# EXPOSURE bound, and when a $1 authorization meant as the latter is
# configured onto the former, one matched pair at a 0.95 basis consumes 95%
# of it while carrying no risk at all.
#
# MEASURED against the real executor: with $1 on the cash gate the engine
# places 2 orders and completes 1 pair, then refuses
# `CASH_COMMITMENT:1.3766>1.00`. With the same $1 on the WORST-CASE DEFICIT
# gate and the cash gate left generous, it completes FOUR pairs and the risk
# gate never binds -- because four matched pairs carry ~zero worst case.
#
# THIS FUNCTION CHANGES NO LIMIT AND NO BEHAVIOUR. It reports the split so
# the difference is visible in the record, because the choice between a cash
# bound and a risk bound is the owner's and should be made against numbers.


def matched_cash_split(*, positions: Any, leg_cost: Any,
                       up_key: str = "UP", down_key: str = "DOWN"
                       ) -> Dict[str, Any]:
    """Split committed cash into its risk-free and at-risk parts.

    `positions` and `leg_cost` are the store's own per-side mappings for ONE
    market. Returns a record, or `readable: False` with a reason -- never a
    guess, because an unreadable book must not be reported as risk-free.

    The split is exact and needs no probability:

        matched   = min(|up|, |down|)      pairs, worst case = 1 - basis
        residual  = |up - down|            one-sided, worst case = basis
    """
    def no(reason: str) -> Dict[str, Any]:
        return {"readable": False, "reason": reason}

    # AN ABSENT KEY MEANS FLAT; A PRESENT-BUT-UNREADABLE ONE MEANS UNKNOWN.
    # Collapsing those would let `{"UP": None}` be reported as a risk-free
    # book, which is the one answer this function must never give on a
    # book it cannot read.
    def read(src: Any, k: str) -> Any:
        m = src or {}
        if k not in m:
            return D(0)
        v = m.get(k)
        if v is None:
            return None
        return _d(v)

    try:
        up, dn = read(positions, up_key), read(positions, down_key)
        cu, cd = read(leg_cost, up_key), read(leg_cost, down_key)
    except BaseException:                                      # noqa: BLE001
        return no("POSITIONS_OR_COST_UNREADABLE")
    if up is None or dn is None or cu is None or cd is None:
        return no("POSITIONS_OR_COST_UNREADABLE")
    aup, adn = abs(up), abs(dn)
    if aup < 0 or adn < 0:
        return no("NEGATIVE_QUANTITY")
    committed = cu + cd
    matched = min(aup, adn)
    residual = abs(aup - adn)

    if matched > 0 and (aup == 0 or adn == 0):
        return no("MATCHED_WITHOUT_BOTH_SIDES")
    # per-share basis on each side, needed to price each part separately
    bu = (cu / aup) if aup > 0 else D(0)
    bd = (cd / adn) if adn > 0 else D(0)
    pair_basis = bu + bd if matched > 0 else D(0)
    matched_cash = matched * pair_basis
    residual_basis = bu if aup > adn else bd
    residual_cash = residual * residual_basis

    # the worst case of each part, which is what a RISK bound should charge
    matched_worst = matched * (D(1) - pair_basis)    # a gain, or a loss if
    #                                                 the pair cost over $1
    residual_worst = -residual_cash                  # the whole basis
    return {
        "readable": True,
        "committed_cash_usd": str(committed.quantize(D("0.0001"))),
        "matched_qty": str(matched),
        "matched_pair_basis": str(pair_basis.quantize(D("0.0001"))),
        "matched_cash_usd": str(matched_cash.quantize(D("0.0001"))),
        "matched_worst_case_usd": str(matched_worst.quantize(D("0.0001"))),
        "residual_qty": str(residual),
        "residual_basis": str(residual_basis.quantize(D("0.0001"))),
        "residual_cash_usd": str(residual_cash.quantize(D("0.0001"))),
        "residual_worst_case_usd": str(residual_worst.quantize(D("0.0001"))),
        # the two numbers the decision needs, side by side
        "cash_bound_charges_usd": str(committed.quantize(D("0.0001"))),
        "risk_bound_charges_usd": str(residual_cash.quantize(D("0.0001"))),
        "risk_free_cash_usd": str(matched_cash.quantize(D("0.0001"))),
        "labels": ("A matched pair redeems $1.00 either way, so its worst "
                   "case is a GAIN and a risk bound charges it nothing; the "
                   "cash gate charges its full outlay because the cash has "
                   "genuinely left the account until settlement. Both are "
                   "right for what they measure. Reported so the choice "
                   "between them is made against numbers. NOTHING HERE "
                   "RELEASES ANY RESERVATION OR CHANGES ANY LIMIT."),
    }
