"""Counterfactual net P&L for each relaxation, with OUR fill process.

The divergence audit says what L5 EARNED on the decisions we refuse. This says
what WE would earn taking them, which is a different number because our fills
are adversely selected (81% of our residual lands on the losing side against
L5's 57%). Both are reported; the gap between them IS the adverse selection.
"""
import sys
from decimal import Decimal as D
sys.path.insert(0, ".")
from analysis.l5.print_replay import load, sim

W = load()
EARLY = [w for w in W if w["day"] < "2026-08-01"]
AUG = [w for w in W if w["day"] >= "2026-08-01"]
SP = int(len(W) * 0.6); TR, HO = W[:SP], W[SP:]

BASE = dict(k=1, clip=1, depth=0, band_lo=D("0.30"), band_hi=D("0.60"),
            comp_cap=D("0.96"), max_subs=16, replenish=True,
            simultaneous=False, entry_cut=D(480), complete_cut=D(600),
            rounds=4, slots=1)

ARMS = [
 ("A today (480/600, 1 slot, 4 rounds)", dict(BASE)),
 ("B +2nd slot (authorized already)",    dict(BASE, slots=2)),
 ("C B +entry 600 (code ceiling)",       dict(BASE, slots=2, entry_cut=D(600))),
 ("D C +complete 720 (code ceiling)",    dict(BASE, slots=2, entry_cut=D(600),
                                              complete_cut=D(720))),
 ("E D +rounds 12 (fidelity)",           dict(BASE, slots=2, entry_cut=D(600),
                                              complete_cut=D(720), rounds=12)),
 ("F E +cutoffs 840/870 (fidelity)",     dict(BASE, slots=2, entry_cut=D(840),
                                              complete_cut=D(870), rounds=12)),
 ("G F +clip 3 (code ceiling)",          dict(BASE, slots=2, entry_cut=D(840),
                                              complete_cut=D(870), rounds=12,
                                              clip=3, k=3)),
]

def agg(ws, **kw):
    rs = [sim(w, **kw) for w in ws]
    t = lambda f: sum(x[f] for x in rs)
    pd = [x for x in rs if x["matched"] > 0]
    ctr = t("contracts"); rq = t("r_qty")
    lose = sum(1 for x in rs if x["r_qty"] > 0 and x["r_pnl"] < 0)
    nres = sum(1 for x in rs if x["r_qty"] > 0)
    npairs = [int(x["matched"]) for x in rs]
    return dict(n=len(ws), net=t("net"), m=t("m_pnl"), r=t("r_pnl"),
                mtch=t("matched"), ctr=ctr, rq=rq, fills=t("fills"),
                ratio=(float(t("matched"))/float(rq) if rq else 0),
                strand=(100*float(rq)/float(ctr) if ctr else 0),
                lose=(100*lose/nres if nres else 0),
                ge2=sum(1 for p in npairs if p >= 2),
                ap=(sum(x["pair_cost"] for x in pd)/len(pd)) if pd else None)

H = "%-36s %6s %6s %6s %6s %5s %6s %9s %9s %10s"
print("OUR fill process, 522 windows. Settlement-basis modelled, rebates excluded.\n")
for sn, ws in (("EARLY Mar-Apr era", EARLY), ("ALL 522w", W), ("HOLDOUT 40%", HO)):
    print("=" * 122); print(sn)
    print(H % ("arm", "mtch", "ctr", "resid", "m:r", "w>=2", "lose%",
               "matched$", "resid$", "NET$"))
    for nm, kw in ARMS:
        g = agg(ws, **kw)
        print(H % (nm, g["mtch"], g["ctr"], g["rq"], "%.2f" % g["ratio"],
                   g["ge2"], "%.0f%%" % g["lose"], "%+.2f" % g["m"],
                   "%+.2f" % g["r"], "%+.2f" % g["net"]))
    print()
