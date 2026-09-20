"""SPREAD AS A DISTRIBUTION, before it is a threshold.

WHY THIS EXISTS. `max_spread` was preregistered at 0.08 for BTC5m from a SINGLE
observed book (0.60/0.65 = 0.05). The first live windows showed spreads of
0.30, and the gate refused 36 of 50 entries on it. That is a preregistration
error of ours, not a market finding -- the bound was set from an anecdote.

The response is NOT to move the number. Moving it now, having just watched it
bind, would fit a threshold to the window that exposed it. The response is to
MEASURE the distribution prospectively, conditional on the things that plausibly
govern it, and preregister a treatment against that evidence afterwards.

WHAT IT IS CONDITIONED ON, and why each. A single pooled median would repeat the
original mistake at larger n -- the whole lesson of the sports work on this
branch was that a venue-wide average destroyed real heterogeneity.

    cohort        BTC5m and BTC15m are different instruments, not one clock
    tte_bucket    spreads widen as a window empties; a bound blind to TTE
                  measures mostly which part of the window we sampled
    price_bucket  fees are convex in price and so is quoting behaviour; a
                  0.10 spread at p=0.5 is not the same object as at p=0.05
    flow_state    quiet vs active tape, from the SOCKET (the polled tape is
                  EVIDENCE ONLY and may not be decided on -- but a census is
                  an observation, not a decision, so both are recordable and
                  the source is stamped per row)

WHAT IS RECORDED BESIDE THE SPREAD, so the eventual treatment can be economic
rather than a boolean. The brief's own direction: "longer term I expect spread
to become an economic input rather than a universal spread <= X boolean gate."
So every observation also carries what happened NEXT -- the touch at several
horizons -- which is what turns "the spread was 0.30" into "a 0.30 spread at
this TTE was followed by the touch moving N ticks and a print arriving or not".

NOTHING HERE GATES ANYTHING. This module has no admission rule, no threshold
and no default. It counts.
"""
from __future__ import annotations

import json
import os
import statistics
from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "gemini_econ_spread_census/v1"

#: PREREGISTERED buckets. Fixed before the sample exists so the eventual
#: treatment cannot be chosen by re-cutting the same data until it agrees.
TTE_EDGES: Tuple[float, ...] = (60.0, 120.0, 300.0, 600.0)
TTE_LABELS = ("<=60", "61-120", "121-300", "301-600", ">600")

PRICE_EDGES: Tuple[float, ...] = (0.10, 0.25, 0.45, 0.55, 0.75, 0.90)
PRICE_LABELS = ("<=0.10", "0.11-0.25", "0.26-0.45", "0.46-0.55",
                "0.56-0.75", "0.76-0.90", ">0.90")

#: prints per minute over the flow window
FLOW_EDGES: Tuple[float, ...] = (0.0, 2.0, 10.0)
FLOW_LABELS = ("silent", "thin", "active", "busy")

#: horizons at which the touch is revisited, in seconds
MARKOUT_HORIZONS_S: Tuple[int, ...] = (8, 30, 60)

LABELS = (
    "A CENSUS, not a gate. No threshold, no admission rule, no default.",
    "Conditioned rather than pooled: a venue-wide median is how heterogeneity "
    "gets destroyed, which this branch already learned once on sports.",
)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _bucket(v: Any, edges: Sequence[float], labels: Sequence[str]) -> str:
    if v is None:
        return "unknown"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "unknown"
    for i, e in enumerate(edges):
        if x <= e:
            return labels[i]
    return labels[-1]


def observe(*, cohort: str, tte_s: Any, book: Mapping[str, Any],
            prints_per_min: Any = None, flow_source: str = "GEMINI_WS",
            t: Any = None) -> Optional[Dict[str, Any]]:
    """One spread observation, or None when the book cannot supply one."""
    yes = (book or {}).get("yes") or {}
    bid, ask = _dec(yes.get("bid")), _dec(yes.get("ask"))
    if bid is None or ask is None or ask < bid:
        return None
    mid = (bid + ask) / D("2")
    return {
        "schema": SCHEMA, "cohort": cohort, "t": t,
        "tte_s": None if tte_s is None else float(tte_s),
        "bid": str(bid), "ask": str(ask), "mid": str(mid),
        "spread": str(ask - bid),
        "spread_bps_of_mid": (float((ask - bid) / mid) if mid > 0 else None),
        "tte_bucket": _bucket(tte_s, TTE_EDGES, TTE_LABELS),
        "price_bucket": _bucket(mid, PRICE_EDGES, PRICE_LABELS),
        "flow_bucket": _bucket(prints_per_min, FLOW_EDGES, FLOW_LABELS),
        "prints_per_min": (None if prints_per_min is None
                           else float(prints_per_min)),
        "flow_source": flow_source,
    }


