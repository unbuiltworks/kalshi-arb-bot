#!/usr/bin/env python3
"""What bonereaper DOES in a BTC 5m window, fill by fill (issue #247).

The replication target for `L5_BTC5M_STRADDLE_REQUOTE_TICKS` mode. Reads
l5_window_sequences.csv; every row there is a BUY at a price, with no
maker/taker flag, no fee and no order placements or cancels -- so this
describes FILLS, not quoting, and says nothing about profitability.

  python3 analysis/traders/bonereaper/behaviour_btc5m.py
"""
import collections
import csv
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    seq = collections.defaultdict(list)
    with open(os.path.join(HERE, "l5_window_sequences.csv")) as fh:
        for r in csv.DictReader(fh):
            if r["asset"] == "BTC" and r["window_minutes"] == "5":
                seq[r["window_id"]].append(r)
    ext = red = 0
    by_imb = collections.defaultdict(lambda: [[], []])
    resid, fills, sizes, t0, t1, both = [], [], [], [], [], 0
    usd_band = collections.Counter()
    tq = mq = 0.0
    for rs in seq.values():
        rs.sort(key=lambda r: int(r["trade_seq"]))
        up = dn = 0.0
        for r in rs:
            q, p = float(r["shares"]), float(r["price"])
            imb = up - dn
            if imb:
                k = min(4, int(abs(imb) / (up + dn) * 5))
                heavy = "Up" if imb > 0 else "Down"
                if r["outcome"] == heavy:
                    ext += 1
                    by_imb[k][0].append(q)
                else:
                    red += 1
                    by_imb[k][1].append(q)
            if r["outcome"] == "Up":
                up += q
            else:
                dn += q
            usd_band[min(9, int(p * 10))] += q * p
            sizes.append(q)
        resid.append(abs(up - dn) / (up + dn))
        tq += up + dn
        mq += min(up, dn)
        both += bool(up and dn)
        fills.append(len(rs))
        t0.append(int(rs[0]["seconds_from_window_start"]))
        t1.append(int(rs[-1]["seconds_from_window_start"]))
    n = len(seq)
    q = sorted(resid)
    print("bonereaper BTC 5m behaviour -- %d windows, %d fills" % (n, sum(fills)))
    print("=" * 70)
    print("fills per window (median)              %.1f" % st.median(fills))
    print("windows with fills on BOTH sides       %d (%.1f%%)" % (both, 100.0 * both / n))
    print("first / last fill, s from open (median) %d / %d"
          % (st.median(t0), st.median(t1)))
    print("fill size, shares (median)             %.1f" % st.median(sizes))
    print("\nINVENTORY")
    print("unmatched share of final holdings      median %.3f  (p25 %.3f, p75 %.3f)"
          % (st.median(resid), q[n // 4], q[3 * n // 4]))
    print("share of all shares that end MATCHED   %.3f" % (2 * mq / tq))
    print("fills that ADD to the heavy side       %d of %d (%.1f%%)"
          % (ext, ext + red, 100.0 * ext / (ext + red)))
    print("\nSIZE DOES NOT RESPOND TO INVENTORY (median shares per fill)")
    print("  imbalance      adds-to-heavy   reduces   share adding")
    for k in range(5):
        e, r_ = by_imb[k]
        print("  %3d-%3d%%        %6.1f        %6.1f      %.1f%%"
              % (k * 20, k * 20 + 20, st.median(e), st.median(r_),
                 100.0 * len(e) / (len(e) + len(r_))))
    tot = sum(usd_band.values())
    print("\nWHERE THE DOLLARS GO, by fill price")
    for b in range(10):
        print("  %.1f-%.1f   %5.1f%%" % (b / 10, b / 10 + 0.1,
                                      100.0 * usd_band[b] / tot))
    print("\nNOT IN THIS DATA: maker/taker per fill, fees, resting orders,")
    print("cancels. workup.json puts 49.67% of this wallet's dollars as taker.")


if __name__ == "__main__":
    main()
