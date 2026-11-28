"""One portfolio-level controller for a BTC15m window. Pure; places nothing.

WHY THIS EXISTS. The engine has treated a window as a sequence of isolated
pairs: open two legs, complete or strand, maybe open again. This module
replaces that question with one about the whole book of inventory:

    given everything held, everything resting and what we believe about the
    outcome, what should be quoted on EACH side right now -- at what price
    and size -- and what does each quote do to the portfolio's settlement?

It combines four things that were separate: pair accumulation, favored-side
accumulation, inventory balancing and adaptive maker repricing. They are one
rule here, because they are one quantity.

THE QUANTITY. Hold U contracts of UP and D of DOWN for a total cost C. The
portfolio settles at ``U - C`` if UP wins and ``D - C`` if DOWN wins. With a
win probability p for UP its expectation is ``p*U + (1-p)*D - C``.

    AN IDENTITY THE WHOLE DESIGN RESTS ON: that expectation is the sum, over
    every contract bought, of (its side's win probability - its price).
    Pairing does not appear in it. A pair is two contracts whose risks
    cancel, not a source of profit: matched inventory changes the SPREAD of
    outcomes, never their mean. Profit can only come from which fills are
    taken; hedging decides how much it varies.

So every candidate order is scored on two separate lines:

  * expectation -- ``qty * (p_side - margin - adverse - price)``: the model's
    probability for that side, less an uncertainty margin, less what a maker
    fill is expected to cost in adverse selection, less the price;
  * risk -- the change in unmatched contracts it would cause, charged at
    ``risk * sqrt(p*(1-p))`` a contract. A quote that REDUCES the imbalance
    earns the charge back; one that adds to it pays it.

The reservation price of a side is where those two lines sum to zero. The
controller quotes at the reservation price or at the touch, whichever is
LOWER -- it never crosses and never improves the best bid. The same formula
therefore produces:

  pairs         both sides clear when neither is rich: two quotes;
  favored side  the model puts one side well above its price: that side is
                quoted at the touch and sized up, the other drops back;
  hedging       an unmatched UP position raises the DOWN reservation price
                by twice the risk charge: the hedge is bid up, the heavy
                side is bid down or dropped;
  repricing     the quote is recomputed from the current book and current
                inventory every call; `order_actions` turns the difference
                from what is resting into exact cancels and placements.

WHAT IT DOES NOT DO. It does not submit, cancel, read a venue or hold state.
It does not decide whether trading is authorized, and it does not replace a
single limit: the executor's cash, exposure, order-count, residual and
daily-loss gates, the own-settled-evidence gate, exact-order cancellation
and reconciliation all still run on anything built from its output. The
limits it takes are an EARLIER, tighter copy so that it does not propose
what would be refused.

NO PROBABILITY IS INVENTED. Without a supplied model probability the belief
is the book's own mid, labelled BOOK, and no side can then look cheap to it:
it can only pair and hedge. Favored-side accumulation needs a real estimate.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence

UP, DOWN = "UP", "DOWN"
SIDES = (UP, DOWN)
MODE_ENV = "L5_PORTFOLIO_CONTROLLER"
OFF, SHADOW, STEER = "OFF", "SHADOW", "STEER"
EPS = 1e-9


def mode() -> str:
    """OFF unless the owner sets the variable to exactly SHADOW or STEER.

    SHADOW records what the controller would quote and nothing else.

    STEER also lets it steer the EXISTING pair engine's openings -- which
    side may open and how far behind the touch -- and nothing more. It does
    not submit, it cannot open what that engine could not, and it grants no
    permission: the locks, the window authorization, every limit and the
    own-settled-evidence gate decide whether anything is placed at all."""
    v = os.getenv(MODE_ENV, "")
    return v if v in (SHADOW, STEER) else OFF


def other(side: str) -> str:
    return DOWN if side == UP else UP


@dataclass(frozen=True)
class Params:
    """How the controller values a fill. Every number is a cost or a cap."""
    margin: float = 0.03            # uncertainty on the win probability
    adverse: float = 0.03           # expected adverse move on a maker fill
    risk: float = 0.10              # charge per unmatched contract, x sigma
    min_value: float = 0.0          # least conservative value/contract to quote
    base_qty: float = 1.0
    max_qty: float = 3.0            # per order
    size_per_cent: float = 0.0      # extra contracts per cent of value
    max_unmatched: float = 3.0      # contracts held on one side unmatched
    max_depth_ticks: int = 6        # no quote further than this behind touch
    reprice_ticks: int = 1          # move a resting order only past this
    open_after_s: float = 5.0
    entry_cutoff_s: float = 480.0   # nothing that ADDS imbalance after this
    complete_cutoff_s: float = 600.0  # nothing at all after this
    tick: float = 0.01
    pair_ceiling: Optional[float] = None   # cap on a hedge: avg cost + price
    #: BALANCE FIRST. While ANY inventory is unmatched, the side already
    #: held is not added to and its resting openings are withdrawn; only
    #: the deficient side is quoted until the book is flat again.
    balance_first: bool = False
    #: no new exposure (an ADD) is quoted below this own price. Hedges are
    #: exempt: taking risk off is never refused for being cheap.
    add_floor: float = 0.0
    #: with `balance_first`: how many CLIPS the held side may carry, held
    #: plus resting. 1 is strict -- nothing is added while anything is
    #: unmatched. 2 lets one NEW PAIR open beside an unmatched clip, and
    #: only while that clip's hedge is fully resting.
    heavy_cap_clips: float = 1.0
    #: the engine's clip in contracts, when known; 0 = unknown, and then
    #: the unmatched amount itself is taken as one clip.
    clip: float = 0.0
    # ---- FAVORITE ACCUMULATION, late in the window. Off unless enabled.
    fav_enabled: bool = False
    fav_margin: float = 0.08        # model probability - price - costs
    fav_start_s: float = 600.0      # not before this many seconds in
    fav_max_residual: float = 0.0   # contracts on the favorite, held+resting
    fav_risk_usd: float = 0.0       # dollars at risk on the favorite
    fav_fee: float = 0.0            # fee per contract, charged to the edge
    # ---- FAVORITE ONLY: the owner's live test, a profile of its own. One
    # order a window on the side the MARKET prices at or above
    # `fav_only_min_price`, not before `fav_only_start_s`; nothing else is
    # ever wanted and nothing is hedged. Off unless the profile names it.
    fav_only: bool = False
    fav_only_min_price: float = 0.75
    fav_only_max_price: float = 0.93
    fav_only_start_s: float = 450.0

    def checked(self) -> "Params":
        for name in ("margin", "adverse", "risk", "base_qty", "max_qty",
                     "size_per_cent", "max_unmatched", "tick", "add_floor",
                     "heavy_cap_clips", "clip", "fav_margin", "fav_start_s",
                     "fav_max_residual", "fav_risk_usd", "fav_fee"):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and math.isfinite(v)
                    and v >= 0):
                raise ValueError("bad parameter %s=%r" % (name, v))
        if self.tick <= 0 or self.base_qty <= 0 or self.max_qty <= 0:
            raise ValueError("tick and quantities must be positive")
        if not math.isfinite(self.min_value):
            raise ValueError("bad parameter min_value")
        return self


@dataclass(frozen=True)
class Limits:
    """An earlier copy of the executor's caps. None means NOT SUPPLIED, and
    a cap that was not supplied refuses everything that would need it."""
    cash_usd: Optional[float] = None        # most that may be resting+held
    exposure_usd: Optional[float] = None    # worst-case settlement loss
    max_orders: Optional[int] = None        # resting orders, both sides


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _floor_tick(x: float, tick: float) -> float:
    return round(math.floor(x / tick + 1e-7) * tick, 10)


# ================================================================ portfolio
def portfolio(*, up_qty: Any, up_cost: Any, dn_qty: Any, dn_cost: Any,
              orders: Sequence[Mapping[str, Any]] = ()) -> Dict[str, Any]:
    """Aggregate inventory, exact cost basis, pairs, exposure, resting orders.

    ``up_cost`` / ``dn_cost`` are DOLLARS PAID for the side, not prices.
    ``orders`` are resting maker orders: ``{id, side, price, qty}`` with the
    price in the order's OWN terms and qty still fillable. Raises ValueError
    on anything unreadable: a portfolio that cannot be read is not flat.
    """
    vals = {"up_qty": _num(up_qty), "up_cost": _num(up_cost),
            "dn_qty": _num(dn_qty), "dn_cost": _num(dn_cost)}
    for k, v in vals.items():
        if v is None or v < -EPS:
            raise ValueError("unreadable portfolio field %s=%r"
                             % (k, locals().get(k)))
    u, d = max(0.0, vals["up_qty"]), max(0.0, vals["dn_qty"])
    cu, cd = max(0.0, vals["up_cost"]), max(0.0, vals["dn_cost"])
    if (u <= EPS < cu) or (d <= EPS < cd):
        raise ValueError("cost recorded against a side with no inventory")
    rest: Dict[str, Dict[str, float]] = {s: {"qty": 0.0, "notional": 0.0,
                                             "orders": 0} for s in SIDES}
    rows: List[Dict[str, Any]] = []
    for o in orders:
        side = str(o.get("side", "")).upper()
        px, q = _num(o.get("price")), _num(o.get("qty"))
        if side not in SIDES or px is None or q is None or not (
                0 < px < 1) or q < 0:
            raise ValueError("unreadable resting order %r" % (dict(o),))
        if q <= EPS:
            continue
        rest[side]["qty"] += q
        rest[side]["notional"] += q * px
        rest[side]["orders"] += 1
        rows.append({"id": o.get("id"), "side": side, "price": px, "qty": q})
    cost = cu + cd
    matched = min(u, d)
    avg_up = (cu / u) if u > EPS else None
    avg_dn = (cd / d) if d > EPS else None
    pair_cost = ((avg_up + avg_dn) if (avg_up is not None
                                       and avg_dn is not None) else None)
    if_up, if_down = u - cost, d - cost
    # the worst that can still happen: one side's resting orders all fill
    # and the OTHER side wins. Both filling is never worse than that.
    worst_resting = min(if_down - rest[UP]["notional"],
                        if_up - rest[DOWN]["notional"])
    return {
        "up_qty": u, "dn_qty": d, "up_cost": cu, "dn_cost": cd,
        "cost": cost, "avg_up": avg_up, "avg_dn": avg_dn,
        "matched_pairs": matched, "pair_cost": pair_cost,
        "locked_pair_pnl": (matched * (1.0 - pair_cost)
                            if pair_cost is not None else 0.0),
        "unmatched": u - d,                       # + is UP-heavy
        "unmatched_side": (UP if u - d > EPS else DOWN if d - u > EPS
                           else None),
        "unmatched_cost": (abs(u - d) * (avg_up if u > d else avg_dn)
                           if abs(u - d) > EPS else 0.0),
        "settles_if_up": if_up, "settles_if_down": if_down,
        "worst_case": min(if_up, if_down),
        "worst_case_with_resting": min(if_up, if_down, worst_resting),
        "resting": rest, "orders": rows,
        "committed_usd": cost + rest[UP]["notional"] + rest[DOWN]["notional"],
    }


def from_store(store: Any, market: str) -> Dict[str, Any]:
    """The portfolio as the executor's own ledger records it.

    Reads only: positions, per-side cost basis and still-fillable orders.
    Raises on anything unreadable; the caller treats that as NO DECISION."""
    cost = store.leg_cost(market)
    orders = []
    for coid, o in (store.live_orders() or {}).items():
        if str(o.get("market")) != str(market):
            continue
        rem = o.get("remaining")
        orders.append({"id": coid, "side": o.get("side"),
                       "price": o.get("price"),
                       "qty": rem if rem is not None else o.get("qty")})
    return portfolio(up_qty=store.position(market, UP), up_cost=cost[UP],
                     dn_qty=store.position(market, DOWN), dn_cost=cost[DOWN],
                     orders=orders)


# =================================================================== belief
def win_probability(*, spot: Any, strike: Any, seconds_left: Any,
                    sigma_per_sqrt_second: Any) -> float:
    """P(UP) from strike distance and time: zero-drift lognormal.

    The same baseline the research uses. It matches the market's own price
    and does not beat it (AUC 0.856 against 0.855), which is why it is used
    with a margin and never on its own authority."""
    s, k, t, v = (_num(spot), _num(strike), _num(seconds_left),
                  _num(sigma_per_sqrt_second))
    if None in (s, k, t, v) or s <= 0 or k <= 0 or t <= 0 or v <= 0:
        raise ValueError("unusable spot/strike/horizon/volatility")
    scale = v * math.sqrt(t)
    z = (math.log(s / k) - 0.5 * scale * scale) / scale
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def belief(*, p_up: Any = None, bid: Any = None, ask: Any = None
           ) -> Dict[str, Any]:
    """The probability the controller will act on, and where it came from.

    A supplied model probability is MODEL. Without one the book's mid is
    used and labelled BOOK -- a statement that we know nothing the market
    does not. Neither readable: NONE, and the controller quotes nothing."""
    p = _num(p_up)
    if p is not None and 0.0 < p < 1.0:
        return {"p_up": p, "source": "MODEL"}
    b, a = _num(bid), _num(ask)
    if b is not None and a is not None and 0 < b < a < 1:
        return {"p_up": (b + a) / 2.0, "source": "BOOK"}
    return {"p_up": None, "source": "NONE"}


# =================================================================== scoring
def expected_settlement(port: Mapping[str, Any], p_up: float) -> float:
    return (p_up * port["settles_if_up"]
            + (1.0 - p_up) * port["settles_if_down"])


def value(port: Mapping[str, Any], p_up: float, params: Params) -> float:
    """Expected settlement less the charge for carrying unmatched risk."""
    sigma = math.sqrt(max(0.0, p_up * (1.0 - p_up)))
    return (expected_settlement(port, p_up)
            - params.risk * sigma * abs(port["unmatched"]))


def action_effect(port: Mapping[str, Any], *, side: str, price: float,
                  qty: float, p_up: float, params: Params) -> Dict[str, Any]:
    """What ONE fill of this order would do to the whole portfolio."""
    p_side = p_up if side == UP else 1.0 - p_up
    sigma = math.sqrt(max(0.0, p_up * (1.0 - p_up)))
    signed = qty if side == UP else -qty
    before, after = port["unmatched"], port["unmatched"] + signed
    d_expected = qty * (p_side - price)
    d_conservative = qty * (p_side - params.margin - params.adverse - price)
    d_risk = params.risk * sigma * (abs(after) - abs(before))
    u = port["up_qty"] + (qty if side == UP else 0.0)
    d = port["dn_qty"] + (qty if side == DOWN else 0.0)
    cost = port["cost"] + qty * price
    reduces = abs(after) < abs(before) - EPS
    return {
        "side": side, "price": price, "qty": qty,
        "role": "HEDGE" if reduces else "ADD",
        "p_side": p_side,
        "expected_settlement_change": d_expected,
        "conservative_change": d_conservative,
        "risk_charge_change": d_risk,
        "value_change": d_conservative - d_risk,
        "pairs_added": min(u, d) - port["matched_pairs"],
        "unmatched_after": after,
        "settles_if_up_after": u - cost, "settles_if_down_after": d - cost,
        "worst_case_after": min(u - cost, d - cost),
    }


def reservation_price(port: Mapping[str, Any], *, side: str, p_up: float,
                      params: Params) -> float:
    """The price at which one more base lot on this side is worth nothing."""
    p_side = p_up if side == UP else 1.0 - p_up
    sigma = math.sqrt(max(0.0, p_up * (1.0 - p_up)))
    q = params.base_qty
    signed = q if side == UP else -q
    d_abs = abs(port["unmatched"] + signed) - abs(port["unmatched"])
    return (p_side - params.margin - params.adverse
            - params.risk * sigma * d_abs / q)


def heavy_side_allowed(port: Mapping[str, Any], side: str,
                       params: Params) -> bool:
    """May the side ALREADY HELD take one more clip? (`balance_first` only.)

    Never at a cap of one clip. Above that, only when the unmatched amount
    is fully covered by a resting hedge -- so the new clip is the second
    leg of a NEW pair, not a second stranded leg -- and only while held
    plus resting on that side, plus the new clip, stays inside the cap."""
    unm = abs(port["unmatched"])
    if params.heavy_cap_clips <= 1.0 + EPS:
        return False
    clip = params.clip if params.clip > EPS else unm
    working_hedge = port["resting"][other(side)]["qty"]
    if working_hedge < unm - EPS:
        return False
    carried = unm + port["resting"][side]["qty"]
    return carried + clip <= params.heavy_cap_clips * clip + EPS


def favorite_edge(*, side: str, price: float, p_model: Any, t_off: float,
                  params: Params) -> Dict[str, Any]:
    """Is a LATE purchase of this side justified on the model's own
    probability? Nothing is assumed about favorites: the side must be the
    favorite of the model AND of the market, and the model's probability
    must exceed the price by `fav_margin` after the adverse-selection
    allowance and the fee."""
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason)
    if not params.fav_enabled:
        return no("FAVORITE_OFF")
    p = _num(p_model)
    if p is None or not (0.0 < p < 1.0):
        return no("FAVORITE_NEEDS_A_MODEL_PROBABILITY")
    if t_off < params.fav_start_s - EPS:
        return no("FAVORITE_NOT_YET", starts_s=params.fav_start_s)
    if t_off > params.complete_cutoff_s + EPS:
        return no("AFTER_COMPLETION_CUTOFF")
    p_side = p if side == UP else 1.0 - p
    if p_side <= 0.5 + EPS:
        return no("NOT_THE_FAVORITE", p_side=p_side)
    # THE MARKET MUST AGREE WHICH SIDE LEADS. A side the market prices
    # under 50c that the model calls the favorite is a disagreement about
    # direction, and on our own venue's record the market wins those: its
    # price is the better-calibrated of the two, and fills taken on a
    # large model edge settled worse than the ones passed over.
    if price < 0.5 - EPS:
        return no("NOT_THE_MARKETS_FAVORITE", p_side=p_side, price=price)
    edge = p_side - price - params.adverse - params.fav_fee
    if edge < params.fav_margin - EPS:
        return no("FAVORITE_EDGE_BELOW_MARGIN", edge=edge,
                  margin=params.fav_margin, p_side=p_side, price=price)
    return {"ok": True, "reason": None, "edge": edge, "p_side": p_side}


def favorite_room(port: Mapping[str, Any], side: str, price: float,
                  params: Params, *, own_order_qty: float = 0.0,
                  own_order_notional: float = 0.0) -> float:
    """Contracts the favorite side may still take: the smaller of the
    residual cap and the risk allocation, counting what is held unmatched
    on that side and what already rests there."""
    unm = port["unmatched"]
    held = max(0.0, unm if side == UP else -unm)
    held_cost = port["unmatched_cost"] if held > EPS else 0.0
    resting = port["resting"][side]["qty"] - own_order_qty
    resting_usd = port["resting"][side]["notional"] - own_order_notional
    by_qty = params.fav_max_residual - held - resting
    by_risk = ((params.fav_risk_usd - held_cost - resting_usd) / price
               if price > EPS else 0.0)
    return min(by_qty, by_risk)


# ================================================================== decision
def decide(port: Mapping[str, Any], *, bid: Any, ask: Any, p_up: Any = None,
           t_off: float, params: Params,
           limits: Optional[Limits] = None,
           p_model: Any = None) -> Dict[str, Any]:
    """The quotes this portfolio should have resting right now.

    ``bid`` / ``ask`` are the UP book. Returns ``quotes`` (at most one per
    side), ``skipped`` (every side not quoted, with the reason) and the
    portfolio valuation. Never raises on market input: an unusable book or
    belief returns no quotes and says why.
    """
    params = params.checked()
    limits = limits or Limits()
    out: Dict[str, Any] = {"quotes": [], "skipped": [], "t_off": t_off,
                           "portfolio": dict(port)}
    b, a = _num(bid), _num(ask)
    if b is None or a is None or not (0 < b < a < 1):
        out["skipped"] = [{"side": s, "reason": "BOOK_UNUSABLE"}
                          for s in SIDES]
        out["belief"] = {"p_up": None, "source": "NONE"}
        return out
    bel = belief(p_up=p_up, bid=b, ask=a)
    out["belief"] = bel
    p = bel["p_up"]
    if p is None:
        out["skipped"] = [{"side": s, "reason": "NO_BELIEF"} for s in SIDES]
        return out
    out["expected_settlement"] = expected_settlement(port, p)
    out["value"] = value(port, p, params)
    tick = params.tick
    unm = port["unmatched"]
    cands: List[Dict[str, Any]] = []
    for side in SIDES:
        def skip(reason: str, **kw: Any) -> None:
            out["skipped"].append(dict(kw, side=side, reason=reason))
        heavy = (unm > EPS and side == UP) or (unm < -EPS and side == DOWN)
        hedge = (unm > EPS and side == DOWN) or (unm < -EPS and side == UP)
        if t_off < params.open_after_s:
            skip("BEFORE_OPEN")
            continue
        if t_off > params.complete_cutoff_s:
            skip("AFTER_COMPLETION_CUTOFF")
            continue
        # the side's own best bid and the price that would cross
        touch = b if side == UP else round(1.0 - a, 10)
        cross = a if side == UP else round(1.0 - b, 10)
        # ============ FAVORITE ONLY. The whole decision for this profile:
        # the market's own favorite, once, from the halfway point, while
        # the book is flat and empty. No model, no pair, no hedge. Nothing
        # here assumes the favorite is worth its price.
        if params.fav_only:
            if t_off < params.fav_only_start_s - EPS:
                skip("FAVORITE_NOT_YET", starts_s=params.fav_only_start_s)
            elif t_off > params.entry_cutoff_s:
                skip("AFTER_ENTRY_CUTOFF")
            elif abs(unm) > EPS or port["matched_pairs"] > EPS:
                skip("FAVORITE_ALREADY_HELD")
            elif (port["resting"][UP]["qty"]
                    + port["resting"][DOWN]["qty"]) > EPS:
                skip("FAVORITE_ALREADY_RESTING")
            elif touch < params.fav_only_min_price - EPS:
                skip("NOT_THE_FAVORITE", touch=touch,
                     min_price=params.fav_only_min_price)
            elif touch > params.fav_only_max_price + EPS:
                skip("FAVORITE_TOO_DEAR", touch=touch,
                     max_price=params.fav_only_max_price)
            else:
                eff = action_effect(port, side=side, price=touch,
                                    qty=params.base_qty, p_up=p,
                                    params=params)
                cands.append(dict(eff, role="FAVORITE", reservation=touch,
                                  touch=touch, ticks_behind_touch=0,
                                  favorite={"market_price": touch,
                                            "min_price":
                                            params.fav_only_min_price}))
            continue
        # ============ FAVORITE ACCUMULATION, late, and only on evidence.
        # A hedge is never a favorite order: completing a pair comes first
        # and is handled below exactly as it always was. This branch is
        # for NEW exposure on the model's favorite, and it carries its own
        # caps -- the residual cap and the risk allocation -- instead of
        # the entry cutoff and balance-first, which exist to stop exposure
        # that has no such justification.
        if not hedge and params.fav_enabled:
            fpx = _floor_tick(touch + EPS, tick)
            while fpx >= cross - EPS:
                fpx = round(fpx - tick, 10)
            fav = favorite_edge(side=side, price=fpx, p_model=p_model,
                                t_off=t_off, params=params)
            if fav["ok"] and fpx >= tick - EPS:
                room = favorite_room(port, side, fpx, params)
                need = params.clip if params.clip > EPS else params.base_qty
                if room >= need - EPS:
                    eff = action_effect(port, side=side, price=fpx,
                                        qty=min(need, params.max_qty),
                                        p_up=_num(p_model), params=params)
                    cands.append(dict(
                        eff, role="FAVORITE", reservation=fav["p_side"],
                        touch=touch, ticks_behind_touch=int(round(
                            (touch - fpx) / tick)),
                        favorite={"edge": fav["edge"],
                                  "p_side": fav["p_side"],
                                  "room_contracts": room}))
                    continue
                skip("FAVORITE_CAP", room_contracts=room,
                     max_residual=params.fav_max_residual,
                     risk_usd=params.fav_risk_usd)
                continue
            if t_off >= params.fav_start_s - EPS:
                out.setdefault("favorite_declined", []).append(
                    dict(fav, side=side))
        if not hedge and t_off > params.entry_cutoff_s:
            skip("AFTER_ENTRY_CUTOFF")
            continue
        if heavy and abs(unm) >= params.max_unmatched - EPS:
            skip("UNMATCHED_CAP", unmatched=unm)
            continue
        if heavy and params.balance_first and \
                not heavy_side_allowed(port, side, params):
            skip("BALANCE_FIRST", unmatched=unm,
                 heavy_cap_clips=params.heavy_cap_clips)
            continue
        res = reservation_price(port, side=side, p_up=p, params=params)
        px = _floor_tick(res - params.min_value, tick)
        px = min(px, _floor_tick(touch + EPS, tick))
        if hedge and params.pair_ceiling is not None:
            avg = port["avg_up"] if side == DOWN else port["avg_dn"]
            if avg is not None:
                px = min(px, _floor_tick(params.pair_ceiling - avg + EPS,
                                         tick))
        while px >= cross - EPS:                 # never a crossing order
            px = round(px - tick, 10)
        if px < tick - EPS:
            skip("NO_POSITIVE_PRICE", reservation=res)
            continue
        if not hedge and px < params.add_floor - EPS:
            skip("BELOW_ADD_FLOOR", price=px, floor=params.add_floor)
            continue
        # a HEDGE may wait far behind the touch -- that is patience, and a
        # resting hedge is the only thing that can complete a pair. An ADD
        # that far back is only an order slot spent on nothing.
        if not hedge and px < touch - params.max_depth_ticks * tick - EPS:
            skip("NOT_COMPETITIVE", reservation=res, touch=touch, price=px)
            continue
        per = (p if side == UP else 1.0 - p) - params.margin \
            - params.adverse - px
        qty = params.base_qty + params.size_per_cent * max(0.0, per) * 100.0
        if hedge:
            # exactly the imbalance, up to the order cap: a hedge sized
            # past what is unmatched is a new position on the other side
            qty = min(abs(unm), params.max_qty)
        qty = min(qty, params.max_qty)
        if not hedge:
            room = params.max_unmatched - (abs(unm) if heavy else 0.0)
            qty = min(qty, room)
        if qty <= EPS:
            skip("NO_SIZE")
            continue
        eff = action_effect(port, side=side, price=px, qty=qty, p_up=p,
                            params=params)
        if eff["value_change"] < -EPS:
            skip("NEGATIVE_PORTFOLIO_VALUE", **{k: eff[k] for k in (
                "price", "qty", "value_change")})
            continue
        cands.append(dict(eff, reservation=res, touch=touch,
                          ticks_behind_touch=int(round((touch - px) / tick))))
    # the caps, applied to the most valuable quote first
    cands.sort(key=lambda c: -(c["value_change"] / c["qty"]))
    held = port["cost"]
    committed = 0.0
    placed: Dict[str, float] = {UP: 0.0, DOWN: 0.0}
    for c in cands:
        side = c["side"]
        if limits.max_orders is not None and \
                len(out["quotes"]) >= limits.max_orders:
            out["skipped"].append({"side": side, "reason": "ORDER_LIMIT"})
            continue
        if limits.cash_usd is None or limits.exposure_usd is None:
            out["skipped"].append({"side": side,
                                   "reason": "LIMITS_NOT_SUPPLIED"})
            continue
        notional = c["qty"] * c["price"]
        if held + committed + notional > limits.cash_usd + EPS:
            out["skipped"].append({"side": side, "reason": "CASH_LIMIT",
                                   "needed": held + committed + notional,
                                   "cap": limits.cash_usd})
            continue
        # worst case if THIS side's quotes fill and the other side wins
        lose = (port["settles_if_down"] if side == UP
                else port["settles_if_up"]) - placed[side] - notional
        if -lose > limits.exposure_usd + EPS:
            out["skipped"].append({"side": side, "reason": "EXPOSURE_LIMIT",
                                   "worst_case": lose,
                                   "cap": limits.exposure_usd})
            continue
        committed += notional
        placed[side] += notional
        out["quotes"].append(c)
    return out


def order_actions(quotes: Sequence[Mapping[str, Any]],
                  resting: Sequence[Mapping[str, Any]], *,
                  params: Params) -> List[Dict[str, Any]]:
    """Exact cancels and placements that turn `resting` into `quotes`.

    A resting order is KEPT when its side is still wanted within
    ``reprice_ticks`` of the target price. Anything else on that side is
    cancelled BY ITS OWN ID, and a placement is issued only for a side with
    nothing kept -- so a reprice is a cancel that must be confirmed before
    its replacement, never two orders on one side.
    """
    want = {q["side"]: q for q in quotes}
    acts: List[Dict[str, Any]] = []
    kept: Dict[str, bool] = {}
    for o in resting:
        side = str(o.get("side", "")).upper()
        tgt = want.get(side)
        px = _num(o.get("price"))
        if (tgt is not None and px is not None and not kept.get(side)
                and abs(px - tgt["price"])
                <= params.reprice_ticks * params.tick - params.tick + EPS):
            kept[side] = True
            acts.append({"op": "KEEP", "id": o.get("id"), "side": side,
                         "price": px})
        else:
            acts.append({"op": "CANCEL", "id": o.get("id"), "side": side,
                         "price": px,
                         "reason": ("SIDE_NOT_WANTED" if tgt is None
                                    else "REPRICE")})
    cancelling = {a["side"] for a in acts if a["op"] == "CANCEL"}
    for side, q in want.items():
        if kept.get(side):
            continue
        acts.append({"op": "PLACE", "side": side, "price": q["price"],
                     "qty": q["qty"], "role": q["role"],
                     "after_cancel_confirmed": side in cancelling,
                     "value_change": q["value_change"]})
    return acts


def steer(dec: Mapping[str, Any], *, approved_depth_ticks: int,
          max_extra_ticks: int = 10) -> Dict[str, Any]:
    """What the existing pair engine may be told, from one decision.

    The pair engine prices both legs at ONE depth behind their touches and
    places one leg at a time. So the steer is two things and no more:

      * which sides may OPEN now -- a side the controller does not quote is
        not opened, and a side it wants only deeper than the plan's depth
        is not opened either;
      * how many EXTRA ticks behind the owner's approved depth the plan
        should sit: never fewer. The steer can make an opening more
        patient than the approved configuration, never more aggressive.

    It says nothing about hedges: reducing an unmatched position is never
    the steer's to refuse (the executor enforces that too).
    """
    approved = max(0, int(approved_depth_ticks))
    sides: Dict[str, Dict[str, Any]] = {}
    for side in SIDES:
        why = [x.get("reason") for x in dec.get("skipped", ())
               if x.get("side") == side]
        sides[side] = {"wanted": False,
                       "reason": (why[0] if why else "NOT_QUOTED")}
    for q in dec.get("quotes", ()):
        sides[q["side"]] = {
            "wanted": True, "role": q["role"],
            "ticks_behind_touch": int(q["ticks_behind_touch"]),
            "value_change": q["value_change"], "qty": q["qty"]}
    # A HEDGE THAT IS ALREADY FULLY RESTING needs no second order. Opening
    # the deficient side AGAIN is only right as one leg of a new pair, so
    # it follows the held side: wanted when that is, and not otherwise.
    # (An unmatched amount whose hedge is NOT resting is the executor's to
    # place whatever this says.)
    port = dec.get("portfolio") or {}
    unm = _num(port.get("unmatched")) or 0.0
    for side in SIDES:
        v = sides[side]
        if not (v["wanted"] and v.get("role") == "HEDGE"):
            continue
        working = _num(((port.get("resting") or {}).get(side) or {}
                        ).get("qty")) or 0.0
        if working >= abs(unm) - EPS and not sides[other(side)]["wanted"]:
            v["wanted"] = False
            v["reason"] = "HEDGE_ALREADY_WORKING"
    adds = [v for v in sides.values() if v["wanted"] and v["role"] == "ADD"]
    extra = min((max(0, v["ticks_behind_touch"] - approved) for v in adds),
                default=0)
    extra = max(0, min(int(extra), int(max_extra_ticks)))
    plan_depth = approved + extra
    for v in sides.values():
        if v["wanted"] and v["role"] == "ADD" and \
                v["ticks_behind_touch"] > plan_depth:
            v["wanted"] = False
            v["reason"] = "WANTED_ONLY_DEEPER_THAN_THE_PLAN"
    return {"sides": sides, "extra_depth_ticks": extra,
            "approved_depth_ticks": approved, "plan_depth_ticks": plan_depth,
            "any_wanted": any(v["wanted"] for v in sides.values()),
            "belief": dict(dec.get("belief") or {})}


def withdrawals(port: Mapping[str, Any], *, p_up: Any, params: Params,
                hysteresis_ticks: int = 2, t_off: Optional[float] = None,
                p_model: Any = None) -> List[Dict[str, Any]]:
    """Resting OPENING orders the controller would no longer pay for.

    This is the lever that decides fill selection: a maker bid is filled
    when the market moves through it, so the only defence is not to be
    there. An order is named here when its own price sits more than
    ``hysteresis_ticks`` above what the controller would now pay for that
    side -- the band is what stops a quote being placed and pulled on the
    same threshold.

    A HEDGE IS NEVER NAMED. An order that reduces the unmatched position is
    left alone whatever the belief says. With no belief at all (``p_up``
    None) every opening order is named: no reference, no opening quote.
    """
    params = params.checked()
    p = _num(p_up)
    out: List[Dict[str, Any]] = []
    unm = port["unmatched"]
    for o in port.get("orders", ()):
        side = o["side"]
        hedge = (unm > EPS and side == DOWN) or (unm < -EPS and side == UP)
        if hedge:
            continue
        if params.fav_only:
            # the one favorite order rests until it fills, expires or the
            # terminal cancels it; this profile withdraws nothing
            continue
        heavy = (unm > EPS and side == UP) or (unm < -EPS and side == DOWN)
        # ---- a late FAVORITE order: kept while it is still justified and
        # inside its caps, named the moment it is not. An opening order
        # resting after the entry cutoff can only be a favorite order.
        if params.fav_enabled and t_off is not None and \
                t_off > params.entry_cutoff_s:
            relaxed = replace(params, fav_margin=max(
                0.0, params.fav_margin - hysteresis_ticks * params.tick))
            fav = favorite_edge(side=side, price=o["price"], p_model=p_model,
                                t_off=t_off, params=relaxed)
            room = favorite_room(port, side, o["price"], params,
                                 own_order_qty=o["qty"],
                                 own_order_notional=o["qty"] * o["price"])
            if fav["ok"] and room >= o["qty"] - EPS:
                continue
            out.append({"id": o.get("id"), "side": side, "price": o["price"],
                        "reservation": None,
                        "reason": "FAVORITE_NO_LONGER_JUSTIFIED",
                        "why": (fav["reason"] if not fav["ok"]
                                else "FAVORITE_CAP")})
            continue
        if heavy and params.balance_first:
            # inventory is unmatched and this order adds to it. Kept only
            # when the cap allows a new pair beside it: the hedge fully
            # resting, and held plus resting on this side inside the cap.
            clip = params.clip if params.clip > EPS else abs(unm)
            carried = abs(unm) + port["resting"][side]["qty"]
            hedged = port["resting"][other(side)]["qty"] >= abs(unm) - EPS
            if not (params.heavy_cap_clips > 1.0 + EPS and hedged
                    and carried <= params.heavy_cap_clips * clip + EPS):
                out.append({"id": o.get("id"), "side": side,
                            "price": o["price"], "reservation": None,
                            "reason": "BALANCE_FIRST"})
                continue
        if p is None or not (0.0 < p < 1.0):
            out.append({"id": o.get("id"), "side": side, "price": o["price"],
                        "reservation": None, "reason": "NO_BELIEF"})
            continue
        res = reservation_price(port, side=side, p_up=p, params=params)
        if o["price"] > res + hysteresis_ticks * params.tick + EPS:
            out.append({"id": o.get("id"), "side": side, "price": o["price"],
                        "reservation": res,
                        "reason": "PRICED_ABOVE_WHAT_THE_PORTFOLIO_WOULD_PAY"})
    return out


#: WHAT THE RUNNER USES. Chosen on the FIRST part of the integrated replay
#: only (the `--integrated` run under analysis/l5) and scored on the
#: second: a six-cent haircut on every opening and a heavy
#: charge for unmatched inventory. A test pins this to the committed result.
PRODUCTION = {"margin": 0.03, "adverse": 0.03, "risk": 0.30}


#: INVENTORY BALANCING, from L5's own venue-confirmed fills. Over his 118
#: settled BTC15m windows of 2026-11-09/10, taking his fills only while they
#: kept unmatched inventory to about one clip earned +0.52c a contract
#: [+0.17, +0.87] against +0.75c [-0.09, +1.63] as traded, with the worst
#: window cut from -$119 to -$39; refusing new exposure under 30c raised it
#: further. Capping a hedge by pair cost made it WORSE, so hedges carry no
#: pair ceiling here. Both sides are quoted at the touch on the book's own
#: mid: the live index showed no ability to pick a side, so it is not used.
#: See analysis/l5/L5_STREAM_REPLAY.md. A test pins this to that result.
#:
#: HEAVY CAP OF TWO CLIPS, after the live campaign of 2026-11-10 21:30Z.
#: At one clip the steer refused the held side's leg of every new pair for
#: as long as one clip was unmatched (87 opening ticks at a 0.94-0.95 pair)
#: and let the deficient side open a SECOND order beside its own resting
#: hedge. At two clips a new pair may open beside one unmatched clip, but
#: only while that clip's hedge is fully resting; on L5's stream a two-clip
#: cap earned +0.67c a contract [+0.26, +1.10] against +0.52c at one.
BALANCE = {"margin": 0.0, "adverse": 0.0, "risk": 0.0, "balance_first": True,
           "add_floor": 0.30, "complete_cutoff_s": 840.0,
           "heavy_cap_clips": 2.0}

#: FAVORITE ACCUMULATION, an extension of BALANCE. OFF unless the owner
#: sets L5_PORTFOLIO_FAVORITE to exactly ON; every number is the owner's,
#: read from the environment inside hard bounds, and ONE unreadable or
#: out-of-range value turns the whole feature off rather than guessing.
FAVORITE_ENV = "L5_PORTFOLIO_FAVORITE"
FAVORITE_KNOBS = {
    # env name: (parameter, default, lowest, highest)
    "L5_FAV_MARGIN": ("fav_margin", 0.08, 0.02, 0.50),
    "L5_FAV_START_S": ("fav_start_s", 600.0, 0.0, 840.0),
    "L5_FAV_MAX_RESIDUAL": ("fav_max_residual", 0.30, 0.0, 1.00),
    "L5_FAV_RISK_USD": ("fav_risk_usd", 0.25, 0.0, 0.50),
    "L5_FAV_ADVERSE": ("adverse_for_favorite", 0.03, 0.0, 0.20),
}


def favorite_overrides() -> Dict[str, Any]:
    """The owner's favorite-accumulation settings, or {} when it is off.

    The adverse-selection allowance is carried in `fav_fee`, so it is
    charged against the favorite's edge and leaves every other quote of
    the BALANCE profile exactly as it was."""
    if os.getenv(FAVORITE_ENV, "") != "ON":
        return {}
    got: Dict[str, Any] = {"fav_enabled": True}
    for env, (name, default, lo, hi) in FAVORITE_KNOBS.items():
        raw = (os.getenv(env) or "").strip()
        v = _num(raw) if raw else default
        if v is None or v < lo - EPS or v > hi + EPS:
            return {}
        got["fav_fee" if name == "adverse_for_favorite" else name] = v
    return got
PROFILE_ENV = "L5_PORTFOLIO_PROFILE"
MODEL_EDGE_PROFILE, BALANCE_PROFILE = "MODEL_EDGE", "BALANCE"
FAVORITE_ONLY_PROFILE = "FAVORITE_ONLY"


def profile() -> str:
    """BALANCE or FAVORITE_ONLY only when the owner names it exactly;
    otherwise unchanged."""
    raw = os.getenv(PROFILE_ENV, "")
    if raw in (BALANCE_PROFILE, FAVORITE_ONLY_PROFILE):
        return raw
    return MODEL_EDGE_PROFILE


def profile_uses_model() -> bool:
    return profile() == MODEL_EDGE_PROFILE


def favorite_only() -> bool:
    """Is this process in the owner's favorite-only test? Read by the
    executor's submission gate as well as here, so the two cannot disagree
    about whether anything but the favorite may be ordered."""
    return profile() == FAVORITE_ONLY_PROFILE


#: FAVORITE ONLY. Every number is the owner's, inside hard bounds, and ONE
#: unreadable or out-of-range value makes the profile want nothing at all.
FAVORITE_ONLY_KNOBS = {
    # env name: (parameter, default, lowest, highest)
    "L5_FAVONLY_MIN_PRICE": ("fav_only_min_price", 0.75, 0.60, 0.93),
    "L5_FAVONLY_MAX_PRICE": ("fav_only_max_price", 0.93, 0.60, 0.93),
    "L5_FAVONLY_START_S": ("fav_only_start_s", 450.0, 300.0, 780.0),
}
FAVORITE_ONLY = {"margin": 0.0, "adverse": 0.0, "risk": 0.0,
                 "fav_only": True}


def favorite_only_overrides() -> Dict[str, Any]:
    """The profile's numbers, or a profile that can never want an order."""
    got: Dict[str, Any] = {}
    for env, (name, default, lo, hi) in FAVORITE_ONLY_KNOBS.items():
        raw = (os.getenv(env) or "").strip()
        v = default if not raw else _num(raw)
        if v is None or not (lo - EPS <= v <= hi + EPS):
            # refuse everything: a start that never comes
            return {"fav_only_start_s": float("inf")}
        got[name] = float(v)
    if got["fav_only_max_price"] < got["fav_only_min_price"] - EPS:
        return {"fav_only_start_s": float("inf")}
    return got


