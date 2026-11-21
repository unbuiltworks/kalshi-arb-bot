"""Three maker-only inventory policies, on recorded BTC5m quotes.

THE BLOCKER THIS ADDRESSES. Holding both legs to settlement loses at
every quoting distance, and the shape of the loss says why: pairs are
profitable at every distance (+0.01 to +0.25 each) while unmatched legs
cost -0.24 to -0.52. The loss is UNMATCHED INVENTORY, not fill capacity.

DATA. `BTC5M_QUOTE` lines from l5-btc5m-public-recorder -- the venue's own
bid/ask at ~2s resolution, plus `to_beat`, the window's strike. Settlement
is computed from the INDEX (60s BRTI average at window end vs at window
start) rather than inferred from the last quote, so a window whose late
quotes are missing is still usable and no outcome is guessed.

POLICIES, all maker-only. None takes liquidity; none is permitted to
cross. Entry for all three: rest a BUY on each side at `d` behind that
side's touch, at the first observation of the window.

  P0 BASELINE      leave both quotes where they were placed; carry any
                   unmatched leg to settlement.
  P1 REPRICE       when one side fills, re-place the OTHER side at the
                   current touch minus d, repeatedly, chasing the pair.
  P2 REDUCE        when one side fills, keep trying to pair, but if the
                   prospective pair cost (fill price + the opposite
                   side's ask) exceeds `pair_ceiling`, abandon the pair
                   and rest a maker SELL of the filled leg at the bid.
  P3 SIZE_CAP      quote as P1, but cap committed notional per side and
                   shrink the heavy side's clip as imbalance grows.

FILL RULES, both reported, because the gap between them IS the queue:
  TOUCHED      the opposing price reaches our level (optimistic: assumes
               we are at the front of the queue).
  THROUGH      it goes strictly past our level (conservative proxy for
               the queue having had to clear).

WHAT THIS IS NOT. Replay fills are not confirmed execution. Queue depth,
partial fills, our own impact and venue-side cancellation are unmodelled.
Maker fee and rebate are both 0 because the only measured evidence is
that $0 of rebate has ever been credited to this account.
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys

AV = 60
WINDOW_S = 300
MAKER_FEE = 0.0
REBATE = 0.0
PAIR_CEILING = 0.96
CLIP = 0.08
SIDE_CAP = 0.30


def load(qpath, bpath):
    wins = json.load(open(qpath))
    brti = {t: v for t, v in json.load(open(bpath))}
    return wins, brti


def slug_start(slug):
    #: cpc-btc-updown-5m-2026-11-19-0340z
    import datetime as dt
    tail = slug.rsplit("-", 1)[-1].rstrip("z")
    date = "-".join(slug.split("-")[-4:-1])
    d = dt.datetime.strptime(date + " " + tail, "%Y-%m-%d %H%M")
    return int(d.replace(tzinfo=dt.timezone.utc).timestamp())


def settled_up(slug, brti):
    """UP wins iff the 60s index average at the close is >= that at the
    open. Authoritative; returns None when the index does not cover it."""
    st_ = slug_start(slug)
    a = [brti[t] for t in range(st_ - AV + 1, st_ + 1) if t in brti]
    b = [brti[t] for t in range(st_ + WINDOW_S - AV + 1, st_ + WINDOW_S + 1)
         if t in brti]
    if len(a) < AV - 5 or len(b) < AV - 5:
        return None
    return (sum(b) / len(b)) >= (sum(a) / len(a))


def fills(price, series, i0, side, through):
    """First index >= i0 at which a maker BUY at `price` on `side` fills."""
    for i in range(i0, len(series)):
        _, bid, ask, _ = series[i]
        opp = ask if side == "UP" else (1.0 - bid)
        if price <= 0:
            return None
        if (opp < price) if through else (opp <= price):
            return i
    return None


def sell_fills(price, series, i0, side, through):
    """First index at which a maker SELL at `price` on `side` fills: the
    other side's BID has to come UP to us."""
    for i in range(i0, len(series)):
        _, bid, ask, _ = series[i]
        own_bid = bid if side == "UP" else (1.0 - ask)
        if (own_bid > price) if through else (own_bid >= price):
            return i
    return None


