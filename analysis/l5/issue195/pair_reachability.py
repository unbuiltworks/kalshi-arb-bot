"""Can a two-sided maker pair below 0.96 actually be reached? Measured.

THE IDENTITY THAT DECIDES IT. For a binary pair, own prices are
own_UP = best_bid and own_DOWN = 1 - best_ask, so the pair available AT THE
TOUCH is exactly

    touch_pair = best_bid + (1 - best_ask) = 1 - spread

With a 1-tick spread the touch pair is 0.99: buying both sides at the market
cannot beat $1.00 by more than the spread. Resting each leg `d` ticks behind
its own touch gives

    pair = 1 - spread - 2 * d * tick

so the engine's `depth_ticks = 2` yields 0.95 on a 1-tick spread -- exactly
the pair 0100z had resting. The design is self-consistent. The cost is that
a quote `d` ticks behind only fills when the market comes TO it, and BOTH
legs must fill, which needs the touch to travel down `d` ticks and up `d`
ticks within one window: a round trip of 2*d ticks.

WHAT THIS MEASURES. For every sample as a candidate entry, whether the touch
later reached both resting levels. Reaching a level is NECESSARY for a maker
fill and not sufficient -- the venue publishes no per-order queue position --
so these are UPPER BOUNDS on completion, never fill rates.
"""
import json
import sys
from collections import Counter
from decimal import Decimal as D

sys.path.insert(0, ".")
TAPE = "analysis/l5/completion_requote/tape_20261028.jsonl"
TICK = D("0.01")


def main():
    rows = [json.loads(l) for l in open(TAPE)]
    by = {}
    for r in rows:
        bids, asks = r.get("bids") or [], r.get("asks") or []
        if not bids or not asks:
            continue
        b, a = D(bids[0][0]), D(asks[0][0])
        if a <= b:
            continue
        by.setdefault(r["slug"], []).append((float(r["t"]), b, a))

    spreads = Counter()
    for slug, s in sorted(by.items()):
        s.sort()
        print("=" * 70)
        print("%s   %d usable samples" % (slug, len(s)))
        sp = [a - b for _, b, a in s]
        for x in sp:
            spreads[str(x)] += 1
        print("  spread (wire)        min %s  median %s  max %s"
              % (min(sp), sorted(sp)[len(sp) // 2], max(sp)))
        tp = [D(1) - x for x in sp]
        print("  touch pair (1-spread) min %s  median %s  max %s"
              % (min(tp), sorted(tp)[len(tp) // 2], max(tp)))
        for d in (0, 1, 2, 3):
            both = 0
            for i, (t0, b0, a0) in enumerate(s):
                up_at = b0 - d * TICK                 # own UP, rest below bid
                dn_at = (D(1) - a0) - d * TICK        # own DOWN
                fut = s[i + 1:]
                if not fut:
                    continue
                # UP at own p fills only if the bid comes DOWN to p
                up_ok = any(b <= up_at for _, b, _ in fut)
                # DOWN at own q is wire 1-q on the ask; fills if the ask
                # comes UP to it, i.e. own touch of DOWN falls to q
                dn_ok = any((D(1) - a) <= dn_at for _, _, a in fut)
                both += (up_ok and dn_ok)
            pair = D(1) - sorted(sp)[len(sp) // 2] - 2 * d * TICK
            print("  depth %d ticks -> median pair %s : both levels later "
                  "reached in %d of %d entries (%.0f%%)"
                  % (d, pair, both, len(s), 100.0 * both / len(s)))
    print("=" * 70)
    print("spread distribution over both windows:")
    for k, v in sorted(spreads.items()):
        print("   %s : %d" % (k, v))
    print()
    print("NOT A FILL RATE. 'both levels reached' is an UPPER BOUND on")
    print("completing the pair: it ignores queue position entirely.")


if __name__ == "__main__":
    main()
