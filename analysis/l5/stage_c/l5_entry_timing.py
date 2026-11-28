"""What creates the ENTRY half of the discount: time, or skill?

The first Up trade and the first Down trade together cost ~0.98-0.99, below a
dollar, even though the two outcomes price to ~1.00 at any single instant.
They must therefore be separated in time. This measures that separation and
asks whether the discount is mechanical (a maker collecting spread on both
sides as the price oscillates) or predictive (L5 knowing which side will fall).

The predictive reading has three independent tests available, and all three
are already answered by the reconstruction:
  - L5's residual P&L per contract      (skill would make it positive)
  - its basis position within each side's traded range (skill -> near 0)
  - whether basis-improving trades follow a price fall (skill -> >> 50%)
"""
from __future__ import annotations
import collections, gzip, json, os
from decimal import Decimal as D

SRC = ("data/l5/raw/activity.jsonl", "data/l5/raw/activity_gap.jsonl.gz",
       "data/l5/ops/activity_20260901.jsonl")
def _rows(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln: continue
            try: d = json.loads(ln)
            except Exception: continue
            pay = d.get("payload")
            if isinstance(pay, list):
                for r in pay: yield r
            elif isinstance(d, dict) and d.get("slug"): yield d

keep = set(json.load(open("/tmp/claude-0/train.json"))
           + json.load(open("/tmp/claude-0/test.json")))
seen = set(); T = collections.defaultdict(list)
for p in SRC:
    if not os.path.exists(p): continue
    for r in _rows(p):
        k = (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
             r.get("size"), r.get("side"), r.get("type"))
        if k in seen: continue
        seen.add(k)
        s = str(r.get("slug") or "")
        if r.get("type") != "TRADE" or s not in keep: continue
        ws = int(s.rsplit("-", 1)[1])
        T[s].append({"t": int(r["timestamp"]) - ws,
                     "oc": str(r.get("outcome") or ""),
                     "px": D(str(r.get("price") or 0))})

gaps, disc, both_first_same_sec = [], [], 0
for slug, acts in T.items():
    acts.sort(key=lambda a: a["t"])
    f = {}
    for a in acts:
        if a["oc"] in ("Up", "Down") and a["oc"] not in f:
            f[a["oc"]] = a
        if len(f) == 2: break
    if len(f) != 2: continue
    gap = abs(f["Up"]["t"] - f["Down"]["t"])
    d = D(1) - (f["Up"]["px"] + f["Down"]["px"])
    gaps.append(gap); disc.append(d)
    if gap == 0: both_first_same_sec += 1

def med(v):
    v = sorted(v); return v[len(v)//2]
print("windows with a first trade on BOTH sides: %d" % len(gaps))
print("\nTIME between the first Up and the first Down trade:")
g = sorted(gaps)
print("   p10=%ds  median=%ds  p90=%ds" % (g[len(g)//10], g[len(g)//2],
                                           g[len(g)*9//10]))
print("   both within the same second: %d (%.1f%%)"
      % (both_first_same_sec, 100.0*both_first_same_sec/len(gaps)))
print("\nENTRY discount 1 - (first_up + first_dn):")
print("   median %+.4f" % med(disc))

# the discount CONDITIONAL on the time gap
B = collections.defaultdict(list)
for gp, d in zip(gaps, disc):
    for lo, hi, lbl in ((0, 0, "same second"), (1, 10, "1-10s"),
                        (11, 60, "11-60s"), (61, 300, "61-300s"),
                        (301, 10**9, ">300s")):
        if lo <= gp <= hi:
            B[lbl].append(d); break
print("\nENTRY discount BY the gap between the two first entries:")
print("   %-14s %6s %10s" % ("gap", "n", "median"))
for lbl in ("same second", "1-10s", "11-60s", "61-300s", ">300s"):
    if B.get(lbl):
        print("   %-14s %6d %+10.4f" % (lbl, len(B[lbl]), med(B[lbl])))
print("\n   If the discount were mechanical spread capture by a resting maker,")
print("   it would be present even at a ZERO time gap -- two quotes resting")
print("   simultaneously, both filled, costs 1 - spread - 2*depth. If it came")
print("   from waiting out a price move, it would GROW with the gap.")
