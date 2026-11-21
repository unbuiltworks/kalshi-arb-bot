import sys
from decimal import Decimal as D
sys.path.insert(0, ".")
from analysis.l5.print_replay import load, sim

W = load()
EARLY = [w for w in W if w["day"] < "2026-08-01"]
AUG   = [w for w in W if w["day"] >= "2026-08-01"]
SP = int(len(W) * 0.6); TR, HO = W[:SP], W[SP:]
B = dict(k=1, clip=1, depth=0, comp_cap=D("0.96"), max_subs=16, replenish=True)

def agg(ws, **kw):
    rs = [sim(w, **kw) for w in ws]
    t = lambda f: sum(x[f] for x in rs)
    run = D(0); tr = D(0)
    for x in rs: run += x["net"]; tr = min(tr, run)
    pd = [x for x in rs if x["matched"] > 0]
    ctr = t("contracts")
    traded = [x for x in rs if x["contracts"] > 0]
    return dict(n=len(ws), traded=len(traded), net=t("net"), m=t("m_pnl"),
                r=t("r_pnl"), mtch=t("matched"), ctr=ctr, rq=t("r_qty"),
                reb=t("rebate"), dd=tr, cap=t("capital"),
                wp=len(pd), fills=t("fills"), subs=t("subs"),
                strand=(100*float(t("r_qty"))/float(ctr) if ctr else 0),
                ap=(sum(x["pair_cost"] for x in pd)/len(pd)) if pd else None,
                wins=sum(1 for x in traded if x["net"] > 0),
                loss=sum(1 for x in traded if x["net"] < 0))

BANDS = [("0.30-0.60 (live)", D("0.30"), D("0.60")),
         ("0.40-0.60", D("0.40"), D("0.60")),
         ("0.45-0.60", D("0.45"), D("0.60"))]
SETS = [("ALL  522w", W), ("EARLY Mar-Apr", EARLY), ("AUG", AUG),
        ("train 60%", TR), ("HOLDOUT 40%", HO)]
H = "%-30s %4s %4s %4s %5s %6s %6s %9s %9s %10s %7s"
print("522 windows / 20 days / 63,631 prints. clip 1, k=1, ceiling 0.96, maker only.")
print("Net is settlement-basis modelled P&L. Rebates excluded from net.\n")
for mode_nm, simul in (("SEQUENTIAL (today's engine)", False),
                       ("SIMULTANEOUS two-sided", True)):
    print("=" * 122); print(mode_nm)
    print(H % ("arm", "n", "trd", "w+", "w-", "mtch", "strand", "matched$",
               "resid$", "NET$", "avgpair"))
    for bn, lo, hi in BANDS:
        for sn, ws in SETS:
            g = agg(ws, band_lo=lo, band_hi=hi, simultaneous=simul, **B)
            ap = ("%.4f" % g["ap"]) if g["ap"] is not None else "n/a"
            print(H % ("%s | %s" % (bn, sn), g["n"], g["traded"], g["wins"],
                       g["loss"], g["mtch"], "%.0f%%" % g["strand"],
                       "%+.2f" % g["m"], "%+.2f" % g["r"], "%+.2f" % g["net"], ap))
        print()
