"""Sections 3, 5, 6, 7 and 9 of the overnight brief.

S3 withdrawal · S5 ladder geometry · S6 clip consumption/replenishment ·
S7 transaction-level action templates · S9 80-share parameter stability.

All descriptive characterisations as preregistered, with the variables named
in each section. Nothing here is a strategy and no P&L is optimized.
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
from analysis.l5.microstructure.stats import cliffs_delta, qs

TICK = 0.01
GAP_S = 10
CLIP80 = 80.0
TOL = 0.75


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


# ------------------------------------------------------------------ S7
def templates(ev: List[dict]) -> Dict[str, Any]:
    """How many apparent 'actions' collapse into one transaction?

    Atomicity is taken from the TRANSACTION HASH, never from a shared second
    -- the brief is explicit and the earlier near-miss on the par pairs is
    why.
    """
    bytx: Dict[Any, List[dict]] = defaultdict(list)
    for r in ev:
        bytx[(r["market_slug"], r.get("tx"))].append(r)
    tmpl = Counter()
    vol = Counter()
    fills = Counter()
    rows = []
    for (slug, tx), g in bytx.items():
        outs = {r["outcome"] for r in g}
        pxs = {round(r["price"], 4) for r in g}
        roles = {r.get("l5_role") for r in g}
        q = sum(r["quantity"] for r in g)
        if len(g) == 1:
            t = "T1_SINGLE_LEG"
        elif len(outs) == 2 and len(g) == 2 and abs(
                g[0]["quantity"] - g[1]["quantity"]) < 1e-6 and abs(
                sum(r["price"] for r in g) - 1.0) < 1e-9:
            t = "T2_PAR_PAIR_equal_qty_complementary"
        elif len(outs) == 1 and len(pxs) > 1:
            t = "T3_MULTI_LEVEL_SAME_OUTCOME"
        elif len(outs) == 2 and len(pxs) > 2:
            t = "T4_MULTI_LEVEL_BOTH_OUTCOMES"
        elif len(outs) == 2:
            t = "T5_BOTH_OUTCOMES_OTHER"
        else:
            t = "T6_SAME_OUTCOME_SAME_PRICE_MULTI_FILL"
        tmpl[t] += 1
        vol[t] += q
        fills[t] += len(g)
        rows.append({"market_slug": slug, "tx": tx, "template": t,
                     "n_fills": len(g), "shares": round(q, 4),
                     "outcomes": sorted(outs), "n_prices": len(pxs),
                     "roles": sorted(x for x in roles if x)})
    tf = sum(fills.values())
    tv = sum(vol.values())
    return {
        "atomicity_source": "TRANSACTION HASH, never a shared second",
        "n_transactions": len(bytx), "n_fills": tf,
        "COLLAPSE_RATIO_fills_per_transaction": round(tf / len(bytx), 3),
        "apparent_actions_collapsing": round(1 - len(bytx) / tf, 4),
        "by_template": {
            t: {"transactions": tmpl[t], "fills": fills[t],
                "fill_share": round(fills[t] / tf, 4),
                "shares": round(vol[t], 1),
                "volume_share": round(vol[t] / tv, 4)}
            for t in sorted(tmpl, key=lambda x: -vol[x])},
        "role_mix_by_template": {
            t: dict(Counter("+".join(r["roles"]) or "unknown" for r in rows
                             if r["template"] == t).most_common(3))
            for t in sorted(tmpl)},
    }


# ------------------------------------------------------------------ S6
def consumption(clips: List[dict], by_slug_clips: Dict[str, List[dict]]
                ) -> Dict[str, Any]:
    """Fraction of 80 consumed before each kind of next event."""
    out: Dict[str, List[float]] = defaultdict(list)
    seqs: Dict[str, List[float]] = defaultdict(list)
    for slug, cs in by_slug_clips.items():
        cs = sorted(cs, key=lambda c: c["t_first"])
        for i, c in enumerate(cs):
            frac = min(1.0, c["clip_total_shares"] / CLIP80)
            nxt = cs[i + 1] if i + 1 < len(cs) else None
            if nxt is None:
                k = "then_APPARENT_WITHDRAWAL"
            elif nxt["outcome"] != c["outcome"]:
                k = "then_OPPOSITE_SIDE"
            elif abs(nxt["price"] - c["price"]) < 1e-9:
                k = "then_SAME_PRICE_REPLENISH"
            else:
                k = "then_NEW_LEVEL"
            out[k].append(frac)
            seqs[k].append(c["clip_total_shares"])
    res = {k: {"n": len(v), "fraction_of_80_consumed": qs(v),
               "shares": qs(seqs[k])} for k, v in sorted(out.items())}
    allf = [x for v in out.values() for x in v]
    # do the named example sequences look like exhaustion?
    multi = [c for c in clips if c["n_fills"] > 1]
    exhaust = [c for c in multi if abs(c["clip_total_shares"] - CLIP80) < TOL]
    return {
        "by_next_event": res,
        "all_clips_fraction_of_80": qs(allf),
        "share_of_clips_reaching_80": round(
            sum(1 for x in allf if x >= 1.0 - TOL / CLIP80) / len(allf), 4)
        if allf else None,
        "multi_fill_clips": len(multi),
        "multi_fill_clips_summing_to_80": len(exhaust),
        "share_of_multi_fill_clips_that_exhaust_80": round(
            len(exhaust) / len(multi), 4) if multi else None,
        "named_examples_are_exhaustion_not_sizing": (
            "6+74, 50+30 and 78.54+1.46 all total exactly 80.00, and %d of "
            "%d multi-fill clips do the same. A sequence that sums to the "
            "lattice value is consumption of one clip, not two independent "
            "sizing decisions." % (len(exhaust), len(multi))),
    }


# ------------------------------------------------------------------ S5
def geometry(by_slug_clips: Dict[str, List[dict]], books, tok_by_w
             ) -> Dict[str, Any]:
    """Ladder geometry in RELATIVE coordinates. Width stays a lower bound."""
    offs, gaps, from_half, pair_sums, per_window_levels = [], [], [], [], []
    spacing_by_prob: Dict[str, List[int]] = defaultdict(list)
    spacing_by_imb: Dict[str, List[int]] = defaultdict(list)
    for slug, cs in by_slug_clips.items():
        cs = sorted(cs, key=lambda c: c["t_first"])
        inv = {"up": 0.0, "down": 0.0}
        for c in cs:
            bk = books.get(tok_by_w[slug][c["outcome"]])
            s = bk.at(float(c["t_first"] - 1), max_age_s=MAX_CARRY_S) if bk \
                else None
            bb = (s or {}).get("best_bid")
            if bb is not None:
                offs.append(round((float(bb) - c["price"]) / TICK))
            from_half.append(round(abs(c["price"] - 0.5), 4))
            inv[c["outcome"]] += c["clip_total_shares"]
        # distance between consecutive distinct levels on one side
        for side in ("up", "down"):
            ps = [c["price"] for c in cs if c["outcome"] == side]
            for a, b in zip(ps, ps[1:]):
                if abs(a - b) > 1e-9:
                    d = abs(round((b - a) / TICK))
                    gaps.append(d)
                    band = ("p<0.25" if a < .25 else "p<0.50" if a < .5
                            else "p<0.75" if a < .75 else "p>=0.75")
                    spacing_by_prob[band].append(d)
                    im = inv["up"] - inv["down"]
                    ib = ("balanced" if abs(im) < 50 else
                          "heavy_up" if im > 0 else "heavy_down")
                    spacing_by_imb[ib].append(d)
        # simultaneous plausible levels: distinct prices per side within 2s
        bysec: Dict[Tuple[int, str], set] = defaultdict(set)
        for c in cs:
            bysec[(c["t_first"] // 2, c["outcome"])].add(round(c["price"], 4))
        per_window_levels.append(max((len(v) for v in bysec.values()),
                                     default=0))
        # UP+DOWN acquisition price relationship
        up = [c["price"] for c in cs if c["outcome"] == "up"]
        dn = [c["price"] for c in cs if c["outcome"] == "down"]
        if up and dn:
            pair_sums.append(round(sum(up) / len(up) + sum(dn) / len(dn), 4))
    return {
        "ticks_below_touch_at_clip_start": qs(offs),
        "tick_gap_between_consecutive_levels_same_side": qs(gaps),
        "distance_from_0_50": qs(from_half),
        "mean_UP_price_plus_mean_DOWN_price_per_window": qs(pair_sums),
        "max_simultaneous_distinct_levels_per_window": qs(per_window_levels),
        "spacing_by_probability_band": {
            k: qs(v) for k, v in sorted(spacing_by_prob.items())},
        "spacing_by_inventory_imbalance": {
            k: qs(v) for k, v in sorted(spacing_by_imb.items())},
        "WIDTH_IS_A_LOWER_BOUND": (
            "unfilled and cancelled orders are unobservable, so every level "
            "count here is a floor on the ladder, never its width"),
    }


# ------------------------------------------------------------------ S3
def withdrawal(by_slug_clips: Dict[str, List[dict]], books, tok_by_w,
               window_s: int) -> Dict[str, Any]:
    """Continuation vs apparent withdrawal on the preregistered variables."""
    cont, wd = [], []
    for slug, cs in by_slug_clips.items():
        cs = sorted(cs, key=lambda c: c["t_first"])
        start = int(slug.rsplit("-", 1)[1])
        inv = {"up": 0.0, "down": 0.0}
        last_on: Dict[str, Optional[dict]] = {"up": None, "down": None}
        for i, c in enumerate(cs):
            inv[c["outcome"]] += c["clip_total_shares"]
            later_same = any(x["outcome"] == c["outcome"]
                             for x in cs[i + 1:])
            bk = books.get(tok_by_w[slug][c["outcome"]])
            s = bk.at(float(c["t_last"]), max_age_s=MAX_CARRY_S) if bk \
                else None
            bb = (s or {}).get("best_bid")
            ba = (s or {}).get("best_ask")
            prev = last_on[c["outcome"]]
            rec = {
                "market_slug": slug,
                "price": c["price"], "tte_s": start + window_s - c["t_last"],
                "secs_after_open": c["t_last"] - start,
                "inv_same_side": round(inv[c["outcome"]], 3),
                "residual": round(abs(inv["up"] - inv["down"]), 3),
                "imbalance_signed": round(inv["up"] - inv["down"], 3),
                "matched": round(min(inv["up"], inv["down"]), 3),
                "spread": (round(float(ba) - float(bb), 4)
                           if None not in (bb, ba) else None),
                "depth_at_touch": (size_at_price(s, float(bb))
                                   if s and bb is not None else None),
                "depth_at_my_price": size_at_price(s, c["price"]),
                "frac_of_80_consumed": round(
                    min(1.0, c["clip_total_shares"] / CLIP80), 4),
                "elapsed_since_prev_same_side_s": (
                    None if prev is None else c["t_first"] - prev["t_last"]),
                "clip_duration_s": c["duration_s"],
            }
            (cont if later_same else wd).append(rec)
            last_on[c["outcome"]] = c
    keys = ("tte_s", "price", "inv_same_side", "residual", "matched",
            "spread", "depth_at_touch", "depth_at_my_price",
            "frac_of_80_consumed", "elapsed_since_prev_same_side_s",
            "secs_after_open", "clip_duration_s")
    res = {}
    for k in keys:
        res[k] = {"continuation": qs([r[k] for r in cont]),
                  "apparent_withdrawal": qs([r[k] for r in wd]),
                  "cliffs_delta_withdrawal_minus_continuation":
                      cliffs_delta([r[k] for r in wd],
                                   [r[k] for r in cont])}
    res = dict(sorted(res.items(),
                      key=lambda kv: -abs(
                          kv[1]["cliffs_delta_withdrawal_minus_continuation"]
                          .get("delta") or 0)))
    return {
        "n_continuation": len(cont), "n_apparent_withdrawal": len(wd),
        "variables_ranked_by_effect_size": res,
        "WHAT_THE_ARCHIVE_CANNOT_IDENTIFY": (
            "absence of a later fill on a side is NOT proof no L5 order "
            "existed there. The archive shows executions and anonymous "
            "depth; an order that rested and was never hit, and any cancel, "
            "are invisible. 'Apparent withdrawal' means only that no further "
            "fill on that side was observed in this window."),
    }


# ------------------------------------------------------------------ S9
def stability(clips15, clips5, ev15, ev5) -> Dict[str, Any]:
    def lat(cs):
        tot = [c["clip_total_shares"] for c in cs]
        n = len(tot)
        at = sum(1 for x in tot if abs(x - CLIP80) < TOL)
        bg = sum(1 for x in tot if 1.5 < abs(x - CLIP80) <= 3.75)
        exp = bg / 3.0 if bg else 0.0
        return {"n_clips": n, "at_80": at,
                "share_at_80": round(at / n, 4) if n else None,
                "local_background_expectation": round(exp, 2),
                "excess_ratio": (round(at / exp, 2) if exp else None),
                "p90_of_totals": qs(tot).get("p90"),
                "max": qs(tot).get("max")}

    def by_day(cs):
        d: Dict[str, List[dict]] = defaultdict(list)
        for c in cs:
            import datetime as dt
            day = dt.datetime.utcfromtimestamp(
                int(c["market_slug"].rsplit("-", 1)[1])).strftime("%m-%d")
            d[day].append(c)
        return {k: lat(v) for k, v in sorted(d.items())}

    def fill_only(ev):
        q = [r["quantity"] for r in ev]
        return {"LABEL": "FILL-ONLY SIZING ARITHMETIC, not latent clips",
                "n_fills": len(q),
                "max_single_fill": round(max(q), 4),
                "fills_at_or_above_80": sum(1 for x in q if x >= 80 - TOL),
                "fills_above_100": sum(1 for x in q if x > 100 + 1e-9),
                "quantiles": qs(q)}
    return {
        "BTC15m": {"lattice": lat(clips15), "by_session_day": by_day(clips15),
                   "fill_only_arithmetic": fill_only(ev15)},
        "BTC5m": {"lattice": lat(clips5), "by_session_day": by_day(clips5),
                  "fill_only_arithmetic": fill_only(ev5)},
        "scope_limit": (
            "Latent-clip reconstruction needs the book, so it exists ONLY "
            "inside the 2026-08-21..24 Zenodo overlap. Outside it, L5 history "
            "is fills without a contemporaneous ladder, which supports "
            "labelled sizing arithmetic and NOT latent-clip claims."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events15", required=True)
    ap.add_argument("--events5", required=True)
    ap.add_argument("--parquet15", required=True)
    ap.add_argument("--parquet5", required=True)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    out: Dict[str, Any] = {"protocol": "PREREGISTRATION_OVERNIGHT.md"}
    store = {}
    for tag, evp, pqp, ws in (("15m", a.events15, a.parquet15, 900),
                              ("5m", a.events5, a.parquet5, 300)):
        ev = jl(evp)
        byw: Dict[str, List[dict]] = defaultdict(list)
        for r in ev:
            byw[r["market_slug"]].append(r)
        for g in byw.values():
            g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
        books, wins = load(pqp, contract_length=tag, slugs=set(byw))
        tok_by_w, by_clips, allclips = {}, {}, []
        for slug, seq in byw.items():
            toks = wins[slug]["tokens"]
            tok_by_w[slug] = {
                "up": next(t for t, o in toks.items()
                           if str(o).lower() == "up"),
                "down": next(t for t, o in toks.items()
                             if str(o).lower() == "down")}
            pairs, member = find_pairs(seq)
            passive = [r for r in seq if _id(r) not in member]
            cs = build_clips(passive, books, gap_s=GAP_S, slug=slug,
                             clen=tag, start=int(slug.rsplit("-", 1)[1]))
            by_clips[slug] = cs
            allclips.extend(cs)
        store[tag] = (ev, books, tok_by_w, by_clips, allclips, ws)
        out["S7_transaction_templates_%s" % tag] = templates(ev)
        out["S6_clip_consumption_%s" % tag] = consumption(allclips, by_clips)
        out["S5_ladder_geometry_%s" % tag] = geometry(by_clips, books,
                                                      tok_by_w)
        out["S3_withdrawal_%s" % tag] = withdrawal(by_clips, books, tok_by_w,
                                                   ws)
    out["S9_80_share_stability"] = stability(
        store["15m"][4], store["5m"][4], store["15m"][0], store["5m"][0])
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "STRUCTURE.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written STRUCTURE.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
