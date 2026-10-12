"""Queue-aware MODELLED fills for the executor's own maker intents.

NOTHING HERE IS A FILL. Every number this module produces is MODELLED and
belongs in the modelled ledger, never in realized P&L. The shadow transport
still blocks submit; this module only answers "if that quote had rested,
what would have happened to it?"

WHY IT EXISTS. The executor builds maker intents and records
`"depth": None` for every one of them, because the book state it keeps is
TOP OF BOOK ONLY -- `apply_events` retains bid/bid_qty/ask/ask_qty and
discards the 35-40 level ladder the venue actually sends. Our intents rest a
median 8 ticks behind the touch, so top-of-book depth cannot describe the
level we would join. `pmus_l5_surface_shadow.queue_ahead_at` is right to
return None there, and that None is exactly what made fill evidence
impossible to produce. `Ladder` keeps the rungs so the question becomes
answerable.

THE DISCIPLINE IS INHERITED, not reinvented, from
pmus_l5_surface_shadow:

  * QUEUE-AHEAD IS CAPTURED AT PLACEMENT AND NEVER RE-READ. A quote that
    rests while its level grows is behind what was there when it joined, not
    behind the new volume.
  * UNKNOWN DEPTH IS NOT ZERO. A level the venue never served leaves
    queue_ahead None, and a None NEVER fills in the conservative or base
    case. Treating it as zero would put us at the front of a queue we cannot
    see.
  * ONLY OBSERVED TRADE VOLUME ADVANCES THE QUEUE. Depth shrinking at our
    level is NOT advancement: a cancellation ahead of us is indistinguishable
    from a fill ahead of us in the data we get, and assuming cancels help us
    is the single easiest way to manufacture a fill. `cleared` moves only on
    prints.
  * TOUCH IS NOT FILL. Price reaching our level advances nothing by itself.

WIRE CONVENTION. The venue quotes ONE number per market, always the LONG
(UP/YES) price. An UP buy at own-outcome cost p rests as a BID at wire p. A
DOWN buy at own-outcome cost q is a SELL of YES and rests as an ASK at wire
1-q. Both are passive buys of their own outcome.

THREE SCENARIOS, and the spread between them is the honest width of our
ignorance:

  CONSERVATIVE  cleared >= queue_ahead + our own qty. We are behind every
                resting share AND our own size must trade before we are
                done. Unknown queue never fills.
  BASE          cleared >= queue_ahead. Behind the resting shares, our own
                size fills on the print that reaches us. Unknown queue never
                fills.
  OPTIMISTIC    the level is SWEPT -- a print strictly THROUGH our price,
                which by construction takes the whole level including us.
                This is the only case that can fill an unknown queue, and it
                is still not touch-equals-fill.
"""
from __future__ import annotations

import collections
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

CONSERVATIVE = "CONSERVATIVE"
BASE = "BASE"
OPTIMISTIC = "OPTIMISTIC"
SCENARIOS = (CONSERVATIVE, BASE, OPTIMISTIC)

#: how many price rungs per side to retain. The venue sends 35-40; keeping
#: all of them costs nothing and a truncated ladder silently becomes an
#: unknown depth at exactly the levels we quote.
MAX_RUNGS = 128

BID, ASK = "BID", "ASK"
UP, DOWN = "UP", "DOWN"


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def _level_px_qty(row: Any):
    """One ladder rung, in the venue's own wire shape.

    The wire sends {"px": {"value": "0.5300", ...}, "qty": "300.0000"}. A
    plain {"price":..., "quantity":...} is NOT the venue's shape and is the
    reason an early fixture of mine mistook a book level for a trade print,
    so both spellings are read but neither is invented.
    """
    if not isinstance(row, Mapping):
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            return _d(row[0]), _d(row[1])
        return None, None
    px = row.get("px")
    if isinstance(px, Mapping):
        px = px.get("value")
    if px is None:
        px = row.get("price")
    qty = row.get("qty")
    if qty is None:
        qty = row.get("quantity")
    return _d(px), _d(qty)


