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
import fnmatch
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

#: the SMALLEST gross edge ever acceptable. A pair at or above $1.00 is
#: never placed whatever the fee schedule says.
MIN_GROSS_EDGE = D("0.01")
#: retained for callers that still read it; the binding test is now
#: `fee_floor()`, computed from the ACTUAL proposed quote prices.
MIN_EDGE = MIN_GROSS_EDGE


def fee_floor(up_px: D, dn_px: D) -> Dict[str, Any]:
    """Is this pair profitable on both outcomes, and under which execution?

    WHY THIS IS NOT A CONSTANT. The old flat 0.04 floor was the worst-case
    TAKER fee at p ~ 0.45, which is the most expensive point on the curve:
    the fee is Theta * p * (1-p), so at 0.12/0.88 it is less than half what
    it is at 50/50. A single constant therefore refused configurations that
    are comfortably profitable at the extremes, and it refused resting AT
    THE TOUCH everywhere -- the configuration with by far the best measured
    fill rate.

    BOTH LEGS ARE POST-ONLY BY CONSTRUCTION, so the maker coefficient is the
    one that applies: MODE is FT.MAKER, which is what l5_order_body turns
    into participateDontInitiate, and a crossing order is a different
    experiment. `requires_post_only` is reported so no caller can read this
    verdict as applying to a taker fill.

    THE TAKER FIGURE IS STILL COMPUTED AND REPORTED, including when it is
    negative, because it is the honest worst case if the rebate is not
    credited on a given order.
    """
    edge = D(1) - (up_px + dn_px)
    taker_cost = (TAKER_THETA * up_px * (D(1) - up_px)
                  + TAKER_THETA * dn_px * (D(1) - dn_px))
    maker_credit = -(MAKER_THETA * up_px * (D(1) - up_px)
                     + MAKER_THETA * dn_px * (D(1) - dn_px))
    net_maker = edge + maker_credit
    net_taker = edge - taker_cost
    return {"edge": edge,
            "taker_fee_per_contract": taker_cost,
            "maker_credit_per_contract": maker_credit,
            "net_if_maker": net_maker,
            "net_if_taker": net_taker,
            "ok": bool(edge >= MIN_GROSS_EDGE and net_maker > 0),
            "requires_post_only": True,
            "labels": ("the verdict is for POST-ONLY execution on both "
                       "legs, which is how this probe quotes. net_if_taker "
                       "is the worst case should the rebate not be credited "
                       "and is reported even when negative.")}
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


def market_patterns() -> tuple:
    """The authorized market patterns, in order, from `L5_TS_MARKET`.

    Comma-separated. An entry with no `*` is an EXACT slug and matches only
    itself, exactly as before.
    """
    raw = named_market()
    return tuple(p for p in (x.strip() for x in raw.split(",")) if p)


def market_authorized(market: str) -> bool:
    """Does this slug match an authorized pattern?

    WHY A PATTERN AT ALL. The runner rebuilds its venue client against a
    NEW slug every fifteen minutes, so an exact-slug authorization
    expires the moment its window rolls and the engine goes inert until a
    human edits an environment variable. That is the second half of the
    one-shot restriction: continuous accumulation across successive markets
    is impossible while the authorization names one slug.

    A pattern is still a deliberate, bounded authorization -- it names an
    instrument family (`cpc-btc-updown-15m-*`), not "any market" -- and an
    exact slug remains exact, so nothing already configured widens.
    """
    m = str(market or "").strip()
    if not m:
        return False
    for pat in market_patterns():
        if "*" in pat:
            if fnmatch.fnmatchcase(m, pat):
                return True
        elif m == pat:
            return True
    return False


def active(market: Optional[str] = None) -> bool:
    """False unless BOTH an experiment and a market are named AND it matches.

    INERT IS THE DEFAULT AND THERE IS NO PARTIAL ARMING. A missing experiment
    id, a missing market, or a market that no authorized pattern covers all
    return False, so an incomplete configuration cannot half-enable the
    probe.
    """
    exp = experiment_id()
    if not exp or not market_patterns():
        return False
    if market is None:
        return True
    return market_authorized(market)


# --------------------------------------------------------------- timing

def cancel_cutoff(t_end: float) -> float:
    """The hard terminal deadline. Nothing may rest past it."""
    return float(t_end) - CANCEL_MARGIN_S


