"""Persistent resting vs dynamic reposting: touch excursion-return episodes.

Frozen protocol: PREREGISTRATION_PERSISTENCE.md @ 5816406e.

Episode eligibility is blind to subsequent L5 fills: only the touch path
and the anchoring fill enter it. The matched no-prior-use control is the
whole test, because L5 places AT the touch, so a return fill is expected
under both hypotheses.

A returning fill is NEVER proof the original order remained resting.
Historical data cannot establish order identity.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S
from analysis.l5.microstructure.clips import build_clips
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id
from analysis.l5.microstructure.stats import cliffs_delta, qs

WINDOW_S = 900
GAP_S = 10
CLIP80 = 80.0
TOL = 0.75
TICK = 0.01
# ---- frozen parameters, protocol 5816406e ----
NEAR_LO, NEAR_HI = -3, 4        # T_SET near-touch band, in ticks
D_TICKS = 6                     # excursion distance
MIN_EXC_S = 10                  # minimum excursion duration
RET_TOL_TICKS = 1               # return tolerance
ADJ_TICKS = 2                   # adjacent band, reported separately
HORIZON_S = 60                  # measurement horizon after return
MIN_REMAIN_S = 60               # else INSUFFICIENT_SUBSEQUENT_OPPORTUNITY
MATCH_DIST_TICKS = 2
MATCH_TIME_S = 120
PREREG = "PREREGISTRATION_PERSISTENCE.md @ 5816406e"


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def bidmap(bk, start) -> Tuple[Dict[int, float], Optional[float]]:
    out, crossed, seen = {}, 0, 0
    for s in range(WINDOW_S):
        sn = bk.at(float(start + s), max_age_s=MAX_CARRY_S) if bk else None
        if not sn or sn.get("best_bid") is None:
            continue
        out[s] = float(sn["best_bid"])
        seen += 1
        if sn.get("crossed"):
            crossed += 1
    return out, (round(crossed / seen, 4) if seen else None)


def ticks(a: float, b: float) -> int:
    """signed ticks of (a - b)."""
    return int(round((a - b) / TICK))


def find_episodes(bm: Dict[int, float], p: float, anchor_s: int
                  ) -> List[dict]:
    """Excursion-return episodes for price p after anchor_s.

    Uses ONLY the touch path. No L5 fill after the anchor is consulted.
    """
    secs = sorted(k for k in bm if k >= anchor_s)
    eps: List[dict] = []
    i = 0
    while i < len(secs):
        # find the start of an excursion: |touch - p| >= D
        while i < len(secs) and abs(ticks(bm[secs[i]], p)) < D_TICKS:
            i += 1
        if i >= len(secs):
            break
        exc_start = secs[i]
        # stay away for >= MIN_EXC_S before it counts
        j = i
        last_away = exc_start
        while j < len(secs) and abs(ticks(bm[secs[j]], p)) >= D_TICKS:
            last_away = secs[j]
            j += 1
        dur = last_away - exc_start
        peak = max(abs(ticks(bm[secs[k]], p)) for k in range(i, j))
        if dur < MIN_EXC_S or j >= len(secs):
            i = max(j, i + 1)
            continue
        # find the return: touch within RET_TOL of p
        k = j
        ret = None
        while k < len(secs):
            if abs(ticks(bm[secs[k]], p)) <= RET_TOL_TICKS:
                ret = secs[k]
                break
            if abs(ticks(bm[secs[k]], p)) >= D_TICKS:
                break          # excursed again before returning
            k += 1
        if ret is None:
            i = max(k, j, i + 1)
            continue
        eps.append({"price": p, "anchor_s": anchor_s,
                    "exc_start_s": exc_start, "exc_end_s": last_away,
                    "excursion_duration_s": dur,
                    "excursion_peak_ticks": peak,
                    "return_s": ret,
                    "remaining_window_s": WINDOW_S - ret})
        i = k + 1
    return eps


def classify(ep: dict, fills_by_px: Dict[float, List[int]],
             opp_fill_secs: List[int]) -> str:
    if ep["remaining_window_s"] < MIN_REMAIN_S:
        return "INSUFFICIENT_SUBSEQUENT_OPPORTUNITY"
    r, p = ep["return_s"], ep["price"]
    hi = r + HORIZON_S
    exact = [s for s in fills_by_px.get(round(p, 4), []) if r <= s <= hi]
    if exact:
        ep["secs_to_first_fill_at_old_level"] = min(exact) - r
        return "SAME_EXACT_PRICE_RETURN"
    adj = [s for px, ss in fills_by_px.items()
           if 0 < abs(ticks(px, p)) <= ADJ_TICKS for s in ss if r <= s <= hi]
    if adj:
        ep["secs_to_first_adjacent_fill"] = min(adj) - r
        return "ADJACENT_PRICE_RETURN"
    if any(r <= s <= hi for s in opp_fill_secs):
        return "OPPOSITE_OUTCOME_FILL"
    return "NO_L5_FILL"


def build(slug, seq, books, tokens) -> Dict[str, Any]:
    start = int(slug.rsplit("-", 1)[1])
    tok = {str(o).lower(): t for t, o in tokens.items()}
    pairs, member = find_pairs(seq)
    maker = [r for r in seq
             if _id(r) not in member and r.get("l5_role") == "maker"]
    res = {"slug": slug, "treated": [], "control": [], "crossed": {}}
    side_state = {}
    for oc in ("up", "down"):
        bm, cr = bidmap(books.get(tok[oc]), start)
        res["crossed"][oc] = cr
        clips = sorted(build_clips([r for r in maker if r["outcome"] == oc],
                                   books, gap_s=GAP_S, slug=slug,
                                   clen="15m", start=start),
                       key=lambda c: c["t_first"])
        fills_by_px: Dict[float, List[int]] = defaultdict(list)
        for c in clips:
            fills_by_px[round(c["price"], 4)].append(c["t_first"] - start)
        side_state[oc] = {"bm": bm, "clips": clips,
                          "fills_by_px": dict(fills_by_px)}

    for oc in ("up", "down"):
        st_ = side_state[oc]
        other = side_state["down" if oc == "up" else "up"]
        opp_secs = [s for ss in other["fills_by_px"].values() for s in ss]
        bm = st_["bm"]

        # ---- treated: anchored by a first L5 fill in the near-touch band
        first_at: Dict[float, dict] = {}
        for c in st_["clips"]:
            px = round(c["price"], 4)
            s0 = c["t_first"] - start
            if px in first_at or s0 not in bm:
                continue
            off = ticks(bm[s0], px)
            if NEAR_LO <= off <= NEAR_HI:
                # consumption BEFORE any excursion = this clip's total
                first_at[px] = {"anchor_s": s0,
                                "consumed_before": c["clip_total_shares"],
                                "n_fills": c["n_fills"]}
        for px, a in first_at.items():
            for ep in find_episodes(bm, px, a["anchor_s"]):
                ep.update(outcome=oc, slug=slug, arm="TREATED",
                          consumed_before_excursion=round(
                              a["consumed_before"], 4),
                          prior_consumption=("FULL"
                                             if a["consumed_before"]
                                             >= CLIP80 - TOL else "PARTIAL"),
                          remaining_latent_INFERRED=(
                              None if a["consumed_before"] >= CLIP80 - TOL
                              else round(CLIP80 - a["consumed_before"], 4)),
                          tte_s=WINDOW_S - ep["return_s"],
                          inventory_at_return=round(sum(
                              c["clip_total_shares"] for c in st_["clips"]
                              if c["t_first"] - start <= ep["return_s"]), 2))
                ep["class"] = classify(ep, st_["fills_by_px"], opp_secs)
                res["treated"].append(ep)

        # ---- control: same path shape, NO PRIOR L5 fill at that price
        # The condition is "no L5 fill at p' BEFORE that excursion", per the
        # frozen protocol -- NOT "never filled in the window". An earlier
        # version used the whole-window condition, which made a same-price
        # return fill impossible by construction and forced the control
        # rate to exactly 0.0. A control price may be filled later; that is
        # precisely the counterfactual being measured.
        cand_px = sorted({round(v, 4) for v in bm.values()})
        for px in cand_px:
            near = [s for s, b in sorted(bm.items())
                    if NEAR_LO <= ticks(b, px) <= NEAR_HI]
            if not near:
                continue
            prior = st_["fills_by_px"].get(px, [])
            for ep in find_episodes(bm, px, near[0]):
                # eligibility is judged ONLY on pre-excursion history
                if any(s <= ep["exc_start_s"] for s in prior):
                    continue
                ep.update(outcome=oc, slug=slug, arm="CONTROL",
                          tte_s=WINDOW_S - ep["return_s"])
                ep["class"] = classify(ep, st_["fills_by_px"], opp_secs)
                res["control"].append(ep)
    return res


def match_controls(treated: List[dict], control: List[dict]) -> List[dict]:
    """Match on window+outcome, excursion distance +/-2 ticks, return time
    +/-120 s. The time match stops an active L5 phase being compared
    against an inactive one."""
    by = defaultdict(list)
    for c in control:
        by[(c["slug"], c["outcome"])].append(c)
    out = []
    for t in treated:
        pool = by.get((t["slug"], t["outcome"]), [])
        for c in pool:
            if abs(c["excursion_peak_ticks"]
                   - t["excursion_peak_ticks"]) <= MATCH_DIST_TICKS and \
                    abs(c["return_s"] - t["return_s"]) <= MATCH_TIME_S:
                out.append(dict(c, matched_to=t["return_s"]))
    # de-duplicate on (slug, outcome, price, return_s)
    seen, uniq = set(), []
    for c in out:
        k = (c["slug"], c["outcome"], c["price"], c["return_s"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def rate(eps: List[dict], cls="SAME_EXACT_PRICE_RETURN"
         ) -> Tuple[Optional[float], int, int]:
    el = [e for e in eps
          if e["class"] != "INSUFFICIENT_SUBSEQUENT_OPPORTUNITY"]
    if not el:
        return None, 0, 0
    k = sum(1 for e in el if e["class"] == cls)
    return round(k / len(el), 4), k, len(el)


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

    treated = [e for w in allw for e in w["treated"]]
    control_all = [e for w in allw for e in w["control"]]
    control = match_controls(treated, control_all)

    tr_rate, tk, tn = rate(treated)
    co_rate, ck, cn = rate(control)
    ratio = (round(tr_rate / co_rate, 3)
             if tr_rate and co_rate else None)

    # per-window direction
    perw = {}
    for w in allw:
        t = [e for e in w["treated"]]
        c = [e for e in control if e["slug"] == w["slug"]]
        trr, _, tnn = rate(t)
        corr, _, cnn = rate(c)
        perw[w["slug"][-10:]] = {"treated_rate": trr, "treated_n": tnn,
                                 "control_rate": corr, "control_n": cnn,
                                 "treated_higher": (
                                     None if trr is None or corr is None
                                     else trr > corr)}
    dir_ok = sum(1 for v in perw.values()
                 if v["treated_higher"] and (v["treated_n"] or 0) >= 2)
    p1 = bool(ratio is not None and ratio >= 2.0 and tn >= 30 and dir_ok >= 9)

    # ---- P2 partial vs full ----
    part = [e for e in treated if e.get("prior_consumption") == "PARTIAL"]
    full = [e for e in treated if e.get("prior_consumption") == "FULL"]
    pr, _, pn = rate(part)
    fr, _, fn = rate(full)
    diff = (round(pr - fr, 4) if pr is not None and fr is not None else None)
    sign_w = 0
    for w in allw:
        tp = [e for e in w["treated"]
              if e.get("prior_consumption") == "PARTIAL"]
        tf = [e for e in w["treated"] if e.get("prior_consumption") == "FULL"]
        a1, _, n1 = rate(tp)
        a2, _, n2 = rate(tf)
        if n1 >= 3 and n2 >= 3 and a1 is not None and a2 is not None \
                and a1 > a2:
            sign_w += 1
    p2 = bool(diff is not None and diff >= 0.10 and pn >= 30 and fn >= 30
              and sign_w >= 8)

    # ---- covariates ----
    hit = [e for e in treated if e["class"] == "SAME_EXACT_PRICE_RETURN"]
    miss = [e for e in treated if e["class"] == "NO_L5_FILL"]
    cov = {}
    for k in ("excursion_peak_ticks", "excursion_duration_s", "tte_s",
              "inventory_at_return", "consumed_before_excursion",
              "remaining_latent_INFERRED"):
        x = [e[k] for e in hit if e.get(k) is not None]
        y = [e[k] for e in miss if e.get(k) is not None]
        cov[k] = (cliffs_delta(x, y) if min(len(x), len(y)) >= 10
                  else {"n_a": len(x), "n_b": len(y), "delta": None})

    lat = [e["secs_to_first_fill_at_old_level"] for e in treated
           if "secs_to_first_fill_at_old_level" in e]

    verdict = ("UNIDENTIFIABLE" if tn < 30 or cn < 10 else
               "CONSISTENT_WITH_PERSISTENCE" if p1 and p2 else
               "CONSISTENT_WITH_REPOSTING" if not p1 else
               "NOT_DISCRIMINATED")

    out = {
        "protocol": PREREG,
        "frozen": {"D_ticks": D_TICKS, "min_excursion_s": MIN_EXC_S,
                   "return_tolerance_ticks": RET_TOL_TICKS,
                   "near_touch_band_ticks": [NEAR_LO, NEAR_HI],
                   "horizon_s": HORIZON_S},
        "THE_CONTROL_IS_THE_TEST": (
            "L5 places at the touch, so a return fill is expected under "
            "both persistence and reposting. Only the treated-vs-control "
            "contrast discriminates."),
        "ORDER_IDENTITY_UNOBSERVABLE": (
            "a returning fill is never proof the original order remained "
            "resting; no order id, placement or cancel is observable"),
        "crossed_exposure_archive_flag": {
            w["slug"][-10:]: w["crossed"] for w in allw},
        "treated_class_counts": dict(Counter(e["class"] for e in treated)),
        "control_class_counts": dict(Counter(e["class"] for e in control)),
        "P1": {"treated_rate": tr_rate, "treated_k": tk, "treated_n": tn,
               "control_rate": co_rate, "control_k": ck, "control_n": cn,
               "ratio": ratio, "windows_treated_higher": dir_ok,
               "bar": "ratio>=2.0 AND n>=30 AND >=9/12 windows",
               "PASS": p1},
        "P2_partial_vs_full": {
            "partial_rate": pr, "partial_n": pn,
            "full_rate": fr, "full_n": fn, "difference": diff,
            "windows_partial_higher": sign_w,
            "bar": "diff>=0.10 AND n>=30 each AND >=8 windows", "PASS": p2},
        "latency_return_to_first_fill_at_old_level_s": qs(lat),
        "covariates_hit_vs_miss": cov,
        "per_window": perw,
        "VERDICT": verdict,
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "PERSISTENCE.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    with open(os.path.join(a.outdir, "PERSISTENCE_EPISODES.jsonl"), "w") as fh:
        for e in treated + control:
            fh.write(json.dumps(e, default=str) + "\n")
    print("written PERSISTENCE.json: treated=%d control=%d verdict=%s"
          % (len(treated), len(control), verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
