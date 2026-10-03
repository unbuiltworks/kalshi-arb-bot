"""BTC15m oscillation / continuous-inventory opportunity analysis.

PRINTS ARE PRINTS. Every number below is derived from the venue's own public
TRADE TAPE for expired BTC15m contracts (`/v1/trades/{symbol}`). A print is
evidence that SOMEBODY traded at that price. It is NOT an executable price,
NOT a quote, NOT a fill we could have had, and NOT a book. No fill is
inferred, replayed or counted anywhere in this file -- the backfill's own
caveat is that the tape is recoverable for expired contracts and the BOOK is
not, so a fill replay is impossible here and is not attempted.

What a print DOES tell us: the YES price somebody paid at a moment. The
complementary NO price at that moment is 1 - YES by the venue's identity, so a
single ladder of prints gives the observable path of both sides.

Pair-cost arithmetic below is therefore "two prints, at two times, whose
prices sum below X" -- an OBSERVED PATH property, not a tradeable pair. It
bounds where to look, nothing more.
"""
from __future__ import annotations

import json
import statistics as st
from collections import Counter
from pathlib import Path

SRC = Path("/tmp/claude-0/-home-user-kalshi-arb-bot/"
           "f2f630e2-70fa-534f-9e92-d1857b7df8ca/scratchpad/btc15m_prints.json")
BUCKETS = (0.01, 0.02, 0.05, 0.10, 0.20)
PAIR_TARGETS = (1.00, 0.99, 0.98, 0.97, 0.95)


def pq(v, a):
    v = sorted(x for x in v if x is not None)
    return v[min(int(a * (len(v) - 1)), len(v) - 1)] if v else None


