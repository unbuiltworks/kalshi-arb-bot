"""The late-window favourite maker candidate. PURE AND DEFAULT-OFF.

WHAT IT IS. In the last seconds of a 5-minute UP/DOWN window most of the
settlement average is already observed, so the outcome can be far more
certain than the book's price implies. This module decides whether to
rest a MAKER buy on the favoured side at one of a few configured prices,
and returns the reason when it will not.

WHAT IT IS NOT. It places nothing, holds no credential, reads no clock
and performs no I/O. It is a candidate generator: `l5_btc5m_controller`
asks it for one order and arbitrates between it and the straddle against
ONE exposure budget. Nothing here can consume capital on its own.

WHY MAKER-ONLY IS THE HARD PART, stated because it is the whole economic
risk. At 0.97 a losing share costs 0.97 and a winning one earns 0.03, so
gross break-even is a 97% win rate and ONE loss cancels about 32 wins. A
resting bid fills only when somebody sells into it, and selling a
near-certain winner below its value is exactly what an informed or
distressed counterparty does. The measurement we have -- 98.21% of
windows won when the favourite's ASK sat in 0.97-0.98, +1.21c a share
over 29 markets -- is the TAKER population: it says what happens when we
could have lifted an offer, not what happens when our own bid is hit.
Those differ by precisely the adverse selection this strategy must
survive, and no data we hold measures it. So:

  * the default is OFF, and `enabled()` is the only way in;
  * `min_edge_per_share` must be set deliberately -- there is no default
    margin that makes an unmeasured edge safe;
  * every refusal is named, so a disabled or gated run still records
    what it WOULD have done and at what price.

FAIL CLOSED, ALWAYS. No probability, a stale probability, an unreadable
book, a one-sided book with no ask, a price that would cross, no depth,
no room in the shared budget, no daily-loss headroom, or an estimate
that does not clear the margin -- each returns a refusal and no order.
An absent input is never read as a favourable one.
"""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence

D = Decimal
ZERO = D("0")
ONE = D("1")
CENT = D("0.01")

ENABLE_ENV = "L5_LATE_FAVORITE"
#: the prices this strategy may rest at, cheapest first. 0.97 is the
#: primary candidate; 0.98 and 0.99 are kept so the controller can record
#: what each WOULD have cost and earned in the same window.
#: MARKETABLE-LIMIT, PREPARED AND OFF.
#:
#: The maker ladder rests BELOW the ask and fills only if the market
#: comes down to it. Measured over 11 windows, 120 live books: a 0.97
#: limit rests at the front of the queue in 3 of 120 moments, and would
#: CROSS in 80. So the same price is a 2.5% opportunity as a maker and a
#: 67% one as a taker -- they are different strategies wearing one label.
#:
#: THE PRICE PAID IS THE ASK, NOT THE LIMIT. Across those 80 crossable
#: moments the average offer was 0.8219, not 0.97. A limit of 0.97 does
#: not buy a 97c favourite; it buys whatever is offered. So the edge is
#: computed at the price we would ACTUALLY PAY, with the venue's real
#: taker fee at that price, and never at the limit.
#:
#: OFF unless the variable is exactly "YES" AND the standing envelope
#: says `taker_allowed`. Two independent switches, because crossing the
#: spread is the one action that always fills and therefore the one that
#: can lose money fastest while looking like success.
MARKETABLE_ENV = "L5_LATE_FAVORITE_MARKETABLE"


def marketable_enabled() -> bool:
    """Exactly "YES", by the SAME rule as `enabled()`.

    Deliberately not stricter. A case-sensitive variant here would make
    `L5_LATE_FAVORITE=yes` work while
    `L5_LATE_FAVORITE_MARKETABLE=yes` silently did not -- safe in
    direction, but an operator trap, and two adjacent flags obeying two
    rules is worse than one rule. The protection is the two INDEPENDENT
    switches (this and the envelope's `taker_allowed`), not spelling.
    """
    return str(os.environ.get(MARKETABLE_ENV, "")).strip().upper() == "YES"


