"""WHEN in the window does the pair discount arrive?

Computes the pair cost L5 would have locked had it stopped at each quartile
of elapsed window time. If the discount accrues LATE, the mechanism is
completing the out-of-the-money side cheaply, not entering well -- and the
actionable rule is about the completion price, not the opening.
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

tr = set(json.load(open("/tmp/claude-0/train.json")))
te = set(json.load(open("/tmp/claude-0/test.json")))
keep = tr | te
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
                     "q": D(str(r.get("size") or 0)),
                     "px": D(str(r.get("price") or 0))})

CUTS = [225, 450, 675, 900]
acc = {g: collections.defaultdict(list) for g in ("train", "test")}
cheap_side_late = cheap_den = 0
for slug, acts in T.items():
    acts.sort(key=lambda a: a["t"])
    g = "train" if slug in tr else "test"
    for cut in CUTS:
        qu = cu = qd = cd = D(0)
        for a in acts:
            if a["t"] > cut: break
            if a["oc"] == "Up": qu += a["q"]; cu += a["q"]*a["px"]
            elif a["oc"] == "Down": qd += a["q"]; cd += a["q"]*a["px"]
        if qu > 0 and qd > 0:
            acc[g][cut].append((cu/qu) + (cd/qd))
    # of trades in the LAST quarter, what fraction are on the side whose
    # price is below 0.25 -- i.e. buying the out-of-the-money leg cheaply?
    for a in acts:
        if a["t"] > 675:
            cheap_den += 1
            if a["px"] < D("0.25"):
                cheap_side_late += 1

def med(v):
    v = sorted(v); return v[len(v)//2]
print("PAIR COST IF L5 HAD STOPPED AT EACH QUARTILE (median)\n")
print("%-14s %12s %12s" % ("stopped at", "TRAIN", "TEST"))
for cut in CUTS:
    a = acc["train"].get(cut) or []
    b = acc["test"].get(cut) or []
    print("%-14s %12s %12s" % (
        "%ds (%d%%)" % (cut, cut*100//900),
        ("%.4f (n=%d)" % (med(a), len(a))) if a else "-",
        ("%.4f (n=%d)" % (med(b), len(b))) if b else "-"))
print("\n  A pair cost that FALLS across the window means the discount is")
print("  earned by the trades that arrive LATE, not by the entry.")
print("\nTrades in the final quarter priced below 0.25 (deep out-of-the-money):")
print("   %d of %d  (%.1f%%)" % (cheap_side_late, cheap_den,
                                 100.0*cheap_side_late/max(1, cheap_den)))
