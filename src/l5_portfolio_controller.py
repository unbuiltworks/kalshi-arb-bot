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
OFF, SHADOW = "OFF", "SHADOW"
EPS = 1e-9


def mode() -> str:
    """OFF unless the owner sets the variable to exactly SHADOW.

    There is deliberately no value that lets this module's output reach the
    venue: SHADOW records what it would quote and nothing else."""
    return SHADOW if os.getenv(MODE_ENV, "") == SHADOW else OFF


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

    def checked(self) -> "Params":
        for name in ("margin", "adverse", "risk", "base_qty", "max_qty",
                     "size_per_cent", "max_unmatched", "tick"):
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


# ================================================================== decision
def decide(port: Mapping[str, Any], *, bid: Any, ask: Any, p_up: Any = None,
           t_off: float, params: Params,
           limits: Optional[Limits] = None) -> Dict[str, Any]:
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
        if not hedge and t_off > params.entry_cutoff_s:
            skip("AFTER_ENTRY_CUTOFF")
            continue
        if heavy and abs(unm) >= params.max_unmatched - EPS:
            skip("UNMATCHED_CAP", unmatched=unm)
            continue
        # the side's own best bid and the price that would cross
        touch = b if side == UP else round(1.0 - a, 10)
        cross = a if side == UP else round(1.0 - b, 10)
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
            qty = max(qty, min(abs(unm), params.max_qty))
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


def with_overrides(params: Params, **kw: Any) -> Params:
    return replace(params, **kw).checked()
