"""Two-sided continuous PMUS controller — REPLAY/SHADOW ONLY, no order path.

WHAT THIS IS. The next stage after the q1 canary proved the execution
substrate live (+$0.0600 realized, analysis/MILESTONE_Q1_FIRST_REALIZED.md).
That canary was one leg then one complement then done. L5's own evidence looks
like continuous two-sided accumulation, so this maintains passive UP and DOWN
quotes SIMULTANEOUSLY, banks each matched pair, repairs whichever side missed,
and cycles for the whole window.

WHAT IT IS NOT. It holds no transport, no credentials and no order path: it
cannot place an order. Nothing here is authorized to trade, and SELL/flatten
exists only as a shadow decision.

PRIOR ART, used as architecture not as mechanics.
`feature/l5-gemini-inventory-v4` contributes: banking each completed pair with
its own held/completion prices and fees; a fill record carrying maker/taker
ROLE and its evidence; a cutoff before expiry after which no new cycle opens;
explicit counters for why we did NOT act; and -- the one that changes a
calculation here -- searching the completion budget ON THE TICK GRID rather
than closed-form, because fee rounding to cents makes a closed-form threshold
wrong near boundaries. PMUS fees, tick, queue, fill rules and settlement are
PMUS-native throughout; no Gemini fee or venue assumption is imported.

ACCOUNTING, identical to what is now proven live:
  * cumulative-fill DELTA booking -- a leg's cumulative filled quantity only
    grows, and only the increase is ever booked;
  * prices are held in each outcome's OWN TERMS and inverted exactly once, at
    the wire boundary, which this module never crosses;
  * maker/taker ROLE comes from our own action record, never inferred;
  * UNROUNDED fees for decision bounds; `cents=True` on the WHOLE order
    quantity for cash economics -- never rounded per contract and multiplied;
  * maker rebates are reported separately and never assumed credited;
  * venue commission would be authoritative against live records; in replay
    the modeled cashflow figure stands in and is labelled as such.
"""
from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal as D
from typing import Any, Dict, List, Optional

from src import pmus_fees as FEES

ONE = D("1")
TICK = D("0.01")

#: arms. They share the fill simulator and differ ONLY in policy.
ARM_REPLICATION = "L5_REPLICATION"
ARM_ADAPTED = "L5_ADAPTED_ECONOMIC"

#: CONTROL_ONE_LEG -- a REAL comparator, not "replication with max_cycles=1".
#:
#: It reproduces the shape the live q1 canary actually ran:
#:   1. while flat it rests exactly ONE side, never both at once, so it never
#:      carries simultaneous two-sided working exposure;
#:   2. the first-leg side is chosen deterministically as the cheaper
#:      own-terms executable touch (ties -> "up") -- no arm-specific economics;
#:   3. once a first leg exists it works ONLY the deficient complement side;
#:   4. it never extends the heavy side;
#:   5. it runs EXACTLY ONE cycle: when a pair banks, the market is finished.
#: Its completion comparator is the patient REPLICATION one, because CONTROL
#: isolates STRUCTURE (one leg, one cycle) rather than the WAIT/COMPLETE/EXIT
#: economics that separate REPLICATION from ADAPTED.
ARM_CONTROL = "CONTROL_ONE_LEG"

#: actions the controller may take on a deficient side
WAIT, COMPLETE_NOW, EXIT_NOW = "WAIT", "COMPLETE_NOW", "EXIT_NOW"


def floor_tick(px: D) -> D:
    return (D(str(px)) / TICK).to_integral_value(rounding=ROUND_FLOOR) * TICK


class Leg:
    """One simulated resting quote, with a causally earned queue position."""

    __slots__ = ("side", "px", "qty", "filled", "ahead0", "ahead", "consumed",
                 "placed_t", "maker", "role_source", "cycle")

    def __init__(self, side: str, px: D, qty: D, ahead: D, t: float, *,
                 maker: bool, cycle: int) -> None:
        self.side, self.px, self.qty = side, D(str(px)), D(str(qty))
        self.filled = D(0)
        self.ahead0 = self.ahead = D(str(ahead))
        self.consumed = D(0)
        self.placed_t = t
        #: the ROLE comes from the action we took, never from a venue reply.
        self.maker = bool(maker)
        self.role_source = "our_action_record"
        self.cycle = cycle

    def remaining(self) -> D:
        return self.qty - self.filled


