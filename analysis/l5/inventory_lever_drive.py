"""Which lever flips the sign?  The k sweep shows matched economics positive in
every arm and residual settlement losses swamping them.  So sweep the levers
that act on the RESIDUAL, not on inventory capacity.

`taker` arms model crossing the spread to flatten.  Taker crossing is NOT live
authorized; these arms exist to price what that restriction costs, nothing more.
"""
import sys
from decimal import Decimal as D
sys.path.insert(0, ".")
from analysis.l5.inventory_k_replay import load, sim

W = load(); SPLIT = int(len(W) * 0.6)
TRAIN, HOLD = W[:SPLIT], W[SPLIT:]
BASE = dict(clip=1, depth=0, band_lo=D("0.30"), band_hi=D("0.60"),
            comp_cap=D("0.96"), max_subs=16, max_replace=6,
            allow_taker=False, requote="stable")

def agg(ws, **kw):
    rs = [sim(w, **kw) for w in ws]
    run = D(0); tr = D(0)
    for x in rs: run += x["net"]; tr = min(tr, run)
    t = lambda f: sum(x[f] for x in rs)
    pd = [x for x in rs if x["matched"] > 0]
    return dict(n=len(ws), net=t("net"), m=t("m_pnl"), r=t("r_pnl"),
                mtch=t("matched"), ctr=t("contracts"), rq=t("r_qty"),
                dd=tr, wp=len(pd),
                ap=(sum(x["pair_cost"] for x in pd)/len(pd)) if pd else None)

ARMS = [
 ("A  live-equivalent k=1",        dict(BASE, k=1)),
 ("B  depth1 (behind touch)",      dict(BASE, k=1, depth=1)),
 ("C  depth2",                     dict(BASE, k=1, depth=2)),
 ("D  band .40-.60 (favoured)",    dict(BASE, k=1, band_lo=D("0.40"))),
 ("E  band .30-.45 (cheap only)",  dict(BASE, k=1, band_hi=D("0.45"))),
 ("F  taker flatten cap .99",      dict(BASE, k=1, allow_taker=True, comp_cap=D("0.99"))),
 ("G  taker flatten cap 1.00",     dict(BASE, k=1, allow_taker=True, comp_cap=D("1.00"))),
 ("H  taker cap1.00 k=2",          dict(BASE, k=2, allow_taker=True, comp_cap=D("1.00"))),
 ("I  taker cap1.00 k=3",          dict(BASE, k=3, allow_taker=True, comp_cap=D("1.00"))),
 ("J  taker cap1.00 depth1 k=1",   dict(BASE, k=1, depth=1, allow_taker=True, comp_cap=D("1.00"))),
 ("K  taker cap1.00 depth2 k=1",   dict(BASE, k=1, depth=2, allow_taker=True, comp_cap=D("1.00"))),
 ("L  taker cap1.00 depth2 k=2",   dict(BASE, k=2, depth=2, allow_taker=True, comp_cap=D("1.00"))),
]
H = "%-30s %4s %5s %5s %5s %9s %9s %9s %7s %7s"
print("usable=%d TRAIN=%d HOLDOUT=%d   settlement-basis modelled P&L, rebates excluded\n" % (len(W), len(TRAIN), len(HOLD)))
print(H % ("arm", "win+", "mtch", "ctr", "resid", "matched$", "resid$", "NET$", "maxDD", "avgpair"))
for nm, kw in ARMS:
    for lab, ws in (("", W), ("   HOLDOUT", HOLD)):
        g = agg(ws, **kw)
        ap = ("%.4f" % g["ap"]) if g["ap"] is not None else "n/a"
        print(H % (nm + lab, g["wp"], g["mtch"], g["ctr"], g["rq"],
                   "%+.4f" % g["m"], "%+.4f" % g["r"], "%+.4f" % g["net"],
                   "%+.2f" % g["dd"], ap))