class Ladder:
    """Full per-slug depth, which is the thing nobody was keeping.

    Read-only bookkeeping over frames the AuthBook already receives. It adds
    no subscription, no request and no network traffic.
    """

    def __init__(self, max_rungs: int = MAX_RUNGS):
        self.max_rungs = max_rungs
        self.books: Dict[str, Dict[str, Any]] = {}
        self.frames = 0
        self.rejected = 0

    def ingest(self, msg: Any, now: float) -> Optional[str]:
        """Fold one raw frame. Returns the slug it updated, or None."""
        if not isinstance(msg, Mapping):
            return None
        slug = msg.get("marketSlug") or msg.get("market_slug") or \
            msg.get("slug")
        if not isinstance(slug, str) or not slug:
            return None
        raw_b = msg.get("bids")
        raw_a = msg.get("offers")
        if raw_a is None:
            raw_a = msg.get("asks")
        if not isinstance(raw_b, list) and not isinstance(raw_a, list):
            return None

        def rungs(raw, reverse):
            out = []
            if not isinstance(raw, list):
                return None
            for row in raw[:self.max_rungs]:
                px, qty = _level_px_qty(row)
                if px is None or qty is None or qty < 0:
                    self.rejected += 1          # fail closed per rung
                    continue
                out.append((px, qty))
            out.sort(key=lambda r: r[0], reverse=reverse)
            return out

        b = rungs(raw_b, True)                   # bids: best = highest
        a = rungs(raw_a, False)                  # asks: best = lowest
        cur = self.books.setdefault(slug, {"bids": None, "asks": None,
                                           "t": None, "updates": 0})
        if b is not None:
            cur["bids"] = b
        if a is not None:
            cur["asks"] = a
        cur["t"] = now
        cur["updates"] += 1
        self.frames += 1
        return slug

    def depth_at(self, slug: str, resting_side: str, wire_px: D
                 ) -> Optional[D]:
        """Displayed size resting AT exactly this wire price.

        None means the venue never served that rung -- UNKNOWN, not zero.
        """
        bk = self.books.get(slug)
        if not bk:
            return None
        rows = bk.get("bids") if resting_side == BID else bk.get("asks")
        if rows is None:
            return None
        for px, qty in rows:
            if px == wire_px:
                return qty
        # the ladder was served and this rung is not in it. An absent rung
        # inside the served range is genuinely empty; beyond the deepest
        # rung served it is unknown.
        if not rows:
            return None
        deepest = rows[-1][0]
        inside = (wire_px >= deepest) if resting_side == BID \
            else (wire_px <= deepest)
        return D(0) if inside else None

    def ahead_of_us(self, slug: str, resting_side: str, wire_px: D
                    ) -> Optional[D]:
        """Everything that would trade BEFORE us: better-priced rungs plus
        our own level. Price priority first, then time priority at our rung.
        """
        bk = self.books.get(slug)
        if not bk:
            return None
        rows = bk.get("bids") if resting_side == BID else bk.get("asks")
        if not rows:
            return None
        here = self.depth_at(slug, resting_side, wire_px)
        if here is None:
            return None
        better = D(0)
        for px, qty in rows:
            if (px > wire_px) if resting_side == BID else (px < wire_px):
                better += qty
        return better + here

    def status(self) -> Dict[str, Any]:
        return {"slugs": len(self.books), "frames": self.frames,
                "rungs_rejected": self.rejected,
                "depth_served": {s: {"bids": len(b["bids"] or []),
                                     "asks": len(b["asks"] or [])}
                                 for s, b in list(self.books.items())[:4]}}


