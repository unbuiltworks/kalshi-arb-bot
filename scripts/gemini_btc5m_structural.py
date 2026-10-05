"""Expanded-evidence STRUCTURAL DESCRIPTIVES for Gemini BTC5m.

DESCRIPTIVE ONLY. No policy is fitted, no threshold swept, no candidate
proposed. Quantities are reported as distributions over independent
windows, never as a selected best case.

EXCLUSIONS, applied by name before anything is computed:
  * the 6 OOS windows sealed by the first-leg study -- their
    complement timing IS that study's target variable, so they are
    excluded from every measure here, not merely from the outcomes;
  * windows touched by live probes (PROBE_CONTAMINATED).

The first-leg definitions are FIXED and pre-existing, not chosen here:
  UNDERDOG  -- cheaper executable q1 side at the first eligible instant
  FAVORED   -- the dearer side at the same instant
Both are evaluated; neither is selected on its result.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

sys.path.insert(0, "scripts")
from gemini_complement_forensic import (  # noqa: E402
    REACTION_LATENCY_S, SIZES, fee, load)

LABEL = "GEMINI_BTC5M_STRUCTURAL_EXPANDED_V1"
SEALED = {"BTC05M2610051845", "BTC05M2610051850", "BTC05M2610051855",
          "BTC05M2610051900", "BTC05M2610051905", "BTC05M2610051910"}
MIN_T_PLUS_S = 15.0


def q(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return round(s[i], 4)


def dist(xs: List[float], unit: str = "") -> Dict[str, Any]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "unit": unit, "min": q(xs, 0), "p10": q(xs, .1),
            "median": q(xs, .5), "p90": q(xs, .9), "max": q(xs, 1.0),
            "mean": round(statistics.fmean(xs), 4)}


def first_leg(series, t_open: float, which: str) -> Optional[Dict[str, Any]]:
    for row in series:
        if row["t"] - t_open < MIN_T_PLUS_S:
            continue
        up, dn = row.get("UP_q1"), row.get("DOWN_q1")
        if up is None or dn is None or up == dn:
            continue
        cheap = "UP" if up < dn else "DOWN"
        side = cheap if which == "UNDERDOG" else (
            "DOWN" if cheap == "UP" else "UP")
        px = up if side == "UP" else dn
        return {"side": side, "price": px, "t": row["t"],
                "t_plus_s": row["t"] - t_open}
    return None


def complement(series, fl, size: int) -> Dict[str, Any]:
    other = "DOWN" if fl["side"] == "UP" else "UP"
    basis = fl["price"] + fee(fl["price"])
    key = "%s_q%d" % (other, size)
    first_t, best, open_s, episodes = None, None, 0.0, 0
    prev_ok, prev_t = False, None
    for r in series:
        if r["t"] < fl["t"] + REACTION_LATENCY_S:
            continue
        c = r.get(key)
        ok = False
        if c is not None:
            lock = basis + c + fee(c)
            if lock < D("1"):
                ok = True
                if first_t is None:
                    first_t = r["t"]
                if best is None or lock < best:
                    best = lock
        if ok:
            if not prev_ok:
                episodes += 1
            elif prev_t is not None:
                open_s += r["t"] - prev_t
        prev_ok, prev_t = ok, r["t"]
    return {"reached": first_t is not None,
            "seconds_to_first": (round(first_t - fl["t"], 2)
                                 if first_t else None),
            "open_seconds": round(open_s, 1), "episodes": episodes,
            "best_lock": (float(best) if best else None)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", action="append", required=True)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    excl = {x for x in a.exclude.split(",") if x} | SEALED

    ser: Dict[str, Any] = {}
    t0: Dict[str, float] = {}
    strike: Dict[str, Any] = {}
    exp: Dict[str, float] = {}
    st: Dict[str, str] = {}
    for pat in a.tapes:
        s1, a1, k1, e1, z1 = load(pat)
        for m, v in s1.items():
            if m not in ser or len(v) > len(ser[m]):
                ser[m] = v
        t0.update(a1); strike.update(k1); exp.update(e1); st.update(z1)

    wins = []
    for m, s in sorted(ser.items()):
        tk = m[5:-3]
        if tk in excl or m not in t0 or m not in exp or len(s) < 30:
            continue
        if strike.get(m) is None or st.get(tk) is None:
            continue
        wins.append((m, s))

    spreads, pairs = [], []
    depth = {s: [] for s in SIZES}
    fills = {s: 0 for s in SIZES}
    nobs = 0
    for m, s in wins:
        for r in s:
            up, dn = r.get("UP_q1"), r.get("DOWN_q1")
            if up is None or dn is None:
                continue
            nobs += 1
            pairs.append(float(up + dn))
            spreads.append(float(up + dn - D("1")))
            for z in SIZES:
                have = (r.get("UP_q%d" % z) is not None
                        and r.get("DOWN_q%d" % z) is not None)
                fills[z] += 1 if have else 0
                if have:
                    depth[z].append(float(r["UP_q%d" % z]
                                          + r["DOWN_q%d" % z]))

    res: Dict[str, Any] = {
        "label": LABEL,
        "independent_windows": len(wins),
        "observations": nobs,
        "excluded_by_name": sorted(excl),
        "exclusion_reason": ("6 sealed OOS windows from the first-leg study "
                             "plus live-probe-contaminated windows"),
        "pair_cost_q1": dist(pairs, "$ UP+DOWN"),
        "spread_proxy_q1": dist(spreads, "$ over 1.00"),
        "both_sides_executable_pct": {
            "q%d" % z: round(100.0 * fills[z] / nobs, 2) if nobs else None
            for z in SIZES},
        "pair_cost_at_depth": {"q%d" % z: dist(depth[z], "$")
                               for z in SIZES},
    }
    for which in ("UNDERDOG", "FAVORED"):
        per = []
        for m, s in wins:
            fl = first_leg(s, t0[m], which)
            if fl is None:
                continue
            row = {"market": m, "price": float(fl["price"]),
                   "t_plus_s": round(fl["t_plus_s"], 2)}
            for z in SIZES:
                row["q%d" % z] = complement(s, fl, z)
            per.append(row)
        res[which] = {
            "windows_with_a_first_leg": len(per),
            "first_leg_price": dist([r["price"] for r in per], "$"),
            "first_leg_t_plus": dist([r["t_plus_s"] for r in per], "s"),
            "complement": {
                "q%d" % z: {
                    "windows_reached": sum(1 for r in per
                                           if r["q%d" % z]["reached"]),
                    "of_windows": len(per),
                    "reach_pct": (round(100.0 * sum(
                        1 for r in per if r["q%d" % z]["reached"])
                        / len(per), 2) if per else None),
                    "seconds_to_first": dist(
                        [r["q%d" % z]["seconds_to_first"] for r in per], "s"),
                    "open_seconds_per_window": dist(
                        [r["q%d" % z]["open_seconds"] for r in per
                         if r["q%d" % z]["reached"]], "s"),
                    "episodes_per_window": dist(
                        [float(r["q%d" % z]["episodes"]) for r in per
                         if r["q%d" % z]["reached"]], "count"),
                    "best_lock": dist(
                        [r["q%d" % z]["best_lock"] for r in per
                         if r["q%d" % z]["reached"]], "$"),
                } for z in SIZES},
        }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, default=str)
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
