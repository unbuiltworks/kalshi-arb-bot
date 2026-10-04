"""Compact headline for the two addendum studies, on one screen."""
from __future__ import annotations

import json
import sys


def main() -> int:
    lag = json.load(open(sys.argv[1]))
    cap = json.load(open(sys.argv[2]))

    print("=== BRTI -> PMUS response lag ===")
    print("windows=%s events=%s" % (lag["windows"], lag["n_brti_events"]))
    print("measured latency ms: %s" % lag["measured_latency_ms"])
    a = lag["ALL_events"]
    print("ALL: illusion=%s%%  lead=%s%%  never_responded=%s%%  "
          "median_resp_s=%s  stale_depth_med=%s"
          % (a["ILLUSION_pct_pmus_already_moved_before_info_existed"],
             a["LEAD_pct_pmus_had_not_moved"],
             a["secs_to_material_response_after_availability"]["pct_never_responded"],
             a["secs_to_material_response_after_availability"]["median"],
             a["depth_at_stale_price_median"]))
    print("ALL: capturable at measured latencies: %s"
          % {k.replace("pct_capturable_at_", ""): v
             for k, v in a.items() if k.startswith("pct_capturable_at_")})
    print("ALL: spread_med=%s round_trip_cost_floor=%s  drift_beats_cost_30s=%s"
          % (a["median_spread_at_entry"], a["mean_round_trip_cost_floor"],
             a["VERDICT_drift_beats_cost_at_30s"]))
    print("ALL: favourable mid drift %s"
          % {k.replace("mean_favourable_mid_drift_", ""): v
             for k, v in a.items() if "favourable_mid_drift" in k})
    print("ALL: pnl %s"
          % {k.replace("mean_pnl_", ""): v
             for k, v in a.items() if k.startswith("mean_pnl_")})
    print("\nby move magnitude (sigmas of 1s realized vol):")
    hdr = ("band", "n", "illus%", "capt%", "drift30", "cost", "pnl30",
           "pnlSettle", "beats")
    print(" %-12s %-7s %-7s %-7s %-10s %-9s %-10s %-10s %s" % hdr)
    for b, v in lag["by_move_magnitude_band"].items():
        print(" %-12s %-7d %-7s %-7s %-10s %-9s %-10s %-10s %s"
              % (b, v["n_events"],
                 v["ILLUSION_pct_pmus_already_moved_before_info_existed"],
                 v["pct_capturable_at_measured_median"],
                 v["mean_favourable_mid_drift_30.0s"],
                 v["mean_round_trip_cost_floor"], v["mean_pnl_30.0s"],
                 v["mean_pnl_settlement"], v["VERDICT_drift_beats_cost_at_30s"]))

    print("\n=== Capture mechanics (rebate ZERO, touch is never a fill) ===")
    print(" %-4s %-11s %-10s %-11s %-10s %-10s %-7s %-16s %s"
          % ("mode", "matched", "locked", "residual", "NET", "net/win",
             "pos%", "maker fill", "rest_s med"))
    for m, v in cap["by_mode"].items():
        print(" %-4s %-11s %-10s %-11s %-10s %-10s %-7s %-16s %s"
              % (m, v["matched_qty"], v["locked_net_total"],
                 v["residual_pnl_total"], v["TOTAL_NET"],
                 v["net_per_window_mean"], v["pct_windows_positive"],
                 "%s/%s (%s%%)" % (v["maker_orders_filled"],
                                   v["maker_orders_placed"],
                                   v["maker_fill_rate_pct"]),
                 v["maker_rest_seconds"]["median"]))
    for m, v in cap["by_mode"].items():
        r = v["REBATE_SENSITIVITY_not_in_primary"]
        print(" %-4s rebate sensitivity (NOT in NET above): +%s at %s/contract"
              % (m, r["if_applied_to_passive_fills_only"],
                 r["sourced_rate_per_contract"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
