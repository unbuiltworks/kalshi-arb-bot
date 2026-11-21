"""Bonereaper's most recent complete days, fill by fill: what he pays, what
it returns, and what each venue's taker fee does to it.

INPUT: his public activity for 2026-11-17 to 2026-11-20 (fetch_activity.py).
Every trade is a BUY. A market's winner is the outcome he REDEEMED; a
market with buys and no redemption is one where everything he held lost
(only markets that ended at least six hours before the pull are used).

WHAT THE ACTIVITY FEED'S AMOUNTS ARE. Cash paid for buys against cash
redeemed is -0.38% of dollars in these markets. The platform's own series
for the same days reads: trades before fees +0.94%, fees paid -1.49%,
market P&L after fees -0.55%. So the feed's amounts are AFTER the taker
fee (it is taken from the shares received), and the platform's "before
fees" figure is that result with the fee added back. The columns follow
from that:

    actual        (payout - cost) / cost from the feed: after Polymarket's
                  0.07 taker fee, before any rebate
    before fee    actual + 0.07 x p x (1 - p) per share
    PMUS no reb.  before fee - 0.095 x p x (1 - p): the same fills at
                  Polymarket US's taker coefficient for this account's
                  tier, where no rebate is available to it
    with 40% reb. actual + 40% of the fee: his rebate over the last 30
                  days was 40% of fees charged

Each fill is treated as a taker fill. The platform puts his fees at
1.49-1.68% of volume, which is what all-taker gives at these prices.
Intervals are a 95% bootstrap over MARKETS. These are his fills at
Polymarket's prices; nothing here shows the same asks exist on PMUS.
"""
import collections
import json
import re
import sys

SRC = sys.argv[1]
PULL_TS = 1795203464
BUCKETS = [(0, .30), (.30, .50), (.50, .70), (.70, .85), (.85, .93),
           (.93, .97), (.97, .99), (.99, 1.01)]


def main():
    rows, seen = [], set()
    for line in open(SRC):
        r = json.loads(line)
        k = (r["transaction_hash"], r["token_id"], r["type"], r["size"],
             r["price"], r["timestamp"])
        if k in seen:
            continue
        seen.add(k)
        rows.append(r)
    won = {}
    for r in rows:
        if r["type"] == "REDEEM" and (r.get("usdc_size") or 0) > 0:
            won[r["condition_id"]] = (r.get("outcome") or "").lower()
    by = collections.defaultdict(list)
    for r in rows:
        if r["type"] != "TRADE":
            continue
        m = re.match(r"btc-updown-5m-(\d+)$", r.get("slug") or "")
        if not m:
            continue
        end = int(m.group(1)) + 300
        if end > PULL_TS - 6 * 3600:
            continue
        by[r["condition_id"]].append(r)
    P = print
    unknown = [c for c, v in by.items()
               if c not in won and len({x["outcome"] for x in v}) > 1]
    P("BTC 5m markets used: %d   fills: %d   markets with both sides bought "
      "and no redemption (excluded): %d" % (
          len(by) - len(unknown), sum(len(v) for c, v in by.items()
                                      if c not in unknown), len(unknown)))
    redeemed_named = sum(1 for c in by if won.get(c))
    P("markets whose winner is named by a redemption: %d" % redeemed_named)
    import random
    per = collections.defaultdict(lambda: collections.defaultdict(
        lambda: [0, 0.0, 0.0, 0.0]))            # market -> bucket -> sums
    per_mkt = []
    for c, v in by.items():
        if c in unknown:
            continue
        w = won.get(c)
        cost = pay = 0.0
        for x in v:
            hit = (x["outcome"] or "").lower() == w if w else False
            payout = x["size"] if hit else 0.0
            pq = x["size"] * x["price"] * (1 - x["price"])
            for i, (lo, hi) in enumerate(list(BUCKETS) + [(0, 1.01)]):
                if lo <= x["price"] < hi:
                    a = per[c][i]
                    a[0] += 1
                    a[1] += x["usdc_size"]
                    a[2] += payout - x["usdc_size"]
                    a[3] += pq
            cost += x["usdc_size"]
            pay += payout
        per_mkt.append((cost, pay - cost))
    mk = list(per)

    def ratio(sample, i, fn):
        d = sum(per[c][i][1] for c in sample)
        return (100.0 * sum(fn(per[c][i]) for c in sample) / d) if d else 0

    def ci(i, fn, n=1500):
        rng = random.Random(4)
        xs = sorted(ratio([rng.choice(mk) for _ in mk], i, fn)
                    for _ in range(n))
        return xs[int(.025 * n)], xs[int(.975 * n)]

    cols = (("actual", lambda a: a[2]),
            ("before fee", lambda a: a[2] + .07 * a[3]),
            ("PMUS no reb.", lambda a: a[2] + .07 * a[3] - .095 * a[3]),
            ("with 40% reb.", lambda a: a[2] + .4 * .07 * a[3]))
    P("\n% of dollars spent, [95% over markets]")
    P("price paid   fills    dollars share  " + "".join(
        "%-25s" % c[0] for c in cols))
    tot = sum(per[c][len(BUCKETS)][1] for c in mk)
    for i, (lo, hi) in enumerate(list(BUCKETS) + [(0, 1.01)]):
        n = sum(per[c][i][0] for c in mk)
        d = sum(per[c][i][1] for c in mk)
        if not d:
            continue
        row = " %s %6d %10s %5.1f%% " % (
            "ALL      " if i == len(BUCKETS) else "%.2f-%.2f" % (lo, hi),
            n, f"{d:,.0f}", 100 * d / tot)
        for _name, fn in cols:
            lo_, hi_ = ci(i, fn)
            row += " %+6.2f [%+5.2f,%+5.2f]   " % (ratio(mk, i, fn), lo_,
                                                    hi_)
        P(row)
    pos = sum(1 for _c, x in per_mkt if x > 0)
    P("\nmarkets with a positive result (after fee, before rebate): %d of "
      "%d   mean per market %+.2f USD   median cost %.0f USD" % (
          pos, len(per_mkt), sum(x for _c, x in per_mkt) / len(per_mkt),
          sorted(c for c, _x in per_mkt)[len(per_mkt) // 2]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
