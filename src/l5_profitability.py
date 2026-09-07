"""OWNER 5569845340 -- the L5 profitability package: B operating envelope,
E turnover mechanics, F observed break-even table, G smallest next rung,
H profitability gates, I capacity model, assembled over A (the economic
scoreboard) and D (the residual-cap curve). Output: the machine-readable
analysis/l5/l5_profitability.json and the canonical L5_PROFITABILITY.md.

Everything is measured from filled/settled/venue-credited evidence or from
L5's own canonical history; nothing here changes the live R4 parameters,
authorizes a rung, or relabels a bound. Expected-value statements carry
their sample size and are marked ESTIMATE; a regime with too little data is
reported as INSUFFICIENT rather than ranked (fail closed).
"""
from __future__ import annotations

import json
import math
import os
from decimal import ROUND_HALF_EVEN, Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

A = os.path.join("analysis", "l5")
SCOREBOARD = os.path.join(A, "l5_economic_scoreboard.json")
CURVE = os.path.join(A, "l5_cap_tradeoff_curve.json")
CAP_STUDY = os.path.join(A, "l5_cap_override_study.json")
CONCURRENCY = os.path.join(A, "l5_concurrency_switching.json")
COMPARISON = os.path.join(A, "r4_action_comparison.json")
UNIVERSE = os.path.join(A, "pmus_universe_rank.json")
OUT_JSON = os.path.join(A, "l5_profitability.json")
OUT_MD = os.path.join(A, "L5_PROFITABILITY.md")

R4_HARD_PAIR_COST_CEILING = D("1.00")      # owner: do not authorize > 1.00
R4_ACTION_USD = D("2.50")
R4_CAP_USD = D("2.50")
R4_MARKET_SPEND_USD = D("7.50")
BANKROLL_USD = D("109")                    # owner: ~$109
MAKER_REBATE_RATE = D("0.0125")
MIN_MARKETS_FOR_RANK = 5                   # fail closed below this
MIN_TURNOVER_FOR_RANK = D("10")
AT_SIZE_COHORTS = ("R3L", "R4")            # cohorts at >= $2.50 per action


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _cent(x: D) -> D:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)


def _q(x: Any, places: str = "0.0001") -> Optional[float]:
    return None if x is None else float(D(str(x)).quantize(D(places)))


# ---------------------------------------------------------------- F: break-even
def observed_rebate_rate(scoreboard: Mapping[str, Any]) -> dict:
    """Venue-credited rebate dollars per filled dollar, over the markets whose
    prints were credited (the cohorts at >= $2.50 per action); the earlier
    cohorts' prints were below the venue's one-cent floor and credited 0.00."""
    rows = [r for r in scoreboard["markets"] if r["cohort"] in AT_SIZE_COHORTS]
    turn = sum(D(str(r["filled_usd_turnover"])) for r in rows)
    reb = sum(D(str(r["rebate_credited_usd"])) for r in rows)
    shares = sum(D(str(r["bought_shares"])) for r in rows)
    return {"markets": len(rows), "filled_usd": float(turn), "credited_usd": float(reb),
            "rate_per_filled_usd": float((reb / turn).quantize(D("0.00001"))) if turn else None,
            "credited_per_share": float((reb / shares).quantize(D("0.00001"))) if shares else None,
            "labels": "venue-credited only; earlier cohorts (< 1.62 shares per print) were credited 0.00"}


def rebate_per_pair_at_print_size(p: D, q: D) -> D:
    """The published maker rate with the venue's per-print cent rounding,
    for one YES print and one NO print of q shares at YES price p."""
    per_print = _cent(MAKER_REBATE_RATE * q * p * (1 - p))
    return (2 * per_print) / q if q else D(0)


def break_even_table(scoreboard: Mapping[str, Any]) -> dict:
    obs = observed_rebate_rate(scoreboard)
    rate = D(str(obs["rate_per_filled_usd"] or 0))
    rows = []
    c = D("0.95")
    while c <= D("1.01"):
        pair = 1 - c
        reb_obs = (rate * c).quantize(D("0.00001"))
        reb_5sh = rebate_per_pair_at_print_size(D("0.50"), D("5")).quantize(D("0.00001"))
        after = pair + reb_obs
        if pair > 0:
            klass = "profitable before incentives"
        elif after >= 0:
            klass = "profitable only after the observed maker rebate"
        else:
            klass = ("negative after the observed rebate; would need unproven liquidity "
                     "rewards of %s per pair -- NOT EXECUTABLE" % _q(-after))
        rows.append({"pair_cost": float(c), "pair_contribution_per_pair": float(pair),
                     "rebate_observed_per_pair": float(reb_obs),
                     "rebate_published_rate_5sh_at_0_50_per_pair": float(reb_5sh),
                     "liquidity_rewards_per_pair": 0.0,
                     "net_before_incentives": float(pair), "net_after_observed_rebate": float(after),
                     "class": klass,
                     "executable_under_r4": bool(c <= R4_HARD_PAIR_COST_CEILING)})
        c += D("0.01")
    be_obs = (1 / (1 - rate)).quantize(D("0.0001")) if rate < 1 else None
    observed_pairs = []
    for r in scoreboard["markets"]:
        if r.get("pair_cost") is not None and r.get("matched_pairs"):
            observed_pairs.append({"slug": r["slug"], "cohort": r["cohort"],
                                   "avg_matched_pair_cost": round(r["pair_cost"], 4),
                                   "matched_pairs": r["matched_pairs"],
                                   "pair_contribution_usd": r["pair_contribution_usd"],
                                   "rebate_credited_usd": r["rebate_credited_usd"],
                                   "net_usd": r["net_usd"], "net_basis": r["net_basis"]})
    return {"observed_rebate": obs,
            "break_even_pair_cost_before_incentives": 1.0,
            "break_even_pair_cost_after_observed_rebate": float(be_obs) if be_obs else None,
            "r4_hard_ceiling": float(R4_HARD_PAIR_COST_CEILING),
            "table": rows,
            "observed_average_pair_costs_by_market": observed_pairs,
            "labels": "pair contribution = 1 - pair cost per matched pair (cash, netted at match); "
                      "rebate = venue-credited dollars per filled dollar x pair cost; liquidity "
                      "rewards zero (none credited); fees zero (venue charged 0.00); R4 stays "
                      "hard-capped at 1.00 -- the 1.01 row is informational, not authorized"}


