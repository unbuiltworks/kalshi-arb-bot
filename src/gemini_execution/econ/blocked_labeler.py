"""WOULD THE BLOCKED FILL HAVE BEEN WORTH OWNING? Research labels, forward only.

THE QUESTION ATTRIBUTION CANNOT ANSWER. `attribution.py` says SPREAD is the
binding constraint. It cannot say whether being blocked by it was GOOD for us.
A gate that blocks every fill is a disaster if those fills were profitable and a
triumph if every one was toxic, and the refusal counts look identical either
way.

So every candidate first fill -- ADMITTED OR BLOCKED -- is opened here and
followed down the subsequent tape, and the labels that come back are what
separate the two.

WHAT IS MEASURED, per candidate:

    markout at 8/30/60/120s   from the MAKER PERSPECTIVE of the proposed fill.
                              Positive is FAVOURABLE to the leg we would have
                              bought. A YES candidate marks against the YES
                              bid, a NO candidate against the NO bid -- never
                              one against the other's book.
    opposite_available        did a complement price at or under this fill's
                              OWN economic ceiling ever appear?
    time_to_opposite_s        and how long did it take
    best_pair_basis           the best complete we could have reached
    max_adverse_markout       the worst drawdown while holding
    settlement_value          terminal, where the venue has said

YES AND NO ARE NEVER POOLED. The brief is explicit that their toxicity is not
assumed symmetric, and pooling would average away exactly the asymmetry the
sprint is looking for. Every report is segmented by cohort, TTE bucket AND leg.

THIS IS LABELLING, NOT A SIGNAL, AND THE SEPARATION IS STRUCTURAL. A label is
computed from the tape AFTER the candidate moment; feeding it back into a gate
would be look-ahead of the worst kind. Nothing in `entry.py`, `veto.py` or the
arm's decision path imports this module, and a test asserts it over their ASTs.
The arm WRITES here and never READS.

A CANDIDATE IS NOT A FILL. Even the admitted ones only mean "a rung would have
rested". Whether it would have been hit is the hypothetical book's question and
carries all of that machinery's caveats; this module asks the separate question
of what the market did next.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution.econ import veto as VT

SCHEMA = "gemini_econ_blocked_labeler/v1"

#: horizons, in seconds. Preregistered, and the brief names these four.
HORIZONS_S = (8, 30, 60, 120)

#: how long a candidate is followed before it is closed out
MAX_FOLLOW_S = 300.0

LABELS = (
    "RESEARCH LABELS. Computed from the tape AFTER the candidate moment and "
    "never read by any gate. The arm writes here and never reads.",
    "Markout is from the MAKER PERSPECTIVE of the proposed fill: positive is "
    "favourable to the leg we would have bought.",
    "YES and NO are reported separately. Their toxicity is not assumed "
    "symmetric.",
    "A candidate is not a fill: an admitted candidate means a rung would have "
    "RESTED, not that it would have been hit.",
)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _leg_bid(book: Mapping[str, Any], leg: str) -> Optional[D]:
    side = (book or {}).get(str(leg).lower()) or {}
    return _dec(side.get("bid"))


@dataclass
class Candidate:
    """One proposed first fill, followed forward."""

    cand_id: int
    cohort: str
    symbol: str
    t0: float
    tte0: Optional[float]
    leg: str
    complement_leg: str
    price: D
    tick: D
    spread0: Optional[D]
    admitted: bool
    blocking: List[str]
    marginal_blocker: Optional[str]
    ceiling: Optional[D]
    features: Dict[str, Any] = field(default_factory=dict)

    markouts: Dict[str, float] = field(default_factory=dict)
    best_complement: Optional[D] = None
    opposite_t: Optional[float] = None
    max_adverse_ticks: float = 0.0
    observations: int = 0
    closed: bool = False
    settlement_leg: Optional[str] = None

    # ------------------------------------------------------------ forward
    def observe(self, *, book: Mapping[str, Any], t: float) -> None:
        """One subsequent cycle. Scores any horizon that has elapsed, once."""
        if self.closed:
            return
        age = float(t) - float(self.t0)
        self.observations += 1

        bid = _leg_bid(book, self.leg)
        if bid is not None and self.tick > 0:
            mo = float((bid - self.price) / self.tick)
            self.max_adverse_ticks = min(self.max_adverse_ticks, mo)
            for h in HORIZONS_S:
                k = str(h)
                if k not in self.markouts and age >= h:
                    self.markouts[k] = mo

        # could we have completed economically at any point?
        cbid = _leg_bid(book, self.complement_leg)
        if cbid is not None:
            if self.best_complement is None or cbid < self.best_complement:
                self.best_complement = cbid
            if (self.ceiling is not None and cbid <= self.ceiling
                    and self.opposite_t is None):
                self.opposite_t = float(t)

        if age >= MAX_FOLLOW_S:
            self.closed = True

    def settle(self, leg: Optional[str]) -> None:
        self.settlement_leg = leg
        self.closed = True

    # ------------------------------------------------------------- labels
    def label(self) -> Dict[str, Any]:
        basis = (None if self.best_complement is None
                 else self.price + self.best_complement)
        settle_v = None
        if self.settlement_leg in ("YES", "NO"):
            win = D("1") if self.settlement_leg == self.leg else D("0")
            settle_v = str(win - self.price)
        return {
            "schema": SCHEMA, "cand_id": self.cand_id, "cohort": self.cohort,
            "symbol": self.symbol, "t0": self.t0, "tte_s": self.tte0,
            "leg": self.leg, "price": str(self.price),
            "spread": (None if self.spread0 is None else str(self.spread0)),
            "admitted": self.admitted,
            "blocking": list(self.blocking),
            "marginal_blocker": self.marginal_blocker,
            "markout_ticks_by_horizon_s": dict(self.markouts),
            "max_adverse_markout_ticks": self.max_adverse_ticks,
            "opposite_available": self.opposite_t is not None,
            "time_to_opposite_s": (None if self.opposite_t is None
                                   else round(self.opposite_t - self.t0, 2)),
            "best_complement_price": (None if self.best_complement is None
                                      else str(self.best_complement)),
            "best_pair_basis": None if basis is None else str(basis),
            "best_pair_below_one": (None if basis is None
                                    else bool(basis <= D("1"))),
            "settlement_leg": self.settlement_leg,
            "settlement_value_per_contract": settle_v,
            "observations": self.observations,
            "features_at_candidate": dict(self.features),
            "stage": "shadow",
            "labels": list(LABELS),
        }


@dataclass
class BlockedLabeler:
    """Every candidate first fill, followed and labelled."""

    max_open: int = 200
    max_labels: int = 4000
    _next_id: int = 1
    open_: List[Candidate] = field(default_factory=list)
    labels: List[Dict[str, Any]] = field(default_factory=list)

    def open_candidate(self, *, cohort: str, symbol: str, t: float,
                       tte_s: Any, leg: str, price: Any, spread: Any,
                       admitted: bool, blocking: Sequence[str],
                       marginal_blocker: Optional[str],
                       min_edge: Any = "0.005", tick: Any = "0.01",
                       features: Optional[Mapping[str, Any]] = None
                       ) -> Optional[Candidate]:
        p, tk = _dec(price), _dec(tick) or D("0.01")
        if p is None or not (D("0") < p < D("1")):
            return None
        if len(self.open_) >= self.max_open:
            return None
        ceil = VT.max_complement_price(held_avg_cost=p, min_edge=min_edge,
                                       tick=tk)
        c = Candidate(
            cand_id=self._next_id, cohort=cohort, symbol=symbol, t0=float(t),
            tte0=(None if tte_s is None else float(tte_s)),
            leg=str(leg).upper(),
            complement_leg=("NO" if str(leg).upper() == "YES" else "YES"),
            price=p, tick=tk, spread0=_dec(spread), admitted=bool(admitted),
            blocking=list(blocking or []), marginal_blocker=marginal_blocker,
            ceiling=(_dec(ceil.get("ceiling")) if ceil.get("ok") else None),
            features=dict(features or {}))
        self._next_id += 1
        self.open_.append(c)
        return c

    def advance(self, *, book: Mapping[str, Any], t: float,
                symbol: Optional[str] = None) -> int:
        """Advance only candidates belonging to THIS contract.

        WITHOUT THE SYMBOL FILTER this silently marks a candidate opened on
        contract A against contract B's book once the lane rolls over. Those
        are different instruments with different strikes; the markout would be
        arithmetic between unrelated prices and would look perfectly plausible.
        `symbol=None` is accepted only for single-contract tests.
        """
        n = 0
        for c in list(self.open_):
            if symbol is not None and c.symbol != symbol:
                continue
            c.observe(book=book, t=t)
            n += 1
            if c.closed:
                self._retire(c)
        return n

    def settle_all(self, *, leg: Optional[str] = None,
                   symbol: Optional[str] = None) -> int:
        """Close out candidates, by contract. Called at rollover.

        A candidate still open when its contract ends must be closed HERE: the
        next window is a different instrument and following it there would be
        the same cross-contract error `advance` guards against.
        """
        n = 0
        for c in list(self.open_):
            if symbol is not None and c.symbol != symbol:
                continue
            c.settle(leg)
            self._retire(c)
            n += 1
        return n

    def _retire(self, c: Candidate) -> None:
        try:
            self.open_.remove(c)
        except ValueError:
            return
        self.labels.append(c.label())
        if len(self.labels) > self.max_labels:
            self.labels = self.labels[-(self.max_labels // 2):]

    # ------------------------------------------------------------ report
    def report(self, *, min_cell: int = 10) -> Dict[str, Any]:
        from src.gemini_execution.econ import spread_census as SC
        rows = self.labels
        if not rows:
            return {"schema": SCHEMA, "labelled": 0, "open": len(self.open_),
                    "labels": list(LABELS)}

        def summarise(rs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
            def med(xs):
                return statistics.median(xs) if xs else None
            out: Dict[str, Any] = {"n": len(rs)}
            if not rs:
                # an EMPTY segment is not a segment of zeros. Reporting 0.0
                # for "opposite available share" on a cell holding nothing
                # would read as a measured absence.
                out["sufficient"] = False
                out["empty"] = True
                return out
            for h in HORIZONS_S:
                xs = [r["markout_ticks_by_horizon_s"].get(str(h))
                      for r in rs]
                xs = [x for x in xs if x is not None]
                out["markout_p50_%ds" % h] = med(xs)
                out["adverse_share_%ds" % h] = (
                    (sum(1 for x in xs if x < 0) / len(xs)) if xs else None)
            opp = [r for r in rs if r["opposite_available"]]
            out["opposite_available_share"] = len(opp) / len(rs)
            out["median_time_to_opposite_s"] = med(
                [r["time_to_opposite_s"] for r in opp
                 if r["time_to_opposite_s"] is not None])
            bases = [float(r["best_pair_basis"]) for r in rs
                     if r["best_pair_basis"] is not None]
            out["best_pair_basis_p50"] = med(bases)
            out["best_pair_below_one_share"] = (
                (sum(1 for b in bases if b <= 1.0) / len(bases))
                if bases else None)
            out["max_adverse_markout_p50_ticks"] = med(
                [r["max_adverse_markout_ticks"] for r in rs])
            sv = [float(r["settlement_value_per_contract"]) for r in rs
                  if r["settlement_value_per_contract"] is not None]
            out["settlement_value_p50"] = med(sv)
            out["settled_n"] = len(sv)
            out["sufficient"] = len(rs) >= min_cell
            return out

        seg: Dict[str, List[Mapping[str, Any]]] = {}
        for r in rows:
            b = SC._bucket(r.get("tte_s"), SC.TTE_EDGES, SC.TTE_LABELS)
            seg.setdefault("%s|%s|%s" % (r["cohort"], b, r["leg"]), []).append(r)

        by_blocker: Dict[str, List[Mapping[str, Any]]] = {}
        for r in rows:
            key = r["marginal_blocker"] or (
                "ADMITTED" if r["admitted"] else "MULTI_BLOCKED")
            by_blocker.setdefault(key, []).append(r)

        return {
            "schema": SCHEMA,
            "labelled": len(rows), "open": len(self.open_),
            "min_cell": min_cell,
            "overall": summarise(rows),
            "admitted": summarise([r for r in rows if r["admitted"]]),
            "blocked": summarise([r for r in rows if not r["admitted"]]),
            "by_marginal_blocker": {k: summarise(v)
                                    for k, v in sorted(by_blocker.items())},
            "by_cohort_tte_leg": {k: summarise(v)
                                  for k, v in sorted(seg.items())},
            "horizons_s": list(HORIZONS_S),
            "labels": list(LABELS),
        }
