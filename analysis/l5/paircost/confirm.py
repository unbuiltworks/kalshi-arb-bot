"""Running pair-cost control -- CONFIRMATION RUN.

Executes frozen protocol 118571f3 / fingerprint e9089554...3b10 exactly.
Every threshold is READ FROM THE FROZEN SPEC, never restated here, so a
value cannot drift between protocol and run.

SELECTION_EDGE and CF0 are decisive. No threshold is tuned.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book
from analysis.l5.microstructure.clips import build_clips
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id
from analysis.l5.microstructure.stats import cliffs_delta, qs, spearman
from analysis.l5.paircost.spec import SPEC, PAIRCOST_FINGERPRINT, assert_frozen

WINDOW_S = 900
GAP_S = 10
T = SPEC["target"]["TARGET_COST"]
BAND = tuple(SPEC["target"]["band_primary"])
BANDS2 = [tuple(b) for b in SPEC["target"]["bands_secondary_preregistered"]]
EDGE_BAR = SPEC["discriminating_test"]["bar"]
BUD_BAR = SPEC["budget_test"]["bar"]
BINS = SPEC["frozen_bins"]
MIN_N = BINS["min_n_per_cell"]
FEE_K = 0.07          # model-implied 0.07*p*(1-p), kept on its own line


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def sn(bk: Optional[Book], t: float) -> Optional[dict]:
    return bk.at(float(t), max_age_s=MAX_CARRY_S) if bk else None


# ------------------------------------------------------------------ build
def acquisitions(slug, seq, books, tokens):
    start = int(slug.rsplit("-", 1)[1])
    tok = {str(o).lower(): t for t, o in tokens.items()}
    pairs, member = find_pairs(seq)
    passive = [r for r in seq if _id(r) not in member]
    clips = sorted(build_clips(passive, books, gap_s=GAP_S, slug=slug,
                               clen="15m", start=start),
                   key=lambda c: c["t_first"])
    ev = [{"t": c["t_first"], "outcome": c["outcome"], "role": "maker",
           "shares": c["clip_total_shares"], "price": c["price"],
           "is_par_pair": False} for c in clips]
    for p in pairs:
        for leg in ("up", "down"):
            r = p[leg]
            ev.append({"t": r["t"], "outcome": leg, "role": "taker",
                       "shares": r["quantity"], "price": r["price"],
                       "is_par_pair": True})
    ev.sort(key=lambda e: e["t"])
    return ev, tok, start


def running_series(ev: List[dict], conv: str) -> List[dict]:
    """Running matched pair cost under one attribution convention.

    C3_VWAP_POOLED (primary): matched shares carry each side's running VWAP.
    C1_FIFO / C2_LIFO: matched shares consume lots from the front / back.
    """
    sh = {"up": 0.0, "down": 0.0}
    dl = {"up": 0.0, "down": 0.0}
    lots: Dict[str, List[dict]] = {"up": [], "down": []}
    m_qty = 0.0
    m_cost = 0.0
    out = []
    for e in ev:
        oc = e["outcome"]
        other = "down" if oc == "up" else "up"
        sh[oc] += e["shares"]
        dl[oc] += e["shares"] * e["price"]
        if conv == "C3_VWAP_POOLED":
            matched = min(sh["up"], sh["down"])
            vu = dl["up"] / sh["up"] if sh["up"] else 0.0
            vd = dl["down"] / sh["down"] if sh["down"] else 0.0
            m_qty = matched
            m_cost = matched * (vu + vd)
        else:
            lots[oc].append({"q": e["shares"], "p": e["price"]})
            while lots[oc] and lots[other]:
                ia = 0 if conv == "C1_FIFO" else -1
                a, b = lots[oc][ia], lots[other][ia]
                take = min(a["q"], b["q"])
                if take <= 1e-9:
                    break
                m_qty += take
                m_cost += take * (a["p"] + b["p"])
                a["q"] -= take
                b["q"] -= take
                if a["q"] <= 1e-9:
                    lots[oc].pop(ia)
                if b["q"] <= 1e-9:
                    lots[other].pop(ia)
        out.append({
            "t": e["t"], "outcome": oc, "role": e["role"],
            "is_par_pair": e["is_par_pair"],
            "price": e["price"], "shares": e["shares"],
            "matched_qty": round(m_qty, 6),
            "running_pair_cost": (round(m_cost / m_qty, 6) if m_qty > 1e-9
                                  else None),
            "shares_up": round(sh["up"], 4), "shares_down": round(sh["down"], 4),
            "residual": round(abs(sh["up"] - sh["down"]), 4),
            "fee_on_this_action": round(
                FEE_K * e["price"] * (1 - e["price"]) * e["shares"], 6),
        })
    return out


# ------------------------------------------------- F1-F4 band behaviour
def band_tests(ser: List[dict], band) -> Dict[str, Any]:
    rows = [r for r in ser if r["running_pair_cost"] is not None]
    if not rows:
        return {"VERDICT": "NO_MATCHED_INVENTORY"}
    lo, hi = band
    t0 = rows[0]["t"]
    first_in = next((r for r in rows if lo <= r["running_pair_cost"] <= hi),
                    None)
    if first_in is None:
        return {"entered_band": False,
                "max_excursion": round(max(
                    abs(r["running_pair_cost"] - T) for r in rows), 6)}
    after = [r for r in rows if r["t"] >= first_in["t"]]
    inside = [r for r in after if lo <= r["running_pair_cost"] <= hi]
    tot_m = after[-1]["matched_qty"] - after[0]["matched_qty"]
    in_m = 0.0
    prev = after[0]
    span = 0.0
    in_span = 0.0
    for r in after[1:]:
        dq = r["matched_qty"] - prev["matched_qty"]
        dt = r["t"] - prev["t"]
        span += dt
        if lo <= prev["running_pair_cost"] <= hi:
            in_m += max(0.0, dq)
            in_span += dt
        prev = r
    # excursions and returns
    exc, cur = [], None
    for r in after:
        out_of = not (lo <= r["running_pair_cost"] <= hi)
        if out_of and cur is None:
            cur = {"start_t": r["t"], "peak": abs(r["running_pair_cost"] - T)}
        elif out_of:
            cur["peak"] = max(cur["peak"], abs(r["running_pair_cost"] - T))
        elif cur is not None:
            cur["end_t"] = r["t"]
            cur["returned"] = True
            exc.append(cur)
            cur = None
    if cur is not None:
        cur["returned"] = False
        exc.append(cur)
    return {
        "entered_band": True,
        "F1_matched_shares_to_first_entry": round(first_in["matched_qty"], 4),
        "F1_seconds_to_first_entry": int(first_in["t"] - t0),
        "F2_fraction_matched_inventory_inside": (
            round(in_m / tot_m, 4) if tot_m > 1e-9 else None),
        "F2_fraction_time_inside": (round(in_span / span, 4) if span else None),
        "F3_max_excursion_from_target": round(max(
            abs(r["running_pair_cost"] - T) for r in after), 6),
        "F4_n_excursions": len(exc),
        "F4_n_returned": sum(1 for e in exc if e["returned"]),
        "F4_all_returned": all(e["returned"] for e in exc) if exc else True,
    }


# ------------------------------------------- the decisive SELECTION_EDGE
def selection_edge(ev, tok, books, start, view: str,
                   exclude_par: bool) -> Dict[str, Any]:
    """time-weighted available complement sum  -  volume-weighted realised.

    `view`: 'taker' uses best asks (what it costs to take both sides);
            'maker' uses best bids (what a resting pair would cost).
    """
    key = "best_ask" if view == "taker" else "best_bid"
    bu, bd = books.get(tok["up"]), books.get(tok["down"])
    # COMMON SUPPORT. Both views must average over the SAME seconds, else
    # each is a mean over its own subsample and the comparison between them
    # is meaningless -- which showed up as ask_sum < bid_sum in two windows,
    # arithmetically impossible for a real book. A second counts only when
    # all four quantities (bid and ask, both tokens) are present.
    sums = []
    for s in range(WINDOW_S):
        t = start + s
        a, b = sn(bu, t), sn(bd, t)
        if not a or not b:
            continue
        if any(a.get(k) is None or b.get(k) is None
               for k in ("best_bid", "best_ask")):
            continue
        sums.append(float(a[key]) + float(b[key]))
    if len(sums) < 60:
        return {"VERDICT": "INSUFFICIENT_BOOK_COVERAGE", "n_seconds": len(sums)}

    use = [e for e in ev if not (exclude_par and e["is_par_pair"])]
    # realised pair cost, volume weighted: pair the matched shares by each
    # side's VWAP (the primary C3 convention), which is what the running
    # series reports.
    sh = {"up": 0.0, "down": 0.0}
    dl = {"up": 0.0, "down": 0.0}
    for e in use:
        sh[e["outcome"]] += e["shares"]
        dl[e["outcome"]] += e["shares"] * e["price"]
    if not (sh["up"] and sh["down"]):
        return {"VERDICT": "ONE_SIDED_ONLY"}
    realised = dl["up"] / sh["up"] + dl["down"] / sh["down"]
    tw = sum(sums) / len(sums)

    # volume-weighted available sum AT L5's OWN action times: separates
    # "when he traded the sum was low" from "he paid less than the sum".
    at_action = []
    wts = []
    for e in use:
        a, b = sn(bu, e["t"] - 1), sn(bd, e["t"] - 1)
        if not a or not b:
            continue
        if any(a.get(k) is None or b.get(k) is None
               for k in ("best_bid", "best_ask")):
            continue
        at_action.append(float(a[key]) + float(b[key]))
        wts.append(e["shares"])
    vw_avail = (sum(x * w for x, w in zip(at_action, wts)) / sum(wts)
                if wts else None)
    return {
        "view": view,
        "n_seconds_with_both_books": len(sums),
        "time_weighted_available_sum": round(tw, 6),
        "volume_weighted_available_sum_at_L5_action_times": (
            round(vw_avail, 6) if vw_avail is not None else None),
        "L5_volume_weighted_realised_pair_cost": round(realised, 6),
        "SELECTION_EDGE": round(tw - realised, 6),
        "TIMING_COMPONENT_tw_minus_vwAvail": (
            round(tw - vw_avail, 6) if vw_avail is not None else None),
        "EXECUTION_COMPONENT_vwAvail_minus_realised": (
            round(vw_avail - realised, 6) if vw_avail is not None else None),
    }


# ------------------------------------------------------------ CF0 and co
def counterfactuals(ev, tok, books, start, rng: random.Random
                    ) -> Dict[str, Any]:
    """Each controller must reproduce BOTH accumulation and cost trajectory."""
    bu, bd = books.get(tok["up"]), books.get(tok["down"])
    l5 = running_series(ev, "C3_VWAP_POOLED")
    l5_final_matched = l5[-1]["matched_qty"] if l5 else 0.0
    l5_final_cost = next((r["running_pair_cost"] for r in reversed(l5)
                          if r["running_pair_cost"] is not None), None)
    l5_curve = [(r["t"] - start, r["running_pair_cost"]) for r in l5
                if r["running_pair_cost"] is not None]

    def score(ser) -> Dict[str, Any]:
        c = [(r["t"] - start, r["running_pair_cost"]) for r in ser
             if r["running_pair_cost"] is not None]
        fm = ser[-1]["matched_qty"] if ser else 0.0
        fc = next((r["running_pair_cost"] for r in reversed(ser)
                   if r["running_pair_cost"] is not None), None)
        # cost-trajectory L1 on the common time grid
        d = dict(c)
        errs = [abs(v - d[k]) for k, v in l5_curve if k in d]
        return {
            "final_matched": round(fm, 4),
            "final_running_pair_cost": fc,
            "accumulation_ratio_vs_L5": (round(fm / l5_final_matched, 4)
                                         if l5_final_matched else None),
            "cost_trajectory_L1_vs_L5": (round(sum(errs) / len(errs), 6)
                                         if errs else None),
            "n_compared_points": len(errs),
        }

    out = {"L5": {"final_matched": round(l5_final_matched, 4),
                  "final_running_pair_cost": l5_final_cost}}

    # CF0 NULL_M: no control at all. Take both sides at the touch at the
    # SAME instants L5 acted, same total shares, split evenly.
    cf0 = []
    for e in ev:
        for side in ("up", "down"):
            bk = bu if side == "up" else bd
            s = sn(bk, e["t"] - 1)
            px = (s or {}).get("best_ask")
            if px is None:
                continue
            cf0.append({"t": e["t"], "outcome": side, "role": "taker",
                        "shares": e["shares"] / 2.0, "price": float(px),
                        "is_par_pair": False})
    cf0.sort(key=lambda x: x["t"])
    out["CF0_NULL_M_touch_taker_no_control"] = (
        score(running_series(cf0, "C3_VWAP_POOLED")) if cf0
        else {"VERDICT": "NO_BOOK"})

    # CF1 always lighter side, L5's own prices/sizes/times
    def resequence(choose):
        sh = {"up": 0.0, "down": 0.0}
        seq2 = []
        for e in ev:
            side = choose(sh, e)
            bk = bu if side == "up" else bd
            s = sn(bk, e["t"] - 1)
            px = (s or {}).get("best_bid")
            if px is None:
                px = e["price"]
            sh[side] += e["shares"]
            seq2.append({"t": e["t"], "outcome": side, "role": e["role"],
                         "shares": e["shares"], "price": float(px),
                         "is_par_pair": False})
        return seq2

    out["CF1_always_lighter_side"] = score(running_series(
        resequence(lambda sh, e: "up" if sh["up"] <= sh["down"] else "down"),
        "C3_VWAP_POOLED"))
    out["CF4_equal_share_controller"] = out["CF1_always_lighter_side"]

    # CF2 fixed per-pair ceiling / CF3 fixed per-action reservation:
    # drop any action violating the rule, keep everything else as L5 had it
    def filt(pred):
        return [e for e in ev if pred(e)]
    out["CF2_fixed_per_pair_ceiling"] = score(running_series(
        filt(lambda e: True), "C3_VWAP_POOLED"))  # see note below
    cf2 = []
    sh = {"up": 0.0, "down": 0.0}
    dl = {"up": 0.0, "down": 0.0}
    for e in ev:
        oc, other = e["outcome"], ("down" if e["outcome"] == "up" else "up")
        vo = dl[other] / sh[other] if sh[other] else None
        if vo is not None and (e["price"] + vo) > BAND[1]:
            continue
        sh[oc] += e["shares"]
        dl[oc] += e["shares"] * e["price"]
        cf2.append(e)
    out["CF2_fixed_per_pair_ceiling"] = score(
        running_series(cf2, "C3_VWAP_POOLED")) if cf2 else {"VERDICT": "EMPTY"}

    cf3 = [e for e in ev if e["price"] <= T / 2.0 or True]
    # reservation price: never pay more than TARGET minus complement touch
    cf3 = []
    for e in ev:
        other = "down" if e["outcome"] == "up" else "up"
        bk = bd if other == "down" else bu
        s = sn(bk, e["t"] - 1)
        comp = (s or {}).get("best_bid")
        if comp is not None and e["price"] > T - float(comp):
            continue
        cf3.append(e)
    out["CF3_fixed_per_action_reservation_price"] = (
        score(running_series(cf3, "C3_VWAP_POOLED")) if cf3
        else {"VERDICT": "EMPTY_every_action_violated_reservation"})

    # CF5 random permutation preserving prices and sizes
    best = None
    for _ in range(20):
        perm = list(ev)
        sides = [e["outcome"] for e in perm]
        rng.shuffle(sides)
        p2 = [dict(e, outcome=s) for e, s in zip(perm, sides)]
        sc = score(running_series(p2, "C3_VWAP_POOLED"))
        if best is None or (sc["cost_trajectory_L1_vs_L5"] or 9) < \
                (best["cost_trajectory_L1_vs_L5"] or 9):
            best = sc
    out["CF5_random_permutation_preserving_prices_and_sizes"] = best

    # CF6 running-average controller: act only if it keeps running cost <= hi
    cf6 = []
    sh = {"up": 0.0, "down": 0.0}
    dl = {"up": 0.0, "down": 0.0}
    for e in ev:
        oc = e["outcome"]
        s2 = dict(sh); d2 = dict(dl)
        s2[oc] += e["shares"]; d2[oc] += e["shares"] * e["price"]
        if s2["up"] and s2["down"]:
            rc = d2["up"] / s2["up"] + d2["down"] / s2["down"]
            if rc > BAND[1]:
                continue
        sh, dl = s2, d2
        cf6.append(e)
    out["CF6_running_average_pair_cost_controller"] = (
        score(running_series(cf6, "C3_VWAP_POOLED")) if cf6
        else {"VERDICT": "EMPTY"})
    return out


# ------------------------------------------------------- budget/headroom
def budget_test(ser: List[dict]) -> Dict[str, Any]:
    """Does cheap EXTEND saving correlate with later COMPRESS premium?"""
    sav, prem = [], []
    prev_res = 0.0
    run_sav = 0.0
    pts = []
    for r in ser:
        res = r["residual"]
        cls = ("EXTENDS" if res > prev_res + 1 else
               "COMPRESSES" if res < prev_res - 1 else "NEUTRAL")
        prev_res = res
        if cls == "EXTENDS":
            run_sav += (T / 2.0 - r["price"]) * r["shares"]
        elif cls == "COMPRESSES":
            pts.append((run_sav, (r["price"] - T / 2.0) * r["shares"]))
    if len(pts) < 10:
        return {"n": len(pts), "VERDICT": "INSUFFICIENT_COMPRESS_ACTIONS"}
    return {"n": len(pts),
            "spearman_savings_vs_subsequent_premium": spearman(
                [a for a, _ in pts], [b for _, b in pts])}


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
    rng = random.Random(20260821)

    per: Dict[str, Any] = {}
    for slug in sorted(byw):
        ev, tok, start = acquisitions(slug, byw[slug], books,
                                      wins[slug]["tokens"])
        ev_np = [e for e in ev if not e["is_par_pair"]]
        rec: Dict[str, Any] = {"n_actions": len(ev),
                               "n_actions_excl_par": len(ev_np)}
        for conv in ("C3_VWAP_POOLED", "C1_FIFO", "C2_LIFO"):
            rec["band_" + conv] = band_tests(running_series(ev, conv), BAND)
            rec["band_exclPar_" + conv] = band_tests(
                running_series(ev_np, conv), BAND)
        for b in BANDS2:
            rec["band_sensitivity_%s" % (b,)] = band_tests(
                running_series(ev, "C3_VWAP_POOLED"), b)
        for view in ("maker", "taker"):
            rec["edge_%s_inclPar" % view] = selection_edge(
                ev, tok, books, start, view, exclude_par=False)
            rec["edge_%s_exclPar" % view] = selection_edge(
                ev, tok, books, start, view, exclude_par=True)
        rec["counterfactuals"] = counterfactuals(ev, tok, books, start, rng)
        rec["budget"] = budget_test(running_series(ev, "C3_VWAP_POOLED"))
        ser = running_series(ev, "C3_VWAP_POOLED")
        rec["fees_total"] = round(sum(r["fee_on_this_action"] for r in ser), 6)
        rec["gross_acquisition_dollars"] = round(
            sum(r["price"] * r["shares"] for r in ser), 4)
        per[slug] = rec

    # ---- aggregate the frozen >=9/12 decisions ----
    def edges(view, tag):
        return {s: (v["edge_%s_%s" % (view, tag)] or {}).get("SELECTION_EDGE")
                for s, v in per.items()}

    decisions = {}
    for view in ("maker", "taker"):
        for tag in ("inclPar", "exclPar"):
            e = edges(view, tag)
            vals = [x for x in e.values() if x is not None]
            n_pass = sum(1 for x in vals if x >= EDGE_BAR["min_edge"])
            decisions["SELECTION_EDGE_%s_%s" % (view, tag)] = {
                "per_window": e,
                "n_windows_with_edge_ge_bar": n_pass,
                "required": EDGE_BAR["min_windows"],
                "of": EDGE_BAR["of_windows"],
                "median_edge": (qs(vals) or {}).get("p50"),
                "PASSES": n_pass >= EDGE_BAR["min_windows"],
            }
    bud = [per[s]["budget"].get("spearman_savings_vs_subsequent_premium", {})
           .get("rho") for s in per]
    bud = [x for x in bud if x is not None]
    decisions["BUDGET_TEST"] = {
        "per_window_rho": {s: per[s]["budget"]
                           .get("spearman_savings_vs_subsequent_premium", {})
                           .get("rho") for s in per},
        "n_windows_rho_ge_bar": sum(1 for x in bud
                                    if x >= BUD_BAR["min_rho"]),
        "required": BUD_BAR["min_windows"], "of": BUD_BAR["of_windows"],
        "median_rho": (qs(bud) or {}).get("p50"),
        "PASSES": sum(1 for x in bud if x >= BUD_BAR["min_rho"])
                  >= BUD_BAR["min_windows"],
    }

    # CF0 adequacy: does the no-control null reproduce BOTH?
    cf0 = {s: per[s]["counterfactuals"]
           .get("CF0_NULL_M_touch_taker_no_control", {}) for s in per}
    cf0_cost = [v.get("final_running_pair_cost") for v in cf0.values()
                if v.get("final_running_pair_cost") is not None]
    cf0_in_band = sum(1 for x in cf0_cost if BAND[0] <= x <= BAND[1])
    decisions["CF0_NULL_M"] = {
        "per_window": cf0,
        "n_windows_final_cost_in_band": cf0_in_band,
        "of": len(cf0_cost),
        "median_final_cost": (qs(cf0_cost) or {}).get("p50"),
        "REPRODUCES_COST_TRAJECTORY": cf0_in_band >= EDGE_BAR["min_windows"],
    }

    out = {
        "protocol": "PREREGISTRATION_PAIRCOST.md @ 118571f3",
        "spec_fingerprint": PAIRCOST_FINGERPRINT,
        "target_cost": T, "band_primary": list(BAND),
        "DECISIONS": decisions,
        "per_window": per,
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "PAIRCOST.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written PAIRCOST.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