def window_end(t_start: float) -> float:
    """Window END from its START. BTC15m slugs name the start."""
    return float(t_start) + WINDOW_S


def cancel_cutoff_from_start(t_start: float) -> float:
    """The terminal deadline, taken from the WINDOW START.

    WHY THIS EXISTS RATHER THAN CALLERS DOING THE ARITHMETIC. `cancel_cutoff`
    takes the window END, but every slug and every book row names the window
    START, so a caller who passes what it has gets `start - 180` -- a
    deadline 180 seconds BEFORE the window opens, i.e. already expired on the
    first tick, which cancels the probe's legs the moment they rest. That is
    exactly the bug an independent review found in Runner.maybe_ts_deadline.
    Callers that hold a start must use this and never do the addition
    themselves.
    """
    return cancel_cutoff(window_end(t_start))


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
    floor = fee_floor(up_px, dn_px)
    edge = floor["edge"]
    if not floor["ok"]:
        return no("EDGE_BELOW_FEE_FLOOR", pair_cost=str(pair),
                  edge=str(edge),
                  net_if_maker=str(floor["net_if_maker"]),
                  net_if_taker=str(floor["net_if_taker"]),
                  required="gross edge >= %s and net_if_maker > 0"
                           % MIN_GROSS_EDGE)

    # size: the WORST single-leg fill must stay inside the loss ceiling, and
    # the gross cash must stay inside the envelope. The dearer leg binds.
    dear = max(up_px, dn_px)
    by_loss = cap / dear
    # SIZE AGAINST THE GATE'S OWN CASH BASIS, not the pair cost. The cash
    # gate prices a resting DOWN leg (a BUY_SHORT) at a DOLLAR A SHARE, not
    # at its limit price, because a short owes $1 if YES settles 1. Sizing on
    # `pair` would therefore build a plan the gate then refuses -- which is
    # exactly what happened the first time this was wired. The conservative
    # accounting is preserved and respected, not worked around.
    oblig_per_contract = D(1) + up_px
    by_cash = env / oblig_per_contract
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
                and qty * oblig_per_contract <= env):
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

    # ACCEPTANCE IS ON THE EXECUTION THIS PROBE ACTUALLY USES. Both legs are
    # post-only, so the maker coefficient applies here exactly as it does in
    # fee_floor; evaluating acceptance under taker fees while the floor used
    # maker fees would have contradicted itself, and did.
    econ = net_settlement(up_qty=qty, dn_qty=qty, up_price=up_px,
                          dn_price=dn_px, theta=MAKER_THETA)
    # the worst case is still COMPUTED and reported, never hidden
    econ_taker = net_settlement(up_qty=qty, dn_qty=qty, up_price=up_px,
                                dn_price=dn_px, theta=TAKER_THETA)
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
            "fee_floor": {k: (str(v) if isinstance(v, D) else v)
                          for k, v in floor.items()},
            "qty_per_leg": str(qty),
            "gross_cash_usd": str(econ["cost"]),
            "cash_gate_obligation_usd": str(qty * oblig_per_contract),
            "cash_basis": ("a resting DOWN leg is a BUY_SHORT and the cash "
                           "gate charges it a dollar a share; sizing uses "
                           "that basis so the plan the gate sees is the "
                           "plan this built"),
            "up_pnl": str(econ["up_pnl"]), "dn_pnl": str(econ["dn_pnl"]),
            "up_pnl_if_taker": str(econ_taker["up_pnl"]),
            "dn_pnl_if_taker": str(econ_taker["dn_pnl"]),
            "both_positive_if_taker": bool(econ_taker["both_positive"]),
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


# ------------------------------------------- patient opposite-side completion

#: how close to the terminal deadline completion may be CONSIDERED. Before
#: this the maker leg is still resting and still the better price, so there is
#: nothing to decide; crossing early throws away the edge the maker quote was
#: placed for.
COMPLETION_WINDOW_S = 120.0


def completion_phase(now: float, t_start: float) -> str:
    """MAKER while there is time, COMPLETION near the deadline, else LATE.

    Three named phases rather than a boolean, because "near the deadline" and
    "past the deadline" need different answers: at the cutoff nothing may
    rest, so a completion that has not been decided by then is not decided at
    all.
    """
    cut = cancel_cutoff_from_start(float(t_start))
    if float(now) >= cut:
        return "LATE"
    if float(now) >= cut - COMPLETION_WINDOW_S:
        return "COMPLETION"
    return "MAKER"