class HypoOrder:
    """One MODELLED resting order. It is never submitted."""

    __slots__ = ("slug", "side", "own_px", "wire_px", "resting_side", "qty",
                 "placed_t", "queue_ahead", "level_depth", "cleared",
                 "swept", "filled", "fill_t", "end_reason", "coid",
                 "bid_at_placement", "ask_at_placement", "prints_seen")

    def __init__(self, slug: str, side: str, own_px: D, qty: D,
                 placed_t: float, *, ladder: Optional[Ladder] = None,
                 bid: Optional[D] = None, ask: Optional[D] = None,
                 coid: Optional[str] = None):
        self.slug, self.side, self.qty, self.placed_t = slug, side, qty, placed_t
        self.own_px = own_px
        # own-outcome cost -> the wire price it rests at, and which side
        self.wire_px = own_px if side == UP else (D(1) - own_px)
        self.resting_side = BID if side == UP else ASK
        self.coid = coid
        self.bid_at_placement, self.ask_at_placement = bid, ask
        # FROZEN AT PLACEMENT, never re-read.
        self.level_depth = (None if ladder is None else
                            ladder.depth_at(slug, self.resting_side,
                                            self.wire_px))
        self.queue_ahead = (None if ladder is None else
                            ladder.ahead_of_us(slug, self.resting_side,
                                               self.wire_px))
        self.cleared = D(0)
        self.swept = False
        self.filled: Dict[str, bool] = {s: False for s in SCENARIOS}
        self.fill_t: Dict[str, Optional[float]] = {s: None for s in SCENARIOS}
        self.end_reason: Optional[str] = None
        self.prints_seen = 0

    @property
    def restable(self) -> bool:
        """A passive order must not cross. In wire terms a BID must sit at or
        below the bid and an ASK at or above the ask."""
        if self.resting_side == BID:
            return (self.ask_at_placement is None or
                    self.wire_px < self.ask_at_placement)
        return (self.bid_at_placement is None or
                self.wire_px > self.bid_at_placement)

    def ticks_behind_touch(self, tick: D = D("0.01")) -> Optional[int]:
        ref = (self.bid_at_placement if self.resting_side == BID
               else self.ask_at_placement)
        if ref is None or tick <= 0:
            return None
        delta = (ref - self.wire_px) if self.resting_side == BID \
            else (self.wire_px - ref)
        return int(delta / tick)

    def consumes(self, px: D, direction: Optional[str]) -> bool:
        """Does a print at this price trade against our side of the book?

        A resting BID is consumed by SELL aggressors at or below our price;
        a resting ASK by BUY aggressors at or above it. When the venue did
        not label the aggressor we fall back to price alone, which is the
        conservative reading: it can only ever count MORE volume as ahead
        of us, never less.
        """
        if self.resting_side == BID:
            if px > self.wire_px:
                return False
            return direction in (None, "", "SELL", "sell", "SELL_AGGRESSOR")
        if px < self.wire_px:
            return False
        return direction in (None, "", "BUY", "buy", "BUY_AGGRESSOR")

    def is_sweep(self, px: D) -> bool:
        """A print strictly THROUGH our level takes the whole level with it."""
        return (px < self.wire_px) if self.resting_side == BID \
            else (px > self.wire_px)

    def advance(self, prints: Sequence[Mapping[str, Any]]) -> None:
        """Fold new prints. Only trade volume moves the queue."""
        for p in prints:
            px, qty = _d(p.get("px")), _d(p.get("qty"))
            if px is None or qty is None or qty <= 0:
                continue
            if not self.consumes(px, p.get("dir")):
                continue
            self.prints_seen += 1
            self.cleared += qty
            t = p.get("t")
            if self.is_sweep(px):
                self.swept = True
            self._evaluate(t)

    def _evaluate(self, t: Optional[float]) -> None:
        q = self.queue_ahead
        if q is not None:
            if not self.filled[CONSERVATIVE] and self.cleared >= q + self.qty:
                self.filled[CONSERVATIVE] = True
                self.fill_t[CONSERVATIVE] = t
            if not self.filled[BASE] and self.cleared >= q:
                self.filled[BASE] = True
                self.fill_t[BASE] = t
        if self.swept and not self.filled[OPTIMISTIC]:
            self.filled[OPTIMISTIC] = True
            self.fill_t[OPTIMISTIC] = t
        # a sweep also satisfies the stricter cases when the queue is known
        if self.swept and q is not None:
            for s in (BASE, CONSERVATIVE):
                if not self.filled[s]:
                    self.filled[s] = True
                    self.fill_t[s] = t

    def record(self) -> Dict[str, Any]:
        return {
            "coid": self.coid, "slug": self.slug, "side": self.side,
            "own_px": str(self.own_px), "wire_px": str(self.wire_px),
            "rests_as": self.resting_side, "qty": str(self.qty),
            "placed_t": self.placed_t,
            "bid_at_placement": (None if self.bid_at_placement is None
                                 else str(self.bid_at_placement)),
            "ask_at_placement": (None if self.ask_at_placement is None
                                 else str(self.ask_at_placement)),
            "ticks_behind_touch": self.ticks_behind_touch(),
            "restable": self.restable,
            "displayed_depth_at_price": (None if self.level_depth is None
                                         else str(self.level_depth)),
            "queue_ahead_at_placement": (None if self.queue_ahead is None
                                         else str(self.queue_ahead)),
            "volume_through_level": str(self.cleared),
            "prints_consumed": self.prints_seen,
            "swept": self.swept,
            "modelled_fill": dict(self.filled),
            "modelled_fill_t": dict(self.fill_t),
            "end_reason": self.end_reason,
            "labels": ("MODELLED, never submitted. queue_ahead None means "
                       "the venue never served that rung: unknown, not "
                       "zero, and it never fills except on a sweep. Only "
                       "trade prints advance the queue -- a cancellation "
                       "ahead of us is not queue advancement."),
        }


