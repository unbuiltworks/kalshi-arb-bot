"""What makes L5 EXTEND the heavy side instead of COMPLETING the deficient one?

This is a SEQUENTIAL DECISION study on L5's own public fills. For every grouped
action it reconstructs the state that L5's PRIOR FILLS can support, then asks
which state variables separate:

  (a) EXTEND (add to the already-heavy side) from COMPLETE (work the deficient
      side); and
  (b) basis-IMPROVING EXTENDs from basis-WORSENING ones -- because "only extend
      if basis improves" demonstrably cannot explain L5, who worsens his basis
      roughly half the time.

EXPLICITLY UNAVAILABLE, never inferred: maker/taker role, order placements,
cancels, requotes, resting quotes, book depth, opposite-venue state. The feed is
fills only. Variables derived from L5's own fills are marked OWN_FILLS; nothing
else is used.

Settlement outcome is marked UNAVAILABLE unless a REDEEM row for the market is
present in the same harvest.

    python3 scripts/l5_extend_model.py <activity.json[.gz]> [...]
"""
from __future__ import annotations

import gzip
import json
import math
import sys
from collections import defaultdict
from decimal import Decimal as D

WINDOW_S = 900
F = float


def load(paths):
    rows = []
    for p in paths:
        op = gzip.open if p.endswith(".gz") else open
        o = json.loads(op(p, "rt").read())
        rows += o["rows"] if isinstance(o, dict) and "rows" in o else o
    return rows


def group_actions(rows, kind="btc-updown-15m"):
    """Fill fragments sharing (ts, slug, outcome, price) are ONE order."""
    frag, redeem = defaultdict(list), defaultdict(list)
    for p in rows:
        s = str(p.get("slug") or "")
        if not s.startswith(kind):
            continue
        if p.get("type") == "REDEEM":
            redeem[s].append(p)
            continue
        if p.get("type") != "TRADE":
            continue
        frag[(s, p["timestamp"], p.get("outcome"), str(p.get("price")))].append(p)
    mkt = defaultdict(list)
    for (slug, ts, out, px), fr in frag.items():
        mkt[slug].append({"t": ts, "o": out, "px": D(str(px)),
                          "sz": sum(D(str(f.get("size") or 0)) for f in fr),
                          "frags": len(fr)})
    for s in mkt:
        mkt[s].sort(key=lambda a: (a["t"], str(a["o"])))
    return mkt, redeem


def features(mkt, redeem):
    """One row per action, carrying only state derivable from prior own fills."""
    rec = []
    for slug, acts in mkt.items():
        try:
            ws = int(slug.split("-")[-1])
        except ValueError:
            continue
        qty = {"Up": D(0), "Down": D(0)}
        cost = {"Up": D(0), "Down": D(0)}
        last = {}
        prev = None
        for i, a in enumerate(acts):
            o, other = a["o"], ("Down" if a["o"] == "Up" else "Up")
            resid = qty[o] - qty[other]
            matched = min(qty["Up"], qty["Down"])
            basis = {s: (cost[s] / qty[s] if qty[s] > 0 else None)
                     for s in ("Up", "Down")}
            pair_cost = (basis["Up"] + basis["Down"]
                         if basis["Up"] is not None and basis["Down"] is not None
                         else None)
            if i == 0:
                cls = "OPEN"
            elif resid > 0:
                cls = "EXTEND"
            elif resid < 0:
                cls = "COMPLETE"
            else:
                cls = "REOPEN"
            same_px = last.get(o)
            # OWN_FILLS price movement: last price on this outcome, and the
            # last price on the opposite outcome, versus this action's price.
            rec.append({
                "slug": slug, "i": i, "t": a["t"], "cls": cls, "o": o,
                "px": a["px"], "sz": a["sz"], "frags": a["frags"],
                "qty_same": qty[o], "qty_other": qty[other],
                "matched": matched,
                "resid": resid, "abs_resid": abs(resid),
                "heavy_is_this_side": resid > 0,
                "basis_same": basis[o], "basis_other": basis[other],
                "pair_cost": pair_cost,
                "secs_left": ws + WINDOW_S - a["t"],
                "secs_into": a["t"] - ws,
                "prev_o": (prev["o"] if prev else None),
                "prev_px": (prev["px"] if prev else None),
                "prev_sz": (prev["sz"] if prev else None),
                "gap_s": (a["t"] - prev["t"] if prev else None),
                "same_side_as_prev": (prev is not None and prev["o"] == o),
                "px_vs_last_same": (None if same_px is None
                                    else a["px"] - same_px),
                "basis_improving": (same_px is not None and
                                    basis[o] is not None and
                                    a["px"] < basis[o]),
                "px": a["px"],
                "settlement": ("REDEEM_PRESENT" if redeem.get(slug)
                               else "UNAVAILABLE"),
            })
            qty[o] += a["sz"]
            cost[o] += a["sz"] * a["px"]
            last[o] = a["px"]
            prev = a
    # subsequent action and latency
    by_slug = defaultdict(list)
    for r in rec:
        by_slug[r["slug"]].append(r)
    for s in by_slug:
        xs = sorted(by_slug[s], key=lambda r: r["i"])
        for j, r in enumerate(xs):
            nxt = xs[j + 1] if j + 1 < len(xs) else None
            r["next_cls"] = nxt["cls"] if nxt else None
            r["next_latency_s"] = (nxt["t"] - r["t"]) if nxt else None
    return rec


