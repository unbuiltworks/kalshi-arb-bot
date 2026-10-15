"""One-window two-sided BTC15m probe. Experiment-gated, inert by default.

THE QUESTION IT EXISTS TO ANSWER. Can we acquire actual filled inventory --
not displayed prices -- whose settlement pays positive net on BOTH outcomes?
A book holding `a` UP and `b` DOWN for executed cost `C` pays `a` if UP
settles and `b` if DOWN settles, so that question is exactly `C < min(a, b)`
after fees. Unequal quantities are allowed: extra contracts on whichever side
is currently the MINIMUM lift `min(a, b)` for less than a dollar a share.

WHY A SEPARATE MODULE AND NOT A CHANGE TO THE ENGINE. `FaithfulExecutor`
mirrors L5: `FT.l5_native(row)` takes side, price and size from the L5 row and
there is no injection point. The ordinary path can HOLD and GATE this shape
but cannot generate it. This module generates two intents and nothing else --
it owns no transport, no ledger, no cancellation and no gate. Everything that
can move money or lose track of an order stays where it already is.

STRUCTURAL FACT THIS MODULE CANNOT CHANGE. Resting post-only on both sides,
the pair cost is forced by the spread: bid_UP + bid_DOWN = 1 - spread. On a
one-tick book that is 0.99, whose 1-cent gross edge is less than the 3.44-cent
worst-case taker fee at p~0.45. So a probe resting AT the touch is negative on
both outcomes by construction, and `MIN_EDGE` refuses it. Only resting behind
the touch clears the bar -- which is a bet on fill probability, and
`min_completion_probability()` below says how big a bet.

NOTHING HERE SUBMITS, CANCELS OR SETTLES ANYTHING.
"""
from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal as D, ROUND_CEILING, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence

from src import l5_faithful_translation as FT

EXPERIMENT_ENV = "L5_TS_EXPERIMENT_ID"
MARKET_ENV = "L5_TS_MARKET"

#: worst-case fee coefficient. The maker rebate is a credit we are never
#: entitled to assume, so the probe is priced as if both legs paid taker.
TAKER_THETA = D("0.0695")
#: the rebate, used ONLY to report the upside, never to justify an order
MAKER_THETA = D("-0.0125")

#: both legs post-only. A crossing order is a different experiment.
MODE = FT.MAKER

#: the pair must clear the worst-case fee on both legs, with margin
MIN_EDGE = D("0.04")
#: price sanity, same band the queue measurement uses
MIN_PRICE = D("0.05")
MAX_PRICE = D("0.95")

WINDOW_S = 900
#: the hard terminal deadline: nothing rests past this
CANCEL_MARGIN_S = 180.0
#: no leg may be PLACED after this
PLACE_OFFSET_S = 480.0
MIN_REST_S = 60.0
MAX_BOOK_AGE_S = 2.0


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def experiment_id() -> str:
    return str(os.getenv(EXPERIMENT_ENV, "")).strip()


def named_market() -> str:
    return str(os.getenv(MARKET_ENV, "")).strip()


def active(market: Optional[str] = None) -> bool:
    """False unless BOTH an experiment and a market are named AND it matches.

    INERT IS THE DEFAULT AND THERE IS NO PARTIAL ARMING. A missing experiment
    id, a missing market, or a market that is not the named one all return
    False, so an incomplete configuration cannot half-enable the probe.
    """
    exp, mkt = experiment_id(), named_market()
    if not exp or not mkt:
        return False
    if market is None:
        return True
    return str(market).strip() == mkt


# --------------------------------------------------------------- timing

def cancel_cutoff(t_end: float) -> float:
    """The hard terminal deadline. Nothing may rest past it."""
    return float(t_end) - CANCEL_MARGIN_S


def place_cutoff(t_start: float, t_end: float) -> float:
    return min(float(t_start) + PLACE_OFFSET_S,
               cancel_cutoff(t_end) - MIN_REST_S)


