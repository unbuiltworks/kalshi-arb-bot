"""SHADOW ONLY: the bounded complement-cross, exactly as pre-registered.

Spec: analysis/l5/cross_shadow/PREREGISTRATION.md, frozen at commit
135f5d79 (sha256 7e31c447eaa1028c546787b23bdc16a7925478d9d9e0e7d11635ebb7c
aea3dd0) BEFORE this file existed. Nothing here may relax a rule in that
document; where a rule cannot be evaluated the answer is UNKNOWN.

NO LIVE TAKER ORDER IS PLACED BY ANYTHING IN THIS MODULE. It is not
imported by any armed path, it opens no socket, and it computes
counterfactuals over decisions the engine already made.

FEES ARE CONFIRMED, and the rounding is the point at our size:

    docs.polymarket.us/fees, fetched 2026-09-13, effective 12 AM ET
    2026-07-01, exchange-wide:
      taker   Fee = 0.06 * C * p * (1-p),  max $1.50 at p = 0.50
      maker   same structure with -0.0125, max rebate -$0.31
      "All fees and rebates are rounded to the nearest $0.01 using
       banker's rounding (round half to even)."
      reduced taker tiers begin at $250,000 prior-calendar-month taker
      volume; we are orders of magnitude below the first tier, so the base
      0.06 applies and no tier is assumed.

At C = 1.72 and p = 0.50 the formula gives $0.0258 and the CHARGE IS $0.03.
The quantisation is larger than the ~0.5c price effect being measured, so
it is applied at the real clip size and never averaged away.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_HALF_EVEN
from typing import Any, Mapping, Optional

# --- confirmed fee schedule -------------------------------------------------
#: CORRECTED 2026-11-03 against the VENUE'S OWN RECORDS. Every retained
#: BTC15m market from 2026-09-30 to 2026-11-03 carries `feeCoefficient`
#: 0.0695 -- queried individually by slug, 11 dates spanning the claimed
#: 2026-10-19 change, with no step at it. The 0.06 here came from a
#: docs.polymarket.us/fees read on 2026-09-13, which predates the gateway's
#: market-retention floor (~2026-09-30) and therefore cannot be re-verified.
#:
#: DIRECTION OF THE ERROR: 0.06 UNDERSTATES the taker charge by ~16%, so
#: every crossing cost computed with it was optimistic. All of our own live
#: trade dates (2026-10-11 .. 2026-11-02) fall inside the verified span, so
#: one coefficient covers them and no per-date split is warranted.
TAKER_THETA = D("0.0695")
MAKER_THETA = D("0.0125")           # credited to us; a rebate, not a charge
TAKER_MAX = D("1.50")
MAKER_REBATE_MAX = D("0.31")
FEE_SOURCE = ("taker theta from the venue's own per-market feeCoefficient, "
              "gateway.polymarket.us/v1/market/slug/<slug>, verified constant at "
              "0.0695 across 2026-09-30..2026-11-03; maker theta from "
              "docs.polymarket.us/fees fetched 2026-09-13")
FEE_STATUS_CONFIRMED = "CONFIRMED"
REDUCED_TIER_FLOOR_USD = D("250000")

# --- arms, exactly as pre-registered ---------------------------------------
THRESHOLDS = (D("0.97"), D("0.98"), D("0.99"), D("1.00"))
TIMINGS = (0, 10, 30, 60)
MAX_QUOTE_AGE_S = 5.0
PAIR_CEILING = D("1.00")            # unchanged; no arm exists above it

# --- outcome labels --------------------------------------------------------
SUPPORTED = "SUPPORTED_COUNTERFACTUAL_CROSS"
REFUSED = "REFUSED_BY_A_GATE"
UNKNOWN_EXEC = "UNKNOWN_EXECUTABILITY"


def _cents(x: D) -> D:
    """Banker's rounding to the cent, as the venue states it."""
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_EVEN)


def taker_fee(contracts: D, price: D) -> D:
    """The charge, not the formula value. Rounded at the real clip size."""
    raw = TAKER_THETA * D(contracts) * D(price) * (D("1") - D(price))
    return min(_cents(raw), TAKER_MAX)


def maker_rebate(contracts: D, price: D) -> D:
    """What a maker fill WOULD have been credited -- so crossing can be
    charged for giving it up, which is a real cost of the cross."""
    raw = MAKER_THETA * D(contracts) * D(price) * (D("1") - D(price))
    return min(_cents(raw), MAKER_REBATE_MAX)


