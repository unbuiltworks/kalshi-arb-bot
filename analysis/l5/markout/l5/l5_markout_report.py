#!/usr/bin/env python3
"""Writes L5_MARKOUT_RESULT.md from l5_markout_summary.json (run l5_markout.py first).
    python3 -I l5_markout_report.py [OUT_DIR]"""
import json, os, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
S = json.load(open(os.path.join(OUT, "l5_markout_summary.json")))
H5 = ["5s", "30s", "2m", "5m", "15m"]
M = S["MARKOUT_14_18_proxy"]


def z(t):
    return t.replace("-0.0c", "0.0c").replace("-0.0 ", "0.0 ").replace("-0.0,", "0.0,").replace("-0.0]", "0.0]") if t.strip() != "-0.0" else "0.0"


def c(x, ci=True, n=True):
    """mean [ci] n (coverage)"""
    if not x or not x.get("n_valued"):
        return "-"
    s = z("%+.1f" % x["mean_c"])
    if ci:
        s += z(" [%+.1f,%+.1f]" % tuple(x["ci95"])) if x.get("ci95") else " [n/a]"
    if n:
        s += " n=%d (%.0f%%)" % (x["n_valued"], 100 * x["coverage"])
    return s


def row(label, d, hs=H5, **kw):
    return "| %s | %s |" % (label, " | ".join(c(d[h], **kw) for h in hs))


L = []
A = L.append
sa = S["sanity"]; v = S["proxy_validation_22Nov"]; xt = S["proxy_check_vs_taker_exit_touch_14_18"]
ex = S["EXACT_22Nov"]; cy = S["SETTLED_cycles_exact"]; mk = S["SETTLED_14_18_all_markets_market_level"]
f5 = S["ITEM5_adverse_selection"]; mm = S["MID_MOVE_since_order_14_18_proxy"]["tables"]
ot = S["OPEN_time_to_flat"]; cap = S["first_completion_bid_vs_pair_target"]
pm = v["proxy_markout_minus_exact_markout_c_on_22Nov_fills"]["AB_adj"]

A("# What happened to price after our fills - sports bot, 14-18 Nov and 22 Nov 2026")
A("")
A("Read-only. Files: `l5_markout.py`, `l5_markout_report.py`, `l5_markout_fills.csv` (one row per fill), `l5_markout_summary.json` (every table, all variants). Cents per contract, before fees.")
A("- **MARKOUT(h)** = value of the side we bought at fill time + h, minus our fill price. YES is worth the mid, NO is worth 1 - mid.")
A("- **SETTLED** = what the side finally paid (1 or 0) minus our fill price. The two are never mixed below.")
A("- Role, from the position before the fill: **OPEN** = no unpaired contracts (YES = NO; pairs are netted and pay $1); **EXTEND** = adds to")
A("  the heavier side; **COMPLETE** = adds to the lighter side. (If \"flat\" means 0/0 only, the %d OPEN fills made at YES = NO > 0 would be" % M["A"]["role_detail"]["OPEN_REOPEN"]["5s"]["n_fills"])
A("  \"EXTEND with 0 unpaired\"; the CSV keeps them apart as `OPEN_REOPEN`.) Mean = contract-weighted; [a,b] = 95% bootstrap interval")
A("  resampling whole markets; n = fills with a usable value (coverage %).")
A("")
A("## MEASURED")
A("### M1. Sanity checks (14-18 Nov file)")
A("- %s rows = %s ORDER + %s FILL. FILL rows: **%.2f contracts, $%.2f cost** (expected 1,900.97 / $953.91: exact match)." % (
    format(sa["rows"], ","), format(sa["order_rows"], ","), format(sa["fill_rows"], ","), sa["total_contracts"], sa["total_cost"]))
A("  %d markets with fills, %d with orders. Duplicate fills: %d. Fills with no matching order: %d. `pos` agrees with a running sum on every fill." % (
    sa["markets_with_fills"], sa["markets_with_orders"], sa["exact_duplicate_fill_rows"], sa["fills_without_matching_order"]))
A("- The 57 rows without a side are NOT cancels: they are ORDER rows with side and price missing (47 NHL, 16 Nov 00h; 10 with qty \"?\").")
A("  The file has no cancel rows. Odd but harmless: %d partial-fill rows (20 orders have 2-3 fill rows), %d fills under 1 contract," % (
    sa["partial_fill_rows"], sa["fills_below_1_contract"]))
