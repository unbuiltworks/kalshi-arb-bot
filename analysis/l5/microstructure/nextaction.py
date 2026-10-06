"""Causal next-action test, implemented strictly to PREREGISTRATION_NEXTACTION.md.

Decision point = the end of a passive clip. Target = the NEXT passive clip.
Atomic $1.00 par pairs are a separate action class: excluded from the target,
available only as a feature.

Every rule is from the preregistered list; nothing outside it exists in this
file. Every training constant is estimated on the training fold and applied
unchanged to the held-out window.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book
from analysis.l5.microstructure.clips import build_clips, size_at_price
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id

TICK = 0.01
GAP_S = 10
CLIP80 = 80.0
TOL = 0.75
SIDES = ("up", "down")
SIDE_RULES = ("S-MAJ", "S-PERSIST", "S-ALT", "S-LIGHT", "S-TOUCHFELL")
PRICE_RULES = ("P-TOUCH", "P-PREV", "P-TOUCH-K", "P-COMPLEMENT")
ACT_RULES = ("A-EXPOSE", "A-PERSIST", "A-FROZEN")
TIME_RULES = ("T-MED", "T-COND")
CEIL_RULES = ("C-BASE", "C-PREV")


def feats(books, tok, start, t) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for side in SIDES:
        bk = books.get(tok[side])
        s = bk.at(float(t), max_age_s=MAX_CARRY_S) if bk else None
        out[side + "_bid"] = (s or {}).get("best_bid")
        out[side + "_ask"] = (s or {}).get("best_ask")
        out[side + "_depth_at_touch"] = (
            size_at_price(s, float(s["best_bid"]))
            if s and s.get("best_bid") is not None else None)
        out[side + "_total_depth"] = (
            sum(float(x) for x in (s.get("bid_sizes") or [])) if s else None)
        out[side + "_levels"] = len((s or {}).get("bid_prices") or [])
        for L in (5, 10, 30):
            a = bk.at(float(t - L), max_age_s=MAX_CARRY_S) if bk else None
            b0, bL = out[side + "_bid"], (a or {}).get("best_bid")
            out["%s_move_%ds" % (side, L)] = (
                round(b0 - bL, 6) if None not in (b0, bL) else None)
    return out


def build_dps(slug, clips, pairs, books, tok, start, window_s
              ) -> List[Dict[str, Any]]:
    """One decision point per clip; target is the next clip."""
    inv = {"up": 0.0, "down": 0.0}
    prev_on: Dict[str, Optional[dict]] = {"up": None, "down": None}
    prev_act: Optional[str] = None
    pair_ts = sorted(p["up"]["t"] for p in pairs)
    dps: List[Dict[str, Any]] = []
    for i, c in enumerate(clips):
        inv[c["outcome"]] += c["clip_total_shares"]
        t = c["t_last"]
        nxt = clips[i + 1] if i + 1 < len(clips) else None
        # action-type target
        if nxt is None:
            act = "WITHDRAW"
        else:
            p = prev_on_after(clips, i + 1, nxt["outcome"])
            act = ("MAINTAIN" if p is not None
                   and abs(p["price"] - nxt["price"]) < 1e-9 else "EXPOSE")
        f = feats(books, tok, start, t)
        dp = {
            "market_slug": slug, "t": t,
            "secs_after_open": t - start, "tte_s": start + window_s - t,
            "tte_fraction": round((start + window_s - t) / window_s, 4),
            "this_clip_outcome": c["outcome"], "this_clip_price": c["price"],
            "this_clip_total": c["clip_total_shares"],
            "this_clip_is_80": abs(c["clip_total_shares"] - CLIP80) < TOL,
            "inv_up": round(inv["up"], 4), "inv_down": round(inv["down"], 4),
            "imbalance": round(inv["up"] - inv["down"], 4),
            "matched": round(min(inv["up"], inv["down"]), 4),
            "prev_act": prev_act,
            "prev_price_up": (prev_on["up"] or {}).get("price"),
            "prev_price_down": (prev_on["down"] or {}).get("price"),
            "prev_was_80_up": (abs((prev_on["up"] or {}).get("clip_total_shares", -1) - CLIP80) < TOL),
            "prev_was_80_down": (abs((prev_on["down"] or {}).get("clip_total_shares", -1) - CLIP80) < TOL),
            "pairs_so_far": sum(1 for x in pair_ts if x <= t),
            # targets
            "y_action": act,
            "y_side": (nxt or {}).get("outcome"),
            "y_price": (nxt or {}).get("price"),
            "y_dt": (None if nxt is None else nxt["t_first"] - t),
            "y_is_80": (None if nxt is None
                        else abs(nxt["clip_total_shares"] - CLIP80) < TOL),
            "is_terminal": nxt is None,
            **f,
        }
        dps.append(dp)
        prev_on[c["outcome"]] = c
        prev_act = act
    return dps


def prev_on_after(clips, idx, side) -> Optional[dict]:
    for j in range(idx - 1, -1, -1):
        if clips[j]["outcome"] == side:
            return clips[j]
    return None


# ---------------------------------------------------------------- the rules

def pred_side(rule: str, d: Dict[str, Any], tr: Dict[str, Any]
              ) -> Optional[str]:
    if rule == "S-MAJ":
        return tr["maj_side"]
    if rule == "S-PERSIST":
        return d["this_clip_outcome"]
    if rule == "S-ALT":
        return "down" if d["this_clip_outcome"] == "up" else "up"
    if rule == "S-LIGHT":
        return "up" if d["inv_up"] <= d["inv_down"] else "down"
    if rule == "S-TOUCHFELL":
        a, b = d.get("up_move_5s"), d.get("down_move_5s")
        if a is None or b is None:
            return None
        return "up" if a < b else "down"
    return None


def pred_price(rule: str, d: Dict[str, Any], side: Optional[str],
               tr: Dict[str, Any]) -> Optional[float]:
    if side is None:
        return None
    if rule == "P-TOUCH":
        return d.get(side + "_bid")
    if rule == "P-PREV":
        return d.get("prev_price_" + side)
    if rule == "P-TOUCH-K":
        b = d.get(side + "_bid")
        return None if b is None else round(b - tr["k_ticks"] * TICK, 4)
    if rule == "P-COMPLEMENT":
        o = "down" if side == "up" else "up"
        b = d.get(o + "_bid")
        return None if b is None else round(1.0 - b, 4)
    return None


def pred_act(rule: str, d: Dict[str, Any], side, price,
             tr: Dict[str, Any]) -> str:
    if rule == "A-EXPOSE":
        return "EXPOSE"
    if rule == "A-PERSIST":
        return d["prev_act"] or "EXPOSE"
    if rule == "A-FROZEN":
        if (d["tte_s"] < tr["T_w"]
                and d["this_clip_price"] < tr["P_w"]):
            return "WITHDRAW"
        pp = d.get("prev_price_" + side) if side else None
        if price is not None and pp is not None and abs(pp - price) < 1e-9:
            return "MAINTAIN"
        return "EXPOSE"
    return "EXPOSE"


def pred_dt(rule: str, d: Dict[str, Any], tr: Dict[str, Any]) -> float:
    if rule == "T-COND":
        return tr["dt_by_act"].get(d["prev_act"], tr["dt_med"])
    return tr["dt_med"]


def pred_ceil(rule: str, d: Dict[str, Any], side, tr) -> bool:
    if rule == "C-PREV" and side:
        return bool(d.get("prev_was_80_" + side))
    return tr["base_80"]


def train_consts(tr: List[Dict[str, Any]]) -> Dict[str, Any]:
    nt = [d for d in tr if not d["is_terminal"]]
    sides = Counter(d["y_side"] for d in nt)
    offs = []
    for d in nt:
        b = d.get((d["y_side"] or "up") + "_bid")
        if b is not None and d["y_price"] is not None:
            offs.append(round((b - d["y_price"]) / TICK))
    term = [d for d in tr if d["is_terminal"]]
    dts = sorted(d["y_dt"] for d in nt if d["y_dt"] is not None)
    by: Dict[Any, List[float]] = defaultdict(list)
    for d in nt:
        if d["y_dt"] is not None:
            by[d["prev_act"]].append(d["y_dt"])
    return {
        "maj_side": sides.most_common(1)[0][0] if sides else "down",
        "k_ticks": (sorted(offs)[len(offs) // 2] if offs else 0),
        "T_w": (sorted(d["tte_s"] for d in term)[len(term) // 2]
                if term else 0),
        "P_w": (sorted(d["this_clip_price"] for d in term)[len(term) // 2]
                if term else 0),
        "dt_med": (dts[len(dts) // 2] if dts else 0),
        "dt_by_act": {k: sorted(v)[len(v) // 2] for k, v in by.items()},
        "base_80": (sum(1 for d in nt if d["y_is_80"]) / len(nt) > 0.5
                    if nt else False),
    }


def select(tr: List[Dict[str, Any]], consts) -> Dict[str, str]:
    nt = [d for d in tr if not d["is_terminal"]]
    best: Dict[str, str] = {}
    best["side"] = max(SIDE_RULES, key=lambda r: sum(
        1 for d in nt if pred_side(r, d, consts) == d["y_side"]))
    sr = best["side"]
    def phit(r):
        n = 0
        for d in nt:
            s = pred_side(sr, d, consts)
            p = pred_price(r, d, s, consts)
            if p is not None and d["y_price"] is not None \
                    and abs(p - d["y_price"]) < TICK / 2:
                n += 1
        return n
    best["price"] = max(PRICE_RULES, key=phit)
    pr = best["price"]
    def ahit(r):
        n = 0
        for d in tr:
            s = pred_side(sr, d, consts)
            p = pred_price(pr, d, s, consts)
            if pred_act(r, d, s, p, consts) == d["y_action"]:
                n += 1
        return n
    best["act"] = max(ACT_RULES, key=ahit)
    best["time"] = min(TIME_RULES, key=lambda r: sum(
        abs(pred_dt(r, d, consts) - d["y_dt"]) for d in nt
        if d["y_dt"] is not None))
    best["ceil"] = max(CEIL_RULES, key=lambda r: sum(
        1 for d in nt if pred_ceil(r, d, pred_side(sr, d, consts), consts)
        == d["y_is_80"]))
    return best


def evaluate(dps_by_w: Dict[str, List[dict]]) -> Dict[str, Any]:
    wins = sorted(dps_by_w)
    out: List[Dict[str, Any]] = []
    chosen: Dict[str, Counter] = {k: Counter() for k in
                                  ("side", "price", "act", "time", "ceil")}
    for held in wins:
        tr = [d for w in wins if w != held for d in dps_by_w[w]]
        if not tr:
            continue
        c = train_consts(tr)
        r = select(tr, c)
        for k, v in r.items():
            chosen[k][v] += 1
        for d in dps_by_w[held]:
            s = pred_side(r["side"], d, c)
            p = pred_price(r["price"], d, s, c)
            a = pred_act(r["act"], d, s, p, c)
            covered = (s is not None and p is not None)
            out.append({**d, "p_side": s, "p_price": p, "p_act": a,
                        "p_dt": pred_dt(r["time"], d, c),
                        "p_80": pred_ceil(r["ceil"], d, s, c),
                        "covered": covered, "fold": held,
                        "rules": r})
    nt = [o for o in out if not o["is_terminal"]]
    cov = [o for o in nt if o["covered"]]

    def acc(rows, f):
        return round(sum(1 for o in rows if f(o)) / len(rows), 4) if rows \
            else None
    # baselines
    base_side_maj = Counter(o["y_side"] for o in nt).most_common(1)
    b_maj = (round(base_side_maj[0][1] / len(nt), 4) if nt else None)
    b_persist = acc(nt, lambda o: o["this_clip_outcome"] == o["y_side"])
    b_price_touch = acc(
        [o for o in nt if o.get((o["y_side"] or "up") + "_bid") is not None],
        lambda o: abs(o[(o["y_side"] or "up") + "_bid"] - o["y_price"])
        < TICK / 2)
    b_act_maj = (round(Counter(o["y_action"] for o in out).most_common(1)[0][1]
                       / len(out), 4) if out else None)
    tick_err = [abs(o["p_price"] - o["y_price"]) / TICK for o in cov
                if o["y_price"] is not None]
    tick_err.sort()
    dt_err = [abs(o["p_dt"] - o["y_dt"]) for o in nt if o["y_dt"] is not None]
    dt_err.sort()
    base_dt = sorted(o["y_dt"] for o in nt if o["y_dt"] is not None)
    bmed = base_dt[len(base_dt) // 2] if base_dt else 0
    base_dt_err = sorted(abs(x - bmed) for x in base_dt)
    s_acc = acc(cov, lambda o: o["p_side"] == o["y_side"])
    best_side_base = max(x for x in (b_maj, b_persist) if x is not None)
    return {
        "n_decision_points": len(out),
        "n_non_terminal": len(nt), "n_terminal": len(out) - len(nt),
        "coverage": round(len(cov) / len(nt), 4) if nt else None,
        "rules_selected_across_folds": {k: dict(v) for k, v in
                                        chosen.items()},
        "SIDE": {
            "accuracy_covered": s_acc,
            "accuracy_all_non_terminal": acc(
                nt, lambda o: o["covered"] and o["p_side"] == o["y_side"]),
            "baseline_majority": b_maj, "baseline_persistence": b_persist,
            "lift_over_best_baseline": (round(s_acc / best_side_base, 3)
                                        if s_acc and best_side_base else None),
        },
        "PRICE": {
            "exact_tick_hit_rate_covered": acc(
                cov, lambda o: o["y_price"] is not None
                and abs(o["p_price"] - o["y_price"]) < TICK / 2),
            "baseline_touch_hit_rate": b_price_touch,
            "tick_error_p50": (tick_err[len(tick_err) // 2]
                               if tick_err else None),
            "tick_error_p90": (tick_err[int(.9 * (len(tick_err) - 1))]
                               if tick_err else None),
            "tick_error_mean": (round(sum(tick_err) / len(tick_err), 3)
                                if tick_err else None),
        },
        "JOINT_SIDE_AND_PRICE": {
            "exact_accuracy_covered": acc(
                cov, lambda o: o["p_side"] == o["y_side"]
                and o["y_price"] is not None
                and abs(o["p_price"] - o["y_price"]) < TICK / 2),
        },
        "ACTION_TYPE": {
            "accuracy": acc(out, lambda o: o["p_act"] == o["y_action"]),
            "baseline_majority": b_act_maj,
            "confusion_true_to_pred": {
                "%s->%s" % k: v for k, v in
                Counter((o["y_action"], o["p_act"]) for o in out).items()},
        },
        "TIMING": {
            "mae_s": (round(sum(dt_err) / len(dt_err), 2)
                      if dt_err else None),
            "median_abs_error_s": (dt_err[len(dt_err) // 2]
                                   if dt_err else None),
            "baseline_median_mae_s": (
                round(sum(base_dt_err) / len(base_dt_err), 2)
                if base_dt_err else None),
        },
        "CLIP_CEILING_secondary": {
            "accuracy": acc(nt, lambda o: o["p_80"] == o["y_is_80"]),
            "base_rate_is_80": acc(nt, lambda o: o["y_is_80"]),
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--contract-length", required=True)
    ap.add_argument("--window-s", type=int, required=True)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    ev = [json.loads(l) for l in open(a.events, encoding="utf-8")
          if l.strip()]
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    for g in byw.values():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
    books, wins = load(a.parquet, contract_length=a.contract_length,
                       slugs=set(byw))
    dps_by_w: Dict[str, List[dict]] = {}
    for slug, seq in sorted(byw.items()):
        toks = wins[slug]["tokens"]
        tok = {"up": next(t for t, o in toks.items()
                          if str(o).lower() == "up"),
               "down": next(t for t, o in toks.items()
                            if str(o).lower() == "down")}
        pairs, member = find_pairs(seq)
        passive = [r for r in seq if _id(r) not in member]
        start = int(slug.rsplit("-", 1)[1])
        clips = build_clips(passive, books, gap_s=GAP_S, slug=slug,
                            clen=a.contract_length, start=start)
        if len(clips) < 2:
            continue
        dps_by_w[slug] = build_dps(slug, clips, pairs, books, tok, start,
                                   a.window_s)
    rep = {"contract_length": a.contract_length,
           "protocol": "PREREGISTRATION_NEXTACTION.md, leave-one-window-out",
           "n_windows": len(dps_by_w),
           "par_pairs_excluded_from_target": True,
           "quantity_is_not_a_target": True,
           **evaluate(dps_by_w)}
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "NEXTACTION_%s.json"
                           % a.contract_length), "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
