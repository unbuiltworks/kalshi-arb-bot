"""Is ANY entry cell positive out of sample?

Every (depth behind the bid, side price band, time bucket) cell from
entry_research.py PART 2 -- 9 x 5 x 4 = 180 cells. A cell is SELECTED when
its expected value per quote is positive on the DISCOVERY half of the
windows, with at least 30 quotes there; it is then scored on the OTHER
half, which played no part in choosing it. Done both ways round.

If selection found real edge, the selected cells stay positive on the
other half. If it found noise, they regress to the overall mean.
Portfolio view: USD per window from quoting one share in every selected
cell, mean with a 95% bootstrap over windows. Same assumed fill rule.
"""
import contextlib
import io
import sys

sys.path.insert(0, ".")
from analysis.l5.btc5m import entry_research as R     # noqa: E402

PB = [(0, .15), (.15, .35), (.35, .65), (.65, .85), (.85, 1.0)]


def cell_of(r):
    d, t, bid = r[0], r[1], r[2]
    pb = next((i for i, (lo, hi) in enumerate(PB) if lo <= bid < hi), None)
    tb = next((i for i, (lo, hi) in enumerate(R.TB) if lo <= t < hi), None)
    return None if pb is None or tb is None else (d, pb, tb)


def main():
    with contextlib.redirect_stdout(io.StringIO()):
        recs, names = R.part2(print)
    h = len(names) // 2
    halves = {"FIRST": names[:h], "SECOND": names[h:]}
    P = print

    def cell_stats(ws):
        tot = {}
        for m in ws:
            for r in recs[m]:
                c = cell_of(r)
                if c is None:
                    continue
                a = tot.setdefault(c, [0, 0.0])
                a[0] += 1
                a[1] += r[4] if r[3] else 0.0
        return tot

    def window_pnl(ws, cells):
        out = []
        for m in ws:
            out.append(sum(r[4] for r in recs[m]
                           if r[3] and cell_of(r) in cells))
        return out

    for disc, test in (("FIRST", "SECOND"), ("SECOND", "FIRST")):
        st = cell_stats(halves[disc])
        chosen = {c for c, (n, s) in st.items() if n >= 30 and s > 0}
        tested = sum(1 for c, (n, s) in st.items() if n >= 30)
        P("== discovered on %s half, scored on %s half ==" % (disc, test))
        P("   cells with >=30 quotes: %d   positive in discovery: %d" % (
            tested, len(chosen)))
        ts = cell_stats(halves[test])
        still = sum(1 for c in chosen if ts.get(c, [0, 0])[1] > 0)
        P("   of those, positive on the other half: %d (%.0f%%)" % (
            still, 100.0 * still / len(chosen) if chosen else 0))
        for lab, ws in (("discovery", halves[disc]), ("OTHER HALF",
                                                      halves[test])):
            v = window_pnl(ws, chosen)
            c = R.boot(v)
            q = sum(cell_stats(ws).get(c_, [0, 0])[0] for c_ in chosen)
            P("   %-10s selected cells: %+.4f USD per window %s  "
              "(%d quotes)" % (lab, sum(v) / len(v),
                               "[%+.4f,%+.4f]" % c if c else "", q))
        allc = set(st)
        v = window_pnl(halves[test], allc)
        c = R.boot(v)
        P("   OTHER HALF every cell:     %+.4f USD per window %s" % (
            sum(v) / len(v), "[%+.4f,%+.4f]" % c if c else ""))
        top = sorted(chosen, key=lambda c_: -st[c_][1] / st[c_][0])[:8]
        P("   best discovery cells -> other half (EV per quote):")
        for c_ in top:
            n2, s2 = ts.get(c_, [0, 0.0])
            P("     depth %2dc  bid %.2f-%.2f  t%d-%d   %+.4f (n=%d) -> "
              "%s" % (c_[0], PB[c_[1]][0], PB[c_[1]][1], R.TB[c_[2]][0],
                      R.TB[c_[2]][1], st[c_][1] / st[c_][0], st[c_][0],
                      "%+.4f (n=%d)" % (s2 / n2, n2) if n2 else "no quotes"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