A("  3 fills at a better price than the order (all taker exits), rows not in time order.")
so = sa["stage_counts_orders"]; sf = sa["stage_counts_fills"]
A("- Stage, orders with a side / fills: " + "; ".join("%s %d / %d" % (k.replace("two_sided_", ""), so[k], sf.get(k, 0)) for k in so) + ".")
te = sa["taker_exit_fill_minus_order_time_s"]
A("- **Fill times are the bot's log times, not venue times.** The %d taker `exit` fills are logged %.1f-%.1f s after their order (median %.1f s), never sooner; on 22 Nov two fills were logged ~30 s late. The 5 s horizon is therefore not trustworthy." % (
    sa["taker_exit_fills"], te["min"], te["max"], te["median"]))
A("")
A("### M2. MARKOUT, 22 Nov, against the recorded book (EXACT; 8 fills, 2 markets - too few for any interval)")
A("| Fill (UTC, log time) | Role (unpaired before) | at fill | 5 s | 30 s | 2 min | 5 min | 15 min |")
A("|---|---|---|---|---|---|---|---|")
nm = {"atc-sea-bol-udi-2026-11-22-bol": "Bologna", "atc-sea-sas-gen-2026-11-22-gen": "Genoa"}
fx = lambda x: "gap" if x is None else "%+.1f" % x
for f in ex["fills"]:
    A("| %s %s %s %.2f x%g | %s (%g) | %s | %s |" % (
        f["t"][11:19], nm[f["market"]], f["side"], f["px"], f["qty"], f["role"], f["unpaired_before"],
        fx(f["mo_exact_0s_c"]), " | ".join(fx(f["mo_exact_%s_c" % h]) for h in H5)))
for role in ("OPEN", "EXTEND", "COMPLETE"):
    d = ex["by_role"][role]
    A("| **mean %s (%d fills)** | | | %s |" % (role, d["5s"]["n_fills"], " | ".join(
        "-" if not d[h].get("n_valued") else "%+.1f (n=%d)" % (d[h]["mean_c"], d[h]["n_valued"]) for h in H5)))
al = ex["late_logged_fills_from_venue_time"]
A("- \"gap\" = the recording hole 14:16-14:20Z (3 values lost). Recording began 14:07:34Z, before the first fill. The two 14:28 fills were")
A("  logged ~30 s late: from the venue time (~14:28:07Z) the Genoa NO 0.68 was %+.1fc at the fill and %+.1fc 30 s later (the goal)." % (al[1]["mo_0s_c"], al[1]["mo_30s_c"]))
A("  The 14:40 and 14:44 completions turn to -22.5c only because Bologna scored at ~14:44:30; both were already paired.")
A("- SETTLED, 22 Nov: 16 contracts, -$2.40 = %+.1fc per contract (6 pairs +$0.28; 4 unpaired Genoa NO, cost $2.68, paid 0)." % ex["settled"]["overall"]["mean_c"])
A("")
A("### M3. SETTLED result, 14-18 Nov (exact arithmetic on fills; no mid estimate involved)")
a_ = mk["all_markets"]; fp = mk["markets_fully_paired"]; lu = mk["markets_left_unpaired"]
A("- All 173 markets: %.2f USD on %.2f contracts = **%+.2fc per contract** [%+.1f,%+.1f]. Fully paired (126 markets): %+.2fc [%+.1f,%+.1f]." % (
    a_["pnl_usd"], a_["contracts"], a_["settled_c_per_contract"], a_["ci95"][0], a_["ci95"][1],
    fp["settled_c_per_contract"], fp["ci95"][0], fp["ci95"][1]))
A("  Left with unpaired contracts (47 markets): %+.2fc [%+.1f,%+.1f]; in all 47 the unpaired side paid 0." % (
    lu["settled_c_per_contract"], lu["ci95"][0], lu["ci95"][1]))