def may_place(now: float, t_start: float, t_end: float) -> Optional[str]:
    """None to allow, else the refusal reason."""
    if now < float(t_start):
        return "WINDOW_NOT_OPEN"
    if now >= place_cutoff(t_start, t_end):
        return "PAST_PLACE_CUTOFF"
    return None


# ----------------------------------------------------------------- fees

def fee(theta: D, qty: D, price: D) -> D:
    """Theta * C * p * (1-p), rounded AWAY from zero to the cent.

    The venue bills commission in whole cents -- 107 of our own orders show
    not one sub-cent value -- so a cost is rounded UP and a credit is rounded
    DOWN in magnitude. Both are the conservative direction.
    """
    q = _d(qty) or D(0)
    p = _d(price) or D(0)
    raw = theta * q * p * (D(1) - p)
    if raw > 0:
        return raw.quantize(D("0.01"), rounding=ROUND_CEILING)
    if raw < 0:                      # a credit: keep the SMALLER magnitude
        return -((-raw).quantize(D("0.01")))
    return D(0)


def worst_fee(qty: D, price: D) -> D:
    return fee(TAKER_THETA, qty, price)


# ------------------------------------------------------- the economics

def net_settlement(*, up_qty: Any, dn_qty: Any,
                   up_price: Any, dn_price: Any,
                   theta: D = TAKER_THETA) -> Dict[str, Any]:
    """Net P&L on BOTH outcomes for an arbitrary, possibly ASYMMETRIC book.

    payout is `up_qty` if UP settles and `dn_qty` if DOWN settles, against one
    total executed cost. `both_positive` is the probe's whole objective.
    """
    a = _d(up_qty) or D(0); b = _d(dn_qty) or D(0)
    pu = _d(up_price) or D(0); pd = _d(dn_price) or D(0)
    cost = a * pu + b * pd + fee(theta, a, pu) + fee(theta, b, pd)
    up_pnl = a - cost
    dn_pnl = b - cost
    return {"cost": cost, "up_pnl": up_pnl, "dn_pnl": dn_pnl,
            "min_payout": min(a, b),
            "worst_outcome": min(up_pnl, dn_pnl),
            "both_positive": bool(up_pnl > 0 and dn_pnl > 0),
            "basis": ("payout is up_qty if UP settles and dn_qty if DOWN "
                      "settles; both_positive iff cost < min(up_qty, dn_qty)")}


def expected_value(*, matched_edge: Any, residual_alpha: Any,
                   p_first: Any, p_complete: Any) -> Dict[str, Any]:
    """EV per intended contract, over the four fill outcomes.

    `residual_alpha` is the measured LOSS per unmatched contract and is passed
    as a POSITIVE number. It is not variance: in L5's own BTC15m record the
    unmatched side won 39.5% of windows, so an unmatched leg is adversely
    selected, and a probe resting BEHIND the touch is selected harder still --
    it fills precisely when the market moves against it.
    """
    e = _d(matched_edge) or D(0)
    al = _d(residual_alpha) or D(0)
    pa = _d(p_first) or D(0)
    pb = _d(p_complete) or D(0)
    both = pa * pb
    one = pa * (D(1) - pb) + (D(1) - pa) * pb
    ev = both * e - one * al
    return {"ev": ev, "p_both": both, "p_one_leg": one,
            "from_matched": both * e, "from_residual": -(one * al)}


def min_completion_probability(*, matched_edge: Any,
                               residual_alpha: Any) -> Optional[D]:
    """The symmetric fill probability at which the probe breaks even.

    With p on both legs, EV = p^2 * e - 2p(1-p) * alpha, so break-even is
    p = 2*alpha / (e + 2*alpha). None when no probability suffices.
    """
    e = _d(matched_edge) or D(0)
    al = _d(residual_alpha) or D(0)
    if al <= 0:
        return D(0)
    denom = e + D(2) * al
    if denom <= 0:
        return None
    p = (D(2) * al) / denom
    return p if p <= 1 else None