DEFAULT_PRICES = (D("0.97"), D("0.98"), D("0.99"))
#: HOW LATE THE WINDOW MUST BE. The edge is "most of the 60-second
#: average is already observed", which is only true near the end.
#:
#: THIRTY, NOT FORTY-FIVE, so this never places while the straddle still
#: might: `Params.no_new_entry_after_s` is 270 of a 300-second window, so
#: at 45 the two strategies overlapped for fifteen seconds and could both
#: allocate from the same budget in one step.
DEFAULT_WINDOW_S = D("30")
#: a probability older than this is not a probability. The feed polls
#: about once a second, so two seconds is already two misses.
MAX_P_AGE_S = D("2.5")


def enabled() -> bool:
    """True only for exactly "YES". Anything else is OFF."""
    return str(os.environ.get(ENABLE_ENV, "")).strip().upper() == "YES"


def _dec(x: Any) -> Optional[Decimal]:
    if isinstance(x, Decimal):
        return x if x.is_finite() else None
    if x is None or isinstance(x, bool):
        return None
    try:
        v = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return v if v.is_finite() else None


def ev_per_share(p_win: Decimal, price: Decimal,
                 fee_per_share: Decimal) -> Decimal:
    """Expected dollars a share earns, net of fee, if it fills.

    A win pays 1 and costs `price`; a loss pays nothing and costs
    `price`. The fee is charged either way, which is why it is outside
    the bracket -- a fee only on winners would flatter this.
    """
    return p_win * (ONE - price) - (ONE - p_win) * price - fee_per_share


def break_even_p(price: Decimal, fee_per_share: Decimal) -> Decimal:
    """The win rate at which `ev_per_share` is exactly zero."""
    return price + fee_per_share