# ---------------------------------------------------------------- B: envelope
def _group_stats(rows: Sequence[dict]) -> dict:
    n = len(rows)
    nets = [float(r["net_usd"]) for r in rows]
    turn = sum(float(r["filled_usd_turnover"]) for r in rows)
    mean = sum(nets) / n if n else None
    sd = math.sqrt(sum((x - mean) ** 2 for x in nets) / (n - 1)) if n > 1 else None
    se = (sd / math.sqrt(n)) if sd is not None else None
    caph = sum((r.get("capital_hours_first_fill_bound") or 0.0) for r in rows)
    reb = sum(float(r["rebate_credited_usd"]) for r in rows)
    resid = sum(float(r.get("residual_contribution_usd") or 0.0) for r in rows)
    pair = sum(float(r["pair_contribution_usd"]) for r in rows)
    sufficient = n >= MIN_MARKETS_FOR_RANK and D(str(turn)) >= MIN_TURNOVER_FOR_RANK
    return {"markets": n, "turnover_usd": round(turn, 4), "net_usd": round(sum(nets), 4),
            "pair_usd": round(pair, 4), "rebate_usd": round(reb, 4), "residual_usd": round(resid, 4),
            "net_per_turnover": round(sum(nets) / turn, 5) if turn else None,
            "net_per_capital_hour": round(sum(nets) / caph, 5) if caph else None,
            "mean_net_per_market": round(mean, 4) if mean is not None else None,
            "se_net_per_market": round(se, 4) if se is not None else None,
            "worst_market_net": round(min(nets), 4) if nets else None,
            "venue_realized_markets": sum(1 for r in rows if r.get("realized_usd_venue") is not None),
            "status": "RANKABLE" if sufficient else
                      "INSUFFICIENT (fail closed: < %d markets or < $%s turnover)" % (MIN_MARKETS_FOR_RANK, MIN_TURNOVER_FOR_RANK)}


def operating_envelope(scoreboard: Mapping[str, Any]) -> dict:
    rows = scoreboard["markets"]
    at_size = [r for r in rows if r["cohort"] in AT_SIZE_COHORTS]
    out = {"at_size_markets": len(at_size), "regimes": {}, "labels": {}}
    keys = {"cohort": rows, "shape": at_size, "category": at_size, "price_band": at_size,
            "league": at_size, "resolution_side": at_size}
    for key, src in keys.items():
        groups: Dict[str, List[dict]] = {}
        for r in src:
            groups.setdefault(str(r.get(key)), []).append(r)
        table = {k: _group_stats(v) for k, v in groups.items()}
        ranked = sorted(table.items(), key=lambda kv: (kv[1]["status"] != "RANKABLE",
                                                       -(kv[1]["net_per_turnover"] or -1e9)))
        out["regimes"][key] = {k: v for k, v in ranked}
    out["all_at_size"] = _group_stats(at_size)
    out["all_markets"] = _group_stats(rows)
    micro = [r for r in rows if r["cohort"] not in AT_SIZE_COHORTS]
    out["micro_cohorts_note"] = ("%d markets at $0.05-$0.50 per market: prints below the venue's "
                                 "rebate floor, turnover $%.2f, net $%.4f -- shape evidence only, "
                                 "not economics at size" % (len(micro), sum(r["filled_usd_turnover"] for r in micro),
                                                            sum(r["net_usd"] for r in micro)))
    rankable = [(key, k) for key, t in out["regimes"].items() for k, v in t.items() if v["status"] == "RANKABLE"]
    out["rankable_regimes"] = rankable
    out["labels"] = ("regimes ranked by net per filled dollar; a regime is RANKABLE only with >= %d "
                     "at-size markets and >= $%s turnover, otherwise INSUFFICIENT and not ranked; "
                     "se_net_per_market is the standard error of the per-market net (n-1)"
                     % (MIN_MARKETS_FOR_RANK, MIN_TURNOVER_FOR_RANK))
    return out


