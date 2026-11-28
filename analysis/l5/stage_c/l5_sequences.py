"""Representative winning and losing windows, trade by trade.

For each fill: pre/post UP and DOWN quantity, weighted-average basis per
side, matched quantity, residual exposure, and the MARGINAL effect of that
trade on the window's eventual economics -- the change in
`matched*(1-pair) + residual*(outcome - basis)` evaluated at the window's
actual settled outcome.
"""
from __future__ import annotations
import collections, gzip, json, os, sys
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

W = {w["slug"]: w for w in
     (json.loads(l) for l in open("/tmp/claude-0/l5_windows.jsonl"))}
TE = [W[s] for s in json.load(open("/tmp/claude-0/test.json")) if s in W]
TE = [w for w in TE if w.get("pair_cost") and D(w["matched"]) > 0]
TE.sort(key=lambda w: D(w["realized_net"]))
# a representative loser and winner near the 10th/90th percentile, with a
# tractable number of trades so the sequence can be read
cand = [w for w in TE if 20 <= w["n_trades"] <= 45]
loser = cand[len(cand)//10]
winner = cand[len(cand)*9//10]
want = {loser["slug"], winner["slug"]}

seen = set(); T = collections.defaultdict(list)
for p in SRC:
    if not os.path.exists(p): continue
    for r in _rows(p):
        k = (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
             r.get("size"), r.get("side"), r.get("type"))
        if k in seen: continue
        seen.add(k)
        s = str(r.get("slug") or "")
        if r.get("type") != "TRADE" or s not in want: continue
        ws = int(s.rsplit("-", 1)[1])
        T[s].append({"t": int(r["timestamp"]) - ws,
                     "oc": str(r.get("outcome") or ""),
                     "q": D(str(r.get("size") or 0)),
                     "px": D(str(r.get("price") or 0))})

def value(qu, cu, qd, cd, win):
    """Window value at the ACTUAL outcome: matched redeems 1, residual pays
    1 only on the winning side."""
    m = min(qu, qd)
    bu = (cu / qu) if qu else D(0)
    bd = (cd / qd) if qd else D(0)
    v = m * (D(1) - (bu + bd))
    r = qu - qd
    if r > 0:
        v += r * ((D(1) if win == "Up" else D(0)) - bu)
    elif r < 0:
        v += (-r) * ((D(1) if win == "Down" else D(0)) - bd)
    return v

for tag, w in (("LOSING", loser), ("WINNING", winner)):
    acts = sorted(T.get(w["slug"]) or [], key=lambda a: a["t"])
    if not acts: continue
    print("\n" + "="*104)
    print("%s WINDOW  %s   winner=%s  trades=%d  realized_net=%s  pair=%s"
          % (tag, w["slug"], w["winner"], w["n_trades"],
             w["realized_net"][:9], w["pair_cost"][:6]))
    print("="*104)
    print("%5s %5s %8s %8s %9s %9s %8s %9s %10s %10s" % (
        "t_s", "side", "qty", "price", "up_qty", "dn_qty", "matched",
        "residual", "pair_cost", "marg_val"))
    qu = cu = qd = cd = D(0)
    prev = D(0)
    for a in acts:
        oc = a["oc"]
        if oc == "Up": qu += a["q"]; cu += a["q"]*a["px"]
        elif oc == "Down": qd += a["q"]; cd += a["q"]*a["px"]
        else: continue
        v = value(qu, cu, qd, cd, w["winner"])
        bu = (cu/qu) if qu else D(0); bd = (cd/qd) if qd else D(0)
        print("%5d %5s %8.2f %8.4f %9.2f %9.2f %8.2f %9.2f %10.4f %+10.4f" % (
            a["t"], oc, a["q"], a["px"], qu, qd, min(qu, qd), abs(qu-qd),
            (bu+bd) if (qu and qd) else 0, v - prev))
        prev = v
    print("  marg_val = change in the window's value AT THE ACTUAL OUTCOME.")
    print("  A trade that reduces imbalance converts residual into matched;")
    print("  one that extends it adds a leg that must still be paired.")