# ------------------------------------------------- the two intents

def build_orders(*, market: str, bid: Any, ask: Any,
                 bid_depth: Any, ask_depth: Any,
                 tick: Any, min_qty: Any,
                 depth_ticks: int,
                 cash_envelope: Any,
                 max_worst_case_loss: Any,
                 book_age_s: Any = None,
                 gap_suspected: bool = False) -> Dict[str, Any]:
    """Exactly two post-only intents, or a terminal refusal.

    NO FALLBACK. Every failure returns ok False and nothing is submitted. The
    module never degrades into a single leg, a crossing order, or the
    ordinary lambda-sized L5 decision -- a fallback would turn a measurement
    into a trade under an authorization granted for a measurement.
    """
    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason}, **extra)

    if not market or not active(market):
        return no("NOT_THE_AUTHORIZED_MARKET")
    if gap_suspected:
        return no("GAP_SUSPECTED")
    age = _d(book_age_s)
    if age is None or age > D(str(MAX_BOOK_AGE_S)):
        return no("BOOK_STALE")
    b = _d(bid); a = _d(ask)
    if b is None or a is None or b <= 0 or a <= 0:
        return no("BOOK_NOT_TWO_SIDED")
    if a <= b:
        return no("BOOK_CROSSED_OR_LOCKED")
    tk = _d(tick)
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    mq = _d(min_qty)
    if mq is None or mq <= 0:
        return no("MIN_QTY_UNKNOWN")
    if depth_ticks is None or int(depth_ticks) < 0:
        return no("DEPTH_TICKS_UNKNOWN")
    env = _d(cash_envelope); cap = _d(max_worst_case_loss)
    if env is None or env <= 0:
        return no("CASH_ENVELOPE_UNKNOWN")
    if cap is None or cap <= 0:
        return no("LOSS_CEILING_UNKNOWN")

    dsteps = D(int(depth_ticks)) * tk
    up_px = b - dsteps                     # rest BEHIND the UP bid
    dn_px = (D(1) - a) - dsteps            # and behind the DOWN bid
    for px in (up_px, dn_px):
        if px < MIN_PRICE or px > MAX_PRICE:
            return no("PRICE_OUT_OF_BAND", up_price=str(up_px),
                      dn_price=str(dn_px))
        if (px / tk) != (px / tk).to_integral_value():
            return no("PRICE_NOT_ON_TICK", price=str(px))

    pair = up_px + dn_px
    edge = D(1) - pair
    if edge < MIN_EDGE:
        # the structural refusal: at the touch on a one-tick book this is
        # 0.01 against a 0.0344 worst-case fee, i.e. negative both ways.
        return no("EDGE_BELOW_FEE_FLOOR", pair_cost=str(pair),
                  edge=str(edge), required=str(MIN_EDGE))

    # size: the WORST single-leg fill must stay inside the loss ceiling, and
    # the gross cash must stay inside the envelope. The dearer leg binds.
    dear = max(up_px, dn_px)
    by_loss = cap / dear
    by_cash = env / pair
    qty = min(by_loss, by_cash)
    # quantise DOWN to the venue grid, never up
    qty = (qty / mq).to_integral_value(rounding="ROUND_FLOOR") * mq
    # THEN SHRINK UNTIL THE FEE FITS TOO. Sizing on price alone overshoots,
    # because the worst single-leg fill costs qty*price PLUS its fee, and a
    # loss ceiling that the fee can step over is not a ceiling. One grid
    # step per iteration, bounded, and the refusal stands if nothing fits.
    for _ in range(64):
        if qty < mq:
            break
        if (qty * dear + worst_fee(qty, dear) <= cap
                and qty * pair + worst_fee(qty, up_px)
                + worst_fee(qty, dn_px) <= env):
            break
        qty -= mq
    if qty < mq:
        return no("NO_LEGAL_QUANTITY", by_loss=str(by_loss),
                  by_cash=str(by_cash))
    if qty * dear + worst_fee(qty, dear) > cap:
        return no("LOSS_CEILING_UNREACHABLE", qty=str(qty))
    bd = _d(bid_depth); ad = _d(ask_depth)
    if bd is None or ad is None:
        return no("NO_DISPLAYED_DEPTH")

    econ = net_settlement(up_qty=qty, dn_qty=qty,
                          up_price=up_px, dn_price=dn_px)
    if not econ["both_positive"]:
        return no("NOT_POSITIVE_ON_BOTH_OUTCOMES",
                  up_pnl=str(econ["up_pnl"]), dn_pnl=str(econ["dn_pnl"]))

    def leg(side: str, px: D) -> Dict[str, Any]:
        return {"side": side, "action": "BUY", "price": str(px),
                "qty": str(qty), "mode": MODE, "post_only": True,
                "worst_case_fee_usd": str(worst_fee(qty, px)),
                "alone_cost_usd": str(qty * px + worst_fee(qty, px))}

    return {"ok": True, "market": market,
            "legs": [leg("UP", up_px), leg("DOWN", dn_px)],
            "pair_cost": str(pair), "edge_per_contract": str(edge),
            "qty_per_leg": str(qty),
            "gross_cash_usd": str(econ["cost"]),
            "up_pnl": str(econ["up_pnl"]), "dn_pnl": str(econ["dn_pnl"]),
            "worst_single_leg_loss_usd": str(
                -(qty * dear + worst_fee(qty, dear))),
            "rebate_upside_usd": str(
                -(fee(MAKER_THETA, qty, up_px)
                  + fee(MAKER_THETA, qty, dn_px))),
            "labels": ("displayed prices are NOT fills. Both legs may rest "
                       "unfilled, one may fill alone, and either may fill "
                       "partially; the single-leg loss above is the bound "
                       "that matters.")}


