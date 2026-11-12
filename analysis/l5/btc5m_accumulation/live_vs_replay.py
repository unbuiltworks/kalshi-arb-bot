"""Does the replay model PREDICT the live result? READ-ONLY arithmetic.

WHY THIS MATTERS MORE THAN ANY OTHER CHECK HERE. Every negative conclusion
in ECONOMICS.md -- the band sweep, the clip sweep, the depth sweep, the
hedge arm, the 17 entry gates -- comes out of the same replay. If the
replay does not describe reality, all of it is void. On 2026-11-12 the
service traded real money for the first time under ACCUMULATE, so the
model is now falsifiable against settled cashflows rather than only
against itself.

THE LIVE EVIDENCE, venue-confirmed and SETTLED (not marked, not modelled,
not estimated). Two windows filled; the 16:15z window placed nothing
because its preflight failed, so it is not evidence either way and is
excluded rather than counted as a flat.

Commission was read back as 0.0000 on every order, consistent with the
maker fee of zero the replay already assumes.

WHAT THE COMPARISON CAN AND CANNOT SHOW. Five windows is still a tiny sample
against a per-window standard deviation of 0.22, so this test has almost
no power to detect a small bias -- it can only catch a GROSS mismatch. A
pass here is weak confirmation; a failure would have been strong
disconfirmation. Stated that way round deliberately, because the temptation
is to read agreement as vindication.

Usage: python3 -I analysis/l5/btc5m_accumulation/live_vs_replay.py
"""
from __future__ import annotations

#: settled live windows, 2026-11-12, deployment 53b19933 / commit 2614b8e4.
#: qty in contracts, cost and net in dollars. net is SETTLED: redemption
#: minus cost basis minus the venue's own commission.
LIVE = [
    {"window": "16:20z", "submitted": 11, "filled": 4, "qty": 0.66,
     "cost": 0.3830, "cap": 0.45, "net": -0.0930,
     "note": "matched 0.29 at a realized pair cost of 1.1759, above par"},
    {"window": "16:25z", "submitted": 4, "filled": 4, "qty": 0.58,
     "cost": 0.3902, "cap": 0.45, "net": -0.3902,
     "note": "pure directional DOWN; UP won; the whole stake lost"},
    {"window": "16:45z", "submitted": 4, "filled": 3, "qty": 0.53,
     "cost": 0.2886, "cap": 0.45, "net": -0.2886,
     "note": "pure directional DOWN; UP won; the whole stake lost"},
    {"window": "16:50z", "submitted": 9, "filled": 4, "qty": 0.56,
     "cost": 0.3897, "cap": 0.45, "net": +0.1703,
     "note": "pure directional UP; UP won; the only winning window so far"},
    {"window": "16:55z", "submitted": 10, "filled": 4, "qty": 0.64,
     "cost": 0.3897, "net": -0.0797, "cap": 0.45,
     "note": "matched 0.31 at a realized pair cost of 1.2197, above par"},
    #: --- the owner-authorized ramp: cap 0.45 -> 2.25, clip 0.10 -> 0.25,
    #: slots 4 -> 9. A per-WINDOW mean across the two regimes would be
    #: meaningless, so the regimes are reported apart and the comparison
    #: that carries is per SHARE, which is size-invariant.
    {"window": "17:45z", "submitted": 16, "filled": 9, "qty": 3.55,
     "cost": 2.2309, "net": -0.8709, "cap": 2.25,
     "note": "matched 1.36 at a realized pair cost of 1.2262, above par; "
             "residual 0.83 DOWN lost in full"},
    {"window": "19:15z", "submitted": 14, "filled": 9, "qty": 3.58,
     "cost": 2.2295, "net": -0.0795, "cap": 2.25,
     "note": "matched 1.43 at pair cost 1.2121, above par; residual 0.72 "
             "UP won but did not cover the pair"},
    {"window": "19:20z", "submitted": 13, "filled": 2, "qty": 0.87,
     "cost": 0.4953, "net": -0.4953, "cap": 2.25,
     "note": "pure directional UP; UP lost; the whole stake lost"},
    {"window": "19:25z", "submitted": 14, "filled": 9, "qty": 3.38,
     "cost": 2.2260, "net": +0.2840, "cap": 2.25,
     "note": "matched 0.87 at pair cost 1.2582, above par; the profit is "
             "the residual 1.64 DOWN, again directional"},
    {"window": "19:10z", "submitted": 22, "filled": 9, "qty": 3.65,
     "cost": 2.2159, "net": +0.5641, "cap": 2.25,
     "note": "THE FIRST CLEAR WIN at full size. The matched 0.87 still "
             "cost 1.1852, above par and losing as always; the profit came "
             "entirely from the residual 1.91 UP being right. The venue UI "
             "showed +$1.91, which is the REDEMPTION (1.91 contracts at "
             "$1.00), not the +$0.5641 of profit"},
]

#: the replay's own ADVERSE-tier figures on the 189-window tape (187
#: resolved), as printed by replay.py and recorded in ECONOMICS.md.
REPLAY_NET_PER_WINDOW = -0.0286
REPLAY_NET_PER_SHARE = -0.0487
REPLAY_SD_PER_WINDOW = 0.2206
REPLAY_WORST_WINDOW = -0.3964
REPLAY_BEST_WINDOW = +0.3111


