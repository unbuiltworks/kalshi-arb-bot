"""Ladder coordinate system: levels relative to the moving touch.

Frozen protocol: PREREGISTRATION_SPACING.md @ c65bf29f.

Bids only. Every level count, quantity and lifetime is a LOWER BOUND --
cancels and unfilled orders are invisible. Individual fill quantity is
never treated as intended order quantity, and no unfilled order is
inferred.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S
from analysis.l5.microstructure.clips import build_clips
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id
from analysis.l5.microstructure.stats import qs, spearman

WINDOW_S = 900
GAP_S = 10
CLIP80 = 80.0
TOL = 0.75
TICK = 0.01            # Polymarket grid: one tick IS one cent
VOL_LOOKBACK_S = 60
PREREG = "PREREGISTRATION_SPACING.md @ c65bf29f"


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def bidmap(bk, start):
    out, crossed = {}, 0
    seen = 0
    for s in range(WINDOW_S):
        sn = bk.at(float(start + s), max_age_s=MAX_CARRY_S) if bk else None
        if not sn or sn.get("best_bid") is None:
            continue
        out[s] = float(sn["best_bid"])
        seen += 1
        if sn.get("crossed"):
            crossed += 1
    return out, (round(crossed / seen, 4) if seen else None)


def contract_vol(bm: Dict[int, float], s: int) -> Optional[float]:
    """Realized sd of the CONTRACT price over the trailing 60 s.

    Contract-price volatility, never BTC volatility: there is no settlement
    index (G-STRIKE failed 10/12), so no BTC-derived sigma is used.
    """
    xs = [bm[k] for k in range(max(0, s - VOL_LOOKBACK_S), s + 1) if k in bm]
    if len(xs) < 10:
        return None
    d = [b - a for a, b in zip(xs, xs[1:])]
    if len(d) < 5:
        return None
    return st.pstdev(d) or None


def build(slug, seq, books, tokens) -> Dict[str, Any]:
    start = int(slug.rsplit("-", 1)[1])
    tok = {str(o).lower(): t for t, o in tokens.items()}
    pairs, member = find_pairs(seq)
    maker = [r for r in seq
             if _id(r) not in member and r.get("l5_role") == "maker"]
    out = {"slug": slug, "start": start, "levels": {}, "crossed": {}}
    for oc in ("up", "down"):
        bm, cr = bidmap(books.get(tok[oc]), start)
        out["crossed"][oc] = cr
        side = [r for r in maker if r["outcome"] == oc]
        clips = sorted(build_clips(side, books, gap_s=GAP_S, slug=slug,
                                   clen="15m", start=start),
                       key=lambda c: c["t_first"])
        # one record per (price level, clip) -- a level revisited later is a
        # NEW inference event, which is what "newly inferable" means.
        recs = []
        first_seen: Dict[float, int] = {}
        for c in clips:
            px = round(c["price"], 4)
            s0 = c["t_first"] - start
            s1 = c["t_last"] - start
            bb = bm.get(s0)
            if bb is None:
                continue
            is_new = px not in first_seen
            if is_new:
                first_seen[px] = s0
            recs.append({
                "price": px,
                "secs_after_open_first_inferable": s0,
                "touch_at_first_inference": bb,
                "offset_ticks": round((bb - px) / TICK),
                "offset_dollars": round(bb - px, 4),
                "is_new_level": is_new,
                # LOWER BOUND on exposed quantity: consumption is all that
                # is observable. NOT an intended order quantity.
                "latent_quantity_LOWER_BOUND": round(c["clip_total_shares"], 4),
                "n_fills": c["n_fills"],
                "lifetime_lower_bound_s": s1 - s0,
                "reached_80_ceiling": c["clip_total_shares"] >= CLIP80 - TOL,
                "p_touch": bb,
                "contract_vol_60s": contract_vol(bm, s0),
                "tte_s": WINDOW_S - s0,
            })
        # Liveness must use the PER-PRICE span (first..last fill at that
        # price, across all its clips), which is the same notion the Q2
        # interval-overlap bound used. A per-CLIP span has a median length
        # of 0 s, so it would report ~1 live level and contradict the
        # 5-15 lower bound this study is meant to characterise.
        span: Dict[float, Tuple[int, int]] = {}
        for c in clips:
            px = round(c["price"], 4)
            a, b = c["t_first"] - start, c["t_last"] - start
            span[px] = ((min(span[px][0], a), max(span[px][1], b))
                        if px in span else (a, b))
        for r in recs:
            s_at = r["secs_after_open_first_inferable"]
            live_px = [px for px, (a, b) in span.items() if a <= s_at <= b]
            offs = sorted({round((bm[s_at] - px) / TICK) for px in live_px
                           if s_at in bm})
            r["level_number"] = (offs.index(r["offset_ticks"]) + 1
                                 if r["offset_ticks"] in offs else None)
            r["n_live_levels_lower_bound"] = len(offs)
            r["live_offsets_ticks"] = offs
            r["per_price_span_s"] = span[r["price"]][1] - span[r["price"]][0]
        out["levels"][oc] = recs
    return out


# ------------------------------------------------------- distributions
def distributions(allw) -> Dict[str, Any]:
    rows = [r for w in allw for oc in ("up", "down") for r in w["levels"][oc]]
    new = [r for r in rows if r["is_new_level"]]
    by_num: Dict[int, List[dict]] = defaultdict(list)
    for r in rows:
        if r["level_number"]:
            by_num[r["level_number"]].append(r)
    per_num = {}
    for n in sorted(by_num)[:12]:
        g = by_num[n]
        if len(g) < 30:
            per_num[str(n)] = {"n": len(g), "UNDERPOWERED": True}
            continue
        per_num[str(n)] = {
            "n": len(g),
            "offset_ticks": qs([x["offset_ticks"] for x in g]),
            "latent_quantity_LOWER_BOUND": qs(
                [x["latent_quantity_LOWER_BOUND"] for x in g]),
            "lifetime_lower_bound_s": qs(
                [x["lifetime_lower_bound_s"] for x in g]),
            "P_full_consumption_to_80_given_inferable": round(
                sum(1 for x in g if x["reached_80_ceiling"]) / len(g), 4),
        }
    return {
        "n_level_inference_events": len(rows),
        "n_new_levels": len(new),
        "SELECTION_TAUTOLOGY_NOTE": (
            "P(any consumption | inferable) == 1.0 by construction, because "
            "a level is only visible through its fills. Only P(full "
            "consumption to the 80 ceiling) is reported."),
        "by_level_number": per_num,
        "pooled_offset_ticks": qs([r["offset_ticks"] for r in rows]),
        "pooled_new_level_offset_ticks": qs([r["offset_ticks"] for r in new]),
        "pooled_latent_quantity_LOWER_BOUND": qs(
            [r["latent_quantity_LOWER_BOUND"] for r in rows]),
        "pooled_lifetime_lower_bound_s": qs(
            [r["lifetime_lower_bound_s"] for r in rows]),
        "P_full_consumption_overall": round(
            sum(1 for r in rows if r["reached_80_ceiling"]) / len(rows), 4),
        "n_live_levels_lower_bound": qs(
            [r["n_live_levels_lower_bound"] for r in rows]),
    }


# ----------------------------------------------------- spacing models
def spacing_models(allw) -> Dict[str, Any]:
    """M1 uniform (ticks == cents) | M2 increasing with depth |
    M3 probability-scaled | M4 contract-volatility-scaled. LOWO."""
    # per (window, outcome, second) the sorted live offsets -> gaps by rank
    obs: Dict[str, List[dict]] = defaultdict(list)
    for w in allw:
        for oc in ("up", "down"):
            recs = w["levels"][oc]
            for r in recs:
                live = r.get("live_offsets_ticks") or []
                if len(live) < 3:
                    continue
                gaps = [b - a for a, b in zip(live, live[1:])]
                for k, g in enumerate(gaps, 1):
                    obs[w["slug"]].append({
                        "k": k, "gap": g, "p": r["p_touch"],
                        "vol": r["contract_vol_60s"]})
    slugs = sorted(obs)
    if len(slugs) < 3:
        return {"VERDICT": "INSUFFICIENT_OBSERVATIONS", "n": len(slugs)}

    def fit(rows):
        g = [r["gap"] for r in rows]
        ks = [r["k"] for r in rows]
        mk, mg = sum(ks) / len(ks), sum(g) / len(g)
        den = sum((x - mk) ** 2 for x in ks)
        b = (sum((x - mk) * (y - mg) for x, y in zip(ks, g)) / den
             if den > 1e-9 else 0.0)
        pv = [r for r in rows if r["p"] is not None]
        c3 = (sum(r["gap"] / max(1e-9, r["p"] * (1 - r["p"])) for r in pv)
              / len(pv)) if pv else 0.0
        vv = [r for r in rows if r["vol"]]
        c4 = (sum(r["gap"] / max(1e-9, r["vol"] / TICK) for r in vv)
              / len(vv)) if vv else 0.0
        return {"c1": mg, "a2": mg - b * mk, "b2": b, "c3": c3, "c4": c4}

    scores = {m: [] for m in ("M1_uniform_ticks_eq_cents",
                              "M2_increasing_with_depth",
                              "M3_probability_scaled",
                              "M4_contract_volatility_scaled")}
    wins = {m: 0 for m in scores}
    for held in slugs:
        tr = [r for s in slugs if s != held for r in obs[s]]
        f = fit(tr)
        te = obs[held]

        def err(pred):
            v = [abs(r["gap"] - pred(r)) for r in te if pred(r) is not None]
            return (st.median(v) if v else None)

        e = {
            "M1_uniform_ticks_eq_cents": err(lambda r: f["c1"]),
            "M2_increasing_with_depth": err(
                lambda r: f["a2"] + f["b2"] * r["k"]),
            "M3_probability_scaled": err(
                lambda r: (f["c3"] * r["p"] * (1 - r["p"])
                           if r["p"] is not None else None)),
            "M4_contract_volatility_scaled": err(
                lambda r: (f["c4"] * r["vol"] / TICK if r["vol"] else None)),
        }
        ok = {k: v for k, v in e.items() if v is not None}
        if ok:
            wins[min(ok, key=ok.get)] += 1
        for m, v in e.items():
            if v is not None:
                scores[m].append(v)
    med = {m: (round(st.median(v), 4) if v else None)
           for m, v in scores.items()}
    ok = {m: v for m, v in med.items() if v is not None}
    order = sorted(ok, key=lambda m: ok[m])
    best, nxt = order[0], order[1]
    gain = (ok[nxt] - ok[best]) / ok[nxt] if ok[nxt] else 0.0
    return {
        "DEGENERACY": ("uniform ticks and uniform cents are the SAME model: "
                       "the Polymarket grid IS $0.01. One result, not two."),
        "M4_uses_contract_price_volatility_not_BTC": True,
        "LOWO_median_L1_gap_ticks": med,
        "held_out_wins": wins,
        "best": best, "next_best": nxt,
        "relative_improvement": round(gain, 4),
        "VERDICT": (best if (gain >= 0.20 and wins[best] >= 9)
                    else "NOT_DISCRIMINATED"),
        "bar": ">=20% better than next best AND >=9/12 held-out wins",
        "pooled_gap_ticks": qs([r["gap"] for s in slugs for r in obs[s]]),
    }


# -------------------------------------- constant offsets from moving touch
def stability(allw) -> Dict[str, Any]:
    new = [r for w in allw for oc in ("up", "down")
           for r in w["levels"][oc] if r["is_new_level"]]
    offs = [r["offset_ticks"] for r in new]
    q = qs(offs)
    iqr = (q["p75"] - q["p25"]) if q.get("p75") is not None else None
    pooled_med = q.get("p50")
    per = {}
    for w in allw:
        v = [r["offset_ticks"] for oc in ("up", "down")
             for r in w["levels"][oc] if r["is_new_level"]]
        per[w["slug"][-10:]] = (round(st.median(v), 2) if v else None)
    within = sum(1 for m in per.values()
                 if m is not None and pooled_med is not None
                 and abs(m - pooled_med) <= 2)
    t_stable = bool(iqr is not None and iqr <= 2 and within >= 9)

    # T-SET: smallest offset set covering >=80% of new levels
    cnt = Counter(offs)
    chosen, cov = [], 0
    for off, c in cnt.most_common():
        chosen.append(off)
        cov += c
        if cov / max(1, len(offs)) >= 0.80:
            break

    # T-RECENTER: across a touch move, does offset-from-touch persist?
    deltas = []
    for w in allw:
        for oc in ("up", "down"):
            recs = sorted([r for r in w["levels"][oc] if r["is_new_level"]],
                          key=lambda r: r["secs_after_open_first_inferable"])
            for a, b in zip(recs, recs[1:]):
                if abs(b["touch_at_first_inference"]
                       - a["touch_at_first_inference"]) >= TICK - 1e-9:
                    deltas.append(abs(b["offset_ticks"] - a["offset_ticks"]))
    per_rec = {}
    for w in allw:
        d = []
        for oc in ("up", "down"):
            recs = sorted([r for r in w["levels"][oc] if r["is_new_level"]],
                          key=lambda r: r["secs_after_open_first_inferable"])
            for a, b in zip(recs, recs[1:]):
                if abs(b["touch_at_first_inference"]
                       - a["touch_at_first_inference"]) >= TICK - 1e-9:
                    d.append(abs(b["offset_ticks"] - a["offset_ticks"]))
        per_rec[w["slug"][-10:]] = (round(st.median(d), 2) if d else None)
    rec_ok = sum(1 for v in per_rec.values() if v is not None and v <= 1)
    return {
        "n_new_levels": len(new),
        "new_level_offset_ticks": q,
        "IQR_ticks": iqr,
        "per_window_median_offset": per,
        "windows_within_2_ticks_of_pooled_median": within,
        "T_STABLE_PASS": t_stable,
        "T_STABLE_bar": "IQR<=2 ticks AND >=9/12 windows within 2 ticks",
        "T_SET_smallest_offset_set_ticks": sorted(chosen),
        "T_SET_coverage": round(cov / max(1, len(offs)), 4),
        "T_RECENTER_abs_offset_change_ticks": qs(deltas),
        "T_RECENTER_per_window_median": per_rec,
        "T_RECENTER_windows_median_le_1_tick": rec_ok,
        "T_RECENTER_PASS": rec_ok >= 9,
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

    out = {
        "protocol": PREREG,
        "bids_only": True,
        "LOWER_BOUNDS": ("every level count, latent quantity and lifetime is "
                         "a floor; cancels and unfilled orders are invisible "
                         "and none is inferred"),
        "crossed_exposure_archive_flag": {
            w["slug"][-10:]: w["crossed"] for w in allw},
        "DISTRIBUTIONS": distributions(allw),
        "SPACING_MODELS": spacing_models(allw),
        "STABILITY_from_moving_touch": stability(allw),
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "SPACING.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    with open(os.path.join(a.outdir, "SPACING_LEVELS.jsonl"), "w") as fh:
        for w in allw:
            for oc in ("up", "down"):
                for r in w["levels"][oc]:
                    fh.write(json.dumps({"slug": w["slug"], "outcome": oc,
                                         **r}, default=str) + "\n")
    print("written SPACING.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