def fee_quantisation(contracts: D, price: D) -> dict:
    """The formula value beside the charge, because at our size they differ
    by a double-digit percentage and a report that shows only one of them
    is misleading."""
    raw = TAKER_THETA * D(contracts) * D(price) * (D("1") - D(price))
    charged = taker_fee(contracts, price)
    return {"formula_value": str(raw.quantize(D("0.000001"))),
            "charged_after_bankers_rounding": str(charged),
            "quantisation_premium": str(charged - raw.quantize(D("0.000001"))),
            "per_contract": str((charged / D(contracts)).quantize(D("0.0001"))
                                if D(contracts) > 0 else D("0"))}


def qualify(*, held_residual: D, model_relation: str, quote_age_s: Optional[float],
            depth: Optional[D], want_qty: D, pair_cost: Optional[D],
            threshold: D, risk_gates_pass: bool, horizon_pass: bool,
            evidence_ok: bool) -> tuple[str, str]:
    """The eight pre-registered conditions, in order, returning the FIRST
    thing that stopped it.

    Conditions 3, 4 and 8 return UNKNOWN_EXECUTABILITY rather than a
    refusal: "we could not tell" and "we would not have" are different
    facts and the pre-registration requires them reported separately.
    """
    if not evidence_ok:
        return UNKNOWN_EXEC, "malformed_or_missing_required_evidence"
    if abs(D(held_residual)) <= 0:
        return REFUSED, "no_held_residual"
    if model_relation != "complete":
        return REFUSED, "model_did_not_select_complete"
    if quote_age_s is None:
        return UNKNOWN_EXEC, "no_authenticated_quote_age"
    if quote_age_s > MAX_QUOTE_AGE_S:
        return REFUSED, f"quote_age_{quote_age_s}s_over_{MAX_QUOTE_AGE_S}s"
    if depth is None:
        return UNKNOWN_EXEC, "observed_ask_depth_not_recorded"
    if D(depth) < D(want_qty):
        return REFUSED, f"depth_{depth}_below_requested_{want_qty}"
    if pair_cost is None:
        return UNKNOWN_EXEC, "pair_cost_not_computable"
    if D(pair_cost) > D(threshold):
        return REFUSED, f"pair_{pair_cost}_over_threshold_{threshold}"
    if D(pair_cost) > PAIR_CEILING:
        return REFUSED, "pair_over_the_1_00_ceiling"
    if not risk_gates_pass:
        return REFUSED, "existing_risk_spend_or_daily_loss_gate"
    if not horizon_pass:
        return REFUSED, "settlement_horizon"
    return SUPPORTED, "all_eight_conditions_met"


def cross_economics(*, contracts: D, taker_price: D, maker_price: Optional[D],
                    held_avg_cost: D, winner: Optional[str],
                    held_side: str) -> dict:
    """One supported cross, decomposed as the pre-registration requires.

    A matched pair pays exactly $1, so the locked pair P&L is
    (1 - pair_cost) per contract and carries NO directional exposure. The
    avoided alternative is what that residual actually did at settlement,
    which is a venue fact where the market settled and UNKNOWN where it did
    not -- never zero.
    """
    q, px = D(contracts), D(taker_price)
    pair = D(held_avg_cost) + px
    fee = taker_fee(q, px)
    rebate_forgone = maker_rebate(q, D(maker_price)) if maker_price is not None else D("0")
    gross = q * (D("1") - pair)
    net = gross - fee - rebate_forgone
    residual_if_held = (None if winner is None else
                        q * ((D("1") - D(held_avg_cost))
                             if held_side == winner else -D(held_avg_cost)))
    return {
        "contracts": str(q),
        "taker_price": str(px),
        "maker_price_forgone": (str(maker_price) if maker_price is not None
                                else None),
        "taker_premium_paid": (str((px - D(maker_price)) * q)
                               if maker_price is not None else None),
        "pair_cost": str(pair.quantize(D("0.0001"))),
        "gross_locked_pair_pnl": str(gross.quantize(D("0.0001"))),
        "confirmed_taker_fee": str(fee),
        "maker_rebate_forgone": str(rebate_forgone),
        "net_locked_pair_pnl": str(net.quantize(D("0.0001"))),
        "fee_status": FEE_STATUS_CONFIRMED,
        "fee_source": FEE_SOURCE,
        "capital_required_usd": str((q * px).quantize(D("0.0001"))),
        "residual_dollars_eliminated": str((q * D(held_avg_cost)).quantize(
            D("0.0001"))),
        "settlement_pnl_of_that_residual_if_held": (
            str(residual_if_held.quantize(D("0.0001")))
            if residual_if_held is not None else "UNSETTLED"),
        "incremental_net_vs_holding": (
            str((net - residual_if_held).quantize(D("0.0001")))
            if residual_if_held is not None else "UNSETTLED"),
        "quantisation": fee_quantisation(q, px),
    }
