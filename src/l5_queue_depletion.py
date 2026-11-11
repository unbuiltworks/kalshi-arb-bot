"""How much displayed queue must clear before a maker order could fill.

WHY THIS EXISTS. Every keep-versus-reprice decision the engine makes is a
bet on P(fill) at our queue position, and that number has never been
measured. 0100z rested 1 contract at own 0.4600 with 2923.02 contracts of
displayed depth ahead of it; nothing in the engine could say whether that
was a quote or a wish.

WHAT DATA WE ACTUALLY HAVE, stated before any estimate is built on it. The
durable calibration ledger holds 17,600 records and reports
`usable_for_calibration: 0`, because that gate demands all five capture
categories and `fee_accounting` can only be supplied by a real venue fill.
Worse for this purpose, the per-order OBSERVATION loop -- the one that
records `prints_at_our_price` -- appended nothing (`events_appended: 0`):
it runs over LIVE orders, and the shadow records are pre-submit only. So
there is NO trade-print series to measure depletion from.

    Verified, not assumed: a log query for `prints_at_our_price` across the
    running deployment returns zero records.

WHAT THERE IS: dense 8-level ladder snapshots WITH SIZES, from three
separate recordings on three dates. From those alone, three verdicts are
available, and only three. This module never converts them into a
probability, because a probability is not in this data.

  PROVEN     the opposite side's touch reached our price. A resting buy at
             wire p cannot sit above the best ask, so if the ask later
             falls to p or below, our order executed. This is a SUFFICIENT
             condition and gives a genuine lower bound on the fill rate --
             no prints required.

  POSSIBLE   cumulative DECREASE in the at-or-better-priced displayed size
             reached our queue-ahead. A decrease is a trade OR a
             cancellation and the ladder cannot tell them apart, so this is
             a NECESSARY condition only, and the rate at which it holds is
             an UPPER BOUND on the fill rate.

  IMPOSSIBLE the necessary condition never held: not enough size left the
             at-or-better region, under the assumption most favourable to
             filling, for the queue ahead of us ever to clear. This one is
             a hard negative -- it holds whatever the trade/cancel mix was.

CANCELLATIONS, ADDITIONS, PARTIAL FILLS AND PRIORITY, each handled
explicitly rather than averaged away:

  cancellations  counted in the decrease, which is why POSSIBLE is only an
                 upper bound; a cancel advances our position but never
                 fills us.
  additions      ignored. Size added at a BETTER price joins ahead of us
                 and should raise the requirement; size added at OUR price
                 joins behind us and should not. The ladder cannot tell
                 which, so additions are not allowed to reduce the
                 requirement and are not allowed to raise it either. That
                 choice keeps POSSIBLE an upper bound.
  partial fills  the requirement is expressed in contracts of queue, so a
                 partial clearance reduces it proportionally; our own order
                 is treated as indivisible and unfilled until the whole
                 queue ahead has cleared, which is the conservative order.
  priority       strict price priority then FIFO within a level: everything
                 at a strictly better price, plus everything already
                 resting at our price, is ahead of us. Our own size is
                 never counted as ahead of itself.

NO FILL IS EVER CLAIMED FROM A SHADOW RECORD. `verdict` describes what the
BOOK did, not what our order did. A venue-confirmed fill is a different
object and this module does not produce one.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

UP, DOWN = "UP", "DOWN"

#: The three verdicts, and nothing else. UNKNOWN is not a verdict: a case
#: that is neither proven nor impossible is POSSIBLE, which is honest about
#: being an upper bound rather than pretending to be a measurement.
PROVEN = "PROVEN"
POSSIBLE = "POSSIBLE"
IMPOSSIBLE = "IMPOSSIBLE"


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    if isinstance(v, D):
        return v if v.is_finite() else None
    try:
        x = D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return x if x.is_finite() else None


def _levels(raw: Any) -> List[tuple]:
    """[(price, size)] with unreadable rows dropped, never guessed."""
    out: List[tuple] = []
    for row in (raw or []):
        try:
            px, sz = _d(row[0]), _d(row[1])
        except (IndexError, TypeError, KeyError):
            continue
        if px is None or sz is None or px <= 0 or sz < 0:
            continue
        out.append((px, sz))
    return out


def wire_of(side: str, own_px: D) -> D:
    """Own price to the price our order actually rests at.

    UP buys YES and rests on the BID at wire == own. DOWN buys NO, which is
    wired as a SELL of YES, and rests on the ASK at wire 1 - own.
    """
    return own_px if str(side).upper() == UP else (D(1) - own_px)


def rests_on(side: str) -> str:
    return "BID" if str(side).upper() == UP else "ASK"


def queue_ahead(side: str, own_px: Any, *, bids: Any = None,
                asks: Any = None) -> Optional[D]:
    """Displayed size that must clear before the market trades at us.

    Strict price priority then FIFO: everything at a strictly better price
    plus everything already resting at our own level. Returns None when the
    relevant side of the ladder is unreadable -- never zero, because zero
    is the most favourable answer there is and an absent ladder has not
    earned it.
    """
    p = _d(own_px)
    if p is None:
        return None
    w = wire_of(side, p)
    book = _levels(bids if rests_on(side) == "BID" else asks)
    if not book:
        return None
    # a BID at wire w is beaten by HIGHER bids; an ASK at wire w by LOWER
    better = (lambda px: px >= w) if rests_on(side) == "BID" \
        else (lambda px: px <= w)
    return sum((sz for px, sz in book if better(px)), D(0))


def _at_or_better(side: str, own_px: D, frame: Mapping[str, Any]
                  ) -> Optional[D]:
    return queue_ahead(side, own_px, bids=frame.get("bids"),
                       asks=frame.get("asks"))


def _crossed_by_other_side(side: str, own_px: D,
                           frame: Mapping[str, Any]) -> Optional[bool]:
    """Did the OPPOSITE touch reach our resting price?

    A resting buy at wire w cannot persist while the best ask is at or
    below w -- it would have been executed. Symmetrically for a resting
    sell against the best bid. None when that side is unreadable.
    """
    w = wire_of(side, own_px)
    if rests_on(side) == "BID":
        a = _levels(frame.get("asks"))
        return None if not a else (min(px for px, _ in a) <= w)
    b = _levels(frame.get("bids"))
    return None if not b else (max(px for px, _ in b) >= w)


def fill_verdict(side: str, own_px: Any, frames: Sequence[Mapping[str, Any]],
                 *, start: int = 0, own_qty: Any = None) -> Dict[str, Any]:
    """PROVEN / POSSIBLE / IMPOSSIBLE for one resting order. Read-only.

    `frames` are ladder snapshots in time order; `start` is the index at
    which the order is taken to have joined, so everything displayed in
    that frame is ahead of it. `own_qty` is reported but never reduces the
    requirement: our own order is behind the whole queue it joined.
    """
    p = _d(own_px)
    sd = str(side).upper()
    if p is None or sd not in (UP, DOWN):
        return {"verdict": None, "reason": "SIDE_OR_PRICE_UNREADABLE"}
    if not frames or start < 0 or start >= len(frames):
        return {"verdict": None, "reason": "NO_FRAMES"}

    q0 = _at_or_better(sd, p, frames[start])
    if q0 is None:
        return {"verdict": None, "reason": "QUEUE_UNREADABLE"}

    need = q0
    cleared = D(0)
    prev = q0
    proven_at: Optional[int] = None
    possible_at: Optional[int] = None
    touch_reached_at: Optional[int] = None

    for i in range(start + 1, len(frames)):
        f = frames[i]
        # ---- the SUFFICIENT condition, checked first because it settles
        #      the case: a resting order the other side has reached is an
        #      executed order.
        if proven_at is None and _crossed_by_other_side(sd, p, f) is True:
            proven_at = i
        cur = _at_or_better(sd, p, f)
        if cur is None:
            continue                      # unreadable frame decides nothing
        if touch_reached_at is None:
            # our level is the touch when nothing is displayed at a
            # strictly better price, i.e. the at-or-better size is only
            # what sits at our own level
            own_lvl = _own_level_size(sd, p, f)
            if own_lvl is not None and cur <= own_lvl:
                touch_reached_at = i
        # ---- the NECESSARY condition. A decrease is a trade OR a cancel,
        #      so crediting all of it to trades is the most favourable
        #      reading; an INCREASE is not allowed to help or hurt, because
        #      the ladder cannot say whether it joined ahead of us or
        #      behind.
        if cur < prev:
            moved = prev - cur
            cleared += moved
            need -= moved
            if need <= 0 and possible_at is None:
                possible_at = i
        prev = cur

    if proven_at is not None:
        verdict = PROVEN
    elif possible_at is not None:
        verdict = POSSIBLE
    else:
        verdict = IMPOSSIBLE
    return {
        "verdict": verdict,
        "side": sd, "own_px": str(p), "wire_px": str(wire_of(sd, p)),
        "rests_on": rests_on(sd),
        "queue_ahead_at_join": str(q0),
        "displayed_cleared": str(cleared),
        "still_needed": str(need if need > 0 else D(0)),
        "own_qty": (None if _d(own_qty) is None else str(_d(own_qty))),
        "proven_at_frame": proven_at,
        "possible_at_frame": possible_at,
        "touch_reached_at_frame": touch_reached_at,
        "frames_observed": len(frames) - start - 1,
        "labels": ("PROVEN is sufficient and bounds the fill rate from "
                   "BELOW. POSSIBLE is necessary only -- a decrease may be "
                   "a cancellation -- and bounds it from ABOVE. IMPOSSIBLE "
                   "holds whatever the trade/cancel mix was. None of these "
                   "is a fill and none is a probability."),
    }


def _own_level_size(side: str, own_px: D,
                    frame: Mapping[str, Any]) -> Optional[D]:
    w = wire_of(side, own_px)
    book = _levels(frame.get("bids") if rests_on(side) == "BID"
                   else frame.get("asks"))
    if not book:
        return None
    return sum((sz for px, sz in book if px == w), D(0))


# ======================================================= PAIR ECONOMICS
#
# The identity that makes the depth choice an economic one rather than a
# taste: for a binary pair own_UP = best_bid and own_DOWN = 1 - best_ask, so
# the pair available AT THE TOUCH is exactly 1 - spread, and resting each
# leg d ticks behind its own touch gives 1 - spread - 2*d*tick.

def touch_pair(bids: Any, asks: Any) -> Optional[D]:
    b, a = _levels(bids), _levels(asks)
    if not b or not a:
        return None
    bb, ba = max(px for px, _ in b), min(px for px, _ in a)
    if ba <= bb:
        return None
    return D(1) - (ba - bb)


def pair_at_depth(spread: Any, depth_ticks: int, tick: Any) -> Optional[D]:
    sp, tk = _d(spread), _d(tick)
    if sp is None or tk is None or tk <= 0 or int(depth_ticks) < 0:
        return None
    return D(1) - sp - (2 * int(depth_ticks) * tk)


def maker_pair_edge(pair_own_cost: Any, qty: Any = D(1), *,
                    maker_rebate_per_contract: Any = D(0)) -> Optional[D]:
    """Edge on a MATCHED pair. A pair redeems exactly $1.00 a share.

    The rebate defaults to ZERO and must stay there until one is actually
    credited and source-accounted. The published schedule carries no
    maker-fee term, so a maker pair's only trading cost is the pair price
    itself; a zero commission is NOT a rebate.
    """
    p, q = _d(pair_own_cost), _d(qty)
    r = _d(maker_rebate_per_contract) or D(0)
    if p is None or q is None:
        return None
    return q * ((D(1) - p) + r)
