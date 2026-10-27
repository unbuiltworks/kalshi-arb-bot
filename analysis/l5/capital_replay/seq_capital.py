"""Sequential multi-round capital replay, driven by the PRODUCTION gate.

Uses ValidationGates.refuse / cash_committed / pending_obligation and the
real sizing identity from l5_two_sided_probe.build_orders. Nothing here
models the gate; it calls it.
"""
import sys
from decimal import Decimal as D
sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src import l5_live_validation_gates as VG

ENV    = D("12.00")
ALLOCS = 4                       # round_allocations, deployed
OPEN_CAP = D("3")                # OPENING_LEG_MAX_CONTRACTS

def run(up_px, label, allocs=ALLOCS, env=ENV, cap=OPEN_CAP, max_rounds=10):
    g = VG.ValidationGates(max_cash_deployed_usd=env)
    dn_px = D("0.95") - up_px                       # pair target 0.95
    oblig = D(1) + up_px                            # gate's own cash basis
    per_round = env / allocs                        # sizing is STATIC
    filled = []                                     # completed pairs
    rows, total_matched = [], D(0)
    print("\n=== %s (up_px=%s dn_px=%s pair=0.95, oblig/contract=%s) ==="
          % (label, up_px, dn_px, oblig))
    print(" rnd  clip  committed  outstanding  this_round  total  verdict")
    for r in range(1, max_rounds + 1):
        # the real sizing identity, which never shrinks with use
        by_cash = per_round / oblig
        clip = min(by_cash, cap)
        # the resting leg is the DOWN (BUY_SHORT): worst case $1/share
        outstanding = [{"qty": clip, "price": dn_px, "intent": "BUY_SHORT"}]
        committed_now = g.cash_committed(filled)
        pending = g.pending_obligation(outstanding)
        # the proposed order is the UP leg at its limit price
        this_order = clip * up_px
        total = committed_now + pending + this_order
        ok = total <= env
        print(" %3d  %4.2f   %8.4f   %9.4f   %9.4f  %6.2f  %s"
              % (r, clip, committed_now, pending, this_order, total,
                 "PLACE" if ok else "REFUSED CASH_COMMITMENT"))
        rows.append((r, clip, total, ok))
        if not ok:
            break
        # the round completes: both legs filled, charged at realised cost
        filled.append({"cash_out": clip * D("0.95"), "settled": False,
                       "closing": False})
        total_matched += clip
    print("  -> rounds placed %d, matched contracts %.2f, $/window $%.4f"
          % (sum(1 for _,_,_,ok in rows if ok), total_matched,
             total_matched * D("0.07692")))
    return total_matched

print("MY EARLIER CLAIM: envelope/obligation = %.2f contracts, invariant" % (ENV/D("1.5")))
best = D(0)
for up, lab in ((D("0.50"), "up 0.50 (oblig 1.50, the typical case)"),
                (D("0.64"), "up 0.64 (oblig 1.64, dearest observed)"),
                (D("0.32"), "up 0.32 (oblig 1.32, cheapest observed)")):
    best = max(best, run(up, lab))
print("\n=== sensitivity to round_allocations at up 0.50 ===")
for a in (1, 2, 4, 6, 8):
    m = run(D("0.50"), "allocs=%d" % a, allocs=a)
    best = max(best, m)
print("\nBEST matched quantity found: %.2f contracts = $%.4f/window"
      % (best, best * D("0.07692")))