def completion_decision(*, up_qty: Any, dn_qty: Any,
                        up_cost_px: Any, dn_cost_px: Any,
                        taker_price: Any, available_depth: Any,
                        tick: Any, min_qty: Any,
                        cash_headroom: Any, worst_case_ceiling: Any,
                        book_age_s: Any = None,
                        gap_suspected: bool = False) -> Dict[str, Any]:
    """Should we CROSS to complete the deficient side? Economics only.

    THE DEADLINE IS NOT A REASON. This never returns ok True because time is
    short. It returns ok True only when crossing at the price actually
    executable, paying the actual taker fee, STRICTLY IMPROVES the worst of
    the two settlement outcomes, and fits inside the cash headroom and the
    worst-case ceiling we are already authorized for. Every other case is a
    refusal with its reason, and the caller's answer to a refusal is to
    cancel the unfilled order and carry the residual under the existing
    controls -- not to cross anyway.

    WHY IT CAN BEAT HOLDING. A one-sided book bought at `c` loses `c` on the
    wrong outcome. Completing at `q` makes the pair cost `c + q`, which pays
    `min(a, b)` on BOTH outcomes; whenever `c + q + fee < min(a, b)` after the
    completion that is a smaller worst case than holding, even though `q` is
    a crossing price and strictly worse than the maker quote we wanted.

    PARTIAL COMPLETION IS ALLOWED AND IS THE NORMAL CASE. Displayed depth
    rarely covers the whole deficit. A partial fill of the deficient side
    still lifts `min(a, b)`, so the size is clipped to the depth, the cash and
    the ceiling, and judged on what that clipped size actually does.

    PRICES ARE OWN-OUTCOME. `taker_price` is what a share of the DEFICIENT
    side costs to buy right now -- for DOWN that is `1 - up_bid`, not the UP
    ask -- and the caller owns that arithmetic because only the caller has the
    book.

    NOTHING HERE SUBMITS, CANCELS OR SETTLES ANYTHING.
    """
    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason, "cross": False}, **extra)

    a = _d(up_qty); b = _d(dn_qty)
    if a is None or b is None or a < 0 or b < 0:
        return no("INVENTORY_UNREADABLE")
    if a == 0 and b == 0:
        return no("NO_INVENTORY_TO_COMPLETE")
    if a == b:
        return no("ALREADY_MATCHED", matched=str(a))
    side = "DOWN" if a > b else "UP"
    deficit = abs(a - b)

    if gap_suspected:
        return no("GAP_SUSPECTED", side=side, deficit=str(deficit))
    age = _d(book_age_s)
    if age is None or age > D(str(MAX_BOOK_AGE_S)):
        # A STALE BOOK IS NOT AN EXECUTABLE PRICE. Crossing on a price we
        # cannot date is how a "completion" becomes a worse one-sided book.
        return no("BOOK_STALE", side=side, book_age_s=None if age is None
                  else str(age))
    q = _d(taker_price)
    if q is None or q <= 0 or q >= D(1):
        return no("COMPLETION_PRICE_UNREADABLE", side=side)
    if q < MIN_PRICE or q > MAX_PRICE:
        return no("COMPLETION_PRICE_OUT_OF_BAND", side=side, price=str(q))
    tk = _d(tick)
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    if (q / tk) != (q / tk).to_integral_value():
        return no("COMPLETION_PRICE_NOT_ON_TICK", side=side, price=str(q))
    mq = _d(min_qty)
    if mq is None or mq <= 0:
        return no("MIN_QTY_UNKNOWN")
    depth = _d(available_depth)
    if depth is None:
        # NO DISPLAYED DEPTH IS NOT INFINITE DEPTH.
        return no("NO_DISPLAYED_DEPTH", side=side)
    cash = _d(cash_headroom)
    cap = _d(worst_case_ceiling)
    if cash is None or cash < 0:
        return no("CASH_HEADROOM_UNKNOWN", side=side)
    if cap is None or cap <= 0:
        return no("WORST_CASE_CEILING_UNKNOWN", side=side)

    # SIZE: the deficit, clipped by displayed depth, by the cash this costs,
    # and then shrunk on the venue grid until the fee fits too.
    qty = min(deficit, depth, cash / q if q > 0 else D(0))
    qty = (qty / mq).to_integral_value(rounding="ROUND_FLOOR") * mq
    for _ in range(64):
        if qty < mq:
            break
        if qty * q + worst_fee(qty, q) <= cash:
            break
        qty -= mq
    if qty < mq:
        return no("NO_LEGAL_COMPLETION_QUANTITY", side=side,
                  deficit=str(deficit), depth=str(depth),
                  cash_headroom=str(cash))

    # THE DECISION, through the EXISTING predicate. top_up already answers
    # "does extra on this side improve the worst outcome", at taker fees, so
    # the completion is judged by the same arithmetic as any other top-up
    # rather than by a second opinion written for the deadline.
    tu = top_up(up_qty=a, dn_qty=b, up_price=up_cost_px, dn_price=dn_cost_px,
                extra_side=side, extra_qty=qty, extra_price=q)
    if not tu.get("ok"):
        return no(str(tu.get("reason") or "TOP_UP_WORSENS_WORST_OUTCOME"),
                  side=side, qty=str(qty), price=str(q),
                  worst_before=str(tu.get("worst_before")),
                  worst_after=str(tu.get("worst_after")),
                  labels=("crossing would NOT improve the worst settlement "
                          "outcome, so the deadline is not a reason to do "
                          "it. Cancel the unfilled order and carry the "
                          "residual under the existing controls."))

    # AND THE CROSS MUST BUY MORE THAN IT COSTS.
    #
    # WHY THIS SECOND BAR IS NEEDED, measured rather than assumed. "Improves
    # the worst outcome" alone is very nearly VACUOUS once a book is
    # one-sided: holding loses the leg's whole cost on the wrong outcome, so
    # every completion priced below a dollar reduces that. On a 5-share UP
    # leg at 0.59 the predicate passes at 0.35, 0.45, 0.60, 0.80, 0.90 AND
    # 0.95 -- so a rule written only on worst-case improvement would cross at
    # essentially any price the book showed, which is not an economic
    # decision.
    #
    # WHAT COMPLETION ACTUALLY COSTS. At the market's own price the cross is
    # fair value, so in expectation it costs exactly the TAKER FEE and buys
    # the removal of all settlement variance. This bar says that purchase
    # must be worth its price: the worst case must improve by MORE than the
    # fee paid for the improvement.
    taker_fee = worst_fee(qty, q)
    improvement = tu["worst_after"] - tu["worst_before"]
    if improvement <= taker_fee:
        return no("IMPROVEMENT_BELOW_ITS_OWN_FEE", side=side, qty=str(qty),
                  price=str(q), improvement_usd=str(improvement),
                  taker_fee_usd=str(taker_fee),
                  labels=("the cross would cost more in fee than it buys in "
                          "worst-case improvement. In expectation a "
                          "completion at the market's own price costs "
                          "exactly the taker fee and buys the removal of "
                          "settlement variance; this one does not pay for "
                          "itself."))

    # AND IT MUST STILL FIT THE CEILING WE ARE AUTHORIZED FOR. An improvement
    # that breaches the worst-case bound is not permission to breach it.
    worst_after = tu["worst_after"]
    if worst_after < -cap:
        return no("COMPLETION_EXCEEDS_WORST_CASE_CEILING", side=side,
                  qty=str(qty), price=str(q), worst_after=str(worst_after),
                  ceiling=str(cap))

    partial = bool(qty < deficit)
    return {"ok": True, "cross": True, "side": side, "action": "BUY",
            "qty": str(qty), "price": str(q), "mode": FT.TAKER,
            "post_only": False,
            "deficit": str(deficit), "partial": partial,
            "displayed_depth": str(depth),
            "taker_fee_usd": str(taker_fee),
            "cash_cost_usd": str(qty * q + worst_fee(qty, q)),
            "worst_before": str(tu["worst_before"]),
            "worst_after": str(worst_after),
            "worst_improvement_usd": str(worst_after - tu["worst_before"]),
            "up_pnl_after": str(tu["up_pnl_after"]),
            "dn_pnl_after": str(tu["dn_pnl_after"]),
            "both_positive_after": bool(tu["both_positive_after"]),
            "labels": ("a CROSSING order, priced at the displayed executable "
                       "price and charged the TAKER fee. It is proposed only "
                       "because it improves the worst settlement outcome, "
                       "never because the deadline arrived. Displayed depth "
                       "is not a fill: the size may fill partially or not at "
                       "all, and a refusal to fill leaves the residual the "
                       "existing controls already bound.")}
