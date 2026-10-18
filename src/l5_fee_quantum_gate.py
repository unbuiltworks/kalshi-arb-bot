"""Refuse a pair acquisition whose margin cannot pay its own rounded fee.

READ-ONLY OVER STATE. This gate only ever REFUSES. It arms nothing, clears
no latch, and cannot raise any existing limit -- same contract as
`l5_live_validation_gates`.

WHY THIS EXISTS, in one line: the venue bills commission in WHOLE CENTS,
and at our authorized clip the rounding alone costs more than the edge the
strategy harvests.

THE MEASUREMENT THAT MOTIVATES IT, CORRECTED 2026-10-18 against the live
cost-basis audit. An earlier version of this docstring said our fee load was
"1.401% of cost, 4.8x L5's entire edge" and generalised it to the account.
THAT WAS A SELECTED SUBSET. The 47 legs it measured are the cost-basis
REPAIR population -- legs whose basis was unknown -- and they are not the
account. Across the account's whole venue history the audit reports 1,911
fill rows with:

    commission_signs: negative_a_credited_rebate 831, zero 975,
                      positive_a_charge 105, unreadable 0
    commission_sum_usd: -5.8800        <- a NET CREDIT
    rebates_ever_credited: true

So 94.5% of our fills paid nothing or were CREDITED, and the account is net
credited $5.88. Within the crypto 15m lane, however, fees are real: the 47
established legs carry $1.1400 on $50.6148 of notional, 2.2523%.

THE RATE AND THE ROUNDING ARE NOW IDENTIFIED, not assumed. On the 28
fee-bearing SINGLE-FILL legs -- quantity and price read off the venue's own
rows -- `round_half_up(0.0695 * q * p * (1-p))` matches the charged
commission 28 times out of 28, with ROUND_CEILING matching 17 and
ROUND_FLOOR 11. Every fee-bearing crypto leg is therefore TAKER-priced.

MAKER TREATMENT IS REAL AND SEPARATE. The 4 zero-fee and 2 credited legs in
that same lane are NOT explained by rounding: their raw fees are $0.0057 to
$0.0174, which round to a cent, and nothing was charged. Maker status is
never inferred here from a low fee -- the venue states it per fill as
`isAggressor`, and `l5_cost_basis_audit._exec_objects` already selects our
own execution object with it.

WHAT THIS COSTS AT THE PAIR LEVEL. At the deployed <= 0.96 discipline gross
is 4.00% of qty and the taker fee 3.3013%, leaving 0.70pp. At pair 0.97 --
which the shadow quoted in all three recorded windows -- gross 3.00% is
below the 3.29% fee, so no size that pays a fee is positive.

WHAT THE GATE ASKS. A pair of one long and one short share pays exactly
$1.00 at settlement, so the gross margin of `qty` pairs bought at a combined
`pair_cost` is `(1 - pair_cost) * qty`. The gate refuses unless that gross
exceeds the rounded fee both legs will actually pay, by `required_ratio`.

WHAT IT DOES NOT DO. It does not net a maker rebate: a credit we are not
guaranteed cannot pay for a fee we are. It does not model adverse price
movement, single-leg fills, or settlement risk -- it is a NECESSARY
condition, never a sufficient one. A pair that passes is not thereby
profitable; a pair that fails cannot be.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Dict, Optional

#: PMUS taker Theta, the same conservative constant the deployed gates use.
#: The maker rate is NOT substituted: we are not guaranteed maker treatment
#: on a given fill, and the worst case is what a gate must price.
FEE_THETA_WORST = D("0.0695")

#: the venue bills in whole cents
CENT = D("0.01")

#: HOW THE VENUE ROUNDS, IDENTIFIED RATHER THAN ASSUMED. Against the 28
#: fee-bearing SINGLE-FILL legs in the 2026-10-16 cost-basis audit --
#: quantity and price read off the venue's own rows, not assumed --
#: `round_half_up(0.0695 * q * p * (1-p))` matches the charged commission on
#: 28 of 28 with no exception. ROUND_CEILING matches only 17 and ROUND_FLOOR
#: 11. The first version of this module assumed CEILING, which overstates
#: the fee by up to a cent per leg; at our clip that is the whole margin, so
#: the assumption was not harmless.
#:
#: CEILING REMAINS AVAILABLE as the worst case, because a safety bound may
#: legitimately want it. The DEFAULT is the measured behaviour.
ROUNDING_IDENTIFIED = ROUND_HALF_UP
ROUNDING_WORST_CASE = ROUND_CEILING

#: gross margin must exceed the rounded fee by at least this factor. 1 means
#: "strictly better than break-even after fees" and is the weakest defensible
#: setting. IT IS NOT A FITTED VALUE -- no threshold here was chosen by
#: scanning outcomes, and raising it is an owner risk preference, not a
#: discovery.
DEFAULT_REQUIRED_RATIO = D("1")


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    if isinstance(v, D):
        return v
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def leg_fee(qty: Any, price: Any, *,
            theta: D = FEE_THETA_WORST,
            fills_per_leg: int = 1,
            extra_per_order_usd: Any = D("0"),
            rounding: str = ROUNDING_IDENTIFIED) -> Optional[D]:
    """`round(theta * C * p * (1-p))` to the cent per FILL, then the allowance.

    Returns None when the price is unreadable -- the caller must treat that
    as a refusal, never as a zero fee.
    """
    q, p = _d(qty), _d(price)
    if q is None or p is None or q <= 0 or p < 0 or p > 1:
        return None
    n = max(1, int(fills_per_leg))
    raw = theta * q * p * (D(1) - p)
    if raw <= 0:
        # a zero-notional leg still pays nothing, but the allowance stands
        return (_d(extra_per_order_usd) or D(0))
    # charged per FILL, because cent rounding does not pool across fills
    per_fill = (raw / D(n)).quantize(CENT, rounding=rounding)
    return per_fill * D(n) + (_d(extra_per_order_usd) or D(0))


def evaluate(*, up_px: Any, dn_px: Any, qty: Any,
             theta: D = FEE_THETA_WORST,
             fills_per_leg: int = 1,
             extra_per_order_usd: Any = D("0"),
             required_ratio: D = DEFAULT_REQUIRED_RATIO,
             rounding: str = ROUNDING_IDENTIFIED) -> Dict[str, Any]:
    """Necessary-condition test on one two-legged pair acquisition.

    `ok` False means REFUSE. An unreadable input is a refusal, never a pass.
    """
    u, d, q = _d(up_px), _d(dn_px), _d(qty)
    if u is None or d is None or q is None:
        return _refuse("PRICE_OR_QTY_UNREADABLE", None, None, None, None, q)
    if q <= 0:
        return _refuse("NON_POSITIVE_QTY", None, None, None, None, q)
    pair_cost = u + d
    gross = (D(1) - pair_cost) * q
    fu = leg_fee(q, u, theta=theta, fills_per_leg=fills_per_leg,
                 extra_per_order_usd=extra_per_order_usd, rounding=rounding)
    fd = leg_fee(q, d, theta=theta, fills_per_leg=fills_per_leg,
                 extra_per_order_usd=extra_per_order_usd, rounding=rounding)
    if fu is None or fd is None:
        return _refuse("FEE_UNCOMPUTABLE", pair_cost, gross, None, None, q)
    fee = fu + fd
    net = gross - fee
    if pair_cost >= D(1):
        return _refuse("PAIR_COST_AT_OR_ABOVE_PAR", pair_cost, gross, fee,
                       net, q)
    if gross <= fee * required_ratio:
        return _refuse("MARGIN_BELOW_FEE_QUANTUM", pair_cost, gross, fee,
                       net, q)
    return {"ok": True, "refusal": None, "pair_cost": str(pair_cost),
            "gross_usd": str(gross), "fee_usd": str(fee), "net_usd": str(net),
            "qty": str(q), "required_ratio": str(required_ratio),
            "fills_per_leg": int(max(1, int(fills_per_leg))),
            "basis": _BASIS, "labels": _LABELS}


def breakeven_qty(*, up_px: Any, dn_px: Any,
                  theta: D = FEE_THETA_WORST,
                  fills_per_leg: int = 1,
                  extra_per_order_usd: Any = D("0"),
                  required_ratio: D = DEFAULT_REQUIRED_RATIO,
                  rounding: str = ROUNDING_IDENTIFIED,
                  max_qty: Any = D("100"),
                  step: Any = D("0.01")) -> Optional[D]:
    """Smallest `qty` on the venue's increment that passes, or None.

    NOT A FLOOR. Passing is not monotone in qty -- see the comb in the
    module docstring -- so a larger qty may well refuse. At pair 0.96 this
    returns 0.51 while 1.25 refuses. Callers must evaluate the qty they
    actually intend rather than assume anything above this passes.

    Walks the increment rather than solving, because the fee is a STEP
    function of qty: a closed form would misreport the cent boundaries that
    are the whole point of this module.
    """
    q = _d(step) or CENT
    lim = _d(max_qty) or D("100")
    inc = _d(step) or CENT
    while q <= lim:
        if evaluate(up_px=up_px, dn_px=dn_px, qty=q, theta=theta,
                    fills_per_leg=fills_per_leg,
                    extra_per_order_usd=extra_per_order_usd,
                    required_ratio=required_ratio, rounding=rounding)["ok"]:
            return q
        q += inc
    return None


_BASIS = ("a pair of one long and one short share pays exactly $1.00 at "
          "settlement, so gross = (1 - pair_cost) * qty. The fee is the "
          "TAKER Theta rounded UP to the cent per FILL on BOTH legs, with "
          "no maker rebate netted.")

_LABELS = ("NECESSARY CONDITION ONLY, and refuse-only. Passing does not "
           "make a pair profitable: adverse movement, a single-leg fill and "
           "settlement risk are all outside this test. Failing means the "
           "pair cannot pay its own rounded fee and no price path saves it.")


def _refuse(why: str, pair_cost, gross, fee, net, qty) -> Dict[str, Any]:
    return {"ok": False, "refusal": why,
            "pair_cost": None if pair_cost is None else str(pair_cost),
            "gross_usd": None if gross is None else str(gross),
            "fee_usd": None if fee is None else str(fee),
            "net_usd": None if net is None else str(net),
            "qty": None if qty is None else str(qty),
            "basis": _BASIS, "labels": _LABELS}
