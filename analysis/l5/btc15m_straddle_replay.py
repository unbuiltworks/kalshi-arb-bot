#!/usr/bin/env python3
"""BTC15m STRADDLE replay on recorded PMUS books (books_380.json). REPLAY,
hypothetical fills, not realized. No lookahead: fills from rows only; the
winner is read only to settle.

Rest a bid at price P on BOTH sides from t >= START until t >= CANCEL; no
re-pricing. UP fills when yes_ask < P (through) / <= P (touch); DOWN fills
when yes_bid > 1-P / >= 1-P. BOTH -> +(1-2P); ONE -> (won? 1-P : -P); NONE -> 0.
Per window, one contract a side. Window-clustered 95% intervals, plus a
chronological first-half / second-half split.
"""
import json, math, os, statistics as st
from decimal import Decimal as D
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
B = json.load(open(os.path.join(ROOT, "data/l5/recent/wide_20261111/books_380.json")))

def sim(rows, win, P, rule, start, cancel):
    fu = fd = False
    for t, b, a in rows:
        if t < start or b is None or a is None:
            continue
        if t >= cancel:
            break
        if not fu:
            fu = (a < P) if rule == "through" else (a <= P)
        if not fd:
            fd = (b > 1 - P) if rule == "through" else (b >= 1 - P)
    if fu and fd: return float(1 - 2 * P), "BOTH"
    if fu: return float((1 if win == "UP" else 0) - P), "ONE"
    if fd: return float((1 if win == "DOWN" else 0) - P), "ONE"
    return 0.0, "NONE"

W = []
for s, w in sorted(B.items()):
    if w.get("winner") not in ("UP", "DOWN"): continue
    W.append((s, [(float(r[0]), D(r[1]) if r[1] else None, D(r[2]) if r[2] else None) for r in w["rows"]], w["winner"]))
print("REPLAY, %d BTC15m windows %s .. %s; hypothetical fills, NOT realized" % (len(W), W[0][0][-16:], W[-1][0][-16:]))
half = len(W) // 2
print("%-7s %-5s %-6s %5s %5s %5s %9s %20s %9s %9s" % ("rule","P","start","both","one","lone_win","pnl/w","95% CI","1st half","2nd half"))
for rule in ("through", "touch"):
    for P in (D("0.03"), D("0.05"), D("0.08"), D("0.10"), D("0.12"), D("0.15")):
        for start in (0.0, 120.0):
            res = [sim(r, w, P, rule, start, 870.0) for _, r, w in W]
            v = [x for x, _ in res]; c = [k for _, k in res]
            n = len(v); m = st.mean(v); se = st.pstdev(v) / math.sqrt(n)
            ones = [x for x, k in res if k == "ONE"]
            print("%-7s %-5s %-6s %5d %5d %5.1f%% %+9.4f [%+.4f, %+.4f] %+9.4f %+9.4f" % (
                rule, P, int(start), c.count("BOTH"), c.count("ONE"),
                100 * sum(1 for x in ones if x > 0) / max(1, len(ones)), m, m - 1.96 * se, m + 1.96 * se,
                st.mean(v[:half]), st.mean(v[half:])))