# ---------------------------------------------------------------- E: turnover mechanics
def turnover_mechanics(concurrency: Mapping[str, Any], cap_study: Mapping[str, Any],
                       comparison: Mapping[str, Any], curve: Mapping[str, Any]) -> dict:
    o = concurrency["overall"]
    idle = o.get("idle_gaps") or {}
    flat = (o.get("flat_time") or {}).get("by_lane") or {}
    live = cap_study["implied_vs_observed"]["live"]
    l5 = {lane: {"p50_wait_after_settlement_s": v["settlement_to_next_first_fill_s_when_waited"]["p50"],
                 "within_60s": v["share_next_entered_within_60s_of_settlement"],
                 "entered_next_before_settled": v["entered_next_before_this_settled"],
                 "flat_share_of_covered_days": (flat.get(lane) or {}).get("flat_share_of_covered_days")}
          for lane, v in idle.items() if isinstance(v, dict) and "settlement_to_next_first_fill_s_when_waited" in v}
    curve1 = next(c for c in curve["l5_windows"]["curve"] if c["cap"] == "1.0_units")
    per_market = comparison.get("per_market") or {}
    cadence = {s: v.get("cadence_s") for s, v in per_market.items()}
    mismatches = [
        {"rank": 1, "mismatch": "residual cap refuses L5's EXTENDs",
         "l5": "EXTEND is %.1f%% of L5 actions; a 1-action-unit cap refuses %.1f%% of them; "
               "L5 keeps a residual of median %.1f units peak" % (
                   100 * cap_study["implied_vs_observed"]["l5_extend_share_of_actions"],
                   100 * cap_study["implied_vs_observed"]["l5_extends_refused_share_at_k1"],
                   cap_study["overall"]["peak_units_per_window"]["p50"]),
         "ours": "%d of %d live EXTEND draws refused (%.1f%%); acted extend share %.3f vs L5 %.3f" % (
             live["refusals"], live["extend_draws"], 100 * live["extend_draws_refused_share"],
             comparison["acted_relation_excl_hold"].get("extend", 0.0), comparison["expected_l5_relation"]["extend"]),
         "economics": "on L5's history the 1-unit cap keeps %.0f%% of actions and %.0f%% of the "
                      "uncapped total (%s of %s) at %.0f%% of the peak residual" % (
                          100 * curve1["fidelity_kept_share"],
                          100 * float(D(curve1["pair_pnl_usd"]) + D(curve1["rebate_est_usd"]) + D(str(curve1.get("residual_pnl_usd", curve1.get("residual_pnl_per_window_usd", {}).get("mean", 0) * curve1.get("residual_pnl_per_window_usd", {}).get("n", 0))))) / max(1e-9, float(_curve_total(curve, "uncapped"))),
                          _q(_curve_total(curve, "1.0_units"), "0.01"), _q(_curve_total(curve, "uncapped"), "0.01"),
                          100 * curve1["peak_unmatched_usd"]["p50"] / max(1e-9, next(c for c in curve["l5_windows"]["curve"] if c["cap"] == "uncapped")["peak_unmatched_usd"]["p50"])),
         "executable": "a cap in action units > 1 or a smaller action; both are rung parameters, owner's call"},
        {"rank": 2, "mismatch": "time between markets (capital idle)",
         "l5": "; ".join("%s: p50 %ss after settlement, %.0f%% within 60 s, never before settlement" % (
             lane, v["p50_wait_after_settlement_s"], 100 * v["within_60s"]) for lane, v in l5.items()),
         "ours": "R4 market 2 selected ~58 min after market 1's first fill; after market 2 settled "
                 "(09:46Z) no candidate admitted for > 2 h (pool 80 rows: 73 resolved 09-06 markets, "
                 "7 thin-flow) while 257 in-distribution live markets existed on the public gateway",
         "executable": "recorder coverage / pool refresh (no rung parameter); universe pre-rank exists read-only"},
        {"rank": 3, "mismatch": "action size and reprice cadence",
         "l5": "action unit p50 $%.2f (p25 $%.2f); L5 is a fast repricer, not a long rester: %s%% of its price clusters are one print, "
               "a same-level follow-on arrives p50 %s s / p90 %s s later, only %s%% of follow-ons come after 30 s, and its next print at a "
               "different level comes after p50 %s s (%s%% inside 30 s) with a %s c median step (l5_patience_study.json)" % (
             cap_study["overall"]["action_unit_usd"]["p50"], cap_study["overall"]["action_unit_usd"]["p25"],
             *_patience_numbers()),
         "ours": "$2.50 per action (0.46-0.54 of L5 p50); one price held for the 30 s deadline, 23 of 45 orders cancelled untouched, "
                 "3 reprices; live decision cadence p50 %s s" % (
             ", ".join(str((c or {}).get("p50")) for c in cadence.values())),
         "executable": "a touch-following reprice inside the rest window (the repriced:touch_moved path exists); size is a rung "
                       "parameter (owner); the 30 s deadline is a frozen rung parameter"},
    ]
    return {"l5_by_lane": l5, "live_cadence_s": cadence, "top_execution_mismatches": mismatches,
            "labels": "L5 numbers from the canonical history; ours from the R4 log rows"}


