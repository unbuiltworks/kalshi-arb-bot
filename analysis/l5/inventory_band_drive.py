"""The opening band, not inventory capacity, drives the strand rate.

Arm D (0.40-0.60) was the only positive-holdout arm in the lever sweep.  This
sweeps around it.  NOTE: every band here is a NARROWING of the currently
authorized 0.30-0.60 opening band.  Nothing here widens it.
"""
import sys
from decimal import Decimal as D
sys.path.insert(0, ".")
from analysis.l5.inventory_k_replay import load, sim

W = load(); SPLIT = int(len(W) * 0.6)
TRAIN, HOLD = W[:SPLIT], W[SPLIT:]
B = dict(clip=1, depth=0, comp_cap=D("0.96"), max_subs=16, max_replace=6,
         allow_taker=False, requote="stable")

def one(ws, **kw):
    rs = [sim(w, **kw) for w in ws]
    run = D(0); tr = D(0)
    for x in rs: run += x["net"]; tr = min(tr, run)
    t = lambda f: sum(x[f] for x in rs)
    pd = [x for x in rs if x["matched"] > 0]
    ctr = t("contracts")
    return dict(n=len(ws), net=t("net"), m=t("m_pnl"), r=t("r_pnl"),
                mtch=t("matched"), ctr=ctr, rq=t("r_qty"), dd=tr, wp=len(pd),
                strand=(float(t("r_qty")) / float(ctr) if ctr else 0.0),
                ap=(sum(x["pair_cost"] for x in pd)/len(pd)) if pd else None,
                wins=sum(1 for x in rs if x["net"] > 0),
                loss=sum(1 for x in rs if x["net"] < 0))
BANDS = [("0.30-0.60", D("0.30"), D("0.60")), ("0.35-0.60", D("0.35"), D("0.60")),
         ("0.40-0.60", D("0.40"), D("0.60")), ("0.40-0.55", D("0.40"), D("0.55")),
         ("0.45-0.60", D("0.45"), D("0.60")), ("0.45-0.55", D("0.45"), D("0.55")),
         ("0.40-0.50", D("0.40"), D("0.50"))]
H = "%-22s %4s %4s %4s %5s %5s %6s %9s %9s %9s %7s"
print("usable=%d TRAIN=%d HOLDOUT=%d  settlement-basis modelled, rebates excluded" % (len(W), len(TRAIN), len(HOLD)))
for k in (1, 2, 3):
    print("\n" + "=" * 110 + "\nk=%d" % k)
    print(H % ("band", "n", "w+", "w-", "mtch", "ctr", "strand", "matched$", "resid$", "NET$", "avgpair"))
    for nm, lo, hi in BANDS:
        for lab, ws in (("", W), ("  HOLD", HOLD)):
            g = one(ws, k=k, band_lo=lo, band_hi=hi, **B)
            ap = ("%.4f" % g["ap"]) if g["ap"] is not None else "n/a"
            print(H % (nm + lab, g["n"], g["wins"], g["loss"], g["mtch"], g["ctr"],
                       "%.0f%%" % (100 * g["strand"]), "%+.4f" % g["m"],
                       "%+.4f" % g["r"], "%+.4f" % g["net"], ap))