A("- By cycle (an OPEN fill until YES = NO again; %d cycles, total %.2f USD = the reconciliation's -40.71):" % (cy["all"]["cycles"], cy["all"]["pnl_usd"]))
A("")
A("| Time from OPEN fill to paired | Cycles | Contracts | Mean pair cost | SETTLED c/contract [95%] | $ |")
A("|---|---|---|---|---|---|")
for k, d in cy["by_time_to_pair"].items():
    A("| %s | %d | %.0f | %s | %+.2f [%+.1f,%+.1f] | %+.2f |" % (
        k, d["cycles"], d["contracts"], "%.3f" % d["mean_pair_cost"] if d["mean_pair_cost"] else "-",
        d["settled_c_per_contract"], d["ci95"][0], d["ci95"][1], d["pnl_usd"]))
bd = cy["by_date"]
A("- Mean pair cost by day: " + ", ".join("%s Nov %.3f" % (k[-2:], d["mean_pair_cost"]) for k, d in bd.items()) +
  ". Median time to pair %.0f s; %d of %d OPEN fills never paired." % (ot["median_s"], ot["never_flat_again"], ot["open_fills"]))
R = S["SETTLED_14_18_resolved_markets_only"]
g = lambda grp, k: R[grp][k]["settled"]
A("- SETTLED per fill needs the winner, known only for the 47 markets left unpaired (%d fills, %.0f contracts). **Biased sample**:" % (
    sa["settlement"]["fills_in_resolved_markets"], sa["settlement"]["contracts_in_resolved_markets"]))
A("  these are exactly the markets where the last open leg lost. **OPEN %s (n=%d); COMPLETE %s (n=%d); EXTEND %s (n=%d, 1 market).**" % (
    c(g("role", "OPEN"), n=False), g("role", "OPEN")["n_valued"], c(g("role", "COMPLETE"), n=False), g("role", "COMPLETE")["n_valued"],
    c(g("role", "EXTEND"), ci=False, n=False), g("role", "EXTEND")["n_valued"]))
A("  By entry price: " + "; ".join("%s %s" % (k, c(g("price_bucket", k), n=False)) for k in ("0.25-0.40", "0.40-0.60", "0.60-0.75")) +
  ". By stage: " + "; ".join("%s %s" % (k.replace("two_sided_", ""), c(g("stage", k), n=False))
                             for k in ("two_sided_open", "two_sided_open_rewarded", "two_sided_complete", "two_sided_exit")) + "." +
  " By time rested: " + "; ".join("%s %s" % (k, c(g("rest_time", k), n=False)) for k in ("<10s", "10-60s", "1-5min")) +
  ". By sport and unpaired bucket: in the JSON (most cells are 1-4 markets).")
A("")
A("### M4. How good is the own-quote mid proxy (measured where a real price exists)")
pe = v["pair_mid_error_c"]; se = v["single_adj_error_c"]; d_ = xt["d_c"]
A("- 22 Nov, proxy mid minus recorded mid: two-sided quotes n=%d: mean %+.1fc, mean abs %.1fc, worst %.1fc. One-sided orders, adjusted," % (
    pe["n"], pe["mean"], pe["mean_abs"], max(abs(pe["min"]), abs(pe["max"]))))
A("  n=%d: median %.1fc, mean abs %.1fc, %.0f%% within 2c, worst %.0fc (a completion bid left far behind after the Genoa goal)." % (
    se["n"], abs(se["median"]), se["mean_abs"], 100 * se["within_2c"], se["min"]))
A("  Proxy markout minus exact markout on the 22 Nov fills (n=%d): mean %+.1fc, mean abs %.1fc, worst %+.0fc - the proxy flatters. A weak" % (
    pm["n"], pm["mean"], pm["mean_abs"], pm["max"]))
A("  test: %d of %d two-sided quotes fell inside the recording, and the 22 Nov controller differs from 14-18 Nov." % (
    v["pairs_with_quote"], v["pairs_total"]))
A("- 14-15 Nov, in-sample: a taker exit paid the real far touch. Touch minus the two-sided proxy mid placed seconds later, n=%d:" % xt["n"])
A("  median %+.2fc, %.0f%% within half a cent, %.0f%% within 1c. So the two-sided proxy is good to about half a cent on the" % (
    d_["median"], 100 * xt["share_abs_le_half_cent"], 100 * d_["within_1c"]))