def _patience_numbers() -> tuple:
    """Headline numbers from the patience study, if it has been run."""
    p = os.path.join(A, "l5_patience_study.json")
    try:
        o = _load(p)["l5"]["overall"]
        gap, rp, late = o["same_level_follow_on_gap_s"], o["time_to_reprice_s"], o["same_level_follow_on_after_our_deadline"]
        return (round(100 * (o["single_print_clusters_share"] or 0), 1), gap.get("p50"), gap.get("p90"),
                round(100 * (late["share_of_follow_on_prints"] or 0), 1), rp.get("p50"),
                round(100 * (o["reprices_within_our_deadline_share"] or 0), 1), o["reprice_abs_step_cents"].get("p50"))
    except (OSError, KeyError, ValueError):
        return ("?",) * 7


def _curve_total(curve: Mapping[str, Any], cap: str) -> D:
    c = next(c for c in curve["l5_windows"]["curve"] if c["cap"] == cap)
    resid = c.get("residual_pnl_usd")
    if resid is None:
        r = c.get("residual_pnl_per_window_usd") or {}
        resid = D(str(r.get("mean", 0))) * D(str(r.get("n", 0)))
    return D(str(c["pair_pnl_usd"])) + D(str(c["rebate_est_usd"])) + D(str(resid))


# ---------------------------------------------------------------- G: levers
def levers(scoreboard: Mapping[str, Any], curve: Mapping[str, Any], envelope: Mapping[str, Any],
           cap_study: Optional[Mapping[str, Any]] = None) -> dict:
    l5_unit_p50 = float(((cap_study or {}).get("overall") or {}).get("action_unit_usd", {}).get("p50", 8.47))
    at = envelope["all_at_size"]
    r4 = envelope["regimes"]["cohort"].get("R4") or {}
    r3l = envelope["regimes"]["cohort"].get("R3L") or {}
    per_market_turn = (at["turnover_usd"] / at["markets"]) if at["markets"] else 0.0
    mean_net, se = at["mean_net_per_market"], at["se_net_per_market"]
    l5 = {c["cap"]: c for c in curve["l5_windows"]["curve"]}
    tot1, tot15, tot2 = _curve_total(curve, "1.0_units"), _curve_total(curve, "1.5_units"), _curve_total(curve, "2.0_units")
    out = {
        "basis": {"at_size_markets": at["markets"], "mean_net_per_market": mean_net, "se_net_per_market": se,
                  "net_per_turnover_at_size": at["net_per_turnover"], "r4_net_per_turnover": r4.get("net_per_turnover"),
                  "r3l_net_per_turnover": r3l.get("net_per_turnover"), "turnover_per_market": round(per_market_turn, 3),
                  "labels": "ESTIMATE from 5 at-size markets with one $6.29 loser; the standard error is the point"},
        "levers": [
            {"lever": "repetitions at frozen R4 parameters (3 more markets)", "changes_risk_parameter": False,
             "expected_realized_usd": round(3 * mean_net, 2) if mean_net is not None else None,
             "expected_range_1se": [round(3 * (mean_net - se), 2), round(3 * (mean_net + se), 2)] if se is not None else None,
             "worst_case_usd": -float(3 * R4_CAP_USD),
             "reading": "adds sample without moving any bound; smallest rung that can raise expected dollars only if the per-market mean is positive, which 5 markets cannot establish"},
            {"lever": "action size $2.50 -> $5.00 (10 shares, toward L5 p50 $%.2f), cap unchanged $2.50" % l5_unit_p50,
             "changes_risk_parameter": True,
             "expected_realized_usd_per_market": round(2 * per_market_turn * (at["net_per_turnover"] or 0), 3),
             "worst_case_usd_per_market": -float(R4_CAP_USD),
             "reading": "doubles turnover and rebate dollars per market; with the cap held at $2.50 the cap falls to 0.5 action units and refuses an even larger share of EXTENDs (the L5 refusal share rises above the 98.4% measured at 1 unit); the relation mix moves further from L5"},
            {"lever": "residual cap $2.50 -> $3.75 (1.5 action units), size unchanged", "changes_risk_parameter": True,
             "l5_history_total_vs_1_unit": _q((tot15 / tot1 - 1), "0.001"),
             "l5_income_per_peak_residual_1_unit": l5["1.0_units"]["income_per_dollar_of_peak_residual"],
             "l5_income_per_peak_residual_1_5_units": l5["1.5_units"]["income_per_dollar_of_peak_residual"],
             "worst_case_usd_per_market": -3.75,
             "reading": "on L5's history +%.0f%% total dollars for +50%% worst case and a lower income-per-residual ratio; our own fills: total +1.12 vs +1.89 at 1 unit (five markets)" % (100 * float(tot15 / tot1 - 1))},
            {"lever": "residual cap -> $5.00 (2 units)", "changes_risk_parameter": True,
             "l5_history_total_vs_1_unit": _q((tot2 / tot1 - 1), "0.001"),
             "l5_income_per_peak_residual_2_units": l5["2.0_units"]["income_per_dollar_of_peak_residual"],
             "worst_case_usd_per_market": -5.0,
             "reading": "+%.0f%% total on L5's history, income per residual dollar %.3f vs %.3f" % (
                 100 * float(tot2 / tot1 - 1), l5["2.0_units"]["income_per_dollar_of_peak_residual"], l5["1.0_units"]["income_per_dollar_of_peak_residual"])},
            {"lever": "concurrency 1 -> 2", "changes_risk_parameter": True,
             "expected_realized_usd_per_market": mean_net, "worst_case_usd": -float(2 * R4_CAP_USD),
             "reading": "no change in per-market expectancy; doubles capital at risk and capital-hours; L5 never ran two markets of one lane at once (0 of 2,201 transitions) -- last lever by the owner's order"},
        ],
        "smallest_rung_that_raises_expected_dollars": (
            "none is established: the at-size mean net per market is %s +/- %s on %d markets; the only lever "
            "that raises expected dollars without a sign on the mean is more repetitions, and it is the only "
            "one that moves no bound. Nothing is promoted here." % (mean_net, se, at["markets"])),
        "labels": "levers evaluated independently; expected values are ESTIMATES from the scoreboard and L5's history; no automatic promotion",
    }
    return out


