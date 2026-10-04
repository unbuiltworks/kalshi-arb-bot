"""Print the four headline numbers from a path-economics run, compactly.

The full JSON carries every band table; this is the part a reader needs first,
in the order the study was asked: how often a sub-$1 complement exists, how far
below $1 it gets, what failed first legs cost, and whether the causal mechanism
nets out.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    d = json.load(open(sys.argv[1]))
    print("sample=%s windows=%s rows=%s eligible_seconds=%s"
          % (d["sample"], d["windows"], d["rows"], d["eligible_seconds"]))
    for key in ("phase1", "phase1_excluding_final_60s",
                "phase1_fresh_book_sensitivity"):
        print("\n### %s -- %s" % (key, d[key]["label"]))
        for side, v in d[key]["by_side"].items():
            b = v["pair_cost_below"]
            print(" %-4s n=%-7d  " % (side, v["n_first_legs"])
                  + "  ".join("<%s:%s%%" % (k, b[k]["pct"]) for k in b))
            g = v["hindsight_best_pair_gain_per_unit"]
            f = v["FAILED_first_legs_no_sub1_complement"]
            print("      hindsight gain/unit mean=%s median=%s max=%s | "
                  "FAILED n=%d (%s%%) naked mean=%s median=%s p10=%s worst=%s"
                  % (g["mean"], g["median"], g["max"], f["n"], f["pct"],
                     f["naked_pnl_mean"], f["naked_pnl_median"],
                     f["naked_pnl_p10"], f["naked_pnl_worst"]))
            print("      secs_to_best=%s  comp_touch_depth_med=%s  "
                  "sub1_secs_avail_med=%s  longest_run_med=%s"
                  % (v["seconds_to_best_complement"]["median"],
                     v["complement_touch_depth"]["median"],
                     v["sub1_seconds_available"]["median"],
                     v["sub1_longest_consecutive_run_s"]["median"]))
    print("\n### DECISIVE diagnostics")
    for side, v in d["DECISIVE_diagnostics"].items():
        c = v["1_conditional_sub1_frequency"]
        p = v["2_pair_versus_naked_on_the_same_first_legs"]
        w = v["3_when_the_cheap_complement_arrives"]["by_complement_seconds_remaining"]
        print(" %-4s sub1 | first leg WON %s%% (n=%d)  vs  LOST %s%% (n=%d)"
              % (side, c["first_leg_WON"]["pct_sub1"], c["first_leg_WON"]["n"],
                 c["first_leg_LOST"]["pct_sub1"], c["first_leg_LOST"]["n"]))
        print("      pair vs naked: locked=%s naked=%s advantage=%s "
              "pct_pair_beats_hold=%s%%"
              % (p["mean_locked_pair_gain"], p["mean_naked_hold_pnl"],
                 p["mean_advantage_of_pairing"],
                 p["pct_where_pairing_beats_holding"]))
        print("      complement arrives by secs_remaining: %s" % dict(w))
    print("\n### PHASE 2 (causal, no tuned parameter)")
    for k in ("phase2_up_first", "phase2_down_first",
              "phase2_cheaper_side_first"):
        v = d[k]
        n = v["net_per_window"]
        print(" %-28s matched_qty=%-10s tranches/win_med=%-4s locked=%-9s "
              "residual=%-10s NET=%-10s per_win mean=%-9s med=%-8s pos=%s%% "
              "peak_cap_mean=%s"
              % (v["first_side"], v["total_matched_qty"],
                 v["tranches_per_window"]["median"], v["locked_net_total"],
                 v["residual_pnl_total"], v["TOTAL_NET"], n["mean"],
                 n["median"], n["pct_positive"],
                 v["peak_capital_per_window"]["mean"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