class HypoInventory:
    """MODELLED own-outcome inventory, per scenario.

    This is what lets a modelled first leg trigger a modelled complementary
    decision: the executor asks what the modelled book holds, exactly as it
    asks the real store, but the two never touch.
    """

    def __init__(self):
        self.qty: Dict[str, D] = collections.defaultdict(lambda: D(0))
        self.cost: Dict[str, D] = collections.defaultdict(lambda: D(0))
        self.legs: List[Dict[str, Any]] = []

    @staticmethod
    def _k(slug: str, side: str) -> str:
        return "%s|%s" % (slug, side)

    def fill(self, slug: str, side: str, qty: D, own_px: D,
             t: Optional[float] = None) -> None:
        k = self._k(slug, side)
        self.qty[k] += qty
        self.cost[k] += qty * own_px
        self.legs.append({"slug": slug, "side": side, "qty": str(qty),
                          "own_px": str(own_px), "cost": str(qty * own_px),
                          "t": t})

    def up(self, slug: str) -> D:
        return self.qty[self._k(slug, UP)]

    def down(self, slug: str) -> D:
        return self.qty[self._k(slug, DOWN)]

    def matched(self, slug: str) -> D:
        return min(self.up(slug), self.down(slug))

    def residual(self, slug: str) -> D:
        """Signed: positive is long UP, negative is long DOWN."""
        return self.up(slug) - self.down(slug)

    def cash_out(self, slug: str) -> D:
        return (self.cost[self._k(slug, UP)] +
                self.cost[self._k(slug, DOWN)])

    def settle(self, slug: str, up_won: bool) -> Dict[str, Any]:
        """Conservative settlement economics for one modelled window.

        A matched pair redeems $1 regardless of outcome. The residual pays
        $1 a share only on the winning side and ZERO otherwise -- there is no
        mark-to-market and no partial credit.
        """
        m = self.matched(slug)
        r = self.residual(slug)
        spent = self.cash_out(slug)
        matched_in = m * D(1)
        if r > 0:
            residual_in = r * (D(1) if up_won else D(0))
        elif r < 0:
            residual_in = (-r) * (D(0) if up_won else D(1))
        else:
            residual_in = D(0)
        net = matched_in + residual_in - spent
        return {
            "slug": slug, "up_won": up_won,
            "up": str(self.up(slug)), "down": str(self.down(slug)),
            "matched": str(m), "residual": str(r),
            "cash_out": str(spent),
            "matched_redeemed": str(matched_in),
            "residual_redeemed": str(residual_in),
            "modelled_net": str(net),
            "labels": ("MODELLED settlement. A matched pair redeems $1 "
                       "either way; the residual pays $1 only on the "
                       "winning side and zero otherwise. No "
                       "mark-to-market. NOT realized profit."),
        }


def adverse_selection(order: HypoOrder, scenario: str,
                      wire_at_close: Optional[D]) -> Optional[Dict[str, Any]]:
    """Did the modelled fill arrive just as the market left?

    Measured as the move of the wire price AGAINST our own outcome between
    placement and window close. A passive order far behind the touch fills
    only when its level is reached, which tends to be when the price is
    travelling through it -- so this is the number that decides whether a
    maker edge survives.
    """
    if not order.filled.get(scenario) or wire_at_close is None:
        return None
    ref = (order.bid_at_placement if order.resting_side == BID
           else order.ask_at_placement)
    if ref is None:
        return None
    move = wire_at_close - ref
    against = -move if order.side == UP else move
    return {"scenario": scenario, "side": order.side,
            "wire_at_placement": str(ref),
            "wire_at_close": str(wire_at_close),
            "move": str(move),
            "moved_against_us": str(against),
            "adverse": bool(against > 0),
            "labels": ("positive moved_against_us means the wire travelled "
                       "away from our outcome after we quoted -- filled as "
                       "the market left.")}