def rate_table(rec, var, bins, label, target):
    """P(target) across bins of `var`. Pure counting, no model fitting."""
    out = []
    for lo, hi in bins:
        sel = [r for r in rec
               if r[var] is not None and lo <= F(r[var]) < hi]
        if len(sel) < 50:
            continue
        p = sum(1 for r in sel if target(r)) / len(sel)
        out.append((f"[{lo:g},{hi:g})", len(sel), round(p, 4)))
    print(f"\n  P({label}) by {var}:")
    for b, n, p in out:
        print(f"    {b:>16} n={n:<7} p={p}")
    return out


def lift(rec, name, pred, target):
    a = [r for r in rec if pred(r)]
    b = [r for r in rec if not pred(r)]
    if not a or not b:
        return None
    pa = sum(1 for r in a if target(r)) / len(a)
    pb = sum(1 for r in b if target(r)) / len(b)
    return (name, len(a), round(pa, 4), len(b), round(pb, 4),
            round(pa - pb, 4))


def main(argv):
    rows = load(argv[1:])
    mkt, redeem = group_actions(rows)
    rec = features(mkt, redeem)
    foll = [r for r in rec if r["cls"] in ("EXTEND", "COMPLETE")]
    print(f"markets={len({r['slug'] for r in rec})} actions={len(rec)} "
          f"EXTEND/COMPLETE decisions={len(foll)}")
    base = sum(1 for r in foll if r["cls"] == "EXTEND") / len(foll)
    print(f"base rate P(EXTEND) = {base:.4f}")

    isext = lambda r: r["cls"] == "EXTEND"                   # noqa: E731
    print("\n================ P(EXTEND) vs state ================")
    rate_table(foll, "abs_resid", [(0, 1), (1, 10), (10, 50), (50, 150),
                                   (150, 400), (400, 1e9)], "EXTEND", isext)
    rate_table(foll, "secs_left", [(-1e9, 0), (0, 60), (60, 180), (180, 420),
                                   (420, 700), (700, 1e9)], "EXTEND", isext)
    rate_table(foll, "px", [(0, .1), (.1, .3), (.3, .5), (.5, .7), (.7, .9),
                            (.9, 1.01)], "EXTEND", isext)
    rate_table(foll, "pair_cost", [(0, .9), (.9, .96), (.96, 1.0), (1.0, 1.05),
                                   (1.05, 1e9)], "EXTEND", isext)
    rate_table(foll, "gap_s", [(0, 1), (1, 3), (3, 10), (10, 30), (30, 1e9)],
               "EXTEND", isext)
    rate_table(foll, "matched", [(0, 1), (1, 20), (20, 100), (100, 400),
                                 (400, 1e9)], "EXTEND", isext)

    print("\n================ single-condition lifts for EXTEND ================")
    cands = [
        ("prev action was same side", lambda r: r["same_side_as_prev"]),
        ("price <= 0.10", lambda r: F(r["px"]) <= .10),
        ("price >= 0.90", lambda r: F(r["px"]) >= .90),
        ("secs_left <= 180", lambda r: r["secs_left"] <= 180),
        ("abs_resid >= 50", lambda r: F(r["abs_resid"]) >= 50),
        ("pair_cost is None (one-sided)", lambda r: r["pair_cost"] is None),
        ("pair_cost < 0.96", lambda r: r["pair_cost"] is not None
         and F(r["pair_cost"]) < .96),
        ("px below own same-side basis", lambda r: r["basis_same"] is not None
         and F(r["px"]) < F(r["basis_same"])),
        ("px below opposite-side basis", lambda r: r["basis_other"] is not None
         and F(r["px"]) < F(r["basis_other"])),
    ]
    res = [x for x in (lift(foll, n, p, isext) for n, p in cands) if x]
    res.sort(key=lambda x: -abs(x[5]))
    print(f"  {'condition':<34}{'n_true':>8}{'p_true':>9}{'n_false':>9}"
          f"{'p_false':>9}{'delta':>9}")
    for n, nt, pt, nf, pf, d in res:
        print(f"  {n:<34}{nt:>8}{pt:>9}{nf:>9}{pf:>9}{d:>9}")

    print("\n====== basis-IMPROVING vs basis-WORSENING EXTENDs ======")
    ext = [r for r in rec if r["cls"] == "EXTEND"]
    imp = [r for r in ext if r["basis_improving"]]
    wor = [r for r in ext if not r["basis_improving"]]
    print(f"  improving={len(imp)}  worsening={len(wor)}  "
          f"share_improving={len(imp)/len(ext):.4f}")
    for nm, f in (("abs_resid", lambda r: F(r["abs_resid"])),
                  ("secs_left", lambda r: F(r["secs_left"])),
                  ("px", lambda r: F(r["px"])),
                  ("sz", lambda r: F(r["sz"])),
                  ("gap_s", lambda r: F(r["gap_s"] or 0)),
                  ("matched", lambda r: F(r["matched"]))):
        mi = sorted(f(r) for r in imp)
        mw = sorted(f(r) for r in wor)
        md = lambda v: v[len(v) // 2]                        # noqa: E731
        print(f"    {nm:<12} median improving={md(mi):<12.4f} "
              f"median worsening={md(mw):<12.4f}")
    # what does a worsening extend do next?
    for nm, grp in (("improving", imp), ("worsening", wor)):
        nx = defaultdict(int)
        for r in grp:
            nx[r["next_cls"]] += 1
        lat = sorted(r["next_latency_s"] for r in grp
                     if r["next_latency_s"] is not None)
        print(f"    after {nm}: next={dict(nx)} "
              f"median_latency={lat[len(lat)//2] if lat else None}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
