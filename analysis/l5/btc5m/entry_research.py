"""When, and at what price, does a resting bid acquire inventory with
positive expected value?

TWO INDEPENDENT SOURCES, kept apart.

PART 1 -- REAL FILLS. Bonereaper's own BTC 5-minute buys on Polymarket:
18,715 fills in 304 windows, 2026-03-25 to 2026-08-31, each with its
price, its second within the window and the window's winner
(data/traders/bonereaper/normalized/fills_in_scope.jsonl). Every row is a
fill that HAPPENED, so every return here is conditional on a fill by
construction. What it cannot say: whether a fill was maker or taker, what
the book was, or what was quoted and did not fill. A different venue and
fee schedule; no fee or rebate is applied.

PART 2 -- OUR VENUE'S QUOTES. The 187 resolved PMUS windows of
2026-11-11/12 (public bid/ask each ~2 s, outcome). No sizes, no trades, so
a fill is ASSUMED when the side's ask reaches the quote's price while it
rests. A bid `d` cents behind the side's own bid, placed every 10 s,
resting 10 s. Maker fee 0.

Returns are USD per share at settlement, as the mean of per-window means
with a 95% bootstrap over windows (the unit of independence). PART 2 is
split into a first and second half of windows.
"""
import collections
import json
import random
import sys

sys.path.insert(0, ".")
from analysis.l5.btc5m.one_sided_value import load, quote  # noqa: E402

BR = "data/traders/bonereaper/normalized/fills_in_scope.jsonl"
PX = [(0, .05), (.05, .15), (.15, .30), (.30, .45), (.45, .55), (.55, .70),
      (.70, .85), (.85, .95), (.95, 1.01)]
TB = [(0, 60), (60, 150), (150, 240), (240, 301)]


def boot(vals, n=3000, seed=11):
    if len(vals) < 8:
        return None
    rng = random.Random(seed)
    k = len(vals)
    ms = sorted(sum(rng.choice(vals) for _ in range(k)) / k
                for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]


def cell(by_window):
    """by_window: {window: [(shares, ret_per_share)]} -> text."""
    vals, sh, pnl, n = [], 0.0, 0.0, 0
    for rows in by_window.values():
        s = sum(a for a, _ in rows)
        if s <= 0:
            continue
        vals.append(sum(a * b for a, b in rows) / s)
        sh += s
        pnl += sum(a * b for a, b in rows)
        n += len(rows)
    if not vals:
        return "        -"
    m = sum(vals) / len(vals)
    c = boot(vals)
    return "%+.4f %-17s w=%3d n=%5d pnl %+9.2f" % (
        m, "[%+.3f,%+.3f]" % c if c else "[few]", len(vals), n, pnl)


def part1(P):
    wins = collections.defaultdict(list)
    for line in open(BR):
        f = json.loads(line)
        if f.get("asset") != "BTC" or "-5m-" not in f.get("market_slug", ""):
            continue
        if f.get("side") != "BUY" or f.get("winning_outcome") in (None, ""):
            continue
        wins[f["market_slug"]].append(f)
    P("== PART 1: Bonereaper's real BTC5m buys: %d windows, %d fills ==" % (
        len(wins), sum(len(v) for v in wins.values())))

    def ret(f):
        return (1.0 if f["outcome"] == f["winning_outcome"] else 0.0) \
            - f["price"]

    def table(label, key, buckets):
        P("-- %s: settlement return per share, share-weighted within a "
          "window" % label)
        for lo, hi in buckets:
            bw = collections.defaultdict(list)
            for w, rows in wins.items():
                for f in rows:
                    if lo <= f[key] < hi:
                        bw[w].append((f["shares"], ret(f)))
            P("   %5.2f-%-5.2f %s" % (lo, hi, cell(bw)))
    table("by PRICE PAID", "price", PX)
    table("by SECOND IN WINDOW", "seconds_from_window_start", TB)
    P("-- price x time (mean return per share; window count)")
    P("   price        " + "".join("%14s" % ("t%d-%d" % b) for b in TB))
    for lo, hi in PX:
        row = "   %4.2f-%-4.2f   " % (lo, hi)
        for t0, t1 in TB:
            bw = collections.defaultdict(list)
            for w, rows in wins.items():
                for f in rows:
                    if lo <= f["price"] < hi \
                            and t0 <= f["seconds_from_window_start"] < t1:
                        bw[w].append((f["shares"], ret(f)))
            vals = [sum(a * b for a, b in r) / sum(a for a, _ in r)
                    for r in bw.values() if sum(a for a, _ in r) > 0]
            row += "%14s" % ("%+.3f (%d)" % (sum(vals) / len(vals),
                                             len(vals)) if vals else "-")
        P(row)
    #: the portfolio: one number per window
    tot, cost, wl = [], 0.0, []
    for w, rows in wins.items():
        c = sum(f["shares"] * f["price"] for f in rows)
        r = sum(f["shares"] for f in rows
                if f["outcome"] == f["winning_outcome"])
        tot.append(r - c)
        cost += c
        wl.append((r - c) / c if c else 0.0)
    c = boot(tot)
    P("-- PORTFOLIO, per window: settled net (redemption - cost), no fees "
      "or rebates")
    P("   mean %+.2f USD %s   total %+.2f on %.0f bought (%.2f%%)   "
      "windows positive %d/%d" % (
          sum(tot) / len(tot), "[%+.2f,%+.2f]" % c, sum(tot), cost,
          100 * sum(tot) / cost, sum(1 for x in tot if x > 0), len(tot)))
    fav = collections.defaultdict(list)
    dog = collections.defaultdict(list)
    for w, rows in wins.items():
        for f in rows:
            (fav if f["price"] >= 0.5 else dog)[w].append(
                (f["shares"], ret(f)))
    P("   bought at >= 0.50   %s" % cell(fav))
    P("   bought at <  0.50   %s" % cell(dog))
    up = collections.Counter()
    for rows in wins.values():
        s = {f["outcome"] for f in rows}
        up["both sides" if len(s) == 2 else "one side"] += 1
    P("   windows where it bought: %s" % dict(up))