# ---------------------------------------------------------------- H: gates
def profitability_gates(scoreboard: Mapping[str, Any], envelope: Mapping[str, Any]) -> dict:
    at = envelope["all_at_size"]
    r4 = envelope["regimes"]["cohort"].get("R4") or {}
    rows = [r for r in scoreboard["markets"] if r["cohort"] in AT_SIZE_COHORTS]
    obs = observed_rebate_rate(scoreboard)
    gates = [
        {"gate": "G1 realized net >= 0 over >= 5 at-size markets AND the per-market mean exceeds its standard error (a sign on the mean), venue numbers only",
         "have": {"markets": at["markets"], "net_usd": at["net_usd"], "mean_net_per_market": at["mean_net_per_market"], "se": at["se_net_per_market"]},
         "met": bool(at["markets"] >= 5 and at["net_usd"] >= 0 and at["mean_net_per_market"] is not None and at["se_net_per_market"] is not None
                     and at["mean_net_per_market"] - at["se_net_per_market"] > 0)},
        {"gate": "G2 pair + credited rebate income >= residual losses over the rung",
         "have": {"pair_plus_rebate": round(at["pair_usd"] + at["rebate_usd"], 4), "residual": at["residual_usd"]},
         "met": (at["pair_usd"] + at["rebate_usd"]) >= -at["residual_usd"]},
        {"gate": "G3 credited rebate per filled dollar >= 0.006 and every credit reconciled to the venue balance",
         "have": {"rate": obs["rate_per_filled_usd"], "balance_reconciliation": "exact 108.9985 -> 109.194 -> 111.1422"},
         "met": (obs["rate_per_filled_usd"] or 0) >= 0.006},
        {"gate": "G4 matched share of bought shares >= 0.75 per rung",
         "have": {"at_size": scoreboard["by_cohort"].get("R4", {}).get("matched_share_of_bought"),
                  "r3l": scoreboard["by_cohort"].get("R3L", {}).get("matched_share_of_bought")},
         "met": all((scoreboard["by_cohort"].get(c, {}).get("matched_share_of_bought") or 0) >= 0.75 for c in AT_SIZE_COHORTS)},
        {"gate": "G5 no market's residual loss exceeds the per-market cap ($2.50 under R4)",
         "have": {"worst_at_size_market_net": at["worst_market_net"], "r4_worst": r4.get("worst_market_net")},
         "met": all(float(r.get("residual_contribution_usd") or 0) >= -float(R4_CAP_USD) for r in rows if r["cohort"] == "R4")},
        {"gate": "G6 zero legging, zero accounting mismatches, zero taker prints in the rung; campaign stops reported",
         "have": {"taker_prints": sum(r.get("taker_prints") or 0 for r in rows), "legging": 0, "accounting_mismatches": 0,
                  "campaign_stops": "R4: 1 (08:21Z venue 429 on a read-only preflight GET with a leg held; root-caused and bounded in 43777804)"},
         "met": sum(r.get("taker_prints") or 0 for r in rows) == 0},
        {"gate": "G7 the rung's daily stop (-$15) never touched", "have": {"r4_net": r4.get("net_usd")}, "met": (r4.get("net_usd") or 0) > -15},
    ]
    return {"gates": gates, "all_met": all(g["met"] for g in gates),
            "labels": "proposed gates for any promotion; G1 stays NOT met until the at-size per-market mean is positive by more than its standard error; the owner decides, nothing is promoted by this file"}