A("  tight college-football quotes of the older controller. No such check exists for the wider 17-18 Nov quotes.")
A("- Hard limit: the true mid lies between our two resting quotes, so a two-sided estimate is off by at most its half-width")
hw = S["proxy_14_18"]["pair_half_width_c"]
A("  (median %.2fc, 10th-90th percentile %.2f-%.2fc over %s two-sided quotes)." % (hw["median"], hw["p10"], hw["p90"], format(hw["n"], ",")))
A("")
A("## ESTIMATED (14-18 Nov; everything here rests on the own-quote proxy)")
A("Value at t+h = first two-sided quote at or after t+h within max(h, 30 s); if none, the first one-sided order, moved by the")
A("market's usual half-width. No interpolation. Variants in the JSON: two-sided only (A), this one (AB_adj), unadjusted (AB_raw).")
A("### E1. MARKOUT by role and by controller era (central variant AB_adj)")
A("| | 5 s | 30 s | 2 min | 5 min | 15 min |")
A("|---|---|---|---|---|---|")
T = M["AB_adj"]
A(row("All fills (1,016)", T["overall"]["all"]))
for k, lab in (("OPEN", "OPEN (504)"), ("EXTEND", "EXTEND (6)"), ("COMPLETE", "COMPLETE (506)")):
    A(row(lab, T["role"][k]))
for k in ("OPEN|14-16 Nov", "OPEN|17-18 Nov", "COMPLETE|14-16 Nov", "COMPLETE|17-18 Nov"):
    A(row("%s (%d)" % (k.replace("|", ", "), T["role_x_era"][k]["5s"]["n_fills"]), T["role_x_era"][k]))
A("- 14-16 Nov = older controller (1 contract, bids %.1fc below mid on average, taker exits). 17-18 Nov = reward-first (5 contracts, %.1fc below mid)." % (
    mm["role_x_era"]["OPEN|14-16 Nov"]["quoted_edge"]["mean_c"], mm["role_x_era"]["OPEN|17-18 Nov"]["quoted_edge"]["mean_c"]))
A("")
A("### E2. The two requested numbers: adverse selection on a resting bid")
A("| OPEN fills | 2 min | 5 min |")
A("|---|---|---|")
for vn, lab in (("AB_adj", "(a) MARKOUT, central"), ("A", "(a) two-sided quotes only"), ("AB_raw", "(a) unadjusted one-sided")):
    A(row(lab, f5["a_OPEN_all"][vn], ["2m", "5m"]))
A(row("mid move since the order was placed*", mm["role"]["OPEN"], ["2m", "5m"]))
for k in ("OPEN|14-16 Nov", "OPEN|17-18 Nov"):
    A(row("mid move*, %s" % k.split("|")[1], mm["role_x_era"][k], ["2m", "5m"]))
for sp, d in f5["a_OPEN_by_sport_n30"].items():
    A(row("(a) %s (%d OPEN fills)" % (sp, d["AB_adj"]["2m"]["n_fills"]), d["AB_adj"], ["2m", "5m"]))
qe = mm["role"]["OPEN"]["quoted_edge"]["mean_c"]
A("- *mid move = MARKOUT minus the %.1fc (mean) that the bid sat below the mid when placed; it is the pure price move against the fill." % qe)
t_ = cap["two_sided_open"]
A("- **Every OPEN figure is biased upward; read it as an upper bound.** A still-unpaired fill is valued from our own completion bid; the older controller parked that bid exactly at the pair target in %d of %d cases, where it cannot show an adverse move." % (
    t_["at pair target"], sum(t_.values()) - t_["no completion bid within 30s"]))
A("  Two-sided quotes return only once the leg has paired (survivors). The %d never-paired OPEN legs (M3) are the adverse tail the proxy does not see." % ot["never_flat_again"])
e2, e5 = f5["b_EXTEND_all"]["AB_adj"]["2m"], f5["b_EXTEND_all"]["AB_adj"]["5m"]
A("- **(b) EXTEND: not measurable.** Only %d EXTEND fills in 14-18 Nov, all with 1 unpaired contract before (bucket 1-5): 2 min %+.1fc" % (e2["n_fills"], e2["mean_c"]))
A("  (n=%d), 5 min %+.1fc (n=%d), no interval. 22 Nov adds 2 exact ones (2 unpaired before): -0.5c and -21.5c at 2 min. No sport has n >= 30." % (
    e2["n_valued"], e5["mean_c"], e5["n_valued"]))
