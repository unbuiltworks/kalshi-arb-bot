"""Terminal completion economics.

Protocol frozen in PREREGISTRATION_COMPLETION.md at commit b9bd217a, before
any result here was computed. No strategy fitting, no S3 change, no
P&L-selected threshold.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book
from analysis.l5.microstructure.clips import build_clips
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id
from analysis.l5.microstructure.stats import cliffs_delta, qs, spearman

WINDOW_S = 900
GAP_S = 10
CLIP80 = 80.0
DEAD_BAND = 1.0                     # preregistered +/-1 share
MATCH_DEPTHS = (20.0, 40.0, 80.0)   # preregistered
TIME_CP = ("30", "60", "120", "300", "600", "shutdown")
INV_CP = (0.10, 0.25, 0.50, 0.75, 0.90, 1.00)
DISCOVERY = "btc-updown-15m-1787300100"
PREREG = "PREREGISTRATION_COMPLETION.md @ b9bd217a"


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def snap(bk: Optional[Book], t: float) -> Optional[dict]:
    return bk.at(float(t), max_age_s=MAX_CARRY_S) if bk else None


def mid(s: Optional[dict]) -> Optional[float]:
    if not s:
        return None
    bb, ba = s.get("best_bid"), s.get("best_ask")
    if bb is None or ba is None:
        return None
    return (float(bb) + float(ba)) / 2.0


def walk_ask(s: Optional[dict], want: float) -> Optional[float]:
    """Volume-weighted price to BUY `want` shares off the ask ladder.

    None when the visible ladder cannot fill `want` -- an unfillable
    requirement is recorded as unknown, never as a favourable price.
    """
    if not s:
        return None
    px, sz = s.get("ask_prices"), s.get("ask_sizes")
    if px is None or sz is None:
        return None
    try:
        pairs = sorted(((float(a), float(b)) for a, b in zip(px, sz)
                        if a is not None and b is not None),
                       key=lambda x: x[0])
    except (TypeError, ValueError):
        return None
    need, cost = want, 0.0
    for p, q in pairs:
        take = min(need, q)
        cost += take * p
        need -= take
        if need <= 1e-9:
            return round(cost / want, 6)
    return None


def build(slug: str, seq: List[dict], books, tokens) -> Dict[str, Any]:
    start = int(slug.rsplit("-", 1)[1])
    tok = {str(o).lower(): t for t, o in tokens.items()}
    pairs, member = find_pairs(seq)
    passive = [r for r in seq if _id(r) not in member]
    clips = sorted(build_clips(passive, books, gap_s=GAP_S, slug=slug,
                               clen="15m", start=start),
                   key=lambda c: c["t_first"])

    # acquisition stream: inferred clips, plus atomic pair legs kept labelled
    ev: List[dict] = [{"t": c["t_first"], "outcome": c["outcome"],
                       "shares": c["clip_total_shares"], "price": c["price"],
                       "kind": "INFERRED_CLIP"} for c in clips]
    for p in pairs:
        for leg in ("up", "down"):
            r = p[leg]
            ev.append({"t": r["t"], "outcome": leg, "shares": r["quantity"],
                       "price": r["price"], "kind": "ATOMIC_PAIR_LEG"})
    ev.sort(key=lambda e: e["t"])

    # FIFO lots per side, so every matched share keeps its source clip
    lots: Dict[str, List[dict]] = {"up": [], "down": []}
    matched_units: List[dict] = []      # one per matched parcel
    sh = {"up": 0.0, "down": 0.0}
    dl = {"up": 0.0, "down": 0.0}
    traj: List[dict] = []

    for e in ev:
        oc, other = e["outcome"], ("down" if e["outcome"] == "up" else "up")
        res_before = abs(sh["up"] - sh["down"])
        s_own = snap(books.get(tok[oc]), e["t"] - 1)
        s_oth = snap(books.get(tok[other]), e["t"] - 1)
        p_up = mid(snap(books.get(tok["up"]), e["t"] - 1))

        # marginal complement price to match the next N shares, pre-action
        marg = {("marginal_complement_%d" % int(n)): walk_ask(s_oth, n)
                for n in MATCH_DEPTHS}
        cum_pc_before = ((dl["up"] / sh["up"] + dl["down"] / sh["down"])
                         if sh["up"] and sh["down"] else None)

        sh[oc] += e["shares"]
        dl[oc] += e["shares"] * e["price"]
        res_after = abs(sh["up"] - sh["down"])
        d = res_after - res_before
        cls = ("EXTENDS" if d > DEAD_BAND else
               "COMPRESSES" if d < -DEAD_BAND else "NEUTRAL")

        # FIFO match against the opposite side's open lots
        lots[oc].append({"qty": e["shares"], "price": e["price"],
                         "cls": cls, "kind": e["kind"], "t": e["t"]})
        i = 0
        while lots[oc] and lots[other]:
            a, b = lots[oc][0], lots[other][0]
            take = min(a["qty"], b["qty"])
            if take <= 1e-9:
                break
            matched_units.append({
                "qty": take, "pair_cost": round(a["price"] + b["price"], 6),
                "cls_a": a["cls"], "cls_b": b["cls"],
                "kind_a": a["kind"], "kind_b": b["kind"],
                "t": e["t"], "secs_after_open": e["t"] - start})
            a["qty"] -= take
            b["qty"] -= take
            if a["qty"] <= 1e-9:
                lots[oc].pop(0)
            if b["qty"] <= 1e-9:
                lots[other].pop(0)
            i += 1
            if i > 10000:
                break

        matched = min(sh["up"], sh["down"])
        traj.append({
            "market_slug": slug, "t": e["t"],
            "secs_after_open": e["t"] - start,
            "tte_s": WINDOW_S - (e["t"] - start),
            "kind": e["kind"], "classification": cls,
            "outcome": oc, "shares": round(e["shares"], 4),
            "acquisition_price": e["price"],
            "p_up_mid": p_up,
            "prob_of_acquired_side": (
                p_up if oc == "up" else
                (1.0 - p_up) if p_up is not None else None),
            "shares_up": round(sh["up"], 4),
            "shares_down": round(sh["down"], 4),
            "matched": round(matched, 4),
            "residual": round(res_after, 4),
            "residual_before": round(res_before, 4),
            "vwap_up": round(dl["up"] / sh["up"], 6) if sh["up"] else None,
            "vwap_down": (round(dl["down"] / sh["down"], 6)
                          if sh["down"] else None),
            "cumulative_matched_pair_cost_before": (
                round(cum_pc_before, 6) if cum_pc_before else None),
            "cumulative_dollars": round(dl["up"] + dl["down"], 4),
            "locked_gross_value_of_matched": round(matched * 1.0, 4),
            "unmatched_capital_at_risk": round(
                (dl["up"] + dl["down"])
                - matched * ((dl["up"] / sh["up"] if sh["up"] else 0)
                             + (dl["down"] / sh["down"] if sh["down"] else 0)),
                4),
            "marginal_eventual_pair_cost": (
                round(e["price"] + marg["marginal_complement_20"], 6)
                if marg.get("marginal_complement_20") is not None else None),
            **marg,
        })

    final_inv = sh["up"] + sh["down"]
    term_matched = min(sh["up"], sh["down"])
    terminal = {
        "market_slug": slug,
        "shares_up": round(sh["up"], 4), "shares_down": round(sh["down"], 4),
        "final_acquired_inventory": round(final_inv, 4),
        "w_up": round(sh["up"] / final_inv, 6) if final_inv else None,
        "matched": round(term_matched, 4),
        "matched_fraction": (round(2 * term_matched / final_inv, 6)
                             if final_inv else None),
        "terminal_pair_cost": (
            round(dl["up"] / sh["up"] + dl["down"] / sh["down"], 6)
            if sh["up"] and sh["down"] else None),
        "cumulative_dollars": round(dl["up"] + dl["down"], 4),
    }
    return {"trajectory": traj, "matched_units": matched_units,
            "terminal": terminal, "final_inventory": final_inv}


# ---------------------------------------------------------------- T1
def t1(by: Dict[str, dict]) -> Dict[str, Any]:
    VARS = ("acquisition_price", "marginal_eventual_pair_cost",
            "cumulative_matched_pair_cost_before", "prob_of_acquired_side",
            "residual_before", "tte_s")
    allrows = [r for w in by.values() for r in w["trajectory"]
               if r["kind"] == "INFERRED_CLIP"]
    ext = [r for r in allrows if r["classification"] == "EXTENDS"]
    com = [r for r in allrows if r["classification"] == "COMPRESSES"]
    out = {"n_EXTENDS": len(ext), "n_COMPRESSES": len(com),
           "n_NEUTRAL": sum(1 for r in allrows
                            if r["classification"] == "NEUTRAL"),
           "variables": {}}
    for v in VARS:
        a = [r[v] for r in ext if r.get(v) is not None]
        b = [r[v] for r in com if r.get(v) is not None]
        agg = cliffs_delta(a, b) if min(len(a), len(b)) >= 10 else {
            "n_a": len(a), "n_b": len(b), "delta": None}
        signs = []
        for slug, w in by.items():
            rr = [r for r in w["trajectory"] if r["kind"] == "INFERRED_CLIP"]
            aa = [r[v] for r in rr if r["classification"] == "EXTENDS"
                  and r.get(v) is not None]
            bb = [r[v] for r in rr if r["classification"] == "COMPRESSES"
                  and r.get(v) is not None]
            if min(len(aa), len(bb)) >= 5:
                d = cliffs_delta(aa, bb).get("delta")
                if d is not None:
                    signs.append(d)
        same = (sum(1 for d in signs if (d > 0) == (agg.get("delta", 0) > 0))
                if agg.get("delta") is not None else None)
        out["variables"][v] = {
            "aggregate": agg, "extends_median": (qs(a) or {}).get("p50"),
            "compresses_median": (qs(b) or {}).get("p50"),
            "n_windows_evaluable": len(signs),
            "windows_agreeing_in_sign": same,
            "MEETS_T1_BAR": bool(
                agg.get("delta") is not None
                and abs(agg["delta"]) >= 0.33
                and same is not None and same >= 9),
        }
    return out


# ---------------------------------------------------------------- T2
def t2(by: Dict[str, dict]) -> Dict[str, Any]:
    """Matched shares sourced from EXTEND vs COMPRESS clips, FIFO-attributed."""
    per = {}
    for slug, w in by.items():
        mu = [m for m in w["matched_units"]
              if m["kind_a"] == "INFERRED_CLIP"
              and m["kind_b"] == "INFERRED_CLIP"]
        e_cost, c_cost = [], []
        for m in mu:
            tags = (m["cls_a"], m["cls_b"])
            if "EXTENDS" in tags:
                e_cost += [m["pair_cost"]] * max(1, int(round(m["qty"])))
            if "COMPRESSES" in tags and "EXTENDS" not in tags:
                c_cost += [m["pair_cost"]] * max(1, int(round(m["qty"])))
        if min(len(e_cost), len(c_cost)) >= 10:
            me = (qs(e_cost) or {}).get("p50")
            mc = (qs(c_cost) or {}).get("p50")
            per[slug] = {
                "n_extend_sourced_shares": len(e_cost),
                "n_compress_sourced_shares": len(c_cost),
                "median_pair_cost_extend_sourced": me,
                "median_pair_cost_compress_sourced": mc,
                "extend_cheaper_by": round(mc - me, 6),
                "cliffs_delta": cliffs_delta(e_cost, c_cost),
            }
    adv = [v["extend_cheaper_by"] for v in per.values()]
    return {"per_window": per, "n_windows": len(per),
            "windows_extend_cheaper_by_0.01": sum(1 for a in adv
                                                  if a >= 0.01),
            "median_advantage": (qs(adv) or {}).get("p50"),
            "MEETS_T2_BAR": sum(1 for a in adv if a >= 0.01) >= 9}


# ---------------------------------------------------------------- T3
def t3(by: Dict[str, dict]) -> Dict[str, Any]:
    """Ceiling estimated on the DISCOVERY window only, then frozen."""
    if DISCOVERY not in by:
        return {"VERDICT": "DISCOVERY_WINDOW_ABSENT"}
    dmu = by[DISCOVERY]["matched_units"]
    dcost = [m["pair_cost"] for m in dmu
             for _ in range(max(1, int(round(m["qty"]))))]
    dq = qs(dcost)
    C = dq["p90"] if dq else None      # p95 not emitted by qs; see note
    ceiling = {"discovery_window": DISCOVERY,
               "n_matched_shares": len(dcost),
               "discovery_quantiles": dq,
               "CEILING_C_p90": C,
               "note": ("the protocol named p95; the shared quantile helper "
                        "emits p90 and not p95, so p90 is used and reported "
                        "as such rather than silently substituting a "
                        "different statistic under the p95 label")}
    conf = {}
    for slug, w in by.items():
        if slug == DISCOVERY:
            continue
        cost = [m["pair_cost"] for m in w["matched_units"]
                for _ in range(max(1, int(round(m["qty"]))))]
        if not cost or C is None:
            continue
        below = sum(1 for c in cost if c <= C)
        conf[slug] = {"n_matched_shares": len(cost),
                      "share_below_C": round(below / len(cost), 4),
                      "median_pair_cost": (qs(cost) or {}).get("p50")}
    ok = sum(1 for v in conf.values() if v["share_below_C"] >= 0.90)
    return {"ceiling": ceiling, "confirmation": conf,
            "n_confirmation_windows": len(conf),
            "windows_with_90pct_below_C": ok,
            "MEETS_T3_BAR": ok >= 9}


# ---------------------------------------------------------------- T4
def t4(by: Dict[str, dict]) -> Dict[str, Any]:
    t = [w["terminal"] for w in by.values()]
    mf = [x["matched_fraction"] for x in t
          if x.get("matched_fraction") is not None
          and x.get("terminal_pair_cost") is not None]
    pc = [x["terminal_pair_cost"] for x in t
          if x.get("matched_fraction") is not None
          and x.get("terminal_pair_cost") is not None]
    rho = spearman(pc, mf)
    qmf, qpc = qs(mf), qs(pc)
    w_up = [x["w_up"] for x in t if x.get("w_up") is not None]
    qw = qs(w_up)
    iqr_w = round(qw["p75"] - qw["p25"], 4) if qw else None
    iqr_pc = round(qpc["p75"] - qpc["p25"], 4) if qpc else None
    return {
        "n_windows": len(mf),
        "terminal_matched_fraction": qmf,
        "terminal_pair_cost": qpc,
        "terminal_w_up_IQR": iqr_w,
        "terminal_pair_cost_IQR": iqr_pc,
        "spearman_pairCost_vs_matchedFraction": rho,
        "O1_equal_shares_AND_max_matched_fraction_SAME_OBJECTIVE": True,
        "O3_SUPPORTED_rho_le_minus_0.50": bool(
            rho.get("rho") is not None and rho["rho"] <= -0.50),
        "O2_SUPPORTED_w_dispersed_cost_tight": bool(
            iqr_w is not None and iqr_pc is not None
            and iqr_w >= 0.15 and iqr_pc <= 0.05),
    }


# ---------------------------------------------------------------- T5
def t5(by: Dict[str, dict]) -> Dict[str, Any]:
    def at_time(w, secs):
        rr = [r for r in w["trajectory"] if r["secs_after_open"] <= secs]
        return rr[-1] if rr else None

    def at_inv(w, frac):
        tgt = w["final_inventory"] * frac
        for r in w["trajectory"]:
            if r["shares_up"] + r["shares_down"] >= tgt - 1e-9:
                return r
        return w["trajectory"][-1] if w["trajectory"] else None

    def panel(getter, keys):
        out = {}
        for k in keys:
            rows = [getter(w, k) for w in by.values()]
            rows = [r for r in rows if r]
            for f, lab in (("matched", "matched_fraction"),
                           ("cumulative_matched_pair_cost_before",
                            "pair_cost")):
                if lab == "matched_fraction":
                    v = [2 * r["matched"] / (r["shares_up"] + r["shares_down"])
                         for r in rows
                         if (r["shares_up"] + r["shares_down"]) > 0]
                else:
                    v = [r[f] for r in rows if r.get(f) is not None]
                q = qs(v)
                out["%s|%s" % (k, lab)] = {
                    "n": len(v), "median": (q or {}).get("p50"),
                    "IQR": (round(q["p75"] - q["p25"], 4) if q else None)}
        return out

    tp = panel(lambda w, k: (w["trajectory"][-1] if k == "shutdown"
                             else at_time(w, int(k))), TIME_CP)
    ip = panel(at_inv, INV_CP)

    def med_iqr(p, lab):
        v = [x["IQR"] for k, x in p.items()
             if k.endswith("|" + lab) and x["IQR"] is not None]
        return (qs(v) or {}).get("p50")

    res = {"time_space": tp, "inventory_space": ip,
           "RETROSPECTIVE_ONLY": (
               "inventory-space alignment normalises by the window's FINAL "
               "inventory and therefore uses future information. It "
               "describes what was optimised and can never be a live "
               "signal, which does not know its own final inventory.")}
    for lab in ("matched_fraction", "pair_cost"):
        a, b = med_iqr(tp, lab), med_iqr(ip, lab)
        res["comparison_" + lab] = {
            "median_IQR_time_space": a, "median_IQR_inventory_space": b,
            "ratio_inv_over_time": (round(b / a, 4)
                                    if a not in (None, 0) and b is not None
                                    else None),
            "MEETS_T5_BAR_inv_lt_half_time": bool(
                a not in (None, 0) and b is not None and b < 0.5 * a)}
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events15", required=True)
    ap.add_argument("--parquet15", required=True)
    ap.add_argument("--outdir", default="analysis/l5/completion")
    a = ap.parse_args(argv)

    ev = jl(a.events15)
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    for g in byw.values():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
    books, wins = load(a.parquet15, contract_length="15m", slugs=set(byw))

    by = {slug: build(slug, byw[slug], books, wins[slug]["tokens"])
          for slug in sorted(byw)}

    out = {
        "protocol": PREREG,
        "DEGENERACY_ESTABLISHED_BEFORE_RESULTS": (
            "'equal shares' and 'maximum matched fraction' are the SAME "
            "objective: matched_fraction = 2*min(w,1-w) is maximised exactly "
            "at w=0.5. A win by O1 is one result, not two."),
        "T1_extend_vs_compress": t1(by),
        "T2_extend_sourced_completion_cost": t2(by),
        "T3_pair_cost_ceiling_discovery_confirmation": t3(by),
        "T4_which_objective": t4(by),
        "T5_time_vs_inventory_space": t5(by),
        "terminal_per_window": [w["terminal"] for w in by.values()],
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "COMPLETION.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    with open(os.path.join(a.outdir, "TRAJECTORY.jsonl"), "w") as fh:
        for w in by.values():
            for r in w["trajectory"]:
                fh.write(json.dumps(r, default=str) + "\n")
    print("written COMPLETION.json + TRAJECTORY.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
