"""DURATIONS ON ONE ENGINE. BTC15m and BTC5m are the same executor.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT. This registry carries the
things that genuinely differ between a 15-minute and a 5-minute contract --
the ticker pattern, the window length, where that duration's datasets and
cohorts live -- so one executor can run either. It carries NO decision
rule. The frozen policy still decides every side, price, size and moment.

THE FROZEN POLICY DOES APPLY TO BTC5m, AND IT IS NOT A DEAD END.
An earlier note here claimed the 5m lane was unusable because the frozen
upper bound 840s "exceeds the whole contract". That was too strong, and it
was wrong. Intersecting the frozen band with a 300-second contract leaves a
real eligible interval:

    frozen [120, 840] INTERSECT window [0, 300]  ->  EFFECTIVE [120, 300]

i.e. openable from 120s to 300s of time-to-expiry, which is roughly the
first three minutes of a five-minute window. So BTC5m CAN be run on the
frozen policy exactly as it stands, and `effective_open_band()` reports
that interval rather than refusing.

WHAT WOULD ACTUALLY BE WRONG is pretending that is an apples-to-apples
comparison with BTC15m, or quietly editing the threshold. On 15m the band
covers 720 of 900 seconds and sits clear of both ends; on 5m it covers 180
of 300 seconds and is CLIPPED by the contract's own start. Those are
different operating regimes wearing one pair of numbers, so:

  * `AS_FROZEN` is the comparability baseline: the frozen rule, unmodified,
    with its effective interval stated.
  * a duration-normalized band is a SEPARATE, LABELLED HYPOTHESIS. It is
    not inherited, not applied by default, and it never touches BTC15m.

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


BASELINE_AS_FROZEN = "AS_FROZEN"
HYPOTHESIS_NORMALIZED = "DURATION_NORMALIZED_HYPOTHESIS"


def effective_open_band(name: str) -> Dict[str, Any]:
    """The frozen band INTERSECTED with this duration's window.

    This is the comparability baseline: the frozen rule unmodified, with
    the interval it actually produces on this contract length stated
    rather than assumed.
    """
    d = of(name)
    lo = max(FROZEN_OPEN_TTE_LO, 0.0)
    hi = min(FROZEN_OPEN_TTE_HI, d["window_s"])
    if lo >= hi:
        return {"name": d["name"], "basis": BASELINE_AS_FROZEN,
                "empty": True, "band": None,
                "why": ("the frozen band and a %.0fs window do not overlap"
                        % d["window_s"])}
    clipped = FROZEN_OPEN_TTE_HI > d["window_s"]
    return {"name": d["name"], "basis": BASELINE_AS_FROZEN, "empty": False,
            "band": (lo, hi), "window_s": d["window_s"],
            "seconds_eligible": hi - lo,
            "fraction_of_window": (hi - lo) / d["window_s"],
            "clipped_by_contract_start": clipped,
            "frozen_band": (FROZEN_OPEN_TTE_LO, FROZEN_OPEN_TTE_HI),
            "comparability": (
                "CLIPPED. The frozen upper bound %.0fs exceeds this %.0fs "
                "window, so the effective interval is bounded by the "
                "contract itself. The rule is unmodified and runnable; it "
                "is NOT an apples-to-apples regime against BTC15m, where "
                "the band sits clear of both ends."
                % (FROZEN_OPEN_TTE_HI, d["window_s"]) if clipped else
                "UNCLIPPED. The frozen band sits inside this window.")}


def ready_for_live(name: str) -> Dict[str, Any]:
    """May this duration be run for real?

    A duration is runnable on the frozen policy whenever the effective
    band is non-empty. `band_authorized` records whether an owner has
    authorized LIVE money on it -- which is a separate question from
    whether the rule is well defined, and is what gates execution.
    """
    d = of(name)
    eff = effective_open_band(name)
    if eff["empty"]:
        return {"ok": False, "duration": d["name"], "effective_band": eff,
                "why": eff["why"]}
    return {"ok": bool(d["band_authorized"]), "duration": d["name"],
            "effective_band": eff["band"],
            "runnable_on_frozen_policy": True,
            "live_authorized": bool(d["band_authorized"]),
            "window_s": d["window_s"],
            "cohort_root": d["cohort_root"],
            "dataset_root": d["dataset_root"],
            "why": (None if d["band_authorized"] else
                    ("the frozen rule is well defined here (effective "
                     "%.0f..%.0fs) and may be replayed and shadowed. LIVE "
                     "money on %s is not authorized yet, which is an "
                     "owner's decision and not a property of the rule."
                     % (eff["band"][0], eff["band"][1], d["name"]))),
            "comparability": eff["comparability"]}


def normalized_hypothesis(name: str) -> Dict[str, Any]:
    """A duration-normalized OPEN band, AS A LABELLED HYPOTHESIS.

    It scales the frozen 15m band by the ratio of window lengths, which is
    one defensible normalization among several. IT IS NOT THE FROZEN RULE,
    it is never applied by default, and it never touches BTC15m: the 15m
    policy is frozen and this function returns its own band unchanged for
    that duration precisely so a caller cannot use it to move it.
    """
    d = of(name)
    ratio = d["window_s"] / DURATIONS[BTC15M]["window_s"]
    band = (FROZEN_OPEN_TTE_LO * ratio, FROZEN_OPEN_TTE_HI * ratio)
    return {"name": d["name"], "basis": HYPOTHESIS_NORMALIZED,
            "band": band, "window_s": d["window_s"], "ratio": ratio,
            "frozen_band": (FROZEN_OPEN_TTE_LO, FROZEN_OPEN_TTE_HI),
            "is_frozen_policy": False,
            "labels": ("A HYPOTHESIS, not the frozen policy. It must be "
                       "tested against the AS_FROZEN baseline on the SAME "
                       "data before it earns any capital, and its results "
                       "are never pooled with BTC15m's."),
            "warning": ("applying this to BTC15m would CHANGE THE FROZEN "
                        "POLICY and is not permitted"
                        if d["name"] == BTC15M else None)}


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
