"""L5 BEHAVIOUR CENSUS from public fill data -- direct observation only.

WHAT THIS CAN AND CANNOT SEE. The Polymarket activity feed gives FILLS, not
orders: there is no placement, cancel, requote or resting-quote record, and no
maker/taker role flag. So:

  * DIRECTLY OBSERVABLE: fill timing, side sequencing, price, size, resulting
    inventory/imbalance, matched pairs, cycling, settlement-relative timing.
  * INFERRED ONLY: persistence vs repricing, WAIT, replenishment -- these are
    order-level behaviours that fills can only hint at.
  * NOT DETERMINABLE: maker vs taker per fill (the feed has no role field;
    the whole history holds 4 MAKER_REBATE and 3 TAKER_REBATE rows).

Rows at the SAME (timestamp, slug, outcome, price) are fill fragments of ONE
order and are grouped into a single ACTION. Treating fragments as separate
decisions would inflate every count and destroy the timing statistics.
"""
from __future__ import annotations

import gzip
import json
import statistics as st
import sys
from collections import defaultdict
from decimal import Decimal as D

WINDOW_S = 900


def load_rows(paths):
    out = []
    for p in paths:
        o = json.loads(gzip.open(p, "rt").read())
        out += o["rows"]
    return out


def actions_by_market(rows, kind="btc-updown-15m"):
    """Group fill fragments into orders, then order each market's actions."""
    frag = defaultdict(list)
    for p in rows:
        if p.get("type") != "TRADE":
            continue
        s = str(p.get("slug") or "")
        if not s.startswith(kind):
            continue
        key = (s, p["timestamp"], p.get("outcome"), str(p.get("price")))
        frag[key].append(p)
    mkt = defaultdict(list)
    for (slug, ts, outcome, px), fr in frag.items():
        mkt[slug].append({
            "t": ts, "outcome": outcome, "px": D(str(px)),
            "size": sum(D(str(f.get("size") or 0)) for f in fr),
            "usdc": sum(D(str(f.get("usdcSize") or 0)) for f in fr),
            "fragments": len(fr),
            "side": fr[0].get("side"),
        })
    for slug in mkt:
        mkt[slug].sort(key=lambda a: (a["t"], str(a["outcome"])))
    return mkt


def window_start(slug):
    try:
        return int(slug.split("-")[-1])
    except ValueError:
        return None


def census(mkt):
    """Walk each market's action sequence and classify each action."""
    rec = []
    for slug, acts in mkt.items():
        ws = window_start(slug)
        qty = {"Up": D(0), "Down": D(0)}
        last_px = {}
        prev_t = None
        n_match_before = D(0)
        for i, a in enumerate(acts):
            o = a["outcome"]
            other = "Down" if o == "Up" else "Up"
            resid = qty[o] - qty[other]          # BEFORE this action
            matched_before = min(qty["Up"], qty["Down"])
            if i == 0:
                cls = "OPEN"
            elif resid > 0:
                cls = "EXTEND"                   # adding to the heavy side
            elif resid < 0:
                cls = "COMPLETE"                 # working the deficient side
            else:
                cls = "REOPEN"                   # flat/balanced, new leg
            improves = (o in last_px and a["px"] < last_px[o])
            rec.append({
                "slug": slug, "t": a["t"], "i": i,
                "class": cls, "outcome": o, "px": a["px"],
                "size": a["size"], "usdc": a["usdc"],
                "fragments": a["fragments"],
                "resid_before": resid,
                "abs_resid_before": abs(resid),
                "matched_before": matched_before,
                "gap_s": (None if prev_t is None else a["t"] - prev_t),
                "secs_into": (None if ws is None else a["t"] - ws),
                "secs_left": (None if ws is None else ws + WINDOW_S - a["t"]),
                "same_side_as_prev": (i > 0 and acts[i - 1]["outcome"] == o),
                "px_delta_same_side": (None if o not in last_px
                                       else a["px"] - last_px[o]),
                "basis_improving": improves,
                "cycled_after_match": matched_before > 0 and cls in ("REOPEN",
                                                                    "EXTEND"),
            })
            qty[o] += a["size"]
            last_px[o] = a["px"]
            prev_t = a["t"]
    return rec


def q(vals, p):
    v = sorted(vals)
    if not v:
        return None
    return v[min(len(v) - 1, int(p * len(v)))]


def summarise(rec):
    out = {}
    out["markets"] = len({r["slug"] for r in rec})
    out["actions"] = len(rec)
    out["fill_fragments"] = sum(r["fragments"] for r in rec)
    cls = defaultdict(int)
    for r in rec:
        cls[r["class"]] += 1
    out["action_classes"] = dict(cls)
    nf = [r for r in rec if r["i"] > 0]
    out["same_side_share_of_followups"] = (
        round(sum(1 for r in nf if r["same_side_as_prev"]) / len(nf), 4)
        if nf else None)
    gaps = [r["gap_s"] for r in rec if r["gap_s"] is not None]
    out["gap_s"] = {"median": q(gaps, .5), "p25": q(gaps, .25),
                    "p75": q(gaps, .75), "p90": q(gaps, .9),
                    "mean": round(st.mean(gaps), 2) if gaps else None}
    for c in ("EXTEND", "COMPLETE", "REOPEN"):
        g = [r["gap_s"] for r in rec
             if r["class"] == c and r["gap_s"] is not None]
        out[f"gap_s_{c}"] = {"n": len(g), "median": q(g, .5), "p90": q(g, .9)}
    ar = [r["abs_resid_before"] for r in rec]
    out["abs_residual_before_action"] = {
        "median": str(q(ar, .5)), "p90": str(q(ar, .9)), "max": str(max(ar))}
    ext = [r for r in rec if r["class"] == "EXTEND"]
    out["extend_basis_improving_share"] = (
        round(sum(1 for r in ext if r["basis_improving"]) / len(ext), 4)
        if ext else None)
    out["cycled_after_match_actions"] = sum(1 for r in rec
                                            if r["cycled_after_match"])
    sz = [r["size"] for r in rec]
    out["size"] = {"median": str(q(sz, .5)), "p90": str(q(sz, .9)),
                   "max": str(max(sz))}
    out["fragments_per_action"] = {
        "median": q([r["fragments"] for r in rec], .5),
        "p90": q([r["fragments"] for r in rec], .9)}
    sl = [r["secs_left"] for r in rec if r["secs_left"] is not None]
    out["secs_left_at_action"] = {"median": q(sl, .5), "p10": q(sl, .1),
                                 "p90": q(sl, .9),
                                 "negative(after_close)": sum(1 for x in sl
                                                              if x < 0)}
    # does size depend on imbalance?
    big = [r["size"] for r in rec if r["abs_resid_before"] > 0]
    flat = [r["size"] for r in rec if r["abs_resid_before"] == 0]
    out["size_median_when_imbalanced"] = str(q(big, .5)) if big else None
    out["size_median_when_flat"] = str(q(flat, .5)) if flat else None
    return out


def main(argv):
    rows = load_rows(argv[1:])
    mkt = actions_by_market(rows)
    rec = census(mkt)
    print(json.dumps(summarise(rec), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
