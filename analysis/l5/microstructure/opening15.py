"""OPENING15 — implemented strictly to PREREGISTRATION_OPENING15.md.

Subdivisions OPEN_A [0,30) and OPEN_B [30,60) are the only ones computed.
The prior 0-60 result is discovery evidence and is never counted as
confirmation here. Economics are computed last and may not touch rule
selection.
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
from analysis.l5.microstructure.clipstate import transitions
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id

TICK = 0.01
GAP_S = 10
CLIP80 = 80.0
TOL = 0.75
FEE = 0.07
PHASES = (("OPEN_A", 0, 30), ("OPEN_B", 30, 60))
COMBINED = ("OPEN_0_60", 0, 60)
N_FLOOR = 50
LIFT_BAR = 1.10
SIDE_RULES = ("S-MAJ", "S-PERSIST", "S-ALT", "S-LIGHT", "S-TOUCHFELL",
              "S-CLIPROOM")
PRICE_RULES = ("P-TOUCH", "P-PREV", "P-TOUCH-K", "P-COMPLEMENT")
ACT_RULES = ("A-EXPOSE", "A-PERSIST", "A-FROZEN")
TIME_RULES = ("T-MED", "T-COND")


def H(c: Counter) -> float:
    t = sum(c.values())
    return -sum((v / t) * math.log2(v / t) for v in c.values() if v) if t \
        else 0.0


def trans_lift(seqs: List[List[str]]) -> Dict[str, Any]:
    uni = Counter()
    cond: Dict[str, Counter] = defaultdict(Counter)
    for s in seqs:
        for a in s:
            uni[a] += 1
        for a, b in zip(s, s[1:]):
            cond[a][b] += 1
    n = sum(uni.values())
    tot = sum(sum(c.values()) for c in cond.values())
    if n < 2 or not tot:
        return {"n_transitions": n, "lift": None, "note": "too few"}
    base = max(uni.values()) / n
    hit = sum(max(c.values()) for c in cond.values())
    cH = sum(sum(c.values()) * H(c) for c in cond.values()) / tot
    return {"n_transitions": n, "n_ordered_pairs": tot,
            "base_rate_modal": round(base, 4),
            "first_order_accuracy": round(hit / tot, 4),
            "lift": round((hit / tot) / base, 3),
            "bits_gained": round(H(uni) - cH, 3)}


def book_feats(books, tok, t) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for side in ("up", "down"):
        bk = books.get(tok[side])
        s = bk.at(float(t), max_age_s=MAX_CARRY_S) if bk else None
        bb = (s or {}).get("best_bid")
        ba = (s or {}).get("best_ask")
        out[side + "_bid"] = bb
        out[side + "_ask"] = ba
        out[side + "_spread"] = (round(ba - bb, 6)
                                 if None not in (bb, ba) else None)
        out[side + "_depth_at_touch"] = (size_at_price(s, float(bb))
                                         if s and bb is not None else None)
        out[side + "_total_depth"] = (
            sum(float(x) for x in (s.get("bid_sizes") or [])) if s else None)
        out[side + "_levels"] = len((s or {}).get("bid_prices") or [])
        a5 = bk.at(float(t - 5), max_age_s=MAX_CARRY_S) if bk else None
        b5 = (a5 or {}).get("best_bid")
        out[side + "_move_5s"] = (round(bb - b5, 6)
                                  if None not in (bb, b5) else None)
    return out


def build_dps(slug, units, books, tok, start) -> List[Dict[str, Any]]:
    """One decision point per unit; target = the next PASSIVE clip."""
    inv = {"up": 0.0, "down": 0.0}
    prev_clip: Dict[str, Optional[dict]] = {"up": None, "down": None}
    consumed: Dict[Tuple[str, float], float] = defaultdict(float)
    prev_act: Optional[str] = None
    prev_t: Optional[int] = None
    pairs_so_far = 0
    dps: List[Dict[str, Any]] = []
    for i, u in enumerate(units):
        if u["kind"] == "CLIP":
            inv[u["outcome"]] += u["total"]
            consumed[(u["outcome"], round(u["price"], 4))] += u["total"]
        else:
            inv["up"] += u["total"] / 2.0
            inv["down"] += u["total"] / 2.0
            pairs_so_far += 1
        t = u["t_last"]
        nxt = next((v for v in units[i + 1:] if v["kind"] == "CLIP"), None)
        if nxt is None:
            act = "WITHDRAW"
        else:
            p = prev_clip.get(nxt["outcome"])
            act = ("MAINTAIN" if p is not None
                   and abs(p["price"] - nxt["price"]) < 1e-9 else "EXPOSE")
        room = {}
        for side in ("up", "down"):
            pc = prev_clip[side]
            c = (consumed[(side, round(pc["price"], 4))] if pc else 0.0)
            room[side] = round(max(0.0, CLIP80 - c), 4) if pc else CLIP80
        dp = {
            "market_slug": slug, "t": t, "secs_after_open": t - start,
            "unit_kind": u["kind"],
            "this_outcome": u.get("outcome"), "this_price": u.get("price"),
            "this_total": u.get("total"),
            "this_is_80": (abs(u.get("total", -1) - CLIP80) < TOL
                           if u["kind"] == "CLIP" else None),
            "this_n_fills": u.get("n_fills"),
            "frac_of_80_consumed": (round(u["total"] / CLIP80, 4)
                                    if u["kind"] == "CLIP" else None),
            "remaining_to_80_up": room["up"],
            "remaining_to_80_down": room["down"],
            "inv_up": round(inv["up"], 4), "inv_down": round(inv["down"], 4),
            "imbalance": round(inv["up"] - inv["down"], 4),
            "matched": round(min(inv["up"], inv["down"]), 4),
            "residual": round(abs(inv["up"] - inv["down"]), 4),
            "prev_act": prev_act,
            "prev_was_pair": (units[i - 1]["kind"] == "INJECTION"
                              if i else False),
            "pairs_so_far": pairs_so_far,
            "elapsed_since_prev_action_s": (None if prev_t is None
                                            else t - prev_t),
            "prev_price_up": (prev_clip["up"] or {}).get("price"),
            "prev_price_down": (prev_clip["down"] or {}).get("price"),
            "y_side": (nxt or {}).get("outcome"),
            "y_price": (nxt or {}).get("price"),
            "y_action": act,
            "y_dt": (None if nxt is None else nxt["t_first"] - t),
            "y_clip_t_first": (nxt or {}).get("t_first"),
            "is_terminal": nxt is None,
            **book_feats(books, tok, t),
        }
        dps.append(dp)
        if u["kind"] == "CLIP":
            prev_clip[u["outcome"]] = u
        prev_act = act
        prev_t = t
    return dps


def pred_side(rule, d, tr):
    if rule == "S-MAJ":
        return tr["maj_side"]
    if rule == "S-PERSIST":
        return d.get("this_outcome")
    if rule == "S-ALT":
        o = d.get("this_outcome")
        return None if o not in ("up", "down") else ("down" if o == "up"
                                                     else "up")
    if rule == "S-LIGHT":
        return "up" if d["inv_up"] <= d["inv_down"] else "down"
    if rule == "S-TOUCHFELL":
        a, b = d.get("up_move_5s"), d.get("down_move_5s")
        return None if a is None or b is None else ("up" if a < b else "down")
    if rule == "S-CLIPROOM":
        return ("up" if d["remaining_to_80_up"] >= d["remaining_to_80_down"]
                else "down")
    return None


def pred_price(rule, d, side, tr):
    if side is None:
        return None
    if rule == "P-TOUCH":
        return d.get(side + "_bid")
    if rule == "P-PREV":
        return d.get("prev_price_" + side)
    if rule == "P-TOUCH-K":
        b = d.get(side + "_bid")
        return None if b is None else round(b - tr["k"] * TICK, 4)
    if rule == "P-COMPLEMENT":
        o = "down" if side == "up" else "up"
        b = d.get(o + "_bid")
        return None if b is None else round(1.0 - b, 4)
    return None


def pred_act(rule, d, side, price, tr):
    if rule == "A-EXPOSE":
        return "EXPOSE"
    if rule == "A-PERSIST":
        return d.get("prev_act") or "EXPOSE"
    if rule == "A-FROZEN":
        pp = d.get("prev_price_" + side) if side else None
        if price is not None and pp is not None and abs(pp - price) < 1e-9:
            return "MAINTAIN"
        return "EXPOSE"
    return "EXPOSE"


def pred_dt(rule, d, tr):
    if rule == "T-COND":
        return tr["dt_by_act"].get(d.get("prev_act"), tr["dt_med"])
    return tr["dt_med"]


def consts(tr):
    nt = [d for d in tr if not d["is_terminal"]]
    sides = Counter(d["y_side"] for d in nt)
    offs = []
    for d in nt:
        b = d.get((d["y_side"] or "up") + "_bid")
        if b is not None and d["y_price"] is not None:
            offs.append(round((b - d["y_price"]) / TICK))
    dts = sorted(d["y_dt"] for d in nt if d["y_dt"] is not None)
    by = defaultdict(list)
    for d in nt:
        if d["y_dt"] is not None:
            by[d.get("prev_act")].append(d["y_dt"])
    return {"maj_side": sides.most_common(1)[0][0] if sides else "down",
            "k": sorted(offs)[len(offs) // 2] if offs else 0,
            "dt_med": dts[len(dts) // 2] if dts else 0,
            "dt_by_act": {k: sorted(v)[len(v) // 2] for k, v in by.items()}}


def select(tr, c):
    nt = [d for d in tr if not d["is_terminal"]]
    out = {}
    out["side"] = max(SIDE_RULES, key=lambda r: sum(
        1 for d in nt if pred_side(r, d, c) == d["y_side"]))
    sr = out["side"]
    out["price"] = max(PRICE_RULES, key=lambda r: sum(
        1 for d in nt
        if (lambda p: p is not None and d["y_price"] is not None
            and abs(p - d["y_price"]) < TICK / 2)(
                pred_price(r, d, pred_side(sr, d, c), c))))
    pr = out["price"]
    out["act"] = max(ACT_RULES, key=lambda r: sum(
        1 for d in tr if pred_act(
            r, d, pred_side(sr, d, c),
            pred_price(pr, d, pred_side(sr, d, c), c), c) == d["y_action"]))
    out["time"] = min(TIME_RULES, key=lambda r: sum(
        abs(pred_dt(r, d, c) - d["y_dt"]) for d in nt
        if d["y_dt"] is not None))
    return out


def lowo(dps_by_w: Dict[str, List[dict]]) -> List[dict]:
    wins = sorted(dps_by_w)
    out = []
    for held in wins:
        tr = [d for w in wins if w != held for d in dps_by_w[w]]
        nt = [d for d in tr if not d["is_terminal"]]
        if len(nt) < 5:
            continue
        c = consts(tr)
        r = select(tr, c)
        for d in dps_by_w[held]:
            s = pred_side(r["side"], d, c)
            p = pred_price(r["price"], d, s, c)
            out.append({**d, "p_side": s, "p_price": p,
                        "p_act": pred_act(r["act"], d, s, p, c),
                        "p_dt": pred_dt(r["time"], d, c),
                        "covered": s is not None, "rules": r, "fold": held})
    return out


def metrics(rows: List[dict], label: str) -> Dict[str, Any]:
    nt = [o for o in rows if not o["is_terminal"]]
    cov = [o for o in nt if o["covered"]]
    if not nt:
        return {"phase": label, "n": 0, "note": "no decision points"}
    maj = Counter(o["y_side"] for o in nt).most_common(1)[0][1] / len(nt)
    per = sum(1 for o in nt if o["this_outcome"] == o["y_side"]) / len(nt)
    best = max(maj, per)
    acc = (sum(1 for o in cov if o["p_side"] == o["y_side"]) / len(cov)
           if cov else None)
    te = sorted(abs(o["p_price"] - o["y_price"]) / TICK for o in cov
                if o["p_price"] is not None and o["y_price"] is not None)
    return {
        "phase": label,
        "n_decision_points": len(rows), "n_non_terminal": len(nt),
        "n_covered": len(cov),
        "coverage": round(len(cov) / len(nt), 4),
        "MEETS_N_FLOOR": len(nt) >= N_FLOOR,
        "side_accuracy_covered": round(acc, 4) if acc is not None else None,
        "side_accuracy_all": round(
            sum(1 for o in nt if o["covered"]
                and o["p_side"] == o["y_side"]) / len(nt), 4),
        "baseline_majority": round(maj, 4),
        "baseline_persistence": round(per, 4),
        "LIFT_over_best_baseline": (round(acc / best, 3)
                                    if acc and best else None),
        "MEETS_LIFT_BAR": bool(acc and best and acc / best >= LIFT_BAR),
        "price_exact_tick": round(
            sum(1 for o in cov if o["p_price"] is not None
                and o["y_price"] is not None
                and abs(o["p_price"] - o["y_price"]) < TICK / 2)
            / len(cov), 4) if cov else None,
        "price_tick_error_p50": te[len(te) // 2] if te else None,
        "action_accuracy": round(
            sum(1 for o in rows if o["p_act"] == o["y_action"])
            / len(rows), 4),
        "action_baseline_majority": round(
            Counter(o["y_action"] for o in rows).most_common(1)[0][1]
            / len(rows), 4),
        "rules_selected": dict(Counter(json.dumps(o["rules"], sort_keys=True)
                                       for o in rows)),
    }


def jackknife(rows: List[dict]) -> Dict[str, Any]:
    """PREREGISTERED test 8.4, evaluated FIRST and overriding the others:
    drop the two windows contributing the most correct side predictions and
    recompute the 0-60 lift."""
    nt = [o for o in rows if not o["is_terminal"] and o["covered"]]
    by = Counter(o["market_slug"] for o in nt
                 if o["p_side"] == o["y_side"])
    top2 = [w for w, _ in by.most_common(2)]
    kept = [o for o in rows if o["market_slug"] not in top2]
    full = metrics(rows, "OPEN_0_60")
    red = metrics(kept, "OPEN_0_60_minus_top2")
    fl = full.get("LIFT_over_best_baseline")
    rl = red.get("LIFT_over_best_baseline")
    # THE TEST IS VACUOUS WHEN THE FULL LIFT NEVER REACHED THE BAR. It asks
    # whether removing two windows DROPS the lift below 1.10; if the lift was
    # already below 1.10 with every window included, a "below 1.10" answer
    # says nothing about those two windows. Reporting it as a positive
    # finding would be wrong, so it is reported as inapplicable instead.
    vacuous = fl is None or fl < LIFT_BAR
    return {
        "top2_contributing_windows": top2,
        "correct_predictions_by_window": dict(by.most_common()),
        "full_lift": fl,
        "lift_without_top2": rl,
        "n_without_top2": red.get("n_non_terminal"),
        "TEST_IS_VACUOUS": vacuous,
        "vacuous_because": ("the full-sample lift never reached the %.2f bar, "
                            "so removing windows cannot be what pushed it "
                            "below" % LIFT_BAR) if vacuous else None,
        "DRIVEN_BY_FEW_WINDOWS": (None if vacuous else
                                  bool(rl is not None and rl < LIFT_BAR)),
        "reduced_metrics": red,
    }


def economics(rows: List[dict], ev_by_window: Dict[str, List[dict]],
              winners: Dict[str, Dict[str, bool]]) -> Dict[str, Any]:
    """Computed LAST, on CORRECTLY PREDICTED actions only. Never feeds back
    into rule selection -- the rules and predictions are already fixed."""
    hits = [o for o in rows if not o["is_terminal"] and o["covered"]
            and o["p_side"] == o["y_side"]]
    tot_q = tot_cost = tot_fee = tot_payout = 0.0
    n_fills = 0
    per_side = Counter()
    for o in hits:
        fills = [f for f in ev_by_window.get(o["market_slug"], [])
                 if f["t"] >= (o["y_clip_t_first"] or 0)
                 and f["outcome"] == o["y_side"]
                 and abs(f["price"] - (o["y_price"] or -1)) < 1e-9]
        # the clip the prediction pointed at: fills at that price from its
        # first-fill second onwards, on that side
        for f in fills[:1] if False else fills:
            if f["t"] > (o["y_clip_t_first"] or 0) + 60:
                continue
            q, p = f["quantity"], f["price"]
            tot_q += q
            tot_cost += p * q
            tot_fee += FEE * p * (1 - p) * q
            w = winners.get(o["market_slug"], {}).get(o["y_side"])
            tot_payout += q if w else 0.0
            n_fills += 1
            per_side[o["y_side"]] += 1
    return {
        "scope": ("fills of the correctly-predicted next clip, at the "
                  "predicted side and price, within 60s of its first fill"),
        "n_correct_side_predictions": len(hits),
        "n_fills_matched": n_fills,
        "shares": round(tot_q, 4),
        "cost_usd": round(tot_cost, 4),
        "model_implied_fee_usd": round(tot_fee, 4),
        "settlement_payout_usd": round(tot_payout, 4),
        "gross_usd": round(tot_payout - tot_cost, 4),
        "net_after_model_fee_usd": round(tot_payout - tot_cost - tot_fee, 4),
        "vwap": round(tot_cost / tot_q, 6) if tot_q else None,
        "by_side": dict(per_side),
        "fee_note": ("0.07*p*(1-p), from L5_OPERATING_MODEL.md; model-implied, "
                     "NOT a credited venue fee"),
        "caveat": ("settlement is a DIAGNOSTIC: these are L5's own realised "
                   "fills, so this measures the economics of the actions the "
                   "rule correctly anticipated, not of a strategy we ran. "
                   "Economics did not select or alter any rule."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    ev = [json.loads(l) for l in open(a.events, encoding="utf-8")
          if l.strip()]
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    for g in byw.values():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
    books, wins = load(a.parquet, contract_length="15m", slugs=set(byw))

    units_by_w: Dict[str, List[dict]] = {}
    winners: Dict[str, Dict[str, bool]] = {}
    tok_by_w = {}
    for slug, seq in sorted(byw.items()):
        toks = wins[slug]["tokens"]
        tok = {"up": next(t for t, o in toks.items()
                          if str(o).lower() == "up"),
               "down": next(t for t, o in toks.items()
                            if str(o).lower() == "down")}
        tok_by_w[slug] = tok
        winners[slug] = {"up": wins[slug]["winner"].get(tok["up"]),
                         "down": wins[slug]["winner"].get(tok["down"])}
        pairs, member = find_pairs(seq)
        start = int(slug.rsplit("-", 1)[1])
        passive = [r for r in seq if _id(r) not in member]
        clips = build_clips(passive, books, gap_s=GAP_S, slug=slug,
                            clen="15m", start=start)
        u = [{"kind": "CLIP", "outcome": c["outcome"], "price": c["price"],
              "t_first": c["t_first"], "t_last": c["t_last"],
              "secs_after_open_first": c["secs_after_open_first"],
              "total": c["clip_total_shares"], "n_fills": c["n_fills"]}
             for c in clips]
        u += [{"kind": "INJECTION", "outcome": "both", "price": None,
               "t_first": p["up"]["t"], "t_last": p["up"]["t"],
               "secs_after_open_first": p["up"]["secs_since_open"],
               "total": 2 * p["up"]["quantity"], "n_fills": 2}
              for p in pairs]
        u.sort(key=lambda x: (x["t_first"], x["kind"]))
        units_by_w[slug] = u

    rep: Dict[str, Any] = {
        "protocol": "PREREGISTRATION_OPENING15.md",
        "prior_0_60_result_is_discovery_not_validation": True,
        "subdivisions_are_the_only_ones_computed": [p[0] for p in PHASES],
        "n_floor": N_FLOOR, "lift_bar": LIFT_BAR,
        "phases": {}, "per_window": [],
    }
    all_rows: Dict[str, List[dict]] = {}
    for name, lo, hi in list(PHASES) + [COMBINED]:
        dps_by_w = {}
        seqs = []
        for slug, u in units_by_w.items():
            sel = [x for x in u if lo <= x["secs_after_open_first"] < hi]
            if len(sel) < 2:
                continue
            start = int(slug.rsplit("-", 1)[1])
            dps_by_w[slug] = build_dps(slug, sel, books, tok_by_w[slug],
                                       start)
            seqs.append([t["transition"] for t in transitions(sel)])
        rows = lowo(dps_by_w)
        all_rows[name] = rows
        rep["phases"][name] = {
            "window_s": [lo, hi],
            "TRANSITION_LIFT_comparable_to_discovery": trans_lift(seqs),
            "LOWO_PREDICTION": metrics(rows, name),
        }

    # per-window replay: every window's own contribution, so no mean hides it
    for slug in sorted(units_by_w):
        u = [x for x in units_by_w[slug] if x["secs_after_open_first"] < 60]
        r60 = [o for o in all_rows["OPEN_0_60"]
               if o["market_slug"] == slug and not o["is_terminal"]
               and o["covered"]]
        bk = books.get(tok_by_w[slug]["up"])
        start = int(slug.rsplit("-", 1)[1])
        nobs = sum(1 for s in ("up", "down")
                   for ts in (books.get(tok_by_w[slug][s]).t
                              if books.get(tok_by_w[slug][s]) else [])
                   if start <= ts < start + 60)
        rep["per_window"].append({
            "market_slug": slug,
            "archived_book_observations_0_60s": nobs,
            "units_0_60s": len(u),
            "clips": sum(1 for x in u if x["kind"] == "CLIP"),
            "dollar_pairs": sum(1 for x in u if x["kind"] == "INJECTION"),
            "first_unit_s": (u[0]["secs_after_open_first"] if u else None),
            "first_unit_kind": (u[0]["kind"] if u else None),
            "decision_points_scored": len(r60),
            "side_correct": sum(1 for o in r60
                                if o["p_side"] == o["y_side"]),
            "side_accuracy": (round(sum(1 for o in r60
                                        if o["p_side"] == o["y_side"])
                                    / len(r60), 4) if r60 else None),
            "sequence": [("PAIR" if x["kind"] == "INJECTION"
                          else "%s@%.2f x%.1f" % (x["outcome"][0].upper(),
                                                  x["price"], x["total"]))
                         for x in u],
        })

    rep["JACKKNIFE_evaluated_first"] = jackknife(all_rows["OPEN_0_60"])
    rep["ECONOMICS_computed_last"] = economics(
        all_rows["OPEN_0_60"], byw, winners)

    # ---- classification, by the preregistered bar
    A = rep["phases"]["OPEN_A"]["LOWO_PREDICTION"]
    B = rep["phases"]["OPEN_B"]["LOWO_PREDICTION"]
    C = rep["phases"]["OPEN_0_60"]["LOWO_PREDICTION"]
    jk = rep["JACKKNIFE_evaluated_first"]
    okA = A.get("MEETS_N_FLOOR") and A.get("MEETS_LIFT_BAR")
    okB = B.get("MEETS_N_FLOOR") and B.get("MEETS_LIFT_BAR")
    okC = C.get("MEETS_N_FLOOR") and C.get("MEETS_LIFT_BAR")
    # The concentration verdict is reported SEPARATELY for the two measures,
    # because they disagree and collapsing them into one label would hide
    # exactly the distinction this study exists to make.
    tA = rep["phases"]["OPEN_A"]["TRANSITION_LIFT_comparable_to_discovery"]
    tB = rep["phases"]["OPEN_B"]["TRANSITION_LIFT_comparable_to_discovery"]
    def _desc(t):
        return bool((t.get("lift") or 0) >= LIFT_BAR
                    and (t.get("n_transitions") or 0) >= N_FLOOR)
    dA, dB = _desc(tA), _desc(tB)
    if jk.get("DRIVEN_BY_FEW_WINDOWS"):
        conc_pred = "4_DRIVEN_BY_A_FEW_WINDOWS"
    elif okA and okB:
        conc_pred = "3_CONSISTENT_ACROSS_BOTH"
    elif okA:
        conc_pred = "1_CONCENTRATED_IN_0_30"
    elif okB:
        conc_pred = "2_CONCENTRATED_IN_30_60"
    else:
        conc_pred = "NONE_MEETS_THE_BAR"
    conc_desc = ("3_CONSISTENT_ACROSS_BOTH" if dA and dB else
                 "1_CONCENTRATED_IN_0_30" if dA else
                 "2_CONCENTRATED_IN_30_60" if dB else "NONE_MEETS_THE_BAR")
    conc = {"on_LOWO_PREDICTION": conc_pred,
            "on_DESCRIPTIVE_transition_lift": conc_desc,
            "note": ("the two measures disagree; the predictive one is the "
                     "one that bears on deployment")}
    econ_ok = rep["ECONOMICS_computed_last"]["net_after_model_fee_usd"] > 0
    if not (okA or okB or okC) or jk["DRIVEN_BY_FEW_WINDOWS"]:
        verdict = ("REJECT" if not any(
            p["TRANSITION_LIFT_comparable_to_discovery"].get("lift") or 0
            >= LIFT_BAR for p in rep["phases"].values())
            else "DESCRIPTIVE")
    else:
        verdict = ("PREDICTIVE + ECONOMICALLY PROMISING" if econ_ok
                   else "PREDICTIVE")
    rep["CONCENTRATION_VERDICT"] = conc
    rep["FINAL_CLASSIFICATION"] = verdict
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "OPENING15.json"), "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in rep.items()
                      if k != "per_window"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