def cell_of(row: Mapping[str, Any]) -> str:
    return "|".join((str(row.get("cohort")), str(row.get("tte_bucket")),
                     str(row.get("price_bucket")), str(row.get("flow_bucket"))))


@dataclass
class SpreadCensus:
    """Per-cell spread distribution plus what the touch did next.

    Pending observations are held until their horizon elapses and only then
    scored, so a markout can never be computed from a future the row has not
    reached.
    """

    #: below this a cell is arithmetic rather than evidence
    min_cell: int = 30
    cells: Dict[str, List[float]] = field(default_factory=dict)
    #: cell -> horizon -> list of |touch movement| in ticks
    markouts: Dict[str, Dict[str, List[float]]] = field(default_factory=dict)
    _pending: List[Dict[str, Any]] = field(default_factory=list)
    observations: int = 0

    def record(self, row: Optional[Mapping[str, Any]], *,
               tick: Any = "0.01") -> Optional[str]:
        if not row:
            return None
        cell = cell_of(row)
        try:
            self.cells.setdefault(cell, []).append(float(row["spread"]))
        except (KeyError, TypeError, ValueError):
            return None
        self.observations += 1
        self._pending.append({"cell": cell, "t": row.get("t"),
                              "bid": row.get("bid"), "tick": str(tick),
                              "done": []})
        if len(self._pending) > 4000:
            self._pending = self._pending[-2000:]
        return cell

    def advance(self, *, now: Any, bid: Any) -> None:
        """Score any pending observation whose horizon has now elapsed.

        Called with the CURRENT touch. A pending row is scored once per
        horizon and never re-scored, so one long-lived row cannot dominate.
        """
        b = _dec(bid)
        if b is None or now is None:
            return
        for p in self._pending:
            t0 = p.get("t")
            if t0 is None:
                continue
            age = float(now) - float(t0)
            tick = _dec(p.get("tick")) or D("0.01")
            b0 = _dec(p.get("bid"))
            if b0 is None or tick <= 0:
                continue
            for h in MARKOUT_HORIZONS_S:
                if h in p["done"] or age < h:
                    continue
                p["done"].append(h)
                moved = float(abs(b - b0) / tick)
                self.markouts.setdefault(p["cell"], {}).setdefault(
                    str(h), []).append(moved)

    def report(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "schema": SCHEMA, "observations": self.observations,
            "min_cell": self.min_cell, "labels": list(LABELS), "cells": {}}
        for cell, xs in sorted(self.cells.items()):
            if not xs:
                continue
            s = sorted(xs)
            n = len(s)
            row: Dict[str, Any] = {
                "n": n,
                "sufficient": n >= self.min_cell,
                "median": statistics.median(s),
                "p10": s[max(0, int(0.10 * (n - 1)))],
                "p90": s[max(0, int(0.90 * (n - 1)))],
                "min": s[0], "max": s[-1],
            }
            mk = self.markouts.get(cell) or {}
            row["median_touch_move_ticks_by_horizon_s"] = {
                h: (statistics.median(v) if v else None)
                for h, v in sorted(mk.items())}
            out["cells"][cell] = row
        out["cells_sufficient"] = sum(1 for r in out["cells"].values()
                                      if r["sufficient"])
        return out

    # -- persistence so a 24/7 collector accumulates across restarts -------
    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"schema": SCHEMA, "min_cell": self.min_cell,
                       "cells": self.cells, "markouts": self.markouts,
                       "observations": self.observations}, fh)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "SpreadCensus":
        try:
            with open(path) as fh:
                d = json.load(fh)
            return cls(min_cell=int(d.get("min_cell") or 30),
                       cells={k: list(v) for k, v in (d.get("cells") or {}).items()},
                       markouts={k: {h: list(x) for h, x in v.items()}
                                 for k, v in (d.get("markouts") or {}).items()},
                       observations=int(d.get("observations") or 0))
        except (OSError, ValueError, TypeError):
            return cls()
