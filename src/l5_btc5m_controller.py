"""Inventory-aware decision controller for PMUS BTC 5-minute up/down. PURE.

One function, `decide`, maps (YES book, time in window, inventory, resting
orders, optional probability) to ONE action plus the orders that implement
it. It does no I/O, reads no clock, holds no state and never mutates its
inputs; the only environment it reads is the on/off switch in `enabled()`.
It places nothing itself: a caller must act on the returned orders.

MARKET. Binary; UP (YES) wins when the BTC index at window end is at or
above the index at window start. 300 s window, tick 0.01, minimum quantity
0.01. Taker fee 0.095 * p * (1-p) per share, maker fee 0, NO rebate assumed.

OWN-PRICE CONVENTION. Every price here is the price of the side being
bought: UP price = YES price, DOWN price = 1 - YES price. So a DOWN bid is
1 - YES ask and a DOWN ask is 1 - YES bid.

WHY IT MOSTLY HOLDS. A passive buy below the bid has measured as adversely
selected (about 6 cents a share on a proxy). The value of a maker buy at x
on side s is therefore p_s - x - adverse_selection_per_share, and an entry
needs that to clear min_edge_per_share. With the book mid as the only
probability, the estimate is not independent of the price being paid
(`independent_estimate: False`) and a normal tight book fails the test.

FAIL CLOSED. Any unreadable input returns HOLD_OR_SKIP with a named reason
and cancels every resting order, as does an unusable or stale book: nothing
is left working that this decision could not re-justify.

Every PLACE is a post-only maker BUY unless `Params.taker_allowed` is True
(default False), which is the only way EXIT_OR_FLIP can be emitted.

TWO ENTRY RULES, selected by `Params.entry_mode` and described there.
"EDGE_GATED" is the original and the default. "ACCUMULATE" is favorite
accumulation: repeated small maker buys of the current favorite at an
eligible paid price, for the whole window, while the cap has room, with no
edge gate and no pair formation. Nothing about the caps, the halts, the
post-only rule or the authorization path differs between them.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

D = Decimal

CONTROLLER_ENV = "L5_BTC5M_CONTROLLER"

DISABLED = "DISABLED"
OPEN_FAVORITE = "OPEN_FAVORITE"
ADD_FAVORITE = "ADD_FAVORITE"
HEDGE_UNDERDOG = "HEDGE_UNDERDOG"
REPRICE = "REPRICE"
EXIT_OR_FLIP = "EXIT_OR_FLIP"
HOLD_OR_SKIP = "HOLD_OR_SKIP"

UP = "UP"
DOWN = "DOWN"
SIDES = (UP, DOWN)

#: the two entry rules. See `Params.entry_mode`.
EDGE_GATED = "EDGE_GATED"
ACCUMULATE = "ACCUMULATE"
#: "STRADDLE" rests a FIXED limit on BOTH sides for the whole window and
#: never requotes. It has no favorite, no band and no edge gate, because it
#: is not a view on which side wins -- it is a bet that the window reverses
#: hard enough to fill both legs cheaply. See `Params.straddle_price`.
STRADDLE = "STRADDLE"
ENTRY_MODES = (EDGE_GATED, ACCUMULATE, STRADDLE)

ZERO = D("0")
ONE = D("1")
HALF = D("0.5")
CENT = D("0.01")
PRICE_MIN = D("0.01")
PRICE_MAX = D("0.99")


def enabled() -> bool:
    """True only when the switch is exactly "ON". Anything else is off."""
    return os.environ.get(CONTROLLER_ENV) == "ON"


@dataclass(frozen=True)
class Params:
    band_lo: Decimal = D("0.51")              # favorite bid band, inclusive
    band_hi: Decimal = D("0.90")
    depth_ticks: int = 2                      # entry rests this far below the bid
    tick: Decimal = D("0.01")
    min_qty: Decimal = D("0.01")
    clip_usd: Decimal = D("0.10")             # dollars per entry
    #: SHARES per entry, when the owner means a share count rather than a
    #: dollar amount. None (the default) sizes by `clip_usd`. The owner's
    #: manual orders were 0.10 SHARES (about $0.06-$0.09 each); the pilot
    #: brief says about $0.10 per order. They are different sizes and the
    #: choice has to be stated, so both exist and neither is inferred.
    clip_shares: Optional[Decimal] = None
    max_exposure_usd: Decimal = D("5.00")     # filled cost + resting notional
    hedge_reserve_usd: Decimal = D("1.50")    # part of the cap entries may not use
    min_edge_per_share: Decimal = D("0.01")
    adverse_selection_per_share: Decimal = D("0.06")
    hedge_target_pair: Decimal = D("0.98")
    hard_pair_ceiling: Decimal = D("1.00")
    quote_ttl_s: float = 20.0
    stale_book_s: float = 3.0
    no_new_entry_after_s: float = 270.0
    window_s: float = 300.0
    taker_allowed: bool = False
    taker_fee_coef: Decimal = D("0.095")
    flip_margin: Decimal = D("0.05")
    #: How entries keep room for the completing (opposite-side) order.
    #: "FIXED": entries may not use the last `hedge_reserve_usd` of the cap
    #: (the original rule; meaningless when the cap is below the reserve).
    #: "COMPLETION": an entry is placed only if, after it, the cap still
    #: holds the cost of completing EVERY unmatched share -- held, resting
    #: on the favorite side, and the new entry -- at the target pair price,
    #: plus `completion_fee_per_share`. Shares already resting on the
    #: completing side are counted once, as resting notional, not reserved
    #: again. The executor runs in this mode with its effective ceiling.
    #: "NONE": entries may use the whole cap. Only coherent with
    #: `hedge_enabled` False -- there is no completing order to reserve for.
    reserve_mode: str = "FIXED"
    completion_fee_per_share: Decimal = D("0")

    # ------------------------------------------------- entry mode
    #: WHICH ENTRY RULE APPLIES. Two, and they are different strategies.
    #:
    #: "EDGE_GATED" (the default, unchanged): one entry at a time, resting
    #: `depth_ticks` below the favorite's bid, admitted only when
    #:     p_side - price - adverse_selection_per_share >= min_edge_per_share
    #: On a one-cent book that asks the entry to sit about SEVEN cents under
    #: the bid (0.06 + 0.01), which a 5-minute book does not offer: every
    #: step of the 2026-11-12 06:30z and 08:00z windows refused with
    #: EV_BELOW_MIN while $0.41 of the $0.45 cap sat unused.
    #:
    #: "ACCUMULATE" (favorite accumulation, owner's contract 2026-11-12):
    #: repeated small post-only maker buys of the CURRENT FAVORITE at an
    #: eligible price, throughout the window, while capacity remains. The
    #: edge gate is NOT an entry condition: the eligible price band is. The
    #: band is judged ON THE PRICE THIS ORDER WOULD PAY -- `band_lo` through
    #: `band_hi` inclusive -- not on the touch it is derived from, because
    #: the band is a statement about the price paid. `ev_per_share` is still
    #: computed and reported on every decision, as evidence; it blocks
    #: nothing. Adding to a side STOPS the moment that side is no longer the
    #: favorite, and anything resting on the other side is cancelled.
    entry_mode: str = "EDGE_GATED"
    #: STRADDLE only: the fixed own-price each side rests at, all window.
    #: A matched pair costs exactly 2 x this and pays 1.00, so anything at
    #: or above 0.50 locks a LOSS by construction and is refused as
    #: unreadable rather than treated as a configuration choice. Measured
    #: best on the 189-window tape around 0.05-0.10 (analysis/l5/
    #: btc5m_accumulation/straddle.py); the default is deliberately the
    #: do-nothing value so the mode cannot trade until it is set.
    straddle_price: Decimal = D("0")
    #: STRADDLE only: extra rungs. Empty means the single `straddle_price`.
    #: A LADDER IS LEVERAGE, NOT DIVERSIFICATION -- every rung fills on the
    #: same loser collapse and pays on the same reversal, so they are
    #: perfectly correlated and widening the ladder does NOT improve the
    #: risk-adjusted result. Measured on the 189-window tape: 0.05-0.20
    #: returns 10x the absolute profit of a single rung for 27x the
    #: capital, with t falling 1.67 -> 1.17; 0.05-0.25 and 0.10-0.25 go
    #: NEGATIVE out of sample. Only the narrow cheap end (0.05-0.10) holds
    #: its return on capital. Rungs at or above 0.50 are refused with the
    #: single price, for the same reason.
    straddle_rungs: tuple = ()
    #: ACCUMULATE only: ticks below the favorite's bid an entry rests at.
    #: 0 joins the bid, which is where a maker buy has queue priority and
    #: the smallest gap to the price the band speaks about.
    accumulate_depth_ticks: int = 0
    #: ACCUMULATE only: how many of this strategy's own entry orders may
    #: rest on the favorite at once. The executor's `max_live_orders` is a
    #: separate, harder bound and the smaller of the two is what binds.
    #: FOUR, because four $0.10 clips are what a $0.45 ceiling holds, so the
    #: CAP is what stops accumulation rather than a count: measured on the
    #: 142-window tape, a limit of 2 refused 20 entries the cap would have
    #: allowed and a limit of 4 refuses 1. Raising it further changes
    #: nothing at this clip size.
    max_resting_entries: int = 4
    #: Whether an unmatched leg is completed with the opposite side at all.
    #: False is favorite accumulation: a directional inventory with no pair
    #: formation, so no HEDGE_UNDERDOG is ever emitted, nothing is reserved
    #: for a completion, and a resting order on the non-favorite side is
    #: cancelled rather than read as a hedge.
    hedge_enabled: bool = True


# --------------------------------------------------------------- readers

def _dec(x: Any) -> Optional[Decimal]:
    """A finite Decimal, or None. bool is not a number here."""
    if x is None or isinstance(x, bool):
        return None
    try:
        d = x if isinstance(x, Decimal) else D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def _num(x: Any) -> Optional[float]:
    """A finite float, or None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (ValueError, TypeError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _other(side: str) -> str:
    return DOWN if side == UP else UP


def _floor_to(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _floor_qty(x: Decimal, params: Params) -> Decimal:
    """Down to a multiple of min_qty, then to 0.01. Never negative."""
    if x <= 0:
        return D("0.00")
    return _floor_to(_floor_to(x, params.min_qty), CENT).quantize(CENT)


def _read_inventory(inventory: Any) -> Optional[Dict[str, tuple]]:
    """{side: (qty, cost)}; a missing side is flat; anything odd is None."""
    if not isinstance(inventory, dict):
        return None
    out = {}
    for side in SIDES:
        leg = inventory.get(side)
        if leg is None:
            out[side] = (ZERO, ZERO)
            continue
        if not isinstance(leg, dict):
            return None
        qty, cost = _dec(leg.get("qty")), _dec(leg.get("cost"))
        if qty is None or cost is None or qty < 0 or cost < 0:
            return None
        if qty == 0 and cost > 0:           # cost with nothing held: not ours
            return None
        out[side] = (qty, cost)
    return out


def _read_resting(resting: Any) -> Optional[List[Dict[str, Any]]]:
    """Normalised resting BUY orders, or None when any one is unreadable."""
    if not isinstance(resting, (list, tuple)):
        return None
    out = []
    for o in resting:
        if not isinstance(o, dict) or o.get("id") is None:
            return None
        price, qty, age = _dec(o.get("price")), _dec(o.get("qty")), _num(o.get("age_s"))
        if o.get("side") not in SIDES or price is None or qty is None or age is None:
            return None
        if not (ZERO < price < ONE) or qty <= 0 or age < 0:
            return None
        out.append({"id": o["id"], "side": o["side"], "price": price,
                    "qty": qty, "age_s": age})
    return out


def _cancel_all(resting: Any) -> List[Dict[str, Any]]:
    """A CANCEL for every resting order that has an id, readable or not."""
    out, seen = [], []
    if isinstance(resting, (list, tuple)):
        for o in resting:
            if isinstance(o, dict) and o.get("id") is not None and o["id"] not in seen:
                seen.append(o["id"])
                out.append({"op": "CANCEL", "id": o["id"]})
    return out


# ----------------------------------------------------------- probability

def p_up_from_spot(spot: Any, strike: Any, t_left_s: Any,
                   vol_per_sqrt_s: Any) -> Optional[Decimal]:
    """P(UP) = Phi((spot - strike) / (vol * sqrt(t_left))). None if unreadable.

    `vol_per_sqrt_s` is in index dollars per square-root second. With no
    time left the outcome is decided: 1 when spot >= strike, else 0.
    """
    s, k, t = _num(spot), _num(strike), _num(t_left_s)
    if s is None or k is None or t is None:
        return None
    if t <= 0:
        return ONE if s >= k else ZERO
    v = _num(vol_per_sqrt_s)
    if v is None or v <= 0:
        return None
    z = (s - k) / (v * math.sqrt(t))
    p = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return D(repr(min(1.0, max(0.0, p)))).quantize(D("0.000001"))


# -------------------------------------------------------------- exposure

def _exposure(inv: Dict[str, tuple], rest: List[Dict[str, Any]]) -> Dict[str, Any]:
    (up_qty, up_cost), (down_qty, down_cost) = inv[UP], inv[DOWN]
    filled = up_cost + down_cost
    resting_notional = sum((o["price"] * o["qty"] for o in rest), ZERO)
    matched = min(up_qty, down_qty)
    if up_qty > down_qty:
        unmatched_side, unmatched = UP, up_qty - down_qty
    elif down_qty > up_qty:
        unmatched_side, unmatched = DOWN, down_qty - up_qty
    else:
        unmatched_side, unmatched = None, ZERO
    pair = None
    if up_qty > 0 and down_qty > 0:
        pair = up_cost / up_qty + down_cost / down_qty
    # Filled inventory only: the losing side pays nothing, the winner $1 each.
    # Negative means the filled book is ahead on either outcome.
    worst_filled = max(filled - up_qty, filled - down_qty)
    return {
        "filled_cost_usd": filled,
        "resting_notional_usd": resting_notional,
        "total_exposure_usd": filled + resting_notional,
        "up_qty": up_qty,
        "down_qty": down_qty,
        "unmatched_side": unmatched_side,
        "unmatched_qty": unmatched,
        "matched_qty": matched,
        "matched_cost_per_pair": pair,
        "worst_case_settlement_loss_usd": worst_filled,
        # Every resting buy fills and then loses: all cost, no payoff. So a
        # resting hedge can only raise this, never lower either figure.
        "worst_case_if_resting_fill_usd": worst_filled + resting_notional,
    }


def exposure(inventory: Any, resting: Any) -> Optional[Dict[str, Any]]:
    """Dollar exposure and worst cases; None when either input is unreadable."""
    inv, rest = _read_inventory(inventory), _read_resting(resting)
    if inv is None or rest is None:
        return None
    return _exposure(inv, rest)


# ------------------------------------------------------ completion reserve

def completion_reserve(inv: Dict[str, tuple], rest: List[Dict[str, Any]],
                       params: "Params", add_side: Optional[str] = None,
                       add_price: Decimal = ZERO, add_qty: Decimal = ZERO
                       ) -> Dict[str, Any]:
    """Dollars the cap must still hold to complete every unmatched share.

    Worst case for the reserve: every resting BUY fills (and the optional
    new order too). The long side is the one with more shares after that;
    each uncovered share needs one share of the other side at a price no
    higher than `hedge_target_pair - avg long cost` -- what an ordinary
    completion may pay -- plus the fee allowance. A flipped-favorite hedge
    may pay up to `hard_pair_ceiling`; for that case the reserve can fall
    short by (ceiling - target) a share and the hedge is sized to the room
    left. Pricing the reserve at the ceiling instead was measured on the 42
    recorded windows (analysis/l5/btc5m_cap_policy_replay.py): it blocked
    entries that later completed cheaply and replayed worse. Shares resting on the short side are
    already in `total_exposure_usd` as resting notional, so they cover their
    part of the gap and are not reserved a second time.
    """
    qty = {s: inv[s][0] for s in SIDES}
    cost = {s: inv[s][1] for s in SIDES}
    for o in rest:
        qty[o["side"]] += o["qty"]
        cost[o["side"]] += o["qty"] * o["price"]
    if add_side in SIDES and add_qty > 0:
        qty[add_side] += add_qty
        cost[add_side] += add_qty * add_price
    if qty[UP] == qty[DOWN]:
        return {"usd": ZERO, "uncovered_qty": ZERO, "long_side": None,
                "price_bound": None}
    long_ = UP if qty[UP] > qty[DOWN] else DOWN
    gap = qty[long_] - qty[_other(long_)]
    avg = cost[long_] / qty[long_]
    bound = max(ZERO, params.hedge_target_pair - avg)
    per = bound + params.completion_fee_per_share
    return {"usd": gap * per, "uncovered_qty": gap, "long_side": long_,
            "price_bound": bound}


# --------------------------------------------------------------- decide

def _result(action, why, orders, evaluation, exp, favorite=None, p_up=None,
            p_source=None) -> Dict[str, Any]:
    return {"action": action, "why": why, "orders": orders,
            "evaluation": evaluation, "exposure": exp, "favorite": favorite,
            "p_up": p_up, "p_source": p_source}


def _place(side, price, qty, reason, *, post_only=True, intent="BUY", slot=0):
    """One order. `slot` is which of the concurrent entries this is: 0
    everywhere except an ACCUMULATE entry beyond the first, where the
    caller needs to tell two identical clips apart without reading either
    as a replay of the other."""
    return {"op": "PLACE", "side": side, "price": price, "qty": qty,
            "post_only": post_only, "intent": intent, "reason": reason,
            "slot": int(slot)}


def decide(*, book, t_in_window_s, inventory, resting, params=Params(),
           p_up=None, p_source=None) -> Dict[str, Any]:
    """One decision. See the module docstring; the steps are numbered below."""
    # 0. Off unless explicitly switched on.
    if not enabled():
        return _result(DISABLED, "CONTROLLER_OFF", [], {}, exposure(inventory, resting))

    exp = exposure(inventory, resting)
    cancel_all = _cancel_all(resting)

    def hold(why, *, cancel=False, evaluation=None):
        return _result(HOLD_OR_SKIP, why, list(cancel_all) if cancel else [],
                       evaluation or {}, exp)

    tick, min_qty = _dec(params.tick), _dec(params.min_qty)
    if tick is None or tick <= 0 or min_qty is None or min_qty <= 0:
        return hold("PARAMS_UNREADABLE", cancel=True)
    if params.reserve_mode not in ("FIXED", "COMPLETION", "NONE"):
        return hold("PARAMS_UNREADABLE", cancel=True)
    if params.entry_mode not in ENTRY_MODES:
        return hold("PARAMS_UNREADABLE", cancel=True)
    straddle = params.entry_mode == STRADDLE
    _sx = _dec(params.straddle_price)
    if straddle and (_sx is None or not (ZERO < _sx < HALF)):
        # at or above 0.50 the pair costs >= 1.00 and cannot profit, so it
        # is a contradiction rather than an aggressive setting.
        return hold("PARAMS_UNREADABLE", cancel=True)
    _rungs = ()
    if straddle:
        raw = params.straddle_rungs or (params.straddle_price,)
        try:
            _rungs = tuple(sorted({_dec(r) for r in raw}))
        except TypeError:
            return hold("PARAMS_UNREADABLE", cancel=True)
        if not _rungs or any(r is None or not (ZERO < r < HALF)
                             for r in _rungs):
            return hold("PARAMS_UNREADABLE", cancel=True)
    # "NONE" says nothing is kept back for a completing order, which is only
    # true when no completing order is ever placed. Asking for one without
    # the other is a contradiction, not a default.
    if (params.reserve_mode == "NONE") != (not params.hedge_enabled):
        return hold("PARAMS_UNREADABLE", cancel=True)
    accumulate = params.entry_mode == ACCUMULATE
    if accumulate and int(params.max_resting_entries) < 1:
        return hold("PARAMS_UNREADABLE", cancel=True)
    _cap, _fee = _dec(params.max_exposure_usd), _dec(params.completion_fee_per_share)
    if _cap is None or _cap < 0 or _fee is None or _fee < 0:
        return hold("PARAMS_UNREADABLE", cancel=True)

    # 1. The book. Without a fresh two-sided YES quote nothing may rest.
    if not isinstance(book, dict):
        return hold("BOOK_UNUSABLE", cancel=True)
    bid, ask, age = _dec(book.get("bid")), _dec(book.get("ask")), _num(book.get("age_s"))
    if bid is None or ask is None or age is None or age < 0:
        return hold("BOOK_UNUSABLE", cancel=True)
    if age > params.stale_book_s:
        return hold("BOOK_STALE", cancel=True)
    if bid <= 0 or ask <= bid or ask >= 1:
        return hold("BOOK_UNUSABLE", cancel=True)

    t = _num(t_in_window_s)
    if t is None or t < 0:
        return hold("TIME_UNREADABLE", cancel=True)
    if t >= params.window_s:
        return hold("WINDOW_OVER", cancel=True)

    inv, rest = _read_inventory(inventory), _read_resting(resting)
    if inv is None:
        return hold("INVENTORY_UNREADABLE", cancel=True)
    if rest is None:
        return hold("RESTING_UNREADABLE", cancel=True)

    # 1b. STRADDLE. Handled in full here, before any favorite, band or
    #     edge machinery is touched, so the two older modes cannot be
    #     perturbed by it: nothing below this block runs in this mode.
    #
    #     The rule is deliberately dumb. Leave ONE resting buy per side at
    #     `straddle_price` and never move it. A fixed limit cannot drift,
    #     so if both fill the pair cost is exactly 2x that price -- no
    #     adverse selection on the PRICE at all, only on which legs fill.
    #     A side that already holds inventory is finished: the straddle
    #     wants one fill a side, not accumulation.
    if straddle:
        exp_s = _exposure(inv, rest)
        room = _cap - exp_s["total_exposure_usd"]
        #: SIZED SO ONE SIDE'S WHOLE LADDER COSTS `clip_usd`, which keeps
        #: the single-rung case identical (sum == the one price) and makes
        #: the worst case exactly 2 x clip whatever the ladder's width.
        ladder = sum(_rungs, ZERO)
        qty = _floor_qty(_dec(params.clip_usd) / ladder, params)
        want = []
        why_s = "STRADDLE_RESTING"
        if qty <= 0:
            why_s = "CLIP_TOO_SMALL"
        elif t >= params.no_new_entry_after_s:
            why_s = "ENTRY_WINDOW_CLOSED"
        else:
            #: RUNGS OUTER, SIDES INNER -- so that any truncation falls
            #: EVENLY on both sides. Draining one side first (the original
            #: order) meant an external cap left the ladder lopsided: with
            #: 12 rungs wanted and 10 orders allowed, UP took all 6 and
            #: DOWN got 4, which is a directional bet wearing a straddle's
            #: clothes. Observed live 2026-11-12 23:12Z as MAX_LIVE_ORDERS
            #: refusing ten placements after UP had been filled first.
            #: A half-built ladder is the one shape this strategy must
            #: never hold.
            live = {s: {o["price"] for o in rest if o["side"] == s}
                    for s in SIDES}
            for i, r in enumerate(_rungs):
                for side in SIDES:
                    if inv[side][0] > 0:
                        continue                  # this side is done
                    if r in live[side]:
                        continue                  # that rung already rests
                    if r * qty > room:
                        why_s = "NO_EXPOSURE_ROOM"
                        continue
                    room -= r * qty
                    want.append(_place(side, r, qty, "STRADDLE_LEG",
                                       slot=i))
        ev_s = {"entry": {"why": why_s, "price": str(_sx), "qty": str(qty),
                          "room_usd": str(max(ZERO, room)),
                          "cap_usd": str(_cap),
                          "rungs": [str(r) for r in _rungs],
                          "ladder_cost_one_side": str(ladder * qty),
                          "pair_cost_if_both_fill": str(2 * _sx)}}
        return _result(OPEN_FAVORITE if want else HOLD_OR_SKIP,
                       why_s, want, ev_s, exp_s)

    # 2. Probability and favorite.
    if p_up is None:
        p, source = (bid + ask) / 2, "BOOK"
    else:
        p = _dec(p_up)
        if p is None:
            return hold("P_UP_UNREADABLE", cancel=True)
        p, source = min(ONE, max(ZERO, p)), (p_source if p_source is not None else "CALLER")
    independent = source != "BOOK"
    fav = UP if p >= HALF else DOWN
    prob = {UP: p, DOWN: ONE - p}
    quote = {UP: {"bid": bid, "ask": ask}, DOWN: {"bid": ONE - ask, "ask": ONE - bid}}
    fav_bid = quote[fav]["bid"]

    def priced(x):
        return PRICE_MIN <= x <= PRICE_MAX

    # 3. Entry price and its value after the adverse-selection allowance.
    #    ACCUMULATE rests `accumulate_depth_ticks` below the bid (0 = join
    #    it); EDGE_GATED keeps its own `depth_ticks`.
    _ticks = (params.accumulate_depth_ticks if accumulate
              else params.depth_ticks)
    target = _floor_to(fav_bid - _ticks * tick, tick)

    def ev_at(side, price):
        return prob[side] - price - params.adverse_selection_per_share

    late = t >= params.no_new_entry_after_s

    def entry_block(price):
        """Why a favorite entry at `price` is refused, or None.

        ACCUMULATE judges the band on `price` -- what this order pays --
        and applies NO edge gate. EDGE_GATED is unchanged: the band on the
        touch, and the adverse-selection edge test.
        """
        if late:
            return "ENTRY_WINDOW_CLOSED"
        banded = price if accumulate else fav_bid
        if not (params.band_lo <= banded <= params.band_hi):
            return "OUT_OF_BAND"
        if not priced(price):
            return "PRICE_OUT_OF_RANGE"
        if not accumulate and ev_at(fav, price) < params.min_edge_per_share:
            return "EV_BELOW_MIN"
        return None

    # Hedge geometry: the unmatched side is `held`, the hedge buys the other.
    # With `hedge_enabled` False there is no hedge at all: no leg is read as
    # one, none is priced, and none is ever chosen.
    held, unmatched = exp["unmatched_side"], exp["unmatched_qty"]
    if not params.hedge_enabled:
        held, unmatched = None, ZERO
    hedge_side = _other(held) if held else None
    avg_held = inv[held][1] / inv[held][0] if held else None
    hedge_price = _floor_to(quote[hedge_side]["bid"], tick) if held else None
    flipped = bool(held) and prob[held] < HALF - params.flip_margin

    def hedge_block(price):
        """Why a hedge at `price` is refused, or None."""
        pair = avg_held + price
        if pair > params.hard_pair_ceiling:
            return "PAIR_ABOVE_CEILING"
        if pair <= params.hedge_target_pair or flipped:
            return None
        return "PAIR_ABOVE_TARGET"

    # 4./5. Review what is resting: keep only what would be placed again now.
    cancels, review, kept = [], [], []
    kept_hedge_qty = ZERO
    kept_entries = 0
    #: an ACCUMULATE entry tracks the touch exactly: one tick of drift means
    #: paying over the bid (filled by a seller on the way down) or sitting
    #: behind it (never reached). EDGE_GATED keeps its one-tick tolerance.
    drift_tol = ZERO if accumulate else tick
    for o in rest:
        purpose, why = None, None
        on_hedge, on_fav = bool(held) and o["side"] == hedge_side, o["side"] == fav
        if o["age_s"] > params.quote_ttl_s:
            why = "TTL_EXPIRED"
        elif not on_hedge and not on_fav:
            why = "WRONG_SIDE"
        else:
            if on_hedge:
                if abs(o["price"] - hedge_price) > tick:
                    why = "PRICE_DRIFT"
                elif hedge_block(o["price"]) is not None:
                    why = hedge_block(o["price"])
                elif kept_hedge_qty + o["qty"] > unmatched:
                    why = "HEDGE_EXCEEDS_UNMATCHED"
                else:
                    purpose = "HEDGE"
                    kept_hedge_qty += o["qty"]
            if purpose is None and on_fav:
                if abs(o["price"] - target) > drift_tol:
                    why = "PRICE_DRIFT"
                elif entry_block(o["price"]) is not None:
                    why = entry_block(o["price"])
                elif (accumulate and
                      kept_entries >= int(params.max_resting_entries)):
                    why = "MAX_RESTING_ENTRIES"
                else:
                    purpose, why = "ENTRY", None
                    kept_entries += 1
        if purpose is None:
            cancels.append({"op": "CANCEL", "id": o["id"]})
        else:
            kept.append(dict(o, purpose=purpose))
        review.append({"id": o["id"], "side": o["side"], "price": o["price"],
                       "purpose": purpose, "verdict": "KEEP" if purpose else "CANCEL",
                       "why": why})

    total = exp["total_exposure_usd"]   # counts orders being cancelled: a cancel can lose the race

    # 6. Hedge the unmatched leg. May use the hedge reserve.
    hedge = {"chosen": False,
             "why": ("HEDGING_DISABLED" if not params.hedge_enabled
                     else "NO_UNMATCHED_INVENTORY")}
    if held:
        pair = avg_held + hedge_price
        # Everything already working on the hedge side counts, cancelled or not.
        working = sum((o["qty"] for o in rest if o["side"] == hedge_side), ZERO)
        need = unmatched - working
        room = params.max_exposure_usd - total
        can_afford = room / hedge_price if hedge_price > 0 and room > 0 else ZERO
        h_qty = _floor_qty(min(need, can_afford), params)
        hedge = {"held_side": held, "side": hedge_side, "price": hedge_price,
                 "qty": h_qty, "pair_cost": pair, "avg_held_cost": avg_held,
                 "unmatched_qty": unmatched, "already_working_qty": working,
                 "p_held": prob[held], "flipped": flipped,
                 "ev_per_share": ev_at(hedge_side, hedge_price),
                 "room_usd": room, "chosen": False}
        if not priced(hedge_price):
            hedge["why"] = "PRICE_OUT_OF_RANGE"
        elif hedge_block(hedge_price) is not None:
            hedge["why"] = hedge_block(hedge_price)
        elif need <= 0:
            hedge["why"] = "HEDGE_ALREADY_WORKING"
        elif _floor_qty(need, params) < min_qty:
            hedge["why"] = "QTY_BELOW_MIN"
        elif h_qty < min_qty:
            hedge["why"] = "NO_EXPOSURE_ROOM"
        else:
            hedge["chosen"] = True
            hedge["why"] = ("PAIR_AT_OR_BELOW_TARGET" if pair <= params.hedge_target_pair
                            else "FAVORITE_FLIPPED_PAIR_WITHIN_CEILING")

    # 7. Exit: sell the unmatched leg when the bid pays more than it is worth.
    if not params.taker_allowed:
        exit_ = {"blocked": "TAKER_DISABLED", "chosen": False, "why": "TAKER_DISABLED"}
    elif not held:
        exit_ = {"chosen": False, "why": "NO_UNMATCHED_INVENTORY"}
    else:
        x_price = _floor_to(quote[held]["bid"], tick)
        fee = params.taker_fee_coef * x_price * (ONE - x_price)
        x_qty = _floor_qty(unmatched, params)
        exit_ = {"side": held, "price": x_price, "qty": x_qty,
                 "taker_fee_per_share": fee, "net_per_share": x_price - fee,
                 "value_per_share": prob[held],
                 "ev_per_share": x_price - fee - prob[held], "chosen": False}
        if hedge["chosen"]:
            exit_["why"] = "HEDGE_TAKES_PRIORITY"
        elif not priced(x_price):
            exit_["why"] = "PRICE_OUT_OF_RANGE"
        elif x_qty < min_qty:
            exit_["why"] = "QTY_BELOW_MIN"
        elif x_price - fee > prob[held] + params.min_edge_per_share:
            exit_["chosen"], exit_["why"] = True, "BID_NET_OF_FEE_ABOVE_VALUE"
        else:
            exit_["why"] = "BID_NOT_ABOVE_VALUE"

    # 8. Open or add to the favorite. Entries may not touch the hedge reserve.
    kind = ADD_FAVORITE if inv[fav][0] > 0 else OPEN_FAVORITE
    if params.clip_shares is not None:
        _cs = _dec(params.clip_shares)
        e_qty = (_floor_qty(_cs, params)
                 if _cs is not None and _cs > 0 else D("0.00"))
    else:
        e_qty = (_floor_qty(params.clip_usd / target, params)
                 if target > 0 else D("0.00"))
    notional = target * e_qty
    if params.reserve_mode == "COMPLETION":
        res = completion_reserve(inv, rest, params, add_side=fav,
                                 add_price=target, add_qty=e_qty)
        entry_cap = params.max_exposure_usd - res["usd"]
    elif params.reserve_mode == "NONE":
        res = None
        entry_cap = params.max_exposure_usd
    elif params.reserve_mode == "FIXED":
        res = None
        entry_cap = params.max_exposure_usd - params.hedge_reserve_usd
    else:                                   # unreachable: checked above
        return hold("PARAMS_UNREADABLE", cancel=True)
    entry = {"kind": kind, "side": fav, "fav_bid": fav_bid, "price": target,
             "qty": e_qty, "p_side": prob[fav], "ev_per_share": ev_at(fav, target),
             "min_edge_per_share": params.min_edge_per_share,
             "notional_usd": notional, "room_usd": entry_cap - total,
             "reserve_mode": params.reserve_mode,
             "completion_reserve_usd": None if res is None else res["usd"],
             "cap_usd": params.max_exposure_usd,
             "entry_mode": params.entry_mode,
             "resting_entries": kept_entries,
             "max_resting_entries": (int(params.max_resting_entries)
                                     if accumulate else 1),
             "chosen": False}
    block = entry_block(target)
    if block is None:
        # EDGE_GATED allows one entry at a time. ACCUMULATE allows
        # `max_resting_entries`, which is what "repeated small maker orders
        # while capacity remains" needs: the cap, not a count of one, is
        # what should stop it.
        if accumulate:
            if kept_entries >= int(params.max_resting_entries):
                block = "MAX_RESTING_ENTRIES"
        elif any(o["side"] == fav for o in kept):
            block = "ENTRY_ALREADY_RESTING"
    if block is None and e_qty < min_qty:
        block = "QTY_BELOW_MIN"
    if block is None and total + notional > entry_cap:
        block = "NO_EXPOSURE_ROOM"
    if block is None and (hedge["chosen"] or exit_["chosen"]):
        block = "HEDGE_TAKES_PRIORITY" if hedge["chosen"] else "EXIT_TAKES_PRIORITY"
    entry["chosen"], entry["why"] = block is None, block or "EV_AND_ROOM_OK"

    evaluation = {"independent_estimate": independent, "t_in_window_s": t,
                  "entry_window_open": not late, "quotes": quote,
                  "entry": entry, "hedge": hedge, "exit": exit_, "resting": review}

    def done(action, why, orders):
        # Last line of defence: nothing non-post-only or off-grid leaves here.
        for o in orders:
            if o["op"] == "PLACE":
                if not o["post_only"] and not params.taker_allowed:
                    return _result(HOLD_OR_SKIP, "TAKER_ORDER_SUPPRESSED", list(cancel_all),
                                   evaluation, exp, fav, p, source)
                if not priced(o["price"]) or o["qty"] < min_qty:
                    return _result(HOLD_OR_SKIP, "ORDER_OUT_OF_RANGE", list(cancel_all),
                                   evaluation, exp, fav, p, source)
        return _result(action, why, orders, evaluation, exp, fav, p, source)

    if hedge["chosen"]:
        order = _place(hedge_side, hedge_price.quantize(tick), h_qty, hedge["why"])
        return done(HEDGE_UNDERDOG, hedge["why"], cancels + [order])
    if exit_["chosen"]:
        # Selling the leg leaves nothing to hedge or add to: clear the book too.
        order = _place(held, exit_["price"].quantize(tick), exit_["qty"], exit_["why"],
                       post_only=False, intent="SELL")
        return done(EXIT_OR_FLIP, exit_["why"], list(cancel_all) + [order])
    if entry["chosen"]:
        # the lowest free slot, which is simply how many entries are
        # already resting: the same state decides the same slot, so a
        # replayed decision is still recognised as the same intent.
        order = _place(fav, target.quantize(tick), e_qty, kind,
                       slot=(kept_entries if accumulate else 0))
        return done(kind, entry["why"], cancels + [order])
    if cancels:
        reasons = sorted({r["why"] for r in review if r["verdict"] == "CANCEL"})
        return done(REPRICE, "+".join(reasons), cancels)
    # 9. Nothing to do: the entry's binding reason is the reason.
    return done(HOLD_OR_SKIP, entry["why"], [])
