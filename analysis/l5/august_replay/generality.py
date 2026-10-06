"""Does the mechanism found at 08:15 generalize? Frozen S2, eleven new windows.

THE DISCIPLINE THIS ENFORCES. S2 arrives at fingerprint 5650ca16..., frozen
and committed before it had been run on anything. This module asserts that
fingerprint and cannot alter the candidate: there is no parameter here to
tune, no threshold argument, and no per-window branch. The eleven windows are
a TEST, not a training set, and nothing in this file reads a result to decide
a behaviour.

WHAT IS REPORTED. Per window and in aggregate, the behavioural gap to L5 --
fill count and cadence, two-sided activity, matched shares, matched fraction,
residual, stale-quote time and quote replacements -- plus acquisition prices,
pair cost, mint opportunity and settlement cash as DIAGNOSTIC ONLY.

AGGREGATES ARE REPORTED TWICE. Three of the twelve L5-active windows failed
the forensic selection gates (one on a 226 s book gap, two because L5 went
quiet in the final sub-interval). They are included because the brief asks for
the other eleven, and the aggregate is also given over the subset with
COMPLETE L2 coverage so a headline cannot be driven by the window with the
hole. Per-window data-quality flags are carried on every row.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

from analysis.l5.august_replay.s2 import S2_CANDIDATE, S2_FINGERPRINT
from analysis.l5.august_replay.threeway import (DISP, S2_EXPECT, load_window,
                                                run_window, trajectory,
                                                write_trace)
from analysis.l5.august_replay.replay import assert_frozen

BEYOND = "side_ticks_more_than_%d_ticks_behind" % DISP


def ratios(r: Dict[str, Any]) -> Dict[str, Any]:
    """S1 and S2 against L5 on the same window. Division only, no fitting."""
    L, A, B = r["L5"], r["S1"], r["S2"]
    def frac(x, y):
        return (round(x / y, 4) if (y not in (None, 0) and x is not None)
                else None)
    return {
        "fill_count": {"L5": L["n_legs"], "S1": A["n_legs"],
                       "S2": B["n_legs"],
                       "S1_over_L5": frac(A["n_legs"], L["n_legs"]),
                       "S2_over_L5": frac(B["n_legs"], L["n_legs"]),
                       "S2_over_S1": frac(B["n_legs"], A["n_legs"])},
        "matched_shares": {"L5": L["matched"], "S1": A["matched"],
                           "S2": B["matched"],
                           "S1_over_L5": frac(A["matched"], L["matched"]),
                           "S2_over_L5": frac(B["matched"], L["matched"]),
                           "S2_over_S1": frac(B["matched"], A["matched"])},
        "matched_fraction": {"L5": L["matched_fraction_of_shares"],
                             "S1": A["matched_fraction_of_shares"],
                             "S2": B["matched_fraction_of_shares"]},
        "pair_cost": {"L5": L["pair_cost_vwap_sum"],
                      "S1": A["pair_cost_vwap_sum"],
                      "S2": B["pair_cost_vwap_sum"]},
        "both_side_ticks": {"S1": A["both_side"]["ticks_quoting_both_sides"],
                            "S2": B["both_side"]["ticks_quoting_both_sides"]},
        "stale_beyond_threshold_side_ticks": {
            "S1": A["stale_quote"][BEYOND],
            "S2": B["stale_quote"][BEYOND],
            "S1_worst_ticks": A["stale_quote"]["worst_displacement_ticks"],
            "S2_worst_ticks": B["stale_quote"]["worst_displacement_ticks"]},
        "quote_replacements": {"S1": A["quote_replacements"],
                               "S2": B["quote_replacements"]},
        "settlement_net_diagnostic": {
            "L5": L["settlement_net_diagnostic"],
            "S1": A["settlement_net_diagnostic"],
            "S2": B["settlement_net_diagnostic"]},
        "spend": {"L5": L["spend"], "S1": A["spend"], "S2": B["spend"]},
    }


def aggregate(rows: List[Dict[str, Any]], label: str) -> Dict[str, Any]:
    def s(who, key):
        return round(sum((r[who][key] or 0) for r in rows), 4)
    def st(who, key):
        return sum((r[who]["stale_quote"][key] or 0) for r in rows)
    out: Dict[str, Any] = {"label": label, "n_windows": len(rows),
                           "windows": [r["market_slug"] for r in rows]}
    for who in ("L5", "S1", "S2"):
        tot_sh = s(who, "shares_total")
        mat = s(who, "matched")
        spend = s(who, "spend")
        pay = s(who, "settlement_payout_diagnostic")
        out[who] = {
            "fill_legs": int(s(who, "n_legs")),
            "shares_total": tot_sh, "matched_shares": mat,
            "residual_shares": s(who, "residual"),
            "matched_fraction_of_shares": (round(2 * mat / tot_sh, 4)
                                           if tot_sh else None),
            "spend": spend,
            "settlement_payout_diagnostic": pay,
            "settlement_net_diagnostic": round(pay - spend, 4),
            "net_per_dollar_spent_diagnostic": (round((pay - spend) / spend, 4)
                                                if spend else None),
            "windows_with_both_sides_active":
                sum(1 for r in rows if r[who]["both_sides_active"]),
        }
        if who != "L5":
            out[who]["stale_beyond_threshold_side_ticks"] = st(who, BEYOND)
            out[who]["side_ticks_with_a_quote"] = st(
                who, "side_ticks_with_a_quote")
            out[who]["worst_displacement_ticks"] = max(
                (r[who]["stale_quote"]["worst_displacement_ticks"]
                 for r in rows), default=None)
            out[who]["quote_replacements"] = sum(
                r[who]["quote_replacements"] for r in rows)
            out[who]["ticks_quoting_both_sides"] = sum(
                r[who]["both_side"]["ticks_quoting_both_sides"] for r in rows)
    L, A, B = out["L5"], out["S1"], out["S2"]
    def frac(x, y):
        return round(x / y, 4) if y else None
    out["gap_to_L5"] = {
        "fill_legs": {"S1_over_L5": frac(A["fill_legs"], L["fill_legs"]),
                      "S2_over_L5": frac(B["fill_legs"], L["fill_legs"]),
                      "S2_over_S1": frac(B["fill_legs"], A["fill_legs"])},
        "matched_shares": {
            "S1_over_L5": frac(A["matched_shares"], L["matched_shares"]),
            "S2_over_L5": frac(B["matched_shares"], L["matched_shares"]),
            "S2_over_S1": frac(B["matched_shares"], A["matched_shares"])},
        "matched_fraction": {"L5": L["matched_fraction_of_shares"],
                             "S1": A["matched_fraction_of_shares"],
                             "S2": B["matched_fraction_of_shares"]},
        "net_per_dollar_spent_diagnostic": {
            "L5": L["net_per_dollar_spent_diagnostic"],
            "S1": A["net_per_dollar_spent_diagnostic"],
            "S2": B["net_per_dollar_spent_diagnostic"]},
    }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times", required=True)
    ap.add_argument("--selection", required=True)
    ap.add_argument("--prints-dir",
                    default="analysis/l5/august_replay/prints")
    ap.add_argument("--outdir", default="analysis/l5/august_replay")
    ap.add_argument("--arm", default="A", choices=["A", "C"])
    ap.add_argument("--include-selected", action="store_true",
                    help="also run the diagnostic window, for reference")
    a = ap.parse_args(argv)

    frozen = assert_frozen()
    assert S2_FINGERPRINT == S2_EXPECT, (
        "S2 CHANGED -- the generality test is void if the candidate moved")
    print("frozen S1:", frozen["candidate_fingerprint_sha256"])
    print("frozen S2:", S2_FINGERPRINT)

    sel = json.load(open(a.selection))
    chosen = sel["selected"]
    rows_sel = {r["market_slug"]: r for r in sel["rows"] if "gates" in r}
    todo = [s for s in sorted(rows_sel)
            if a.include_selected or s != chosen]

    results: List[Dict[str, Any]] = []
    per_window: List[Dict[str, Any]] = []
    for n, slug in enumerate(todo, 1):
        pp = os.path.join(a.prints_dir, "prints_%s.json" % slug)
        if a.arm == "C" and not os.path.exists(pp):
            print("[%d/%d] %s SKIPPED: no prints for arm C"
                  % (n, len(todo), slug))
            per_window.append({"market_slug": slug,
                               "status": "NO_PRINTS_FOR_ARM_C"})
            continue
        w = load_window(a.parquet, a.backfill, a.block_times, slug,
                        pp if os.path.exists(pp) else None)
        res, s1, s2 = run_window(w, arm=a.arm)
        sr = rows_sel[slug]
        res["data_quality"] = {
            "complete_l2_coverage": sr["coverage"]["COMPLETE_L2_COVERAGE"],
            "seconds_resolvable": sr["coverage"]["seconds_resolvable"],
            "chunk_holes": sr["chunk_holes"],
            "passed_forensic_selection_gates": sr["PASSES_ALL_GATES"],
            "gates": sr["gates"],
        }
        res["ratios"] = ratios(res)
        res["trajectory"] = trajectory(s1, s2, w["start"], w["end"])
        results.append(res)
        os.makedirs(os.path.join(a.outdir, "traces"), exist_ok=True)
        write_trace(os.path.join(a.outdir, "traces",
                                 "TRACE3_arm%s_%s.csv" % (a.arm, slug)),
                    s1, s2)
        print("[%d/%d] %s  L5 legs=%-4d S2 legs=%-4d  matched L5=%-9.2f "
              "S2=%-8.2f  mfrac S2=%-6s pair S2=%-8s stale S2=%d"
              % (n, len(todo), slug.replace("btc-updown-15m-", ""),
                 res["L5"]["n_legs"], res["S2"]["n_legs"],
                 res["L5"]["matched"], res["S2"]["matched"],
                 res["S2"]["matched_fraction_of_shares"],
                 res["S2"]["pair_cost_vwap_sum"],
                 res["S2"]["stale_quote"][BEYOND]), flush=True)

    complete = [r for r in results
                if r["data_quality"]["complete_l2_coverage"]]
    rep = {
        "classification": "HISTORICAL_DIAGNOSTIC_ONLY_NO_SHADOW_NO_LIVE",
        "s2_frozen_before_these_windows": True,
        "s2_fingerprint": S2_FINGERPRINT,
        "s1_fingerprint": frozen["candidate_fingerprint_sha256"],
        "s2_candidate": S2_CANDIDATE,
        "arm": a.arm,
        "diagnostic_window_excluded": (None if a.include_selected
                                       else chosen),
        "no_optimization_on_these_windows": True,
        "aggregate_all": aggregate(results, "all windows run"),
        "aggregate_complete_l2_only": (aggregate(complete,
                                                 "COMPLETE L2 coverage only")
                                       if complete else None),
        "per_window": results,
        "skipped": [x for x in per_window if x.get("status")],
    }
    os.makedirs(a.outdir, exist_ok=True)
    out = os.path.join(a.outdir, "GENERALITY_arm%s.json" % a.arm)
    with open(out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print("\n==== AGGREGATE (arm %s) ====" % a.arm)
    for k in ("aggregate_all", "aggregate_complete_l2_only"):
        if rep[k]:
            print(k, json.dumps(rep[k]["gap_to_L5"], indent=1))
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
