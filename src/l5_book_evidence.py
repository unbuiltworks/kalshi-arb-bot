"""Reporting-only book evidence for the complement-reachability question.

WHAT THIS ANSWERS, AND WHY NOTHING ELSE COULD. Eight settled windows left
the decisive question open: when a first leg filled and the complement never
came back, WAS a fee-safe complement actually displayed at any point while we
held unmatched inventory, and with how much depth? The deployed stack could
not answer it. `FillCalibration` captures a book block at PRE-SUBMIT only, so
nothing is recorded at a fill; `AuthBook.snapshot` keeps the current top of
book and no history at all (it reads a `bbo_history` key that only the
separate evidence recorder ever writes, so `last_update_t` is permanently
None in this process); and the ladder keeps full rungs for exactly one frame.
Between them there is no record of the opposite side at the moment a leg
filled, and none whatsoever for the intervals when no order was resting --
which is precisely when the position sat unmatched.

IT IS REPORTING ONLY, AND THE BOUNDARY IS STRUCTURAL RATHER THAN PROMISED.
This module imports nothing from the execution stack: only `decimal` and
`typing`. It takes books as DATA and returns dicts. It cannot size, price,
submit, cancel or refuse anything, because it is handed no object that could.
Entry, sizing, repricing and cancellation policy are untouched.

A TOUCH IS NOT A FILL, and every field here is named so a reader cannot
mistake one for the other. The maker figures are a quote that was DISPLAYED,
carrying the depth that would trade ahead of us; they are never an executable
opportunity, because the venue publishes no per-order queue position and we
would be joining a rung, not crossing it. The taker figures ARE executable
against displayed depth at the instant observed -- subject to fees, and to
displayed depth being displayed rather than guaranteed.

PRICE SEMANTICS, which have cost real money when assumed. Prices go on the
wire in YES/UP terms. Buying UP at wire p costs p per contract; buying DOWN
costs 1 - p, where p is the wire price of the rung we transact against. So
the own-cost of acquiring DOWN as a maker joining the ask at wire a is
1 - a, and as a taker hitting the bid at wire b is 1 - b.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

UP, DOWN = "UP", "DOWN"
BID, ASK = "BID", "ASK"
ONE = D(1)

#: the fee-safe pair ceiling the deployed probe uses; a reporting default
#: only, never read from here by the money path.
PAIR_CEILING = D("0.96")

#: the venue tick, from the live preflight `tick_constraint_known`.
TICK = D("0.01")

COMPLEMENT_OF = {UP: DOWN, DOWN: UP}

#: how a book row relates to the event it is being used to describe.
SYNC_AT_EVENT = "VENUE_EVENT_TIME_SYNCHRONIZED"
SYNC_BEFORE_RING = "NO_FRAME_AT_OR_BEFORE_EVENT"
SYNC_NO_EVENT_TIME = "NO_VENUE_EVENT_TIME_SUPPLIED"
SYNC_PROCESSING = "PROCESSING_TIME_SNAPSHOT"

#: a reader must be able to tell these two apart, which is the whole point
#: of carrying both: one is the book as the venue had it when the fill
#: happened, the other is the book as we saw it when we got round to
#: processing the fill, and they are separated by ingestion lag.
SYNC_LABELS = (
    "book_at_event: the ring frame in effect at the venue's own event "
    "timestamp. book_at_processing: the book as read when this record was "
    "built. They differ by feed and processing lag; neither is a fill.")


def _d(v: Any) -> Optional[D]:
    """A Decimal or None. Never raises, never guesses."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, D):
        return v if v.is_finite() else None
    try:
        out = D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out.is_finite() else None


def _f(v: Any) -> Optional[float]:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) \
        else None


def _side(v: Any) -> Optional[str]:
    s = str(v or "").strip().upper()
    if not s:
        return None
    if s.endswith(UP) or s in ("YES", "LONG"):
        return UP
    if s.endswith(DOWN) or s in ("NO", "SHORT"):
        return DOWN
    return None


