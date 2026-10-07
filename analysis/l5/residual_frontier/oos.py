"""OUT-OF-SAMPLE CONFIRMATION of the residual-efficiency frontier.

Protocol RESIDUAL-EFFICIENCY-FRONTIER-LANE-B, spec frozen at
`spec.FRONTIER_FINGERPRINT`. This module runs the EXISTING 30 cells,
unchanged, on populations the spec has never seen. It is a CONFIRMATION run:

  * `spec.py` is not imported for anything but reading, and
    `spec.assert_frozen()` is called before any data is touched;
  * no cell is added, removed, retuned or rescued;
  * `replay.py` is reused as-is -- `replay.build_events` and `replay.run` are
    called on adapter-materialized inputs, so the replay's semantics cannot
    bend to fit the holdout.

HISTORICAL REPLAY ONLY. Every dollar is a counterfactual modification of
another trader's public Polymarket activity. Not shadow, not a probe, not
submitted, not filled, not settled, NEVER REALIZED. L5's cash is not our
profit. Rebates and rewards are exactly $0 by construction.

PRE-COMMITTED TESTS (FRONTIER.md Section 9, written before these files were
read):
  T1  `ABS100_EXTEND_ONLY`      -- does the SIGN and the EXCHANGE RATE survive?
  T2  `TTE120x0.25_EXTEND_ONLY` -- same.
  T3  `ABS200_EXTEND_ONLY`      -- does its E3-negative single-stratum failure
                                   repeat?
Net dollars are NOT the test.

SECOND TASK: L5's own ex-incentive economics with settlement accounted
properly, reported with `SETTLEMENT_RESOLVED` and `SETTLEMENT_UNRESOLVED`
kept apart, plus the REDEEM winner-resolver validation and an explicit test
of the self-selection confound.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import statistics as stx
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.residual_frontier import oos_adapter as ad
from analysis.l5.residual_frontier import replay, spec

#: The two candidates whose signs and exchange rates are the pre-committed
#: test, and the pre-committed single-stratum failure to re-check.
PRECOMMITTED_CANDIDATES: Tuple[str, ...] = (
    "ABS100_EXTEND_ONLY", "TTE120x0.25_EXTEND_ONLY")
PRECOMMITTED_FAILURE_CELL = "ABS200_EXTEND_ONLY"

IN_SAMPLE_JSON = "analysis/l5/residual_frontier/FRONTIER.json"
MARKETS = "data/l5/raw/markets.jsonl"


# ------------------------------------------------- independent resolver

def independent_winners(path: str = MARKETS) -> Dict[str, int]:
    """conditionId -> winning outcomeIndex, from market `outcomePrices`.

    This is an INDEPENDENT resolver: it does not look at whether L5 holds a
    redeemable position, so it is the only way to test whether having a
    REDEEM record is correlated with the outcome. Coverage is partial and
    reported as such.
    """
    out: Dict[str, int] = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            env = json.loads(line)
        except json.JSONDecodeError:
            continue
        for m in (env.get("payload") or []):
            if not isinstance(m, dict):
                continue
            op = m.get("outcomePrices")
            cid = m.get("conditionId")
            if not (op and cid):
                continue
            try:
                vals = json.loads(op) if isinstance(op, str) else op
                vals = [float(x) for x in vals]
            except Exception:
                continue
            if len(vals) == 2 and abs(sum(vals) - 1.0) < 1e-6:
                out[cid] = 0 if vals[0] > vals[1] else 1
    return out


# ---------------------------------------- resolver validation + settlement

def _raw_by_slug(recs: List[Dict[str, Any]], keep: set
                 ) -> Dict[str, Dict[str, Any]]:
    d: Dict[str, Dict[str, Any]] = collections.defaultdict(
        lambda: {"redeem_cash": 0.0, "redeem_idx": set(), "n_redeem": 0,
                 "cid": None})
    for r in recs:
        slug = r.get("eventSlug") or r.get("conditionId") or ""
        if slug not in keep:
            continue
        e = d[slug]
        if e["cid"] is None and r.get("conditionId"):
            e["cid"] = r["conditionId"]
        if r.get("type") == "REDEEM":
            e["redeem_cash"] += float(r.get("usdcSize") or 0.0)
            e["redeem_idx"].add(r.get("outcomeIndex"))
            e["n_redeem"] += 1
    return d


def settlement_block(events: List[replay._Event],
                     recs: List[Dict[str, Any]],
                     indep: Dict[str, int]) -> Dict[str, Any]:
    """L5's OWN ex-incentive economics with settlement accounted properly.

    Modelled payout: a held complete set redeems $1.00/share regardless of
    outcome (so matched shares are NEVER booked at $0 for want of a REDEEM
    record), and the residual pays $1.00/share iff its side is the resolved
    winner. This is `replay`'s BASELINE valuation, reported here per event so
    the two settlement strata are never averaged together.
    """
    keep = {e.slug for e in events}
    raw = _raw_by_slug(recs, keep)

    rows: List[Dict[str, Any]] = []
    for ev in events:
        up = dn = cu = cd = fee = turn = 0.0
        for f in ev.fills:
            if f.is_up:
                up += f.size
                cu += f.price * f.size
            else:
                dn += f.size
                cd += f.price * f.size
            fee += max(0.0, f.usdc - f.price * f.size)
            turn += f.usdc
        matched = min(up, dn)
        vwu = cu / up if up else 0.0
        vwd = cd / dn if dn else 0.0
        matched_usd = matched * (1.0 - (vwu + vwd)) if matched > 0 else 0.0
        resid = up - dn
        r_side = 0 if resid > 0 else 1
        vwap = vwu if (r_side == 0 and up) else (vwd if (r_side == 1 and dn)
                                                 else 0.0)
        r_cost = vwap * abs(resid)
        win = ev.winner_index
        r_payout = abs(resid) if (win is not None and r_side == win) else 0.0
        rr = raw.get(ev.slug, {})
        modelled_total_payout = matched * 1.0 + r_payout
        rows.append({
            "slug": ev.slug, "day": ev.day, "era": ev.era,
            "has_redeem": win is not None,
            "redeem_winner": win,
            "n_redeem": rr.get("n_redeem", 0),
            "redeem_cash": round(rr.get("redeem_cash", 0.0), 6),
            "matched_shares": matched,
            "one_sided": matched == 0.0,
            "matched_usd": matched_usd,
            "residual_shares": abs(resid),
            "residual_side": r_side,
            "residual_usd": r_payout - r_cost,
            "fees_usd": fee, "turnover_usd": turn,
            "net_usd": matched_usd + (r_payout - r_cost) - fee,
            # audit-style valuation: settlement == OBSERVED redeem cash only
            "audit_net_usd": (rr.get("redeem_cash", 0.0)
                              - (cu + cd + fee)),
            "modelled_total_payout": modelled_total_payout,
            "indep_winner": indep.get(rr.get("cid") or ""),
        })

    # ---- resolver validation, exactly as in-sample ----
    val = [r for r in rows if r["has_redeem"]]
    diffs = [abs(r["redeem_cash"] - r["modelled_total_payout"]) for r in val]
    # complement check: would paying the OTHER side fit the observed cash
    # better? `flip` keeps the matched set (which pays $1 either way) and
    # moves the residual payout to the opposite outcome.
    comp = 0
    for r in val:
        here = abs(r["redeem_cash"] - r["modelled_total_payout"])
        flip = r["matched_shares"] + (
            r["residual_shares"] if r["residual_side"] != r["redeem_winner"]
            else 0.0)
        if here <= abs(r["redeem_cash"] - flip):
            comp += 1
    resolver = {
        "n_events_with_redeem": len(val),
        "median_abs_redeem_minus_modelled_usd": (
            round(stx.median(diffs), 6) if diffs else None),
        "mean_abs_redeem_minus_modelled_usd": (
            round(sum(diffs) / len(diffs), 6) if diffs else None),
        "exact_rate": (round(sum(1 for d in diffs if d < 1e-6) / len(diffs), 4)
                       if diffs else None),
        "closer_than_complement_rate": (round(comp / len(val), 4)
                                        if val else None),
        "p90_abs_diff_usd": (round(sorted(diffs)[int(0.90 * len(diffs))], 4)
                             if diffs else None),
    }

    def agg(sub: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not sub:
            return {"events": 0}
        return {
            "events": len(sub),
            "matched_usd": round(sum(r["matched_usd"] for r in sub), 2),
            "residual_usd": round(sum(r["residual_usd"] for r in sub), 2),
            "fees_usd": round(sum(r["fees_usd"] for r in sub), 2),
            "net_ex_incentives_usd": round(sum(r["net_usd"] for r in sub), 2),
            "audit_net_usd": round(sum(r["audit_net_usd"] for r in sub), 2),
            "turnover_usd": round(sum(r["turnover_usd"] for r in sub), 2),
            "one_sided_events": sum(1 for r in sub if r["one_sided"]),
            "matched_shares": round(sum(r["matched_shares"] for r in sub), 2),
            "median_event_net_usd": round(
                stx.median([r["net_usd"] for r in sub]), 4),
            "win_rate_event_net_positive": round(
                sum(1 for r in sub if r["net_usd"] > 0) / len(sub), 4),
        }

    resolved = [r for r in rows if r["has_redeem"]]
    unresolved = [r for r in rows if not r["has_redeem"]]
    unres_matched = [r for r in unresolved if not r["one_sided"]]

    # ---- the self-selection confound, tested directly ----
    # THE QUESTION. `SETTLEMENT_RESOLVED` is defined by "L5 has a REDEEM
    # record for this event". On Polymarket a REDEEM exists only if there was
    # something redeemable: a winning share or a complete set. So for a
    # ONE-SIDED position the presence of a REDEEM record is LOGICALLY
    # EQUIVALENT to having won, and the stratum cannot be treated as a random
    # sample of events. This block measures that directly with an INDEPENDENT
    # resolver (market `outcomePrices`), which does not look at L5's holdings.
    cov = [r for r in rows if r["indep_winner"] is not None]
    cov_redeem = [r for r in cov if r["has_redeem"]]
    cov_no = [r for r in cov if not r["has_redeem"]]
    one_no = [r for r in cov_no if r["one_sided"]]
    one_no_lost = [r for r in one_no
                   if r["residual_side"] != r["indep_winner"]]
    one_no_won = [r for r in one_no
                  if r["residual_side"] == r["indep_winner"]]
    one_re = [r for r in cov_redeem if r["one_sided"]]
    one_re_won = [r for r in one_re
                  if r["residual_side"] == r["indep_winner"]]
    confound = {
        "independent_resolver": (
            "data/l5/raw/markets.jsonl `outcomePrices`, which does not "
            "depend on whether L5 held a redeemable position"),
        "events_with_independent_resolution": len(cov),
        "independent_coverage_frac": (round(len(cov) / len(rows), 4)
                                      if rows else None),
        "independent_coverage_of_redeem_backed_events": len(cov_redeem),
        "independent_coverage_of_no_redeem_events": len(cov_no),
        "COVERAGE_CAVEAT": (
            "the independent resolver's coverage is NOT random: it was "
            "harvested for the no-REDEEM events, so it covers almost none "
            "of the REDEEM-backed stratum. P(REDEEM | won) therefore cannot "
            "be estimated on this data; the one-sided no-REDEEM direction "
            "below CAN be, and it is the decisive one."),
        # the decisive statistic: among one-sided events with NO redeem
        # record, did L5's side actually lose?
        "one_sided_no_redeem_events_covered": len(one_no),
        "frac_one_sided_no_redeem_that_LOST": (
            round(len(one_no_lost) / len(one_no), 4) if one_no else None),
        "n_one_sided_no_redeem_LOST": len(one_no_lost),
        "n_one_sided_no_redeem_WON_anyway": len(one_no_won),
        "usd_cost_of_one_sided_no_redeem_WON_anyway": round(
            sum(r["residual_shares"] for r in one_no_won), 2),
        # and the other direction where coverage allows it
        "one_sided_redeem_backed_events_covered": len(one_re),
        "frac_one_sided_redeem_backed_that_WON": (
            round(len(one_re_won) / len(one_re), 4) if one_re else None),
        "agreement_redeem_winner_vs_independent": (
            round(sum(1 for r in cov_redeem
                      if r["redeem_winner"] == r["indep_winner"])
                  / len(cov_redeem), 4) if cov_redeem else None),
        "SELF_SELECTION_CONFIRMED": (
            bool(one_no) and (len(one_no_lost) / len(one_no)) > 0.90),
        "WHAT_THAT_MEANS": (
            "if ~all one-sided no-REDEEM events are events L5 LOST, then "
            "`SETTLEMENT_RESOLVED` is self-selecting: it conditions on not "
            "having lost outright. Its net is a CONDITIONAL quantity and is "
            "NOT an estimate of L5's unconditional economics. The only "
            "unbiased figure is FULL_POPULATION, which pools both strata."),
    }

    return {
        "resolver_validation": resolver,
        "strata": {
            "FULL_POPULATION": agg(rows),
            "SETTLEMENT_RESOLVED_redeem_backed": agg(resolved),
            "SETTLEMENT_UNRESOLVED_no_redeem": agg(unresolved),
            "SETTLEMENT_UNRESOLVED_but_holding_matched_shares": agg(
                unres_matched),
        },
        "guaranteed_but_absent_redemption_usd": round(
            sum(r["matched_shares"] for r in unres_matched), 2),
        "modelled_minus_audit_usd": round(
            sum(r["net_usd"] - r["audit_net_usd"] for r in rows), 2),
        "self_selection_confound": confound,
        "per_era_net_ex_incentives_usd": {
            e: round(sum(r["net_usd"] for r in rows if r["era"] == e), 2)
            for e in sorted({r["era"] for r in rows})},
        "observed_days": len({r["day"] for r in rows}),
        "first_day": min(r["day"] for r in rows) if rows else None,
        "last_day": max(r["day"] for r in rows) if rows else None,
    }


# ------------------------------------------------------------- the run

def run_population(name: str, recs: List[Dict[str, Any]], outdir: str,
                   exclude: set, indep: Dict[str, int]) -> Dict[str, Any]:
    mat = ad.materialize(recs, outdir, name, exclude)
    events = replay.build_events(activity=mat["activity_path"],
                                 q1_events=mat["population_path"])
    frontier = replay.run(events)
    sett = settlement_block(events, ad._dedupe(recs), indep)
    return {"materialization": mat, "frontier": frontier,
            "settlement": sett,
            "coverage": {
                "n_events": len(events),
                "n_fills": sum(len(e.fills) for e in events),
                "observed_days": sett["observed_days"],
                "first_day": sett["first_day"],
                "last_day": sett["last_day"],
                "NOTE_events_per_observed_day": (
                    "replay.OBSERVED_DAYS is frozen at 22 (the in-sample "
                    "day count) and was NOT changed, so the metric "
                    "`events_per_observed_day` in the frontier block is on "
                    "the in-sample denominator and is NOT comparable here. "
                    "The holdout's own day count is `observed_days` above."),
            }}


def verdicts(oos: Dict[str, Any], ins: Dict[str, Any]) -> Dict[str, Any]:
    """Judge the pre-committed tests in their pre-committed terms."""
    out: Dict[str, Any] = {}
    for cid in PRECOMMITTED_CANDIDATES + (PRECOMMITTED_FAILURE_CELL,):
        i = ins["cells"][cid]
        o = oos["cells"][cid]
        ifull = i["strata"]["FULL_CORPUS"]
        ofull = o["strata"]["FULL_CORPUS"]
        i_net = ifull["net_cash_ex_incentives_usd"]
        o_net = ofull["net_cash_ex_incentives_usd"]
        i_xr = i["DERIVED_avoided_per_matched_sacrificed"]
        o_xr = o["DERIVED_avoided_per_matched_sacrificed"]
        i_imp = i["DERIVED_net_improvement_vs_baseline_usd"]
        o_imp = o["DERIVED_net_improvement_vs_baseline_usd"]
        out[cid] = {
            "in_sample_net_usd": i_net, "oos_net_usd": o_net,
            "in_sample_sign": _sign(i_net), "oos_sign": _sign(o_net),
            "SIGN_SURVIVED": _sign(i_net) == _sign(o_net),
            "in_sample_exchange_rate": i_xr, "oos_exchange_rate": o_xr,
            "exchange_rate_delta": (round(o_xr - i_xr, 4)
                                    if (i_xr is not None
                                        and o_xr is not None) else None),
            "EXCHANGE_RATE_ABOVE_1_IN_SAMPLE": (i_xr or 0) > 1.0,
            "EXCHANGE_RATE_ABOVE_1_OOS": (o_xr or 0) > 1.0,
            "EXCHANGE_RATE_SURVIVED": (
                ((i_xr or 0) > 1.0) == ((o_xr or 0) > 1.0)),
            "in_sample_net_improvement_vs_baseline_usd": i_imp,
            "oos_net_improvement_vs_baseline_usd": o_imp,
            "in_sample_era_nets": {
                k: v["net_cash_ex_incentives_usd"]
                for k, v in i["strata"].items() if k.startswith("E")},
            "oos_era_nets": {
                k: v["net_cash_ex_incentives_usd"]
                for k, v in o["strata"].items() if k.startswith("E")},
        }
    return out


def _sign(x: float) -> str:
    return "+" if x > 0 else ("-" if x < 0 else "0")


def dead_cells(res: Dict[str, Any]) -> List[str]:
    return sorted(cid for cid, c in res["cells"].items()
                  if c["strata"]["FULL_CORPUS"]["events_touched"] == 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", default=os.environ.get(
        "OOS_SCRATCH", "/tmp/claude-0/-home-user-kalshi-arb-bot/"
                       "6097e3a7-6e63-5966-9551-0d9d2f12d9cc/scratchpad/oos"))
    ap.add_argument("--out", default="analysis/l5/residual_frontier/"
                                     "OOS_RESULT.json")
    a = ap.parse_args(argv)

    # RULE 1: abort before touching any data if the spec is not the frozen one.
    spec.assert_frozen()

    ins = json.load(open(IN_SAMPLE_JSON, encoding="utf-8"))
    if ins["fingerprint"] != spec.FRONTIER_FINGERPRINT:
        raise SystemExit("in-sample result is not from the frozen spec")

    indep = independent_winners()
    leak = ad.in_sample_slugs()

    prim_recs = ad.read_primary()
    sec_recs = ad.read_secondary()
    prim = run_population("primary", prim_recs, a.scratch, leak, indep)
    # the 1 slug shared with PRIMARY goes to PRIMARY
    sec_exclude = set(leak) | {
        json.loads(l)["slug"]
        for l in open(prim["materialization"]["population_path"],
                      encoding="utf-8") if l.strip()}
    sec = run_population("secondary", sec_recs, a.scratch, sec_exclude, indep)

    out = {
        "protocol": spec.PROTOCOL + " / OUT-OF-SAMPLE CONFIRMATION",
        "spec_fingerprint": spec.FRONTIER_FINGERPRINT,
        "spec_fingerprint_recomputed": spec.fingerprint(),
        "spec_unmodified": spec.fingerprint() == spec.FRONTIER_FINGERPRINT,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profit_label": "historical replay",
        "NOT_OUR_REALIZED_PROFIT": (
            "a counterfactual modification of another trader's public "
            "activity. Not shadow, not submitted, not filled, not settled, "
            "never realized."),
        "rebates_rewards_usd": 0.0,
        "is_confirmation_run_not_discovery": True,
        "nothing_tuned": (
            "the 30 frozen cells were run unchanged. No cell added, removed, "
            "retuned or rescued. No threshold re-chosen."),
        "precommitted_tests": {
            "T1": "ABS100_EXTEND_ONLY sign + exchange rate",
            "T2": "TTE120x0.25_EXTEND_ONLY sign + exchange rate",
            "T3": ("ABS200_EXTEND_ONLY E3-negative single-stratum failure "
                   "repeats?"),
            "net_dollars_are_not_the_test": True,
        },
        "populations": {
            "PRIMARY": {"source": ad.PRIMARY, **prim["coverage"],
                        "materialization": prim["materialization"]},
            "SECONDARY": {"source": ad.SECONDARY, **sec["coverage"],
                          "materialization": sec["materialization"]},
        },
        "leak_guard": {
            "in_sample_population": ad.IN_SAMPLE_POPULATION,
            "in_sample_slugs": len(leak),
            "primary_slugs_excluded_as_leak":
                prim["materialization"]["slugs_excluded_as_leak"],
            "secondary_slugs_excluded_as_leak":
                sec["materialization"]["slugs_excluded_as_leak"],
        },
        "IN_SAMPLE_REFERENCE": {
            "path": IN_SAMPLE_JSON,
            "n_events": ins["n_events"], "n_fills": ins["n_fills"],
            "cells": {cid: {
                "net_cash_ex_incentives_usd":
                    c["strata"]["FULL_CORPUS"]["net_cash_ex_incentives_usd"],
                "exchange_rate":
                    c["DERIVED_avoided_per_matched_sacrificed"],
                "turnover_retained_frac":
                    c["strata"]["FULL_CORPUS"]["turnover_retained_frac"],
                "suppressed_action_frac":
                    c["strata"]["FULL_CORPUS"]["suppressed_action_frac"],
                "events_touched":
                    c["strata"]["FULL_CORPUS"]["events_touched"],
            } for cid, c in ins["cells"].items()},
        },
        "PRIMARY_frontier": prim["frontier"],
        "SECONDARY_frontier": sec["frontier"],
        "PRIMARY_settlement": prim["settlement"],
        "SECONDARY_settlement": sec["settlement"],
        "VERDICTS_PRIMARY": verdicts(prim["frontier"], ins),
        "VERDICTS_SECONDARY": verdicts(sec["frontier"], ins),
        "DEAD_CELLS": {
            "in_sample": dead_cells(ins),
            "primary": dead_cells(prim["frontier"]),
            "secondary": dead_cells(sec["frontier"]),
            "note": ("dead by construction = took zero fills. Reported as "
                     "dead, never rescued."),
        },
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written %s" % a.out)
    for pop in ("PRIMARY", "SECONDARY"):
        p = out["populations"][pop]
        print("%s: %d events, %d fills, %d days %s..%s"
              % (pop, p["n_events"], p["n_fills"], p["observed_days"],
                 p["first_day"], p["last_day"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