class Pair:
    """One banked matched pair, with the economics it locked."""

    __slots__ = ("qty", "up_px", "down_px", "up_maker", "down_maker",
                 "fee_bound", "fee_cash", "rebate_bound", "rebate_cash",
                 "cycle", "t")

    def __init__(self, qty: D, up_px: D, down_px: D, *, up_maker: bool,
                 down_maker: bool, cycle: int, t: float) -> None:
        self.qty, self.up_px, self.down_px = qty, up_px, down_px
        self.up_maker, self.down_maker = up_maker, down_maker
        self.cycle, self.t = cycle, t
        # fees per leg, on the EXECUTED price and this pair's quantity
        self.fee_bound = sum(
            (FEES.taker_fee(px, qty) for px, mk in
             ((up_px, up_maker), (down_px, down_maker)) if not mk), D(0))
        self.fee_cash = sum(
            (FEES.taker_fee(px, qty, cents=True) for px, mk in
             ((up_px, up_maker), (down_px, down_maker)) if not mk), D(0))
        self.rebate_bound = sum(
            (FEES.maker_rebate(px, qty) for px, mk in
             ((up_px, up_maker), (down_px, down_maker)) if mk), D(0))
        self.rebate_cash = sum(
            (FEES.maker_rebate(px, qty, cents=True) for px, mk in
             ((up_px, up_maker), (down_px, down_maker)) if mk), D(0))

    def cost(self) -> D:
        return (self.up_px + self.down_px) * self.qty

    def gross_edge(self) -> D:
        """Payout minus cost. A matched pair pays qty whichever way it settles."""
        return self.qty - self.cost()


# ------------------------------------------------------- completion budget

def completion_budget(held_px: D, held_qty: D, *, held_maker: bool,
                      completion_maker: bool, pair_floor: D = D("0.01"),
                      cash: bool = False) -> Optional[D]:
    """Highest complementary price that still locks `pair_floor` after fees.

    SEARCHED ON THE TICK GRID, not closed-form. That is the one calculation
    the Gemini prior work changes here: fee rounding to cents makes a
    closed-form threshold wrong near a boundary, so the budget is the best
    quotable price that actually satisfies the bound when evaluated.

    `cash=False` uses unrounded fees, which is what a DECISION BOUND wants.
    `cash=True` uses the venue's cent rounding on the whole quantity, which is
    what a cash projection wants. The two genuinely differ: at q=1 a taker fee
    of .01703445 is .02 in cash, and rounding per contract then multiplying
    would overstate ten contracts as .20 instead of .17.
    """
    held_px, held_qty = D(str(held_px)), D(str(held_qty))

    def fee(px: D, maker: bool) -> D:
        if maker:
            return D(0)                     # a maker pays no taker fee
        return (FEES.taker_fee(px, held_qty, cents=True) if cash
                else FEES.taker_fee(px, held_qty))

    held_fee = fee(held_px, held_maker)
    best: Optional[D] = None
    px = TICK
    while px <= ONE - TICK:
        locked = (held_qty - held_px * held_qty - held_fee
                  - px * held_qty - fee(px, completion_maker))
        if locked >= pair_floor * held_qty:
            best = px
        px += TICK
    return best


# ------------------------------------------------- the three-way comparison

