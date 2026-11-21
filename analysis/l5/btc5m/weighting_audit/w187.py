import sys, random, collections, json, io, contextlib
sys.path.insert(0, ".")
from analysis.l5.btc5m import one_sided_value as O
from analysis.l5.btc5m import adverse_selection_anatomy as AA
from analysis.l5.btc5m import entry_filter_study as E
from analysis.l5.btc5m import entry_research as R
from analysis.l5.btc5m import taker_fee_adjusted as T

def pool(pairs, n=2000, seed=7):
    pairs = [p for p in pairs if p[1] > 0]
    if not pairs: return "n/a"
    k = len(pairs); pt = sum(a for a,_ in pairs)/sum(b for _,b in pairs)
    if k < 8: return "%+.4f [few] w=%d" % (pt, k)
    rng = random.Random(seed); ms = []
    for _ in range(n):
        sa = sb = 0.0
        for _i in range(k):
            a,b = pairs[rng.randrange(k)]; sa += a; sb += b
        ms.append(sa/sb)
    ms.sort()
    return "%+.4f [%+.3f,%+.3f] w=%d" % (pt, ms[int(.025*n)], ms[int(.975*n)], k)

def pk(per, key, pred=lambda r: True):
    out = []
    for recs in per.values():
        xs = [r[key] for r in recs if pred(r) and r.get(key) is not None]
        out.append((sum(xs), len(xs)))
    return pool(out)

P = print
data = O.load(); names = list(data); h = len(names)//2
per = {m: O.candidates(data[m]) for m in names}
P("=== one_sided_value sec 2, POOLED per filled share (ALL 187)")
cheap = lambda r: r["x"] <= 0.40
allow = lambda r: r["filled"] and r["edge"] is not None and r["edge"] >= O.MIN_EDGE
refuse = lambda r: r["filled"] and (r["edge"] is None or r["edge"] < O.MIN_EDGE)
anyf = lambda r: r["filled"]
for hn, ws in (("ALL", names), ("FIRST", names[:h]), ("SECOND", names[h:])):
    sub = {m: per[m] for m in ws}
    for lab, base in (("all sides", lambda r: True), ("bid<=0.40", cheap), ("bid<=0.25", lambda r: r["x"] <= 0.25)):
        for l2, f in (("every fill", anyf), ("ALLOWS", allow), ("REFUSES", refuse)):
            pr = lambda r, b=base, f=f: f(r) and b(r)
            P("  %-6s %-10s %-10s settle %s | mark_fill %s | mark_10s %s" % (hn, lab, l2, pk(sub, "settle", pr), pk(sub, "mark_fill", pr), pk(sub, "mark_10s", pr)))
P("  sec 3 unconditional won-x pooled:")
for lab, pred in (("ALLOWS bid<=.40", lambda r: cheap(r) and r["edge"] is not None and r["edge"] >= O.MIN_EDGE),
                  ("REFUSES bid<=.40", lambda r: cheap(r) and (r["edge"] is None or r["edge"] < O.MIN_EDGE))):
    P("   %s %s" % (lab, pool([(sum(r["won"]-r["x"] for r in recs if pred(r)), sum(1 for r in recs if pred(r))) for recs in per.values()])))
# EV per candidate quote placed (unfilled = 0), the executable form
P("  EV per QUOTE PLACED (unfilled=0), settle: all %s | allows %s | refuses %s" % (
   pool([(sum(r["settle"] for r in recs if r["filled"]), len(recs)) for recs in per.values()]),
   pool([(sum(r["settle"] for r in recs if allow(r)), sum(1 for r in recs if r["edge"] is not None and r["edge"]>=O.MIN_EDGE)) for recs in per.values()]),
   pool([(sum(r["settle"] for r in recs if refuse(r)), sum(1 for r in recs if (r["edge"] is None or r["edge"]<O.MIN_EDGE))) for recs in per.values()])))
bm = [(sum((r["p"]-r["won"])**2 - (r["mid"]-r["won"])**2 for r in recs if r["p"] is not None), sum(1 for r in recs if r["p"] is not None)) for recs in per.values()]
P("  Brier diff model-market pooled over candidates: %s" % pool(bm))

P("\n=== adverse_selection_anatomy sec 1, POOLED per fill")
pf = {m: AA.fills(rows) for m, rows in data.items()}
for k in ("m0", "m10", "m30", "m60", "settle", "dp_before", "dp_during"):
    P("   %-10s %s" % (k, pk(pf, k)))

