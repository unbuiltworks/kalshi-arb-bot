"""Can L5's sequence be explained as a state machine on 80-share latent clips
plus atomic $1 complete-set injections?

THE REPRESENTATION. Every L5 fill is assigned to exactly one of:

  INJECTION   a leg of a transaction-level equal-quantity complementary-price
              pair -- L5 taker on both legs, price sum exactly 1.0000
  CLIP        otherwise: a maximal run of consecutive passive fills on one
              (window, outcome, price), broken by a price change, a time gap,
              or OBSERVABLE replenishment at that price

Then every consecutive pair of clips on a side is labelled with one of the
five transitions the brief names, and the leftover is counted rather than
hidden.

COVERAGE IS REPORTED TWO WAYS, because they answer different questions.
ASSIGNMENT coverage (what fraction of fills and volume the representation
accounts for at all) is near-total by construction and is therefore the WEAK
measure -- it is reported so it cannot be mistaken for the strong one.
PREDICTIVE coverage is the real test: given the current state, how often is
the next transition the modal one? A state machine that explains the sequence
must beat the base rate; one that merely re-describes it will not.

NOTHING IS OPTIMIZED. No parameter is fitted, no P&L is computed, and the
80-share lattice is taken as frozen evidence from
l5-microstructure-frozen-2026-10-06 rather than re-estimated here.
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

CLIP = 80.0
TOL = 0.75
GAP_S = 10


def transitions(units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Label each step between consecutive units in one window.

    The five rules are applied in the brief's own order, and `INJECTION` wins
    whenever the unit is a par pair because that is a different mechanism, not
    a ladder move.
    """
    out = []
    last_on: Dict[str, Dict[str, Any]] = {}
    for i, u in enumerate(units):
        prev = units[i - 1] if i else None
        if u["kind"] == "INJECTION":
            lab = "MATCHED_INJECTION"
        else:
            p = last_on.get(u["outcome"])
            if p is None:
                lab = ("OPEN_SIDE" if prev is None or prev["kind"] == "INJECTION"
                       or prev.get("outcome") != u["outcome"]
                       else "OPEN_SIDE")
            elif abs(p["price"] - u["price"]) < 1e-9:
                lab = "REPLENISH_SAME_PRICE"
            else:
                lab = "MOVE_LEVEL"
            if (prev is not None and prev["kind"] == "CLIP"
                    and prev["outcome"] != u["outcome"]
                    and lab in ("OPEN_SIDE", "MOVE_LEVEL",
                                "REPLENISH_SAME_PRICE")):
                lab = "TWO_SIDED_ACQUISITION_" + lab
        out.append({**u, "transition": lab,
                    "dt_from_prev_s": (None if prev is None
                                       else u["t_first"] - prev["t_first"])})
        if u["kind"] == "CLIP":
            last_on[u["outcome"]] = u
    # WITHDRAWAL: the final clip on each side, with nothing replacing it
    final: Dict[str, int] = {}
    for i, u in enumerate(out):
        if u["kind"] == "CLIP":
            final[u["outcome"]] = i
    for i in final.values():
        out[i]["terminal_on_side"] = True
        out[i]["transition_out"] = "WITHDRAWAL"
    return out


