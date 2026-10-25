"""Signed-inventory action evaluator: BUY UP / BUY DOWN / WAIT / CANCEL-REPRICE.

WHY THIS EXISTS. The existing controller is a PAIRING engine: it opens one leg
and then tries to complete the complement, so its P&L is a function of pair
cost and completion rate, not of whether holding the leg was worth holding.
That is why a signal measured real out-of-sample (book depth imbalance predicts
the mid, t = +6.41, 48 of 54 markets) does not convert through it.

This module replaces the "always complete the pair" reflex with a per-decision
marginal test. It holds SIGNED inventory on purpose when the incremental
risk-adjusted expected net value of doing so is positive, reduces it when
reduction is the better marginal trade, and WAITS otherwise. Completion is one
candidate among four, never an obligation.

WHAT IT REFUSES TO DO. It does not manufacture an edge. The probability input
carries its own uncertainty and staleness; if the estimate is stale, or its
uncertainty exceeds the configured ceiling, every BUY candidate is rejected and
the decision is WAIT. A WAIT that comes from absent information is recorded
with that reason, so an absent signal can never be read back as a flat market.

CONVENTIONS (identical to analysis/profit_search/replay_b.py, which is the
replay harness this engine is driven by, so the two cannot disagree):
  * The wire is YES. Own cost is what WE pay per contract, in dollars.
  * taker buy UP   = lift the YES ask  = 1 - no_bid,  depth no_bid_qty
  * taker buy DOWN = lift the NO  ask  = 1 - yes_bid, depth yes_bid_qty
  * maker buy UP   = rest a YES bid at yes_bid  (own cost yes_bid)
  * maker buy DOWN = rest a NO  bid at no_bid   (own cost no_bid)
  * Settlement pays 1.00 on the winning side, 0.00 on the loser. A matched
    UP/DOWN pair therefore redeems 1.00 whichever way the contract resolves.

ALL MONEY IS Decimal. Floats are used only for timestamps.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from decimal import Decimal as D, ROUND_CEILING
from typing import Any, Callable, List, Optional, Sequence

LABEL = "L5_INVENTORY_ENGINE_V1"

# ---- actions -----------------------------------------------------------------
BUY_UP = "BUY_UP"
BUY_DOWN = "BUY_DOWN"
WAIT = "WAIT"
CANCEL = "CANCEL"
REPRICE = "REPRICE"

MAKER = "MAKER"
TAKER = "TAKER"

# ---- fee model (authoritative constants shared with the replay harness) ------
TAKER_THETA = D("0.0695")
MAKER_THETA = D("-0.0125")   # negative: maker is rebated
PER_ORDER = D("0.01")

ZERO = D(0)
ONE = D(1)


def fee_dollars(theta: D, qty: D, wire_px: D) -> D:
    """Venue fee in dollars for `qty` contracts at wire (YES) price `wire_px`.

    Rounds AWAY from us: a fee rounds up, a rebate rounds down. Convex in price,
    maximised at 0.50.
    """
    raw = theta * qty * wire_px * (ONE - wire_px)
    if raw > 0:
        return raw.quantize(D("0.01"), rounding=ROUND_CEILING)
    if raw < 0:
        return -((-raw).quantize(D("0.01")))
    return ZERO


# ---- state -------------------------------------------------------------------
@dataclass(frozen=True)
class Inventory:
    """Filled inventory and its cost basis. Costs are TOTAL dollars paid."""

    up_qty: D = ZERO
    dn_qty: D = ZERO
    up_cost: D = ZERO
    dn_cost: D = ZERO
    fees_paid: D = ZERO

    @property
    def resid(self) -> D:
        """Signed directional exposure: >0 long UP, <0 long DOWN."""
        return self.up_qty - self.dn_qty

    @property
    def matched(self) -> D:
        return min(self.up_qty, self.dn_qty)

    @property
    def cost(self) -> D:
        return self.up_cost + self.dn_cost

    def up_basis(self) -> Optional[D]:
        return (self.up_cost / self.up_qty) if self.up_qty > 0 else None

    def dn_basis(self) -> Optional[D]:
        return (self.dn_cost / self.dn_qty) if self.dn_qty > 0 else None

    def add(self, side: str, qty: D, own: D, fee: D) -> "Inventory":
        if side == "UP":
            return replace(self, up_qty=self.up_qty + qty,
                           up_cost=self.up_cost + qty * own,
                           fees_paid=self.fees_paid + fee)
        return replace(self, dn_qty=self.dn_qty + qty,
                       dn_cost=self.dn_cost + qty * own,
                       fees_paid=self.fees_paid + fee)

    def settle(self, yes_won: int) -> D:
        """Realised net cashflow at settlement. Complete accounting, no residual
        left implicit: matched redeems 1.00 per pair, the residual redeems only
        if it is on the winning side, and every fee already paid is subtracted.
        """
        redeem = self.matched + (
            abs(self.resid)
            if (self.resid > 0 and yes_won == 1) or (self.resid < 0 and yes_won == 0)
            else ZERO
        )
        return redeem - self.cost - self.fees_paid

    def worst_case_settlement(self) -> D:
        """Net cashflow under the worse of the two outcomes. This is the number
        the risk limits are enforced against -- never an expected value.
        """
        return min(self.settle(1), self.settle(0))


@dataclass(frozen=True)
class Book:
    """Executable book state at decision time, own-cost convention."""

    yes_bid: D
    yes_bid_qty: D
    no_bid: D
    no_bid_qty: D
    ts: float = 0.0

    # --- taker side: what we pay to lift, and how deep it is
    def taker_own(self, side: str) -> D:
        return (ONE - self.no_bid) if side == "UP" else (ONE - self.yes_bid)

    def taker_depth(self, side: str) -> D:
        return self.no_bid_qty if side == "UP" else self.yes_bid_qty

    # --- maker side: where we may rest at the touch, and the queue ahead of us
    def maker_own(self, side: str) -> D:
        return self.yes_bid if side == "UP" else self.no_bid

    def maker_queue(self, side: str) -> D:
        return self.yes_bid_qty if side == "UP" else self.no_bid_qty

    def wire(self, side: str, own: D) -> D:
        """Wire (YES) price of our own-cost `own` on `side`. A NO bid at own px
        is an offer to sell YES at 1-px."""
        return own if side == "UP" else (ONE - own)

    @property
    def spread(self) -> D:
        return ONE - self.yes_bid - self.no_bid

    @property
    def mid(self) -> D:
        """Market-implied YES mid: (yes_bid + yes_ask)/2, yes_ask = 1 - no_bid."""
        return (self.yes_bid + (ONE - self.no_bid)) / D(2)

    @property
    def imbalance(self) -> D:
        tot = self.yes_bid_qty + self.no_bid_qty
        return (self.yes_bid_qty - self.no_bid_qty) / tot if tot > 0 else ZERO


@dataclass(frozen=True)
class Prob:
    """A settlement-probability estimate that carries its own trustworthiness.

    p_up      estimated P(YES settles 1)
    sigma     1-sd uncertainty of p_up, in probability units
    age_s     seconds since the inputs this was computed from
    stale     the estimator itself declares the inputs unusable
    source    provenance string, carried into the ledger
    """

    p_up: D
    sigma: D
    age_s: float = 0.0
    stale: bool = False
    source: str = "UNSPECIFIED"

    @property
    def p_dn(self) -> D:
        return ONE - self.p_up


@dataclass(frozen=True)
class RestingOrder:
    side: str
    kind: str
    own: D
    qty: D
    filled: D = ZERO
    t_live: float = 0.0
    queue: D = ZERO

    @property
    def remaining(self) -> D:
        return self.qty - self.filled


@dataclass(frozen=True)
class Limits:
    """Hard limits. Every one of these is a refusal, never a penalty term."""

    max_side_qty: D = D(200)          # per market, per side
    max_resid_qty: D = D(50)          # signed directional exposure cap
    max_capital: D = D(80)            # dollars of cost basis per market
    max_worst_case_loss: D = D(40)    # worst-case settlement loss per market
    daily_loss_remaining: D = D(5000)
    max_concurrent_orders: int = 1


@dataclass(frozen=True)
class Config:
    # --- edge thresholds, in dollars of risk-adjusted EV for the whole clip
    min_edge: D = D("0.02")           # reduce / neutral-ish trades
    min_edge_extend: D = D("0.06")    # STRICTLY higher bar to add exposure
    # --- risk aversion: dollars of EV demanded per dollar of settlement sd added.
    # kappa = 1.0 would demand a dollar of edge per dollar of outcome sd, which
    # refuses every directional trade a 15m up/down market can offer; the replay
    # sweeps this parameter and reports the economics at each value.
    kappa: D = D("0.10")
    # --- probability trust
    max_sigma: D = D("0.08")
    max_prob_age_s: float = 30.0
    # --- execution realism
    maker_fill_ref_s: float = 120.0   # horizon a resting order is given
    adverse_shift: D = D("0.015")     # maker fills are selected against: p moves
                                      # against the side we got filled on
    taker_slip: D = D("0.00")         # own-cost slippage assumed on a cross
    min_qty: D = D("0.50")
    clip_qty: D = D(5)                # target contracts per action
    # --- lifecycle
    order_ttl_s: float = 120.0
    reprice_ticks: D = D("0.01")      # reprice if the touch moved at least this
    tte_min_open_s: float = 240.0     # do not open a new exposure this late
    tte_min_extend_s: float = 300.0   # do not ADD exposure this late
    allow_taker: bool = True
    allow_extend: bool = True
    # --- VENUE EXECUTION MODEL. None keeps the generic modelled behaviour that
    # the unit tests pin. A provider (see pmus_execution_model) replaces BOTH
    # the fee model and the invented fill probability with the venue's real
    # schedule and a P(fill) calibrated on our own submitted orders. When the
    # provider cannot estimate P(fill) at the rest horizon it returns None, and
    # that is a REFUSAL -- never zero, never the last observed value.
    venue: Any = None
    #: how long a maker order is assumed to rest when pricing P(fill). Capped
    #: to the provider's estimable horizon so a decision is never priced on an
    #: extrapolation.
    maker_rest_s: float = 30.0


# ---- candidate ---------------------------------------------------------------
@dataclass
class Candidate:
    action: str
    side: Optional[str] = None
    kind: Optional[str] = None
    own: Optional[D] = None
    qty: D = ZERO
    fee: D = ZERO
    fill_prob: D = ONE
    gross_ev: D = ZERO            # EV before risk adjustment, after fee+fill prob
    risk_before: D = ZERO
    risk_after: D = ZERO
    risk_adj_ev: D = ZERO
    worst_case_after: D = ZERO
    resid_before: D = ZERO
    resid_after: D = ZERO
    extends: bool = False
    ok: bool = False
    reason: str = ""

    def as_row(self) -> dict:
        return dict(
            action=self.action, side=self.side, kind=self.kind,
            own=(str(self.own) if self.own is not None else None),
            qty=str(self.qty), fee=str(self.fee), fill_prob=str(self.fill_prob),
            gross_ev=str(self.gross_ev), risk_adj_ev=str(self.risk_adj_ev),
            risk_before=str(self.risk_before), risk_after=str(self.risk_after),
            worst_case_after=str(self.worst_case_after),
            resid_before=str(self.resid_before), resid_after=str(self.resid_after),
            extends=self.extends, ok=self.ok, reason=self.reason,
        )


@dataclass
class Decision:
    action: str
    chosen: Optional[Candidate]
    candidates: List[Candidate] = field(default_factory=list)
    reason: str = ""
    # ledger context
    t: float = 0.0
    tte: float = 0.0
    market: str = ""
    p_up: D = ZERO
    sigma: D = ZERO
    prob_source: str = ""
    inv_before: Optional[Inventory] = None
    book: Optional[Book] = None

    def as_row(self) -> dict:
        b = self.book
        inv = self.inv_before
        return dict(
            label=LABEL, market=self.market, t=self.t, tte=self.tte,
            p_up=str(self.p_up), sigma=str(self.sigma), prob_source=self.prob_source,
            yes_bid=(str(b.yes_bid) if b else None),
            no_bid=(str(b.no_bid) if b else None),
            yes_bid_qty=(str(b.yes_bid_qty) if b else None),
            no_bid_qty=(str(b.no_bid_qty) if b else None),
            inv_up=(str(inv.up_qty) if inv else None),
            inv_dn=(str(inv.dn_qty) if inv else None),
            inv_resid=(str(inv.resid) if inv else None),
            inv_cost=(str(inv.cost) if inv else None),
            worst_case_before=(str(inv.worst_case_settlement()) if inv else None),
            action=self.action, reason=self.reason,
            chosen=(self.chosen.as_row() if self.chosen else None),
            rejected=[c.as_row() for c in self.candidates if c is not self.chosen],
        )


# ---- risk ---------------------------------------------------------------------
def settlement_risk(resid: D, p_up: D, sigma: D) -> D:
    """Standard deviation of the settlement cashflow of a signed residual, in
    dollars, inflated by our own uncertainty about p.

    A matched pair contributes nothing: it redeems 1.00 either way. Only the
    residual is uncertain, so risk is |resid| * (sd of the Bernoulli outcome +
    the sd our probability estimate itself carries).
    """
    r = abs(resid)
    if r == 0:
        return ZERO
    p = float(max(D(0), min(ONE, p_up)))
    bern = D(str(math.sqrt(max(0.0, p * (1.0 - p)))))
    return r * (bern + sigma)


def maker_fill_prob(book: Book, side: str, own: D, qty: D, tte: float,
                    cfg: Config) -> D:
    """Probability a resting maker order of `qty` fills before it is cancelled.

    Deliberately crude but never optimistic, and NEVER inferred from a print
    merely touching our price: it is a function of the queue ahead of us, our
    own size, and how much of the configured horizon the window still has. An
    order behind the touch gets zero -- we do not model improving into a queue
    we are not at the front of.
    """
    touch = book.maker_own(side)
    if own < touch:                     # resting behind the touch
        return ZERO
    if own > touch:                     # would cross; not a maker order at all
        return ZERO
    queue = book.maker_queue(side)
    horizon = max(0.0, min(tte, cfg.maker_fill_ref_s))
    if horizon <= 0:
        return ZERO
    # fraction of the horizon available, damped by how much depth sits ahead
    time_frac = D(str(horizon / cfg.maker_fill_ref_s))
    ahead = queue + qty
    depth_frac = qty / ahead if ahead > 0 else ZERO
    p = time_frac * depth_frac
    return max(ZERO, min(ONE, p))


# ---- candidate construction ---------------------------------------------------
def _buy_candidate(side: str, kind: str, inv: Inventory, book: Book, prob: Prob,
                   limits: Limits, cfg: Config, tte: float) -> Candidate:
    resid_before = inv.resid
    own = book.taker_own(side) + cfg.taker_slip if kind == TAKER else book.maker_own(side)
    depth = book.taker_depth(side) if kind == TAKER else book.maker_queue(side)

    c = Candidate(action=(BUY_UP if side == "UP" else BUY_DOWN), side=side,
                  kind=kind, own=own, resid_before=resid_before)

    if own <= 0 or own >= ONE:
        c.reason = "PRICE_OUT_OF_RANGE"
        return c

    # --- size: clip, bounded by executable depth and by every hard limit
    qty = cfg.clip_qty
    if kind == TAKER:
        qty = min(qty, depth)                       # partial fill on thin depth
    held_side = inv.up_qty if side == "UP" else inv.dn_qty
    qty = min(qty, limits.max_side_qty - held_side)
    room_cap = (limits.max_capital - inv.cost) / own if own > 0 else ZERO
    qty = min(qty, room_cap)

    resid_after_full = resid_before + (qty if side == "UP" else -qty)
    extends = abs(resid_after_full) > abs(resid_before)
    c.extends = extends
    if extends:
        # cap the clip so the residual limit is never breached
        signed_room = limits.max_resid_qty - abs(resid_before)
        if signed_room <= 0:
            c.reason = "RESID_LIMIT"
            return c
        qty = min(qty, signed_room)

    if qty < cfg.min_qty:
        c.reason = "SIZE_BELOW_MIN"
        return c
    c.qty = qty

    # --- fees and P(fill): the venue's own schedule and our own measured
    # fill outcomes when a provider is supplied; the generic model otherwise.
    wire = book.wire(side, own)
    if cfg.venue is not None:
        c.fee = (cfg.venue.taker_fee(qty, wire) if kind == TAKER
                 else cfg.venue.maker_fee(qty, wire))
        if kind == TAKER:
            c.fill_prob = ONE
        else:
            if own < book.maker_own(side) or own > book.maker_own(side):
                c.reason = "NO_FILL_PATH"
                return c
            rest = min(cfg.maker_rest_s, tte)
            pf = cfg.venue.p_fill(
                leg=cfg.venue.leg_for(resid_before=resid_before, side=side),
                rest_s=rest)
            if pf is None:
                # beyond the horizon our own orders have ever reached. The only
                # instrument that can settle it is a live run that rests longer.
                c.reason = "FILL_UNESTIMABLE"
                return c
            c.fill_prob = D(str(round(pf, 6)))
    else:
        theta = TAKER_THETA if kind == TAKER else MAKER_THETA
        c.fee = fee_dollars(theta, qty, wire) + PER_ORDER
        c.fill_prob = (ONE if kind == TAKER
                       else maker_fill_prob(book, side, own, qty, tte, cfg))
    if c.fill_prob <= 0:
        c.reason = "NO_FILL_PATH"
        return c

    # --- probability used for THIS side, shifted against us for maker
    # adverse selection: if we get filled resting, the world moved first.
    p = prob.p_up
    if kind == MAKER:
        p = p - cfg.adverse_shift if side == "UP" else p + cfg.adverse_shift
    p = max(D(0), min(ONE, p))
    win_p = p if side == "UP" else (ONE - p)

    # --- expected value of the increment, fee-inclusive, fill-probability weighted
    ev_if_filled = qty * (win_p - own) - c.fee
    c.gross_ev = ev_if_filled * c.fill_prob

    # --- risk adjustment on the residual, and the hard worst-case gate
    resid_after = resid_before + (qty if side == "UP" else -qty)
    c.resid_after = resid_after
    c.risk_before = settlement_risk(resid_before, prob.p_up, prob.sigma)
    c.risk_after = settlement_risk(resid_after, prob.p_up, prob.sigma)
    c.risk_adj_ev = c.gross_ev - cfg.kappa * (c.risk_after - c.risk_before) * c.fill_prob

    inv_after = inv.add(side, qty, own, c.fee)
    c.worst_case_after = inv_after.worst_case_settlement()

    # ---- gates, in refusal order ------------------------------------------
    if prob.stale:
        c.reason = "PROB_STALE"
        return c
    if prob.age_s > cfg.max_prob_age_s:
        c.reason = "PROB_TOO_OLD"
        return c
    if prob.sigma > cfg.max_sigma:
        c.reason = "PROB_UNCERTAIN"
        return c
    if kind == TAKER and not cfg.allow_taker:
        c.reason = "TAKER_DISABLED"
        return c
    if extends and not cfg.allow_extend:
        c.reason = "EXTEND_DISABLED"
        return c
    if c.worst_case_after < -limits.max_worst_case_loss:
        c.reason = "WORST_CASE_LIMIT"
        return c
    if inv_after.cost > limits.max_capital:
        c.reason = "CAPITAL_LIMIT"
        return c
    if limits.daily_loss_remaining <= 0:
        c.reason = "DAILY_LOSS_HALT"
        return c
    if extends and tte < cfg.tte_min_extend_s:
        c.reason = "TOO_LATE_TO_EXTEND"
        return c
    if (not extends) and resid_before == 0 and tte < cfg.tte_min_open_s:
        c.reason = "TOO_LATE_TO_OPEN"
        return c

    bar = cfg.min_edge_extend if extends else cfg.min_edge
    if c.risk_adj_ev < bar:
        c.reason = f"EDGE_BELOW_BAR({bar})"
        return c

    c.ok = True
    c.reason = "EXTEND_OK" if extends else "REDUCE_OK"
    return c


def _cancel_candidate(order: RestingOrder, inv: Inventory, book: Book,
                      prob: Prob, limits: Limits, cfg: Config,
                      t: float, tte: float) -> Candidate:
    """Should a live resting order stay, move, or die?

    A resting order is re-tested against the CURRENT book and probability every
    decision point. It dies when the reason it was placed has gone: its price is
    no longer the touch, its edge has evaporated, its remaining size no longer
    fits the limits, or it has outlived its TTL.
    """
    c = Candidate(action=CANCEL, side=order.side, kind=order.kind,
                  own=order.own, qty=order.remaining,
                  resid_before=inv.resid, resid_after=inv.resid)
    c.risk_before = c.risk_after = settlement_risk(inv.resid, prob.p_up, prob.sigma)
    c.worst_case_after = inv.worst_case_settlement()

    if (t - order.t_live) >= cfg.order_ttl_s:
        c.ok, c.reason = True, "TTL_EXPIRED"
        return c
    if prob.stale or prob.sigma > cfg.max_sigma:
        c.ok, c.reason = True, "PROB_UNUSABLE_WHILE_RESTING"
        return c

    touch = book.maker_own(order.side)
    if order.own < touch - cfg.reprice_ticks / D(2):
        c.action = REPRICE
        c.ok, c.reason = True, "TOUCH_MOVED_AWAY"
        return c
    if order.own > touch:
        c.ok, c.reason = True, "WOULD_CROSS"
        return c

    # would we still place this order, at this price, right now?
    live = _buy_candidate(order.side, MAKER, inv, book, prob, limits, cfg, tte)
    if not live.ok:
        c.ok, c.reason = True, f"EDGE_GONE({live.reason})"
        return c

    c.reason = "KEEP"
    return c


# ---- the evaluator ------------------------------------------------------------
def evaluate(*, market: str, t: float, tte: float, inv: Inventory, book: Book,
             prob: Prob, resting: Sequence[RestingOrder] = (),
             limits: Limits = Limits(), cfg: Config = Config()) -> Decision:
    """Evaluate every action available at one decision point and pick the best.

    Pure: no I/O, no clock, no venue. Everything it is allowed to know is an
    argument, which is what makes the replay timestamp-causal by construction.

    Returns a Decision carrying the chosen action AND every rejected
    alternative with its EV, risk, worst case and refusal reason, so the ledger
    records what was declined as well as what was done.
    """
    d = Decision(action=WAIT, chosen=None, t=t, tte=tte, market=market,
                 p_up=prob.p_up, sigma=prob.sigma, prob_source=prob.source,
                 inv_before=inv, book=book)

    # 1. lifecycle first: a live order is re-tested before anything new is sized.
    for o in resting:
        cc = _cancel_candidate(o, inv, book, prob, limits, cfg, t, tte)
        d.candidates.append(cc)
        if cc.ok:
            d.action, d.chosen, d.reason = cc.action, cc, cc.reason
            return d

    # 2. with an order still live we hold the slot; no new order is sized.
    if len(resting) >= limits.max_concurrent_orders:
        d.reason = "ORDER_SLOT_HELD"
        return d

    # 3. the four buy candidates: both sides, both execution types.
    kinds = [MAKER] + ([TAKER] if cfg.allow_taker else [])
    for side in ("UP", "DOWN"):
        for kind in kinds:
            d.candidates.append(
                _buy_candidate(side, kind, inv, book, prob, limits, cfg, tte))

    viable = [c for c in d.candidates if c.ok and c.action in (BUY_UP, BUY_DOWN)]
    if not viable:
        # WAIT, with the most informative refusal reason rather than a bare WAIT
        reasons = [c.reason for c in d.candidates]
        for pref in ("PROB_STALE", "PROB_TOO_OLD", "PROB_UNCERTAIN",
                     "DAILY_LOSS_HALT", "WORST_CASE_LIMIT", "RESID_LIMIT",
                     "CAPITAL_LIMIT", "FILL_UNESTIMABLE"):
            if pref in reasons:
                d.reason = pref
                return d
        d.reason = reasons[0] if reasons else "NO_CANDIDATES"
        return d

    best = max(viable, key=lambda c: (c.risk_adj_ev, -abs(c.resid_after)))
    d.action, d.chosen, d.reason = best.action, best, best.reason
    return d