#: THE BALANCE PROFILE'S FLOOR ON NEW EXPOSURE, the owner's variable.
#:
#: 0.30 unless this names a price from 0.05 to 0.50. It bounds only an ADD
#: -- the first leg of new exposure -- and never a hedge, exactly as
#: `add_floor` always did. Lowering it lets the controller quote a pair
#: whose cheap leg is under 30 cents (a 0.90 / 0.06 pair late in a window),
#: which the 0.96 opening ceiling still has to admit. Unset, unreadable or
#: out of range is 0.30: a typo cannot remove the floor.
BALANCE_ADD_FLOOR_ENV = "L5_BALANCE_ADD_FLOOR"


def balance_add_floor() -> float:
    raw = (os.getenv(BALANCE_ADD_FLOOR_ENV) or "").strip()
    default = float(BALANCE["add_floor"])
    if not raw:
        return default
    v = _num(raw)
    if v is None or not (0.05 - EPS <= v <= 0.50 + EPS):
        return default
    return float(v)


def production_params(tick: Any = None) -> Params:
    t = _num(tick)
    if profile() == BALANCE_PROFILE:
        knobs = dict(BALANCE, **favorite_overrides())
        knobs["add_floor"] = balance_add_floor()
    elif profile() == FAVORITE_ONLY_PROFILE:
        knobs = dict(FAVORITE_ONLY, **favorite_only_overrides())
    else:
        knobs = dict(PRODUCTION)
    return Params(tick=(t if t and t > 0 else 0.01), **knobs).checked()


def with_overrides(params: Params, **kw: Any) -> Params:
    return replace(params, **kw).checked()