def compare_actions(*, held_side: str, held_px: D, held_qty: D,
                    held_maker: bool, comp_ask: Optional[D],
                    own_bid: Optional[D], pair_floor: D = D("0.01"),
                    cash: bool = False) -> Dict[str, Any]:
    """WAIT vs COMPLETE_NOW vs EXIT_NOW, on EXECUTABLE prices only.

    Never a midpoint: `comp_ask` is what we would actually pay to complete
    (the complement's executable ask, in the complement's own terms) and
    `own_bid` is what we would actually receive to flatten (the held side's
    executable bid, in the held side's own terms). A midpoint would flatter
    both and is not a price anyone will trade with us.

    Returns every candidate's value so the caller's choice is auditable, and
    the values are directly comparable: each is the dollar outcome of the
    matched-or-flattened position, excluding the settlement lottery on a
    residual we would still be holding under WAIT.
    """
    held_px, held_qty = D(str(held_px)), D(str(held_qty))

    def tf(px: D, maker: bool) -> D:
        if maker:
            return D(0)
        return (FEES.taker_fee(px, held_qty, cents=True) if cash
                else FEES.taker_fee(px, held_qty))

    held_cost = held_px * held_qty + tf(held_px, held_maker)
    out: Dict[str, Any] = {"held_side": held_side, "held_qty": str(held_qty),
                           "held_cost": str(held_cost), "cash_basis": cash}

    # COMPLETE_NOW: cross the complement's ask; the pair then pays held_qty.
    if comp_ask is not None:
        comp_ask = D(str(comp_ask))
        comp_cost = comp_ask * held_qty + tf(comp_ask, False)
        out[COMPLETE_NOW] = {"price": str(comp_ask),
                             "value": str(held_qty - held_cost - comp_cost),
                             "executable": True}
    else:
        out[COMPLETE_NOW] = {"value": None, "executable": False,
                             "why": "no executable complement ask"}

    # EXIT_NOW: sell the held side into its own bid. SHADOW ONLY -- no SELL is
    # authorized live, and this module cannot place one.
    if own_bid is not None:
        own_bid = D(str(own_bid))
        proceeds = own_bid * held_qty - tf(own_bid, False)
        out[EXIT_NOW] = {"price": str(own_bid),
                         "value": str(proceeds - held_cost),
                         "executable": True,
                         "note": "shadow only; no live SELL authorization"}
    else:
        out[EXIT_NOW] = {"value": None, "executable": False,
                         "why": "no executable bid on the held side"}

    # WAIT: keep working the deficient side passively. Its value is not a
    # number we may assert -- it depends on a fill that has not happened -- so
    # the BUDGET is reported instead, and the caller treats WAIT as "neither
    # of the above is good enough yet".
    budget = completion_budget(held_px, held_qty, held_maker=held_maker,
                               completion_maker=True, pair_floor=pair_floor,
                               cash=cash)
    out[WAIT] = {"completion_budget": (str(budget) if budget is not None
                                       else None),
                 "value": None,
                 "why": ("value is unknowable without a fill; the budget is "
                         "the price at which waiting pays off")}
    return out


def best_action(cmp: Dict[str, Any], *, arm: str,
                residual_tolerance: D = D("0")) -> str:
    """Pick an action. The two arms differ HERE and nowhere else.

    L5_REPLICATION prefers to keep working the passive completion -- L5's own
    behaviour is patient and rarely force-flattens -- and completes only when
    the executable pair is already good. It never chooses EXIT_NOW unless the
    held position breaches tolerance.

    L5_ADAPTED_ECONOMIC picks the economically superior executable action on
    PMUS fees, which may mean crossing sooner or flattening at a loss to stop
    a worse one.
    """
    comp = cmp.get(COMPLETE_NOW) or {}
    exit_ = cmp.get(EXIT_NOW) or {}
    comp_v = D(comp["value"]) if comp.get("value") is not None else None
    exit_v = D(exit_["value"]) if exit_.get("value") is not None else None
    forced_flatten = (residual_tolerance > 0 and exit_v is not None and
                      -exit_v > residual_tolerance)
    if arm in (ARM_REPLICATION, ARM_CONTROL):
        # PATIENT, per the preregistered definition: complete when completion
        # carries positive executable value, and flatten ONLY when the residual
        # tolerance is breached. A merely better exit is not a reason to act.
        if comp_v is not None and comp_v > 0:
            return COMPLETE_NOW
        if forced_flatten:
            return EXIT_NOW
        return WAIT
    # ADAPTED, per the preregistered definition: take the economically
    # SUPERIOR executable action. WAIT is worth 0 by construction (it asserts
    # no value), so the best strictly-positive value wins, and EXIT may be
    # chosen over COMPLETE purely because it is worth more -- with no
    # tolerance breach required. The forced flatten still applies underneath.
    best, best_v = WAIT, D(0)
    if comp_v is not None and comp_v > best_v:
        best, best_v = COMPLETE_NOW, comp_v
    if exit_v is not None and exit_v > best_v:
        best, best_v = EXIT_NOW, exit_v
    if best is not WAIT:
        return best
    if forced_flatten:
        return EXIT_NOW
    return WAIT