P("\n=== entry_filter_study PART A, POOLED per round")
pr_ = {m: E.rounds(data[m]) for m in names}
anypos = []
for lab, pred in E.BUCKETS:
    for hn, ws in (("FIRST", names[:h]), ("SECOND", names[h:])):
        def col(fn):
            return pool([(sum(fn(r) for r in pr_[m] if pred(r)), sum(1 for r in pr_[m] if pred(r))) for m in ws])
        net = col(lambda r: r["pair"] + r["settle"])
        P("   %-38s %-6s pair %s | lone %s | net %s | net@mark %s" % (lab, hn, col(lambda r: r["pair"]), col(lambda r: r["settle"]), net, col(lambda r: r["pair"] + r["mark"])))

P("\n=== entry_research PART 1 POOLED per share (pnl/shares) and per $")
wins = collections.defaultdict(list)
for line in open(R.BR):
    f = json.loads(line)
    if f.get("asset") != "BTC" or "-5m-" not in f.get("market_slug", ""): continue
    if f.get("side") != "BUY" or f.get("winning_outcome") in (None, ""): continue
    wins[f["market_slug"]].append(f)
ret = lambda f: (1.0 if f["outcome"] == f["winning_outcome"] else 0.0) - f["price"]
def tab(key, buckets):
    for lo, hi in buckets:
        ps, pd = [], []
        for w, rows in wins.items():
            xs = [f for f in rows if lo <= f[key] < hi]
            ps.append((sum(f["shares"]*ret(f) for f in xs), sum(f["shares"] for f in xs)))
            pd.append((sum(f["shares"]*ret(f) for f in xs), sum(f["shares"]*f["price"] for f in xs)))
        P("   %6.2f-%-6.2f per share %s | per $ %s" % (lo, hi, pool(ps), pool(pd)))
P("  by price"); tab("price", R.PX)
P("  by second"); tab("seconds_from_window_start", R.TB)
P("  fav>=0.5 / dog<0.5"); tab("price", [(0.5, 1.01), (0, 0.5)])

P("\n=== entry_research PART 2 POOLED")
with contextlib.redirect_stdout(io.StringIO()):
    recs, nm = R.part2(print)
for d in R.DEPTHS:
    for hn, ws in (("FIRST", names[:h]), ("SECOND", names[h:])):
        ev = pool([(sum(r[4] for r in recs[m] if r[0]==d and r[3]), sum(1 for r in recs[m] if r[0]==d)) for m in ws])
        rf = pool([(sum(r[4] for r in recs[m] if r[0]==d and r[3]), sum(1 for r in recs[m] if r[0]==d and r[3])) for m in ws])
        P("   depth %2d %-6s ret|fill %s | EV/quote %s" % (d, hn, rf, ev))
PB = [(0, .15), (.15, .35), (.35, .65), (.65, .85), (.85, 1.0)]
P("   depth x price EV/quote pooled (all windows)")
for d in R.DEPTHS:
    P("   %2dc " % d + " | ".join(pool([(sum(r[4] for r in recs[m] if r[0]==d and lo<=r[2]<hi and r[3]), sum(1 for r in recs[m] if r[0]==d and lo<=r[2]<hi)) for m in names], n=500) for lo, hi in PB))
P("   depth x time EV/quote pooled")
for d in R.DEPTHS:
    P("   %2dc " % d + " | ".join(pool([(sum(r[4] for r in recs[m] if r[0]==d and t0<=r[1]<t1 and r[3]), sum(1 for r in recs[m] if r[0]==d and t0<=r[1]<t1)) for m in names], n=500) for t0, t1 in R.TB))

P("\n=== taker_fee_adjusted A1 POOLED net per share")
pt = {m: T.obs(data[m]) for m in names}
for lo, hi in T.AB:
    P("   ask %.2f-%.2f FIRST %s | SECOND %s" % (lo, hi, *[pool([(sum(o[3] for o in pt[m] if lo<=o[0]<hi), sum(1 for o in pt[m] if lo<=o[0]<hi)) for m in ws]) for ws in (names[:h], names[h:])]))
P("   A2 pooled")
for ai, (lo, hi) in enumerate(T.AB):
    P("   %.2f-%.2f " % (lo, hi) + " | ".join(pool([(sum(o[3] for o in pt[m] if T.cell(o)==(ai,si)), sum(1 for o in pt[m] if T.cell(o)==(ai,si))) for m in names], n=500) for si in range(len(T.SB))))
P("   B pooled")
for dws, tws, dn in ((names[:h], names[h:], "FIRST"), (names[h:], names[:h], "SECOND")):
    st = {}
    for m in dws:
        for o in pt[m]:
            c = T.cell(o)
            if c:
                a = st.setdefault(c, [0, 0.0]); a[0] += 1; a[1] += o[3]
    chosen = {c for c, (n, s) in st.items() if n >= 30 and s > 0}
    P("   found on %s: %s" % (dn, pool([(sum(o[3] for o in pt[m] if T.cell(o) in chosen), sum(1 for o in pt[m] if T.cell(o) in chosen)) for m in tws])))
