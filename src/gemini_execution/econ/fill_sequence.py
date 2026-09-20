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
from src.gemini_execution.econ import veto as VT

SCHEMA = "gemini_econ_fill_sequence/v1"

MARKOUT_HORIZONS_S = (8, 30, 60, 120)

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
        age = float(t) - float(self.first_fill_t or t)

        b = _bid(book, held)
        if b is not None and self.tick > 0 and self.first_fill_price is not None:
            mo = float((b - self.first_fill_price) / self.tick)
            self.max_adverse_ticks = min(self.max_adverse_ticks, mo)
            for h in MARKOUT_HORIZONS_S:
                k = str(h)
                if k not in self.markouts and age >= h:
                    self.markouts[k] = mo
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
            "rested": self.rested, "rested_legs": list(self.rested_legs),
            "first_fill": self.first_fill_leg is not None,
            "first_fill_leg": self.first_fill_leg,
            "first_fill_price": (None if self.first_fill_price is None
                                 else str(self.first_fill_price)),
            "first_fill_queue_known": self.first_fill_queue_known,
            "markout_ticks_by_horizon_s": dict(self.markouts),
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