# ================================ the controller ================================

class TwoSided:
    """Continuous two-sided inventory cycling. Replay/shadow only.

    State machine, in the order the mandate states it:

    1. BALANCED/FLAT -> maintain passive UP and DOWN quotes SIMULTANEOUSLY
       whenever both are valid. This is the change from the q1 canary, which
       held one leg at a time.
    2. every live leg is tracked independently: side, price, original quantity,
       cumulative filled, remaining, queue position and depth, age, and role.
    3. both sides fill -> min(UP, DOWN) becomes matched inventory, its pair
       economics are BANKED, and a new cycle may open immediately.
    4. one side fills -> the excess is residual. The heavy side is not added to
       unless the EXTEND rule permits a basis-improving add inside the cap.
    5. the deficient side is worked passively while its price keeps acceptable
       all-in pair economics.
    6. a fee-safe executable complement ask may be taken, exactly as the live
       q1 did.
    7. when completion deteriorates, WAIT / COMPLETE_NOW / EXIT_NOW are
       compared on executable prices only.
    8. near settlement, residual tolerance tightens and no new cycle opens.
    9. a finished cycle resets cycle-local state only; cumulative market
       inventory and economics persist.
    """

    def __init__(self, *, arm: str, clip: D = D(1),
                 pair_floor: D = D("0.01"), residual_cap: D = D(2),
                 no_new_cycle_s: float = 120.0,
                 tighten_residual_s: float = 180.0,
                 max_cycles: int = 50, allow_extend: bool = False,
                 allow_reactive_take: bool = True,
                 cash_basis: bool = False) -> None:
        if arm not in (ARM_REPLICATION, ARM_ADAPTED, ARM_CONTROL):
            raise ValueError(f"unknown arm {arm!r}")
        self.arm = arm
        #: CONTROL rests one side at a time and runs exactly one cycle. These
        #: are BEHAVIOURAL, not reporting flags.
        self.two_sided = (arm != ARM_CONTROL)
        self.clip = D(str(clip))
        self.pair_floor = D(str(pair_floor))
        self.residual_cap = D(str(residual_cap))
        self.no_new_cycle_s = no_new_cycle_s
        self.tighten_residual_s = tighten_residual_s
        self.max_cycles = 1 if arm == ARM_CONTROL else max_cycles
        self.allow_extend = False if arm == ARM_CONTROL else allow_extend
        self.allow_reactive_take = allow_reactive_take
        self.cash_basis = cash_basis

        #: cumulative inventory, in each outcome's OWN terms
        self.qty = {"up": D(0), "down": D(0)}
        self.cost = {"up": D(0), "down": D(0)}
        self.legs: Dict[str, Optional[Leg]] = {"up": None, "down": None}
        self.pairs: List[Pair] = []
        self.fills: List[dict] = []
        self.flatten_pnl = D(0)
        self.flattens: List[dict] = []

        self.book = {"bid": None, "ask": None, "bid_qty": None,
                     "ask_qty": None}
        self.ladder: Dict[str, Dict[D, D]] = {"bids": {}, "offers": {}}
        self.window_end: Optional[float] = None
        self.now: float = 0.0

        #: counters
        self.cycle = 0
        self.cycles_started = self.cycles_completed = 0
        self.places = self.requotes = self.cancels = self.takes = 0
        self.holds = self.partials = 0
        self.queue_forfeited = self.queue_consumed = D(0)
        self.ahead_at_placement: List[D] = []
        self.blocked: Dict[str, int] = {}
        self.actions: List[dict] = []
        self.peak_committed = D(0)
        self.worst_residual = D(0)
        self.one_sided_s = 0.0
        self._last_t: Optional[float] = None
        self.adverse: List[D] = []

    # ------------------------------------------------------------ inventory

    def residual(self) -> D:
        return self.qty["up"] - self.qty["down"]

    def matched(self) -> D:
        return sum((p.qty for p in self.pairs), D(0))

    def basis(self, side: str) -> D:
        q = self.qty[side]
        return (self.cost[side] / q) if q > 0 else D(0)

    def deficient(self) -> Optional[str]:
        r = self.residual()
        return "down" if r > 0 else ("up" if r < 0 else None)

    def own_touch(self, side: str) -> Optional[D]:
        if side == "up":
            return self.book["bid"]
        a = self.book["ask"]
        return None if a is None else ONE - a
    # the executable ASK in a side's own terms: what we would PAY to buy it
    def own_ask(self, side: str) -> Optional[D]:
        if side == "up":
            return self.book["ask"]
        b = self.book["bid"]
        return None if b is None else ONE - b

    def _block(self, why: str) -> None:
        self.blocked[why] = self.blocked.get(why, 0) + 1

    def seconds_left(self) -> Optional[float]:
        if self.window_end is None:
            return None
        return self.window_end - self.now

    # ----------------------------------------------------------- quoting

    def displayed_at(self, side: str, px: D) -> Optional[D]:
        """Size resting at exactly `px` on our side, in own terms.

        A full ladder that does not show the price means the level is EMPTY,
        so we would be first in queue -- returning None there would deadlock
        the controller. With top-of-book only we cannot know, and None is
        correct: we refuse rather than invent a queue position.
        """
        book = self.ladder["bids"] if side == "up" else self.ladder["offers"]
        if not book:
            return None
        want = D(str(px)) if side == "up" else ONE - D(str(px))
        if want in book:
            return book[want]
        return D(0)

    def first_leg_side(self) -> str:
        """CONTROL's single first-leg side: the cheaper own-terms executable
        touch, ties to "up". Deterministic and arm-independent."""
        up, dn = self.own_touch("up"), self.own_touch("down")
        if up is None:
            return "down"
        if dn is None:
            return "up"
        return "up" if up <= dn else "down"

    def desired(self, side: str) -> Optional[D]:
        """The price we want resting on `side`, or None to rest nothing."""
        left = self.seconds_left()
        defic = self.deficient()
        if defic is not None and side != defic:
            # this is the HEAVY side. Rule 4: no blind adding.
            if not self.allow_extend:
                self._block("heavy_side_extend_disabled")
                return None
            own = self.own_touch(side)
            if own is None:
                return None
            if self.qty[side] > 0 and own >= self.basis(side):
                self._block("extend_would_not_improve_basis")
                return None
            if abs(self.residual()) + self.clip > self.residual_cap:
                self._block("extend_breaches_residual_cap")
                return None
            return own
        if defic == side:
            # rule 5: work the deficient side while the pair still locks.
            held = "up" if side == "down" else "down"
            budget = completion_budget(
                self.basis(held), abs(self.residual()),
                held_maker=True, completion_maker=True,
                pair_floor=self.pair_floor, cash=self.cash_basis)
            if budget is None:
                self._block("no_fee_safe_completion_budget")
                return None
            own = self.own_touch(side)
            if own is None:
                return None
            # quote at the touch if it is inside budget, else at the budget
            return floor_tick(min(own, budget))
        # rule 1: flat/balanced -> quote both sides passively (CONTROL: one)
        if not self.two_sided and side != self.first_leg_side():
            self._block("control_single_first_leg")
            return None
        if left is not None and left <= self.no_new_cycle_s:
            self._block("no_new_cycle_near_settlement")
            return None
        if self.cycles_started >= self.max_cycles:
            self._block("max_cycles")
            return None
        own = self.own_touch(side)
        if own is None or own <= 0 or own >= ONE:
            return None
        return own

    def _still_valid(self, side: str, leg: Leg) -> bool:
        """May this leg keep its price, and with it its queue position?

        A better touch is NOT a reason to move -- that is the finding the q1
        arm was built on. It goes only when it stops being economically valid.
        """
        if leg.remaining() <= 0:
            return False
        want = self.desired(side)
        if want is None:
            return False
        defic = self.deficient()
        if defic == side:
            held = "up" if side == "down" else "down"
            budget = completion_budget(
                self.basis(held), abs(self.residual()), held_maker=True,
                completion_maker=True, pair_floor=self.pair_floor,
                cash=self.cash_basis)
            return budget is not None and leg.px <= budget
        return True

    def reconcile_quotes(self) -> None:
        """Rule 1 and 2: both sides, each leg tracked independently."""
        for side in ("up", "down"):
            leg = self.legs[side]
            if leg is not None:
                if self._still_valid(side, leg):
                    self.holds += 1
                    continue
                self._drop(side, leg, "no_longer_valid")
                leg = None
            want = self.desired(side)
            if want is None:
                continue
            shown = self.displayed_at(side, want)
            if shown is None:
                self._block("queue_position_unknowable")
                continue
            self.legs[side] = Leg(side, want, self.clip, shown, self.now,
                                  maker=True, cycle=self.cycle)
            self.places += 1
            self.ahead_at_placement.append(shown)

    def _drop(self, side: str, leg: Leg, why: str) -> None:
        self.queue_forfeited += (leg.ahead0 - leg.ahead)
        self.legs[side] = None
        self.cancels += 1
        self._block(f"cancel:{why}")

    # -------------------------------------------------------------- fills

    def _book_fill(self, side: str, qty: D, px: D, *, maker: bool,
                   evidence: str) -> None:
        """Book a fill DELTA. Cumulative quantity is never booked twice."""
        # A CYCLE IS STARTED BY ITS FIRST FILL. `self.cycle` is the current
        # cycle index and is advanced only when a pair banks, so the first
        # fill carrying this index is the one that opens the cycle. Counted
        # BEFORE _bank_pairs can advance the index.
        if not any(f["cycle"] == self.cycle for f in self.fills):
            self.cycles_started += 1
        self.qty[side] += qty
        self.cost[side] += qty * px
        self.fills.append({"t": self.now, "side": side, "px": str(px),
                           "qty": str(qty), "maker": maker,
                           "role_source": "our_action_record",
                           "evidence": evidence, "cycle": self.cycle})
        committed = self.cost["up"] + self.cost["down"] - self.matched()
        if committed > self.peak_committed:
            self.peak_committed = committed
        r = abs(self.residual())
        if r > self.worst_residual:
            self.worst_residual = r
        self._bank_pairs()

    def _bank_pairs(self) -> None:
        """Rule 3: min(UP, DOWN) becomes matched, and its economics are banked."""
        m = min(self.qty["up"], self.qty["down"])
        already = self.matched()
        new = m - already
        if new <= 0:
            return
        up_legs = [f for f in self.fills if f["side"] == "up"]
        dn_legs = [f for f in self.fills if f["side"] == "down"]
        up_maker = bool(up_legs[-1]["maker"]) if up_legs else True
        dn_maker = bool(dn_legs[-1]["maker"]) if dn_legs else True
        self.pairs.append(Pair(new, self.basis("up"), self.basis("down"),
                               up_maker=up_maker, down_maker=dn_maker,
                               cycle=self.cycle, t=self.now))
        self.cycles_completed += 1
        # rule 9: reset cycle-local state only; inventory and economics persist
        self.cycle += 1

    def on_trade(self, px: D, qty: D, t: float) -> None:
        """A real print. The only thing that fills a resting quote.

        Causal: a leg may fill only from evidence AFTER it was placed, its
        queue is consumed only by prints at or through its price, and a
        partial reduces remaining quantity without resetting queue.
        """
        self.now = t
        px, qty = D(str(px)), D(str(qty))
        for side in ("up", "down"):
            leg = self.legs[side]
            if leg is None or t < leg.placed_t:
                continue
            own = px if side == "up" else ONE - px
            if own > leg.px:
                continue                    # printed away from us
            left = qty
            if leg.ahead > 0:
                eat = left if left < leg.ahead else leg.ahead
                leg.ahead -= eat
                leg.consumed += eat
                self.queue_consumed += eat
                left -= eat
                if left <= 0:
                    continue
            got = left if left < leg.remaining() else leg.remaining()
            if got <= 0:
                continue
            leg.filled += got
            self._book_fill(side, got, leg.px, maker=leg.maker,
                            evidence=f"print@{px}x{qty}")
            if leg.remaining() > 0:
                self.partials += 1          # remainder keeps its queue place
            else:
                self.legs[side] = None
        self.reconcile_quotes()

    def on_book(self, row: dict, t: float) -> None:
        if self._last_t is not None and self.deficient() is not None:
            self.one_sided_s += max(0.0, t - self._last_t)
        self._last_t = t
        self.now = t
        lad = row.get("_ladder")
        if lad is not None:
            self.ladder = lad
        for k in ("bid", "ask", "bid_qty", "ask_qty"):
            v = row.get(k)
            self.book[k] = None if v is None else D(str(v))
        self._track_adverse()
        self.decide_completion()
        self.reconcile_quotes()

    def _track_adverse(self) -> None:
        for side in ("up", "down"):
            leg = self.legs[side]
            own = self.own_touch(side)
            if leg is None or own is None:
                continue
            adv = leg.px - own
            if adv > 0:
                self.adverse.append(adv)

    # ---------------------------------------------- rules 6, 7 and 8

    def residual_tolerance(self) -> D:
        """Rule 8: tolerance tightens as settlement approaches."""
        left = self.seconds_left()
        if left is None:
            return D("0.50")
        if left <= self.tighten_residual_s:
            return D("0.05")
        return D("0.50")

    def decide_completion(self) -> Optional[str]:
        """Rules 6 and 7. Records the comparison whether or not it acts."""
        defic = self.deficient()
        if defic is None:
            return None
        held = "up" if defic == "down" else "down"
        held_qty = abs(self.residual())
        comp_ask = self.own_ask(defic)
        own_bid = self.own_touch(held)
        cmp_ = compare_actions(held_side=held, held_px=self.basis(held),
                               held_qty=held_qty, held_maker=True,
                               comp_ask=comp_ask, own_bid=own_bid,
                               pair_floor=self.pair_floor,
                               cash=self.cash_basis)
        action = best_action(cmp_, arm=self.arm,
                             residual_tolerance=self.residual_tolerance())
        self.actions.append({"t": self.now, "arm": self.arm, "action": action,
                             "residual": str(self.residual()),
                             "seconds_left": self.seconds_left(),
                             "comparison": cmp_})
        if action == COMPLETE_NOW and self.allow_reactive_take and \
                comp_ask is not None:
            # rule 6: cross the executable ask, exactly as the live q1 did.
            leg = self.legs.get(defic)
            if leg is not None:
                self._drop(defic, leg, "pre_take")
            self.takes += 1
            self._book_fill(defic, held_qty, D(str(comp_ask)), maker=False,
                            evidence="reactive_take")
            return COMPLETE_NOW
        if action == EXIT_NOW and own_bid is not None:
            # SHADOW ONLY. No live SELL authorization exists, and this module
            # holds no order path; the P&L is reported on its own line.
            proceeds = D(str(own_bid)) * held_qty
            fee = (FEES.taker_fee(D(str(own_bid)), held_qty, cents=True)
                   if self.cash_basis
                   else FEES.taker_fee(D(str(own_bid)), held_qty))
            pnl = proceeds - fee - self.basis(held) * held_qty
            self.flatten_pnl += pnl
            self.flattens.append({"t": self.now, "side": held,
                                  "qty": str(held_qty), "px": str(own_bid),
                                  "pnl": str(pnl), "shadow_only": True})
            self.qty[held] -= held_qty
            self.cost[held] -= self.basis(held) * held_qty
            leg = self.legs.get(defic)
            if leg is not None:
                self._drop(defic, leg, "flattened")
            return EXIT_NOW
        return action

    # ------------------------------------------------------------- report

    def run(self, events, *, window_end: Optional[float] = None) -> dict:
        self.window_end = window_end
        for t, kind, row in events:
            if kind == "book":
                self.on_book(row, t)
            else:
                self.on_trade(row.get("px"), row.get("qty"), t)
        return self.report()

    def settle(self, winner: Optional[str]) -> dict:
        """Settled economics. A matched pair pays its quantity either way; the
        residual pays only if it is the winning side."""
        matched = self.matched()
        gross = sum((p.gross_edge() for p in self.pairs), D(0))
        fee_bound = sum((p.fee_bound for p in self.pairs), D(0))
        fee_cash = sum((p.fee_cash for p in self.pairs), D(0))
        reb_bound = sum((p.rebate_bound for p in self.pairs), D(0))
        reb_cash = sum((p.rebate_cash for p in self.pairs), D(0))
        r = self.residual()
        resid_pnl = None
        if winner in ("up", "down") and r != 0:
            side = "up" if r > 0 else "down"
            qty = abs(r)
            payout = qty if winner == side else D(0)
            resid_pnl = payout - self.basis(side) * qty
        elif r == 0:
            resid_pnl = D(0)
        # TWO TOTALS, NEVER ONE. `source_settled_total` contains only
        # economics backed by events in the source tape: banked matched pairs,
        # venue fee cashflow, and the residual's actual settlement against the
        # source winner. The modelled maker rebate is DELIBERATELY EXCLUDED and
        # stays on its own line -- a rebate is not economics until it is
        # actually credited. `shadow_total_with_flatten` adds
        # flatten_pnl_shadow, a HYPOTHETICAL unwind of open inventory at
        # executable prices that never happened -- so it is modelled cashflow
        # and must never be called settled or realized.
        source_settled = (None if resid_pnl is None else
                          gross - fee_cash + resid_pnl)
        shadow_total = (None if source_settled is None else
                        source_settled + self.flatten_pnl)
        return {"matched_contracts": str(matched),
                "matched_gross_edge": str(gross),
                "taker_fees_cashflow": str(fee_cash),
                "taker_fees_bound": str(fee_bound),
                "maker_rebates_modeled_cashflow": str(reb_cash),
                "maker_rebates_modeled_bound": str(reb_bound),
                "rebate_note": "modeled, never assumed credited",
                "flatten_pnl_shadow": str(self.flatten_pnl),
                "residual_settlement_pnl": (None if resid_pnl is None
                                            else str(resid_pnl)),
                "source_settled_total": (None if source_settled is None
                                         else str(source_settled)),
                "shadow_total_with_flatten": (None if shadow_total is None
                                              else str(shadow_total)),
                "accounting_note":
                    "source_settled_total = matched_gross_edge "
                    "- taker_fees_cashflow + residual_settlement_pnl. "
                    "Modelled maker rebates are EXCLUDED until credited. "
                    "shadow_total_with_flatten adds flatten_pnl_shadow, "
                    "which is MODELLED cashflow, not settled or realized"}

    def report(self, *, winner: Optional[str] = None) -> dict:
        med = (sorted(self.ahead_at_placement)[len(self.ahead_at_placement)//2]
               if self.ahead_at_placement else D(0))
        span = (self.now - (self.fills[0]["t"] if self.fills else self.now)) or 0.0
        minutes = (span / 60.0) if span else 0.0
        out = {"arm": self.arm, "clip": str(self.clip),
               "cycles_started": self.cycles_started,
               "cycles_completed": self.cycles_completed,
               "maker_fills": sum(1 for f in self.fills if f["maker"]),
               "taker_fills": sum(1 for f in self.fills if not f["maker"]),
               "partial_fills": self.partials,
               "residual_now": str(self.residual()),
               "max_dollars_committed": str(self.peak_committed),
               "max_directional_residual": str(self.worst_residual),
               "time_one_sided_s": round(self.one_sided_s, 2),
               "queue_forfeited": str(self.queue_forfeited),
               "queue_consumed": str(self.queue_consumed),
               "median_queue_ahead_at_placement": str(med),
               "places": self.places, "requotes": self.requotes,
               "cancels": self.cancels, "takes": self.takes,
               "holds": self.holds,
               "adverse_markout_mean": str(
                   (sum(self.adverse) / D(len(self.adverse)))
                   if self.adverse else D(0)),
               "blocked_reasons": dict(self.blocked),
               "pairs_per_min": (round(len(self.pairs) / minutes, 4)
                                 if minutes else None),
               "actions_considered": len(self.actions),
               "flatten_count": len(self.flattens),
               "shadow_only": True}
        out.update(self.settle(winner))
        if minutes:
            for key, rate in (("source_settled_total",
                               "source_settled_dollars_per_min"),
                              ("shadow_total_with_flatten",
                               "shadow_dollars_per_min")):
                if out.get(key) is not None:
                    out[rate] = str(D(out[key]) / D(str(round(minutes, 6))))
        return out