def _rungs(raw: Any) -> Optional[List[Tuple[D, D]]]:
    """Normalise ladder rungs to [(px, qty)] or None for UNKNOWN.

    None and [] are different facts and are kept different: None is a side
    the venue never served, [] is a side served empty.
    """
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        return None
    out: List[Tuple[D, D]] = []
    for row in raw:
        if isinstance(row, Mapping):
            px, qty = _d(row.get("px") or row.get("price")), _d(row.get("qty"))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            px, qty = _d(row[0]), _d(row[1])
        else:
            continue
        if px is None or qty is None or qty < 0:
            continue
        out.append((px, qty))
    return out


#: the venue timestamp keys this stack already recognises, in the order
#: `l5_live_execution._ack_epoch` tries them.
VENUE_TIME_KEYS = ("transact_time", "transactTime", "ack_time", "createdAt",
                   "created_at", "updateTime", "update_time")


def venue_event_t(row: Any) -> Tuple[Optional[float], Optional[str]]:
    """The venue's OWN event time, or (None, None). NEVER a fallback.

    `_ack_epoch` on the money path falls back to `now()` when the venue
    served no timestamp, which is correct there -- a deadline computed from
    our receipt errs early, which is the safe direction. It is WRONG here:
    substituting our receipt would turn "we do not know when the venue
    filled this" into a confident fill time and quietly relabel a
    processing-time book as an event-time one. So this returns None and the
    record says NO_VENUE_EVENT_TIME_SUPPLIED.
    """
    if not isinstance(row, Mapping):
        return None, None
    for k in VENUE_TIME_KEYS:
        v = row.get(k)
        if v is None:
            continue
        try:
            if isinstance(v, str):
                t = dt.datetime.fromisoformat(
                    v.replace("Z", "+00:00")).timestamp()
            else:
                t = float(v)
        except (ValueError, TypeError, OverflowError):
            continue
        # venue epochs have arrived in seconds and in milliseconds
        if t > 1e11:
            t = t / 1000.0
        if t == t and 1e9 < t < 1e11:
            return t, k
    return None, None


# ---------------------------------------------------------------- the ring

