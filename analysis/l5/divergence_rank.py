"""Ranked divergences, normalised to OUR size.

WHY PER CONTRACT. L5's median action is ~12.8 contracts; ours is 1. "This gate
blocks $509 of L5 P&L" is therefore not our counterfactual. The honest figure
is (blocked actions) x (our clip) x (per-contract settled value of those
actions), which is what we would have earned taking the same decisions at the
size we are actually authorized for.

FILL UNCERTAINTY. The counterfactual assumes we are filled at L5's own traded
price. That needs either strict price priority or a crossing, neither of which
is guaranteed, and our own fill latency is unmeasurable (`venue_event_t` is
null on every fill we hold). So every counterfactual is given at three fill
fractions rather than one, and the 100% column is an upper bound, not an
estimate.

A KNOWN ACCOUNTING GAP, stated rather than hidden: summing per-row
price x quantity across all 63,686 actions gives -$359.97, while the
authoritative window-level figure from cum_up_spend/cum_down_spend is
-$1,105.09. Contract counts reconcile EXACTLY (1,217,877 both ways), so the
$745.10 difference is a per-contract cost of $0.0006 present in cum_spend and
absent from the `price` column -- fee-like. It is two orders of magnitude
below the gate differences being ranked and does not change any ordering, but
it means the P&L columns here are GROSS of that component.
"""
import sys, collections
from decimal import Decimal as D
sys.path.insert(0, ".")
from analysis.l5.divergence_audit import load, audit, KIND

W = load()
EARLY = [w for w in W if w["day"] < "2026-08-01"]
CLIP = D(1)
GATES = ["PAST_ENTRY_CUTOFF", "RESIDUAL_ABOVE_CAP", "OPENING_BAND",
         "ROUND_ALLOCATIONS_SPENT", "PAST_COMPLETE_CUTOFF",
         "PAIR_COST_ABOVE_CEILING", "PAIR_ABOVE_REDEMPTION"]

def table(name, ws):
    t, clip = audit(ws)
    rows = []
    for g in GATES:
        d = t.get(g)
        if not d or d["n"] == 0 or d["qty"] == 0: continue
        per = d["pnl"] / d["qty"]                    # $/contract
        cf = D(d["n"]) * CLIP * per                  # our-size, 100% fill
        kind, reach = KIND[g]
        rows.append((cf, g, d["n"], per, kind, reach))
    rows.sort(reverse=True)
    print("=" * 112)
    print("%s -- %d windows.  Counterfactual = blocked actions x clip 1 x $/contract" % (name, len(ws)))
    print("%-26s %8s %11s %10s %10s %10s %-9s %-17s"
          % ("restriction", "blocked", "$/contract", "cf@100%", "cf@50%",
             "cf@25%", "kind", "configurable?"))
    for cf, g, n, per, kind, reach in rows:
        print("%-26s %8d %+11.4f %+10.2f %+10.2f %+10.2f %-9s %-17s"
              % (g, n, per, cf, cf / 2, cf / 4, kind, reach))
    per_c = clip["pnl"] / clip["qty"] if clip["qty"] else D(0)
    print("%-26s %8d %+11.4f %10s %10s %10s %-9s %-17s"
          % ("CLIP size forgone", clip["n"], per_c,
             "see note", "", "", "CAPITAL", "HARD>3"))
    att = t["ATTEMPTED"]
    print("\n  our engine attempts %d of %d L5 actions (%.1f%%)"
          % (att["n"], sum(x["n"] for x in t.values()),
             100.0 * att["n"] / sum(x["n"] for x in t.values())))
    print("  attempted actions earn $%+.4f/contract; L5 overall earns "
          "$%+.4f/contract"
          % (att["pnl"] / att["qty"] if att["qty"] else 0,
             sum(x["pnl"] for x in t.values()) /
             sum(x["qty"] for x in t.values())))
    print()

table("EARLY Mar-Apr (the era worth replicating)", EARLY)
table("ALL BTC15m", W)