def predictive(seqs: List[List[str]]) -> Dict[str, Any]:
    """How predictable is the next transition from the current one?"""
    uni = Counter()
    cond: Dict[str, Counter] = defaultdict(Counter)
    for s in seqs:
        for a in s:
            uni[a] += 1
        for a, b in zip(s, s[1:]):
            cond[a][b] += 1
    n = sum(uni.values())
    base = max(uni.values()) / n if n else None
    hit = tot = 0
    for a, c in cond.items():
        hit += max(c.values())
        tot += sum(c.values())
    def H(c: Counter) -> float:
        t = sum(c.values())
        return -sum((v / t) * math.log2(v / t) for v in c.values() if v)
    cond_H = (sum(sum(c.values()) * H(c) for c in cond.values()) / tot
              if tot else None)
    return {
        "n_transitions": n,
        "unconditional_distribution": {k: round(v / n, 4)
                                       for k, v in uni.most_common()},
        "base_rate_modal_transition": round(base, 4) if base else None,
        "first_order_accuracy_predicting_next_from_current":
            round(hit / tot, 4) if tot else None,
        "lift_over_base_rate": (round((hit / tot) / base, 3)
                                if tot and base else None),
        "unconditional_entropy_bits": round(H(uni), 3) if n else None,
        "conditional_entropy_bits": (round(cond_H, 3) if cond_H is not None
                                     else None),
        "interpretation": ("lift near 1.0 means knowing the current "
                           "transition tells you nothing about the next, i.e. "
                           "the state machine re-describes the sequence "
                           "without explaining it"),
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
    books, _ = load(a.parquet, contract_length=a.contract_length,
                    slugs=set(byw))

    all_units: List[Dict[str, Any]] = []
    seqs: List[List[str]] = []
    n_inj_fills = n_clip_fills = 0
    inj_shares = clip_shares = 0.0
    for slug, seq in sorted(byw.items()):
        pairs, member = find_pairs(seq)
        start = int(slug.rsplit("-", 1)[1])
        passive = [r for r in seq if _id(r) not in member]
        clips = build_clips(passive, books, gap_s=GAP_S, slug=slug,
                            clen=a.contract_length, start=start)
        units: List[Dict[str, Any]] = []
        for c in clips:
            units.append({"kind": "CLIP", "market_slug": slug,
                          "outcome": c["outcome"], "price": c["price"],
                          "t_first": c["t_first"], "t_last": c["t_last"],
                          "secs_after_open_first": c["secs_after_open_first"],
                          "tte_at_first_s": c["tte_at_first_s"],
                          "total": c["clip_total_shares"],
                          "n_fills": c["n_fills"],
                          "fill_quantities": c["fill_quantities"],
                          "duration_s": c["duration_s"],
                          "dominant_mechanism": c["dominant_mechanism"],
                          "visible_size_at_price_before":
                              c["visible_size_at_price_before"],
                          "price_first_present_s":
                              c["price_first_continuously_present_s_after_open"],
                          "clips_implied": max(1, math.ceil(
                              (c["clip_total_shares"] - TOL) / CLIP)),
                          "fits_one_80_clip":
                              c["clip_total_shares"] <= CLIP + TOL,
                          "on_20_grid": (
                              abs(c["clip_total_shares"]
                                  - round(c["clip_total_shares"] / 20) * 20)
                              <= TOL
                              and c["clip_total_shares"] >= 20 - TOL)})
            n_clip_fills += c["n_fills"]
            clip_shares += c["clip_total_shares"]
        for p in pairs:
            u, d = p["up"], p["down"]
            units.append({"kind": "INJECTION", "market_slug": slug,
                          "outcome": "both", "price": None,
                          "t_first": u["t"], "t_last": u["t"],
                          "secs_after_open_first": u["secs_since_open"],
                          "tte_at_first_s": u["tte_s"],
                          "total": round(2 * u["quantity"], 6),
                          "quantity_per_leg": u["quantity"],
                          "n_fills": 2, "duration_s": 0,
                          "at_80_per_leg": abs(u["quantity"] - CLIP) < TOL})
            n_inj_fills += 2
            inj_shares += 2 * u["quantity"]
        units.sort(key=lambda x: (x["t_first"], x["kind"]))
        lab = transitions(units)
        all_units.extend(lab)
        seqs.append([x["transition"] for x in lab])

    tot_fills = len(ev)
    tot_shares = sum(r["quantity"] for r in ev)
    clips_only = [u for u in all_units if u["kind"] == "CLIP"]
    rep = {
        "contract_length": a.contract_length,
        "clip_shares_lattice": CLIP, "gap_s": GAP_S,
        "lattice_is_frozen_evidence_not_refitted": True,
        "ASSIGNMENT_COVERAGE_WEAK_MEASURE": {
            "note": ("near-total BY CONSTRUCTION -- every fill is either a "
                     "pair leg or lands in a clip. Reported so it cannot be "
                     "mistaken for explanatory power."),
            "fills_assigned": n_inj_fills + n_clip_fills,
            "fills_total": tot_fills,
            "fill_coverage": round((n_inj_fills + n_clip_fills)
                                   / tot_fills, 4),
            "volume_assigned": round(inj_shares + clip_shares, 2),
            "volume_total": round(tot_shares, 2),
            "volume_coverage": round((inj_shares + clip_shares)
                                     / tot_shares, 4),
            "injection_fill_share": round(n_inj_fills / tot_fills, 4),
            "injection_volume_share": round(inj_shares / tot_shares, 4),
            "clip_fill_share": round(n_clip_fills / tot_fills, 4),
            "clip_volume_share": round(clip_shares / tot_shares, 4),
        },
        "LATTICE_FIT": {
            "n_clips": len(clips_only),
            "share_fitting_ONE_80_clip": round(
                sum(1 for c in clips_only if c["fits_one_80_clip"])
                / len(clips_only), 4) if clips_only else None,
            "share_on_the_20_grid": round(
                sum(1 for c in clips_only if c["on_20_grid"])
                / len(clips_only), 4) if clips_only else None,
            "implied_clips_per_observed_clip": dict(
                Counter(c["clips_implied"] for c in clips_only)),
            "volume_in_clips_over_80": round(
                sum(c["total"] for c in clips_only if not c["fits_one_80_clip"]),
                2),
            "share_of_clip_volume_over_80": round(
                sum(c["total"] for c in clips_only
                    if not c["fits_one_80_clip"]) / clip_shares, 4)
            if clip_shares else None,
        },
        "TRANSITIONS": dict(Counter(u["transition"] for u in all_units)),
        "PREDICTIVE_COVERAGE_STRONG_MEASURE": predictive(seqs),
        "WITHDRAWALS": {
            "n": sum(1 for u in all_units if u.get("transition_out")
                     == "WITHDRAWAL"),
            "tte_at_final_clip": sorted(
                u["tte_at_first_s"] for u in all_units
                if u.get("transition_out") == "WITHDRAWAL"),
        },
        "no_pnl_optimized": True, "no_parameter_fitted": True,
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "CLIPSTATE_%s.json"
                           % a.contract_length), "w") as fh:
        json.dump({**rep, "units": all_units}, fh, indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
