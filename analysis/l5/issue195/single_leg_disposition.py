"""The unmatched leg dominates the economics. Hold it, or complete it?

WHY THIS IS THE REAL QUESTION. The depth comparison shows a proven pair
earns the identity's edge almost exactly (+$0.05 at depth 2 on a 1-tick
spread) while a leg that fills ALONE realizes about -$0.22 at the final
mid. The single is 4-5x the pair in magnitude and happens about as often,
so the sign of the whole strategy is set by what we do with singles, not by
how we quote.

THE THREE DISPOSITIONS, priced on the same recorded frames:

  HOLD        carry the residual to settlement. Realized at the final mid,
              which is the proxy, so this is ex-post and includes the
              adverse continuation.
  COMPLETE@T  cross immediately at the opposite touch as it stood when our
              leg filled. Pays the published taker fee
              theta*q*p*(1-p), theta = 0.0695. A matched pair redeems
              exactly $1.00, so realized = (1 - pair) - fee.
  WAIT096     hold out for a complement that makes the pair <= 0.96, taking
              it if the touch ever offers one and otherwise settling
              unmatched. This is today's discipline.

EX ANTE VERSUS EX POST, stated because it decides how to read this. At the
moment our leg fills, holding is EV-NEUTRAL against the market's own prices
-- that identity is already pinned by
`test_declining_a_zero_edge_completion_is_ev_neutral`. These figures are
EX POST: they measure what the market actually did next, which is the
adverse selection a maker quote earns by being filled.

NO FILL IS CLAIMED. The single legs here are PROVEN-filled by the
opposite-touch condition on recorded ladders, never venue-confirmed.
"""
import json
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_queue_depletion as QD        # noqa: E402
from analysis.l5.issue195.queue_depletion_report import (  # noqa: E402
    IN_SAMPLE, OUT_OF_SAMPLE, _load, _mid, _touches)

TICK = D("0.01")
TAKER_THETA = D("0.0695")
PAIR_CAP = D("0.96")


def taker_fee(own_px, qty=D(1)):
    p = D(str(own_px))
    return (TAKER_THETA * qty * p * (D(1) - p)).quantize(D("0.0001"))


def run(group, label, depth=2):
    rows = []
    for path, shape in group:
        for slug, frames in sorted(_load(path, shape).items()):
            if len(frames) < 10:
                continue
            final_mid = None
            for f in reversed(frames):
                final_mid = _mid(f)
                if final_mid is not None:
                    break
            if final_mid is None:
                continue
            for i, f in enumerate(frames[:-1]):
                tt = _touches(f)
                if tt is None:
                    continue
                bb, ba = tt
                up_own, dn_own = bb - depth * TICK, (D(1) - ba) - depth * TICK
                if up_own <= 0 or dn_own <= 0:
                    continue
                vu = QD.fill_verdict("UP", up_own, frames, start=i)
                vd = QD.fill_verdict("DOWN", dn_own, frames, start=i)
                ru, rd = vu.get("verdict"), vd.get("verdict")
                if (ru == QD.PROVEN) == (rd == QD.PROVEN):
                    continue                       # both or neither
                if ru == QD.PROVEN:
                    side, cost, fill_i = "UP", up_own, vu["proven_at_frame"]
                    settle = final_mid
                else:
                    side, cost, fill_i = "DOWN", dn_own, vd["proven_at_frame"]
                    settle = D(1) - final_mid
                ft = _touches(frames[fill_i]) if fill_i is not None else None
                if ft is None:
                    continue
                # THE CROSSING PRICE, NOT THE MAKER TOUCH. Completing as
                # a taker LIFTS the far side: to buy DOWN we sell YES into
                # the bid (own 1 - bid), and to buy UP we pay the ask. An
                # earlier version of this script used the maker touch and
                # so understated the taker's cost by a whole spread.
                cross = (D(1) - ft[0]) if side == "UP" else ft[1]
                # and the MAKER touch, which is what a resting completion
                # would have to wait for
                other_touch = (D(1) - ft[1]) if side == "UP" else ft[0]
                pair_now = cost + cross
                hold = settle - cost
                comp = (D(1) - pair_now) - taker_fee(cross)
                # WAIT096: does the touch ever offer a <=0.96 pair later?
                got = None
                for j in range(fill_i, len(frames)):
                    t2 = _touches(frames[j])
                    if t2 is None:
                        continue
                    ot = (D(1) - t2[1]) if side == "UP" else t2[0]
                    if cost + ot <= PAIR_CAP:
                        got = cost + ot
                        break
                wait = (D(1) - got) if got is not None else hold
                rows.append((hold, comp, wait, got is not None))
    if not rows:
        print("%s: no single-leg cases" % label)
        return
    n = len(rows)
    h = sum(r[0] for r in rows) / n
    c = sum(r[1] for r in rows) / n
    w = sum(r[2] for r in rows) / n
    got = sum(1 for r in rows if r[3])
    print("=" * 72)
    print("%s   depth %d   %d single-leg cases" % (label, depth, n))
    print("  HOLD to settlement (final mid)      $%s per leg"
          % h.quantize(D("0.0001")))
    print("  COMPLETE at the touch, taker        $%s per leg"
          % c.quantize(D("0.0001")))
    print("  WAIT for a <=0.96 pair              $%s per leg   "
          "(offered in %d of %d = %.0f%%)"
          % (w.quantize(D("0.0001")), got, n, 100.0 * got / n))
    print("  completing beats holding by          $%s per leg"
          % (c - h).quantize(D("0.0001")))
    print("  completing beats waiting by          $%s per leg"
          % (c - w).quantize(D("0.0001")))


if __name__ == "__main__":
    for d in (1, 2, 3):
        run(IN_SAMPLE, "IN SAMPLE", depth=d)
    print()
    for d in (1, 2, 3):
        run(OUT_OF_SAMPLE, "OUT OF SAMPLE", depth=d)
