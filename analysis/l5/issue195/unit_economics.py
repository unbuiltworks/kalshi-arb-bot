"""Per-attempt unit economics by depth, under each single-leg disposition.

Combines the two measurements into the one number that matters:

    EV per attempt = P(pair) * pair_edge + P(single) * single_outcome

P(pair) is the BOTH-PROVEN rate, a LOWER bound. P(single) is the
exactly-one-PROVEN rate. `pair_edge` is the identity's edge. The single
outcome is measured three ways: carried to settlement, completed as a taker
at the opposite touch, or held out for a <=0.96 pair (today's discipline).

OVERLAPPING ENTRIES. Every frame is evaluated as a candidate entry and they
share futures, so these are not independent trades and the TOTALS in the
other scripts are not a P&L. Per-unit figures and rates are what this
reports, and they are correlated within a window.

THE SAMPLE IS BIASED AGAINST US, which is the honest direction to err. A leg
is PROVEN filled only when the OPPOSITE touch reached it -- the market moved
through our price -- so proven fills over-sample exactly the adverse case.
Real maker fills also happen inside the queue without the touch crossing,
and those are less adverse. So single-leg losses here are likely
OVERSTATED and the pair rate UNDERSTATED.
"""
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_queue_depletion as QD        # noqa: E402
from analysis.l5.issue195.queue_depletion_report import (  # noqa: E402
    IN_SAMPLE, OUT_OF_SAMPLE, _load, _mid, _touches)
from analysis.l5.issue195.single_leg_disposition import (  # noqa: E402
    taker_fee)

TICK = D("0.01")
PAIR_CAP = D("0.96")
DEPTHS = (0, 1, 2, 3, 4, 5, 6)


def measure(group, depth):
    pairs = singles = n = 0
    pair_edge = D(0)
    hold = comp = wait = D(0)
    for path, shape in group:
        for slug, frames in sorted(_load(path, shape).items()):
            if len(frames) < 10:
                continue
            fm = None
            for f in reversed(frames):
                fm = _mid(f)
                if fm is not None:
                    break
            if fm is None:
                continue
            for i, f in enumerate(frames[:-1]):
                tt = _touches(f)
                if tt is None:
                    continue
                bb, ba = tt
                up, dn = bb - depth * TICK, (D(1) - ba) - depth * TICK
                if up <= 0 or dn <= 0:
                    continue
                n += 1
                vu = QD.fill_verdict("UP", up, frames, start=i)
                vd = QD.fill_verdict("DOWN", dn, frames, start=i)
                pu = vu.get("verdict") == QD.PROVEN
                pd = vd.get("verdict") == QD.PROVEN
                if pu and pd:
                    pairs += 1
                    pair_edge += (D(1) - (up + dn))
                elif pu or pd:
                    singles += 1
                    if pu:
                        cost, fi, settle = up, vu["proven_at_frame"], fm
                    else:
                        cost, fi, settle = dn, vd["proven_at_frame"], D(1) - fm
                    ft = _touches(frames[fi]) if fi is not None else None
                    if ft is None:
                        continue
                    # crossing price for the completion (lift the far
                    # side), maker touch for the wait-for-0.96 branch
                    cross = (D(1) - ft[0]) if pu else ft[1]
                    other = (D(1) - ft[1]) if pu else ft[0]
                    h = settle - cost
                    hold += h
                    comp += (D(1) - (cost + cross)) - taker_fee(cross)
                    got = None
                    for j in range(fi, len(frames)):
                        t2 = _touches(frames[j])
                        if t2 is None:
                            continue
                        ot = (D(1) - t2[1]) if pu else t2[0]
                        if cost + ot <= PAIR_CAP:
                            got = cost + ot
                            break
                    wait += (D(1) - got) if got is not None else h
    return n, pairs, singles, pair_edge, hold, comp, wait


def table(group, label):
    print("=" * 78)
    print("%s" % label)
    print("  d   P(pair)  P(single)  pair$   hold$    taker$   wait$ | "
          "EV/attempt: today  taker")
    for d in DEPTHS:
        n, pr, sg, pe, h, c, w = measure(group, d)
        if not n or not pr:
            continue
        pp, ps = D(pr) / n, D(sg) / n
        epr = pe / pr
        eh = (h / sg) if sg else D(0)
        ec = (c / sg) if sg else D(0)
        ew = (w / sg) if sg else D(0)
        ev_today = pp * epr + ps * ew
        ev_taker = pp * epr + ps * ec
        print("  %d   %6.1f%%  %7.1f%%  %6s  %7s  %7s  %7s | %8s  %8s"
              % (d, 100 * pp, 100 * ps,
                 epr.quantize(D("0.0001")), eh.quantize(D("0.0001")),
                 ec.quantize(D("0.0001")), ew.quantize(D("0.0001")),
                 ev_today.quantize(D("0.0001")),
                 ev_taker.quantize(D("0.0001"))))


if __name__ == "__main__":
    table(IN_SAMPLE, "IN SAMPLE   (2026-10-28 x3 windows, 2026-11-06 0300z)")
    print()
    table(OUT_OF_SAMPLE,
          "OUT OF SAMPLE   (2026-10-25 x12 windows, 2026-11-06 1130z)")
    print()
    print("today = maker-only, hold out for a <=0.96 pair, taker disabled.")
    print("taker = complete the unmatched leg at the opposite touch.")
    print("P(pair) is a LOWER bound; single losses are likely OVERSTATED.")
    print("Nothing here is a venue-confirmed fill or a realized P&L.")
