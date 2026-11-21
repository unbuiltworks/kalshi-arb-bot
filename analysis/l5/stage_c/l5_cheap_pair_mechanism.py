"""HOW does L5 reach a 0.967 pair when p_up + p_dn ~= 1 at every instant?

At any instant the two outcomes price to about a dollar, so buying both
simultaneously costs ~1.00 and earns nothing. A 0.967 pair must therefore be
built ACROSS TIME. This decomposes the discount additively:

    discount            = 1 - (b_up + b_dn)
    entry_pair          = p_up(first) + p_dn(first)
    from_entry          = 1 - entry_pair          <- the first trade on each side
    from_averaging      = entry_pair - (b_up+b_dn) <- every trade after it

and then asks, of the trades that improved a side's running basis, how many
arrived after that side's price had FALLEN -- i.e. whether the averaging is
buying local dips rather than drifting.
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
        if r.get("type") != "TRADE": continue
        slug = "cpc" if False else s
        if s not in keep and ("btc-updown-15m-" not in s): continue
        if s not in keep: continue
        ws = int(s.rsplit("-", 1)[1])
        T[s].append({"t": int(r["timestamp"]) - ws,
                     "oc": str(r.get("outcome") or ""),
                     "q": D(str(r.get("size") or 0)),
                     "px": D(str(r.get("price") or 0))})
print("windows loaded (train+test, reconciled only): %d" % len(T))

tr = set(json.load(open("/tmp/claude-0/train.json")))
agg = {"train": collections.defaultdict(list), "test": collections.defaultdict(list)}
dip_num = dip_den = 0
for slug, acts in T.items():
    acts.sort(key=lambda a: a["t"])
    g = "train" if slug in tr else "test"
    first = {}; q = {"Up": D(0), "Down": D(0)}; c = {"Up": D(0), "Down": D(0)}
    pxs = {"Up": [], "Down": []}; lastpx = {}
    for a in acts:
        oc = a["oc"]
        if oc not in q: continue
        if oc not in first: first[oc] = a["px"]
        # does this trade improve that side's running basis, and did the
        # side's price just FALL?
        if q[oc] > 0:
            wav = c[oc] / q[oc]
            if a["px"] < wav:
                dip_den += 1
                if oc in lastpx and a["px"] < lastpx[oc]:
                    dip_num += 1
        lastpx[oc] = a["px"]
        q[oc] += a["q"]; c[oc] += a["q"] * a["px"]
        pxs[oc].append(a["px"])
    if not (q["Up"] > 0 and q["Down"] > 0): continue
    b_up = c["Up"] / q["Up"]; b_dn = c["Down"] / q["Down"]
    pair = b_up + b_dn
    if "Up" not in first or "Down" not in first: continue
    entry = first["Up"] + first["Down"]
    A = agg[g]
    A["pair"].append(pair)
    A["entry"].append(entry)
    A["from_entry"].append(D(1) - entry)
    A["from_avg"].append(entry - pair)
    for side, b in (("Up", b_up), ("Down", b_dn)):
        v = pxs[side]
        lo, hi = min(v), max(v)
        if hi > lo:
            A["pos_" + side].append((b - lo) / (hi - lo))
        A["range_" + side].append(hi - lo)

def med(v):
    v = sorted(v); return v[len(v)//2]
def show(g):
    A = agg[g]
    n = len(A["pair"])
    print("\n=== %s (n=%d two-sided windows) ===" % (g.upper(), n))
    print("  realised pair cost            median %.4f" % med(A["pair"]))
    print("  entry pair (first trade each) median %.4f" % med(A["entry"]))
    print("  DISCOUNT 1 - pair             median %+.4f" % med(A["from_entry"] and
          [D(1) - x for x in A["pair"]]))
    print("    from favourable ENTRY       median %+.4f" % med(A["from_entry"]))
    print("    from AVERAGING after entry  median %+.4f" % med(A["from_avg"]))
    te = sum(A["from_entry"]); ta = sum(A["from_avg"])
    tot = te + ta
    print("    share of total discount:    entry %5.1f%%   averaging %5.1f%%"
          % (100.0*float(te)/float(tot) if tot else 0,
             100.0*float(ta)/float(tot) if tot else 0))
    print("  basis position within the price range it traded (0=low, 1=high):")
    for s in ("Up", "Down"):
        if A["pos_"+s]:
            print("    %-5s median %.4f   (price range traded: median %.4f)"
                  % (s, med(A["pos_"+s]), med(A["range_"+s])))
    print("  -> 0.5 is neutral. These sit just BELOW it, so L5's basis is")
    print("     only marginally better than buying that side uniformly across")
    print("     the range it traded. This is NOT strong dip-timing.")
show("train"); show("test")
print("\n=== IS THE AVERAGING BUYING DIPS? ===")
print("  basis-improving trades that arrived after that side's price FELL:")
print("    %d of %d  (%.1f%%)" % (dip_num, dip_den,
                                  100.0*dip_num/max(1, dip_den)))