DEPTHS = [0, 1, 2, 3, 5, 8, 12, 20, 30]


def part2(P):
    data = load()
    names = list(data)
    halves = (("FIRST HALF", names[:len(names) // 2]),
              ("SECOND HALF", names[len(names) // 2:]))
    #: per window, per (depth, time bucket, kind): [filled?, ret]
    recs = {m: [] for m in names}
    for m in names:
        rows, nxt = data[m], 0.0
        for i, r in enumerate(rows):
            t = r["t_in_window_s"]
            if t < nxt or t > 285:
                continue
            nxt = t + 10.0
            for side in ("UP", "DOWN"):
                bid, ask = quote(r, side)
                if bid is None or ask is None or not (0 < bid < ask < 1):
                    continue
                won = 1.0 if r["settled_outcome"] == side else 0.0
                for d in DEPTHS:
                    x = round(bid - d / 100.0, 2)
                    if x < 0.01:
                        continue
                    hit = False
                    for j in range(i + 1, len(rows)):
                        if rows[j]["t_in_window_s"] - t > 10.0:
                            break
                        a = quote(rows[j], side)[1]
                        if a is not None and a <= x + 1e-9:
                            hit = True
                            break
                    recs[m].append((d, t, bid, hit, won - x))
    P("\n== PART 2: PMUS quotes, a bid d cents behind the side's own bid, "
      "187 windows ==")
    P("   fill = the side's ask reaches the quote within 10 s (ASSUMED). "
      "ret|fill and EV per quote placed, USD per share")

    def line(ws, pred):
        q = f = 0
        rv, ev = [], []
        for m in ws:
            xs = [r for r in recs[m] if pred(r)]
            if not xs:
                continue
            fl = [r[4] for r in xs if r[3]]
            q += len(xs)
            f += len(fl)
            ev.append(sum(fl) / len(xs))
            if fl:
                rv.append(sum(fl) / len(fl))
        if not q:
            return "no quotes"
        c1, c2 = boot(rv), boot(ev)
        return "fill %5.1f%%  ret|fill %+.4f %-17s EV/quote %+.5f %s" % (
            100.0 * f / q, sum(rv) / len(rv) if rv else 0,
            "[%+.3f,%+.3f]" % c1 if c1 else "[few]",
            sum(ev) / len(ev), "[%+.4f,%+.4f]" % c2 if c2 else "[few]")
    for d in DEPTHS:
        P("-- depth %2d c" % d)
        for hn, ws in halves:
            P("   %-11s %s" % (hn, line(ws, lambda r, d=d: r[0] == d)))
    P("-- depth x side price (both halves pooled; EV per quote placed)")
    PB = [(0, .15), (.15, .35), (.35, .65), (.65, .85), (.85, 1.0)]
    P("   depth    " + "".join("%20s" % ("bid %.2f-%.2f" % b) for b in PB))
    for d in DEPTHS:
        row = "   %2d c    " % d
        for lo, hi in PB:
            ev = []
            for m in names:
                xs = [r for r in recs[m] if r[0] == d and lo <= r[2] < hi]
                if xs:
                    ev.append(sum(r[4] for r in xs if r[3]) / len(xs))
            c = boot(ev)
            row += "%20s" % ("%+.4f%s" % (
                sum(ev) / len(ev), "*" if c and (c[0] > 0 or c[1] < 0)
                else " ") if ev else "-")
        P(row)
    P("   * = 95% interval over windows excludes zero")
    P("-- depth x time in window (both halves pooled; EV per quote placed)")
    P("   depth    " + "".join("%16s" % ("t%d-%d" % b) for b in TB))
    for d in DEPTHS:
        row = "   %2d c    " % d
        for t0, t1 in TB:
            ev = []
            for m in names:
                xs = [r for r in recs[m] if r[0] == d and t0 <= r[1] < t1]
                if xs:
                    ev.append(sum(r[4] for r in xs if r[3]) / len(xs))
            c = boot(ev)
            row += "%16s" % ("%+.4f%s" % (
                sum(ev) / len(ev), "*" if c and (c[0] > 0 or c[1] < 0)
                else " ") if ev else "-")
        P(row)
    return recs, names


def main():
    P = print
    part1(P)
    part2(P)
    return 0


if __name__ == "__main__":
    sys.exit(main())