def main():
    raw = json.loads(SRC.read_text())
    wins = {}
    for sym, rows in raw.items():
        p = []
        for r in rows:
            try:
                p.append((int(r["timestampms"]) / 1000.0, float(r["price"]),
                          float(r["amount"]), r.get("type")))
            except Exception:
                continue
        p.sort()
        if len(p) >= 2:
            wins[sym] = p

    print("=" * 96)
    print("BTC15m OSCILLATION -- PUBLIC TRADE PRINTS ONLY, NO FILLS INFERRED")
    print("=" * 96)
    print("windows with >=2 prints: %d   total prints: %d"
          % (len(wins), sum(len(v) for v in wins.values())))
    print("A print is EVIDENCE SOMEBODY TRADED, not an executable price.\n")

    lead_flips, reversals, exc, gaps, oneway = [], [], [], [], 0
    pair_hits = {t: [] for t in PAIR_TARGETS}
    pair_gaps = {t: [] for t in PAIR_TARGETS}
    alt_counts = []

    for sym, p in wins.items():
        px = [x[1] for x in p]
        ts = [x[0] for x in p]

        # 1. LEADERSHIP FLIPS: which side is the favourite (YES price vs 0.50)
        lead = ["YES" if x > 0.50 else "DOWN" for x in px]
        flips = sum(1 for a, b in zip(lead, lead[1:]) if a != b)
        lead_flips.append(flips)

        # 2/3/4. MEANINGFUL REVERSALS: a direction change in the print path,
        # sized by the excursion that preceded it. 1c is the tick, so a
        # "meaningful" reversal is simply any direction change; the SIZE
        # distribution is what separates noise from opportunity.
        dirs, legs, last_turn_i = [], [], 0
        for i in range(1, len(px)):
            d = px[i] - px[i - 1]
            if d == 0:
                continue
            dirs.append((i, 1 if d > 0 else -1))
        nrev = 0
        if dirs:
            cur = dirs[0][1]
            start_i = dirs[0][0] - 1
            for i, d in dirs[1:]:
                if d != cur:
                    nrev += 1
                    legs.append((abs(px[i - 1] - px[start_i]),
                                 ts[i - 1] - ts[start_i]))
                    cur = d
                    start_i = i - 1
        reversals.append(nrev)
        for mag, dt in legs:
            exc.append(mag)
            gaps.append(dt)

        # 8. ONE-WAY WINDOWS: the path never gives back a meaningful part of
        # its move, so an inventory trader accumulates residual with no
        # complement opportunity.
        rng = max(px) - min(px)
        net = abs(px[-1] - px[0])
        if rng > 0 and net / rng >= 0.90:
            oneway += 1

        # 5/6/7. PAIR-COST PATH PROPERTY: a YES print at p1 and a LATER NO
        # print at (1 - p2) sum to p1 + 1 - p2. Below 1.00 requires p2 > p1,
        # i.e. the YES price RISING after we "bought" YES cheap.
        best = {t: None for t in PAIR_TARGETS}
        alt = 0
        for i in range(len(px)):
            for j in range(i + 1, len(px)):
                s = px[i] + (1 - px[j])
                for t in PAIR_TARGETS:
                    if s < t and (best[t] is None or s < best[t][0]):
                        best[t] = (s, ts[j] - ts[i], i, j)
        for t in PAIR_TARGETS:
            if best[t]:
                pair_hits[t].append(sym)
                pair_gaps[t].append(best[t][1])
        # alternating opportunities: count disjoint (i<j) pairs under 1.00
        used, i = set(), 0
        while i < len(px) - 1:
            hit = None
            for j in range(i + 1, len(px)):
                if px[i] + (1 - px[j]) < 1.00:
                    hit = j
                    break
            if hit is None:
                i += 1
                continue
            alt += 1
            i = hit + 1
        alt_counts.append(alt)

    n = len(wins)
    print("--- 1. LEADERSHIP FLIPS (which side is above 0.50) ---")
    print("    per window  p50 %d  p90 %d  max %d  mean %.1f | zero-flip windows %d/%d = %.0f%%"
          % (pq(lead_flips, .5), pq(lead_flips, .9), max(lead_flips),
             st.mean(lead_flips), sum(1 for x in lead_flips if x == 0), n,
             100 * sum(1 for x in lead_flips if x == 0) / n))

    print("\n--- 2. MEANINGFUL PRICE REVERSALS (direction changes in the path) ---")
    print("    per window  p50 %d  p90 %d  max %d  mean %.1f"
          % (pq(reversals, .5), pq(reversals, .9), max(reversals),
             st.mean(reversals)))

    print("\n--- 3. EXCURSION SIZES between reversals (n=%d legs) ---" % len(exc))
    c = Counter()
    for m in exc:
        for b in BUCKETS[::-1]:
            if m >= b:
                c[b] += 1
                break
        else:
            c[0.0] += 1
    tot = sum(c.values()) or 1
    for b in (0.0,) + BUCKETS:
        lab = "<1c" if b == 0 else ("%d c+" % round(b * 100))
        print("    %-6s %5d  %5.1f%%" % (lab, c[b], 100 * c[b] / tot))
    print("    excursion  p50 %.3f  p90 %.3f  max %.3f"
          % (pq(exc, .5), pq(exc, .9), max(exc) if exc else 0))

    print("\n--- 4. TIME BETWEEN REVERSALS (s) ---")
    print("    p10 %.1f  p50 %.1f  p90 %.1f  max %.1f"
          % (pq(gaps, .1), pq(gaps, .5), pq(gaps, .9), max(gaps) if gaps else 0))

    print("\n--- 5. WINDOWS WHERE TWO PRINTS SUM BELOW A PAIR TARGET ---")
    print("    (OBSERVED PATH property: YES print at t1 + (1 - YES print at t2>t1).")
    print("     NOT a tradeable pair, NOT a fill.)")
    for t in PAIR_TARGETS:
        h = len(pair_hits[t])
        print("    sum < %.2f : %3d/%d windows = %5.1f%%" % (t, h, n, 100 * h / n))

    print("\n--- 6. TIME SEPARATING THE TWO PRINTS (s) ---")
    for t in PAIR_TARGETS:
        g = pair_gaps[t]
        if g:
            print("    sum < %.2f : p50 %6.1f  p90 %7.1f  max %7.1f"
                  % (t, pq(g, .5), pq(g, .9), max(g)))

    print("\n--- 7. ALTERNATING SUB-1.00 OPPORTUNITIES PER WINDOW (disjoint) ---")
    print("    p50 %d  p90 %d  max %d  mean %.2f | windows with >=2: %d/%d"
          % (pq(alt_counts, .5), pq(alt_counts, .9), max(alt_counts),
             st.mean(alt_counts), sum(1 for x in alt_counts if x >= 2), n))

    print("\n--- 8. ONE-WAY WINDOWS (>=90%% of range retained, residual danger) ---")
    print("    %d/%d = %.1f%%" % (oneway, n, 100 * oneway / n))

    Path("analysis/gemini/info_edge/btc15m_oscillation.json").write_text(
        json.dumps({"windows": n, "prints": sum(len(v) for v in wins.values()),
                    "lead_flips": lead_flips, "reversals": reversals,
                    "excursions": exc, "gaps": gaps,
                    "pair_hit_windows": {str(k): len(v) for k, v in pair_hits.items()},
                    "alt_counts": alt_counts, "oneway": oneway,
                    "labels": "PRINTS ONLY. No executable price, no fill, no book."},
                   indent=2, default=str))
    print("\nwrote analysis/gemini/info_edge/btc15m_oscillation.json")


if __name__ == "__main__":
    main()
