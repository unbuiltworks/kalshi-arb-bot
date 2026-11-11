import sys
from decimal import Decimal as D
sys.path.insert(0, ".")
from analysis.l5.divergence_audit import load, audit, CFG, KIND

W = load()
EARLY = [w for w in W if w["day"] < "2026-08-01"]
AUG = [w for w in W if w["day"] >= "2026-08-01"]
ORDER = ["ATTEMPTED", "SCHEDULING_ONE_ORDER_SLOT", "ROUND_ALLOCATIONS_SPENT",
         "PAST_ENTRY_CUTOFF", "PAST_COMPLETE_CUTOFF", "OPENING_BAND",
         "PAIR_COST_ABOVE_CEILING", "PAIR_ABOVE_REDEMPTION",
         "RESIDUAL_ABOVE_CAP"]

def show(name, ws):
    t, clip = audit(ws)
    tot = sum(g["n"] for g in t.values())
    print("=" * 108)
    print("%s -- %d windows, %d L5 actions" % (name, len(ws), tot))
    print("%-28s %7s %6s %12s %12s %-9s %-16s"
          % ("first gate to refuse", "actions", "%", "L5 qty", "L5 act P&L",
             "kind", "configurable?"))
    for g in ORDER:
        d = t.get(g)
        if not d or d["n"] == 0: continue
        kind, reach = KIND[g]
        print("%-28s %7d %5.1f%% %12.0f %+12.2f %-9s %-16s"
              % (g, d["n"], 100.0 * d["n"] / tot, d["qty"], d["pnl"],
                 kind, reach))
    print("%-28s %7d %5s  %12.0f %+12.2f %-9s %-16s"
          % ("CLIP_TRUNCATED (size forgone)", clip["n"], "-", clip["qty"],
             clip["pnl"], "CAPITAL", "HARD>3"))
    att = t["ATTEMPTED"]
    print("  -> our engine attempts %d of %d actions (%.1f%%), taking %.0f of "
          "%.0f contracts (%.2f%%)"
          % (att["n"], tot, 100.0 * att["n"] / tot, att["qty"],
             att["qty"] + clip["qty"] + sum(t[g]["qty"] for g in ORDER
                                            if g != "ATTEMPTED"),
             100.0 * float(att["qty"]) / float(
                 att["qty"] + clip["qty"] + sum(t[g]["qty"] for g in ORDER
                                                if g != "ATTEMPTED"))))
    print()

for nm, ws in (("ALL BTC15m", W), ("EARLY Mar-Apr (profitable era)", EARLY),
               ("AUGUST (loss-making era)", AUG)):
    show(nm, ws)

print("=" * 108)
print("THE SINGLE-ORDER SLOT, priced separately (EARLY era, assumed hold time)")
print("%-14s %9s %9s %12s" % ("slot hold", "attempts", "blocked", "blocked P&L"))
for h in (D(0), D(5), D(30), D(120)):
    t, _ = audit(EARLY, slot_hold_s=h)
    print("%-14s %9d %9d %+12.2f"
          % ("%ss" % h, t["ATTEMPTED"]["n"],
             t["SCHEDULING_ONE_ORDER_SLOT"]["n"],
             t["SCHEDULING_ONE_ORDER_SLOT"]["pnl"]))