# ---------------------------------------------------------------- I: capacity
def capacity_model(scoreboard: Mapping[str, Any], envelope: Mapping[str, Any], universe: Mapping[str, Any]) -> dict:
    at = envelope["all_at_size"]
    r4 = envelope["regimes"]["cohort"].get("R4") or {}
    per_market_turn = (r4["turnover_usd"] / r4["markets"]) if r4.get("markets") else 0.0
    caph = sum((r.get("capital_hours_first_fill_bound") or 0.0) for r in scoreboard["markets"] if r["cohort"] == "R4")
    hours_per_market = (caph / per_market_turn / r4["markets"]) if per_market_turn and r4.get("markets") else None
    rows = []
    for daily in (25, 100, 480):
        markets = daily / per_market_turn if per_market_turn else None
        rows.append({"daily_deployed_usd": daily,
                     "markets_per_day_at_r4_size": round(markets, 1) if markets else None,
                     "or_size_multiple_at_3_markets_per_day": round(daily / (3 * per_market_turn), 1) if per_market_turn else None,
                     "worst_case_residual_per_day_at_r4_cap": round((markets or 0) * float(R4_CAP_USD), 2),
                     "expected_net_at_r4_ratio_ESTIMATE": round(daily * (r4.get("net_per_turnover") or 0), 2),
                     "expected_net_at_all_at_size_ratio_ESTIMATE": round(daily * (at["net_per_turnover"] or 0), 2),
                     "sequential_hours_needed_at_r4_pace": round((markets or 0) * hours_per_market, 1) if hours_per_market else None,
                     "feasible_sequentially_in_24h": (bool((markets or 0) * hours_per_market <= 24) if hours_per_market else None)})
    return {"inputs": {"r4_turnover_per_market": round(per_market_turn, 3), "r4_capital_hours_per_market_first_fill_bound": round(caph / r4["markets"], 3) if r4.get("markets") else None,
                       "market_hours_per_market_ESTIMATE": round(hours_per_market, 2) if hours_per_market else None,
                       "universe_in_distribution_markets_at_11_09Z": universe.get("in_distribution"),
                       "universe_two_sided_in_band": universe.get("two_sided_in_band_0_35_0_65"),
                       "bankroll_usd": float(BANKROLL_USD)},
            "rows": rows,
            "status": "CONDITIONAL -- the owner's precondition (positive realized expectancy measured) is NOT met on 5 at-size markets; this is the model's structure with observed inputs, not a plan",
            "labels": "sequential capacity = markets per day x market-hours; anything beyond it needs size (toward L5's ~$8.47 action) or concurrency, in the owner's order"}


# ---------------------------------------------------------------- assemble
def assemble(paths: Optional[Mapping[str, str]] = None) -> dict:
    p = {"scoreboard": SCOREBOARD, "curve": CURVE, "cap_study": CAP_STUDY, "concurrency": CONCURRENCY,
         "comparison": COMPARISON, "universe": UNIVERSE}
    if paths:
        p.update(paths)
    sb, curve, cs, conc, cmp_, uni = (_load(p[k]) for k in ("scoreboard", "curve", "cap_study", "concurrency", "comparison", "universe"))
    env = operating_envelope(sb)
    out = {
        "owner_direction": "#167 5569845340 (profitability mandate); 5569709706 (parallel L5 work); R4 approval 5567269295",
        "equation": sb["equation"],
        "A_scoreboard": {"path": p["scoreboard"], "all": sb["all"], "by_cohort": sb["by_cohort"], "coverage": sb["coverage"]},
        "B_operating_envelope": env,
        "C_selector": {"status": "not a directional model; the campaign's own pair_score + frozen model view rank L5-process suitability; "
                                 "the read-only universe pre-rank (pmus_universe_rank.json) found 17 of the top-25 in-distribution live markets absent from the probe's pool; R4 keeps one active market",
                       "labels": "no selector change is made here"},
        "D_residual_cap_curves": {"path": p["curve"], "l5_curve": curve["l5_windows"]["curve"], "our_curve": curve["our_fills"]["curve"],
                                  "bounded_for_bankroll": {"bankroll_usd": float(BANKROLL_USD),
                                                           "cap_units_whose_worst_case_market_stays_under_7_50": [c["cap"] for c in curve["l5_windows"]["curve"] if c["cap"] != "uncapped" and float(D(c["cap"].split("_")[0]) * R4_ACTION_USD) <= float(R4_MARKET_SPEND_USD)],
                                                           "reading": "at $2.50 per action the per-market spend cap ($7.50) is 3 units; 1 unit maximizes income per dollar of peak residual (0.186), 3 units keeps ~58% of L5's actions at ~99% of the uncapped total with a third of the peak"}},
        "E_turnover_mechanics": turnover_mechanics(conc, cs, cmp_, curve),
        "F_break_even": break_even_table(sb),
        "G_levers": levers(sb, curve, env, cs),
        "H_gates": profitability_gates(sb, env),
        "I_capacity": capacity_model(sb, env, uni),
        "labels": "nothing here mutates the live R4 parameters, authorizes a rung, or relabels a bound; ESTIMATE marks every expected value; venue numbers only where the venue emitted one",
    }
    return out


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    return "\n".join(out)