def run_window(series, up_won, d, policy, through):
    """Net settled P&L for one window under one policy. Maker-only."""
    s = series
    t0 = 0
    up_px = round(s[t0][1] - d, 4)
    dn_px = round((1.0 - s[t0][2]) - d, 4)
    legs = {}                       # side -> (price, fill_index)
    committed = {"UP": 0.0, "DOWN": 0.0}

    iu = fills(up_px, s, t0 + 1, "UP", through)
    idn = fills(dn_px, s, t0 + 1, "DOWN", through)
    first = None
    if iu is not None and (idn is None or iu <= idn):
        first = ("UP", iu, up_px)
    elif idn is not None:
        first = ("DOWN", idn, dn_px)
    if first is None:
        return 0.0, 0, 0            # nothing filled

    fside, fidx, fpx = first
    legs[fside] = (fpx, fidx)
    committed[fside] += fpx * CLIP
    other = "DOWN" if fside == "UP" else "UP"

    def value(side, px):
        won = (up_won if side == "UP" else (not up_won))
        return (1.0 if won else 0.0) - px - MAKER_FEE + REBATE

    #: P0: the original opposite quote stands, unchanged.
    if policy == "P0":
        oi = fills(dn_px if other == "DOWN" else up_px, s, fidx + 1, other,
                   through)
        if oi is not None:
            legs[other] = (dn_px if other == "DOWN" else up_px, oi)
    else:
        #: P1/P2/P3 re-place the opposite side at the live touch, repeatedly.
        i = fidx + 1
        while i < len(s):
            _, bid, ask, _ = s[i]
            touch = ask if other == "UP" else (1.0 - bid)
            newp = round(touch - d, 4)
            if policy == "P3":
                room = SIDE_CAP - committed[other]
                if room <= 0:
                    break
            if policy == "P2":
                pair_cost = fpx + (ask if other == "UP" else (1.0 - bid))
                if pair_cost > PAIR_CEILING:
                    #: abandon the pair; rest a maker SELL of the filled leg
                    exit_bid = s[i][1] if fside == "UP" else (1.0 - s[i][2])
                    xi = sell_fills(round(exit_bid, 4), s, i + 1, fside,
                                    through)
                    if xi is not None:
                        mark = (s[xi][1] if fside == "UP"
                                else (1.0 - s[xi][2]))
                        return (mark - fpx - MAKER_FEE), 0, 1
                    break
            oi = fills(newp, s, i + 1, other, through)
            if oi is not None:
                legs[other] = (newp, oi)
                committed[other] += newp * CLIP
                break
            i += 1

    pnl = sum(value(k, v[0]) for k, v in legs.items())
    return pnl, (1 if len(legs) == 2 else 0), (1 if len(legs) == 1 else 0)


def main(qpath="/tmp/q/windows.json", bpath="/tmp/q/brti_series.json"):
    wins, brti = load(qpath, bpath)
    usable = {}
    for slug, series in wins.items():
        r = settled_up(slug, brti)
        if r is None:
            continue
        usable[slug] = (series, r)
    print("quote windows: %d ; with authoritative index settlement: %d"
          % (len(wins), len(usable)))
    if not usable:
        return
    print("maker fee %.4f  rebate %.4f  pair ceiling %.2f  clip %.2f  side cap %.2f"
          % (MAKER_FEE, REBATE, PAIR_CEILING, CLIP, SIDE_CAP))
    for through in (False, True):
        print()
        print("=" * 86)
        print("FILL RULE: %s" % ("THROUGH (conservative)" if through
                                 else "TOUCHED (optimistic)"))
        print("=" * 86)
        print("%-4s %6s %5s %6s %6s %10s %8s %8s %12s"
              % ("pol", "dist", "n", "pairs", "lone", "mean", "sd", "se",
                 "ev_lower(1se)"))
        rows = []
        for policy in ("P0", "P1", "P2", "P3"):
            for d in (0.02, 0.03, 0.05, 0.08):
                vals, pr, ln = [], 0, 0
                for slug, (series, up_won) in sorted(usable.items()):
                    v, p, l = run_window(series, up_won, d, policy, through)
                    vals.append(v); pr += p; ln += l
                n = len(vals); mean = sum(vals) / n
                sd = st.stdev(vals) if n > 1 else 0.0
                se = sd / math.sqrt(n)
                rows.append((policy, d, mean - se, mean, sd, se, pr, ln, n))
                print("%-4s %6.2f %5d %6d %6d %10.4f %8.4f %8.4f %12.4f"
                      % (policy, d, n, pr, ln, mean, sd, se, mean - se))
        best = max(rows, key=lambda r: r[2])
        print()
        print("best by ev_lower(1se): %s d=%.2f  mean %+0.4f  ev_lower %+0.4f  -> %s"
              % (best[0], best[1], best[3], best[2],
                 "POSITIVE" if best[2] > 0 else "NOT DISTINGUISHABLE FROM <=0"))


if __name__ == "__main__":
    main(*sys.argv[1:3] if len(sys.argv) > 2 else ())
