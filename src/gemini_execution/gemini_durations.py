"""DURATIONS ON ONE ENGINE. BTC15m and BTC5m are the same executor.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT. This registry carries the
things that genuinely differ between a 15-minute and a 5-minute contract --
the ticker pattern, the window length, where that duration's datasets and
cohorts live -- so one executor can run either. It carries NO decision
rule. The frozen policy still decides every side, price, size and moment.

THE 5m OPEN BAND IS NOT SET HERE, AND THAT IS THE POINT.
The frozen policy's band is

    OPEN_TTE_LO, OPEN_TTE_HI = 120.0, 840.0

which was fitted to a 900-second window. A BTC5m window is 300 seconds
TOTAL, so 840 exceeds the entire contract: reusing the 15m band on 5m would
mean "openable from the first second to the 180th", a materially different
policy wearing the same numbers. Choosing 5m bounds is a THRESHOLD
DECISION, and thresholds are explicitly not being optimized yet.

So BTC5m is plumbed and REFUSED AT THE GATE until an owner authorizes a
band for it. `ready_for_live()` returns False with that reason, and nothing
in this module invents a number. Failing closed is the whole habit of this
codebase: a silent reuse of 15m thresholds on 5m would be a new strategy
pretending to be a validated one.

DATASETS STAY SEPARATE. Each duration gets its own cohort root and its own
dataset directory, so 5m and 15m evidence can never be pooled by accident.
Pooling them would make a $/window number meaningless -- the commercial
metric is per window, and the windows are not the same length.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

BTC15M = "BTC15M"
BTC5M = "BTC5M"

#: the frozen policy's OPEN band, restated here ONLY to be compared
#: against a duration's window length. It is never redefined.
FROZEN_OPEN_TTE_LO, FROZEN_OPEN_TTE_HI = 120.0, 840.0

DURATIONS: Dict[str, Dict[str, Any]] = {
    BTC15M: {
        "name": BTC15M,
        "pattern": re.compile(r"^BTC15M\d{10}$"),
        "window_s": 900.0,
        # The frozen band was fitted to THIS duration and is authorized for
        # it: 120..840 sits inside a 900s window with room at both ends.
        "open_band": (FROZEN_OPEN_TTE_LO, FROZEN_OPEN_TTE_HI),
        "band_authorized": True,
        "cohort_root": os.path.join("analysis", "gemini", "cohorts"),
        "dataset_root": os.path.join("analysis", "gemini", "info_edge",
                                     "btc15m"),
    },
    BTC5M: {
        "name": BTC5M,
        "pattern": re.compile(r"^BTC5M\d{10}$"),
        "window_s": 300.0,
        # NOT SET. See the module docstring: 840 > 300, so the 15m band
        # cannot be carried over without changing the policy's meaning.
        "open_band": None,
        "band_authorized": False,
        "cohort_root": os.path.join("analysis", "gemini", "cohorts_btc5m"),
        "dataset_root": os.path.join("analysis", "gemini", "info_edge",
                                     "btc5m"),
    },
}


def of(name: str) -> Dict[str, Any]:
    key = str(name or "").strip().upper()
    if key not in DURATIONS:
        raise KeyError("unknown duration %r; known: %s"
                       % (name, ", ".join(sorted(DURATIONS))))
    return DURATIONS[key]


def matches(name: str, ticker: str) -> bool:
    return bool(of(name)["pattern"].match(str(ticker or "")))


def cohort_root(name: str) -> str:
    return of(name)["cohort_root"]


def dataset_root(name: str) -> str:
    return of(name)["dataset_root"]


def roots_are_separate() -> bool:
    """5m and 15m evidence must never share a directory."""
    roots = [(d["cohort_root"], d["dataset_root"])
             for d in DURATIONS.values()]
    flat = [p for pair in roots for p in pair]
    return len(set(flat)) == len(flat)


def ready_for_live(name: str) -> Dict[str, Any]:
    """May this duration be run for real yet?

    A duration whose OPEN band is not authorized is REFUSED here rather
    than run on borrowed numbers.
    """
    d = of(name)
    band = d["open_band"]
    if not d["band_authorized"] or band is None:
        return {"ok": False, "duration": d["name"],
                "why": ("the OPEN band for %s is not authorized. The frozen "
                        "band is %.0f..%.0fs, fitted to a 900s window, and "
                        "this duration's window is %.0fs -- so the upper "
                        "bound %s the whole contract. Choosing bounds for "
                        "%s is a threshold decision and needs an explicit "
                        "authorization; it is not inherited."
                        % (d["name"], FROZEN_OPEN_TTE_LO,
                           FROZEN_OPEN_TTE_HI, d["window_s"],
                           ("exceeds" if FROZEN_OPEN_TTE_HI > d["window_s"]
                            else "fits inside"), d["name"]))}
    lo, hi = band
    if hi > d["window_s"]:
        return {"ok": False, "duration": d["name"],
                "why": ("the authorized band's upper bound %.0fs exceeds "
                        "the %.0fs window" % (hi, d["window_s"]))}
    return {"ok": True, "duration": d["name"], "open_band": band,
            "window_s": d["window_s"], "cohort_root": d["cohort_root"],
            "dataset_root": d["dataset_root"]}


def summary() -> Dict[str, Any]:
    return {"durations": {k: {"window_s": v["window_s"],
                              "open_band": v["open_band"],
                              "band_authorized": v["band_authorized"],
                              "cohort_root": v["cohort_root"],
                              "dataset_root": v["dataset_root"],
                              "ready": ready_for_live(k)["ok"]}
                          for k, v in DURATIONS.items()},
            "roots_are_separate": roots_are_separate(),
            "labels": ("ONE engine, one frozen policy. This registry "
                       "carries durations and dataset locations, never a "
                       "decision rule.")}