OPEN, COMPLETE, EXTEND = "MODELLED_OPEN", "MODELLED_COMPLETE", "MODELLED_EXTEND"


class ModelSession:
    """One window's modelled book: ladder, resting orders, inventory.

    WHAT THIS IS, AND WHAT IT IS NOT. This CLASSIFIES the intents the
    executor already produced against a modelled book. It does NOT generate
    new decisions from modelled inventory, and it is therefore NOT faithful
    inventory-driven behavioural replay. The distinction is load-bearing:

      * CLASSIFICATION (what this does): L5's action stream is fixed. If a
        modelled OPEN fills, the next intent that arrives is LABELLED a
        COMPLETE when it happens to be on the opposite side. The modelled
        inventory never causes an intent to exist, never changes its price,
        its size or its timing, and never causes one to be withheld.
      * GENERATION (what this does not do): a faithful replay would feed
        modelled inventory back into the decision path, so a modelled fill
        would make the engine CHOOSE a complementary side, price and time
        it would not otherwise have chosen. Our engine's complements are
        whatever L5 did next, not a response to our own modelled book.

    So a COMPLETE here means "an opposite-side intent arrived while the
    modelled book was long" -- evidence about whether pair completion is
    AVAILABLE in the live stream, NOT evidence that the engine would
    pursue it. Every report carries a `fidelity` block saying so.

    Doing it the other way needs either a second executor -- the
    independent simulator that is ruled out -- or re-pointing the real
    executor's store at modelled state, which puts modelled numbers on the
    money path. Neither is acceptable, so the weaker claim is made
    explicitly rather than the stronger one implied.

    The intents the executor already produces are CLASSIFIED against the
    modelled book:

        modelled residual flat            -> MODELLED_OPEN
        intent on the same side as it     -> MODELLED_EXTEND
        intent on the opposite side       -> MODELLED_COMPLETE

    So an OPEN that fills in a scenario changes what the NEXT intent means
    in that scenario, which is the sequencing we are trying to measure, and
    it reuses the live decision stream rather than re-deriving it.

    Each scenario carries its own inventory, because a fill that happens
    only in the optimistic case must not create matched inventory in the
    conservative one.
    """

    def __init__(self, slug: str, tick: D = D("0.01")):
        self.slug = slug
        self.tick = tick
        self.ladder = Ladder()
        self.inv: Dict[str, HypoInventory] = {s: HypoInventory()
                                              for s in SCENARIOS}
        self.orders: List[HypoOrder] = []
        self.open_orders: List[HypoOrder] = []
        self.intents = 0
        self.not_restable = 0
        #: ANCHORED BY ROW IDENTITY, not by index and not by timestamp.
        #: Both of the obvious cursors are wrong here:
        #:   * an INDEX cursor freezes once the recorder's rolling 500-row
        #:     trades list hits its cap (`del s["trades"][:-500]`), because
        #:     len() stops growing. 1800z saw trades_seen 500.
        #:   * a TIMESTAMP cursor silently drops prints, because the
        #:     recorder stamps `"t": now` -- the FRAME arrival time -- so
        #:     every print carried in the same WS frame shares a timestamp.
        #:     Skipping `t <= last_t` would keep one print per frame and
        #:     discard the rest. That is a systematic undercount of exactly
        #:     the busy moments that fill orders.
        #: So we remember the identity of the last row consumed, find it
        #: again on the next read, and take what follows.
        self.last_row: Optional[tuple] = None
        self.prints_consumed = 0
        #: the buffer rolled past us: the row we last consumed is no longer
        #: in the list, so prints existed that we never saw. Counted and
        #: REPORTED rather than assumed away -- coverage is not complete and
        #: must not be claimed to be.
        self.coverage_gaps = 0
        self.rows_before_anchor_lost = 0
        self.duplicate_rows_skipped = 0
        self._seen: set = set()
        self.counts: Dict[str, collections.Counter] = {
            s: collections.Counter() for s in SCENARIOS}
        self.booked: set = set()

    # ---------------------------------------------------------- ingest
    def ingest_frame(self, msg: Any, now: float) -> None:
        self.ladder.ingest(msg, now)

    def classify(self, scenario: str, side: str) -> str:
        r = self.inv[scenario].residual(self.slug)
        if r == 0:
            return OPEN
        same = (r > 0 and side == UP) or (r < 0 and side == DOWN)
        return EXTEND if same else COMPLETE

    def place(self, side: str, own_px: D, qty: D, now: float,
              bid: Optional[D], ask: Optional[D],
              coid: Optional[str] = None) -> Dict[str, Any]:
        """Record one maker intent as a MODELLED resting order."""
        self.intents += 1
        o = HypoOrder(self.slug, side, own_px, qty, now, ladder=self.ladder,
                      bid=bid, ask=ask, coid=coid)
        intent_kind = {s: self.classify(s, side) for s in SCENARIOS}
        self.orders.append(o)
        if not o.restable:
            self.not_restable += 1
            o.end_reason = "WOULD_HAVE_CROSSED_NOT_RESTED"
        else:
            self.open_orders.append(o)
        for s in SCENARIOS:
            self.counts[s]["intent:%s" % intent_kind[s]] += 1
        rec = o.record()
        rec["modelled_intent_kind"] = intent_kind
        rec["modelled_residual_at_placement"] = {
            s: str(self.inv[s].residual(self.slug)) for s in SCENARIOS}
        return rec

    @staticmethod
    def row_id(p: Mapping[str, Any]) -> tuple:
        """Identity of one print.

        `trade_time` is the venue's own stamp and is included when served,
        because `t` alone collides for every print in a frame.
        """
        return (p.get("t"), p.get("trade_time"), str(p.get("px")),
                str(p.get("qty")), p.get("dir"))

    def _unconsumed(self, rows: List[Mapping[str, Any]]
                    ) -> List[Mapping[str, Any]]:
        """The prints we have not folded yet, by identity anchor.

        Returns rows AFTER the last one consumed. When that anchor is gone
        the rolling buffer has overtaken us: a coverage GAP, which is
        counted and reported. Identity still filters what we have already
        seen, so a gap over-reads rather than double-counts.
        """
        if not rows:
            return []
        if self.last_row is None:
            tail = rows
        else:
            idx = None
            for i in range(len(rows) - 1, -1, -1):
                if self.row_id(rows[i]) == self.last_row:
                    idx = i
                    break
            if idx is None:
                # the anchor fell off the back of the 500-row buffer
                self.coverage_gaps += 1
                self.rows_before_anchor_lost += len(rows)
                tail = rows
            else:
                tail = rows[idx + 1:]
        out = []
        for p in tail:
            rid = self.row_id(p)
            if rid in self._seen:
                self.duplicate_rows_skipped += 1
                continue
            self._seen.add(rid)
            out.append(p)
        if out:
            self.last_row = self.row_id(out[-1])
        # bounded: the buffer is 500 rows, so a few thousand identities is
        # ample and keeps this from growing without limit over a window.
        if len(self._seen) > 20000:
            self._seen = set(list(self._seen)[-5000:])
            self.last_row = None          # anchor may have been evicted
        return out

    def advance(self, prints: Sequence[Mapping[str, Any]]
                ) -> List[Dict[str, Any]]:
        """Fold every print newer than the cursor into every open order.

        The recorder keeps a ROLLING 500 trades, so the cursor is counted
        against what we have already consumed rather than assuming the list
        is the whole window.
        """
        rows = list(prints or ())
        new = self._unconsumed(rows)
        self.prints_consumed += len(new)
        if not new:
            return []
        events: List[Dict[str, Any]] = []
        for o in list(self.open_orders):
            before = dict(o.filled)
            o.advance(new)
            for s in SCENARIOS:
                if o.filled[s] and not before[s]:
                    key = (id(o), s)
                    if key in self.booked:
                        continue
                    self.booked.add(key)
                    kind = self.classify(s, o.side)
                    self.inv[s].fill(self.slug, o.side, o.qty, o.own_px,
                                     o.fill_t[s])
                    self.counts[s]["fill:%s" % kind] += 1
                    events.append({
                        "scenario": s, "kind": kind, "coid": o.coid,
                        "side": o.side, "own_px": str(o.own_px),
                        "qty": str(o.qty), "t": o.fill_t[s],
                        "queue_ahead": (None if o.queue_ahead is None
                                        else str(o.queue_ahead)),
                        "volume_through_level": str(o.cleared),
                        "swept": o.swept,
                        "modelled_residual_after": str(
                            self.inv[s].residual(self.slug)),
                        "labels": "MODELLED fill. Never submitted.",
                    })
            if all(o.filled.values()):
                self.open_orders.remove(o)
        return events

    def close(self, up_won: Optional[bool],
              wire_at_close: Optional[D] = None) -> Dict[str, Any]:
        """Window close: cancel what still rests, settle, report."""
        for o in self.open_orders:
            if o.end_reason is None:
                o.end_reason = "RESTED_TO_MARKET_CLOSE_UNFILLED"
        self.open_orders = []
        out: Dict[str, Any] = {
            "slug": self.slug, "modelled_intents": self.intents,
            "not_restable": self.not_restable,
            "up_won": up_won,
            "queue_known": sum(1 for o in self.orders
                               if o.queue_ahead is not None),
            "queue_unknown": sum(1 for o in self.orders
                                 if o.queue_ahead is None),
            "prints_consumed": self.prints_consumed,
            "trade_coverage": {
                "coverage_gaps": self.coverage_gaps,
                "rows_seen_after_a_gap": self.rows_before_anchor_lost,
                "duplicate_rows_skipped": self.duplicate_rows_skipped,
                "complete": self.coverage_gaps == 0,
                "labels": ("a gap means the recorder's rolling 500-row "
                           "trade buffer overtook our anchor, so prints "
                           "existed that the model never saw. Modelled "
                           "fills are then an UNDERCOUNT and coverage is "
                           "NOT complete.")},
            "scenarios": {},
            "labels": ("MODELLED only. These are not fills and not realized "
                       "profit. A None queue never fills except on a sweep."),
            "fidelity": {
                "mode": "CLASSIFICATION_OF_REAL_INTENTS",
                "inventory_driven_generation": False,
                "what_completes_mean": (
                    "an opposite-side intent arrived while the modelled "
                    "book was long. Evidence that pair completion is "
                    "AVAILABLE in the live stream, NOT evidence the engine "
                    "would pursue it."),
                "not_claimed": (
                    "this is NOT faithful inventory-driven behavioural "
                    "replay. Modelled inventory never causes an intent to "
                    "exist, and never changes its side, price, size or "
                    "timing. The decision path reads the REAL store only."),
            },
        }
        ticks = [o.ticks_behind_touch(self.tick) for o in self.orders]
        ticks = sorted(t for t in ticks if t is not None)
        if ticks:
            out["ticks_behind_touch"] = {
                "median": ticks[len(ticks) // 2],
                "min": ticks[0], "max": ticks[-1]}
        for s in SCENARIOS:
            filled = [o for o in self.orders if o.filled[s]]
            adv = [M for M in (adverse_selection(o, s, wire_at_close)
                               for o in filled) if M]
            blk = {
                "modelled_fills": len(filled),
                "fill_rate": (round(float(len(filled)) / self.intents, 4)
                              if self.intents else None),
                "opens": self.counts[s]["fill:%s" % OPEN],
                "extends": self.counts[s]["fill:%s" % EXTEND],
                "completes": self.counts[s]["fill:%s" % COMPLETE],
                "adverse_fills": sum(1 for a in adv if a["adverse"]),
                "adverse_rate": (round(float(sum(1 for a in adv
                                                 if a["adverse"])) / len(adv),
                                       4) if adv else None),
            }
            if up_won is not None:
                blk["settlement"] = self.inv[s].settle(self.slug, up_won)
            else:
                blk["settlement"] = {
                    "modelled_net": None,
                    "why": "window outcome not established; nothing booked",
                    "residual": str(self.inv[s].residual(self.slug)),
                    "matched": str(self.inv[s].matched(self.slug)),
                    "cash_out": str(self.inv[s].cash_out(self.slug))}
            out["scenarios"][s] = blk
        return out
