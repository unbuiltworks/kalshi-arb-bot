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
