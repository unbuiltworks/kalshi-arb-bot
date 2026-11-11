"""GEMINI_FILL_QUALITY_V1 -- what a fill was WORTH, computed at the fill.

A first fill is not success. The probe that ran on 2026-09-21 recorded "FILL
NO 1 @ 0.02" and nothing about whether that fill could ever be completed at a
price that made the pair worth holding -- and it could not: the book was
crossed for the next six cycles and the window ended with a one-cent residual.
That outcome was knowable AT THE FILL and was not written down.

So after every fill this module computes, and the caller logs, the whole
economic picture at that instant:

    matched inventory                what is already paired
    residual inventory               what is directional, signed
    average cost per side            the basis on each leg
    effective matched pair cost      avg_yes + avg_no, below 1.00 is locked
    executable complement cost       what the NEEDED side costs RIGHT NOW,
                                     taken from the ask we would have to pay
    projected pair cost              basis + that complement, i.e. what the
                                     pair would cost if completed now
    residual dollars at risk         the cost basis actually exposed
    next desired L5 action           what the frozen controller wants next

NOTHING HERE DECIDES ANYTHING. It computes and returns; no order, no veto, no
threshold. A field that would gate a trade would be a new strategy parameter,
and none is authorized. It is instrumentation, and its purpose is that the
next probe can be judged on economics rather than on a fill count.

REBATE IS ABSENT ON PURPOSE. No field here nets a rebate into a cost, a pair
cost or a P&L. A trade is acceptable on pre-rebate economics or it is not
acceptable; the rebate is reported on its own line by the settlement audit.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src.gemini_execution import continuous_probe as CP

LABEL = "GEMINI_FILL_QUALITY_V1"

#: the lifecycle every fill is carried through. Nothing skips a stage, and
#: REALIZED is deliberately absent: the last stage this module knows about is
#: CASH_RECONCILED, because a number without an attributable cash movement is
#: not realized however settled it looks.
SUBMITTED, FILLED, SETTLED, CASH_RECONCILED = (
    "SUBMITTED", "FILLED", "SETTLED", "CASH_RECONCILED")
LIFECYCLE = (SUBMITTED, FILLED, SETTLED, CASH_RECONCILED)


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def needed_leg(inv: "CP.Inventory") -> Optional[str]:
    """Which leg would REDUCE the residual. None when flat."""
    res = inv.residual()
    if res == 0:
        return None
    return "NO" if res > 0 else "YES"


def executable_complement_cost(book: Mapping[str, Any],
                               leg: Optional[str]) -> Optional[D]:
    """What buying the complement costs RIGHT NOW, as a taker would pay.

    The ASK on the needed side, not the bid and not the mid. This is the
    honest "what would it cost to finish this pair at this instant" number: a
    maker quote might do better, and might never fill. Reporting the mid here
    would flatter every pair by half a spread.
    """
    if leg is None:
        return None
    side = "yes" if leg == "YES" else "no"
    q = (book or {}).get(side) or {}
    return _d(q.get("ask"))


def residual_dollars_at_risk(inv: "CP.Inventory") -> D:
    """Cost basis actually exposed to direction, not a notional.

    The residual's own average cost, not the contract's $1 face: a residual of
    one NO bought at 0.02 risks two cents, and calling it a dollar would
    overstate the exposure by fifty times.
    """
    res = inv.residual()
    if res == 0:
        return D("0")
    avg = inv.avg_yes() if res > 0 else inv.avg_no()
    return abs(res) * (avg if avg is not None else D("1"))


def quality(inv: "CP.Inventory", book: Mapping[str, Any], *,
            tte_s: Any = None, stage: str = FILLED,
            next_action: Optional[str] = None,
            fee_paid: Any = None) -> Dict[str, Any]:
    """The whole economic picture at one fill. Computes; decides nothing."""
    if stage not in LIFECYCLE:
        raise ValueError("unknown lifecycle stage %r" % (stage,))
    need = needed_leg(inv)
    comp = executable_complement_cost(book, need)
    basis = inv.avg_yes() if need == "NO" else inv.avg_no()
    pair_now = (basis + comp) if (basis is not None and comp is not None) \
        else None
    fee = _d(fee_paid)
    return {
        "label": LABEL, "stage": stage, "tte_s": tte_s,
        "matched_qty": str(inv.matched()),
        "residual_qty_signed": str(inv.residual()),
        "inventory_imbalance": str(inv.imbalance()),
        "avg_yes_cost": None if inv.avg_yes() is None else str(inv.avg_yes()),
        "avg_no_cost": None if inv.avg_no() is None else str(inv.avg_no()),
        "effective_matched_pair_cost": (
            None if inv.effective_matched_pair_cost() is None
            else str(inv.effective_matched_pair_cost())),
        "needed_leg": need,
        "executable_complement_cost": None if comp is None else str(comp),
        "projected_pair_cost_if_completed_now": (
            None if pair_now is None else str(pair_now)),
        "pair_locked_below_1": (None if pair_now is None
                                else bool(pair_now < D("1"))),
        "residual_dollars_at_risk": str(residual_dollars_at_risk(inv)),
        "total_spend_usd": str(inv.spend()),
        "next_desired_l5_action": next_action,
        "fee_paid_this_fill": None if fee is None else str(fee),
        "fee_source": ("venue /v1/mytrades fee_amount" if fee is not None
                       else "NOT YET RECONCILED"),
        "labels": (
            "PRE-REBATE. No rebate is netted into any figure here; a trade is "
            "acceptable on these numbers or it is not. projected pair cost "
            "uses the ASK we would have to pay, never the mid. Nothing here "
            "is REALIZED: only CASH_RECONCILED with an attributable cash "
            "movement is."),
    }


def cap_counterfactual(inv: "CP.Inventory", offered_leg: str, price: Any, *,
                       action_size: Any = None) -> Dict[str, Any]:
    """Would the envelope change what the L5 controller wants to do next?

    CAP4 and the per-leg caps are UNTESTED AT RELEVANT INVENTORY DEPTH: no
    window in 151 shadow windows reached the four net same-side fills
    MAX_IMBALANCE needs, so zero observed blocks carries no information. The
    only way that changes is to record, from real probe evidence, every
    instance where the bound WOULD have altered the desired action -- without
    loosening the bound to manufacture one.

    Returns a record whether or not it binds, so the denominator is visible.
    """
    want = CP.classify(CP.LEG_TO_OUTCOME[offered_leg], inv)
    breach = CP.would_breach(offered_leg, price, inv, action_size=action_size)
    b = CP.scaled_bounds(action_size)
    return {
        "label": LABEL, "desired_action": want, "offered_leg": offered_leg,
        "price": str(price),
        "would_envelope_change_it": breach is not None,
        "which_bound": (None if breach is None else
                        ("MAX_IMBALANCE" if "imbalance" in breach
                         else "MAX_LEG_OR_SPEND")),
        "why": breach,
        "imbalance_now": str(inv.imbalance()),
        "max_imbalance": str(b["max_imbalance"]),
        "headroom_units": str(b["max_imbalance"] - inv.imbalance()),
        "spend_now": str(inv.spend()),
        "max_total_spend": str(b["max_total_spend"]),
        #: THESE ARE THE CONTROLLER'S INTERNAL BOUNDS, NOT THE AUTHORIZED
        #: DOLLARS. continuous_probe denominates in 0.01 units and scales by
        #: k = q/0.01, so at q=1 its spend bound reads 15 -- while the
        #: AUTHORIZED ceiling is live_envelope's $4.00, enforced separately
        #: and binding far tighter. Naming both stops the looser number from
        #: being read as the limit.
        "bounds_are": ("continuous_probe internal, q-scaled; the AUTHORIZED "
                       "limits are live_envelope LIVE_Q1_BTC15M_CAP4: $4.00 "
                       "committed, $4.00 residual, 4 units, 2 working orders, "
                       "12 actions, 1 market"),
        "labels": ("EVIDENCE ONLY. The bound is NOT relaxed by this record "
                   "and no action changes because of it. It exists so that "
                   "'CAP4 never binds' can one day be replaced by a measured "
                   "statement instead of an unreachable zero."),
    }


def settlement_economics(*, buy_spend: Any, settlement_payout: Any,
                         sell_proceeds: Any = 0, fees_actual: Any = 0,
                         rebates_paid: Any = 0,
                         cash_out_stop_measure: Any = None) -> Dict[str, Any]:
    """The economic result, beside the stop measure -- never replacing it.

    THE STOP AND THE RESULT ARE DIFFERENT QUESTIONS, and conflating them cost
    an authorized cohort four of its remaining windows. `window_loss_usd`
    counts cash OUT from confirmed fills with no settlement credit; for a
    strategy that buys both legs and is paid on one, that accumulates roughly
    half of turnover as "loss" whether the cohort is making money or losing
    it. It stopped COHORT-1 at 10.63 of a 10.00 cap while the four recorded
    windows settled net +1.14.

    So this computes the economic answer SEPARATELY. It does not feed the
    stop, cannot relax it, and the stop keeps firing on its own conservative
    measure.

    TWO TOTALS, AND THE PRE-REBATE ONE IS THE ONE THAT SELECTS. A rebate
    cannot make an unprofitable configuration selectable, so
    `net_pre_rebate_usd` is what a probe is judged on and `rebates_paid_usd`
    is reported on its own line. Only rebates the venue has marked PAID
    belong here; an accrued or estimated rebate is not one.
    """
    spend = D(str(buy_spend or 0))
    payout = D(str(settlement_payout or 0))
    sells = D(str(sell_proceeds or 0))
    fees = D(str(fees_actual or 0))
    reb = D(str(rebates_paid or 0))
    pre = payout + sells - spend - fees
    return {
        "label": LABEL,
        "buy_spend_usd": str(spend),
        "settlement_payout_usd": str(payout),
        "sell_proceeds_usd": str(sells),
        "fees_actual_usd": str(fees),
        "net_pre_rebate_usd": str(pre),
        "rebates_paid_usd": str(reb),
        "net_with_rebate_usd": str(pre + reb),
        "cash_out_stop_measure_usd": (None if cash_out_stop_measure is None
                                      else str(cash_out_stop_measure)),
        "stop_measure_vs_economics_usd": (
            None if cash_out_stop_measure is None
            else str(D(str(cash_out_stop_measure)) + pre)),
        "selection_basis": "net_pre_rebate_usd",
        "labels": (
            "net_pre_rebate_usd is what the configuration is judged on; "
            "rebates_paid_usd is an independently reported bonus and is "
            "PAID-only, never accrued or estimated. fees_actual_usd is the "
            "venue's own fee_amount from /v1/mytrades, never the published "
            "formula. Nothing here is REALIZED until an attributable cash "
            "movement is read back; settlement_payout is settlement-implied "
            "until then."),
    }
