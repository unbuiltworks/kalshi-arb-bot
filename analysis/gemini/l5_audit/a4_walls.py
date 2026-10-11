"""L5 SETTINGS AUDIT A4 -- reverse-engineer the giant displayed liquidity.

WHAT IS THE LARGE MAKER DOING THAT WE ARE NOT?

Descriptive clustering only. No thresholds optimized to manufacture a
pattern: a "wall" is any displayed level whose size at first sight is >= a
fixed reference, and the three references are reported side by side so the
reader sees the sensitivity rather than one chosen cut.

Per wall, reconstructed from the event-level diff stream:
  first appearance (time, relative to window start), price, size
  atomic vs built (size at first sight / max size ever held)
  lifetime, total cancelled, total executed against it
  price migration (did the same maker re-post at another price?)
  whether a comparable wall later appears on the OPPOSITE side
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")
REFS = (100.0, 1000.0, 10000.0)


def pct(v, q):
    if not v:
        return None
    s = sorted(v)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def main():
    ev, win = defaultdict(list), {}
    for f in sorted(Path("data/gemini_hifi").glob("hifi_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k, m = r.get("kind"), r.get("market") or ""
            if not SYM.match(m):
                continue
            if k == "window":
                win[m] = r
            elif k in ("depth_update", "depth_snapshot", "trade"):
                ev[m].append(r)

    walls = []
    for m, rows in ev.items():
        meta = win.get(m)
        if not meta:
            continue
        expiry = meta["expiry_t"]
        start = expiry - 300.0
        rows.sort(key=lambda r: (r.get("t") or 0))
        books = {"bid": {}, "ask": {}}
        live = {}           # (side,px) -> dict
        seeded = False
        for r in rows:
            k, t = r["kind"], r.get("t")
            if k == "depth_snapshot":
                if seeded:
                    continue
                seeded = True
                for sd, key in (("bid", "bids"), ("ask", "asks")):
                    for px, sz in (r.get(key) or []):
                        books[sd][float(px)] = float(sz)
                continue
            if k == "depth_update":
                if not seeded:
                    continue
                for sd, key in (("bid", "b"), ("ask", "a")):
                    for px, sz in (r.get(key) or []):
                        p, s = float(px), float(sz)
                        before = books[sd].get(p, 0.0)
                        if s == 0.0:
                            books[sd].pop(p, None)
                        else:
                            books[sd][p] = s
                        kk = (sd, p)
                        if s > before:        # size added
                            if kk not in live:
                                live[kk] = {"market": m, "side": sd, "px": p,
                                            "t_first": t, "size_first": s,
                                            "size_max": s, "cancelled": 0.0,
                                            "executed": 0.0, "t_last": t}
                            else:
                                live[kk]["size_max"] = max(live[kk]["size_max"], s)
                        elif s < before and kk in live:
                            live[kk]["cancelled"] += (before - s)
                        if kk in live:
                            live[kk]["t_last"] = t
                            if s == 0.0:
                                w = live.pop(kk)
                                w["lifetime"] = t - w["t_first"]
                                w["rel_T0"] = w["t_first"] - start
                                w["in_window"] = start <= w["t_first"] <= expiry
                                walls.append(w)
                continue
            if k == "trade":
                try:
                    p, q = float(r["price"]), float(r["qty"])
                except Exception:
                    continue
                bim = r.get("buyer_is_maker")
                sd = "bid" if bim is True else "ask" if bim is False else None
                if sd and (sd, p) in live:
                    live[(sd, p)]["executed"] += q
                    live[(sd, p)]["cancelled"] = max(
                        0.0, live[(sd, p)]["cancelled"] - q)
        for kk, w in live.items():
            w["lifetime"] = expiry - w["t_first"]
            w["rel_T0"] = w["t_first"] - start
            w["in_window"] = start <= w["t_first"] <= expiry
            w["still_open"] = True
            walls.append(w)

    Path("analysis/gemini/l5_audit/a4_walls.json").write_text(
        json.dumps(walls, indent=1))

    iw = [w for w in walls if w["in_window"]]
    print("=" * 88)
    print("A4  GIANT LIQUIDITY FORENSICS -- what is the large maker doing?")
    print("=" * 88)
    print(f"\nlevels tracked in-window: {len(iw)} (all sizes)\n")
    print(f"{'ref':>8s} {'walls':>6s} {'size_first_p50':>15s} {'atomic%':>8s} "
          f"{'life_p50':>9s} {'cancelled%':>11s} {'executed%':>10s} {'rel_T0_p50':>11s}")
    print("-" * 86)
    for ref in REFS:
        g = [w for w in iw if w["size_first"] >= ref]
        if not g:
            continue
        atomic = sum(1 for w in g if w["size_first"] >= 0.999 * w["size_max"])
        tc = sum(w["cancelled"] for w in g)
        te = sum(w["executed"] for w in g)
        tot = sum(w["size_max"] for w in g)
        print(f"{ref:8.0f} {len(g):6d} {pct([w['size_first'] for w in g],.5):15.1f} "
              f"{atomic/len(g)*100:7.1f}% "
              f"{pct([w['lifetime'] for w in g],.5):8.1f}s "
              f"{(tc/tot*100 if tot else 0):10.1f}% "
              f"{(te/tot*100 if tot else 0):9.1f}% "
              f"{pct([w['rel_T0'] for w in g],.5):+10.1f}")
    print()
    print("ATOMIC% = size at first sight is already its maximum, i.e. posted")
    print("whole rather than built up. CANCELLED%/EXECUTED% are shares of all")
    print("size the level ever held.")
    print()
    g = [w for w in iw if w["size_first"] >= REFS[1]]
    if g:
        print(f"--- walls >= {REFS[1]:.0f} contracts: fate ---")
        print(f"  n={len(g)}  price p10={pct([w['px'] for w in g],.1):.2f} "
              f"p50={pct([w['px'] for w in g],.5):.2f} "
              f"p90={pct([w['px'] for w in g],.9):.2f}")
        never = sum(1 for w in g if w["executed"] == 0.0)
        print(f"  NEVER executed against at all : {never}/{len(g)} "
              f"({never/len(g)*100:.1f}%)")
        allc = sum(1 for w in g if w["executed"] == 0.0 and w["cancelled"] > 0)
        print(f"  cancelled with zero execution : {allc}/{len(g)} "
              f"({allc/len(g)*100:.1f}%)")
        print(f"  lifetime (s) p10={pct([w['lifetime'] for w in g],.1):.1f} "
              f"p50={pct([w['lifetime'] for w in g],.5):.1f} "
              f"p90={pct([w['lifetime'] for w in g],.9):.1f}")
        byside = defaultdict(int)
        for w in g:
            byside[w["side"]] += 1
        print(f"  side split: {dict(byside)}")
        # opposite-side pairing: same market, opposite side, within 30 s
        pair = 0
        for w in g:
            for v in g:
                if v is w or v["market"] != w["market"] or v["side"] == w["side"]:
                    continue
                if abs(v["t_first"] - w["t_first"]) <= 30.0:
                    pair += 1
                    break
        print(f"  has an opposite-side wall within 30 s : {pair}/{len(g)} "
              f"({pair/len(g)*100:.1f}%)")
    print("\nwrote analysis/gemini/l5_audit/a4_walls.json")


if __name__ == "__main__":
    main()
