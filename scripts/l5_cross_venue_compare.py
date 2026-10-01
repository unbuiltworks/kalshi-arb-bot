"""CROSS-VENUE, WINDOW-LEVEL comparison: L5 (Polymarket) vs frozen clone (PMUS).

STRICTLY NOT same-book state matching. The two venues have different books,
different queues and different prices. What is shared is the 15-minute epoch and
the underlying (BTC up/down). So this compares TRAJECTORY SHAPE within the same
wall-clock window, never L5's price against a PMUS price.

L5 side: grouped actions from his own fills (fragments merged).
Clone side: the frozen `49799e99` per-window metrics as already produced by
scripts/l5_run_two_sided_comparison.py. No clone re-run, no policy change.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from decimal import Decimal as D

WINDOW_S = 900
F = float


def median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def l5_by_window(path):
    o = json.loads(gzip.open(path, "rt").read())
    frag = defaultdict(list)
    for p in o["rows"]:
        s = str(p.get("slug") or "")
        if p.get("type") != "TRADE" or not s.startswith("btc-updown-15m"):
            continue
        frag[(s, p["timestamp"], p.get("outcome"), str(p.get("price")))].append(p)
    acts = defaultdict(list)
    for (slug, ts, out, px), fr in frag.items():
        try:
            ep = int(slug.split("-")[-1])
        except ValueError:
            continue
        acts[ep].append({"t": ts, "o": out, "px": D(str(px)),
                         "sz": sum(D(str(f.get("size") or 0)) for f in fr)})
    out = {}
    for ep, a in acts.items():
        a.sort(key=lambda x: (x["t"], str(x["o"])))
        qty = {"Up": D(0), "Down": D(0)}
        same = 0
        peak_resid = D(0)
        t_above1 = 0.0
        prev = None
        for x in a:
            if prev is not None:
                dt = float(x["t"] - prev["t"])
                if abs(qty["Up"] - qty["Down"]) > 0:
                    t_above1 += dt
                if prev["o"] == x["o"]:
                    same += 1
            qty[x["o"]] += x["sz"]
            r = abs(qty["Up"] - qty["Down"])
            peak_resid = max(peak_resid, r)
            prev = x
        clip = median([x["sz"] for x in a]) or D(1)
        out[ep] = {
            "actions": len(a),
            "same_side_share": (same / (len(a) - 1)) if len(a) > 1 else None,
            "qty_up": qty["Up"], "qty_down": qty["Down"],
            "matched": min(qty["Up"], qty["Down"]),
            "residual_end": qty["Up"] - qty["Down"],
            "peak_residual": peak_resid,
            "peak_residual_clips": peak_resid / clip,
            "clip": clip,
            "first_s": a[0]["t"] - ep, "last_s": a[-1]["t"] - ep,
            "span_s": a[-1]["t"] - a[0]["t"],
            "t_imbalanced_s": t_above1,
            "acts_last_120s": sum(1 for x in a if ep + WINDOW_S - x["t"] <= 120),
        }
    return out


def clone_by_window(paths, arm="L5_REPLICATION"):
    out = {}
    for p in paths:
        txt = open(p).read()
        rows = json.loads(txt[txt.index('{\n "windows"'):
                              txt.index('\n\n================')])["windows"]
        for r in rows:
            a = r[arm]
            out[r["slug"]] = {
                "fills": int(a["maker_fills"]) + int(a["taker_fills"]),
                "maker_fills": int(a["maker_fills"]),
                "taker_fills": int(a["taker_fills"]),
                "matched": D(str(a["matched_contracts"])),
                "residual_end": D(str(a["residual_now"])),
                "peak_residual": D(str(a["max_directional_residual"])),
                "t_one_sided_s": F(a["time_one_sided_s"]),
                "cancels": int(a["cancels"]),
                "takes": int(a["takes"]),
                "blocked": a.get("blocked_reasons") or {},
            }
    return out


def main(argv):
    l5 = l5_by_window(argv[1])
    overlap = json.loads(open(argv[2]).read())
    clone = clone_by_window(argv[3:])
    pm = overlap["pm"]
    eps = [e for e in overlap["overlap"] if str(e) in pm
           and pm[str(e)] in clone and e in l5]
    print(f"overlapping windows compared: {len(eps)}  "
          f"(cross-venue, same 15m epoch, NOT same book)")
    print("\n%-8s %7s %7s | %8s %8s | %9s %9s | %7s %7s | %6s"
          % ("window", "L5_act", "CL_fill", "L5_match", "CL_match",
             "L5_pkRes", "CL_pkRes", "L5_imb", "CL_1sid", "L5_l120"))
    agg = defaultdict(list)
    for e in eps:
        a, c = l5[e], clone[pm[str(e)]]
        print("%-8s %7d %7d | %8.1f %8s | %9.1f %9s | %7.0f %7.0f | %6d"
              % (pm[str(e)][-5:], a["actions"], c["fills"],
                 F(a["matched"]), c["matched"], F(a["peak_residual"]),
                 c["peak_residual"], a["t_imbalanced_s"], c["t_one_sided_s"],
                 a["acts_last_120s"]))
        agg["l5_act"].append(a["actions"])
        agg["cl_fill"].append(c["fills"])
        agg["l5_match"].append(F(a["matched"]))
        agg["cl_match"].append(F(c["matched"]))
        agg["l5_pk"].append(F(a["peak_residual"]))
        agg["l5_pk_clips"].append(F(a["peak_residual_clips"]))
        agg["cl_pk"].append(F(c["peak_residual"]))
        agg["l5_imb"].append(a["t_imbalanced_s"])
        agg["cl_1sid"].append(c["t_one_sided_s"])
        agg["l5_first"].append(a["first_s"])
        agg["l5_last"].append(a["last_s"])
        agg["l5_l120"].append(a["acts_last_120s"])
        if a["same_side_share"] is not None:
            agg["l5_same"].append(a["same_side_share"])
    n = len(eps)
    print("\n================ MEDIANS over %d overlapping windows ================" % n)
    rows = [
        ("actions / fills per window", "l5_act", "cl_fill"),
        ("matched contracts", "l5_match", "cl_match"),
        ("peak directional residual (contracts)", "l5_pk", "cl_pk"),
        ("seconds imbalanced / one-sided", "l5_imb", "cl_1sid"),
    ]
    print("  %-42s %12s %12s %8s" % ("metric", "L5", "CLONE", "ratio"))
    for lab, kl, kc in rows:
        ml, mc = median(agg[kl]), median(agg[kc])
        r = (ml / mc) if mc else None
        print("  %-42s %12.1f %12.1f %8s"
              % (lab, ml, mc, f"{r:.1f}x" if r else "n/a"))
    print("  %-42s %12.2f %12s %8s"
          % ("peak residual in L5's OWN CLIPS", median(agg["l5_pk_clips"]),
             "2 (cap)", "-"))
    print("  %-42s %12.3f %12s %8s"
          % ("same-side share of follow-ups", median(agg["l5_same"]),
             "0.000", "clone does 0 extends"))
    print("  %-42s %12.0f %12s %8s"
          % ("first action, seconds into window", median(agg["l5_first"]),
             "-", "-"))
    print("  %-42s %12.0f %12s %8s"
          % ("last action, seconds into window", median(agg["l5_last"]),
             "-", "-"))
    print("  %-42s %12.0f %12s %8s"
          % ("actions in final 120s (clone: blocked)",
             median(agg["l5_l120"]), "0", "no_new_cycle"))
    tot_l5 = sum(agg["l5_act"])
    tot_cl = sum(agg["cl_fill"])
    print(f"\n  TOTALS: L5 {tot_l5} grouped actions vs clone {tot_cl} fills "
          f"over the same {n} windows  ({tot_l5/tot_cl:.1f}x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