A("")
A("### E3. MARKOUT breakdowns (AB_adj; 30 s / 2 min / 5 min; 5 s and 15 min in the JSON)")
A("| Group (fills) | 30 s | 2 min | 5 min |")
A("|---|---|---|---|")
H3 = ["30s", "2m", "5m"]
for sp in ("cfb", "atp", "cs2", "itfwo", "itfme", "setkameua", "dota2", "nba"):
    A(row("sport %s (%d)" % (sp, T["sport"][sp]["5s"]["n_fills"]), T["sport"][sp], H3))
for k in ("0", "1-5", "6-10"):
    A(row("unpaired before %s (%d)" % (k, T["unpaired_before"][k]["5s"]["n_fills"]), T["unpaired_before"][k], H3))
for k in ("0.25-0.40", "0.40-0.60", "0.60-0.75"):
    A(row("entry price %s (%d)" % (k, T["price_bucket"][k]["5s"]["n_fills"]), T["price_bucket"][k], H3))
for k in ("two_sided_open", "two_sided_open_rewarded", "two_sided_open_rewarded_cheap_first", "two_sided_complete", "two_sided_exit"):
    A(row("stage %s (%d)" % (k.replace("two_sided_", ""), T["stage"][k]["5s"]["n_fills"]), T["stage"][k], H3))
for k in ("<10s", "10-60s", "1-5min", ">5min"):
    A(row("rested %s (%d)" % (k, T["rest_time"][k]["5s"]["n_fills"]), T["rest_time"][k], H3))
A("- No fill had more than 10 unpaired contracts before it; %d fills above 0.75 and %d below 0.25 are too few to value; `exit` = taker, traded ~6 s before the logged time." % (
    T["price_bucket"].get(">0.75", {}).get("5s", {}).get("n_fills", 0), T["price_bucket"].get("<0.25", {}).get("5s", {}).get("n_fills", 0)))
A("")
A("### E4. SETTLED result by 5-minute MARKOUT tercile (47 resolved markets only; markout from the proxy)")
tz = S["SETTLED_by_5min_MARKOUT_tercile"]["AB_adj"]["all"]
A("| Tercile of 5-min MARKOUT | Fills | Mean MARKOUT | Mean SETTLED [95%] | Share that settled as winner |")
A("|---|---|---|---|---|")
for k, lab in (("low_markout", "lowest"), ("mid_markout", "middle"), ("high_markout", "highest")):
    d = tz[k]
    A("| %s | %d | %+.1f | %+.1f [%+.1f,%+.1f] | %.0f%% |" % (lab, d["n"], d["markout_mean_c"], d["settled_mean_c"],
                                                         d["settled_ci95"][0], d["settled_ci95"][1], 100 * d["share_settled_winner"]))
A("- The intervals overlap: a 5-minute markout of a few cents says little about a result decided tens of cents later.")
A("")
A("## NOT AVAILABLE")
A("- A book recording for 14-18 Nov. Every 14-18 Nov markout is an estimate; none is exact.")
A("- Venue fill times (horizons run from the bot's log time, which lags by seconds, ~30 s seen on 22 Nov) and cancel times (no check")
A("  that a quote was still resting; no fill rate per unit of resting time).")
A("- A value for %.0f-%.0f%% of fills, depending on the horizon (the bot placed no order in the window). Coverage is in every cell." % (
    100 * (1 - max(T["overall"]["all"][h]["coverage"] for h in H5)), 100 * (1 - min(T["overall"]["all"][h]["coverage"] for h in H5))))
A("- The winner of the 126 fully paired markets (a pair pays $1 whoever wins): no unbiased SETTLED result per fill or per role.")
A("- EXTEND adverse selection (6 + 2 fills; nothing by sport; no bucket above 10 unpaired). An unbiased OPEN markout: the proxy gives an")
A("  upper bound only; the solid OPEN evidence is M3 (what the cycle finally paid).")
A("- Maker or taker per fill (only the `exit` stage is known taker). Fees and rewards are in no figure here.")

open(os.path.join(OUT, "L5_MARKOUT_RESULT.md"), "w").write("\n".join(L) + "\n")
print("wrote", os.path.join(OUT, "L5_MARKOUT_RESULT.md"), len(L), "lines")
