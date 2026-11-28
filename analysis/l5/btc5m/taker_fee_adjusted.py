"""Is there any state on Polymarket US in which LIFTING THE OFFER on a BTC
5-minute market has positive expected value after our real taker fee?

INPUT: the 187 resolved PMUS windows of 2026-11-11/12 -- public UP and DOWN
ask every ~2 s, the model probability, the outcome. A taker fill needs no
fill model: the ask is the price. What this data does NOT have is SIZE, so
every figure is for a share that was actually offered at that price, and
says nothing about how many.

FEE: Polymarket US's published taker formula, coefficient x price x
(1 - price) per share, at this account's tier (0.095). No rebate: the
taker rebate starts at $250,000 of 30-day taker volume (3%).
Return per share = 1 if the side won else 0, minus ask, minus fee.

A. Every (ask band, seconds remaining) state, sampled once per 10 s per
   side: win rate against the ask, net return, mean of window means with a
   95% bootstrap over windows, first and second half of windows.
B. Selection out of sample: cells positive on one half, scored on the other.
C. ONE ENTRY PER WINDOW, the form a real rule would take: buy one share of
   whichever side first shows an ask at or above X with at most T seconds
   left. Net per window, both halves.
"""
import random
import sys

sys.path.insert(0, ".")
from analysis.l5.btc5m.one_sided_value import load, quote, p_side  # noqa

K = 0.095
AB = [(.50, .60), (.60, .70), (.70, .80), (.80, .90), (.90, .95),
      (.95, .97), (.97, .99), (.99, 1.00)]
SB = [(240, 301), (180, 240), (120, 180), (60, 120), (30, 60), (10, 30),
      (0, 10)]


def fee(a):
    return K * a * (1 - a)


def boot(v, n=3000, seed=21):
    if len(v) < 8:
        return None
    rng = random.Random(seed)
    k = len(v)
    ms = sorted(sum(rng.choice(v) for _ in range(k)) / k for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]


def obs(rows):
    out, nxt = [], 0.0
    for r in rows:
        t = r["t_in_window_s"]
        if t < nxt:
            continue
        nxt = t + 10.0
        left = r.get("seconds_remaining")
        left = 300.0 - t if left is None else left
        for side in ("UP", "DOWN"):
            _b, a = quote(r, side)
            if a is None or not (0 < a < 1):
                continue
            won = 1.0 if r["settled_outcome"] == side else 0.0
            out.append((a, left, won, won - a - fee(a), p_side(r, side)))
    return out


def cell(o):
    ai = next((i for i, (lo, hi) in enumerate(AB) if lo <= o[0] < hi), None)
    si = next((i for i, (lo, hi) in enumerate(SB) if lo <= o[1] < hi), None)
    return None if ai is None or si is None else (ai, si)


def main():
    data = load()
    names = list(data)
    per = {m: obs(data[m]) for m in names}
    h = len(names) // 2
    halves = (("FIRST", names[:h]), ("SECOND", names[h:]))
    P = print
    P("windows %d   taker fee coefficient %.3f   no rebate" % (len(names), K))
    P("\n== A1. by ask (all times): win rate against the price, net per "
      "share ==")
    for lo, hi in AB:
        line = " ask %.2f-%.2f  fee %.4f " % (lo, hi, fee((lo + hi) / 2))
        for hn, ws in halves:
            wv, n, wins, asks = [], 0, 0.0, 0.0
            for m in ws:
                xs = [o for o in per[m] if lo <= o[0] < hi]
                if xs:
                    wv.append(sum(o[3] for o in xs) / len(xs))
                    n += len(xs)
                    wins += sum(o[2] for o in xs)
                    asks += sum(o[0] for o in xs)
            c = boot(wv)
            line += " | %s n=%4d win %.3f ask %.3f net %+.4f %s" % (
                hn, n, wins / n if n else 0, asks / n if n else 0,
                sum(wv) / len(wv) if wv else 0,
                "[%+.3f,%+.3f]" % c if c else "[few]")
        P(line)
    P("\n== A2. ask x seconds remaining, both halves pooled: net per share "
      "(* = 95% interval excludes zero; n windows) ==")
    P("   ask        " + "".join("%16s" % ("%d-%ds left" % b) for b in SB))
    for ai, (lo, hi) in enumerate(AB):
        row = "   %.2f-%.2f " % (lo, hi)
        for si in range(len(SB)):
            wv = []
            for m in names:
                xs = [o[3] for o in per[m] if cell(o) == (ai, si)]
                if xs:
                    wv.append(sum(xs) / len(xs))
            c = boot(wv)
            row += "%16s" % ("%+.4f%s(%d)" % (
                sum(wv) / len(wv), "*" if c and (c[0] > 0 or c[1] < 0)
                else " ", len(wv)) if wv else "-")
        P(row)
    P("\n== B. cells positive on one half, scored on the other ==")
    for (dn, dws), (tn, tws) in ((halves[0], halves[1]),
                                 (halves[1], halves[0])):
        st = {}
        for m in dws:
            for o in per[m]:
                c = cell(o)
                if c:
                    a = st.setdefault(c, [0, 0.0])
                    a[0] += 1
                    a[1] += o[3]
        chosen = {c for c, (n, s) in st.items() if n >= 30 and s > 0}
        ts = {}
        for m in tws:
            for o in per[m]:
                c = cell(o)
                if c in chosen:
                    a = ts.setdefault(c, [0, 0.0])
                    a[0] += 1
                    a[1] += o[3]
        still = sum(1 for c in chosen if ts.get(c, [0, 0])[1] > 0)
        wv = []
        for m in tws:
            xs = [o[3] for o in per[m] if cell(o) in chosen]
            if xs:
                wv.append(sum(xs) / len(xs))
        c = boot(wv)
        P("   found on %s: %d cells positive; positive on %s: %d;  net per "
          "share there %+.4f %s" % (
              dn, len(chosen), tn, still, sum(wv) / len(wv) if wv else 0,
              "[%+.4f,%+.4f]" % c if c else ""))
    P("\n== C. one share per window: first ask >= X with <= T s left; net "
      "USD per window traded [95%], windows traded ==")
    P("   X      T      FIRST HALF                        SECOND HALF")
    for X in (.80, .90, .95, .97, .99):
        for T in (240, 120, 60, 30, 10):
            row = "   %.2f  %4d  " % (X, T)
            for _hn, ws in halves:
                v, wn = [], 0
                for m in ws:
                    for r in data[m]:
                        left = 300.0 - r["t_in_window_s"]
                        if left > T:
                            continue
                        hit = None
                        for side in ("UP", "DOWN"):
                            a = quote(r, side)[1]
                            if a is not None and X <= a < 1:
                                hit = (side, a)
                                break
                        if hit:
                            won = 1.0 if r["settled_outcome"] == hit[0] \
                                else 0.0
                            v.append(won - hit[1] - fee(hit[1]))
                            wn += won
                            break
                c = boot(v)
                row += "%+.4f %-17s n=%3d win %.3f   " % (
                    sum(v) / len(v) if v else 0,
                    "[%+.3f,%+.3f]" % c if c else "[few]", len(v),
                    wn / len(v) if v else 0)
            P(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
