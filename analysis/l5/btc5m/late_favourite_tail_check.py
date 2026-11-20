"""How often does a late, high-priced favourite LOSE? An independent check
of the one state the PMUS taker study left positive.

taker_fee_adjusted.py found, on 187 PMUS windows: an ask at or above 0.97
with at most 60 s left won 82 times out of 82. Eighty-two wins cannot
establish safety: at 0.975 the fee-adjusted gain is about 2.3 cents and a
loss costs about 97.7, so break-even is a loss rate near 2.3%, and zero
losses in 82 only bounds the rate below about 3.6% (95%).

A LARGER REAL SAMPLE: Bonereaper's own buys on Polymarket's BTC 5-minute
markets -- 1,031 markets on 2026-11-17..20 and 303 on 2026-03-25 and
2026-08-30/31. For each market and each state (price paid at or above X,
seconds to expiry at most T) take the side he bought there; count how
often that side lost. One observation per market per state. Same
underlying, a different venue and settlement source, so this bounds the
PHENOMENON, not the PMUS price.

For each state: markets, losses, loss rate with a 95% upper bound, and the
PMUS fee-adjusted return per share at the average price paid, at the
observed loss rate and at its upper bound.
"""
import collections
import gzip
import json
import math
import re
import sys

K = 0.095
RECENT = "analysis/l5/bonereaper_recent/activity_20261117_20261120.jsonl.gz"
OLD = "data/traders/bonereaper/normalized/fills_in_scope.jsonl"


def upper(losses, n, z=1.96):
    """Wilson 95% upper bound on a proportion."""
    if n == 0:
        return None
    p = losses / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c + r) / d


def recent():
    rows, won = [], {}
    for line in gzip.open(RECENT, "rt"):
        r = json.loads(line)
        if r["type"] == "REDEEM" and (r.get("usdc_size") or 0) > 0:
            won[r["condition_id"]] = (r.get("outcome") or "").lower()
        elif r["type"] == "TRADE":
            m = re.match(r"btc-updown-5m-(\d+)$", r.get("slug") or "")
            if m and int(m.group(1)) + 300 < 1795203464 - 6 * 3600:
                rows.append((r["condition_id"], (r["outcome"] or "").lower(),
                             r["price"], int(m.group(1)) + 300
                             - r["timestamp"], r["size"]))
    sides = collections.defaultdict(set)
    for c, o, *_ in rows:
        sides[c].add(o)
    out = []
    for c, o, px, left, sz in rows:
        w = won.get(c)
        if w is None and len(sides[c]) > 1:
            continue
        out.append((c, px, left, 1 if o == w else 0, sz))
    return out


def old():
    out = []
    for line in open(OLD):
        f = json.loads(line)
        if f.get("asset") != "BTC" or f.get("window_minutes") != 5:
            continue
        if f.get("winning_outcome") in (None, ""):
            continue
        out.append((f["market_slug"], f["price"], f["seconds_to_expiry"],
                    1 if f["outcome"] == f["winning_outcome"] else 0,
                    f["shares"]))
    return out


def main():
    P = print
    sets = (("2026-11-17..20", recent()), ("2026-03-25 + 08-30/31", old()))
    for X in (0.90, 0.95, 0.97, 0.99):
        for T in (120, 60, 30, 10):
            P("-- paid >= %.2f with <= %d s to expiry" % (X, T))
            tot_n = tot_l = 0
            for name, fills in sets + (("BOTH", None),):
                if fills is None:
                    n, losses, px = tot_n, tot_l, tot_px / max(tot_n, 1)
                else:
                    mk = {}
                    for c, p, left, win, sz in fills:
                        if p >= X and 0 <= left <= T:
                            a = mk.setdefault(c, [0.0, 0.0, 0.0])
                            a[0] += sz
                            a[1] += sz * win
                            a[2] += sz * p
                    n = len(mk)
                    #: a market counts as a loss when most of the shares
                    #: he bought in this state were on the losing side
                    losses = sum(1 for a in mk.values() if a[1] < a[0] / 2)
                    px = (sum(a[2] for a in mk.values())
                          / max(sum(a[0] for a in mk.values()), 1e-9))
                    if name == sets[0][0]:
                        tot_n, tot_l, tot_px = 0, 0, 0.0
                    tot_n += n
                    tot_l += losses
                    tot_px += px * n
                if not n:
                    P("   %-22s no markets" % name)
                    continue
                ub = upper(losses, n)
                fee = K * px * (1 - px)

                def ev(q):
                    return (1 - q) * (1 - px) - q * px - fee
                P("   %-22s markets %4d  lost %3d  rate %.2f%% (<= %.2f%%)  "
                  "avg paid %.4f  PMUS net/share %+.4f, at the bound %+.4f"
                  % (name, n, losses, 100 * losses / n, 100 * ub, px,
                     ev(losses / n), ev(ub)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