def evaluate(*, p_up: Any, p_age_s: Any, t_in_window_s: Any,
             window_s: Any, book: Any, inventory: Any, resting: Any,
             room_usd: Any, loss_room_usd: Any,
             clip_usd: Any, fee_per_share: Any,
             min_edge_per_share: Any,
             taker_allowed: Any = False,
             taker_fee_fn: Any = None,
             prices: Sequence[Decimal] = DEFAULT_PRICES,
             late_window_s: Decimal = DEFAULT_WINDOW_S,
             min_qty: Any = CENT) -> Dict[str, Any]:
    """One candidate order, or the named reason there is none.

    `room_usd` is what the SHARED exposure budget still allows after
    every other strategy and every resting order; `loss_room_usd` is
    what the daily-loss halt still allows. Both are passed in rather
    than read here, because a strategy that computed its own budget
    could not be prevented from double-spending it.
    """
    levels = [d for d in (_dec(p) for p in prices) if d is not None]
    out: Dict[str, Any] = {"ok": False, "reason": None, "order": None,
                           "enabled": enabled(), "levels": [str(x) for x in levels]}

    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        out.update(kw, ok=False, reason=reason, order=None)
        return out

    if not out["enabled"]:
        return no("LATE_FAVORITE_OFF", env=ENABLE_ENV)
    if not levels:
        return no("NO_PRICE_LEVELS")

    p = _dec(p_up)
    if p is None or not (ZERO <= p <= ONE):
        return no("NO_PROBABILITY")
    age = _dec(p_age_s)
    if age is None or age < ZERO or age > MAX_P_AGE_S:
        return no("PROBABILITY_STALE", p_age_s=None if age is None else str(age))

    t, w = _dec(t_in_window_s), _dec(window_s)
    if t is None or w is None or w <= ZERO or t < ZERO:
        return no("WINDOW_UNREADABLE")
    left = w - t
    out["t_left_s"] = str(left)
    if left < ZERO:
        return no("WINDOW_OVER")
    #: AN UNSET LATE WINDOW REFUSES, and says so as itself.
    #:
    #: It used to fall through to DEFAULT_WINDOW_S, and a caller that
    #: passed None reached `left > None` and raised. Worse, the default
    #: of 30 is unreachable: the session steps only while
    #: `t < window_s - CANCEL_LEAD_S`, so a 30-second window on a
    #: 300-second market begins exactly where stepping ends. Every step
    #: of every window then refused TOO_EARLY -- indistinguishable, in a
    #: log, from a market with no opportunity in it.
    lw = _dec(late_window_s)
    if lw is None or lw <= ZERO:
        return no("NO_LATE_WINDOW_SET")
    if left > lw:
        return no("TOO_EARLY", late_window_s=str(lw))

    if not isinstance(book, Mapping):
        return no("BOOK_UNUSABLE")
    #: the favoured SIDE, and the ask we must stay strictly below.
    side = "UP" if p >= D("0.5") else "DOWN"
    p_win = p if side == "UP" else (ONE - p)
    out["side"], out["p_win"] = side, str(p_win)
    up_ask, up_bid = _dec(book.get("ask")), _dec(book.get("bid"))
    if side == "UP":
        ask, depth = up_ask, _dec(book.get("ask_shares"))
    else:
        #: the DOWN side's ask is 1 - the UP side's bid, and the size
        #: available there is the size resting on the UP bid.
        ask = None if up_bid is None else (ONE - up_bid)
        depth = _dec(book.get("bid_shares"))
    if ask is None or not (ZERO < ask <= ONE):
        return no("NO_ASK_ON_FAVOURITE", side=side)
    out["favourite_ask"] = str(ask)
    if depth is None or depth <= ZERO:
        return no("NO_DEPTH", side=side)
    out["favourite_ask_shares"] = str(depth)

    inv = inventory if isinstance(inventory, Mapping) else None
    if inv is None:
        return no("INVENTORY_UNREADABLE")
    held = _dec((inv.get(side) or {}).get("qty")) if isinstance(
        inv.get(side), Mapping) else ZERO
    if held is None:
        return no("INVENTORY_UNREADABLE")
    if held > ZERO:
        #: one clip a window. Adding to a 97c favourite is how a small
        #: edge becomes a large correlated loss.
        return no("ALREADY_HOLD_FAVOURITE", qty=str(held))

    #: WHAT ALREADY RESTS ON THIS SIDE, read now and JUDGED LAST.
    #:
    #: The straddle rests a CHEAP rung on both sides. If one is still
    #: live on the favourite when this buys at 0.97 and the market then
    #: collapses, the cheap rung fills too and we hold two shares of the
    #: same side at wildly different prices -- a purely directional
    #: position neither strategy chose, and the residual inventory the
    #: concurrent design exists to prevent.
    #:
    #: The conflict is checked AFTER a level has been priced, not before,
    #: so the refusal can carry the order it WOULD have placed. The
    #: arbiter needs that to compare this candidate against the resting
    #: rung's continuation value; without it the only possible rule is
    #: "always refuse", which is not a comparison.
    if resting is None:
        return no("RESTING_UNREADABLE")
    try:
        conflicts = [o for o in resting
                     if str((o or {}).get("side")) == side]
    except TypeError:
        return no("RESTING_UNREADABLE")

    fee = _dec(fee_per_share)
    margin = _dec(min_edge_per_share)
    if fee is None or fee < ZERO:
        return no("FEE_UNREADABLE")
    if margin is None or margin <= ZERO:
        #: there is no safe default margin for an unmeasured edge.
        return no("NO_EDGE_MARGIN_SET")
    room = _dec(room_usd)
    loss_room = _dec(loss_room_usd)
    clip = _dec(clip_usd)
    mq = _dec(min_qty)
    if room is None or loss_room is None or clip is None or mq is None or mq <= ZERO:
        return no("BUDGET_UNREADABLE")

    #: ------------------------------------------------ marketable limit
    #:
    #: Considered BEFORE the maker ladder only in the sense that it is
    #: priced here; the maker path still wins when both qualify, because
    #: resting pays a rebate and crossing pays a fee. This exists for the
    #: 67% of moments where the maker order would simply never fill.
    out["marketable_enabled"] = marketable_enabled()
    out["taker_allowed"] = bool(taker_allowed)
    if out["marketable_enabled"] and out["taker_allowed"]:
        lift = _marketable(
            p_win=p_win, ask=ask, depth=depth, levels=levels,
            fee_fn=taker_fee_fn, margin=_dec(min_edge_per_share),
            room=_dec(room_usd), loss_room=_dec(loss_room_usd),
            clip=_dec(clip_usd), min_qty=_dec(min_qty), side=side)
        out["marketable"] = lift
    else:
        out["marketable"] = {"ok": False, "reason": (
            "MARKETABLE_OFF" if not out["marketable_enabled"]
            else "TAKER_NOT_AUTHORIZED_BY_ENVELOPE")}

    #: EVERY LEVEL IS PRICED, cheapest first, and the first that clears
    #: the margin AND fits both budgets wins. The rest are reported so a
    #: gated run still records what each would have been worth.
    considered = []
    for px in sorted(levels):
        ev = ev_per_share(p_win, px, fee)
        row = {"price": str(px), "ev_per_share": str(ev),
               "break_even_p": str(break_even_p(px, fee))}
        if px >= ask:
            row["reject"] = "WOULD_CROSS"
            considered.append(row); continue
        if ev < margin:
            row["reject"] = "EDGE_BELOW_MARGIN"
            considered.append(row); continue
        want = min(clip, room, loss_room) / px
        qty = (want // mq) * mq
        if qty <= ZERO:
            row["reject"] = "NO_ROOM"
            considered.append(row); continue
        if qty > depth:
            qty = (depth // mq) * mq
            if qty <= ZERO:
                row["reject"] = "NO_DEPTH"
                considered.append(row); continue
        cost = px * qty
        #: THE WORST CASE IS THE WHOLE COST. A losing favourite pays
        #: nothing, so the daily-loss budget must hold the full outlay,
        #: not the 3c of upside.
        if cost > loss_room:
            row["reject"] = "LOSS_BUDGET"
            considered.append(row); continue
        row.update(qty=str(qty), cost_usd=str(cost))
        considered.append(row)
        candidate = {"op": "PLACE", "side": side, "price": px,
                     "qty": qty, "post_only": True, "intent": "BUY",
                     "reason": "LATE_FAVORITE", "slot": 0,
                     "strategy": "LATE_FAVORITE"}
        #: EV OF THE WHOLE CLIP, which is what the arbiter compares
        #: against keeping the rung -- a per-share figure would make a
        #: 2-share candidate look like a 1-share one.
        out.update(considered=considered, ev_per_share=str(ev),
                   ev_usd=str(ev * qty), cost_usd=str(cost))
        if conflicts:
            #: NAMED, not just counted: the arbiter cancels EXACTLY this
            #: order and nothing else.
            out.update(ok=False, reason="SIDE_ALREADY_WORKED",
                       order=None, blocked_order=candidate,
                       #: `id` is the key `l5_btc5m_controller._read_resting`
                       #: normalises to and `_cancel_all` emits, so an exact
                       #: cancel here uses the SAME identifier the executor
                       #: already cancels by. A second naming would be a
                       #: second chance to cancel the wrong order.
                       conflicts=[{"side": str((o or {}).get("side")),
                                   "price": str((o or {}).get("price")),
                                   "qty": str((o or {}).get("qty")),
                                   "id": (o or {}).get("id")}
                                  for o in conflicts][:4])
            return out
        out.update(ok=True, reason=None, order=candidate)
        return out
    out["considered"] = considered
    return no("NO_LEVEL_QUALIFIED")


def _marketable(*, p_win: Decimal, ask: Optional[Decimal],
                depth: Optional[Decimal], levels: Sequence[Decimal],
                fee_fn: Any, margin: Optional[Decimal],
                room: Optional[Decimal], loss_room: Optional[Decimal],
                clip: Optional[Decimal], min_qty: Optional[Decimal],
                side: str) -> Dict[str, Any]:
    """Lift the offer, or the named reason not to.

    THE EDGE IS MEASURED AT THE ASK. A limit of 0.99 that lifts an offer
    at 0.84 is an 0.84 trade, and judging it at 0.99 would call a losing
    purchase a winning one. Everything below -- the fee, the margin, the
    size -- is computed against the price actually paid.

    The venue's taker fee is `theta * p * (1-p)`, so it is LARGEST in the
    middle of the range: about 1.03c at 0.82 against 0.20c at 0.97. At an
    0.82 fill that moves break-even from 82% to roughly 83%, which is a
    material share of a thin edge and is why it is charged here rather
    than noted afterwards.

    NO FEE FUNCTION, NO TRADE. An unknown cost is never zero.
    """
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason, order=None)

    if ask is None or not (ZERO < ask <= ONE):
        return no("NO_ASK_TO_LIFT")
    if depth is None or depth <= ZERO:
        return no("NO_DEPTH_TO_LIFT")
    if margin is None or margin <= ZERO:
        return no("NO_EDGE_MARGIN_SET")
    if room is None or loss_room is None or clip is None or min_qty is None \
            or min_qty <= ZERO:
        return no("BUDGET_UNREADABLE")
    #: THE ASK MUST LIE WITHIN THE LADDER, NOT MERELY UNDER ITS TOP.
    #:
    #: A ceiling alone admits anything cheaper, and that is how this went
    #: wrong: with only `ask <= 0.99` the gate bought a 0.22 ask at 2145z
    #: against a model reading 0.5025 -- a coin-flip called against a 22%
    #: market, scored as +27c of "edge", and it lost. Three sampled
    #: entries came in at 0.45, 0.42 and 0.89. None of those is a
    #: 97-99c favourite; the strategy had quietly become "bet the index
    #: model beats the market on near-even outcomes", which is a
    #: different proposition with no evidence behind it.
    #:
    #: So the executable band is the LADDER's own range, floor and
    #: ceiling. It is derived from `levels` rather than written as a new
    #: constant, so changing the ladder moves the band with it and the
    #: two can never disagree.
    if not levels:
        return no("NO_PRICE_LEVELS")
    floor_, limit = min(levels), max(levels)
    if ask < floor_:
        return no("ASK_BELOW_BAND", ask=str(ask), floor=str(floor_),
                  limit=str(limit),
                  why=("an offer under the ladder is not a favourite at "
                       "this strategy's prices, whatever the model says"))
    if ask > limit:
        return no("ASK_ABOVE_LIMIT", ask=str(ask), limit=str(limit))
    if fee_fn is None:
        return no("TAKER_FEE_UNKNOWN")
    try:
        fee = _dec(fee_fn(ask, ONE))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return no("TAKER_FEE_UNREADABLE", error=type(exc).__name__)
    if fee is None or fee < ZERO:
        return no("TAKER_FEE_UNREADABLE")

    ev = p_win - ask - fee
    row = {"price_paid": str(ask), "taker_fee_per_share": str(fee),
           "ev_per_share": str(ev), "break_even_p": str(ask + fee),
           "limit": str(limit), "p_win": str(p_win)}
    if ev < margin:
        return no("EDGE_BELOW_MARGIN", **row)

    want = min(clip, room, loss_room) / ask
    qty = (want // min_qty) * min_qty
    if qty <= ZERO:
        return no("NO_ROOM_FOR_ONE_LOT", **row)
    qty = min(qty, depth)                 # never more than is offered
    qty = (qty // min_qty) * min_qty
    if qty <= ZERO:
        return no("DEPTH_BELOW_ONE_LOT", **row, depth=str(depth))
    return {"ok": True, "reason": None, **row, "qty": str(qty),
            "cost_usd": str(qty * ask), "ev_usd": str(qty * ev),
            "order": {"side": side, "price": str(ask), "qty": str(qty),
                      "post_only": False, "intent": "BUY",
                      "reason": "LATE_FAVORITE_MARKETABLE",
                      "strategy": "LATE_FAVORITE",
                      "execution": "MARKETABLE_LIMIT"}}