def top_up(*, up_qty: Any, dn_qty: Any, up_price: Any, dn_price: Any,
           extra_side: str, extra_qty: Any, extra_price: Any
           ) -> Dict[str, Any]:
    """Would extra contracts on one side improve the WORST outcome?

    Allowed only when it does. Extra on the side that is already the maximum
    adds cost without lifting `min(a, b)`, which is strictly worse -- so this
    is the asymmetric case stated as a predicate rather than a hope.
    """
    a = _d(up_qty) or D(0); b = _d(dn_qty) or D(0)
    before = net_settlement(up_qty=a, dn_qty=b,
                            up_price=up_price, dn_price=dn_price)
    side = str(extra_side).upper()
    if side not in ("UP", "DOWN"):
        return {"ok": False, "reason": "EXTRA_SIDE_UNKNOWN"}
    eq = _d(extra_qty) or D(0)
    ep = _d(extra_price)
    if eq <= 0 or ep is None or ep <= 0 or ep >= D(1):
        return {"ok": False, "reason": "EXTRA_UNREADABLE"}
    # blended cost on the topped-up side
    if side == "UP":
        na = a + eq
        nc_u = (a * (_d(up_price) or D(0)) + eq * ep) / na if na else D(0)
        after = net_settlement(up_qty=na, dn_qty=b,
                               up_price=nc_u, dn_price=dn_price)
    else:
        nb = b + eq
        nc_d = (b * (_d(dn_price) or D(0)) + eq * ep) / nb if nb else D(0)
        after = net_settlement(up_qty=a, dn_qty=nb,
                               up_price=up_price, dn_price=nc_d)
    better = after["worst_outcome"] > before["worst_outcome"]
    return {"ok": bool(better),
            "reason": None if better else "TOP_UP_WORSENS_WORST_OUTCOME",
            "worst_before": before["worst_outcome"],
            "worst_after": after["worst_outcome"],
            "up_pnl_after": after["up_pnl"], "dn_pnl_after": after["dn_pnl"],
            "both_positive_after": after["both_positive"]}
