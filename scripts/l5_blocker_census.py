"""COUNTERFACTUAL BLOCKER CENSUS: which frozen-clone gates would forbid L5?

For every grouped L5 acquisition, this replays the FROZEN clone's structural
gates (`49799e99`) against state reconstructed from L5's OWN prior fills, and
records which gate -- if any -- would have prohibited the analogous action.

TWO UNIT SYSTEMS, reported separately, because conflating them is how a
structural finding gets mistaken for a size finding:

  * CONTRACTS -- L5's raw quantities.
  * CLIPS     -- L5's quantities divided by HIS OWN median action size, so a
                 residual of 63 contracts at size 20 is 3.2 clips. The clone's
                 `residual_cap=2` is a cap in CLIPS. Comparing 63 against 2 is
                 a category error; comparing 3.2 against 2 is the real test.

NO PMUS PRICE IS USED ANYWHERE HERE. Every quantity comes from L5's own fill
sequence on his own venue. Cross-venue comparison is a separate report.

EVIDENCE STATUS: simultaneous two-sided resting quotes are the leading
INFERENCE from the fill sequence (8.86% of active seconds show both outcomes
filling within one second). Orders, cancels and resting quotes are NOT observed.

The frozen gates, from src/l5_two_sided.py `desired()`:
  heavy side  -> allow_extend False               => heavy_side_extend_disabled
              -> px >= own basis                  => extend_would_not_improve_basis
              -> abs(resid) + clip > residual_cap => extend_breaches_residual_cap
  deficient   -> completion worked (allowed)
  flat        -> secs_left <= 120                 => no_new_cycle_near_settlement
              -> cycles_started >= max_cycles     => max_cycles
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from decimal import Decimal as D

WINDOW_S = 900
RESIDUAL_CAP = D(2)          # clone default, in clips
NO_NEW_CYCLE_S = 120.0
MAX_CYCLES_REPLICATION = 50
THRESHOLDS = (1, 2, 5, 10, 25, 50, 100)
F = float


def load(paths):
    rows = []
    for p in paths:
        op = gzip.open if p.endswith(".gz") else open
        o = json.loads(op(p, "rt").read())
        rows += o["rows"] if isinstance(o, dict) and "rows" in o else o
    return rows


def group(rows, kind="btc-updown-15m"):
    frag = defaultdict(list)
    for p in rows:
        s = str(p.get("slug") or "")
        if p.get("type") != "TRADE" or not s.startswith(kind):
            continue
        frag[(s, p["timestamp"], p.get("outcome"), str(p.get("price")))].append(p)
    mkt = defaultdict(list)
    for (slug, ts, out, px), fr in frag.items():
        mkt[slug].append({"t": ts, "o": out, "px": D(str(px)),
                          "sz": sum(D(str(f.get("size") or 0)) for f in fr)})
    for s in mkt:
        mkt[s].sort(key=lambda a: (a["t"], str(a["o"])))
    return mkt


def median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def census(mkt, clip_unit):
    """Walk L5's sequence; attribute the FIRST clone gate that would block."""
    out = []
    resid_time = defaultdict(float)      # threshold -> seconds held above it
    resid_time_clips = defaultdict(float)
    total_time = 0.0
    for slug, acts in mkt.items():
        try:
            ws = int(slug.split("-")[-1])
        except ValueError:
            continue
        qty = {"Up": D(0), "Down": D(0)}
        cost = {"Up": D(0), "Down": D(0)}
        cycles_started = 0
        prev_t = None
        for a in acts:
            o = a["o"]
            other = "Down" if o == "Up" else "Up"
            resid = qty[o] - qty[other]
            basis = (cost[o] / qty[o]) if qty[o] > 0 else None
            secs_left = ws + WINDOW_S - a["t"]
            # duration-weighted residual exposure
            if prev_t is not None:
                dt = float(a["t"] - prev_t)
                total_time += dt
                r = abs(qty["Up"] - qty["Down"])
                rc = r / clip_unit if clip_unit else D(0)
                for th in THRESHOLDS:
                    if r > th:
                        resid_time[th] += dt
                    if rc > th:
                        resid_time_clips[th] += dt
            gates = []
            if resid > 0:                                  # HEAVY side -> EXTEND
                cls = "EXTEND"
                gates.append("heavy_side_extend_disabled")  # allow_extend False
                if basis is not None and a["px"] >= basis:
                    gates.append("extend_would_not_improve_basis")
                if (abs(resid) / clip_unit) + D(1) > RESIDUAL_CAP:
                    gates.append("extend_breaches_residual_cap")
            elif resid < 0:                                 # deficient -> allowed
                cls = "COMPLETE"
            else:
                cls = "OPEN_OR_REOPEN"
                if secs_left <= NO_NEW_CYCLE_S:
                    gates.append("no_new_cycle_near_settlement")
                if cycles_started >= MAX_CYCLES_REPLICATION:
                    gates.append("max_cycles")
                cycles_started += 1
            out.append({"slug": slug, "t": a["t"], "cls": cls,
                        "px": a["px"], "sz": a["sz"],
                        "resid": resid, "abs_resid": abs(resid),
                        "resid_clips": abs(resid) / clip_unit if clip_unit else None,
                        "secs_left": secs_left,
                        "basis": basis,
                        "blocked": bool(gates),
                        "first_gate": gates[0] if gates else None,
                        "all_gates": gates})
            qty[o] += a["sz"]
            cost[o] += a["sz"] * a["px"]
            prev_t = a["t"]
    return out, resid_time, resid_time_clips, total_time