class BookRing:
    """A bounded per-slug history of full-depth book frames.

    WHY A RING AND NOT A SNAPSHOT. Synchronising a book to the venue's own
    fill timestamp is impossible from a current-value read: by the time a
    fill is processed the book has moved, and the deployed stack keeps no
    history to look back into. A bounded ring makes the question answerable
    and keeps the memory cost fixed.

    IT MUST NEVER RAISE INTO THE FEED. It is attached through
    `FrameFanout.add`, which wraps extra observers individually, but it also
    swallows its own failures and counts them, because a reporting ring that
    can interrupt book ingestion is not worth having.
    """

    __slots__ = ("max_rows", "max_slugs", "rungs", "_rows", "errors",
                 "appended", "deduped", "dropped_slugs")

    def __init__(self, *, max_rows: int = 2048, max_slugs: int = 6,
                 rungs: int = 8) -> None:
        self.max_rows = max(1, int(max_rows))
        self.max_slugs = max(1, int(max_slugs))
        self.rungs = max(1, int(rungs))
        self._rows: Dict[str, List[Dict[str, Any]]] = {}
        self.errors: Dict[str, int] = {}
        self.appended = 0
        self.deduped = 0
        self.dropped_slugs = 0

    def _bump(self, key: str) -> None:
        self.errors[key] = self.errors.get(key, 0) + 1

    # -- ingest ------------------------------------------------------------

    def observe_ladder(self, ladder: Any, now: float,
                       slugs: Optional[Sequence[str]] = None) -> int:
        """Append the ladder's CURRENT rungs for each slug of interest.

        The raw frame is deliberately not parsed here. `FrameFanout` calls
        the money path's observer FIRST, so by the time this runs the ladder
        already holds the frame the executor itself will model, and reading
        it means the evidence and the money path cannot disagree about what
        the book was.
        """
        n = 0
        try:
            books = getattr(ladder, "books", None)
            if not isinstance(books, Mapping):
                self._bump("no_ladder_books")
                return 0
            want = None if slugs is None else {str(s) for s in slugs}
            for slug, bk in list(books.items()):
                if want is not None and str(slug) not in want:
                    continue
                if self.append(str(slug), bk, now):
                    n += 1
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._bump("observe_ladder:%s" % type(exc).__name__)
        return n

    def append(self, slug: str, book: Any, now: float) -> bool:
        """One book row. False when nothing was stored, for any reason."""
        try:
            if not isinstance(book, Mapping):
                return False
            bids = _rungs(book.get("bids"))
            asks = _rungs(book.get("asks"))
            if bids is None and asks is None:
                return False
            t = _f(book.get("t"))
            if t is None:
                t = _f(now)
            if t is None:
                self._bump("no_frame_time")
                return False
            row = {
                "t": round(t, 4),
                "observed_t": round(_f(now) or t, 4),
                "updates": book.get("updates"),
                "bids": [(str(p), str(q)) for p, q in (bids or ())[:self.rungs]],
                "asks": [(str(p), str(q)) for p, q in (asks or ())[:self.rungs]],
                "bids_served": bids is not None,
                "asks_served": asks is not None,
                "n_bids": None if bids is None else len(bids),
                "n_asks": None if asks is None else len(asks),
            }
            rows = self._rows.get(slug)
            if rows is None:
                if len(self._rows) >= self.max_slugs:
                    # keep the ring bounded in slugs as well as rows; the
                    # live probe works one market at a time, so this is a
                    # backstop and not an expected path.
                    self.dropped_slugs += 1
                    return False
                rows = self._rows[slug] = []
            if rows and self._same(rows[-1], row):
                self.deduped += 1
                return False
            rows.append(row)
            if len(rows) > self.max_rows:
                del rows[:-self.max_rows]
            self.appended += 1
            return True
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._bump("append:%s" % type(exc).__name__)
            return False

    @staticmethod
    def _same(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        return a.get("bids") == b.get("bids") and a.get("asks") == b.get("asks")

    # -- reads -------------------------------------------------------------

    def latest(self, slug: str) -> Optional[Dict[str, Any]]:
        rows = self._rows.get(str(slug)) or ()
        return dict(rows[-1]) if rows else None

    def rows(self, slug: str, t0: Optional[float] = None,
             t1: Optional[float] = None) -> List[Dict[str, Any]]:
        out = []
        for r in self._rows.get(str(slug)) or ():
            t = r.get("t")
            if t0 is not None and t < t0:
                continue
            if t1 is not None and t > t1:
                continue
            out.append(dict(r))
        return out

    def as_of(self, slug: str, event_t: Any) -> Dict[str, Any]:
        """The frame in effect at `event_t`, with WHY it is or is not that.

        A row qualifies only when it starts at or before the event and the
        NEXT row starts after it -- that is what "in effect" means. The
        answer carries how long the frame had already been standing and when
        it was superseded, because a frame that changed 20ms after the fill
        is a different kind of evidence from one that stood for a minute.
        """
        et = _f(event_t)
        if et is None:
            return {"basis": SYNC_NO_EVENT_TIME, "row": None,
                    "event_t": None}
        rows = self._rows.get(str(slug)) or ()
        prev = None
        nxt = None
        for r in rows:
            if r.get("t") <= et:
                prev = r
            else:
                nxt = r
                break
        if prev is None:
            first = rows[0].get("t") if rows else None
            return {"basis": SYNC_BEFORE_RING, "row": None, "event_t": et,
                    "ring_first_t": first,
                    "ring_starts_after_event_by_s": (
                        None if first is None else round(first - et, 4)),
                    "ring_rows": len(rows)}
        return {"basis": SYNC_AT_EVENT, "row": dict(prev), "event_t": et,
                "frame_t": prev.get("t"),
                "frame_age_at_event_s": round(et - float(prev["t"]), 4),
                "superseded_at_t": None if nxt is None else nxt.get("t"),
                "frame_held_for_s": (
                    None if nxt is None
                    else round(float(nxt["t"]) - float(prev["t"]), 4)),
                "ring_rows": len(rows)}

    def report(self) -> Dict[str, Any]:
        return {"slugs": {s: len(r) for s, r in self._rows.items()},
                "appended": self.appended, "deduped": self.deduped,
                "dropped_slugs": self.dropped_slugs,
                "max_rows": self.max_rows, "max_slugs": self.max_slugs,
                "rungs_kept": self.rungs,
                "errors": dict(self.errors),
                "labels": "REPORTING ONLY; bounded ring; a touch is not a fill"}


# ------------------------------------------------- reachability arithmetic

def _own_px(side: str, wire_px: Optional[D]) -> Optional[D]:
    """What one contract of `side` costs when transacting at `wire_px`."""
    if wire_px is None:
        return None
    return wire_px if side == UP else ONE - wire_px


def _best(rows: Sequence[Tuple[str, str]], want: str
          ) -> Tuple[Optional[D], Optional[D]]:
    """Top rung of an already-sorted side: (px, qty)."""
    if not rows:
        return None, None
    px, qty = _d(rows[0][0]), _d(rows[0][1])
    return px, qty


def _ahead_at(rows: Sequence[Tuple[str, str]], wire_px: D,
              side: str) -> Optional[D]:
    """Everything trading before us if we join `wire_px`: better rungs plus
    the whole of our own rung. A BOUND, not a position -- the venue
    publishes no per-order queue."""
    total = D(0)
    found = False
    for p_s, q_s in rows:
        p, q = _d(p_s), _d(q_s)
        if p is None or q is None:
            continue
        better = (p > wire_px) if side == BID else (p < wire_px)
        if better:
            total += q
        elif p == wire_px:
            total += q
            found = True
    return total if found else None


def _walk(rows: Sequence[Tuple[str, str]], side: str, need: Optional[D]
          ) -> Dict[str, Any]:
    """Cross displayed depth for `need` contracts; report the own-cost VWAP.

    This is the only genuinely EXECUTABLE figure in this module, and it is
    still bounded by displayed depth being displayed rather than promised.
    """
    taken = D(0)
    cost = D(0)
    levels = 0
    for p_s, q_s in rows:
        p, q = _d(p_s), _d(q_s)
        if p is None or q is None or q <= 0:
            continue
        own = _own_px(UP if side == ASK else DOWN, p)
        if own is None:
            continue
        levels += 1
        if need is None:
            taken += q
            cost += own * q
            continue
        room = need - taken
        if room <= 0:
            break
        use = q if q < room else room
        taken += use
        cost += own * use
        if taken >= need:
            break
    out: Dict[str, Any] = {
        "displayed_qty_crossed": str(taken),
        "levels_used": levels,
        "vwap_own_px": None if taken <= 0 else str(
            (cost / taken).quantize(D("0.000001"))),
        "basis": ("EXECUTABLE_AGAINST_DISPLAYED_DEPTH_AT_OBSERVATION; "
                  "displayed depth is displayed, not guaranteed; excludes "
                  "fees"),
    }
    if need is not None:
        out["qty_needed"] = str(need)
        out["insufficient_displayed_depth"] = bool(taken < need)
    return out


def complement_reachability(*, held_side: Any, first_leg_own_px: Any = None,
                            qty_needed: Any = None, row: Any = None,
                            pair_ceiling: Any = PAIR_CEILING,
                            depth_ticks: Any = None,
                            tick: Any = TICK) -> Dict[str, Any]:
    """Was the complement of `held_side` reachable, in this one book row?

    Returns the maker quote that was displayed (with the depth that would
    trade ahead of us) and the taker cost that was executable, each with the
    pair cost it implies against the first leg we already own.

    `depth_ticks` MATTERS AND IS NOT COSMETIC, and it trades the two axes of
    reachability against each other rather than improving both. The deployed
    probe does not quote at the touch; it rests `depth_ticks` behind it. That
    is a LESS aggressive bid, so the own price is CHEAPER by that many ticks
    and the pair looks better -- while the fill becomes less likely, because
    the book must come to us. Price and fill probability move in opposite
    directions here, so neither figure alone answers "was it reachable":
    `pair_if_maker_behind_touch` says whether a fee-safe pair was PLANNABLE,
    and `queue_ahead_bound` and `taker_walk` speak to whether it would have
    traded.

    CALIBRATED AGAINST THE LIVE 1500z RECORD, twice. With the ask at 0.4700
    and depth_ticks 2 the engine's complement is 1 - (0.47 + 0.02) = 0.5100,
    exactly the `frozen_price_superseded: "0.5100"` it logged; and with the
    ask at 0.1300 it is 1 - 0.15 = 0.8500 for a pair of 1.2900, exactly the
    `complement_price` and `pair_cost` in the refusal.
    """
    hs = _side(held_side)
    if hs is None:
        return {"error": "UNREADABLE_HELD_SIDE", "held_side": held_side}
    comp = COMPLEMENT_OF[hs]
    out: Dict[str, Any] = {
        "held_side": hs, "complement_side": comp,
        "price_semantics": ("wire is YES/UP; own cost of DOWN at wire p is "
                            "1 - p"),
    }
    if not isinstance(row, Mapping):
        return dict(out, error="NO_BOOK_ROW")
    bids = row.get("bids") or ()
    asks = row.get("asks") or ()
    ceiling = _d(pair_ceiling)
    first = _d(first_leg_own_px)
    need = _d(qty_needed)
    if need is not None and need < 0:
        need = -need

    # The complement is acquired by JOINING our own side's touch (maker) or
    # by CROSSING the other side (taker). Which wire side is which depends
    # on the complement, and getting this backwards is the classic error.
    if comp == UP:
        join_rows, join_side = bids, BID
        cross_rows, cross_side = asks, ASK
    else:
        join_rows, join_side = asks, ASK
        cross_rows, cross_side = bids, BID

    join_px, join_qty = _best(join_rows, join_side)
    cross_px, cross_qty = _best(cross_rows, cross_side)

    maker_own = _own_px(comp, join_px)
    taker_own = _own_px(comp, cross_px)

    out["opposite_top_of_book"] = {
        "maker_join_wire_px": None if join_px is None else str(join_px),
        "maker_join_own_px": None if maker_own is None else str(maker_own),
        "maker_join_displayed_qty": None if join_qty is None else str(join_qty),
        "taker_cross_wire_px": None if cross_px is None else str(cross_px),
        "taker_cross_own_px": None if taker_own is None else str(taker_own),
        "taker_cross_displayed_qty": (None if cross_qty is None
                                      else str(cross_qty)),
        "wire_side_joined": join_side,
        "wire_side_crossed": cross_side,
        "side_served": {"bids": bool(row.get("bids_served")),
                        "asks": bool(row.get("asks_served"))},
        "n_rungs": {"bids": row.get("n_bids"), "asks": row.get("n_asks")},
    }

    # What the deployed probe would actually have quoted: `depth_ticks`
    # behind the touch, which is WORSE for us by that many ticks of own
    # price on the complement.
    dt, tk = _d(depth_ticks), _d(tick)
    behind_own = behind_wire = None
    if dt is not None and tk is not None and join_px is not None and dt >= 0:
        off = dt * tk
        behind_wire = (join_px - off) if join_side == BID else (join_px + off)
        behind_own = _own_px(comp, behind_wire)
    out["maker_quote_behind_touch"] = {
        "depth_ticks": None if dt is None else str(dt),
        "tick": None if tk is None else str(tk),
        "wire_px": None if behind_wire is None else str(behind_wire),
        "own_px": None if behind_own is None else str(behind_own),
        "basis": ("the price the deployed probe rests at: depth_ticks "
                  "behind the touch, so CHEAPER in own price and LESS "
                  "likely to fill. None when depth_ticks was not supplied."),
    }

    ahead = None if join_px is None else _ahead_at(join_rows, join_px,
                                                   join_side)
    out["maker_quote"] = {
        "own_px": None if maker_own is None else str(maker_own),
        "queue_ahead_bound": None if ahead is None else str(ahead),
        "qty_needed": None if need is None else str(need),
        "queue_ahead_multiple_of_need": (
            None if (ahead is None or need is None or need <= 0)
            else str((ahead / need).quantize(D("0.01")))),
        "basis": ("DISPLAYED QUOTE, NOT AN EXECUTABLE OPPORTUNITY. Joining "
                  "this rung queues behind at most queue_ahead_bound; the "
                  "venue publishes no per-order queue position, so a touch "
                  "at this price does not imply a maker fill."),
    }
    out["taker_walk"] = _walk(cross_rows, cross_side, need)

    def pair(own: Optional[D]) -> Dict[str, Any]:
        if first is None or own is None:
            return {"pair_own_cost": None,
                    "why": "no_first_leg_price" if first is None
                    else "no_complement_price"}
        total = first + own
        d: Dict[str, Any] = {"pair_own_cost": str(total),
                             "first_leg_own_px": str(first)}
        if ceiling is not None:
            d["pair_ceiling"] = str(ceiling)
            d["clears_pair_ceiling"] = bool(total <= ceiling)
            d["margin_to_ceiling"] = str(ceiling - total)
        return d

    out["pair_if_maker"] = pair(maker_own)
    out["pair_if_maker_behind_touch"] = pair(behind_own)
    vw = _d((out["taker_walk"] or {}).get("vwap_own_px"))
    out["pair_if_taker_walk"] = pair(vw)
    out["labels"] = ("REPORTING ONLY. pair_if_maker is conditional on a "
                     "maker fill that displayed depth does not guarantee; "
                     "pair_if_taker_walk excludes fees. pair_if_maker is "
                     "the AT-TOUCH pair, which is the most EXPENSIVE and the "
                     "most likely to fill; pair_if_maker_behind_touch is what "
                     "the engine actually quotes -- cheaper, less likely to "
                     "fill, and the figure to compare against what it did.")
    return out


# ------------------------------------------------------------ the records

def fill_evidence(*, slug: str, coid: Any, side: Any, fill_qty: Any,
                  fill_own_px: Any = None, venue_event_t: Any = None,
                  processing_t: Any = None, residual_after: Any = None,
                  first_leg_own_px: Any = None, qty_needed: Any = None,
                  ring: Optional[BookRing] = None,
                  book_at_processing: Any = None,
                  order_resting: Any = None,
                  depth_ticks: Any = None,
                  pair_ceiling: Any = PAIR_CEILING) -> Dict[str, Any]:
    """One fill, with the opposite side at it -- on BOTH time bases.

    `book_at_event` is the ring frame in effect at the venue's own event
    timestamp. `book_at_processing` is what we could see when the fill was
    processed. The owner's question turns on not confusing them, so they are
    separate keys with separate bases and the lag between them is stated.
    """
    rec: Dict[str, Any] = {
        "label": "L5_FILL_BOOK_EVIDENCE",
        "slug": str(slug), "coid": None if coid is None else str(coid),
        "side": _side(side), "side_raw": None if side is None else str(side),
        "fill_qty": None if _d(fill_qty) is None else str(_d(fill_qty)),
        "fill_own_px": (None if _d(fill_own_px) is None
                        else str(_d(fill_own_px))),
        "residual_after": (None if _d(residual_after) is None
                           else str(_d(residual_after))),
        "order_resting": (None if order_resting is None
                          else bool(order_resting)),
        "venue_event_t": _f(venue_event_t),
        "processing_t": _f(processing_t),
        "sync_labels": SYNC_LABELS,
    }
    et, pt = _f(venue_event_t), _f(processing_t)
    rec["processing_lag_s"] = (None if (et is None or pt is None)
                              else round(pt - et, 4))

    held = _side(side)
    need = _d(qty_needed)
    if need is None:
        need = _d(fill_qty)
    first = _d(first_leg_own_px)
    if first is None:
        first = _d(fill_own_px)

    at_event = (ring.as_of(str(slug), et) if (ring is not None and et is not None)
                else {"basis": SYNC_NO_EVENT_TIME if et is None
                      else "NO_RING_SUPPLIED", "row": None})
    rec["book_at_event"] = {
        "sync": {k: v for k, v in at_event.items() if k != "row"},
        "row": at_event.get("row"),
        "reachability": complement_reachability(
            held_side=held, first_leg_own_px=first, qty_needed=need,
            row=at_event.get("row"), pair_ceiling=pair_ceiling,
            depth_ticks=depth_ticks),
    }

    proc_row = book_at_processing
    if proc_row is None and ring is not None:
        proc_row = ring.latest(str(slug))
    rec["book_at_processing"] = {
        "sync": {"basis": SYNC_PROCESSING,
                 "row_t": None if not isinstance(proc_row, Mapping)
                 else proc_row.get("t")},
        "row": proc_row if isinstance(proc_row, Mapping) else None,
        "reachability": complement_reachability(
            held_side=held, first_leg_own_px=first, qty_needed=need,
            row=proc_row, pair_ceiling=pair_ceiling,
            depth_ticks=depth_ticks),
    }
    return rec


class ResidualSampler:
    """Sample the book while inventory is unmatched -- order or no order.

    WHY IT CANNOT BE DRIVEN BY AN ORDER. The campaign's unanswered question
    is what the complement side was doing during the intervals when nothing
    of ours was resting: after a complement was cancelled, between a stale
    recovery and its replacement, and after the last refusal of a window.
    A sampler hung off an order's lifecycle is blind in exactly those
    intervals, so this one is driven by the inventory being non-zero and
    records `order_resting` as an OBSERVATION rather than a condition.
    """

    __slots__ = ("every_s", "_last", "emitted", "skipped")

    def __init__(self, *, every_s: float = 5.0) -> None:
        self.every_s = max(0.25, float(every_s))
        self._last: Dict[str, float] = {}
        self.emitted = 0
        self.skipped = 0

    def due(self, slug: str, now: Any) -> bool:
        n = _f(now)
        if n is None:
            return False
        last = self._last.get(str(slug))
        return last is None or (n - last) >= self.every_s

    def reset(self, slug: str) -> None:
        self._last.pop(str(slug), None)

    def sample(self, *, slug: str, now: Any, residual: Any,
               held_side: Any = None, first_leg_own_px: Any = None,
               order_resting: Any = None, ring: Optional[BookRing] = None,
               book: Any = None, force: bool = False,
               depth_ticks: Any = None,
               pair_ceiling: Any = PAIR_CEILING
               ) -> Optional[Dict[str, Any]]:
        """A record when one is due and inventory is unmatched, else None."""
        res = _d(residual)
        if res is None or res == 0:
            self.reset(slug)
            return None
        if not force and not self.due(slug, now):
            self.skipped += 1
            return None
        n = _f(now)
        if n is not None:
            self._last[str(slug)] = n
        side = _side(held_side)
        if side is None:
            side = UP if res > 0 else DOWN
        row = book if isinstance(book, Mapping) else (
            ring.latest(str(slug)) if ring is not None else None)
        self.emitted += 1
        need = res if res > 0 else -res
        return {
            "label": "L5_RESIDUAL_BOOK_SAMPLE",
            "slug": str(slug), "t": n,
            "residual": str(res), "held_side": side,
            "order_resting": (None if order_resting is None
                              else bool(order_resting)),
            "sampler": {"every_s": self.every_s, "forced": bool(force),
                        "driven_by": "UNMATCHED_INVENTORY_NOT_ORDER_STATE"},
            "book": {
                "sync": {"basis": SYNC_PROCESSING,
                         "row_t": None if not isinstance(row, Mapping)
                         else row.get("t"),
                         "row_age_s": (
                             None if (n is None or not isinstance(row, Mapping)
                                      or _f(row.get("t")) is None)
                             else round(n - float(row["t"]), 4))},
                "row": row if isinstance(row, Mapping) else None,
            },
            "reachability": complement_reachability(
                held_side=side, first_leg_own_px=first_leg_own_px,
                qty_needed=need, row=row, pair_ceiling=pair_ceiling,
                depth_ticks=depth_ticks),
            "labels": ("REPORTING ONLY; sampled because inventory was "
                       "unmatched, whether or not an order was resting"),
        }

    def report(self) -> Dict[str, Any]:
        return {"every_s": self.every_s, "emitted": self.emitted,
                "skipped_not_due": self.skipped,
                "tracked_slugs": len(self._last)}
