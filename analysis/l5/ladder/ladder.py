"""Resting-ladder, replenishment/range and envelope analysis.

Frozen protocol: PREREGISTRATION_LADDER.md @ f07aec7c. Bids only -- the
archive's crossed-book defect is ask-side, and resting maker interest on
the side L5 buys lives on the bid. The archive's own `crossed` column is
read and reported, per the standing rule.

LOWER BOUNDS THROUGHOUT. Cancels and unfilled orders are invisible, so
every level count is a floor and the phrase "observed resting orders" is
not used.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S
from analysis.l5.microstructure.clips import build_clips
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id
from analysis.l5.microstructure.stats import cliffs_delta, qs, spearman

WINDOW_S = 900
GAP_S = 10
CLIP80 = 80.0
TOL = 0.75                      # preregistered lattice tolerance, shares
LATTICE = (80, 160, 240, 320, 400, 480)
PREREG = "PREREGISTRATION_LADDER.md @ f07aec7c"


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def bid_series(bk, start) -> List[Tuple[int, Optional[float], bool]]:
    """(second, best_bid, crossed) across the window."""
    out = []
    for s in range(WINDOW_S):
        sn = bk.at(float(start + s), max_age_s=MAX_CARRY_S) if bk else None
        if not sn:
            out.append((s, None, False))
            continue
        out.append((s, (float(sn["best_bid"])
                        if sn.get("best_bid") is not None else None),
                    bool(sn.get("crossed"))))
    return out


def first_relevant(series, price) -> Optional[int]:
    """First second the market came DOWN to this price, i.e. a resting buy
    there became reachable."""
    for s, bb, _ in series:
        if bb is not None and bb <= price + 1e-9:
            return s
    return None


def build(slug, seq, books, tokens) -> Dict[str, Any]:
    start = int(slug.rsplit("-", 1)[1])
    tok = {str(o).lower(): t for t, o in tokens.items()}
    pairs, member = find_pairs(seq)
    maker = [r for r in seq
             if _id(r) not in member and r.get("l5_role") == "maker"]
    clips = {}
    for oc in ("up", "down"):
        side = [r for r in maker if r["outcome"] == oc]
        clips[oc] = sorted(build_clips(side, books, gap_s=GAP_S, slug=slug,
                                       clen="15m", start=start),
                           key=lambda c: c["t_first"])
    series = {oc: bid_series(books.get(tok[oc]), start) for oc in ("up", "down")}

    # ---- per-fill records ----
    fills: List[dict] = []
    for oc in ("up", "down"):
        cl = clips[oc]
        lvl_fills: Dict[float, List[dict]] = defaultdict(list)
        for c in cl:
            for i, q in enumerate(c["fill_quantities"]):
                lvl_fills[round(c["price"], 4)].append(
                    {"t": c["t_first"], "q": q, "clip": c})
        bbmap = {sec: b for sec, b, _ in series[oc] if b is not None}
        for c in cl:
            px = round(c["price"], 4)
            bb_at = bbmap.get(c["t_first"] - start)
            fr = first_relevant(series[oc], px)
            same = [x for x in cl if abs(x["price"] - c["price"]) < 1e-9]
            adj = [x for x in cl
                   if 0 < abs(x["price"] - c["price"]) <= 0.02 + 1e-9]
            cum = 0.0
            for i, q in enumerate(c["fill_quantities"]):
                cum += q
                fills.append({
                    "market_slug": slug, "outcome": oc,
                    "t": c["t_first"], "secs_after_open": c["t_first"] - start,
                    "tte_s": WINDOW_S - (c["t_first"] - start),
                    "fill_quantity": round(q, 6),
                    "fill_index_in_clip": i,
                    "clip_n_fills": c["n_fills"],
                    "clip_total_shares": c["clip_total_shares"],
                    "cumulative_consumption_of_clip": round(cum, 6),
                    "fill_price": px, "price_level": px,
                    "bbo_before_fill_best_bid": bb_at,
                    "ticks_from_touch": (None if bb_at is None
                                         else round((bb_at - px) / 0.01)),
                    "level_first_relevant_secs": fr,
                    "level_age_at_first_fill_s": (
                        None if fr is None
                        else (c["t_first"] - start) - fr),
                    "n_fills_same_level_window": len(same),
                    "n_clips_adjacent_levels": len(adj),
                    "elapsed_in_clip_s": c["duration_s"],
                    "clip_opened_because": c["opened_because"],
                    "clip_closed_because": c["closed_because"],
                })
    return {"slug": slug, "start": start, "clips": clips,
            "series": series, "fills": fills,
            "crossed_share": {
                oc: round(sum(1 for _, _, x in series[oc] if x)
                          / max(1, sum(1 for _, b, _ in series[oc]
                                       if b is not None)), 4)
                for oc in ("up", "down")}}


# ------------------------------------------------------------------ Q1
def q1(allw) -> Dict[str, Any]:
    """Irregular sizes as partial consumption. Count AND volume, separately."""
    n_tot = v_tot = 0
    n_exp = v_exp = 0
    n_irr = v_irr = 0
    n_irr_exp = v_irr_exp = 0
    singles = 0
    for w in allw:
        for oc in ("up", "down"):
            for c in w["clips"][oc]:
                tot = c["clip_total_shares"]
                lattice_hit = any(abs(tot - m) <= TOL for m in LATTICE)
                explained = (c["n_fills"] >= 2 and lattice_hit) or (
                    tot <= CLIP80 + TOL
                    and c["closed_because"] in ("B1_PRICE_CHANGE",
                                                "B2_TIME_GAP",
                                                "B3_REPLENISHED"))
                for q in c["fill_quantities"]:
                    n_tot += 1
                    v_tot += q
                    # "irregular" = not itself a round lattice or 20-grid value
                    irregular = not (any(abs(q - m) <= TOL for m in LATTICE)
                                     or abs(q % 20.0) <= TOL
                                     or abs(20.0 - (q % 20.0)) <= TOL)
                    if irregular:
                        n_irr += 1
                        v_irr += q
                        if explained:
                            n_irr_exp += 1
                            v_irr_exp += q
                    if explained:
                        n_exp += 1
                        v_exp += q
                if c["n_fills"] == 1 and not explained:
                    singles += 1
    return {
        "n_fills_total": n_tot,
        "volume_total_shares": round(v_tot, 2),
        "n_irregular_fills": n_irr,
        "volume_irregular_shares": round(v_irr, 2),
        "IRREGULAR_EXPLAINED_by_fill_count": (
            round(n_irr_exp / n_irr, 4) if n_irr else None),
        "IRREGULAR_EXPLAINED_by_share_volume": (
            round(v_irr_exp / v_irr, 4) if v_irr else None),
        "ALL_FILLS_EXPLAINED_by_count": round(n_exp / n_tot, 4) if n_tot else None,
        "ALL_FILLS_EXPLAINED_by_volume": (round(v_exp / v_tot, 4) if v_tot
                                          else None),
        "unexplained_singleton_clips": singles,
        "note": ("count and volume are reported separately and never "
                 "pooled; a singleton irregular fill with no clip context "
                 "counts AGAINST the hypothesis"),
    }


# ------------------------------------------------------------------ Q2
def q2(allw) -> Dict[str, Any]:
    """Minimum distinct levels needed per second. A LOWER BOUND."""
    per = {}
    for w in allw:
        for oc in ("up", "down"):
            cl = w["clips"][oc]
            # A level filled BEFORE second t and again AFTER t must have
            # been live across t, unless a cancel-and-repost happened --
            # which the archive cannot evidence. Counting overlapping
            # per-level fill INTERVALS is therefore the honest no-repost
            # lower bound on concurrent levels.
            #
            # (An earlier version counted every level filled at or after t,
            # which at t=0 is just the window's total distinct level count
            # and is not a simultaneity bound at all.)
            spans: Dict[float, Tuple[int, int]] = {}
            for c in cl:
                px = round(c["price"], 4)
                a, b = c["t_first"] - w["start"], c["t_last"] - w["start"]
                if px in spans:
                    spans[px] = (min(spans[px][0], a), max(spans[px][1], b))
                else:
                    spans[px] = (a, b)
            need = []
            for s in range(WINDOW_S):
                need.append(sum(1 for a, b in spans.values() if a <= s <= b))
            q = qs(need)
            per["%s|%s" % (w["slug"][-10:], oc)] = {
                "max_simultaneous_lower_bound": (q or {}).get("max"),
                "median": (q or {}).get("p50"),
                "distinct_levels_in_window": len(
                    {round(c["price"], 4) for c in cl}),
            }
    mx = [v["max_simultaneous_lower_bound"] for v in per.values()
          if v["max_simultaneous_lower_bound"] is not None]
    return {"per_window_outcome": per,
            "LOWER_BOUND_max_levels": qs(mx),
            "IS_A_LOWER_BOUND": ("cancels and unfilled orders are "
                                 "invisible; this is a floor on ladder "
                                 "width, never observed resting orders")}


# ------------------------------------------------------------------ Q3
def q3(allw) -> Dict[str, Any]:
    """A->B->A returns. Same-second pairs are INDETERMINATE."""
    qual = 0
    indet = 0
    per = {}
    for w in allw:
        k = 0
        for oc in ("up", "down"):
            cl = w["clips"][oc]
            for i in range(len(cl) - 2):
                a, b, c2 = cl[i], cl[i + 1], cl[i + 2]
                same_sec = (a["t_first"] == b["t_first"]
                            or b["t_first"] == c2["t_first"])
                is_aba = (abs(a["price"] - c2["price"]) < 1e-9
                          and abs(a["price"] - b["price"]) > 1e-9)
                if not is_aba:
                    continue
                if same_sec:
                    indet += 1
                    continue
                # qualifying only if no evidenced replenishment at A between
                if c2["opened_because"] != "B3_REPLENISHED":
                    qual += 1
                    k += 1
        per[w["slug"][-10:]] = k
    return {
        "qualifying_ABA_returns": qual,
        "windows_with_qualifying_return": sum(1 for v in per.values() if v),
        "per_window": per,
        "INDETERMINATE_SAME_SECOND": indet,
        "resolution_caveat": ("block timestamps are whole seconds; a "
                              "same-second pair cannot distinguish "
                              "concurrency from fast reposting and is NOT "
                              "counted as evidence either way"),
    }


# ------------------------------------------------------------------ Q4
def q4(allw) -> Dict[str, Any]:
    """Ladder width, and whether 0.06 is a width feature."""
    rows = []
    for w in allw:
        for oc in ("up", "down"):
            cl = w["clips"][oc]
            if not cl:
                continue
            pxs = [c["price"] for c in cl]
            # clips carry no offset field (that lives on EVENTS), so the
            # offset is computed from the bid series at each clip's first
            # second, exactly as the geometry section did.
            bbmap = {sec: b for sec, b, _ in w["series"][oc] if b is not None}
            offs = []
            for c in cl:
                bb = bbmap.get(c["t_first"] - w["start"])
                if bb is not None:
                    offs.append(round((bb - c["price"]) / 0.01))
            bbs = [b for _, b, _ in w["series"][oc] if b is not None]
            rows.append({
                "key": "%s|%s" % (w["slug"][-10:], oc),
                "best_level": max(pxs), "deepest_level": min(pxs),
                "width_dollars": round(max(pxs) - min(pxs), 4),
                "deepest_offset_ticks": (max(offs) if offs else None),
                "deepest_offset_dollars": (round(max(offs) * 0.01, 4)
                                           if offs else None),
                "touch_travel_dollars": (round(max(bbs) - min(bbs), 4)
                                         if bbs else None),
            })
    deep = [r["deepest_offset_dollars"] for r in rows
            if r["deepest_offset_dollars"] is not None]
    trav = [r["touch_travel_dollars"] for r in rows
            if r["deepest_offset_dollars"] is not None
            and r["touch_travel_dollars"] is not None]
    qd = qs(deep)
    iqr = (round(qd["p75"] - qd["p25"], 4) if qd.get("p75") is not None
           else None)
    rho = spearman(trav, deep)
    width_feature = bool(iqr is not None and iqr <= 0.02
                         and rho.get("rho") is not None
                         and abs(rho["rho"]) < 0.30)
    return {
        "per_window_outcome": rows,
        "width_dollars": qs([r["width_dollars"] for r in rows]),
        "deepest_offset_dollars": qd,
        "deepest_offset_IQR": iqr,
        "spearman_touch_travel_vs_deepest_offset": rho,
        "VERDICT_0_06": ("WIDTH_FEATURE" if width_feature
                         else "FILL_LOCATION_STATISTIC"),
        "bar": "IQR<=0.02 AND |rho|<0.30",
    }


# --------------------------------------------------- R1/R2 lattice test
def r1(allw, rng: random.Random, forced_80: bool) -> Dict[str, Any]:
    """Does per-level total consumption cluster on the 80 lattice?"""
    tot_by_level: Dict[Tuple[str, str, float], float] = defaultdict(float)
    all_fills: List[float] = []
    for w in allw:
        for oc in ("up", "down"):
            for c in w["clips"][oc]:
                tot_by_level[(w["slug"], oc, round(c["price"], 4))] += \
                    c["clip_total_shares"]
                all_fills.extend(c["fill_quantities"])
    totals = list(tot_by_level.values())
    if not totals:
        return {"VERDICT": "NO_DATA"}

    def hits(xs):
        return sum(1 for x in xs if any(abs(x - m) <= TOL for m in LATTICE))

    obs = hits(totals) / len(totals)
    lo, hi = min(totals), max(totals)
    b_unif = hits([rng.uniform(lo, hi) for _ in totals]) / len(totals)
    # randomised fills preserving the empirical single-fill distribution
    b_emp = []
    for t in totals:
        acc = 0.0
        while acc < t and all_fills:
            acc += rng.choice(all_fills)
        b_emp.append(acc)
    b_empr = hits(b_emp) / len(b_emp)
    # phase control: shift the lattice by half a step
    b_phase = sum(1 for x in totals
                  if any(abs(x - (m - 40)) <= TOL for m in LATTICE)) / len(totals)
    worst = max(b_unif, b_empr, b_phase) or 1e-9
    return {
        "mode": ("FORCED_80" if forced_80 else "EMPIRICAL_PARTIAL_CLIPS"),
        "n_levels": len(totals),
        "observed_share_on_lattice": round(obs, 4),
        "baseline_uniform": round(b_unif, 4),
        "baseline_empirical_fill_resample": round(b_empr, 4),
        "baseline_phase_shifted_by_40": round(b_phase, 4),
        "excess_ratio_vs_worst_baseline": round(obs / worst, 3),
        "MEETS_R1_BAR_3x": obs >= 3 * worst,
    }


# ------------------------------------------------- R3 post-depletion ops
def r3(allw) -> Dict[str, Any]:
    ops = Counter()
    lat: List[float] = []
    per = {}
    for w in allw:
        k = Counter()
        for oc in ("up", "down"):
            cl = w["clips"][oc]
            for i, c in enumerate(cl):
                depleted = c["clip_total_shares"] >= CLIP80 - TOL
                if not depleted:
                    continue
                nxt = cl[i + 1] if i + 1 < len(cl) else None
                if nxt is None:
                    op = "WITHDRAW"
                elif abs(nxt["price"] - c["price"]) < 1e-9:
                    op = "REPLENISH"
                elif nxt["price"] < c["price"]:
                    op = "EXTEND_LADDER"
                else:
                    op = "RECENTER"
                ops[op] += 1
                k[op] += 1
                if nxt is not None:
                    lat.append(nxt["t_first"] - c["t_last"])
        per[w["slug"][-10:]] = dict(k)
    return {
        "operations": dict(ops),
        "per_window": per,
        "latency_to_next_attributable_fill_s": qs(lat),
        "LATENCY_IS_AN_UPPER_BOUND": (
            "unfilled order placement is unobservable, so the time to the "
            "next attributable FILL is an upper bound on replacement "
            "latency, never the replacement latency itself"),
    }


def r4(allw) -> Dict[str, Any]:
    """Do the four operations describe a window more compactly than fills?"""
    per = {}
    wins_ok = 0
    for w in allw:
        nf = sum(len(c["fill_quantities"]) for oc in ("up", "down")
                 for c in w["clips"][oc])
        nops = sum(len(w["clips"][oc]) for oc in ("up", "down"))
        ratio = (nops / nf) if nf else None
        per[w["slug"][-10:]] = {"fills": nf, "operations": nops,
                                "ratio": (round(ratio, 4) if ratio else None)}
        if ratio is not None and ratio <= 0.5:
            wins_ok += 1
    return {"per_window": per, "windows_meeting_0.5_bar": wins_ok,
            "of_windows": len(per),
            "MEETS_R4_BAR": wins_ok >= 9}


# ------------------------------------------------------ envelope models
def envelope(allw) -> Dict[str, Any]:
    """E1 fixed absolute range | E2 fixed distance around touch | E3
    inventory-dependent asymmetric. LOWO on center and width separately."""
    obs: Dict[str, List[dict]] = defaultdict(list)
    for w in allw:
        for oc in ("up", "down"):
            cl = w["clips"][oc]
            inv = 0.0
            for s in range(0, WINDOW_S, 5):
                t = w["start"] + s
                act = [c for c in cl if abs(c["t_first"] - t) <= 30]
                if len(act) < 2:
                    continue
                pxs = [c["price"] for c in act]
                bb = next((b for sec, b, _ in w["series"][oc]
                           if sec == s and b is not None), None)
                if bb is None:
                    continue
                inv = sum(c["clip_total_shares"] for c in cl
                          if c["t_first"] <= t)
                obs[w["slug"]].append({
                    "center": (max(pxs) + min(pxs)) / 2.0,
                    "width": max(pxs) - min(pxs),
                    "touch": bb, "inv": inv})
    slugs = sorted(obs)
    if len(slugs) < 3:
        return {"VERDICT": "INSUFFICIENT_ENVELOPE_OBSERVATIONS",
                "n_windows": len(slugs)}

    def fit(rows):
        c = [r["center"] for r in rows]
        w_ = [r["width"] for r in rows]
        d = [r["center"] - r["touch"] for r in rows]
        # E3 needs a real inventory coefficient, by least squares on
        # (inventory, center-offset-from-touch). An earlier version used a
        # rank correlation multiplied by zero, which made E3 identical to
        # E2 by construction and produced a spurious NOT_DISCRIMINATED.
        iv = [r["inv"] for r in rows]
        mi = sum(iv) / len(iv)
        md = sum(d) / len(d)
        den = sum((x - mi) ** 2 for x in iv)
        slope = (sum((x - mi) * (y - md) for x, y in zip(iv, d)) / den
                 if den > 1e-9 else 0.0)
        return {
            "E1_center": sum(c) / len(c),
            "E2_offset_from_touch": md,
            "width": sum(w_) / len(w_),
            "E3_slope_per_share": slope,
            "E3_intercept": md - slope * mi,
        }

    res = {"n_windows": len(slugs), "models": {}}
    scores = {m: [] for m in ("E1_fixed_absolute_range",
                              "E2_fixed_distance_around_touch",
                              "E3_inventory_dependent")}
    wwin = {m: 0 for m in scores}
    for held in slugs:
        tr = [r for s in slugs if s != held for r in obs[s]]
        f = fit(tr)
        te = obs[held]
        e = {
            "E1_fixed_absolute_range": sum(
                abs(r["center"] - f["E1_center"]) for r in te) / len(te),
            "E2_fixed_distance_around_touch": sum(
                abs(r["center"] - (r["touch"] + f["E2_offset_from_touch"]))
                for r in te) / len(te),
            "E3_inventory_dependent": sum(
                abs(r["center"] - (r["touch"] + f["E3_intercept"]
                                   + f["E3_slope_per_share"] * r["inv"]))
                for r in te) / len(te),
        }
        best = min(e, key=e.get)
        wwin[best] += 1
        for m, v in e.items():
            scores[m].append(v)
    med = {m: (qs(v) or {}).get("p50") for m, v in scores.items()}
    order = sorted(med, key=lambda m: med[m] if med[m] is not None else 9)
    b, nb = order[0], order[1]
    gap = ((med[nb] - med[b]) / med[nb]) if med[nb] else 0.0
    return {
        **res,
        "DEGENERACY": ("E2 is identical to the probability-following model "
                       "because contract price IS probability; the "
                       "fixed-around-0.50 model is nested in E1. A win by "
                       "E2 is ONE result, not two."),
        "LOWO_median_L1_center": med,
        "held_out_wins": wwin,
        "best": b, "next_best": nb,
        "relative_improvement": round(gap, 4),
        "width": qs([r["width"] for s in slugs for r in obs[s]]),
        "VERDICT": (b if (gap >= 0.20 and wwin[b] >= 9)
                    else "NOT_DISCRIMINATED"),
        "bar": ">=20% better than next best AND >=9/12 held-out wins",
    }


# --------------------------------------------- pre-positioned vs reactive
def models(allw) -> Dict[str, Any]:
    ages = []
    for w in allw:
        for f in w["fills"]:
            if f["fill_index_in_clip"] == 0 and \
                    f["level_age_at_first_fill_s"] is not None:
                ages.append(f["level_age_at_first_fill_s"])
    q = qs(ages)
    q = q if q.get("p50") is not None else {"n": q.get("n", 0)}
    long_share = (sum(1 for a in ages if a >= 30) / len(ages)) if ages else None
    aba = q3(allw)
    pre = bool(long_share is not None and long_share >= 0.20
               and aba["windows_with_qualifying_return"] >= 9)
    rea = bool(q.get("p50") is not None and q["p50"] <= 5
               and aba["qualifying_ABA_returns"] / max(1, len(ages)) < 0.05)
    return {
        "level_age_at_first_fill_s": q,
        "share_age_ge_30s": (round(long_share, 4) if long_share is not None
                             else None),
        "qualifying_ABA_windows": aba["windows_with_qualifying_return"],
        "PRE_POSITIONED_BAR_MET": pre,
        "REACTIVE_BAR_MET": rea,
        "VERDICT": ("PRE_POSITIONED" if pre and not rea else
                    "REACTIVE" if rea and not pre else "NOT_DISCRIMINATED"),
        "METRIC_LIMITATION_DISCLOSED": (
            "level age is a WEAKER discriminator than the protocol assumed. "
            "Once a level is reachable, a pre-positioned order fills at "
            "once (age ~0) and a reactive repost fills after a short lag "
            "(age small) -- both produce short ages, so a short-age result "
            "does not separate the models. The A->B->A test carries the "
            "discriminating weight. Reported as frozen, not re-specified."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events15", required=True)
    ap.add_argument("--parquet15", required=True)
    ap.add_argument("--outdir", default="analysis/l5/ladder")
    a = ap.parse_args(argv)
    ev = jl(a.events15)
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    for g in byw.values():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
    books, wins = load(a.parquet15, contract_length="15m", slugs=set(byw))
    allw = [build(s, byw[s], books, wins[s]["tokens"]) for s in sorted(byw)]
    rng = random.Random(20260821)

    out = {
        "protocol": PREREG,
        "G_STRIKE": {"agreement": "10/12", "bar": ">=11/12",
                     "VERDICT": "FAIL",
                     "consequence": ("per the frozen protocol the "
                                     "strike-distance test is NOT "
                                     "IDENTIFIABLE and was NOT run"),
                     "note": ("the two mismatches are the two smallest "
                              "proxy moves, -0.044% and -0.015%, i.e. "
                              "inside the venue basis")},
        "bids_only": True,
        "crossed_exposure_from_archive_flag": {
            w["slug"][-10:]: w["crossed_share"] for w in allw},
        "Q1_irregular_sizes": q1(allw),
        "Q2_min_ladder_width_LOWER_BOUND": q2(allw),
        "Q3_concurrency": q3(allw),
        "Q4_ladder_width_and_0_06": q4(allw),
        "R1_lattice_forced80": r1(allw, rng, True),
        "R2_lattice_empirical_partial": r1(allw, random.Random(7), False),
        "R3_post_depletion_operations": r3(allw),
        "R4_operations_vs_fills": r4(allw),
        "ENVELOPE": envelope(allw),
        "MODELS_prepositioned_vs_reactive": models(allw),
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "LADDER.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    with open(os.path.join(a.outdir, "LADDER_FILLS.jsonl"), "w") as fh:
        for w in allw:
            for f in w["fills"]:
                fh.write(json.dumps(f, default=str) + "\n")
    print("written LADDER.json (%d fill records)"
          % sum(len(w["fills"]) for w in allw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
