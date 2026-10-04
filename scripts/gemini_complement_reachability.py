"""COMPLEMENT REACHABILITY: the binding constraint, measured.

WHY THIS IS THE FIRST-CLASS OUTPUT. Two independent readings pointed at
the same thing. The capacity replay found that the OPEN executes at every
size to q100 while the COMPLEMENT stops qualifying at q>=10. The
four-window shadow then showed it inside a single window: OPEN UP 0.64,
EXTEND 0.59, and a DOWN ask that sat at 0.51-0.57 for the whole window
while the policy needed <= 0.345. The OPEN is easy. The complement is the
constraint, so it gets measured directly rather than inferred from P&L.

WHAT IS MEASURED, per duration, per size, for every OPEN and every
inventory state that follows it:

    required_complement_price   -- from the FROZEN policy's own solver
  vs
    best executable opposite ask through the REMAINDER of the window
                                -- walked DOWN acquisition VWAP at that
                                   size, and only where the ladder could
                                   actually fill it

THE POLICY IS READ, NEVER REIMPLEMENTED. `required_complement_price` is
the frozen function, and the sensitivity sweep calls that SAME function
with a different `target` argument. No rule is restated here.

THE SENSITIVITY SWEEP IS ANALYSIS ONLY. Pair-cost ceilings .90 to 1.00 are
evaluated to show the shape of the opportunity set. IT IS NOT A PROPOSAL
AND NOT AUTHORIZATION: .96 remains the frozen ceiling and nothing in this
module changes it. The frozen value is marked in the output so the
baseline is never ambiguous.

ONE EXECUTION BOUND IS DELIBERATELY NOT APPLIED. The analysis uses the
frozen policy's own allowance of 2 EXTENDs. The current probe envelope
(PERSIST4) caps submissions at 2 PER WINDOW, which would stop the second
EXTEND from ever being sent -- so the "reachable after 2 EXTENDs" column
describes the STRATEGY's economics, not what today's envelope could
execute. That gap is reported rather than hidden.

DURATIONS STAY SEPARATE. BTC05M and BTC15M are written to their own
dataset roots and never pooled.

Accounting label: historical replay. No order was sent.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

from src.gemini_execution import gemini_durations as DUR
from src.gemini_execution.ci15m_policy import POLICY
from src.gemini_execution.continuous_probe import EXTEND, HOLD, OPEN

LABEL = "GEMINI_COMPLEMENT_REACHABILITY_V1"
SIZES = (D("1"), D("5"), D("10"), D("25"), D("50"), D("100"))
CEILINGS = (D("0.90"), D("0.92"), D("0.94"), D("0.96"), D("0.98"), D("1.00"))
FROZEN_CEILING = POLICY["MAX_PAIR_COST"]


def _tte(tick: Dict[str, Any]) -> Optional[float]:
    """Time-to-expiry, which the tape DOES carry.

    THE BUG THIS FIXES was mine: this module read a `t` field that the
    capacity tape never writes, so `float(tk.get("t") or 0.0)` was 0.0 for
    every tick and EVERY time-to-complement came out as 0.0 seconds. A
    column of zeros looked like "the complement is instantly reachable",
    which is the opposite of what the data says. Elapsed time within a
    window is the DROP in tte, which is exact and uses a field that is
    really there.
    """
    v = tick.get("tte")
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _d(v: Any) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def load(path: str) -> Dict[str, List[Dict[str, Any]]]:
    by: Dict[str, List[Dict[str, Any]]] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("readable") and r.get("ticker"):
                by.setdefault(r["ticker"], []).append(r)
    for t in by:
        by[t].sort(key=lambda r: str(r.get("utc")))
    return by


def _size(tick: Dict[str, Any], qty: D) -> Optional[Dict[str, Any]]:
    for s in tick.get("sizes") or []:
        if _d(s.get("qty")) == qty:
            return s
    return None


def _eff(tick: Dict[str, Any], qty: D) -> Optional[Tuple[D, D, Dict]]:
    """(effective_bid, effective_ask, size_row) where BOTH legs fill."""
    s = _size(tick, qty)
    if s is None:
        return None
    up, dn = _d(s.get("up_vwap")), _d(s.get("down_vwap"))
    if up is None or dn is None:
        return None
    if not (s.get("up_fully_executable") and s.get("down_fully_executable")):
        return None
    bid = D("1") - dn
    if bid >= up:
        return None
    return bid, up, s


def _max_executable_qty(tick: Dict[str, Any], need: D) -> Optional[str]:
    """The largest size whose DOWN leg both fills AND meets `need`."""
    best = None
    for s in tick.get("sizes") or []:
        dn = _d(s.get("down_vwap"))
        if dn is None or not s.get("down_fully_executable"):
            continue
        if dn <= need:
            q = _d(s.get("qty"))
            if q is not None and (best is None or q > best):
                best = q
    return None if best is None else str(best)


def states_for_window(ticks: List[Dict[str, Any]], qty: D) -> Dict[str, Any]:
    """Walk the frozen policy to OPEN and post-EXTEND inventory states, then
    scan the REMAINDER of the window for a reachable complement at each."""
    inv = POLICY["Inventory"]()
    n_ext = 0
    actions = 0
    legs: List[Dict[str, Any]] = []        # OPEN, then each EXTEND
    open_t: Optional[float] = None
    pre_strike = [0]

    for i, tk in enumerate(ticks):
        got = _eff(tk, qty)
        if got is None:
            continue
        bid, ask, _s = got
        # NO OPEN WHERE THE ENGINE COULD NOT OPEN. The tape records
        # `in_frozen_open_band` from this duration's EFFECTIVE band, and
        # for BTC05M that band is [120, 300] -- not because the contract
        # is short-lived, but because THE STRIKE DOES NOT EXIST EARLIER.
        # Measured 2026-10-04: BTC05M contracts are quoted from ~4112s
        # before expiry, yet `strike.value` is None until
        # `strike.availableAt`, which is expiry-300s. Only the contract at
        # tte 259 carried a strike (84734.49); every longer-dated one was
        # None.
        #
        # Without this gate the analysis opened at tte 600+ where no
        # strike is published, which the real engine refuses, and reported
        # a 600.8s median time-to-complement inside a window whose
        # tradeable region is 180s long. Those OPENs were not available.
        if not tk.get("in_frozen_open_band") and inv.q_up == 0 \
                and inv.q_down == 0:
            pre_strike[0] += 1
            continue
        dec = POLICY["decide_once"](inv, bid=bid, ask=ask,
                                   bid_size=qty, ask_size=qty,
                                   tte=float(tk.get("tte") or 0.0), qty=qty,
                                   n_extends=n_ext, actions=actions)
        act = dec["action"]
        if act in (OPEN, EXTEND):
            inv.add(POLICY["make_fill"](0.0, dec["side"], "taker",
                                        dec["price"], qty,
                                        "reachability " + act))
            inv.assert_identities()
            actions += 1
            if act == EXTEND:
                n_ext += 1
            else:
                open_t = _tte(tk)
            legs.append({
                "leg": act, "n_extends_after": n_ext,
                "utc": tk.get("utc"), "tte": tk.get("tte"),
                "tte_at_leg": _tte(tk),
                "price": str(dec["price"]),
                "avg_up": str(inv.avg_up),
                "qty_held": str(inv.q_up),
                "tick_index": i,
                "required_at_frozen": _req(inv, qty, FROZEN_CEILING),
                "required_by_ceiling": {
                    str(c): _req(inv, qty, c) for c in CEILINGS},
            })
        # the frozen policy stops extending at its own cap
        if n_ext >= int(POLICY["MAX_EXTENDS_BEFORE_COMPLEMENT"]):
            break

    # For each leg, scan FORWARD only -- the complement must be reachable
    # AFTER the inventory existed, never before it.
    for leg in legs:
        rest = ticks[leg["tick_index"] + 1:]
        leg["remaining_ticks"] = len(rest)
        req = _d(leg["required_at_frozen"])
        best_gap: Optional[D] = None
        first: Optional[Dict[str, Any]] = None
        for tk in rest:
            s = _size(tk, qty)
            if s is None:
                continue
            dn = _d(s.get("down_vwap"))
            if dn is None or not s.get("down_fully_executable"):
                continue
            if req is not None:
                gap = dn - req            # <= 0 means REACHABLE
                if best_gap is None or gap < best_gap:
                    best_gap = gap
                if gap <= 0 and first is None:
                    first = {
                        "utc": tk.get("utc"), "tte": tk.get("tte"),
                        "seconds_after_leg": _elapsed(leg["tte_at_leg"],
                                                      _tte(tk)),
                        "down_vwap": str(dn),
                        "effective_pair_cost": str(_d(leg["avg_up"]) + dn),
                        "max_executable_qty_at_that_moment":
                            _max_executable_qty(tk, req),
                    }
        leg["min_complement_gap"] = (None if best_gap is None
                                     else str(best_gap))
        leg["reachable_at_frozen"] = first is not None
        leg["first_reachable"] = first
        # measured from the OPEN, so a complement reachable after an
        # EXTEND still reports its distance from the original entry.
        leg["seconds_from_open_to_first_reachable"] = (
            None if (first is None or open_t is None
                     or first["seconds_after_leg"] is None)
            else round(first["seconds_after_leg"]
                       + _elapsed(open_t, leg["tte_at_leg"]), 1))
        # the SENSITIVITY SWEEP, analysis only
        sweep: Dict[str, Any] = {}
        for c in CEILINGS:
            rc = _d(leg["required_by_ceiling"][str(c)])
            if rc is None:
                sweep[str(c)] = {"reachable": None,
                                 "why": "no complement price solves"}
                continue
            hit = None
            gmin: Optional[D] = None
            for tk in rest:
                s = _size(tk, qty)
                if s is None:
                    continue
                dn = _d(s.get("down_vwap"))
                if dn is None or not s.get("down_fully_executable"):
                    continue
                g = dn - rc
                gmin = g if gmin is None else min(gmin, g)
                if g <= 0 and hit is None:
                    hit = {"seconds_after_leg": _elapsed(
                        leg["tte_at_leg"], _tte(tk)),
                        "effective_pair_cost": str(_d(leg["avg_up"]) + dn)}
            sweep[str(c)] = {"reachable": hit is not None,
                             "min_gap": (None if gmin is None else str(gmin)),
                             "first": hit,
                             "is_frozen_ceiling": c == FROZEN_CEILING}
        leg["ceiling_sweep"] = sweep

    residual = inv.residual
    return {
        "legs": legs,
        "n_legs": len(legs),
        "ticks_outside_open_band": pre_strike[0],
        "opened": any(l["leg"] == OPEN for l in legs),
        "residual_qty": str(residual),
        "residual_side": inv.residual_side,
        "residual_principal": str(
            residual * (inv.avg_up if inv.residual_side == "UP"
                        else inv.avg_down) if residual else D("0")),
        "never_reachable": bool(legs) and not any(
            l["reachable_at_frozen"] for l in legs),
    }


def _elapsed(tte_from: Optional[float],
             tte_to: Optional[float]) -> Optional[float]:
    """Seconds between two ticks of the SAME window, from their tte drop."""
    if tte_from is None or tte_to is None:
        return None
    return round(tte_from - tte_to, 1)


def _req(inv: Any, qty: D, target: D) -> Optional[str]:
    v = POLICY["required_complement_price"](inv, qty, target)
    return None if v is None else str(v)


def _pct(n: int, d: int) -> Optional[float]:
    return None if not d else round(100.0 * n / d, 1)


def run(path: str, duration: str) -> Dict[str, Any]:
    by = load(path)
    per_size: Dict[str, Any] = {}
    for q in SIZES:
        wins = {t: states_for_window(ticks, q) for t, ticks in by.items()}
        opened = [w for w in wins.values() if w["opened"]]
        all_legs = [l for w in wins.values() for l in w["legs"]]
        open_legs = [l for l in all_legs if l["leg"] == OPEN]
        after1 = [l for l in all_legs if l["n_extends_after"] == 1]
        after2 = [l for l in all_legs if l["n_extends_after"] == 2]
        gaps = [_d(l["min_complement_gap"]) for l in open_legs
                if l["min_complement_gap"] is not None]
        ttfr = [l["seconds_from_open_to_first_reachable"] for l in open_legs
                if l["seconds_from_open_to_first_reachable"] is not None]
        reach_open = [l for l in open_legs if l["reachable_at_frozen"]]
        pair_costs = [_d(l["first_reachable"]["effective_pair_cost"])
                      for l in reach_open if l["first_reachable"]]
        exec_qty = [l["first_reachable"]["max_executable_qty_at_that_moment"]
                    for l in reach_open if l["first_reachable"]
                    and l["first_reachable"][
                        "max_executable_qty_at_that_moment"]]
        never = [w for w in opened if w["never_reachable"]]
        sweep: Dict[str, Any] = {}
        for c in CEILINGS:
            hits = [l for l in open_legs
                    if (l["ceiling_sweep"].get(str(c)) or {}).get("reachable")]
            sweep[str(c)] = {
                "pct_open_states_ever_reachable": _pct(len(hits),
                                                       len(open_legs)),
                "n_reachable": len(hits), "n_open_states": len(open_legs),
                "is_frozen_ceiling": c == FROZEN_CEILING,
            }
        per_size[str(q)] = {
            "windows": len(wins),
            "ticks_outside_open_band": sum(
                w["ticks_outside_open_band"] for w in wins.values()),
            "windows_with_open": len(opened),
            "n_open_states": len(open_legs),
            "min_complement_gap": {
                "n": len(gaps),
                "best": (None if not gaps else str(min(gaps))),
                "median": (None if not gaps
                           else str(sorted(gaps)[len(gaps) // 2])),
                "worst": (None if not gaps else str(max(gaps))),
                "labels": "gap = executable DOWN VWAP minus the price the "
                          "frozen .96 ceiling requires. <= 0 is REACHABLE.",
            },
            "pct_ever_reaching_frozen_ceiling": _pct(len(reach_open),
                                                     len(open_legs)),
            "pct_reachable_after_1_extend": _pct(
                len([l for l in after1 if l["reachable_at_frozen"]]),
                len(after1)),
            "n_after_1_extend": len(after1),
            "pct_reachable_after_2_extends": _pct(
                len([l for l in after2 if l["reachable_at_frozen"]]),
                len(after2)),
            "n_after_2_extends": len(after2),
            "time_open_to_first_reachable_s": {
                "n": len(ttfr),
                "min": (min(ttfr) if ttfr else None),
                "median": (sorted(ttfr)[len(ttfr) // 2] if ttfr else None),
                "max": (max(ttfr) if ttfr else None)},
            "qty_executable_when_reachable": sorted(set(exec_qty)) or None,
            "effective_pair_cost_when_reachable": {
                "n": len(pair_costs),
                "best": (None if not pair_costs else str(min(pair_costs))),
                "worst": (None if not pair_costs else str(max(pair_costs)))},
            "residual_when_never_reachable": {
                "n_windows": len(never),
                "qty_total": str(sum((_d(w["residual_qty"]) or D("0"))
                                     for w in never)),
                "principal_total": str(sum(
                    (_d(w["residual_principal"]) or D("0")) for w in never)),
            },
            "ceiling_sensitivity": sweep,
        }
    return {
        "label": LABEL, "duration": duration, "tape": path,
        "n_windows": len(by), "windows": sorted(by),
        "effective_open_band": DUR.effective_open_band(duration),
        "frozen_pair_ceiling": str(FROZEN_CEILING),
        "frozen_max_extends": str(POLICY["MAX_EXTENDS_BEFORE_COMPLEMENT"]),
        "per_size": per_size,
        "accounting_label": "historical replay",
        "labels": (
            "The ceiling sensitivity is ANALYSIS ONLY and is NOT a proposal "
            "to move .96, which remains frozen and is flagged in every "
            "sweep row. The 2-EXTEND column describes the STRATEGY: the "
            "current PERSIST4 envelope caps submissions at 2 per window, so "
            "a second EXTEND could not actually be sent today."),
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", default=DUR.BTC15M)
    ap.add_argument("--tape", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    path = a.tape
    if path is None:
        c = sorted(glob.glob(os.path.join(DUR.dataset_root(a.duration),
                                          "capacity_tape_*.jsonl")))
        if not c:
            print("no tape for %s" % a.duration)
            return 2
        path = c[-1]
    rep = run(path, a.duration)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s  %s  windows=%d  band=%s  frozen ceiling=%s"
          % (rep["label"], rep["duration"], rep["n_windows"],
             rep["effective_open_band"]["band"], rep["frozen_pair_ceiling"]))
    print("%4s %6s %7s %9s %8s %8s %8s %10s %12s"
          % ("q", "opens", "reach%", "bestGap", "aft1E%", "aft2E%",
             "ttfr_s", "pairCost", "residPrin"))
    for q, r in rep["per_size"].items():
        print("%4s %6s %7s %9s %8s %8s %8s %10s %12s"
              % (q, r["n_open_states"],
                 r["pct_ever_reaching_frozen_ceiling"],
                 (r["min_complement_gap"]["best"] or "-"),
                 r["pct_reachable_after_1_extend"],
                 r["pct_reachable_after_2_extends"],
                 (r["time_open_to_first_reachable_s"]["median"]
                  if r["time_open_to_first_reachable_s"]["median"]
                  is not None else "-"),
                 (r["effective_pair_cost_when_reachable"]["best"] or "-"),
                 r["residual_when_never_reachable"]["principal_total"]))
    print("\nCEILING SENSITIVITY (analysis only; .96 is frozen) "
          "-- %% of OPEN states ever reachable")
    print("%4s %s" % ("q", " ".join("%7s" % str(c) for c in CEILINGS)))
    for q, r in rep["per_size"].items():
        cells = []
        for c in CEILINGS:
            s = r["ceiling_sensitivity"][str(c)]
            v = s["pct_open_states_ever_reachable"]
            cells.append("%7s" % (("%s*" % v) if s["is_frozen_ceiling"]
                                  else ("-" if v is None else v)))
        print("%4s %s" % (q, " ".join(cells)))
    print("* = the frozen .96 ceiling")
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
