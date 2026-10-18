"""Refuse a pair acquisition whose margin cannot pay its own rounded fee.

READ-ONLY OVER STATE. This gate only ever REFUSES. It arms nothing, clears
no latch, and cannot raise any existing limit -- same contract as
`l5_live_validation_gates`.

WHY THIS EXISTS, in one line: the venue bills commission in WHOLE CENTS,
and at our authorized clip the rounding alone costs more than the edge the
strategy harvests.

THE MEASUREMENT THAT MOTIVATES IT. Two numbers from primary sources:

  * L5's whole edge, recomputed from `data/l5_master/btc15m_sequences.jsonl`
    with each window's cost calibrated to the corpus `cost` field so the
    baseline reproduces the record (net +$18,717.24 against the corpus's
    +$19,176.71, the $459 residual being the known redemption mismatch):
    **0.2935% of cost**, $6,376,853.08 deployed over 5,546 windows.
  * OUR OWN realized fees, from the 47 venue-confirmed legs in
    `data/l5/ops/cost_basis_inspect_20261016T1658Z.json`: **1.401% of cost**
    overall ($1.1400 on $81.3862); of the 38 fee-bearing legs the MEDIAN is
    **3.67%** and the worst is **7.22%** on a $0.2772 leg.

So our fee load runs 4.8x to 12.5x the entire edge. That is not a fidelity
gap and no amount of behavioural replication closes it.

VIABILITY IS NOT MONOTONE IN SIZE. It is a comb, and this corrects the
"minimum clip size" framing this module was first written with. At the
deployed <=0.96 discipline the gross-to-fee gap is 0.70pp of notional while
ONE CENT of rounding on a ~$1 leg is 1.0pp, so the cent -- not the strategy
-- decides the sign. Measured at pair 0.96: qty 0.51 nets +$0.0004 and
PASSES, qty 0.75/1.00/1.25/1.50/2.00/2.50 all net <= 0 and REFUSE, and qty
5 and 10 net +$0.0200 and +$0.0600. The authorized clip range sits inside
the refusing band.

THE MECHANISM IS ROUNDING, NOT THE RATE. The fee is
`Theta * C * p * (1-p)` rounded UP to the cent. On a 1.25-contract clip at
p=0.50 the true fee is 0.55 of a cent and the bill is a full cent -- an 83%
surcharge. At 10 contracts the same formula rounds 4.4 cents to 5, a 14%
surcharge. The penalty scales as 1/notional, which is exactly the shape of
the 1-cent quanta on our own fills.

Verified against those fills: `cpc-btc-updown-15m-2026-10-15-2230z|DOWN`,
cost 0.2921 in ONE fill, charged $0.0200, and
`0.0695 * 1 * 0.2921 * 0.7079 = 0.01437 -> ceil = 0.0200`. Exact.

AND THE REAL BILL IS WORSE THAN PER-ORDER. `cpcxrp-...-0330z|UP` carries
FIVE fills and $0.0500 -- a cent per fill. Cent rounding is super-additive
across fills, so `fills_per_leg` exists to charge that rather than argue it.
The default of 1 is the OPTIMISTIC case and is documented as such.

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

from decimal import Decimal as D, ROUND_CEILING
from typing import Any, Dict, Optional

#: PMUS taker Theta, the same conservative constant the deployed gates use.
#: The maker rate is NOT substituted: we are not guaranteed maker treatment
#: on a given fill, and the worst case is what a gate must price.
FEE_THETA_WORST = D("0.0695")

#: the venue bills in whole cents
CENT = D("0.01")

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
            extra_per_order_usd: Any = D("0")) -> Optional[D]:
    """`ceil_to_cent(theta * C * p * (1-p))` per FILL, then the allowance.

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
    per_fill = (raw / D(n)).quantize(CENT, rounding=ROUND_CEILING)
    return per_fill * D(n) + (_d(extra_per_order_usd) or D(0))


def evaluate(*, up_px: Any, dn_px: Any, qty: Any,
             theta: D = FEE_THETA_WORST,
             fills_per_leg: int = 1,
             extra_per_order_usd: Any = D("0"),
             required_ratio: D = DEFAULT_REQUIRED_RATIO) -> Dict[str, Any]:
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
                 extra_per_order_usd=extra_per_order_usd)
    fd = leg_fee(q, d, theta=theta, fills_per_leg=fills_per_leg,
                 extra_per_order_usd=extra_per_order_usd)
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
                    required_ratio=required_ratio)["ok"]:
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
