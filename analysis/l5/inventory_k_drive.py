"""k = 1 / 2 / 3 / 5 under the bounded inventory controller, chronological OOS.

Reported net is SETTLEMENT-BASIS MODELLED P&L: matched redemption plus residual
settlement, average cost basis per side.  Rebates are shown in their own column
and are NEVER added to net -- at clip 1 the maker rebate rounds to zero at every
price, which Campaign E's venue record (actual_fees_usd $0 over 6 maker orders)
confirms rather than assumes.  No number here is a fill or realized profit.
"""
import sys
from decimal import Decimal as D
sys.path.insert(0, ".")
from analysis.l5.inventory_k_replay import load, sim

W = load()
SPLIT = int(len(W) * 0.6)
TRAIN, HOLD = W[:SPLIT], W[SPLIT:]

BASE = dict(clip=1, depth=0, band_lo=D("0.30"), band_hi=D("0.60"),
            comp_cap=D("0.96"), max_subs=16, max_replace=6, allow_taker=False)

def agg(ws, **kw):
    rs = [sim(w, **kw) for w in ws]
    run = D(0); trough = D(0)
    for x in rs:
        run += x["net"]; trough = min(trough, run)
    tot = lambda f: sum(x[f] for x in rs)
    ref = {}
    for x in rs:
        for a, b in x["ref"].items(): ref[a] = ref.get(a, 0) + b
    paired = [x for x in rs if x["matched"] > 0]
    return dict(n=len(ws), net=tot("net"), m=tot("m_pnl"), r=tot("r_pnl"),
                matched=tot("matched"), contracts=tot("contracts"),
                fills=tot("fills"), subs=tot("subs"), repl=tot("repl"),
                reb=tot("rebate"), rq=tot("r_qty"), dd=trough,
                peak=max([x["peak"] for x in rs] or [D(0)]),
                wpair=len(paired), ref=ref,
                avgpair=(sum(x["pair_cost"] for x in paired) / len(paired)
                         if paired else None))

H = "%-26s %3s %4s %5s %5s %5s %8s %8s %9s %8s %7s"
print("usable windows=%d  TRAIN=%d  HOLDOUT=%d" % (len(W), len(TRAIN), len(HOLD)))
print("settlement-basis modelled P&L; rebate column is NOT in net\n")
for rq in ("stable", "chase"):
    print("=" * 112)
    print("REQUOTE = %s   (stable = place once and leave it;  chase = follow the touch)" % rq.upper())
    print(H % ("arm", "n", "win+", "mtch", "ctr", "resid", "matched$", "resid$",
               "NET$", "rebate$", "maxDD$"))
    for k in (1, 2, 3, 5):
        for lab, ws in (("ALL", W), (" train", TRAIN), (" HOLDOUT", HOLD)):
            g = agg(ws, k=k, requote=rq, **BASE)
            print(H % ("k=%d %s" % (k, lab), g["n"], g["wpair"], g["matched"],
                       g["contracts"], g["rq"],
                       "%+.4f" % g["m"], "%+.4f" % g["r"], "%+.4f" % g["net"],
                       "%.4f" % g["reb"], "%+.3f" % g["dd"]))
        g = agg(W, k=k, requote=rq, **BASE)
        ap = ("%.4f" % g["avgpair"]) if g["avgpair"] is not None else "n/a"
        print("      -> fills=%d subs=%d repl=%d avg_pair_cost=%s  refusals=%s"
              % (g["fills"], g["subs"], g["repl"], ap,
                 {a: b for a, b in sorted(g["ref"].items()) if b}))
    print()
