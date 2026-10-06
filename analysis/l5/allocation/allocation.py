"""L5 BTC15m allocation / sizing policy study.

Protocol frozen in PREREGISTRATION_ALLOCATION.md at commit 5cab0496, before
any result here was computed. Tests ALLOCATION STRUCTURE, not fill ordering.

No P&L is computed or optimized. No S3 parameter is fitted from any of this.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book
from analysis.l5.microstructure.clips import build_clips
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id
from analysis.l5.microstructure.stats import cliffs_delta, qs, spearman

SNAPSHOTS = (15, 30, 45, 60, 90, 120, 180, 300, 600)
WINDOW_S = 900
GAP_S = 10
CLIP80 = 80.0
BINS = tuple(round(0.1 * i, 1) for i in range(10))   # preregistered 0.10-wide
MIN_BIN_N = 30
PREREG = "PREREGISTRATION_ALLOCATION.md @ 5cab0496"


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def mid(bk: Optional[Book], t: float) -> Optional[float]:
    s = bk.at(float(t), max_age_s=MAX_CARRY_S) if bk else None
    if not s:
        return None
    bb, ba = s.get("best_bid"), s.get("best_ask")
    if bb is None or ba is None:
        return None
    return (float(bb) + float(ba)) / 2.0


# ---------------------------------------------------------------- laws
def split_targets(p: float) -> Dict[str, float]:
    """The three DISTINCT share-space split laws. Degeneracies were
    established in the preregistration before any data was read:
    1/p and equal-dollars both reduce exactly to 1-p; equal-shares,
    equal-payout and p(1-p) all reduce to 0.5."""
    return {"L_A_prop_p": p, "L_B_prop_1_minus_p": 1.0 - p,
            "L_C_equal_shares": 0.5}


LAW_ALIASES = {
    "L_A_prop_p": ("allocation ∝ p",),
    "L_B_prop_1_minus_p": ("allocation ∝ 1-p", "allocation ∝ 1/p",
                           "equal dollars"),
    "L_C_equal_shares": ("equal shares", "equal potential payout",
                         "allocation ∝ p(1-p) [as a split law]"),
}


def bin_of(p: Optional[float]) -> Optional[str]:
    if p is None or not (0.0 <= p <= 1.0):
        return None
    i = min(9, int(p * 10))
    return "[%.1f,%.1f)" % (BINS[i], BINS[i] + 0.1)


# ---------------------------------------------------------------- state
def window_state(slug: str, seq: List[dict], books, tokens
                 ) -> Tuple[List[dict], List[dict], Dict[str, Any]]:
    """Per-second cumulative state plus the inferred clip sequence."""
    start = int(slug.rsplit("-", 1)[1])
    tok = {str(o).lower(): t for t, o in tokens.items()}
    pairs, member = find_pairs(seq)
    passive = [r for r in seq if _id(r) not in member]
    clips = sorted(build_clips(passive, books, gap_s=GAP_S, slug=slug,
                               clen="15m", start=start),
                   key=lambda c: c["t_first"])

    # every acquisition event: inferred clips plus atomic pair legs, kept
    # distinguishable. Observed fill quantity is NEVER used as a sizing
    # decision; clip totals are the inferred exposure unit.
    events: List[dict] = [
        {"t": c["t_first"], "outcome": c["outcome"],
         "shares": c["clip_total_shares"], "price": c["price"],
         "kind": "INFERRED_CLIP",
         "frac80": min(1.0, c["clip_total_shares"] / CLIP80)}
        for c in clips]
    for p in pairs:
        for leg in ("up", "down"):
            r = p[leg]
            events.append({"t": r["t"], "outcome": leg,
                           "shares": r["quantity"], "price": r["price"],
                           "kind": "ATOMIC_PAIR_LEG", "frac80": None})
    events.sort(key=lambda e: e["t"])

    sh = {"up": 0.0, "down": 0.0}
    dl = {"up": 0.0, "down": 0.0}
    nclip = {"up": 0, "down": 0}
    for e in events:
        e["sh_before"] = dict(sh)
        sh[e["outcome"]] += e["shares"]
        dl[e["outcome"]] += e["shares"] * e["price"]
        if e["kind"] == "INFERRED_CLIP":
            nclip[e["outcome"]] += 1
        e["sh_after"] = dict(sh)
        e["dl_after"] = dict(dl)
        e["nclip_after"] = dict(nclip)

    final = {"shares": dict(sh), "dollars": dict(dl),
             "matched": min(sh["up"], sh["down"]),
             "capital": dl["up"] + dl["down"]}

    snaps = []
    prev_secs = 0
    last_t = max((e["t"] for e in events), default=start)
    for label, secs in ([(str(s), s) for s in SNAPSHOTS]
                        + [("shutdown", last_t - start)]):
        t = start + secs
        done = [e for e in events if e["t"] <= t]
        s_up = sum(e["shares"] for e in done if e["outcome"] == "up")
        s_dn = sum(e["shares"] for e in done if e["outcome"] == "down")
        d_up = sum(e["shares"] * e["price"] for e in done
                   if e["outcome"] == "up")
        d_dn = sum(e["shares"] * e["price"] for e in done
                   if e["outcome"] == "down")
        c_up = sum(1 for e in done
                   if e["outcome"] == "up" and e["kind"] == "INFERRED_CLIP")
        c_dn = sum(1 for e in done
                   if e["outcome"] == "down" and e["kind"] == "INFERRED_CLIP")
        # INCREMENTAL quantities. Cumulative counts rise mechanically with
        # elapsed time, so a cumulative-count-vs-TTE correlation is a
        # tautology and is not a scaling result. Rates are the honest test.
        inc = [e for e in events if prev_secs < e["t"] - start <= secs]
        interval_s = max(1, secs - prev_secs)
        inc_clips = sum(1 for e in inc if e["kind"] == "INFERRED_CLIP")
        inc_shares = sum(e["shares"] for e in inc)
        inc_dollars = sum(e["shares"] * e["price"] for e in inc)
        prev_secs = secs
        p_up = mid(books.get(tok["up"]), t)
        p_dn = mid(books.get(tok["down"]), t)
        tot_s, tot_d = s_up + s_dn, d_up + d_dn
        matched = min(s_up, s_dn)
        snaps.append({
            "market_slug": slug, "snapshot": label, "secs_after_open": secs,
            "tte_s": WINDOW_S - secs,
            "p_up_mid": p_up, "p_down_mid": p_dn,
            "prob_bin": bin_of(p_up),
            "shares_up": round(s_up, 4), "shares_down": round(s_dn, 4),
            "dollars_up": round(d_up, 4), "dollars_down": round(d_dn, 4),
            "clips_up": c_up, "clips_down": c_dn,
            "clips_total": c_up + c_dn,
            "interval_s": interval_s,
            "clips_in_interval": inc_clips,
            "shares_in_interval": round(inc_shares, 4),
            "dollars_in_interval": round(inc_dollars, 4),
            "clip_rate_per_min": round(60.0 * inc_clips / interval_s, 4),
            "share_rate_per_min": round(60.0 * inc_shares / interval_s, 4),
            "matched": round(matched, 4),
            "residual": round(abs(s_up - s_dn), 4),
            "imbalance_signed": round(s_up - s_dn, 4),
            "vwap_up": round(d_up / s_up, 6) if s_up else None,
            "vwap_down": round(d_dn / s_dn, 6) if s_dn else None,
            "effective_matched_pair_cost": (
                round(d_up / s_up + d_dn / s_dn, 6)
                if s_up and s_dn else None),
            # the five normalizations
            "w_up_shares": round(s_up / tot_s, 6) if tot_s else None,
            "w_up_dollars": round(d_up / tot_d, 6) if tot_d else None,
            # potential payout is $1/share, so payout allocation == shares
            "w_up_payout": round(s_up / tot_s, 6) if tot_s else None,
            "w_up_clips": (round(c_up / (c_up + c_dn), 6)
                           if (c_up + c_dn) else None),
            "matched_fraction_of_shares": (round(2 * matched / tot_s, 6)
                                           if tot_s else None),
            "pct_eventual_capital_deployed": (
                round(tot_d / final["capital"], 6)
                if final["capital"] else None),
            "pct_eventual_matched_accumulated": (
                round(matched / final["matched"], 6)
                if final["matched"] else None),
        })
    return snaps, events, final


# ---------------------------------------------------------------- A1
def a1_split_law(snaps: List[dict]) -> Dict[str, Any]:
    """Which split law fits, window by window and under LOWO."""
    def err(rows, law, field="w_up_shares"):
        e = []
        for r in rows:
            if r.get(field) is None or r.get("p_up_mid") is None:
                continue
            e.append(abs(r[field] - split_targets(r["p_up_mid"])[law]))
        return e

    out: Dict[str, Any] = {"preregistered_laws": LAW_ALIASES}
    for field in ("w_up_shares", "w_up_dollars", "w_up_clips"):
        at60 = [r for r in snaps if r["snapshot"] == "60"]
        allrows = [r for r in snaps if r["snapshot"] != "shutdown"]
        per_law = {}
        for law in split_targets(0.5):
            per_law[law] = {
                "median_L1_error_T60": (qs(err(at60, law, field)) or {}).get("p50"),
                "median_L1_error_all_snapshots": (
                    qs(err(allrows, law, field)) or {}).get("p50"),
            }
        # window-level winner
        wins: Counter = Counter()
        bywin: Dict[str, List[dict]] = defaultdict(list)
        for r in allrows:
            bywin[r["market_slug"]].append(r)
        for slug, rows in bywin.items():
            scores = {law: (qs(err(rows, law, field)) or {}).get("p50")
                      for law in split_targets(0.5)}
            scores = {k: v for k, v in scores.items() if v is not None}
            if scores:
                wins[min(scores, key=scores.get)] += 1
        # LOWO: fit on 11 windows, check the winner on the held-out one
        lowo: Counter = Counter()
        slugs = sorted(bywin)
        for held in slugs:
            tr = [r for s in slugs if s != held for r in bywin[s]]
            sc = {law: (qs(err(tr, law, field)) or {}).get("p50")
                  for law in split_targets(0.5)}
            sc = {k: v for k, v in sc.items() if v is not None}
            if not sc:
                continue
            best = min(sc, key=sc.get)
            te = {law: (qs(err(bywin[held], law, field)) or {}).get("p50")
                  for law in split_targets(0.5)}
            te = {k: v for k, v in te.items() if v is not None}
            if te and min(te, key=te.get) == best:
                lowo[best] += 1
        out[field] = {
            "per_law": per_law,
            "window_level_wins": dict(wins),
            "n_windows": len(bywin),
            "LOWO_held_out_agreements": dict(lowo),
        }
    return out


# ---------------------------------------------------------------- A2
def a2_error_correction(events_by_window: Dict[str, List[dict]],
                        snaps: List[dict]) -> Dict[str, Any]:
    """Does the next inferred clip reduce distance to a target allocation
    more often than the baselines -- B-light above all?"""
    res: Dict[str, Any] = {}
    for law in list(split_targets(0.5)) + ["D1_dynamic_balance"]:
        per_window = {}
        for slug, evs in events_by_window.items():
            hit = tot = 0
            b_light = b_persist = b_heavy = 0
            prev_side = None
            for e in evs:
                if e["kind"] != "INFERRED_CLIP":
                    continue
                b = e["sh_before"]
                t_s = b["up"] + b["down"]
                if t_s <= 0:
                    prev_side = e["outcome"]
                    continue
                p = e.get("p_at_event")
                if law == "D1_dynamic_balance":
                    tgt = 0.5
                elif p is None:
                    prev_side = e["outcome"]
                    continue
                else:
                    tgt = split_targets(p)[law]
                w0 = b["up"] / t_s
                # which side moves w toward the target?
                a = dict(b); a["up"] += e["shares"]
                w_up = a["up"] / (t_s + e["shares"])
                a2 = dict(b); a2["down"] += e["shares"]
                w_dn = a2["up"] / (t_s + e["shares"])
                best = "up" if abs(w_up - tgt) < abs(w_dn - tgt) else "down"
                light = "up" if b["up"] < b["down"] else "down"
                heavy = "down" if light == "up" else "up"
                tot += 1
                hit += (e["outcome"] == best)
                b_light += (light == best)
                b_heavy += (heavy == best)
                if prev_side is not None:
                    b_persist += (prev_side == best)
                prev_side = e["outcome"]
            if tot >= 10:
                per_window[slug] = {
                    "n": tot,
                    "policy_hit_rate": round(hit / tot, 4),
                    "B_light": round(b_light / tot, 4),
                    "B_heavy": round(b_heavy / tot, 4),
                    "B_persist": round(b_persist / tot, 4),
                    "beats_B_light_by": round((hit - b_light) / tot, 4),
                }
        margins = [v["beats_B_light_by"] for v in per_window.values()]
        res[law] = {
            "per_window": per_window,
            "n_windows": len(per_window),
            "windows_beating_B_light_by_10pp": sum(
                1 for m in margins if m >= 0.10),
            "median_margin_over_B_light": (qs(margins) or {}).get("p50"),
        }
    return res


# ---------------------------------------------------------------- A3
def a3_convergence(snaps: List[dict]) -> Dict[str, Any]:
    out = {}
    for label in ("30", "60"):
        rows = [r for r in snaps if r["snapshot"] == label]
        for field in ("w_up_shares", "w_up_dollars", "w_up_clips",
                      "matched_fraction_of_shares"):
            v = [r[field] for r in rows if r.get(field) is not None]
            q = qs(v)
            out["T%s_%s" % (label, field)] = {
                "n_windows": len(v),
                "IQR": (round(q["p75"] - q["p25"], 4) if q else None),
                "median": (q or {}).get("p50"),
                "min": (q or {}).get("min"), "max": (q or {}).get("max"),
            }
    return out


# ---------------------------------------------------------------- A4 + bins
def a4_scaling(snaps: List[dict], events_by_window) -> Dict[str, Any]:
    """Does the inferred 80-share clip UNIT scale with p, inventory error
    and TTE?

    Two corrections over a naive version of this test, both material:

    (1) CUMULATIVE clip count rises mechanically as the window elapses, so
        correlating it with TTE is a TAUTOLOGY, not a scaling result. The
        tested quantity is therefore the clip RATE in each interval.
    (2) A per-window test over 9 snapshots cannot reach the n>=10 that the
        rank test requires, so a snapshot-level per-window loop returns no
        rho at all and must NOT be reported as 0/12 windows failing. The
        per-window test is run at CLIP resolution, where each window has
        100-300 observations.
    """
    rows = [r for r in snaps if r["snapshot"] != "shutdown"]

    # ---- aggregate, on rates (snapshot resolution) ----
    feats = {
        "prob_distance_from_half": lambda r: (
            abs(r["p_up_mid"] - 0.5) if r.get("p_up_mid") is not None
            else None),
        "p_up_mid": lambda r: r.get("p_up_mid"),
        "inventory_error_residual": lambda r: r.get("residual"),
        "tte_s": lambda r: r.get("tte_s"),
    }
    aggregate = {}
    for name, fn in feats.items():
        pairs = [(fn(r), r["clip_rate_per_min"]) for r in rows
                 if fn(r) is not None]
        aggregate[name] = spearman([a for a, _ in pairs],
                                   [b for _, b in pairs])

    # ---- per window, at CLIP resolution ----
    # one observation per inferred clip: its own size in units of 80
    # against the state at placement.
    per_window: Dict[str, Dict[str, Any]] = {}
    for slug, evs in events_by_window.items():
        cl = [e for e in evs if e["kind"] == "INFERRED_CLIP"
              and e.get("frac80") is not None]
        if len(cl) < 10:
            continue
        start = int(slug.rsplit("-", 1)[1])
        obs = []
        for e in cl:
            b = e["sh_before"]
            obs.append({
                "frac80": e["frac80"],
                "p": e.get("p_at_event"),
                "dist_half": (abs(e["p_at_event"] - 0.5)
                              if e.get("p_at_event") is not None else None),
                "residual": abs(b["up"] - b["down"]),
                "tte_s": WINDOW_S - (e["t"] - start),
            })
        w = {}
        for k in ("p", "dist_half", "residual", "tte_s"):
            pp = [(o[k], o["frac80"]) for o in obs if o[k] is not None]
            w[k] = spearman([a for a, _ in pp], [b for _, b in pp])
        per_window[slug] = w

    summary = {}
    for k in ("p", "dist_half", "residual", "tte_s"):
        rhos = [v[k].get("rho") for v in per_window.values()
                if v[k].get("rho") is not None]
        pos = sum(1 for r in rhos if r > 0)
        summary[k] = {
            "n_windows_evaluable": len(rhos),
            "windows_with_abs_rho_ge_0.30": sum(
                1 for r in rhos if abs(r) >= 0.30),
            "windows_positive": pos,
            "windows_negative": len(rhos) - pos,
            "median_rho": (qs(rhos) or {}).get("p50"),
            "MEETS_A4_BAR": sum(1 for r in rhos if abs(r) >= 0.30) >= 9,
        }

    return {
        "TAUTOLOGY_AVOIDED": (
            "cumulative clip count necessarily rises with elapsed time, so "
            "a cumulative-count-vs-TTE rho (which measures ~-1 by "
            "construction) is NOT reported as a scaling finding. Rates and "
            "per-clip sizes are used instead."),
        "VACUITY_AVOIDED": (
            "a per-window test at snapshot resolution has only 9 points and "
            "returns no rho; that is NOT evidence against A4. The "
            "per-window test runs at clip resolution instead."),
        "aggregate_clip_rate_vs_state": aggregate,
        "per_window_clip_size_vs_state": per_window,
        "per_window_summary": summary,
    }


def bin_table(snaps: List[dict], events_by_window) -> Dict[str, Any]:
    rows = [r for r in snaps if r["snapshot"] != "shutdown"
            and r.get("prob_bin")]
    byb: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        byb[r["prob_bin"]].append(r)

    # replenishment / withdrawal probability per bin, from the clip stream
    rep: Dict[str, List[int]] = defaultdict(list)
    wd: Dict[str, List[int]] = defaultdict(list)
    for slug, evs in events_by_window.items():
        cl = [e for e in evs if e["kind"] == "INFERRED_CLIP"]
        for i, e in enumerate(cl):
            b = bin_of(e.get("p_at_event"))
            if b is None:
                continue
            nxt = cl[i + 1] if i + 1 < len(cl) else None
            rep[b].append(1 if (nxt and nxt["outcome"] == e["outcome"]
                                and abs(nxt["price"] - e["price"]) < 1e-9)
                          else 0)
            wd[b].append(0 if any(x["outcome"] == e["outcome"]
                                  for x in cl[i + 1:]) else 1)

    out = {}
    for b in sorted(byb):
        rr = byb[b]
        n = len(rr)
        out[b] = {
            "n_snapshots": n,
            "UNDERPOWERED": n < MIN_BIN_N,
            "median_shares_in_interval": (qs(
                [r["shares_in_interval"] for r in rr]) or {}).get("p50"),
            "median_dollars_in_interval": (qs(
                [r["dollars_in_interval"] for r in rr]) or {}).get("p50"),
            "median_clip_rate_per_min": (qs(
                [r["clip_rate_per_min"] for r in rr]) or {}).get("p50"),
            "median_cumulative_clip_count_CONFOUNDED_BY_TIME": (qs(
                [r["clips_total"] for r in rr]) or {}).get("p50"),
            "median_imbalance_tolerance_residual": (qs(
                [r["residual"] for r in rr]) or {}).get("p50"),
            "median_w_up_shares": (qs(
                [r["w_up_shares"] for r in rr
                 if r.get("w_up_shares") is not None]) or {}).get("p50"),
            "replenishment_probability": (
                round(sum(rep[b]) / len(rep[b]), 4) if rep.get(b) else None),
            "withdrawal_probability": (
                round(sum(wd[b]) / len(wd[b]), 4) if wd.get(b) else None),
            "n_clips_for_probabilities": len(rep.get(b, [])),
        }
    return out


def a3b_trajectory(snaps: List[dict]) -> Dict[str, Any]:
    """Dispersion across windows at EVERY preregistered snapshot.

    DESCRIPTIVE, and labelled as such. The A3 bar was preregistered at T+30
    and T+60 only; A3 therefore stands or falls on those two. This panel
    adds no new snapshot and no new normalization -- every snapshot and both
    normalizations below are in the frozen protocol -- but it is NOT a
    preregistered test and is not counted as one.
    """
    out = {}
    labels = [str(x) for x in SNAPSHOTS] + ["shutdown"]
    for lab in labels:
        rows = [r for r in snaps if r["snapshot"] == lab]
        panel = {}
        for field in ("matched_fraction_of_shares", "w_up_shares",
                      "effective_matched_pair_cost"):
            v = [r[field] for r in rows if r.get(field) is not None]
            q = qs(v)
            if not q:
                continue
            panel[field] = {
                "n_windows": len(v), "median": q["p50"],
                "IQR": round(q["p75"] - q["p25"], 4),
                "min": q["min"], "max": q["max"],
                "equal_shares_L1_error_median": (
                    round(abs(q["p50"] - 0.5), 4)
                    if field == "w_up_shares" else None),
            }
        out["T+" + lab if lab != "shutdown" else "shutdown"] = panel
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events15", required=True)
    ap.add_argument("--parquet15", required=True)
    ap.add_argument("--outdir", default="analysis/l5/allocation")
    a = ap.parse_args(argv)

    ev = jl(a.events15)
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    for g in byw.values():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
    books, wins = load(a.parquet15, contract_length="15m", slugs=set(byw))

    all_snaps: List[dict] = []
    events_by_window: Dict[str, List[dict]] = {}
    finals = {}
    for slug in sorted(byw):
        snaps, events, final = window_state(slug, byw[slug], books,
                                            wins[slug]["tokens"])
        tok = {str(o).lower(): t for t, o in wins[slug]["tokens"].items()}
        for e in events:
            e["p_at_event"] = mid(books.get(tok["up"]), e["t"] - 1)
        all_snaps.extend(snaps)
        events_by_window[slug] = events
        finals[slug] = final

    out = {
        "protocol": PREREG,
        "DEGENERACY_ESTABLISHED_BEFORE_RESULTS": (
            "7 of the 9 named candidates collapse to 3 distinct share-space "
            "split laws: 1/p and equal-dollars both reduce exactly to 1-p; "
            "equal-shares, equal-payout and p(1-p) all reduce to 0.5. A win "
            "by one is NOT three independent confirmations."),
        "n_windows": len(byw),
        "snapshots": list(SNAPSHOTS) + ["shutdown"],
        "A1_split_law": a1_split_law(all_snaps),
        "A2_error_correction": a2_error_correction(events_by_window,
                                                   all_snaps),
        "A3_convergence": a3_convergence(all_snaps),
        "A3b_convergence_trajectory_DESCRIPTIVE": a3b_trajectory(all_snaps),
        "A4_clip_scaling": a4_scaling(all_snaps, events_by_window),
        "probability_bin_table": bin_table(all_snaps, events_by_window),
        "SNAPSHOTS_RAW": all_snaps,
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "ALLOCATION.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written ALLOCATION.json (%d snapshots)" % len(all_snaps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
