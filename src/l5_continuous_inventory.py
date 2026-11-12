"""A continuous UP/DOWN inventory decision, recomputed from current state.

WHY THIS EXISTS. The deployed two-sided path decides from a FROZEN PLAN
inside a NUMBERED ROUND. Both are experiment scaffolding, and together they
stop the engine behaving like L5 at all:

  * `probe_store_plan(market, plan, rnd)` freezes leg prices for a round, so
    leg two is priced at a book that has since moved.
  * `probe_round` walks rounds from zero and returns the first with an
    unspent leg, so a round that cannot finish pins the market forever.
  * `probe_round_is_safe_to_retire` refuses to retire a round while
    `up != dn`, which one stranded fill makes permanently true.
  * `controller_action_key(market, rnd, leg)` puts the round IN the
    idempotency key, so duplicate suppression is only available inside the
    round machinery.

Measured consequence, on the owner-authorized campaign of 2026-11-02: eight
rounds authorized per window, exactly one ever reachable, one fill per
window, zero completed pairs.

WHAT REPLACES IT. Nothing is stored and nothing is numbered. Every call
recomputes the wanted action from the CURRENT book and the CURRENT realized
inventory, so a book update, a fill, a cancellation and an inventory change
are all the same event: decide again. There is no plan to go stale and no
round to exhaust.

IDEMPOTENCY WITHOUT THE ROUND. `action_key` is content-addressed on
(market, side, price, qty). While the engine wants the same order it
produces the same key, hence the same client_order_id, hence
DUPLICATE_SUPPRESSED on every later tick -- the exact property the
round-scoped key provided. When the book moves and it wants a DIFFERENT
order the key differs, which is the intended behavioural change, and that
new order still has to pass MAX_LIVE_ORDERS, the cash envelope, the
per-market worst case, the residual cap and both loss halts, none of which
this module touches. Because the key is derived from current state rather
than a counter, restart recovery needs no new durable state: the same
inventory and the same book reproduce the same key.

L5'S VOCABULARY, AND ITS ACTUAL MEANING. From 63,686 reconstructed L5
actions: ADD 33,490, REDUCE 28,255, REVERSE 1,379, OPEN 556. REDUCE is
L5 BUYING THE OPPOSITE SIDE -- never a sale; `q` is non-negative in all
63,686 rows and the imbalance moves toward zero. REVERSE is the same buy
in a size that carries the imbalance PAST flat and out the other side.
`l5_live_execution.inventory_class` already distinguishes the first three
as FLAT_OPEN / INCREASE_RESIDUAL / REDUCE_RESIDUAL; it cannot see REVERSE,
because it classifies on sign alone and ignores size. `classify` here adds
exactly that distinction and nothing else.

THIS MODULE DECIDES; IT NEVER SUBMITS. It returns an action or a named
refusal. Every capital limit, loss halt, reconciliation, exact-order
cancellation and watchdog protection stays where it is, in the caller.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src import l5_two_sided_probe as TS

UP, DOWN = "UP", "DOWN"
OPEN, ADD, REDUCE, REVERSE = "OPEN", "ADD", "REDUCE", "REVERSE"
KEY_PREFIX = "l5ci"


#: THE EX-POST IMBALANCE BAND HAS BEEN REMOVED.
#:
#: An earlier revision derived an ADD budget from a density fitted to each
#: L5 window's PEAK |imbalance|. That quantity is only knowable AFTER the
#: window ends, so a threshold fitted on it cannot be applied at decision
#: time -- it is look-ahead, and the owner excluded it from promotion.
#:
#: `decide` therefore takes `imbalance_cap` EXPLICITLY and defaults it to
#: ZERO, which means no ADD. A caller that wants to permit a temporary
#: imbalance must state the bound itself, from something it can know before
#: the order, and the hard residual cap still wins over whatever it states.
def classify(up_q: D, dn_q: D, side: str, qty: D) -> str:
    """L5's own action vocabulary, from CURRENT realized inventory.

    REDUCE and REVERSE are both BUYS of the opposite side. The only
    difference is whether the size carries the imbalance past flat.
    """
    side = str(side).upper()
    if side not in (UP, DOWN):
        raise ValueError("side must be UP or DOWN, got %r" % side)
    if qty <= 0:
        raise ValueError("qty must be positive, got %r" % qty)
    resid = D(up_q) - D(dn_q)
    if resid == 0:
        return OPEN
    if (resid > 0) == (side == UP):
        return ADD
    return REVERSE if qty > abs(resid) else REDUCE


def inventory_epoch(up_q: Any, dn_q: Any) -> str:
    """A durable fingerprint of REALIZED inventory, for the action key.

    THE DUPLICATE-SUPPRESSION DEFECT THIS CLOSES. The key was
    (market, side, price) alone, which collapses two genuinely different
    intents into one: a re-fire at an unchanged book (which MUST be
    suppressed) and the FIRST LEG OF A SECOND PAIR at the same price after
    the first pair completed (which must NOT be). The second is the whole
    point of continuous inventory, and the old key silently refused it --
    which is why `L5_TS_CONTINUOUS_INVENTORY` regressed pair-after-pair and
    was not promotable.

    WHY REALIZED INVENTORY IS THE RIGHT DISCRIMINATOR. It moves when, and
    only when, a fill lands. A re-tick against an unchanged book leaves it
    identical, so suppression still fires; a completed pair changes it, so
    the next intent at the same price is a NEW identity. No clock, no
    counter and no round -- still content-addressed, just on the content
    that actually distinguishes the two cases.

    It is NOT a sequence number: it cannot advance on its own, cannot be
    incremented by a retry, and after a restart it is recomputed from the
    durable ledger rather than remembered.
    """
    return "%s:%s" % (D(up_q).normalize(), D(dn_q).normalize())


def action_key(market: str, side: str, price: D, qty: Any = None,
               *, epoch: Any = None) -> str:
    """Content-addressed idempotency key. No round, no clock, no counter.

    KEYED ON (market, side, price) and, when the caller supplies it, the
    realized-inventory `epoch` from `inventory_epoch`. `qty` is accepted so
    callers can pass the order they built, and is deliberately NOT part of
    the identity.

    `epoch=None` reproduces the previous key byte-for-byte, so a caller that
    does not opt in is unaffected.

    WHY QUANTITY IS EXCLUDED, measured. The executor SHRINKS an order to fit
    whatever cash and headroom are left, so four identical ticks against an
    unchanged book produced sizes 3, then 1, then 2. With quantity in the
    key each of those was a different identity, duplicate suppression never
    fired, and three orders went out where the round-scoped key would have
    sent one. Quantity is a consequence of the budget, not a statement of
    intent, so it cannot be allowed to mint a new identity.

    What remains is the property that matters: the same side at the same
    price against the same realized inventory is ONE intent however the size
    is trimmed, so a repeated trigger is suppressed; a genuine book move
    changes the price, and a landed fill changes the epoch -- either of which
    is the signal that a different order is actually wanted.
    """
    base = "%s|%s|%s|%s" % (KEY_PREFIX, str(market), str(side).upper(),
                            D(price).normalize())
    return base if epoch is None else "%s|%s" % (base, epoch)


def _own(side: str, bid: Any, ask: Any, tick: D, depth: int) -> Optional[D]:
    return TS.quote_own_price(side, bid=bid, ask=ask, tick=tick,
                              depth_ticks=depth)


def decide(*, market: str, bid: Any, ask: Any, tick: D,
           up_q: D = D(0), dn_q: D = D(0),
           up_cost: D = D(0), dn_cost: D = D(0),
           clip: D = D("2.52"), depth_ticks: int = 2,
           cash_spent: D = D(0), cash_cap: D = D("12"),
           may_open: bool = True, residual_cap: D = D(5),
           imbalance_cap: D = D(0),
           pair_ceiling: Optional[D] = None,
           complete_ceiling: D = D("1.00")) -> Dict[str, Any]:
    """The whole decision, recomputed. Returns an action or a named refusal.

    BOTH SIDES ARE RECONSIDERED EVERY CALL. There is no memory of what was
    wanted last tick, so a fill, a cancel or a book move needs no special
    handling: the next call simply sees different inputs.

    PRIORITY. Completing held inventory comes before opening more, because a
    REDUCE removes exposure and an OPEN adds it. That ordering is also what
    L5's record shows: 556 OPENs against 61,745 ADD/REDUCE/REVERSE actions,
    about one opening per window with everything after it working the
    position.
    """
    up_q, dn_q = D(up_q), D(dn_q)
    resid = up_q - dn_q
    ceiling = TS.max_pair_cost() if pair_ceiling is None else D(pair_ceiling)
    u = _own(UP, bid, ask, tick, depth_ticks)
    d = _own(DOWN, bid, ask, tick, depth_ticks)
    if u is None or d is None or u <= 0 or d <= 0:
        return {"ok": False, "reason": "NO_QUOTABLE_PRICE"}

    def _afford(px: D, q: D) -> bool:
        return (D(cash_spent) + px * q) <= D(cash_cap)

    # ---------------------------------------------------- REDUCE / REVERSE
    # Held inventory first. The complement is priced at the book as it is
    # NOW, which is the whole point: a frozen plan cannot do this.
    if resid != 0:
        need = abs(resid)
        cside = DOWN if resid > 0 else UP
        cown = d if cside == DOWN else u
        held_cost = (up_cost if resid > 0 else dn_cost)
        held_qty = (up_q if resid > 0 else dn_q)
        # AN UNATTESTED BASIS IS NOT A FREE POSITION. A held leg with no
        # recorded cost divides to a basis of ZERO, which prices the pair as
        # though the inventory had been given to us and makes any complement
        # look completable. The venue has no zero-price fill, so cost 0
        # against positive quantity means the basis was never attested --
        # the same condition `l5_two_sided_probe.completion_requote` refuses
        # as HELD_COST_UNKNOWN and that `adopt_venue_position` pins at the
        # $1/share worst case. Fail closed rather than invent a basis.
        basis = ((D(held_cost) / D(held_qty))
                 if (held_qty > 0 and D(held_cost) > 0) else None)
        q = min(need, D(clip))
        if basis is None:
            return {"ok": False, "reason": "HELD_COST_UNKNOWN"}
        pair = basis + cown
        if pair > D(complete_ceiling):
            red = {"ok": False, "reason": "COMPLETION_PAIR_ABOVE_CEILING",
                   "pair_cost": str(pair), "ceiling": str(complete_ceiling)}
        elif not _afford(cown, q):
            red = {"ok": False, "reason": "CASH_CAP_WOULD_BIND"}
        else:
            return {"ok": True, "action": classify(up_q, dn_q, cside, q),
                    "side": cside, "price": cown, "qty": q,
                    "pair_cost": pair, "basis": basis,
                    "key": action_key(market, cside, cown, q),
                    "labels": ("a REDUCE is a BUY of the opposite side, "
                               "priced at the CURRENT book")}
    else:
        red = None

    # ------------------------------------------------------------- ADD
    # Only when it moves the average basis toward a completable pair. An ADD
    # that cannot improve the basis is just more directional exposure, and
    # chronological replay showed that adding for its own sake loses money.
    if resid != 0:
        aside = UP if resid > 0 else DOWN
        aown = u if aside == UP else d
        held_cost = (up_cost if resid > 0 else dn_cost)
        held_qty = (up_q if resid > 0 else dn_q)
        # AN EXPLICIT BOUND, defaulting to zero.
        #
        # Requiring every ADD to LOWER the average basis made ADD
        # unreachable in practice: it fires only when the market has already
        # moved our way, and when it has, a REDUCE is available and takes
        # priority. So the basis test is gone -- but it is NOT replaced by a
        # threshold fitted to an outcome. The caller states the bound, or
        # gets no ADD.
        # STATED, never derived. Zero means no ADD.
        band = min(D(imbalance_cap), D(residual_cap))
        if held_qty > 0 and D(held_cost) > 0:
            basis = D(held_cost) / D(held_qty)
            q = D(clip)
            # size the clip down to land exactly on the band rather than
            # refuse: a smaller ADD inside the band is still an ADD.
            room = band - abs(resid)
            if room < q:
                q = room
            if q > 0 and _afford(aown, q):
                new_basis = (D(held_cost) + aown * q) / (held_qty + q)
                return {"ok": True, "action": ADD, "side": aside,
                        "price": aown, "qty": q,
                        "basis_before": basis, "basis_after": new_basis,
                        "imbalance_after": abs(resid) + q,
                        "imbalance_band": band,
                        "key": action_key(market, aside, aown, q),
                        "labels": ("an ADD is permitted because the imbalance "
                                   "it creates stays inside the bound the "
                                   "CALLER stated; nothing here derives one")}
        # BOTH REASONS ARE TRUE, so both are reported. The completion
        # refusal outranks, because a held leg that cannot be completed is
        # the more consequential fact, but the band state is what says
        # whether an ADD was even available -- and a refusal that named
        # only one of them made the other invisible in the record.
        out = dict(red) if red is not None else {
            "ok": False, "reason": "IMBALANCE_BAND_EXHAUSTED"}
        out["imbalance"] = str(abs(resid))
        out["imbalance_band"] = str(band)
        out["add_available"] = bool(band - abs(resid) > 0)
        return out

    # ------------------------------------------------------------ OPEN
    if not may_open:
        return {"ok": False, "reason": "OPENING_CLOSED"}
    if (u + d) > ceiling:
        return {"ok": False, "reason": "PAIR_ABOVE_CEILING",
                "pair_cost": str(u + d), "ceiling": str(ceiling)}
    side, own = (UP, u) if u <= d else (DOWN, d)
    lo, hi = TS.min_first_leg_own_price(), TS.max_first_leg_own_price()
    if not (lo <= own <= hi):
        return {"ok": False, "reason": "FIRST_LEG_OUTSIDE_BAND",
                "own_price": str(own)}
    q = D(clip)
    if not _afford(own, q):
        return {"ok": False, "reason": "CASH_CAP_WOULD_BIND"}
    if q > D(residual_cap):
        return {"ok": False, "reason": "CLIP_ABOVE_RESIDUAL_CAP"}
    return {"ok": True, "action": OPEN, "side": side, "price": own, "qty": q,
            "pair_cost": u + d, "key": action_key(market, side, own, q),
            "labels": "a first leg, priced at the CURRENT book"}

# ====================================================================
# THE PROSPECTIVE ACTION RULE
#
# Every term below is knowable BEFORE the order is sent. Nothing in it is
# fitted to an outcome, and nothing uses a quantity that is only observable
# after the window ends -- which is the defect in deriving a threshold from
# a window's ex-post PEAK imbalance.
#
# THE IDENTITY IT RESTS ON. A prediction market's own price IS its
# probability. So for inventory of `q` on a side whose current market price
# is `p`, bought at average basis `b`:
#
#     expected value of CARRYING it to settlement  =  q * (p - b)
#     value of COMPLETING it at complement cost c  =  q * (1 - b - c)
#
# Completing beats carrying exactly when  1 - b - c > p - b, i.e. when
#
#     c  <  1 - p
#
# The basis `b` cancels. What was already paid is sunk and must not enter
# the decision -- which is why "complete only when the PAIR is under $1.00"
# is the wrong test: it chases the entry price instead of comparing the two
# futures actually available now. A complement is worth buying when it
# costs less than its own fair value, whatever the first leg cost.
#
# WHAT L5'S RECORD SAYS ABOUT THIS. Across 31,043 opposite-side purchases,
# decision-time only: 57.8% completed at a pair under $1.00 and were taken
# at prices 0.1173 BELOW that side's window-to-date mean; 42.2% completed at
# a pair over $1.00 and were taken 0.1632 ABOVE it, with a larger prior
# imbalance (177.79 against 133.87) and bigger clips. t = -202.69. So L5
# completes opportunistically when the complement is cheap and pays up
# defensively when exposure is large -- two motives, cleanly separable, and
# both computable before the order.
#
# WHY ADD IS `NO_ACTION` BY DEFAULT. A maker ADD rests BELOW the touch, so
# at decision time its price always looks better than the market's
# probability. That edge is an illusion: the order fills only when the
# market comes to it, and at that moment the probability equals the price.
# Measured on 693,916 of L5's own fills the per-contract alpha is +0.00116,
# and on a second cut of 63,686 it is -0.00091 -- opposite signs, so zero
# within noise. An ADD therefore buys no expected edge and only adds
# exposure, and this rule will not take one to raise activity.
# ====================================================================

NO_ACTION = "NO_ACTION"


def complement_is_worth_buying(*, held_side_price: D, complement_cost: D,
                               margin: D = D(0)) -> bool:
    """`c < (1 - p) - margin`. The basis is sunk and does not appear."""
    return D(complement_cost) < (D(1) - D(held_side_price)) - D(margin)


def expected_carry(*, qty: D, basis: D, held_side_price: D) -> D:
    """Expected settlement value of carrying unresolved inventory."""
    return D(qty) * (D(held_side_price) - D(basis))


def expected_complete(*, qty: D, basis: D, complement_cost: D) -> D:
    """Value of resolving it now at `complement_cost`. Certain, not expected."""
    return D(qty) * (D(1) - D(basis) - D(complement_cost))


def select_action(*, bid: Any, ask: Any, tick: D, up_q: D = D(0),
                  dn_q: D = D(0), up_cost: D = D(0), dn_cost: D = D(0),
                  depth_ticks: int = 2, margin: D = D(0),
                  may_open: bool = True, allow_add: bool = False
                  ) -> Dict[str, Any]:
    """OPEN, ADD, COMPLETE or NO_ACTION, with the economics that chose it.

    Decided entirely from the current book and current realized inventory.
    Returns the comparison it made, so a refusal is auditable rather than
    merely a name.
    """
    up_q, dn_q = D(up_q), D(dn_q)
    resid = up_q - dn_q
    u = _own(UP, bid, ask, tick, depth_ticks)
    d = _own(DOWN, bid, ask, tick, depth_ticks)
    if u is None or d is None or u <= 0 or d <= 0:
        return {"action": NO_ACTION, "reason": "NO_QUOTABLE_PRICE"}
    # the MARKET's probability for each side, at the touch, not our quote
    try:
        p_up = D(str(bid))
        p_dn = D(1) - D(str(ask))
    except BaseException:                                      # noqa: BLE001
        return {"action": NO_ACTION, "reason": "NO_QUOTABLE_PRICE"}

    if resid != 0:
        held = UP if resid > 0 else DOWN
        cside = DOWN if held == UP else UP
        cown = d if cside == DOWN else u
        hq = up_q if held == UP else dn_q
        hc = up_cost if held == UP else dn_cost
        if hq <= 0 or D(hc) <= 0:
            return {"action": NO_ACTION, "reason": "HELD_COST_UNKNOWN"}
        basis = D(hc) / hq
        p_held = p_up if held == UP else p_dn
        q = min(abs(resid), hq)
        carry = expected_carry(qty=q, basis=basis, held_side_price=p_held)
        done = expected_complete(qty=q, basis=basis, complement_cost=cown)
        info = {"held": held, "held_qty": str(q), "basis": str(basis),
                "held_side_price": str(p_held),
                "complement_cost": str(cown),
                "fair_complement": str(D(1) - p_held),
                "expected_carry": str(carry),
                "value_if_completed": str(done),
                "advantage_of_completing": str(done - carry)}
        # THE COMPARISON AT THE TOUCH IS DEGENERATE, AND THAT IS THE FINDING.
        #
        # Our complement is a MAKER quote `depth_ticks` behind the touch, so
        # mechanically cown = (1 - p_held) - spread - d*tick. The advantage
        # of completing therefore comes out at exactly
        # q * (spread + d*tick) -- a constant, positive at every price, in
        # every market state. The rule "complete when the complement is
        # below fair value" consequently ALWAYS fires and carries no
        # information at all.
        #
        # It is the same illusion as the ADD case. The quote looks cheap
        # only because a resting maker order fills when the market comes TO
        # it, and at that moment the probability equals the price we paid.
        # Priced at the fill rather than at the touch, carrying and
        # completing have the SAME expected value, which is what the
        # measured per-contract alpha of +0.00116 on 693,916 fills (and
        # -0.00091 on a second cut of 63,686) already says.
        #
        # SO NO EXPECTED-VALUE RULE EXISTS HERE, and one that appears to is
        # reading the depth of its own quote as edge. What genuinely differs
        # between the two futures is not expectation but VARIANCE, plus the
        # credited rebate on the extra fill:
        #
        #   completing  removes the exposure, at no expected cost
        #   carrying    keeps a directional position with no expected gain
        #
        # That is why this returns REDUCE whenever a complement is quotable
        # and NO_ACTION rather than ADD otherwise: not because completing is
        # +EV, but because it is variance-reducing at zero expected cost,
        # and an ADD is variance-increasing at zero expected gain. The
        # `margin` parameter exists so a caller may demand a REAL discount
        # to fair value -- one larger than the mechanical spread+depth --
        # before resolving, and 0 means "resolve whenever quotable".
        _mechanical = (D(1) - p_held) - cown
        info["mechanical_discount"] = str(_mechanical)
        info["is_degenerate_at_touch"] = bool(_mechanical > 0)
        if complement_is_worth_buying(held_side_price=p_held,
                                      complement_cost=cown, margin=margin):
            return dict(info, action=REDUCE if q <= abs(resid) else REVERSE,
                        side=cside, price=cown, qty=q,
                        key=action_key("", cside, cown),
                        labels=("resolving removes the exposure at no "
                                "expected cost. NOT an expected-value edge: "
                                "the apparent discount is the depth of our "
                                "own maker quote, see the comment above"))
        if allow_add:
            return dict(info, action=NO_ACTION,
                        reason="ADD_HAS_NO_EXPECTED_EDGE")
        return dict(info, action=NO_ACTION,
                    reason="CARRYING_BEATS_COMPLETING_AT_THIS_PRICE")

    if not may_open:
        return {"action": NO_ACTION, "reason": "OPENING_CLOSED"}
    if (u + d) > TS.max_pair_cost():
        return {"action": NO_ACTION, "reason": "PAIR_ABOVE_CEILING",
                "pair_cost": str(u + d)}
    side, own = (UP, u) if u <= d else (DOWN, d)
    lo, hi = TS.min_first_leg_own_price(), TS.max_first_leg_own_price()
    if not (lo <= own <= hi):
        return {"action": NO_ACTION, "reason": "FIRST_LEG_OUTSIDE_BAND",
                "own_price": str(own)}
    return {"action": OPEN, "side": side, "price": own,
            "pair_cost": str(u + d), "key": action_key("", side, own),
            "labels": "a fee-safe opening pair at the current book"}


# ====================================================================
# ONE PASS OF THE CONTINUOUS INVENTORY CONTROLLER
#
# `decide` answers "what is the ONE next order". A runner pass needs more
# than that: both maker quotes are managed together, a resting order the
# book has left must be cancelled by its exact id before it is replaced,
# and the same cash must never be spent twice by two orders chosen in the
# same pass. `decide_pass` is that whole pass, still PURE: every input is a
# number the caller read from durable state or the authenticated book, and
# the output is a list of intents plus the refusals that explain every side
# that got none. It submits nothing and cancels nothing.
#
# THE LIFECYCLE IT ENCODES, in priority order:
#
#   CANCEL    a resting order whose wanted price moved by >= reprice_ticks
#             (or whose basis now breaches the hard completion ceiling, or
#             which under-hedges what is held). The caller cancels BY EXACT
#             ID; the replacement is decided again from the state the
#             venue-confirmed cancel leaves behind.
#   COMPLETE  the light side, against the AVERAGE COST PER CONTRACT of the
#             held side (average-cost lot attribution: deterministic, and
#             matched + remaining basis always sums to the filled basis).
#             Priced at the touch (completion depth), capped so that
#             basis + price + maker fee <= the maker completion target, and
#             refused outright if it could exceed the hard ceiling (1.00).
#   OPEN /    both maker quotes from flat while entry is open: fee-safe pair
#   REPLENISH (u + d <= pair ceiling), own price inside the first-leg band,
#             one common size that fits cash, worst case and residual cap.
#             REPLENISH is the same quote after the window has traded: a
#             side whose order filled is re-quoted on the next pass, subject
#             to the caller's replenishment credit.
#   ADD       the heavy side, only inside the STATED imbalance band (default
#             0 => never), only within the fill-contingent residual cap, and
#             only when (new average cost + opposite own bid) <= pair ceiling.
#   HOLD      everything else, by name.
#
# SKEW. With `skew_ticks` > 0 and an imbalance, the light side is quoted
# that many ticks nearer its touch (never through it) and the heavy side
# that many ticks further away. Default 0: no skew.
#
# REDUCE (SELLING) IS NOT AUTHORIZED LIVE. It is evaluated -- the value of
# selling the excess into the bid against its basis -- and reported under
# `evaluated`, never returned as an action.
# ====================================================================

PLACE, CANCEL, HOLD = "PLACE", "CANCEL", "HOLD"
COMPLETE, REPLENISH = "COMPLETE", "REPLENISH"
SELL_REDUCE = "REDUCE_BY_SELLING"


def maker_fee_per_contract() -> D:
    """What a resting (maker) order pays per contract at PMUS: nothing.

    `pmus_fees` charges `taker_fee` to the taker only and credits
    `maker_rebate` to the maker. The rebate is NEVER netted against a cost
    (that module's own rule), and this account's tier earns none until a
    credit is source-accounted -- so the maker cost used in every pair bound
    here is exactly zero, and no rebate is assumed anywhere. A taker order
    is never produced by this module.
    """
    from src import pmus_fees as _PF                           # noqa: F401
    return D(0)


def _floor_inc(q: D, inc: D) -> D:
    if inc is None or inc <= 0:
        return q
    return (q / inc).to_integral_value(rounding="ROUND_FLOOR") * inc


def _floor_tick(px: D, tick: D) -> D:
    return (px / tick).to_integral_value(rounding="ROUND_FLOOR") * tick


def worst_settlement_loss(*, up_q: D, dn_q: D, up_cost: D, dn_cost: D,
                          rest_up_usd: D = D(0), rest_dn_usd: D = D(0)
                          ) -> D:
    """Worst loss over both settlements, every resting order on the LOSING
    side assumed filled and none on the winning side.

    A resting order on the side that wins can only add value (it pays $1 for
    at most $1), so the worst case for "UP wins" is the filled cost, minus
    the UP redemption, plus every DOWN order resting -- and symmetrically.
    """
    cost = D(up_cost) + D(dn_cost)
    if_up = cost - D(up_q) + D(rest_dn_usd)
    if_dn = cost - D(dn_q) + D(rest_up_usd)
    return max(if_up, if_dn, D(0))


def _residual_binds(*, side: str, qty: D, resid: D, same_resting: D,
                    other_resting: D, k: Optional[D], clip_hist: D) -> bool:
    """The executor's own fill-contingent residual cap, asked in advance so
    a pass does not propose an order the gate is certain to refuse. The
    gate itself still runs on the order; this only avoids spending a claim.
    """
    if k is None:
        return False
    from src import l5_live_execution as _LX
    _rc = _LX.residual_cap_decision(
        resid=D(resid), same_side=D(same_resting), other_side=D(other_resting),
        qty=D(qty), side=side, k=D(k), clip=D(clip_hist),
        same_side_includes_this_order=False, certain=True)
    return bool(_rc.get("binds"))


def decide_pass(*, market: str, bid: Any, ask: Any, tick: Any,
                up_q: D = D(0), dn_q: D = D(0),
                up_cost: D = D(0), dn_cost: D = D(0),
                resting: Any = (),
                now_s: Optional[float] = None,
                entry_refusal: Optional[str] = None,
                complete_refusal: Optional[str] = None,
                clip: D = D(1), qty_increment: D = D("0.01"),
                depth_ticks: int = 2, complete_depth_ticks: int = 0,
                skew_ticks: int = 0,
                cash_committed: D = D(0), cash_cap: D = D("0.90"),
                max_worst_loss: D = D("1.00"),
                residual_k: Optional[D] = D(1), clip_hist: D = D(0),
                abs_residual_cap: D = D(5),
                imbalance_cap: D = D(0),
                pair_ceiling: Optional[D] = None,
                complete_target: Optional[D] = None,
                complete_ceiling: D = D("1.00"),
                first_leg_band: Any = None,
                reprice_ticks: int = 1, reprice_min_rest_s: float = 0.0,
                stranded_since_s: Optional[float] = None,
                maker_fee: Optional[D] = None,
                replenish_permitted: bool = True,
                traded_before: bool = False,
                orders_uncertain: Optional[str] = None,
                cash_cost: Any = None,
                slots_left: Optional[Mapping[str, Any]] = None,
                bid_shares: Any = None, ask_shares: Any = None,
                haircut_ticks: int = 1,
                keep_valid_orders: bool = False
                ) -> Dict[str, Any]:
    """The whole pass, recomputed from the current book and inventory.

    `resting` is a sequence of mappings with `coid`, `side`, `price`,
    `remaining` and optionally `submitted_s`: every still-fillable order in
    this market, each COUNTED ONCE -- in cash, in worst case and in the
    residual cap. Returns
      {"ok", "phase", "actions": [...], "holds": {side: reason},
       "evaluated": {...}, "state": {...}}
    where every action is a CANCEL (by exact `coid`) or a PLACE with an
    `intent` of OPEN / REPLENISH / ADD / COMPLETE. `cash_cost(side, price,
    qty)` is the cash the CALLER'S gate will reserve for an order (the
    validation gates reserve a DOWN buy at $1 a share unless it hedges);
    default `price * qty`. Cancels come first, and a
    side being cancelled gets no placement in the same pass: the caller must
    see the venue confirm the cancel and decide again.
    """
    tk = D(str(tick)) if tick is not None else None
    fee = maker_fee_per_contract() if maker_fee is None else D(maker_fee)
    ceiling = TS.max_pair_cost() if pair_ceiling is None else D(pair_ceiling)
    target = (TS.maker_completion_target() if complete_target is None
              else D(complete_target))
    hard = D(complete_ceiling)
    target = min(target, hard)
    lo, hi = (first_leg_band if first_leg_band is not None
              else (TS.min_first_leg_own_price(),
                    TS.max_first_leg_own_price()))
    up_q, dn_q = D(up_q), D(dn_q)
    up_cost, dn_cost = D(up_cost), D(dn_cost)
    resid = up_q - dn_q
    heavy = None if resid == 0 else (UP if resid > 0 else DOWN)
    light = None if heavy is None else (DOWN if heavy == UP else UP)

    rows = []
    for r in (resting or ()):
        try:
            rows.append({"coid": str(r["coid"]),
                         "side": str(r["side"]).upper(),
                         "price": D(str(r["price"])),
                         "remaining": D(str(r["remaining"])),
                         "submitted_s": r.get("submitted_s")})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"ok": False, "reason": "RESTING_ORDER_UNREADABLE",
                    "actions": [], "holds": {UP: "RESTING_ORDER_UNREADABLE",
                                             DOWN: "RESTING_ORDER_UNREADABLE"},
                    "evaluated": {}, "state": {}, "phase": "UNKNOWN"}
    by_side = {UP: [r for r in rows if r["side"] == UP],
               DOWN: [r for r in rows if r["side"] == DOWN]}
    rest_qty = {s: sum((r["remaining"] for r in by_side[s]), D(0))
                for s in (UP, DOWN)}
    rest_usd = {s: sum((r["remaining"] * r["price"] for r in by_side[s]),
                       D(0)) for s in (UP, DOWN)}
    worst_now = worst_settlement_loss(
        up_q=up_q, dn_q=dn_q, up_cost=up_cost, dn_cost=dn_cost,
        rest_up_usd=rest_usd[UP], rest_dn_usd=rest_usd[DOWN])
    state = {"up_q": str(up_q), "dn_q": str(dn_q),
             "up_cost": str(up_cost), "dn_cost": str(dn_cost),
             "up_avg": (str(up_cost / up_q) if up_q > 0 else None),
             "dn_avg": (str(dn_cost / dn_q) if dn_q > 0 else None),
             "realized_residual": str(resid), "heavy": heavy,
             "resting": [{k: str(v) for k, v in r.items()} for r in rows],
             "resting_qty": {s: str(v) for s, v in rest_qty.items()},
             "cash_committed": str(cash_committed),
             "cash_cap": str(cash_cap),
             "worst_settlement_loss": str(worst_now),
             "max_worst_loss": str(max_worst_loss),
             "pair_ceiling": str(ceiling),
             "complete_target": str(target),
             "complete_ceiling": str(hard),
             "maker_fee_per_contract": str(fee),
             "entry_refusal": entry_refusal,
             "complete_refusal": complete_refusal}
    out: Dict[str, Any] = {"ok": True, "market": str(market),
                           "actions": [], "holds": {}, "evaluated": {},
                           "state": state}
    phase = ("ENTRY" if entry_refusal is None else
             "COMPLETE_ONLY" if complete_refusal is None else "CLOSED")
    out["phase"] = phase

    # ------------------------------------------- REDUCE, EVALUATED ONLY
    if heavy is not None:
        hq = up_q if heavy == UP else dn_q
        hc = up_cost if heavy == UP else dn_cost
        try:
            sell_px = (D(str(bid)) if heavy == UP else D(1) - D(str(ask)))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            sell_px = None
        basis_h = (hc / hq) if (hq > 0 and hc > 0) else None
        out["evaluated"][SELL_REDUCE] = {
            "authorized": False, "side": heavy, "qty": str(abs(resid)),
            "sell_price_at_touch": None if sell_px is None else str(sell_px),
            "basis": None if basis_h is None else str(basis_h),
            "realized_if_sold": (None if (sell_px is None or basis_h is None)
                                 else str(abs(resid) * (sell_px - basis_h))),
            "labels": ("selling is NOT authorized live; this is the value it "
                       "would have had, recorded so the choice not to sell "
                       "is auditable")}

    if orders_uncertain:
        out["holds"] = {UP: "ORDERS_UNCERTAIN", DOWN: "ORDERS_UNCERTAIN"}
        out["reason"] = "ORDERS_UNCERTAIN"
        return out
    if tk is None or tk <= 0:
        out["holds"] = {UP: "TICK_UNKNOWN", DOWN: "TICK_UNKNOWN"}
        out["reason"] = "TICK_UNKNOWN"
        return out
    if phase == "CLOSED":
        out["holds"] = {UP: str(complete_refusal),
                        DOWN: str(complete_refusal)}
        return out

    def own(side: str, depth: int) -> Optional[D]:
        p = TS.quote_own_price(side, bid=bid, ask=ask, tick=tk,
                               depth_ticks=max(0, int(depth)))
        return p if (p is not None and p > 0) else None

    skew = max(0, int(skew_ticks)) if heavy is not None else 0
    inc = D(str(qty_increment)) if qty_increment else D("0.01")

    # ------------------------------------------------- the wanted orders
    wanted: Dict[str, Optional[Dict[str, Any]]] = {UP: None, DOWN: None}
    holds: Dict[str, str] = {}
    if heavy is not None:
        hq = up_q if heavy == UP else dn_q
        hc = up_cost if heavy == UP else dn_cost
        basis = (hc / hq) if (hq > 0 and hc > 0) else None
        # ---- COMPLETE the light side
        if basis is None:
            holds[light] = "HELD_COST_UNKNOWN"
        elif complete_refusal is not None:
            holds[light] = str(complete_refusal)
        else:
            p_touch = own(light, int(complete_depth_ticks) - skew)
            if p_touch is None:
                holds[light] = "NO_QUOTABLE_PRICE"
            else:
                limit = _floor_tick(target - basis - fee, tk)
                px = min(p_touch, limit)
                need = _floor_inc(abs(resid), inc)
                if px < tk:
                    holds[light] = "COMPLETION_BELOW_MIN_PRICE"
                elif basis + px + fee > hard:
                    holds[light] = "COMPLETION_ABOVE_HARD_CEILING"
                elif need <= 0:
                    holds[light] = "NEED_BELOW_INCREMENT"
                else:
                    wanted[light] = {
                        "intent": COMPLETE, "side": light, "price": px,
                        "qty": need, "basis": basis,
                        "pair_cost_per_contract": basis + px + fee,
                        "rests_below_touch": bool(px < p_touch),
                        "action": classify(up_q, dn_q, light, need)}
        # ---- ADD the heavy side, only inside the stated band
        band = min(D(imbalance_cap), D(abs_residual_cap))
        room = band - abs(resid) - rest_qty[heavy]
        if entry_refusal is not None:
            holds[heavy] = "ADD_" + str(entry_refusal)
        elif band <= 0:
            holds[heavy] = "ADD_BAND_ZERO"
        elif room <= 0:
            holds[heavy] = "IMBALANCE_BAND_EXHAUSTED"
        elif basis is None:
            holds[heavy] = "HELD_COST_UNKNOWN"
        else:
            p = own(heavy, int(depth_ticks) + skew)
            opp = own(light, int(depth_ticks))
            q = _floor_inc(min(D(clip), room), inc)
            if p is None or opp is None or q <= 0:
                holds[heavy] = "NO_QUOTABLE_PRICE"
            else:
                new_basis = (hc + p * q) / (hq + q)
                if new_basis + opp + 2 * fee > ceiling:
                    holds[heavy] = "ADD_PROJECTED_PAIR_ABOVE_CEILING"
                else:
                    wanted[heavy] = {
                        "intent": ADD, "side": heavy, "price": p, "qty": q,
                        "basis_before": basis, "basis_after": new_basis,
                        "projected_pair": new_basis + opp + 2 * fee,
                        "action": ADD}
    else:
        # ---- flat: both maker quotes
        u, d = own(UP, depth_ticks), own(DOWN, depth_ticks)
        if entry_refusal is not None:
            holds[UP] = holds[DOWN] = str(entry_refusal)
        elif u is None or d is None:
            holds[UP] = holds[DOWN] = "NO_QUOTABLE_PRICE"
        elif u + d + 2 * fee > ceiling:
            holds[UP] = holds[DOWN] = "PAIR_ABOVE_CEILING"
        else:
            intent = REPLENISH if (traded_before or up_q > 0) else OPEN
            # THE FIRST-LEG BAND JUDGES THE PAIR BY ITS CHEAPER LEG, exactly
            # as the round path does: the cheaper leg is the opening and the
            # dearer one its complement, which the band never applies to.
            cheap = min(u, d)
            band_why = ("FIRST_LEG_BELOW_BAND" if (D(lo) > 0 and cheap < D(lo))
                        else "FIRST_LEG_ABOVE_BAND"
                        if (D(hi) > 0 and cheap > D(hi)) else None)
            for s, p in ((UP, u), (DOWN, d)):
                if band_why is not None:
                    holds[s] = band_why
                elif intent == REPLENISH and not replenish_permitted:
                    holds[s] = "REPLENISH_NOT_PERMITTED"
                else:
                    wanted[s] = {"intent": intent, "side": s, "price": p,
                                 "qty": _floor_inc(D(clip), inc),
                                 "pair_cost": u + d, "action": OPEN}

    def _slots(side: str) -> Optional[int]:
        if slots_left is None:
            return None
        try:
            return int(slots_left.get(side))
        except (TypeError, ValueError):
            return 0                 # unreadable: no slot, never a guess

    # ------------------------------------- reconcile wanted with resting
    actions = []
    for s in (UP, DOWN):
        w = wanted[s]
        rs = by_side[s]
        if not rs:
            continue
        if len(rs) > 1:
            # this controller never rests two orders on one side; guessing
            # which to cancel is how a requote becomes an unhedged leg.
            holds[s] = "MULTIPLE_RESTING_ON_SIDE"
            wanted[s] = None
            continue
        r = rs[0]
        why = None
        if w is not None:
            thr = D(int(reprice_ticks)) * tk
            # BEHIND: the book left us, so the order sits deeper than this
            # pass would quote it and is less likely to fill. AHEAD: the
            # book came to us. A completion that is AHEAD is simply a better
            # placed bid still inside its ceiling, so it is never moved down
            # (that would only spend a claim to be less fillable); an opening
            # leg AHEAD is moved, because it now rests nearer the touch than
            # the pair it was priced for.
            behind = (w["price"] - r["price"]) >= thr
            ahead = (r["price"] - w["price"]) >= thr
            # TWO POLICIES FOR A BOOK THAT CAME TO US, chosen by the caller.
            #
            # DEFAULT (owner directive 2026-11-12, "move the bid
            # constantly"): every resting bid follows the touch both ways,
            # every pass it moves by `reprice_ticks` -- an opening leg back
            # to its depth, a completion down to the cheaper pair.
            #
            # `keep_valid_orders` (issue #233 Track A): a completion AHEAD
            # is a better-placed bid inside its ceiling and is kept; an
            # opening leg AHEAD is cancelled only once its pair with the
            # other side's current quote breaches the opening ceiling.
            if keep_valid_orders:
                if ahead and w["intent"] != COMPLETE:
                    o_s = DOWN if s == UP else UP
                    o_px = own(o_s, depth_ticks)
                    ahead = o_px is None or (r["price"] + o_px + 2 * fee
                                             > ceiling)
                moved = behind or (ahead and w["intent"] != COMPLETE)
            else:
                moved = behind or ahead
            left = _slots(s)
            spare = left is None or left >= (1 if w["intent"] == COMPLETE
                                             else 2)
            age = (None if (now_s is None or r.get("submitted_s") is None)
                   else float(now_s) - float(r["submitted_s"]))
            # an order placed BEFORE the fill that made it a completion was
            # priced as an opening leg: it is repriced at once, not after
            # the minimum rest a completion quote serves (PR #218, P0).
            role_changed = (w["intent"] == COMPLETE
                            and stranded_since_s is not None
                            and r.get("submitted_s") is not None
                            and float(r["submitted_s"])
                            < float(stranded_since_s))
            rested = (age is None or age >= float(reprice_min_rest_s)
                      or role_changed)
            if w["intent"] == COMPLETE:
                if w["basis"] + r["price"] + fee > hard:
                    why = "REPRICE_CEILING_BREACH"
                elif moved and rested and spare:
                    why = ("REPRICE_MISSING_LEG" if role_changed
                           else "REPRICE_TOUCH_MOVED")
                elif r["remaining"] < w["qty"] and rested and spare:
                    why = "RESIZE_UNDER_HEDGED"
            elif moved and rested and spare:
                why = "REPRICE_TOUCH_MOVED"
            if why is None and moved and rested and not spare:
                holds[s] = "REPRICE_DEFERRED_CLAIM_SLOTS_LOW"
        if why is not None:
            actions.append({"kind": CANCEL, "coid": r["coid"], "side": s,
                            "price": r["price"], "remaining": r["remaining"],
                            "reason": why})
        else:
            holds.setdefault(s, "KEEP_RESTING")
            wanted[s] = None             # the resting order IS the quote

    cancelled_sides = {a["side"] for a in actions}

    # ------------------------------------ size against shared capital
    def _fits(side: str, px: D, q: D, cash: D, rup: D, rdn: D, intent: str
              ) -> Optional[str]:
        other_side = DOWN if side == UP else UP
        if intent != COMPLETE and _residual_binds(
                side=side, qty=q, resid=resid,
                same_resting=rest_qty[side] + placed_qty[side],
                other_resting=rest_qty[other_side] + placed_qty[other_side],
                k=residual_k, clip_hist=D(clip_hist)):
            return "RESIDUAL_CAP"
        if cash + _cost(side, px, q) > D(cash_cap):
            return "CASH_CAP"
        nu = rup + (px * q if side == UP else 0)
        nd = rdn + (px * q if side == DOWN else 0)
        w_after = worst_settlement_loss(
            up_q=up_q, dn_q=dn_q, up_cost=up_cost, dn_cost=dn_cost,
            rest_up_usd=nu, rest_dn_usd=nd)
        w_before = worst_settlement_loss(
            up_q=up_q, dn_q=dn_q, up_cost=up_cost, dn_cost=dn_cost,
            rest_up_usd=rup, rest_dn_usd=rdn)
        if w_after > D(max_worst_loss) and w_after > w_before:
            return "WORST_CASE_LOSS"
        return None

    def _cost(side: str, px: D, q: D) -> D:
        if cash_cost is None:
            return D(px) * D(q)
        return D(str(cash_cost(side, D(px), D(q))))

    # A cancelled order is NOT counted as freed until the venue confirms
    # the cancel; the caller re-decides after it. So placements in this
    # pass are sized with every resting order still charged, once.
    cash = D(cash_committed)
    rup, rdn = rest_usd[UP], rest_usd[DOWN]
    placed_qty = {UP: D(0), DOWN: D(0)}
    order = sorted((s for s in (UP, DOWN) if wanted[s] is not None),
                   key=lambda s: (wanted[s]["intent"] != COMPLETE, s))
    # a flat OPEN/REPLENISH of BOTH sides is sized as one pair
    if (len(order) == 2 and all(wanted[s]["intent"] in (OPEN, REPLENISH)
                                for s in order)):
        pair_px = (_cost(UP, wanted[UP]["price"], D(1))
                   + _cost(DOWN, wanted[DOWN]["price"], D(1)))
        head = D(cash_cap) - cash
        qmax = _floor_inc(head / pair_px, inc) if pair_px > 0 else D(0)
        for s in order:
            wanted[s]["qty"] = min(wanted[s]["qty"], max(qmax, D(0)))
            if qmax <= 0:
                holds[s] = "CASH_CAP"
    for s in order:
        w = wanted[s]
        q = w["qty"]
        if s in cancelled_sides:
            holds[s] = "AWAITING_CANCEL_CONFIRMATION"
            continue
        # THE LAST CLAIM SLOT ON A SIDE IS KEPT FOR A COMPLETION. Every
        # placement spends one durable claim, and the window's count is the
        # owner's L5_ACC_MAX_ROUNDS; an opening or a replenishment that took
        # the last one could leave the side unable to complete a fill on the
        # other side.
        left = _slots(s)
        if left is not None and left < (1 if w["intent"] == COMPLETE else 2):
            holds[s] = ("CLAIM_SLOTS_EXHAUSTED" if left < 1
                        else "CLAIM_SLOT_RESERVED_FOR_COMPLETION")
            continue
        # shrink to fit rather than refuse: a smaller order is still the
        # same intent, and the gates downstream judge the order as sized.
        why = None
        while q > 0:
            why = _fits(s, w["price"], q, cash, rup, rdn, w["intent"])
            if why is None:
                break
            q = _floor_inc(q - inc, inc)
        if q <= 0:
            holds[s] = why or holds.get(s) or "QTY_BELOW_INCREMENT"
            continue
        cash += _cost(s, w["price"], q)
        placed_qty[s] += q
        if s == UP:
            rup += w["price"] * q
        else:
            rdn += w["price"] * q
        rec = {k: (str(v) if isinstance(v, D) else v) for k, v in w.items()}
        rec.update(kind=PLACE, side=s, price=w["price"], qty=q,
                   key=action_key(market, s, w["price"], q,
                                  epoch=inventory_epoch(up_q, dn_q)))
        actions.append(rec)
    for s in (UP, DOWN):
        if s not in holds and not any(a["side"] == s for a in actions):
            holds[s] = "NOTHING_WANTED"
    # ------------------------------ every disposition, scored and recorded
    if heavy is not None:
        rl = by_side[light][0] if len(by_side[light]) == 1 else None
        mk = next((a for a in actions if a.get("kind") == PLACE
                   and a.get("side") == light), None)
        cx = next((a for a in actions if a.get("kind") == CANCEL
                   and a.get("side") == light), None)
        sc = score_alternatives(
            up_q=up_q, dn_q=dn_q, up_cost=up_cost, dn_cost=dn_cost,
            bid=bid, ask=ask, tick=tk, bid_shares=bid_shares,
            ask_shares=ask_shares, fee=fee,
            resting=(None if rl is None or cx is not None
                     else (rl["price"], rl["remaining"])),
            maker=(None if (mk or wanted.get(light)) is None
                   else (D((mk or wanted[light])["price"]),
                         D((mk or wanted[light])["qty"]))),
            reprice=cx is not None, haircut_ticks=haircut_ticks)
        sc["chosen"] = ("MAKER_REPRICE" if cx is not None
                        else "MAKER_COMPLETE" if mk is not None
                        else "KEEP_RESTING" if rl is not None else "HOLD")
        sc["reason"] = (cx or {}).get("reason") or holds.get(light) \
            or (mk or {}).get("intent")
        out["evaluated"]["alternatives"] = sc
    out["actions"] = actions
    out["holds"] = holds
    return out


# ====================================================================
# EVERY DISPOSITION OF AN IMBALANCE, SCORED THE SAME WAY (issue #233 A)
#
# The measure is the INCREMENTAL WORST-CASE SETTLED P&L: the worse of the
# two settlements of the whole market position after the alternative, minus
# the same figure for holding, computed from the ledger's ACTUAL fill cost
# (never a $1 placeholder, never a mid). Prices are EXECUTABLE: a maker
# completion at our own resting/quoted price, a taker completion at the
# own-side ASK (UP: the YES ask; DOWN: 1 - YES bid) sized to the DISPLAYED
# depth there, a sale at the own-side BID sized to its depth. Taker fees use
# the PMUS formula at the larger of `pmus_fees`' coefficient and the
# owner-stated Tier-1 0.095; no maker rebate is credited anywhere. Every
# maker alternative carries a latency / cancellation haircut of
# `haircut_ticks` per contract; the taker and the sale carry one tick of
# slippage. Only maker alternatives are ever AUTHORIZED: taker and sale
# rows are evaluated and recorded, never placed.
# ====================================================================

TIER1_TAKER_THETA = D("0.095")


def tier1_taker_fee(price: D, qty: D) -> D:
    """theta * q * p * (1 - p), theta = max(pmus_fees, owner Tier-1 0.095)."""
    from src import pmus_fees as _PF
    theta = max(_PF.taker_theta(), TIER1_TAKER_THETA)
    p, q = D(price), D(qty)
    return (theta * q * p * (D(1) - p)).quantize(D("0.0001"))


def worst_settled(up_q: D, dn_q: D, cost: D) -> D:
    """The worse of the two settled outcomes of the whole position."""
    return min(D(up_q) - D(cost), D(dn_q) - D(cost))


def score_alternatives(*, up_q: D, dn_q: D, up_cost: D, dn_cost: D,
                       bid: Any, ask: Any, tick: D, bid_shares: Any = None,
                       ask_shares: Any = None, fee: D = D(0),
                       resting: Any = None, maker: Any = None,
                       reprice: bool = False, haircut_ticks: int = 1
                       ) -> Dict[str, Any]:
    """Score keep / maker complete / maker reprice / taker / sale / hold."""
    up_q, dn_q = D(up_q), D(dn_q)
    cost = D(up_cost) + D(dn_cost)
    resid = up_q - dn_q
    heavy = UP if resid > 0 else DOWN
    light = DOWN if heavy == UP else UP
    need = abs(resid)
    w0 = worst_settled(up_q, dn_q, cost)
    hc = D(int(haircut_ticks)) * D(tick)

    def _d(v: Any) -> Optional[D]:
        try:
            x = D(str(v))
            return x if x.is_finite() else None
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None

    b, a = _d(bid), _d(ask)
    bs, as_ = _d(bid_shares), _d(ask_shares)

    def buy(side: str, px: D, q: D, extra: D) -> Dict[str, Any]:
        u = up_q + (q if side == UP else 0)
        d = dn_q + (q if side == DOWN else 0)
        w1 = worst_settled(u, d, cost + px * q + extra)
        return {"side": side, "price": str(px), "qty": str(q),
                "worst_settled_after": str(w1),
                "incremental_worst_case": str(w1 - w0)}

    rows: Dict[str, Any] = {"HOLD": {"incremental_worst_case": "0",
                                     "worst_settled_after": str(w0),
                                     "authorized": True}}
    if resting is not None:
        px, rem = D(resting[0]), min(D(resting[1]), need)
        r = buy(light, px, rem, fee * rem + hc * rem)
        r.update(authorized=True, if_unfilled="0",
                 haircut=str(hc * rem),
                 note="worst case over fill/no-fill is min(0, if filled)")
        rows["KEEP_RESTING"] = r
    if maker is not None:
        px, q = D(maker[0]), min(D(maker[1]), need)
        r = buy(light, px, q, fee * q + hc * q)
        r.update(authorized=True, haircut=str(hc * q),
                 queue_ahead_displayed=str(bs if light == UP else as_))
        rows["MAKER_REPRICE" if reprice else "MAKER_COMPLETE"] = r
    # taker completion: evaluated only, never placed
    if b is not None and a is not None:
        tpx = a if light == UP else D(1) - b
        depth = as_ if light == UP else bs
        tq = need if depth is None else min(need, depth)
        if tq > 0 and D(0) < tpx < D(1):
            f = tier1_taker_fee(tpx, tq)
            r = buy(light, tpx, tq, f + hc * tq)
            r.update(authorized=False, taker_fee=str(f),
                     executable_depth=None if depth is None else str(depth),
                     note="TAKER IS NOT AUTHORIZED: evaluated only")
            rows["TAKER_COMPLETE"] = r
        # risk-reducing sale of the excess: evaluated only, never placed
        spx = b if heavy == UP else D(1) - a
        sdepth = bs if heavy == UP else as_
        sq = need if sdepth is None else min(need, sdepth)
        hq = up_q if heavy == UP else dn_q
        hcost = D(up_cost) if heavy == UP else D(dn_cost)
        if sq > 0 and D(0) < spx < D(1) and hq > 0:
            f = tier1_taker_fee(spx, sq)
            u = up_q - (sq if heavy == UP else 0)
            d = dn_q - (sq if heavy == DOWN else 0)
            w1 = worst_settled(u, d, cost - (spx - hc) * sq + f)
            rows["SELL_REDUCE"] = {
                "side": heavy, "price": str(spx), "qty": str(sq),
                "basis": str(hcost / hq), "taker_fee": str(f),
                "executable_depth": None if sdepth is None else str(sdepth),
                "worst_settled_after": str(w1),
                "incremental_worst_case": str(w1 - w0),
                "realized_vs_basis": str(sq * (spx - hcost / hq) - f),
                "authorized": False,
                "note": "SELLING IS NOT AUTHORIZED: evaluated only; never "
                        "assumed profitable"}
    best = max(rows, key=lambda k: D(rows[k]["incremental_worst_case"]))
    best_auth = max((k for k in rows if rows[k].get("authorized")),
                    key=lambda k: D(rows[k]["incremental_worst_case"]))
    return {"basis": "INCREMENTAL_WORST_CASE_SETTLED_PNL",
            "worst_settled_now": str(w0), "residual_side": heavy,
            "residual_qty": str(need), "alternatives": rows,
            "best_any": best, "best_authorized": best_auth,
            "labels": ("executable prices and displayed depth only; never "
                       "mid_up + mid_down. No rebate credited.")}
