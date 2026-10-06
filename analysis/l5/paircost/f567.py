"""F5, F6, F7 of frozen protocol 118571f3.

F6/F7 obey the protocol's FAIRNESS REQUIREMENT: a cumulative average is
mechanically smoother than the increments it averages, so every competing
quantity is put through the SAME cumulative transform, and NULL_M's own
cumulative pair cost is included as a comparator. Comparing a cumulative
series to a marginal one would be the pre-barred tautology.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
from collections import defaultdict
from typing import Any, Dict, List, Optional

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.stats import qs
from analysis.l5.paircost.confirm import (BAND, T, acquisitions, jl,
                                          running_series, sn)
from analysis.l5.paircost.spec import assert_frozen


def cum(xs: List[float]) -> List[float]:
    """Running mean -- the one transform applied to EVERY quantity."""
    out, s = [], 0.0
    for i, x in enumerate(xs, 1):
        s += x
        out.append(s / i)
    return out


def disp(xs: List[float]) -> Optional[float]:
    """Dispersion of a series about its own final level, scale-free."""
    xs = [x for x in xs if x is not None]
    if len(xs) < 5:
        return None
    lvl = abs(xs[-1]) or 1.0
    return round(st.pstdev(xs) / lvl, 6)


def main(argv=None) -> int:
    assert_frozen()
    ap = argparse.ArgumentParser()
    ap.add_argument("--events15", required=True)
    ap.add_argument("--parquet15", required=True)
    ap.add_argument("--outdir", default="analysis/l5/paircost")
    a = ap.parse_args(argv)

    ev_all = jl(a.events15)
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev_all:
        byw[r["market_slug"]].append(r)
    for g in byw.values():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
    books, wins = load(a.parquet15, contract_length="15m", slugs=set(byw))

    per: Dict[str, Any] = {}
    terminals: Dict[str, float] = {}
    for slug in sorted(byw):
        ev, tok, start = acquisitions(slug, byw[slug], books,
                                      wins[slug]["tokens"])
        ser = running_series(ev, "C3_VWAP_POOLED")
        rows = [r for r in ser if r["running_pair_cost"] is not None]
        if len(rows) < 5:
            continue
        bu, bd = books.get(tok["up"]), books.get(tok["down"])

        # --- the quantities, each under the SAME cumulative transform ---
        marginal_pc = []
        for r in rows:
            o = "down" if r["outcome"] == "up" else "up"
            s2 = sn(bd if o == "down" else bu, r["t"] - 1)
            c = (s2 or {}).get("best_bid")
            marginal_pc.append(r["price"] + float(c) if c is not None
                               else None)
        mpc = [x for x in marginal_pc if x is not None]

        matched_frac = [2 * r["matched_qty"] /
                        (r["shares_up"] + r["shares_down"])
                        for r in rows
                        if (r["shares_up"] + r["shares_down"]) > 0]
        w_up = [r["shares_up"] / (r["shares_up"] + r["shares_down"])
                for r in rows if (r["shares_up"] + r["shares_down"]) > 0]
        prices = [r["price"] for r in rows]

        # NULL_M comparator: cumulative pair cost of touch-taking both sides
        nullm = []
        for r in rows:
            au = sn(bu, r["t"] - 1)
            ad = sn(bd, r["t"] - 1)
            if not au or not ad:
                continue
            x, y = au.get("best_ask"), ad.get("best_ask")
            if x is None or y is None:
                continue
            nullm.append(float(x) + float(y))

        per[slug] = {
            # F6: both sides of the comparison are CUMULATIVE
            "F6_dispersion_cumulative_running_pair_cost": disp(
                [r["running_pair_cost"] for r in rows]),
            "F6_dispersion_cumulative_marginal_pair_cost": disp(cum(mpc)),
            "F6_dispersion_RAW_marginal_pair_cost_TAUTOLOGY_GUARD": disp(mpc),
            "F6_dispersion_cumulative_NULL_M_pair_cost": disp(cum(nullm)),
            # F7: competing quantities, same transform
            "F7_dispersion_cumulative_matched_fraction": disp(
                cum(matched_frac)),
            "F7_dispersion_cumulative_share_allocation_w_up": disp(cum(w_up)),
            "F7_dispersion_cumulative_price": disp(cum(prices)),
            "n_actions": len(rows),
        }
        terminals[slug] = rows[-1]["running_pair_cost"]

    # --- F5: does the frozen 0.98 survive leave-one-window-out? ---
    f5 = {}
    for held in terminals:
        tr = [v for s, v in terminals.items() if s != held]
        fitted = round(st.median(tr), 6)
        f5[held] = {
            "LOWO_fitted_target_from_other_11": fitted,
            "frozen_target": T,
            "abs_difference": round(abs(fitted - T), 6),
            "held_out_terminal": round(terminals[held], 6),
            "frozen_target_within_band_of_LOWO_fit": bool(
                abs(fitted - T) <= (BAND[1] - BAND[0]) / 2),
        }
    diffs = [v["abs_difference"] for v in f5.values()]

    def agg(key):
        v = [per[s][key] for s in per if per[s].get(key) is not None]
        q = qs(v)
        return {"n": len(v), "median": (q or {}).get("p50"),
                "min": (q or {}).get("min"), "max": (q or {}).get("max")}

    keys = [k for k in next(iter(per.values())) if k.startswith(("F6", "F7"))]
    out = {
        "protocol": "PREREGISTRATION_PAIRCOST.md @ 118571f3",
        "FAIRNESS_NOTE": (
            "every quantity below is put through the SAME running-mean "
            "transform, and NULL_M's own cumulative pair cost is included. "
            "The RAW marginal series is reported ONLY as a tautology guard, "
            "to show how much of any 'cumulative is smoother' result is "
            "just the averaging."),
        "F5_LOWO_target": {
            "per_window": f5,
            "max_abs_difference_from_frozen_0.98": (max(diffs) if diffs
                                                    else None),
            "median_abs_difference": (round(st.median(diffs), 6) if diffs
                                      else None),
            "SURVIVES_LOWO": all(
                v["frozen_target_within_band_of_LOWO_fit"]
                for v in f5.values()),
        },
        "F6_F7_aggregate": {k: agg(k) for k in keys},
        "per_window": per,
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "PAIRCOST_F567.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written PAIRCOST_F567.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
