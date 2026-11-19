"""Does ANY book-relative maker quoting distance have positive net value?

DATA: REAL QUOTES, NOT A MODEL. 499 authenticated-websocket book
snapshots the live lane itself recorded in `L5_BTC5M_STEP` across 12 real
BTC5m windows on 2026-11-17. Nothing here uses the settlement model's
p_up: the previous measurement did, and the owner was right that a
model-implied price reach is not an executable fill.

POLICY UNDER TEST. At the first snapshot of a window, rest a maker BUY on
each side at distance `d` behind that side's touch:

    UP   buy at  bid  - d
    DOWN buy at (1-ask) - d        (DOWN's touch is one minus UP's)

FILL RULE, and both readings are reported because the gap between them is
the queue:

  TOUCHED      the opposing price later reaches our level (ask <= p).
               OPTIMISTIC: it assumes we are at the front of the queue.
  TRADED_THRU  the opposing price later goes strictly BELOW our level.
               CONSERVATIVE: the level had to clear before moving on,
               which is the closest proxy for our queue position that
               quotes alone can support.

SETTLEMENT comes from the last decisive quote in the window (bid >= 0.9
=> UP won, bid <= 0.1 => DOWN won). A window whose last quote is
undecided is DISCARDED and counted, never guessed.

FEES AND REBATES. Maker fee 0 and maker rebate 0, because the only
measured evidence is that $0 of rebate was ever credited to this account
(analysis/l5/GROK_POLICY_VS_PMUS_ECONOMICS.md). Crediting an uncredited
rebate is the error that made an earlier model look profitable.

WHAT THIS CANNOT SHOW. True queue position, partial fills, our own market
impact, and whether a resting order would have been cancelled by the
venue. It bounds the question; it does not settle it.
"""
from __future__ import annotations

import collections
import json
import sys

FEE = 0.0
REBATE = 0.0
DECISIVE_HI, DECISIVE_LO = 0.9, 0.1


def windows(path):
    rows = json.load(open(path))
    by = collections.defaultdict(list)
    for r in rows:
        by[r["m"]].append(r)
    out = {}
    for m, v in by.items():
        v.sort(key=lambda r: r["t"])
        if len(v) < 8:
            continue
        out[m] = v
    return out


def outcome(path_):
    last = path_[-1]["bid"]
    if last >= DECISIVE_HI:
        return 1          # UP settled 1
    if last <= DECISIVE_LO:
        return 0          # DOWN settled 1
    return None


def simulate(path_, d, strict):
    """Returns (up_filled, dn_filled, up_px, dn_px) for distance d."""
    first = path_[0]
    up_px = round(first["bid"] - d, 4)
    dn_px = round((1.0 - first["ask"]) - d, 4)
    up_fill = dn_fill = False
    for r in path_[1:]:
        dn_ask = 1.0 - r["bid"]          # DOWN's ask is one minus UP's bid
        if up_px > 0:
            if (r["ask"] < up_px) if strict else (r["ask"] <= up_px):
                up_fill = True
        if dn_px > 0:
            if (dn_ask < dn_px) if strict else (dn_ask <= dn_px):
                dn_fill = True
    return up_fill, dn_fill, up_px, dn_px


def run(path, strict):
    wins = windows(path)
    label = "TRADED_THRU (conservative)" if strict else "TOUCHED (optimistic)"
    print("=" * 78)
    print("FILL RULE: %s" % label)
    print("=" * 78)
    print("%6s %5s %7s %7s %6s %9s %9s %9s"
          % ("dist", "wins", "UP fill", "DN fill", "pairs", "mean P&L",
             "pair P&L", "lone P&L"))
    best = None
    for d in (0.00, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.20):
        n = up = dn = pair = 0
        tot = pair_tot = lone_tot = 0.0
        for m, p in sorted(wins.items()):
            res = outcome(p)
            if res is None:
                continue
            n += 1
            uf, df, upx, dpx = simulate(p, d, strict)
            up += uf
            dn += df
            pnl = 0.0
            if uf:
                pnl += (1.0 if res == 1 else 0.0) - upx - FEE + REBATE
            if df:
                pnl += (1.0 if res == 0 else 0.0) - dpx - FEE + REBATE
            if uf and df:
                pair += 1
                pair_tot += pnl
            elif uf or df:
                lone_tot += pnl
            tot += pnl
        if not n:
            continue
        mean = tot / n
        print("%6.2f %5d %7s %7s %6d %9.4f %9.4f %9.4f"
              % (d, n, "%d/%d" % (up, n), "%d/%d" % (dn, n), pair,
                 mean, pair_tot / max(1, pair), lone_tot / max(1, n - pair)))
        if best is None or mean > best[1]:
            best = (d, mean)
    print()
    print("best distance by mean P&L: d=%.2f at %+0.4f per window" % best)
    return best


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else "/tmp/bookdata/book.json"
    w = windows(p)
    dec = sum(1 for v in w.values() if outcome(v) is not None)
    print("windows with usable quote paths : %d" % len(w))
    print("of which settlement is decisive : %d  (rest discarded, not guessed)"
          % dec)
    print("fees: maker %.4f  rebate %.4f (only measured evidence is $0 credited)"
          % (FEE, REBATE))
    print()
    opt = run(p, strict=False)
    print()
    con = run(p, strict=True)
    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    for lbl, b in (("optimistic (touched)", opt), ("conservative (traded thru)", con)):
        verdict = "POSITIVE" if b[1] > 0 else "NEGATIVE"
        print("  %-28s best d=%.2f  mean %+0.4f/window  -> %s"
              % (lbl, b[0], b[1], verdict))
