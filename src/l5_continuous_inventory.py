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


def action_key(market: str, side: str, price: D, qty: D) -> str:
    """Content-addressed idempotency key. No round, no clock, no counter.

    Same wanted order -> same key -> DUPLICATE_SUPPRESSED. A different
    wanted order gets a different key and must still clear every risk gate.
    """
    return "%s|%s|%s|%s|%s" % (KEY_PREFIX, str(market), str(side).upper(),
                               D(price).normalize(), D(qty).normalize())


def _own(side: str, bid: Any, ask: Any, tick: D, depth: int) -> Optional[D]:
    return TS.quote_own_price(side, bid=bid, ask=ask, tick=tick,
                              depth_ticks=depth)


def decide(*, market: str, bid: Any, ask: Any, tick: D,
           up_q: D = D(0), dn_q: D = D(0),
           up_cost: D = D(0), dn_cost: D = D(0),
           clip: D = D("2.52"), depth_ticks: int = 2,
           cash_spent: D = D(0), cash_cap: D = D("12"),
           may_open: bool = True, residual_cap: D = D(5),
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
        if held_qty > 0 and D(held_cost) > 0:
            basis = D(held_cost) / D(held_qty)
            q = D(clip)
            new_basis = (D(held_cost) + aown * q) / (held_qty + q)
            if new_basis < basis and _afford(aown, q) \
                    and abs(resid) + q <= D(residual_cap):
                return {"ok": True, "action": ADD, "side": aside,
                        "price": aown, "qty": q,
                        "basis_before": basis, "basis_after": new_basis,
                        "key": action_key(market, aside, aown, q),
                        "labels": ("an ADD is permitted only because it "
                                   "LOWERS the average basis, which is what "
                                   "makes a cheaper pair reachable")}
        if red is not None:
            return red
        return {"ok": False, "reason": "ADD_WOULD_NOT_IMPROVE_BASIS"}

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