def main(argv):
    rows = load(argv[1:])
    mkt = group(rows)
    clip_unit = median([a["sz"] for acts in mkt.values() for a in acts])
    print(f"L5 median action size (the CLIP UNIT) = {clip_unit}")
    rec, rt, rtc, tt = census(mkt, clip_unit)
    n = len(rec)
    print(f"markets={len(mkt)} grouped actions={n}")

    print("\n================ BLOCKER CENSUS ================")
    blocked = [r for r in rec if r["blocked"]]
    print(f"actions the frozen clone would PROHIBIT: {len(blocked)} / {n} "
          f"= {len(blocked)/n:.4f}")
    first = defaultdict(int)
    for r in blocked:
        first[r["first_gate"]] += 1
    print("\n  attributed to the FIRST binding gate:")
    for g, c in sorted(first.items(), key=lambda x: -x[1]):
        print(f"    {g:<38} {c:7d}  {c/n:.4f} of all actions")
    anyg = defaultdict(int)
    for r in rec:
        for g in r["all_gates"]:
            anyg[g] += 1
    print("\n  each gate counted independently (gates overlap):")
    for g, c in sorted(anyg.items(), key=lambda x: -x[1]):
        print(f"    {g:<38} {c:7d}  {c/n:.4f} of all actions")

    print("\n  by action class:")
    for cls in ("OPEN_OR_REOPEN", "EXTEND", "COMPLETE"):
        s = [r for r in rec if r["cls"] == cls]
        if not s:
            continue
        b = sum(1 for r in s if r["blocked"])
        print(f"    {cls:<16} n={len(s):7d}  blocked={b:7d}  {b/len(s):.4f}")

    print("\n====== basis-improvement gate, measured on EXTENDs alone ======")
    ext = [r for r in rec if r["cls"] == "EXTEND"]
    fail = [r for r in ext if "extend_would_not_improve_basis" in r["all_gates"]]
    print(f"  EXTENDs failing the basis gate: {len(fail)} / {len(ext)} "
          f"= {len(fail)/len(ext):.4f}")
    print(f"  as a share of ALL actions: {len(fail)/n:.4f}")

    print("\n====== residual-cap gate, in CLIPS (the honest comparison) ======")
    capfail = [r for r in ext
               if "extend_breaches_residual_cap" in r["all_gates"]]
    print(f"  EXTENDs breaching residual_cap=2 clips: {len(capfail)} / "
          f"{len(ext)} = {len(capfail)/len(ext):.4f}")
    rc = [F(r["resid_clips"]) for r in rec if r["resid_clips"] is not None]
    print(f"  L5 |residual| in CLIPS: median={median(rc):.2f} "
          f"p90={sorted(rc)[int(.9*len(rc))]:.2f} max={max(rc):.2f}")
    ac = [F(r["abs_resid"]) for r in rec]
    print(f"  L5 |residual| in CONTRACTS: median={median(ac):.1f} "
          f"p90={sorted(ac)[int(.9*len(ac))]:.1f} max={max(ac):.1f}")

    print("\n====== time spent above residual thresholds ======")
    print(f"  total tracked inter-action time: {tt/3600:.2f} h")
    print(f"  {'threshold':>10}{'time>th (contracts)':>24}{'share':>9}"
          f"{'time>th (clips)':>18}{'share':>9}")
    for th in THRESHOLDS:
        print(f"  {th:>10}{rt[th]/3600:>20.2f} h{rt[th]/tt:>9.4f}"
              f"{rtc[th]/3600:>15.2f} h{rtc[th]/tt:>9.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