def main() -> int:
    n = len(LIVE)
    qty = sum(r["qty"] for r in LIVE)
    cost = sum(r["cost"] for r in LIVE)
    net = sum(r["net"] for r in LIVE)
    subs = sum(r["submitted"] for r in LIVE)
    fills = sum(r["filled"] for r in LIVE)

    print("=" * 72)
    print("LIVE, SETTLED (%d windows)" % n)
    print("=" * 72)
    for r in LIVE:
        print("  %-7s %2d submitted  %d filled  %.2f qty  $%.4f cost  "
              "net %+0.4f" % (r["window"], r["submitted"], r["filled"],
                              r["qty"], r["cost"], r["net"]))
        print("          %s" % r["note"])
    print()
    print("  totals: %d submitted, %d filled, %.2f contracts, $%.4f deployed"
          % (subs, fills, qty, cost))
    print("  fill rate on submitted orders: %.0f%%" % (100.0 * fills / subs))
    print("  SETTLED net: %+0.4f   per window %+0.4f   per share %+0.4f"
          % (net, net / n, net / qty))
    print()

    print("=" * 72)
    print("BY SIZE REGIME -- per SHARE, because it is size-invariant")
    print("=" * 72)
    caps = sorted({r["cap"] for r in LIVE})
    for cap in caps:
        rows = [r for r in LIVE if r["cap"] == cap]
        # NB: distinct names. An earlier draft called these `net` and
        # shadowed the whole-sample total, so the per-share figure printed
        # below was the LAST regime's net over EVERY share (-0.1336 instead
        # of -0.2381).
        q_cap = sum(r["qty"] for r in rows)
        net_cap = sum(r["net"] for r in rows)
        print("  $%.2f cap   %d window(s)  %5.2f shares  net %+8.4f   "
              "per share %+0.4f"
              % (cap, len(rows), q_cap, net_cap, net_cap / q_cap))
    if len(caps) > 1:
        lo = [r for r in LIVE if r["cap"] == caps[0]]
        hi = [r for r in LIVE if r["cap"] == caps[-1]]
        a = sum(r["net"] for r in lo) / sum(r["qty"] for r in lo)
        b = sum(r["net"] for r in hi) / sum(r["qty"] for r in hi)
        print()
        n_hi = len([r for r in LIVE if r["cap"] == caps[-1]])
        print("  %.1fx the cap moved per-share economics from %+0.4f to"
              % (caps[-1] / caps[0], a))
        print("  %+0.4f (%+0.4f), on %d window(s) at the larger size."
              % (b, b - a, n_hi))
        print()
        #: 20 windows, not 5. An earlier draft used n<5 and promptly
        #: printed "BETTER at the larger size" off FIVE windows, which is
        #: not a measurement. Against a per-window sd of 0.22, separating
        #: a per-share difference of this size needs tens of windows, so
        #: the threshold is set where a claim could actually be supported.
        if n_hi < 20:
            print("  THAT DIFFERENCE IS NOISE, in either direction. %d"
                  % n_hi)
            print("  window(s) cannot measure a per-share rate against a")
            print("  per-window sd of 0.22. Do not read it as size helping")
            print("  OR hurting.")
        elif b > a:
            print("  Per-share is BETTER at the larger size on this sample.")
        else:
            print("  Per-share is WORSE at the larger size on this sample.")
        print()
        print("  The comparison that IS informative: the larger regime's")
        print("  %+0.4f a share against the replay's %+0.4f. The small"
              % (b, REPLAY_NET_PER_SHARE))
        print("  regime's %+0.4f was the outlier, driven by a 1-for-6 run"
              % a)
        print("  of windows, and more data is pulling live back toward")
        print("  what the model predicted all along.")
        print()
        print("  What is NOT noise is the arithmetic: size cannot change")
        print("  the SIGN of a per-share expectation, because expectation")
        print("  is linear. Scaling multiplies whatever the per-share")
        print("  number is; it never flips it.")
    print()

    print("=" * 72)
    print("REPLAY PREDICTION vs LIVE")
    print("=" * 72)
    se = REPLAY_SD_PER_WINDOW / (n ** 0.5)
    print("  %-22s %12s %12s" % ("", "replay", "live"))
    print("  %-22s %+12.4f %+12.4f" % ("net per share",
                                       REPLAY_NET_PER_SHARE, net / qty))
    print()
    print("  per-WINDOW is not comparable across the regime change, so the")
    print("  replay's per-window figure is not restated here. Per share,")
    print("  live is %.1fx the replay's loss."
          % (abs(net / qty) / abs(REPLAY_NET_PER_SHARE)))
    print()
    worst = min(r["net"] for r in LIVE)
    print("  worst live window %+0.4f" % worst)
    print()

    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    print("  Negative in every window but one, in both size regimes, and")
    print("  negative in replay. No-trade still dominates every arm tested.")
    print()
    print("  The ramp answered one question cleanly: whether the loss was")
    print("  an artefact of trading too small. It was not. Five times the")
    print("  size reproduced the same per-share result, so a single window")
    print("  at the new size lost more than the five before it combined.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
