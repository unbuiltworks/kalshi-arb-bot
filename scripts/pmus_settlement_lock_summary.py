"""Compact headline for the settlement-lock diagnostic."""
from __future__ import annotations
import json
import sys


def main() -> int:
    d = json.load(open(sys.argv[1]))
    v = d["part0_verification"]
    print("=== PART 0  algebra and settlement semantics ===")
    print("rows_checked=%s  max|err| required_remaining_avg=%s  max|err| z=%s"
          % (v["rows_checked"], v["max_abs_error_required_remaining_avg"],
             v["max_abs_error_z"]))
    r = v["settlement_reconstruction"]
    print("settlement recon: %s/%s windows agree under 'UP iff stat > K' (%s%%), "
          "exact ties=%s"
          % (r["agree_with_venue_outcome_under_stat_gt_K"],
             r["windows_with_all_60_closes"], r["agreement_pct"],
             r["exact_ties_stat_equals_K"]))
    print("n_published differs from the pure clock formula on %s rows "
          "(missing index buckets, not an algebra error)"
          % v["n_published_mismatches_vs_pmus_brti"])
    print("final-stat sd in sigma units: %s" % v["final_statistic_sd_in_sigma_units"])
    print("external claims: 30s->%s  15s->%s"
          % (v["external_claim_check"]["claim_1_3_at_30s_remaining"],
             v["external_claim_check"]["claim_1_7_at_15s_remaining"]))

    print("\n=== PART A  does executable pricing incorporate fixed observations? ===")
    print(" %-12s %-7s %-9s %-9s %-9s %-9s %-9s %-10s %s"
          % ("|z|", "n", "disfavPx", "disfavWin", "px-real", "favPx",
             "favWin", "takePnL", "prof%"))
    for b, x in d["partA_market_pricing"]["by_abs_z"].items():
        f = x["hurdle_FAVOURED_side"]
        print(" %-12s %-7d %-9s %-9s %-9s %-9s %-9s %-10s %s"
              % (b, x["n_rows"],
                 x["median_price_of_hurdle_disfavoured_side"],
                 x["realized_win_rate_of_that_side"],
                 x["price_minus_realized"], f["median_price"],
                 f["realized_win_rate"],
                 f["TAKEABLE_mean_pnl_buy_and_hold_to_settlement"],
                 f["pct_profitable"]))

    print("\n=== PART B  residual P&L vs the causally known hurdle ===")
    print("residual totals from the FIXED study: residual=%s locked=%s net=%s"
          % (d["residual_totals_from_fixed_study"]["residual_pnl_total"],
             d["residual_totals_from_fixed_study"]["locked_net_total"],
             d["residual_totals_from_fixed_study"]["TOTAL_NET"]))
    for cp, x in d["partB_residual_vs_hurdle"].items():
        a = x["all_residual_lots"]
        if a is None:
            continue
        print("\n %s (%ss remaining): lots=%s total=%s mean=%s pct_losing=%s"
              % (cp, x["seconds_remaining"], a["n_lots"], a["total_pnl"],
                 a["mean_pnl"], a["pct_losing"]))
        av = x["adverse_vs_favourable"]
        for k in ("hurdle_ADVERSE_to_the_lot", "hurdle_favourable_to_the_lot"):
            s_ = av[k]
            if s_:
                print("   %-30s lots=%-6s total=%-10s mean=%-9s pct_losing=%-7s lot_win%%=%s"
                      % (k, s_["n_lots"], s_["total_pnl"], s_["mean_pnl"],
                         s_["pct_losing"], s_["win_rate_of_lot"]))
        for b, g in x["by_abs_z"].items():
            adv = g["hurdle_ADVERSE_to_the_lot"]
            print("   |z| %-12s all n=%-5s mean=%-9s | ADVERSE n=%-5s mean=%-9s pct_losing=%s"
                  % (b, g["all"]["n_lots"], g["all"]["mean_pnl"],
                     adv["n_lots"] if adv else 0,
                     adv["mean_pnl"] if adv else "-",
                     adv["pct_losing"] if adv else "-"))

    print("\n=== PART C  locked_frac vs z on the same residual lots ===")
    c = d["partC_locked_frac_vs_z"]
    print("checkpoint: %s" % c["checkpoint"])
    print(" by locked_frac: separation range of mean P&L = %s  table=%s"
          % (c["by_locked_frac"]["separation_range_of_mean_pnl"],
             c["by_locked_frac"]["table"]))
    print(" by |z|        : separation range of mean P&L = %s"
          % c["by_abs_z"]["separation_range_of_mean_pnl"])
    for k, t in c["by_abs_z"]["table"].items():
        print("   |z| %-12s n=%-5s mean_pnl=%s" % (k, t["n"], t["mean_pnl"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