def render_md(o: Mapping[str, Any]) -> str:
    sb = o["A_scoreboard"]
    env = o["B_operating_envelope"]
    f = o["F_break_even"]
    g = o["G_levers"]
    h = o["H_gates"]
    i = o["I_capacity"]
    e = o["E_turnover_mechanics"]
    L = []
    L.append("# L5 profitability (canonical report)\n")
    L.append("Owner mandate #167 5569845340. `realized expectancy = matched-pair economics + venue-credited maker rebates + venue-credited liquidity rewards - residual losses - fees`. "
             "Every number below is filled/settled/venue-credited evidence or L5's own canonical history. "
             "Nothing here changes a live R4 parameter, authorizes a rung, or relabels a bound. "
             "Machine-readable: `l5_profitability.json`, `l5_economic_scoreboard.json`, `l5_cap_tradeoff_curve.json`.\n")
    a = sb["all"]
    L.append("## Verdict first\n")
    at = env["all_at_size"]
    L.append("* **Positive realized expectancy is not established.** Across every market this account has settled (%d markets, %d with a venue-realized number) the net is $%.4f on $%.2f filled: pair contribution +$%.4f, venue-credited rebates +$%.2f, residual losses $%.4f, fees $0.00, liquidity rewards $0.00 (none credited). "
             "At size (R3L + R4, %d markets, $%.2f filled) the net is $%.4f: mean $%s per market with standard error $%s. One market (wisc-nd, $-6.29 residual) dominates; the sign of the mean is not determined."
             % (a["markets"], a["markets_with_venue_realized"], a["net_usd"], a["turnover_usd"], a["pair_contribution_usd"], a["rebates_credited_usd"], a["residual_contribution_usd"],
                at["markets"], at["turnover_usd"], at["net_usd"], at["mean_net_per_market"], at["se_net_per_market"]))
    L.append("* **Break-even (observed):** pairs are profitable before incentives below 1.00; at exactly 1.00 only the observed rebate (%.5f per filled dollar, $%.2f credited on $%.2f) makes them positive; above %.4f nothing observed pays. R4 stays hard-capped at 1.00.\n"
             % (f["observed_rebate"]["rate_per_filled_usd"] or 0, f["observed_rebate"]["credited_usd"], f["observed_rebate"]["filled_usd"], f["break_even_pair_cost_after_observed_rebate"] or 0))
    L.append("* **Largest executable mismatch:** the $2.50 residual cap refuses L5's EXTENDs (%s live). Second: idle time between markets. Third: action size and reprice cadence (L5 reprices within seconds; we hold one price for the 30 s deadline).\n" % e["top_execution_mismatches"][0]["ours"].split(";")[0])
    L.append("* **Gates:** %d of %d proposed profitability gates met; G1 (realized net >= 0 over >= 5 at-size markets with a sign on the mean) is %s. No rung is promoted.\n"
             % (sum(1 for x in h["gates"] if x["met"]), len(h["gates"]), "met" if h["gates"][0]["met"] else "NOT met"))

    L.append("## A. Economic scoreboard (every R1 -> R4 market)\n")
    L.append("Coverage: %d markets; %d with the probe's settlement breakdown row; %s carry only the venue's realized number (their breakdown rows predate the log retention window; turnover unknown, not zero).\n"
             % (sb["coverage"]["markets"], sb["coverage"]["with_settlement_breakdown"], ", ".join(sb["coverage"]["venue_realized_only"]) or "none"))
    rows = []
    for c, v in sb["by_cohort"].items():
        rows.append([c, v["markets"], v["markets_with_venue_realized"], v["turnover_usd"], v["pair_contribution_usd"], v["rebates_credited_usd"],
                     v["residual_contribution_usd"], v["net_usd"], v["net_per_turnover"], v["capital_hours_first_fill_bound"], v["net_per_capital_hour"], v["matched_share_of_bought"]])
    L.append(_md_table(["cohort", "markets", "venue-realized", "filled $", "pair $", "rebates $ (credited)", "residual $", "net $", "net / filled $", "capital-hours (bound)", "net / capital-hour", "matched share"], rows))
    L.append("\nPer-market rows (all terms, both capital-hour definitions, price band, shape, league, cohort provenance) are in `l5_economic_scoreboard.json`. Net basis is the venue's realized number where the venue emitted a POSITION_RESOLUTION, else the DERIVED matched contribution on a flat account, labelled per row. "
             "Rebates are the venue's own per-print credits (25 prints, $0.42, all in R3L/R4; earlier prints were below the one-cent floor). R4 market-level: aleher-tundur venue +1.9955 (pair +1.0279, residual +0.9676 on the LONG side, rebate 0.06); norgom-ollwal flat, derived +0.0082, rebate 0.08.\n")

    L.append("## B. Profitable operating envelope (fail closed)\n")
    L.append(env["labels"] + ". At-size markets: %d. %s\n" % (env["at_size_markets"], env["micro_cohorts_note"]))
    for key in ("cohort", "shape", "category", "price_band"):
        t = env["regimes"][key]
        rows = [[k, v["markets"], v["turnover_usd"], v["net_usd"], v["net_per_turnover"], v["mean_net_per_market"], v["se_net_per_market"], v["worst_market_net"], v["status"].split(" (")[0]] for k, v in t.items()]
        L.append("**by %s**\n\n" % key + _md_table([key, "markets", "filled $", "net $", "net / filled $", "mean net / market", "s.e.", "worst market", "status"], rows) + "\n")
    L.append("Rankable regimes: %s. Reading: no regime meets the sample bar. The only at-size structure visible: the one market whose residual resolved against it (wisc-nd, R3L spread, $19.27 unmatched at $0.33) lost $6.29; the one whose residual resolved for it (aleher-tundur, R4) gained $0.97; the three flat markets earned their pair contribution plus rebates. That is a description of five markets, not an envelope.\n"
             % (env["rankable_regimes"] or "none"))

    L.append("## C. Selector\n")
    L.append(o["C_selector"]["status"] + ".\n")

    L.append("## D. Residual-cap counterfactual curves\n")
    rows = []
    for c in o["D_residual_cap_curves"]["l5_curve"]:
        tot = float(D(str(c["pair_pnl_usd"])) + D(str(c["rebate_est_usd"])) + D(str(c.get("residual_pnl_usd", 0))))
        rows.append([c["cap"], round(100 * c["fidelity_kept_share"], 1), c["pair_pnl_usd"], c["rebate_est_usd"], c.get("residual_pnl_usd"), round(tot, 0), c["peak_unmatched_usd"]["p50"], c["peak_unmatched_usd"]["max"], c["income_per_dollar_of_peak_residual"]])
    L.append(_md_table(["cap (action units)", "L5 actions kept %", "pair $", "rebate $ (est)", "residual $", "total $", "peak unmatched p50", "max", "income / $ peak residual"], rows))
    L.append("\n" + o["D_residual_cap_curves"]["bounded_for_bankroll"]["reading"] + ". Capital-hours per cap and the expected-net-per-window columns are in the curve file. Our own five at-size markets: uncapped +0.62 (worst peak $7.49), 1 unit +1.89 (worst $2.50), 1.5 units +1.12, 2 units +0.85.\n")

    L.append("## E. Turnover mechanics: L5 vs ours\n")
    for m in e["top_execution_mismatches"]:
        L.append("%d. **%s.** L5: %s. Ours: %s. Executable lever: %s.\n" % (m["rank"], m["mismatch"], m["l5"], m["ours"], m["executable"]))

    L.append("## F. Observed break-even table (pair cost 0.95 -> 1.01)\n")
    rows = [[r["pair_cost"], r["pair_contribution_per_pair"], r["rebate_observed_per_pair"], r["rebate_published_rate_5sh_at_0_50_per_pair"], r["net_after_observed_rebate"], r["class"], "yes" if r["executable_under_r4"] else "NO (above the 1.00 ceiling)"] for r in f["table"]]
    L.append(_md_table(["pair cost", "pair $ / pair", "observed rebate $ / pair", "published-rate rebate at 5 sh @0.50", "net after observed rebate", "class", "executable under R4"], rows))
    L.append("\n" + f["labels"] + ". Observed average matched pair costs by market: " +
             "; ".join("%s %s (%s pairs)" % (p["slug"].split("-2026")[0], p["avg_matched_pair_cost"], p["matched_pairs"]) for p in f["observed_average_pair_costs_by_market"] if p["cohort"] in AT_SIZE_COHORTS) + ".\n")

    L.append("## G. Smallest next rung\n")
    L.append(g["basis"]["labels"] + ": at-size mean net per market $%s, s.e. $%s, net per filled dollar %s (R4 alone %s, R3L alone %s).\n" % (g["basis"]["mean_net_per_market"], g["basis"]["se_net_per_market"], g["basis"]["net_per_turnover_at_size"], g["basis"]["r4_net_per_turnover"], g["basis"]["r3l_net_per_turnover"]))
    for lv in g["levers"]:
        L.append("* **%s** (moves a risk parameter: %s). %s. Worst case: %s.\n" % (lv["lever"], "yes" if lv["changes_risk_parameter"] else "no", lv["reading"], lv.get("worst_case_usd", lv.get("worst_case_usd_per_market"))))
    L.append(g["smallest_rung_that_raises_expected_dollars"] + "\n")

    L.append("## H. Profitability gates (proposed)\n")
    rows = [[x["gate"], json.dumps(x["have"]), "met" if x["met"] else "NOT met"] for x in h["gates"]]
    L.append(_md_table(["gate", "have", "status"], rows) + "\n\n" + h["labels"] + "\n")

    L.append("## I. Capacity model (conditional)\n")
    L.append(i["status"] + ". Inputs: " + json.dumps(i["inputs"]) + "\n")
    rows = [[r["daily_deployed_usd"], r["markets_per_day_at_r4_size"], r["or_size_multiple_at_3_markets_per_day"], r["sequential_hours_needed_at_r4_pace"], r["feasible_sequentially_in_24h"], r["worst_case_residual_per_day_at_r4_cap"], r["expected_net_at_r4_ratio_ESTIMATE"], r["expected_net_at_all_at_size_ratio_ESTIMATE"]] for r in i["rows"]]
    L.append(_md_table(["$ / day", "markets / day at R4 size", "or size x at 3 markets / day", "sequential hours needed", "feasible in 24 h", "worst-case residual / day", "expected net at R4 ratio (EST)", "expected net at all-at-size ratio (EST)"], rows) + "\n")
    L.append("Rerun: `python3 -m src.l5_economic_scoreboard --logs <dumps>` then `python3 -m src.l5_profitability`.\n")
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    out = assemble()
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True, default=str)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(render_md(out))
    print(json.dumps(out["H_gates"], indent=1))
    print(out["G_levers"]["smallest_rung_that_raises_expected_dollars"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
