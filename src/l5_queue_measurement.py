"""THE QUEUE MEASUREMENT. One maker BUY UP, one authorization, bounded time.

SCOPE, AND WHY IT IS A SEPARATE MODULE. This is not a strategy and it must
never become one. It builds exactly ONE order, for ONE authorized experiment,
to answer the one question historical replay structurally cannot: where our
resting order actually sits in the queue. Keeping it out of
`l5_live_execution`'s decision path means the ordinary L5 shadow logic is
untouched -- nothing here runs unless an experiment is named AND its market
matches, and `active()` is false in every other configuration, including the
shadow-locked one running today.

WHAT IT DELIBERATELY DOES NOT DO.

  * It does not size from lambda. The engine's ordinary sizing is
    behavioural; this order's size is whatever the $1 ALL-IN bound permits at
    the touch, and if no legal quantity exists the order is REFUSED. There is
    no fallback to L5 sizing -- a fallback would quietly turn a measurement
    into a trade.
  * It never crosses. A BUY above the best bid, or at or above the ask, is
    not a maker order; it is refused rather than repriced.
  * It never assumes a cancellation. The deadline fires a cancel; only the
    venue's own re-read, showing a terminal state AND zero remaining, retires
    the order. Anything else is UNCERTAIN.

THE $1 IS ALL-IN, FEES INCLUDED, WHICH THE CASH GATE DOES NOT DO.
`ValidationGates.max_cash_deployed_usd` counts cash: `pending_obligation` is
`qty * price` and excludes fees. PMUS charges `Fee = theta * C * p * (1 - p)`
with taker theta 0.0695; the maker rebate (theta -0.0125) is a CREDIT and is
never assumed, so sizing is done against the worst case that the taker fee is
charged. That makes this bound strictly tighter than the cash gate, which
remains the backstop.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
from decimal import Decimal as D, ROUND_FLOOR
from typing import Any, Dict, Mapping, Optional

#: the experiment and its market. BOTH must be named: an experiment with no
#: market, or a market with no experiment, is not an authorized measurement.
EXPERIMENT_ENV = "L5_EXPERIMENT_ID"
MARKET_ENV = "L5_QM_MARKET"

#: worst-case taker fee coefficient. The maker rebate is NOT applied.
TAKER_THETA = D("0.0695")
#: the all-in ceiling, fees included.
ALL_IN_CAP = D("1.00")
#: contract ceiling: ValidationGates.max_unmatched_contracts.
MAX_CONTRACTS = D("5")
#: the price band. Outside it an episode is censored for want of flow rather
#: than for queue position, which measures nothing.
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.90")

#: timing, in seconds from the window's own bounds.
WINDOW_S = 900
REST_S = 120.0                 #: T_ack + this
CANCEL_MARGIN_S = 180.0        #: T_end - this
PLACE_OFFSET_S = 480.0         #: T_start + this
MIN_REST_S = 60.0              #: every placed order gets at least this long

#: how stale the book may be and still be placed against.
MAX_BOOK_AGE_S = 2.0


def experiment_id() -> str:
    return (os.getenv(EXPERIMENT_ENV) or "").strip()


def experiment_market() -> str:
    return (os.getenv(MARKET_ENV) or "").strip()


def active(market: Optional[str] = None) -> bool:
    """Is the queue measurement authorized for this market RIGHT NOW?

    False unless an experiment and a market are both named and the market
    matches. Absent is never a default: with nothing set -- today's
    configuration -- this module does nothing at all.
    """
    exp, mkt = experiment_id(), experiment_market()
    if not exp or not mkt:
        return False
    return market is None or str(market) == mkt


# --------------------------------------------------------------- timing

def cancel_cutoff(t_end: float) -> float:
    """The absolute per-window bound. Nothing may rest past it."""
    return float(t_end) - CANCEL_MARGIN_S


def place_cutoff(t_start: float, t_end: float) -> float:
    """THE SINGLE placement bound: min(T_start + 480, T_end - 240).

    One number, not three. It is by construction at least MIN_REST_S before
    `cancel_cutoff`, so an order placed at the last legal instant still gets
    a measurable rest and the old separate "hard bar" is unreachable.
    """
    return min(float(t_start) + PLACE_OFFSET_S,
               cancel_cutoff(t_end) - MIN_REST_S)


def t_cancel(t_ack: float, t_end: float) -> float:
    """min(T_ack + 120, T_end - 180), from the VENUE ACKNOWLEDGEMENT.

    Measured from the ack rather than from submission or from a planned
    instant: the order did not exist until the venue said so, and a plan is
    not evidence of when it started resting.
    """
    return min(float(t_ack) + REST_S, cancel_cutoff(t_end))


def may_place(now: float, t_start: float, t_end: float) -> Optional[str]:
    """None when placement is permitted, else the refusal."""
    if now < float(t_start) + PLACE_OFFSET_S - 240.0:
        return "BEFORE_PLACE_WINDOW"
    if now >= place_cutoff(t_start, t_end):
        return "AFTER_PLACE_CUTOFF"
    return None


# ---------------------------------------------------- order construction

def all_in_cost(qty: D, price: D) -> D:
    """Cash plus the WORST-CASE fee, which is what the $1 bounds."""
    return qty * price + TAKER_THETA * qty * price * (D(1) - price)


def legal_quantity(price: D, tick_qty: D, min_qty: D,
                   depth: D) -> Optional[D]:
    """The largest quantity satisfying every bound, or None.

    None means REFUSE. There is deliberately no next-best answer: a
    measurement that silently shrinks past the venue minimum, or past the
    displayed depth it was meant to queue behind, is not the measurement.
    """
    if price <= 0 or tick_qty <= 0:
        return None
    # all_in(C, p) = C * p * (1 + theta * (1 - p)) <= cap
    denom = price * (D(1) + TAKER_THETA * (D(1) - price))
    if denom <= 0:
        return None
    qty = (ALL_IN_CAP / denom).quantize(tick_qty, rounding=ROUND_FLOOR)
    qty = min(qty, MAX_CONTRACTS, depth)
    qty = qty.quantize(tick_qty, rounding=ROUND_FLOOR)
    if qty < min_qty or qty <= 0:
        return None
    if all_in_cost(qty, price) > ALL_IN_CAP:        # belt and braces
        return None
    return qty


def build_order(*, market: str, bid: Any, ask: Any, bid_depth: Any,
                tick: Any, min_qty: Any, qty_increment: Any = None,
                book_age_s: Any = None,
                gap_suspected: bool = False) -> Dict[str, Any]:
    """Exactly one maker BUY UP at the current best bid, or a refusal.

    Returns {"ok": True, "intent": {...}} or {"ok": False,
    "reason": "..."}. Every refusal is terminal for the episode: the caller
    aborts WITHOUT placing rather than adjusting anything.
    """
    def _d(v: Any) -> Optional[D]:
        if v is None:
            return None
        try:
            return D(str(v))
        except Exception:                                      # noqa: BLE001
            return None

    def no(reason: str, **extra: Any) -> Dict[str, Any]:
        return dict({"ok": False, "reason": reason, "market": market}, **extra)

    if not str(market or "").strip():
        return no("NO_MARKET")
    if gap_suspected:
        return no("GAP_SUSPECTED")
    b, a = _d(bid), _d(ask)
    if b is None or a is None:
        return no("BOOK_NOT_TWO_SIDED")
    if b <= 0 or a <= 0 or b >= a:
        # b >= a is a crossed or locked book: joining the bid would be at or
        # through the ask, which is not a maker order.
        return no("BOOK_CROSSED_OR_LOCKED", bid=str(b), ask=str(a))
    age = _d(book_age_s)
    if age is None or age > D(str(MAX_BOOK_AGE_S)):
        return no("BOOK_STALE", book_age_s=(None if age is None else str(age)))
    if b < MIN_PRICE or b > MAX_PRICE:
        return no("PRICE_OUT_OF_BAND", bid=str(b))
    tk = _d(tick)
    if tk is None or tk <= 0:
        return no("TICK_UNKNOWN")
    if b != b.quantize(tk, rounding=ROUND_FLOOR):
        return no("PRICE_NOT_ON_TICK", bid=str(b), tick=str(tk))
    mq = _d(min_qty)
    if mq is None or mq <= 0:
        return no("MIN_QTY_UNKNOWN")
    inc = _d(qty_increment) or mq
    if inc <= 0:
        return no("QTY_INCREMENT_UNKNOWN")
    dep = _d(bid_depth)
    if dep is None or dep <= 0:
        return no("NO_DISPLAYED_DEPTH")
    if dep < mq:
        return no("DEPTH_BELOW_MIN_QTY", depth=str(dep), min_qty=str(mq))
    qty = legal_quantity(b, inc, mq, dep)
    if qty is None:
        return no("NO_LEGAL_QUANTITY", bid=str(b), min_qty=str(mq),
                  depth=str(dep),
                  note=("no quantity satisfies the venue minimum, the "
                        "displayed depth, the 5-contract cap and the $1 "
                        "all-in bound at once. There is no fallback to "
                        "lambda-based L5 sizing."))
    cost = all_in_cost(qty, b)
    return {"ok": True, "intent": {
        "market": market, "side": "UP", "action": "BUY",
        "price": str(b), "qty": str(qty), "mode": "MAKER_JOIN_BEST_BID",
        "post_only": True,
        "cash_usd": str(qty * b), "all_in_usd": str(cost),
        "worst_case_fee_usd": str(cost - qty * b),
        "labels": ("one maker BUY UP joining the best bid, never crossing. "
                   "Size is the $1 ALL-IN bound including the worst-case "
                   "taker fee; the maker rebate is never assumed. No "
                   "complement is quoted and there is no lambda fallback.")}}


# ------------------------------------------------- durable ack + deadline

class DeadlineStore:
    """The ack time and venue order id, durable BEFORE the deadline is used.

    WHY ON DISK AND WHY FIRST. The deadline is only meaningful if it survives
    the process. A restart that forgot when the order was acknowledged cannot
    know whether 120 seconds have passed, and the safe reading of "I do not
    know" is not "assume cancelled" -- it is UNCERTAIN. So the record is
    written and fsynced the moment the venue answers, and a restart reads it
    back before anything else is allowed to proceed.

    Lives beside the authorization ledger on the same mounted volume, so a
    deployment replaces the container and not the record.
    """

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self.state: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self.state = json.load(fh) or {}
        except FileNotFoundError:
            self.state = {}
        except Exception:                                      # noqa: BLE001
            # an unreadable record is NOT an absent one.
            self.state = {"__unreadable__": True}

    def _save(self) -> None:
        p = pathlib.Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(p) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, separators=(",", ":"), default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(p))

    def unreadable(self) -> bool:
        return bool(self.state.get("__unreadable__"))

    def record_ack(self, *, experiment: str, market: str,
                   venue_order_id: str, t_ack: float, t_end: float,
                   price: str, qty: str) -> Dict[str, Any]:
        """Persist the ack. Refuses an unusable identity rather than storing
        a deadline it could never enforce against a named order."""
        vid = str(venue_order_id or "").strip()
        if not vid:
            raise ValueError("ACK_WITHOUT_VENUE_ORDER_ID")
        if not t_ack:
            raise ValueError("ACK_WITHOUT_TIMESTAMP")
        rec = {"experiment": experiment, "market": market,
               "venue_order_id": vid, "t_ack": float(t_ack),
               "t_end": float(t_end), "price": str(price), "qty": str(qty),
               "t_cancel": t_cancel(float(t_ack), float(t_end)),
               "utc": dt.datetime.fromtimestamp(
                   float(t_ack), dt.timezone.utc).isoformat(),
               "cancel_confirmed": False}
        self.state = dict(self.state)
        self.state.pop("__unreadable__", None)
        self.state["order"] = rec
        self._save()
        return rec

    def order(self) -> Optional[Dict[str, Any]]:
        o = self.state.get("order")
        return dict(o) if isinstance(o, Mapping) else None

    def confirm_cancel(self, *, terminal: bool, remaining: Any) -> bool:
        """Retire the order ONLY on a terminal state with zero remaining.

        An accepted cancel request is not a cancelled order, so the ack never
        reaches this method -- only a venue RE-READ does. Remaining is
        checked as a quantity, never by matching a state string, because
        "PARTIALLY_FILLED" contains "FILLED".
        """
        o = self.order()
        if o is None:
            return False
        try:
            rem = D(str(remaining))
        except Exception:                                      # noqa: BLE001
            return False
        if not terminal or rem != 0:
            return False
        o["cancel_confirmed"] = True
        self.state = dict(self.state, order=o)
        self._save()
        return True


def due(now: float, rec: Optional[Mapping[str, Any]]) -> bool:
    """Is the cancel due? Unknown timing is NOT 'not due'."""
    if not rec or rec.get("cancel_confirmed"):
        return False
    tc = rec.get("t_cancel")
    if tc is None:
        return True            # a deadline we cannot read is already overdue
    try:
        return float(now) >= float(tc)
    except Exception:                                          # noqa: BLE001
        return True


def recovery_refusal(store: "DeadlineStore") -> Optional[Dict[str, Any]]:
    """After a restart: None when it is safe to continue, else HALT + why.

    An unreadable record, a record with no venue id, and a record with no ack
    time are all the same thing -- an order may be live at the venue and we
    cannot name it or time it. Each one refuses, so the session reconciles
    rather than assuming the order is gone.
    """
    if store.unreadable():
        return {"halt": True, "reason": "DEADLINE_RECORD_UNREADABLE",
                "note": "an unreadable record is not an absent one"}
    rec = store.order()
    if rec is None:
        return None                        # nothing was ever acknowledged
    if rec.get("cancel_confirmed"):
        return None                        # venue-confirmed terminal, zero left
    if not str(rec.get("venue_order_id") or "").strip():
        return {"halt": True, "reason": "ACK_IDENTITY_UNCERTAIN",
                "note": "cannot cancel by id an order we cannot name"}
    if not rec.get("t_ack"):
        return {"halt": True, "reason": "ACK_TIME_UNCERTAIN",
                "note": "cannot evaluate a deadline without an ack time"}
    return {"halt": False, "reason": "RECOVERED_PENDING_CANCEL",
            "venue_order_id": rec["venue_order_id"],
            "t_cancel": rec.get("t_cancel"),
            "note": ("the order must be cancelled by venue id and proved by "
                     "re-read before any further activity")}


def store_for(inventory_store: Any) -> "DeadlineStore":
    """The DeadlineStore beside a given inventory store, cached on it.

    The inventory store already knows the durable root it was opened at, so
    the deadline record lands on the same mounted volume without the caller
    having to know a path.
    """
    st = getattr(inventory_store, "_qm_deadline_store", None)
    if st is None:
        root = os.path.dirname(str(getattr(inventory_store, "path", "")
                                   or "")) or "."
        st = DeadlineStore(os.path.join(root, "queue_measurement.json"))
        try:
            setattr(inventory_store, "_qm_deadline_store", st)
        except Exception:                                      # noqa: BLE001
            pass
    return st


def record_ack_for(inventory_store: Any, *, market: str, venue_order_id: str,
                   t_ack: float, price: str, qty: str) -> Dict[str, Any]:
    """Persist the measurement ack, deriving T_end from the market's window.

    Raises rather than returning on an unusable identity or time, so the
    caller fails closed instead of holding a deadline it cannot enforce.
    """
    from src import l5_pmus_auth_book as AB
    ws = AB.slug_start(market)
    if ws is None:
        raise ValueError("WINDOW_UNKNOWN_FOR_MARKET")
    return store_for(inventory_store).record_ack(
        experiment=experiment_id(), market=market,
        venue_order_id=venue_order_id, t_ack=t_ack,
        t_end=float(ws) + WINDOW_S, price=price, qty=qty)
