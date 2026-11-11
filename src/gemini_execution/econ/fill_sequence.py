"""THE FILL SEQUENCE. Price availability is not profit; this tracks what filled.

WHY THIS REPLACES PRICE AVAILABILITY AS THE PRIMARY MEASUREMENT. Profit sprint
#1 found that every blocked candidate had an economically acceptable complement
appear within ~10 seconds at a best pair basis of 0.34. That is PRICE GEOMETRY.
It says the two bids summed to 0.34, not that we would have been hit on either.
A wide book makes `yes_bid + no_bid` small almost by definition, and reading
that as obtainable profit is the single easiest way to talk ourselves into a
strategy that never trades.

So the unit here is a SEQUENCE, and every step is a separate fact:

    1  candidate          the entry rule admitted this state
    2  rested             a rung was actually placed at a price
    3  first_fill         the common fill model says the tape reached it
    4  markout            maker-perspective, after that first fill
    5  complement_price   an acceptable opposite price appeared
    6  complement_fill    the SAME fill model says the opposite rung filled
    7  completion_time    first fill -> complement fill
    8  pair_basis         only when BOTH legs actually filled
    9  residual           what happened when the complement did not fill
   10  reduce_exit        best exit economics available to that residual
   11  terminal           settlement where the venue has said

Steps 5 and 6 are deliberately separate. The gap between them IS the thesis
under test: "reversible two-sided flow plus an unconstrained wide passive spread
produces first fills that can subsequently recycle into economically favourable
opposite-side maker fills." If 5 stays high and 6 stays at zero, the thesis is
dead and the 0.34 basis was always a mirage.

THE FILL MODEL IS THE COMMON ONE. Fills come from `HypotheticalBook`, the same
queue accounting every other arm is scored by, with all of its caveats: it is an
UPPER BOUND, size ahead is the displayed size at placement, and our own presence
would have changed the flow being replayed. Nothing here is submitted, filled,
settled or realized.

DEDUPLICATION ACROSS ARMS. The flow-evidence ladder runs four arms over the
SAME cycles, so a state admitted by F0, F1 and F2 is ONE market observation
seen three times, not three observations. Every candidate therefore carries an
`observation_id` derived deterministically from (symbol, decision instant, leg,
price) -- identical across arms by construction -- so a cross-arm comparison
counts unique states rather than multiplying its own sample by the number of
arms it happens to be running.

CAPACITY IS PART OF THE ANSWER. A sequence that completes twice a day is a
curiosity; the same sequence forty times a day is a business. Opportunity and
completion rates are therefore reported PER DAY as well as per window.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution.econ import book_move as BM
from src.gemini_execution.econ import terminal as TM
from src.gemini_execution.econ import veto as VT

SCHEMA = "gemini_econ_fill_sequence/v1"

MARKOUT_HORIZONS_S = (8, 30, 60, 120)

#: confirmed-settlement checkpoints the audit reports the sign and its
#: uncertainty at. Fixed in advance so the read is not chosen after the fact.
CHECKPOINTS = (20, 50, 100)

LABELS = (
    "SHADOW/HYPOTHETICAL. Fills come from the common HypotheticalBook and are "
    "an UPPER BOUND; nothing is submitted, filled, settled or realized.",
    "A complement PRICE appearing and a complement FILLING are separate "
    "facts, counted separately. The gap between them is the thesis under test.",
    "pair_basis is reported ONLY when both legs actually filled. A basis from "
    "two resting bids is price geometry, not obtainable profit.",
)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _utc(t: Any) -> Optional[str]:
    if t is None:
        return None
    import datetime
    try:
        return datetime.datetime.fromtimestamp(
            float(t), datetime.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _bid(book: Mapping[str, Any], leg: str) -> Optional[D]:
    return _dec(((book or {}).get(str(leg).lower()) or {}).get("bid"))


def observation_id(*, symbol: str, t: Any, leg: Any, price: Any,
                   quantum_s: float = 1.0) -> str:
    """A stable id for ONE market observation, shared by every arm that sees it.

    Derived rather than passed down, so arms stepping independently inside the
    same cycle agree without the collector having to thread an id through a
    signature the control also uses. The instant is quantised to `quantum_s`
    because the arms step microseconds apart within a cycle, not at the same
    float.
    """
    import hashlib
    q = int(float(t) // float(quantum_s or 1.0))
    raw = "%s|%d|%s|%s" % (symbol, q, str(leg or "").upper(), price)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class Sequence:
    """One admitted entry, followed through the whole fill sequence."""

    seq_id: int
    cohort: str
    symbol: str
    arm: str
    t_candidate: float
    tte_candidate: Optional[float]
    min_edge: str
    tick: D

    observation_id: Optional[str] = None
    rested: bool = False
    rested_legs: List[str] = field(default_factory=list)
    #: the DUAL-RUNG capture: both outcomes' bids, asks and spreads at the
    #: candidate instant, and how the book had moved by each horizon
    book_t0: Dict[str, Any] = field(default_factory=dict)
    book_moves: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    #: what the entry rule WANTED, before anything filled. Kept so the
    #: per-leg funnel has a denominator: "48 YES first fills" means nothing
    #: without the number of YES rungs that were offered in the first place.
    candidate_leg: Optional[str] = None
    candidate_price: Optional[str] = None

    first_fill_leg: Optional[str] = None
    first_fill_price: Optional[D] = None
    first_fill_t: Optional[float] = None
    first_fill_qty: Optional[D] = None
    first_fill_queue_known: Optional[bool] = None

    ceiling: Optional[D] = None
    complement_price_t: Optional[float] = None
    best_complement_price: Optional[D] = None

    complement_fill_price: Optional[D] = None
    complement_fill_t: Optional[float] = None
    complement_fill_qty: Optional[D] = None

    markouts: Dict[str, float] = field(default_factory=dict)
    #: WHAT THE MARKOUT WAS ACTUALLY MEASURED AGAINST. The horizon is the
    #: REQUESTED age; the observation that satisfied it is the first cycle at
    #: or after that age, so the realised age is >= h and was not recorded
    #: anywhere until now. Reporting a 68s reading as "60s" is the same class
    #: of quiet fiction the pre-fill ring already refuses to commit.
    markout_meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    max_adverse_ticks: float = 0.0
    best_exit_price: Optional[D] = None
    residual_qty: Optional[D] = None
    settlement_leg: Optional[str] = None
    closed: bool = False

    # ------------------------------------------------------------ events
    def note_rest(self, legs: Sequence[str]) -> None:
        if legs:
            self.rested = True
            for l in legs:
                if l not in self.rested_legs:
                    self.rested_legs.append(l)

    def note_fill(self, fill: Mapping[str, Any], *, t: float) -> None:
        leg = str(fill.get("leg") or "").upper()
        px, qty = _dec(fill.get("price")), _dec(fill.get("qty"))
        if leg not in ("YES", "NO") or px is None:
            return
        if self.first_fill_leg is None:
            self.first_fill_leg = leg
            self.first_fill_price = px
            self.first_fill_qty = qty
            self.first_fill_t = float(t)
            self.first_fill_queue_known = fill.get("queue_known")
            ceil = VT.max_complement_price(held_avg_cost=px,
                                           min_edge=self.min_edge,
                                           tick=self.tick)
            self.ceiling = (_dec(ceil.get("ceiling")) if ceil.get("ok")
                            else None)
            return
        # a fill on the OPPOSITE leg completes the pair
        if leg != self.first_fill_leg and self.complement_fill_price is None:
            self.complement_fill_price = px
            self.complement_fill_qty = qty
            self.complement_fill_t = float(t)

    def observe(self, *, book: Mapping[str, Any], t: float) -> None:
        """Markouts, complement-price availability and exit economics."""
        if self.closed:
            return
        # THE DUAL-RUNG BOOK MOVE. Recorded from the CANDIDATE instant, not
        # from the first fill, because the question is what the book does
        # after we would have POSTED into it.
        if self.book_t0:
            age0 = float(t) - float(self.t_candidate)
            for h in MARKOUT_HORIZONS_S:
                k = str(h)
                if k not in self.book_moves and age0 >= h:
                    self.book_moves[k] = BM.classify(
                        self.book_t0, BM.snapshot(book), tick=self.tick)
        if self.first_fill_leg is None:
            return
        held, comp = self.first_fill_leg, (
            "NO" if self.first_fill_leg == "YES" else "YES")
        # `self.first_fill_t or t` would fall back to `t` for a fill stamped
        # exactly 0.0, making every age zero and every markout unrecordable.
        # Latent rather than live -- time.time() is never 0 -- but it is the
        # expression this audit is about, so it is stated explicitly.
        age = float(t) - float(self.first_fill_t
                               if self.first_fill_t is not None else t)

        b = _bid(book, held)
        if b is not None and self.tick > 0 and self.first_fill_price is not None:
            mo = float((b - self.first_fill_price) / self.tick)
            self.max_adverse_ticks = min(self.max_adverse_ticks, mo)
            _set_now: List[str] = []
            for h in MARKOUT_HORIZONS_S:
                k = str(h)
                if k not in self.markouts and age >= h:
                    self.markouts[k] = mo
                    _set_now.append(k)
                    self.markout_meta[k] = {
                        "requested_age_s": float(h),
                        "realised_age_s": round(age, 3),
                        "age_error_s": round(age - float(h), 3),
                        "ref_t": float(t),
                        "ref_field": "held_leg_bid",
                        "ref_price": str(b),
                        "entry_price": str(self.first_fill_price),
                        "tick": str(self.tick),
                        "formula": ("markout_ticks = (held_leg_bid - "
                                    "first_fill_price) / tick; POSITIVE is "
                                    "favourable to the maker who is LONG the "
                                    "held leg"),
                    }
            # ALIASING. One observation can satisfy several horizons at once
            # -- if the book was unreadable for two minutes the first reading
            # afterwards fills 8s, 30s, 60s and 120s with the SAME number.
            # Those horizons are then not independent measurements, and any
            # claim about markout DETERIORATING with horizon is void on such
            # a row. Recorded per horizon rather than inferred later.
            if len(_set_now) > 1:
                for k in _set_now:
                    self.markout_meta[k]["aliased_with"] = [
                        x for x in _set_now if x != k]
            # best price we could have REDUCED/EXITED at, passively (the ask)
            a = _dec(((book or {}).get(held.lower()) or {}).get("ask"))
            if a is not None and (self.best_exit_price is None
                                  or a > self.best_exit_price):
                self.best_exit_price = a

        cb = _bid(book, comp)
        if cb is not None:
            if (self.best_complement_price is None
                    or cb < self.best_complement_price):
                self.best_complement_price = cb
            if (self.ceiling is not None and cb <= self.ceiling
                    and self.complement_price_t is None):
                self.complement_price_t = float(t)

    def close(self, *, residual_qty: Any = None,
              settlement_leg: Optional[str] = None) -> None:
        self.residual_qty = _dec(residual_qty)
        self.settlement_leg = settlement_leg
        self.closed = True

    # ------------------------------------------------------------ report
    def row(self) -> Dict[str, Any]:
        matched = (self.first_fill_price is not None
                   and self.complement_fill_price is not None)
        basis = ((self.first_fill_price + self.complement_fill_price)
                 if matched else None)
        econ = None
        if matched:
            q = min(self.first_fill_qty or D("0"),
                    self.complement_fill_qty or D("0"))
            econ = VT.pair_economics(
                held_qty=q, held_avg_cost=self.first_fill_price,
                complement_qty=q, complement_price=self.complement_fill_price)
        settle_v = None
        if (self.settlement_leg in ("YES", "NO")
                and self.first_fill_price is not None and not matched):
            win = D("1") if self.settlement_leg == self.first_fill_leg else D("0")
            settle_v = str(win - self.first_fill_price)
        exit_econ = None
        if (self.first_fill_price is not None and not matched
                and self.best_exit_price is not None):
            exit_econ = VT.roundtrip_economics(
                qty=(self.first_fill_qty or D("0")),
                held_avg_cost=self.first_fill_price,
                exit_price=self.best_exit_price)
        return {
            "schema": SCHEMA, "seq_id": self.seq_id, "arm": self.arm,
            "observation_id": self.observation_id,
            "book_t0": dict(self.book_t0),
            "book_move_by_horizon_s": {
                k: {"klass": v.get("klass"),
                    "d_bid_sum_ticks": v.get("d_bid_sum_ticks"),
                    "d_yes_bid_ticks": v.get("d_yes_bid_ticks"),
                    "d_no_bid_ticks": v.get("d_no_bid_ticks")}
                for k, v in sorted(self.book_moves.items())},
            "cohort": self.cohort, "symbol": self.symbol,
            "t_candidate": self.t_candidate, "tte_s": self.tte_candidate,
            "candidate_leg": self.candidate_leg,
            "candidate_price": self.candidate_price,
            "rested": self.rested, "rested_legs": list(self.rested_legs),
            "first_fill": self.first_fill_leg is not None,
            "first_fill_leg": self.first_fill_leg,
            "first_fill_price": (None if self.first_fill_price is None
                                 else str(self.first_fill_price)),
            "first_fill_queue_known": self.first_fill_queue_known,
            # THE JOIN KEY to the toxicity dataset. Both stores take this
            # value from the SAME `time.time()` call in the same cycle, so
            # matching on it is exact rather than a tolerance heuristic.
            # `t_candidate` is a DIFFERENT event on the same clock -- the
            # cycle the rung was admitted, at least one cycle earlier -- and
            # joining on it is what desynchronised the two stores.
            "first_fill_t": self.first_fill_t,
            "markout_ticks_by_horizon_s": dict(self.markouts),
            "markout_measurement": dict(self.markout_meta),
            "max_adverse_markout_ticks": self.max_adverse_ticks,
            "complement_price_appeared": self.complement_price_t is not None,
            "time_to_complement_price_s": (
                None if (self.complement_price_t is None
                         or self.first_fill_t is None)
                else round(self.complement_price_t - self.first_fill_t, 2)),
            "best_complement_price": (None if self.best_complement_price is None
                                      else str(self.best_complement_price)),
            "complement_filled": self.complement_fill_price is not None,
            "complement_fill_price": (None if self.complement_fill_price is None
                                      else str(self.complement_fill_price)),
            "time_first_to_complement_fill_s": (
                None if (self.complement_fill_t is None
                         or self.first_fill_t is None)
                else round(self.complement_fill_t - self.first_fill_t, 2)),
            "matched": matched,
            "pair_basis_if_both_filled": None if basis is None else str(basis),
            "pair_economics": econ,
            "residual_qty": (None if self.residual_qty is None
                             else str(self.residual_qty)),
            "best_exit_price": (None if self.best_exit_price is None
                                else str(self.best_exit_price)),
            "best_exit_economics": exit_econ,
            "settlement_leg": self.settlement_leg,
            "residual_settlement_value": settle_v,
            "stage": "shadow", "labels": list(LABELS),
        }


@dataclass
class FillSequenceLedger:
    """Every admitted entry's sequence, and the funnel over them."""

    arm: str = "GEMINI_ECON_V1"
    open_: List[Sequence] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    candidates: int = 0
    _next_id: int = 1
    first_t: Optional[float] = None
    last_t: Optional[float] = None
    max_open: int = 100
    max_rows: int = 4000

    def candidate(self, *, cohort: str, symbol: str, t: float, tte_s: Any,
                  min_edge: Any = "0.005", tick: Any = "0.01",
                  leg: Any = None, price: Any = None,
                  book: Optional[Mapping[str, Any]] = None) -> Sequence:
        self.candidates += 1
        self.first_t = self.first_t if self.first_t is not None else float(t)
        self.last_t = float(t)
        sq = Sequence(seq_id=self._next_id, cohort=cohort, symbol=symbol,
                      arm=self.arm, t_candidate=float(t),
                      tte_candidate=(None if tte_s is None else float(tte_s)),
                      min_edge=str(min_edge), tick=_dec(tick) or D("0.01"),
                      candidate_leg=(str(leg).upper()
                                     if leg is not None else None),
                      candidate_price=(None if price is None else str(price)),
                      observation_id=observation_id(
                          symbol=symbol, t=t, leg=leg, price=price),
                      book_t0=(BM.snapshot(book) if book else {}))
        self._next_id += 1
        if len(self.open_) < self.max_open:
            self.open_.append(sq)
        return sq

    def note_fills(self, fills: Sequence[Mapping[str, Any]], *, t: float,
                   symbol: str) -> None:
        for f in fills or []:
            for sq in self.open_:
                if sq.symbol == symbol and not sq.closed:
                    sq.note_fill(f, t=t)

    def observe(self, *, book: Mapping[str, Any], t: float,
                symbol: str) -> None:
        self.last_t = float(t)
        for sq in self.open_:
            if sq.symbol == symbol and not sq.closed:
                sq.observe(book=book, t=t)

    def close_symbol(self, symbol: str, *, residual_qty: Any = None,
                     settlement_leg: Optional[str] = None) -> int:
        n = 0
        for sq in list(self.open_):
            if sq.symbol != symbol:
                continue
            sq.close(residual_qty=residual_qty, settlement_leg=settlement_leg)
            self.open_.remove(sq)
            self.rows.append(sq.row())
            n += 1
        if len(self.rows) > self.max_rows:
            self.rows = self.rows[-(self.max_rows // 2):]
        return n

    def label_settlement(self, symbol: str,
                         settlement_leg: Optional[str]) -> Dict[str, Any]:
        """STEP 11. Write the venue's published outcome onto closed rows.

        The outcome arrives AFTER the window it belongs to -- the contract
        object only reads `settled` once the venue has resolved it, and that
        read flaps for minutes afterwards -- so the sequence is closed first
        and labelled later. That is safe here and only here: a settlement is
        never a feature, only ever the target, exactly as the toxicity
        dataset already treats it. Nothing about the decision that produced
        the fill is recomputed.

        Only a residual row can carry a settlement value. A matched pair has
        no directional leg left to settle, and a row that never filled has no
        position at all; both are skipped rather than defaulted to zero.
        """
        out: Dict[str, Any] = {"symbol": symbol, "settlement_leg": None,
                               "labelled": 0, "already": 0, "skipped": 0,
                               "why": None}
        leg = str(settlement_leg or "").upper()
        if leg not in ("YES", "NO"):
            out["why"] = ("settlement leg %r is not YES or NO; a residual is "
                          "left UNLABELLED rather than given a made-up "
                          "outcome" % (settlement_leg,))
            return out
        out["settlement_leg"] = leg
        for r in self.rows:
            if r.get("symbol") != symbol:
                continue
            if not r.get("first_fill") or r.get("matched"):
                out["skipped"] += 1
                continue
            if r.get("residual_settlement_value") is not None:
                out["already"] += 1
                continue
            px = _dec(r.get("first_fill_price"))
            if px is None:
                out["skipped"] += 1
                continue
            win = D("1") if leg == r.get("first_fill_leg") else D("0")
            r["settlement_leg"] = leg
            r["residual_settlement_value"] = str(win - px)
            out["labelled"] += 1
        return out

    def unlabelled_symbols(self, *, exclude: Optional[str] = None,
                           limit: int = 4) -> List[str]:
        """Contracts with a residual whose outcome we have not yet recorded.

        `exclude` drops the window that has only just closed: its contract has
        not resolved yet, and asking produces a stale `active` read rather
        than an answer.
        """
        seen: List[str] = []
        for r in reversed(self.rows):
            sym = r.get("symbol")
            if (not sym or sym == exclude or sym in seen
                    or not r.get("first_fill") or r.get("matched")
                    or r.get("residual_settlement_value") is not None):
                continue
            seen.append(sym)
            if len(seen) >= limit:
                break
        return seen

    # ---------------------------------------------------------- durability
    def save(self, path: str) -> None:
        """Atomic. The cohort in `rows` is the evidence; a torn file is not."""
        import json
        import os as _os
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"schema": SCHEMA, "arm": self.arm, "rows": self.rows,
                       "candidates": self.candidates,
                       "first_t": self.first_t, "last_t": self.last_t}, fh,
                      default=str)
        _os.replace(tmp, path)

    @classmethod
    def load(cls, path: str, *, arm: Optional[str] = None
             ) -> "FillSequenceLedger":
        """Best effort. Open sequences are NOT restored: a half-observed
        sequence resumed against a later window's book would mark one
        contract's fill against another's tape."""
        import json
        led = cls(arm=arm or "GEMINI_ECON_V1")
        try:
            with open(path) as fh:
                d = json.load(fh)
        except (OSError, ValueError, TypeError):
            return led
        if not isinstance(d, dict) or d.get("schema") != SCHEMA:
            return led
        led.rows = [r for r in (d.get("rows") or []) if isinstance(r, dict)]
        try:
            led.candidates = int(d.get("candidates") or 0)
        except (TypeError, ValueError):
            led.candidates = 0
        for k in ("first_t", "last_t"):
            v = d.get(k)
            try:
                setattr(led, k, None if v is None else float(v))
            except (TypeError, ValueError):
                setattr(led, k, None)
        return led

    # ------------------------------------------------- per-leg denominators
    @staticmethod
    def _leg_funnel(rows: List[Dict[str, Any]], leg: str) -> Dict[str, Any]:
        """The WHOLE funnel for one leg, not a first-fill-conditional rate.

        THE DENOMINATOR IS THE POINT. "24 of 48 YES first fills completed"
        and "0 of 140 NO first fills completed" are conditional on being
        filled, and being filled is itself side-dependent -- a rung that
        rarely fills and a rung that fills constantly are not comparable at
        the fill. So every step is counted from the rung that was offered.

        NOTE the arm rests BOTH legs from one candidate state, so these do
        not partition the candidates and must not be summed against them.
        The unit here is a RUNG on a leg, not a candidate.
        """
        L = leg.upper()
        offered = [r for r in rows
                   if L in [str(x).upper() for x in (r.get("rested_legs") or [])]]
        cand = [r for r in rows
                if str(r.get("candidate_leg") or "").upper() == L]
        first = [r for r in rows
                 if str(r.get("first_fill_leg") or "").upper() == L]
        capp = [r for r in first if r.get("complement_price_appeared")]
        cfill = [r for r in first if r.get("complement_filled")]
        # the same leg acting as the COMPLEMENT of the other leg's first fill
        as_comp = [r for r in rows
                   if r.get("complement_filled")
                   and str(r.get("first_fill_leg") or "").upper() not in
                   ("", L)]
        settled = [r for r in first
                   if not r.get("matched")
                   and r.get("residual_settlement_value") is not None]
        return {
            "leg": L,
            "candidate_rungs": len(cand),
            "rungs_rested": len(offered),
            "first_fills": len(first),
            "first_fill_rate_of_rested_rungs": (len(first) / len(offered)
                                                if offered else None),
            "complement_price_appeared": len(capp),
            "complement_fills": len(cfill),
            "completion_rate_of_first_fills": (len(cfill) / len(first)
                                               if first else None),
            "completion_rate_of_rested_rungs": (len(cfill) / len(offered)
                                                if offered else None),
            "fills_as_the_OTHER_legs_complement": len(as_comp),
            "residuals_settled": len(settled),
            "why": ("a candidate state may rest BOTH legs, so these counts "
                    "overlap across legs and never sum to the candidate "
                    "total"),
        }

    @staticmethod
    def _bucket_tte(v: Any) -> str:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return "unknown"
        for hi, name in ((60, "0-60"), (120, "60-120"), (180, "120-180"),
                         (300, "180-300"), (600, "300-600")):
            if x <= hi:
                return name
        return "600+"

    @staticmethod
    def _bucket_price(v: Any) -> str:
        p = _dec(v)
        if p is None:
            return "unknown"
        for hi, name in ((D("0.20"), "<=0.20"), (D("0.40"), "0.20-0.40"),
                         (D("0.60"), "0.40-0.60"), (D("0.80"), "0.60-0.80")):
            if p <= hi:
                return name
        return ">0.80"

    def _breakouts(self, first: List[Dict[str, Any]]) -> Dict[str, Any]:
        """First fills cut every way the audit asked for, always BY LEG.

        A cut that pools the legs would hide the only effect being tested.
        """
        def cell(rs):
            cf = sum(1 for r in rs if r.get("complement_filled"))
            return {"first_fills": len(rs), "complement_fills": cf,
                    "completion_rate": (cf / len(rs)) if rs else None}

        def by(keyfn):
            out: Dict[str, Dict[str, Any]] = {}
            for r in first:
                leg = str(r.get("first_fill_leg") or "?").upper()
                out.setdefault(str(keyfn(r)), {}).setdefault(leg, []).append(r)
            return {k: {leg: cell(rs) for leg, rs in v.items()}
                    for k, v in sorted(out.items())}

        return {
            "by_cohort": by(lambda r: r.get("cohort")),
            "by_tte_bucket_s": by(lambda r: self._bucket_tte(r.get("tte_s"))),
            "by_entry_price_bucket": by(
                lambda r: self._bucket_price(r.get("first_fill_price"))),
            "by_market_direction": by(
                lambda r: (r.get("settlement_leg") or "UNRESOLVED")),
            "why": ("by_market_direction is the contract's PUBLISHED outcome "
                    "and is only populated once settlement has been read; "
                    "UNRESOLVED is an absence of evidence, not a third "
                    "outcome"),
        }

    # ------------------------------------------------------------ funnel
    def report(self) -> Dict[str, Any]:
        rows = self.rows
        n = len(rows)
        span_s = ((self.last_t - self.first_t)
                  if (self.first_t is not None and self.last_t is not None)
                  else None)
        per_day = (86400.0 / span_s) if (span_s and span_s > 0) else None

        def med(xs):
            return statistics.median(xs) if xs else None

        rested = [r for r in rows if r["rested"]]
        first = [r for r in rows if r["first_fill"]]
        capp = [r for r in first if r["complement_price_appeared"]]
        cfill = [r for r in first if r["complement_filled"]]
        matched = [r for r in rows if r["matched"]]
        resid = [r for r in first if not r["matched"]]

        bases = [float(r["pair_basis_if_both_filled"]) for r in matched
                 if r["pair_basis_if_both_filled"] is not None]
        nets = [float((r["pair_economics"] or {}).get("net_pair_economics", 0))
                for r in matched if r.get("pair_economics")]

        out: Dict[str, Any] = {
            "schema": SCHEMA, "arm": self.arm,
            "observation_span_s": (None if span_s is None else round(span_s, 1)),
            # ---- the funnel, each step counted separately
            "candidate_states": self.candidates,
            "sequences_closed": n,
            "orders_hypothetically_rested": len(rested),
            "first_fills": len(first),
            "first_fill_rate_of_rested": (len(first) / len(rested)
                                          if rested else None),
            "complement_price_appearances": len(capp),
            "complement_price_rate_of_first_fills": (len(capp) / len(first)
                                                     if first else None),
            "complement_fills": len(cfill),
            # THE NUMBER THE THESIS LIVES OR DIES ON
            "maker_to_maker_completion_rate_of_first_fills": (
                len(cfill) / len(first) if first else None),
            "median_completion_time_s": med(
                [r["time_first_to_complement_fill_s"] for r in cfill
                 if r["time_first_to_complement_fill_s"] is not None]),
            "median_time_to_complement_price_s": med(
                [r["time_to_complement_price_s"] for r in capp
                 if r["time_to_complement_price_s"] is not None]),
            "matched_pairs": len(matched),
            "matched_basis_p50": med(bases),
            "matched_basis_min": (min(bases) if bases else None),
            "matched_net_p50_excl_subsidy": med(nets),
            "matched_net_total_excl_subsidy": (sum(nets) if nets else None),
            "residual_rate_of_first_fills": (len(resid) / len(first)
                                             if first else None),
            "residual_settlement_p50": med(
                [float(r["residual_settlement_value"]) for r in resid
                 if r["residual_settlement_value"] is not None]),
            "residual_best_exit_net_p50": med(
                [float((r["best_exit_economics"] or {}).get("net_roundtrip", 0))
                 for r in resid if r.get("best_exit_economics")]),
            # ---- capacity
            "unique_observations": len({r.get("observation_id")
                                        for r in rows
                                        if r.get("observation_id")}),
            "book_move_classes_by_horizon_s": {
                h: dict(__import__("collections").Counter(
                    (r["book_move_by_horizon_s"].get(h) or {}).get("klass")
                    for r in rows
                    if (r["book_move_by_horizon_s"].get(h) or {}).get("klass")))
                for h in (str(x) for x in MARKOUT_HORIZONS_S)},
            # ---- THE AUDIT SECTIONS
            "window": {
                "first_t": self.first_t, "last_t": self.last_t,
                "first_utc": _utc(self.first_t), "last_utc": _utc(self.last_t),
                "symbols": sorted({r.get("symbol") for r in rows
                                   if r.get("symbol")}),
                "cohorts": sorted({r.get("cohort") for r in rows
                                   if r.get("cohort")}),
                "seq_id_range": ([rows[0].get("seq_id"),
                                  rows[-1].get("seq_id")] if rows else None),
            },
            "by_leg": {leg: self._leg_funnel(rows, leg)
                       for leg in ("YES", "NO")},
            "breakouts": self._breakouts(first),
            "terminal": {
                "settled_residuals": len(
                    [r for r in resid
                     if r.get("residual_settlement_value") is not None]),
                "rows": [t for t in (TM.residual_terminal(r) for r in resid)
                         if t],
            },
            "expectancy": {
                "ALL": TM.expectancy(rows),
                "YES": TM.expectancy(rows, leg="YES"),
                "NO": TM.expectancy(rows, leg="NO"),
            },
            "checkpoints": {
                str(t): TM.checkpoint(rows, target=t, leg="YES")
                for t in CHECKPOINTS
            },
            "candidates_per_day": (None if per_day is None
                                   else round(self.candidates * per_day, 1)),
            "first_fills_per_day": (None if per_day is None
                                    else round(len(first) * per_day, 1)),
            "completions_per_day": (None if per_day is None
                                    else round(len(cfill) * per_day, 1)),
            "labels": list(LABELS),
        }
        for h in MARKOUT_HORIZONS_S:
            xs = [r["markout_ticks_by_horizon_s"].get(str(h)) for r in first]
            xs = [x for x in xs if x is not None]
            out["post_first_fill_markout_p50_%ds" % h] = med(xs)
            out["post_first_fill_adverse_share_%ds" % h] = (
                (sum(1 for x in xs if x < 0) / len(xs)) if xs else None)
        by_leg: Dict[str, List[Mapping[str, Any]]] = {}
        for r in first:
            by_leg.setdefault(r["first_fill_leg"] or "?", []).append(r)
        out["by_first_fill_leg"] = {
            k: {"n": len(v),
                "completion_rate": (sum(1 for r in v if r["complement_filled"])
                                    / len(v)),
                "markout_p50_60s": med(
                    [r["markout_ticks_by_horizon_s"].get("60") for r in v
                     if r["markout_ticks_by_horizon_s"].get("60") is not None])}
            for k, v in sorted(by_leg.items())}
        return out
