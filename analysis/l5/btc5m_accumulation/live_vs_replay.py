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
     "cost": 0.3830, "net": -0.0930,
     "note": "matched 0.29 at a realized pair cost of 1.1759, above par"},
    {"window": "16:25z", "submitted": 4, "filled": 4, "qty": 0.58,
     "cost": 0.3902, "net": -0.3902,
     "note": "pure directional DOWN; UP won; the whole stake lost"},
    {"window": "16:45z", "submitted": 4, "filled": 3, "qty": 0.53,
     "cost": 0.2886, "net": -0.2886,
     "note": "pure directional DOWN; UP won; the whole stake lost"},
    {"window": "16:50z", "submitted": 9, "filled": 4, "qty": 0.56,
     "cost": 0.3897, "net": +0.1703,
     "note": "pure directional UP; UP won; the only winning window so far"},
    {"window": "16:55z", "submitted": 10, "filled": 4, "qty": 0.64,
     "cost": 0.3897, "net": -0.0797,
     "note": "matched 0.31 at a realized pair cost of 1.2197, above par"},
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
    print("REPLAY PREDICTION vs LIVE")
    print("=" * 72)
    se = REPLAY_SD_PER_WINDOW / (n ** 0.5)
    live_pw = net / n
    z = (live_pw - REPLAY_NET_PER_WINDOW) / se
    print("  %-22s %12s %12s" % ("", "replay", "live"))
    print("  %-22s %+12.4f %+12.4f" % ("net per window",
                                       REPLAY_NET_PER_WINDOW, live_pw))
    print("  %-22s %+12.4f %+12.4f" % ("net per share",
                                       REPLAY_NET_PER_SHARE, net / qty))
    print()
    print("  expected spread of a %d-window mean, at the replay's own" % n)
    print("  per-window sd of %.4f:  se = %.4f" % (REPLAY_SD_PER_WINDOW, se))
    print("  live mean is %+0.2f se from the replay's mean" % z)
    print()
    worst = min(r["net"] for r in LIVE)
    print("  worst live window %+0.4f, against a replay range of"
          % worst)
    print("  %+0.4f to %+0.4f -- %s" % (
        REPLAY_WORST_WINDOW, REPLAY_BEST_WINDOW,
        "inside it" if REPLAY_WORST_WINDOW <= worst <= REPLAY_BEST_WINDOW
        else "OUTSIDE it, which the model does not explain"))
    print()

    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    if abs(z) < 2.0:
        print("  NOT REJECTED. The live result sits %+0.2f se from what the"
              % z)
        print("  replay predicts, which at n=%d is ordinary noise. The model"
              % n)
        print("  survives its first contact with settled money.")
        print()
        print("  Read this as the WEAK claim it is: %d windows cannot" % n)
        print("  confirm a model, they can only fail to break it. What they")
        print("  do rule out is the hopeful reading -- that live fills would")
        print("  behave better than the replay's ADVERSE tier. They did not;")
        print("  live came in BELOW the prediction, not above.")
    else:
        print("  REJECTED at %+0.2f se. The replay does not describe what" % z)
        print("  the venue did, and every economic conclusion drawn from it")
        print("  needs re-deriving before it is relied on again.")
    print()
    print("  Either way the sign is unchanged: the measured result is")
    print("  negative live and negative in replay, and no-trade still")
    print("  dominates every arm tested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
