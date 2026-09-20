"""P(opposite ECONOMICALLY ACCEPTABLE maker fill within remaining TTE | state).

WHAT THIS DELIBERATELY DOES NOT DO. It does not ship a fitted model. We have no
labelled prospective sample yet -- `hazard_report.py` enumerates 22 candidate
features over every residual state the replay passed through and fits nothing,
on purpose, because the sample could not support it. Shipping coefficients now
would mean fitting them to the handful of windows we happen to hold, which is
the one thing the brief rules out ("do not fit thresholds to one favorable
window").

So this module does three things instead, in this order:

  1. EXTRACTS the features causally, from data available at the decision
     instant and nothing later;
  2. RECORDS each state with the outcome that later resolves it, building the
     labelled sample that does not exist yet;
  3. ESTIMATES only once a cell holds at least `MIN_CELL` resolved
     observations, and otherwise returns UNCALIBRATED.

UNCALIBRATED IS NOT 0.5, AND IT IS NOT 0. An estimator that returns a number
before it has evidence hands the caller a confidence it has not earned, and a
caller that reads 0.5 as "coin flip" will size against it. `estimate` returns
`p: None` with a reason, and `admits_waiting` FAILS CLOSED on that -- an
uncalibrated hazard may never be the thing that justifies continuing to wait.

THE LABEL IS ECONOMIC, NOT MECHANICAL. The event being predicted is not "did
any opposite print arrive". It is "did an opposite print arrive at or below the
price the veto would actually accept, while time remained". A model trained on
mechanical fills would be most confident exactly where completion is most
expensive, which is precisely backwards.

BUCKETING IS PREREGISTERED. The cell edges below are fixed here, before any
prospective sample exists, and are not to be re-chosen from the data they will
be used to score. Widening a cell after seeing its contents is refitting.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "gemini_econ_opposite_fill_hazard/v1"

#: a cell below this many RESOLVED observations yields no estimate at all
MIN_CELL = 30

#: PREREGISTERED cell edges. Fixed before the sample exists.
TTE_EDGES: Tuple[float, ...] = (30.0, 90.0, 300.0, 600.0)
TTE_LABELS = ("<=30", "31-90", "91-300", "301-600", ">600")

#: how far the required complement price sits below the complement's own bid,
#: in ticks. This is the causal driver: a completion that needs the market to
#: come to us is a different event from one already quoted.
GAP_EDGES: Tuple[int, ...] = (0, 1, 3, 7)
GAP_LABELS = ("at_or_through", "1_tick", "2-3_ticks", "4-7_ticks", ">7_ticks")

#: alternation of aggressor direction in the recent tape
ALT_EDGES: Tuple[float, ...] = (0.1, 0.3, 0.5)
ALT_LABELS = ("one_sided", "low_alt", "mixed", "two_sided")

FEATURES = (
    "tte_s", "time_since_first_fill_s", "held_leg_price",
    "required_complement_price", "complement_bid", "required_gap_ticks",
    "side_alternation", "same_side_run_len", "prints_per_min",
    "qty_per_min", "largest_print_share", "two_sided_depth",
    "touch_movement_ticks", "spread", "book_imbalance",
    "reference_velocity_per_min",
)

LABELS = (
    "UNCALIBRATED is not 0.5 and is not 0. An estimate is withheld until its "
    "cell holds MIN_CELL resolved observations, and a withheld estimate may "
    "never justify continuing to wait.",
    "The predicted event is an opposite fill AT AN ECONOMICALLY ACCEPTABLE "
    "PRICE, not any opposite print.",
)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _bucket(value: Any, edges: Sequence[float], labels: Sequence[str]) -> str:
    if value is None:
        return "unknown"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    for i, e in enumerate(edges):
        if v <= e:
            return labels[i]
    return labels[-1]


def features(*, tte_s: Any, held_leg: str, held_leg_price: Any,
             required_complement_price: Any, book: Mapping[str, Any],
             prints: Sequence[Mapping[str, Any]],
             window_s: float = 120.0,
             time_since_first_fill_s: Any = None,
             same_side_run_len: Any = None,
             reference_velocity_per_min: Any = None,
             touch_movement_ticks: Any = None,
             tick: Any = "0.01") -> Dict[str, Any]:
    """Every feature, computed from THIS instant's data and nothing later.

    `prints` must already be filtered to the trailing `window_s`; this function
    does not look at a clock, so it cannot accidentally include a print from
    after the decision it is describing.
    """
    tk = _dec(tick) or D("0.01")
    comp = "NO" if str(held_leg).upper() == "YES" else "YES"
    comp_leg = (book or {}).get(comp.lower()) or {}
    held_side = (book or {}).get(str(held_leg).lower()) or {}
    comp_bid = _dec(comp_leg.get("bid"))
    comp_ask = _dec(comp_leg.get("ask"))
    req = _dec(required_complement_price)

    gap_ticks = None
    if req is not None and comp_bid is not None and tk > 0:
        # positive means the market must come DOWN to us before we fill
        gap_ticks = int((comp_bid - req) / tk)

    rows = list(prints or [])
    n = len(rows)
    sides = [str(r.get("side") or r.get("type") or "").lower() for r in rows]
    known = [s for s in sides if s in ("buy", "sell")]
    alternations = sum(1 for a, b in zip(known, known[1:]) if a != b)
    alt = (alternations / (len(known) - 1)) if len(known) > 1 else None

    qtys = []
    for r in rows:
        q = _dec(r.get("qty") or r.get("amount"))
        if q is not None:
            qtys.append(q)
    total_qty = sum(qtys) if qtys else D("0")
    largest = max(qtys) if qtys else None

    mins = (window_s / 60.0) if window_s else None
    bid, ask = _dec(held_side.get("bid")), _dec(held_side.get("ask"))
    spread = (ask - bid) if (ask is not None and bid is not None) else None

    # TWO-SIDED means BOTH OUTCOMES ARE QUOTED ON BOTH SIDES, read from the
    # prices the contract publishes. It is deliberately NOT a depth test:
    # `metadata.contract_depth` is a separate public read carrying the label
    # "EVIDENCE ONLY -- no caller may price, size or gate anything on it",
    # and the contract object itself publishes no depth at all. Gating on
    # depth would either break that invariant or relabel an ask size as a
    # "NO bid size", which is the substitution this program refuses.
    two_sided = all(v is not None and D("0") < v < D("1") for v in
                    (comp_bid, comp_ask,
                     _dec(held_side.get("bid")), _dec(held_side.get("ask"))))

    # microprice imbalance, from PRICES. Positive means the held leg sits
    # nearer its ask than the complement does -- a price-space lean, never a
    # size-space one.
    imbalance = None
    hb, ha = _dec(held_side.get("bid")), _dec(held_side.get("ask"))
    if None not in (hb, ha) and ha > hb:
        imbalance = float(((hb + ha) / D("2")) - hb) / float(ha - hb)

    # depth, when the caller supplies it, travels as EVIDENCE and nothing
    # gates on it.
    depth_c = _dec(comp_leg.get("bid_size") or comp_leg.get("size"))
    depth_h = _dec(held_side.get("bid_size") or held_side.get("size"))

    return {
        "tte_s": None if tte_s is None else float(tte_s),
        "time_since_first_fill_s": (None if time_since_first_fill_s is None
                                    else float(time_since_first_fill_s)),
        "held_leg": str(held_leg).upper(),
        "held_leg_price": (None if _dec(held_leg_price) is None
                           else str(_dec(held_leg_price))),
        "required_complement_price": None if req is None else str(req),
        "complement_bid": None if comp_bid is None else str(comp_bid),
        "complement_ask": None if comp_ask is None else str(comp_ask),
        "required_gap_ticks": gap_ticks,
        "side_alternation": alt,
        "same_side_run_len": (None if same_side_run_len is None
                              else int(same_side_run_len)),
        "prints_per_min": (n / mins) if mins else None,
        "qty_per_min": (float(total_qty) / mins) if mins else None,
        "largest_print_share": (float(largest / total_qty)
                                if (largest is not None and total_qty > 0)
                                else None),
        "two_sided_quote": two_sided,
        "depth_evidence_only": {
            "held": None if depth_h is None else str(depth_h),
            "complement": None if depth_c is None else str(depth_c),
            "label": ("EVIDENCE ONLY. metadata.contract_depth forbids gating "
                      "on it and the contract publishes no depth itself.")},
        "touch_movement_ticks": (None if touch_movement_ticks is None
                                 else int(touch_movement_ticks)),
        "spread": None if spread is None else str(spread),
        "book_imbalance": imbalance,
        "reference_velocity_per_min": (
            None if reference_velocity_per_min is None
            else float(reference_velocity_per_min)),
        "window_s": window_s,
        "n_prints": n,
    }


def cell_of(feat: Mapping[str, Any]) -> str:
    """The preregistered cell this state falls in.

    Three dimensions only. A finer partition would look more precise and would
    reach MIN_CELL on nothing.
    """
    return "|".join((
        _bucket(feat.get("tte_s"), TTE_EDGES, TTE_LABELS),
        _bucket(feat.get("required_gap_ticks"), GAP_EDGES, GAP_LABELS),
        _bucket(feat.get("side_alternation"), ALT_EDGES, ALT_LABELS),
    ))


@dataclass
class HazardEstimator:
    """Empirical frequency per preregistered cell, with a hard evidence floor.

    Not a model. A counter with a rule about when it is allowed to speak.
    """

    min_cell: int = MIN_CELL
    cells: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def observe(self, feat: Mapping[str, Any], *, resolved: bool,
                filled_acceptably: bool) -> str:
        """Record one state and how it turned out.

        `resolved` is False while the state's outcome is still unknown -- an
        unresolved state is counted NOWHERE, because counting it as a failure
        would make every estimate a function of how recently we looked.
        """
        cell = cell_of(feat)
        if not resolved:
            return cell
        c = self.cells.setdefault(cell, {"n": 0, "k": 0})
        c["n"] += 1
        if filled_acceptably:
            c["k"] += 1
        return cell

    def estimate(self, feat: Mapping[str, Any]) -> Dict[str, Any]:
        cell = cell_of(feat)
        c = self.cells.get(cell) or {"n": 0, "k": 0}
        base = {"schema": SCHEMA, "cell": cell, "n": c["n"], "k": c["k"],
                "min_cell": self.min_cell, "labels": list(LABELS)}
        if c["n"] < self.min_cell:
            base.update(p=None, calibrated=False,
                        why=("UNCALIBRATED: cell holds %d resolved "
                             "observations, below the preregistered floor of "
                             "%d" % (c["n"], self.min_cell)))
            return base
        base.update(p=(c["k"] / c["n"]), calibrated=True, why=None)
        return base

    # -- persistence, so a 24/7 collector accumulates across restarts ------
    def to_json(self) -> str:
        return json.dumps({"schema": SCHEMA, "min_cell": self.min_cell,
                           "cells": self.cells}, sort_keys=True)

    @classmethod
    def from_json(cls, blob: str) -> "HazardEstimator":
        d = json.loads(blob)
        return cls(min_cell=int(d.get("min_cell") or MIN_CELL),
                   cells=dict(d.get("cells") or {}))

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(self.to_json())
        os.replace(tmp, path)          # atomic; a torn file is not a sample

    @classmethod
    def load(cls, path: str) -> "HazardEstimator":
        try:
            with open(path) as fh:
                return cls.from_json(fh.read())
        except (OSError, ValueError):
            return cls()


def admits_waiting(est: Mapping[str, Any], *, threshold: float) -> Dict[str, Any]:
    """May we keep waiting for a maker completion on this hazard?

    FAILS CLOSED on an uncalibrated estimate. The asymmetry is deliberate:
    waiting is the action that accumulates risk while producing nothing, so it
    is the action that must justify itself.
    """
    if not est.get("calibrated"):
        return {"wait": False, "why": est.get("why") or "UNCALIBRATED",
                "calibrated": False,
                "note": ("an uncalibrated hazard NEVER justifies waiting; the "
                         "state is recorded so the cell can calibrate")}
    p = est.get("p")
    return {"wait": bool(p is not None and p >= threshold),
            "calibrated": True, "p": p, "threshold": threshold,
            "why": None if (p is not None and p >= threshold)
            else "p %.4f below threshold %.4f" % (p or 0.0, threshold)}
