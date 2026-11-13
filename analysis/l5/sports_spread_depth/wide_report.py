"""The wide-spread sports cells, read depth-first. Is `spread x depth` money?

852 of 1,329 two-sided open sports markets quote a spread of 5c or wider,
against BTC5m's 1c. So spread is NOT the scarce thing on sports -- which
means the whole question is what sits behind those quotes.

This reads the depth ladder for a bounded sample of them and reports the
three numbers that decide it together:

  pair_size   min(bid_touch, ask_touch). A pair needs BOTH legs, so the
              size a pair can do is the SMALLER touch, never the average.
              Averaging the sides is how a 1-share book with a 7,000-share
              other side reads as liquid.
  capacity    spread x pair_size, in dollars. The gross a single filled
              pair earns. This is the product the instruction asked for.
  traded      the market's LIFETIME sharesTraded. A market quoting 45c on
              one share a side, having traded sixteen shares all game, is
              not an opportunity -- there is no counterparty to pair with.
              Capacity without flow is a quote, not an edge.

WHY LIFETIME VOLUME AND NOT A RATE. A rate needs two reads of the same
market; this sweep reads each market once, by design, so it can cover
hundreds instead of dozens. Lifetime sharesTraded is what one read gives,
and for the question "does anything trade here at all" it is sufficient and
not an approximation of anything. The per-minute rate is on the recorded
tape instead, where consecutive reads exist.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys

TAG = "SPORTS_SPREAD_DEPTH_WIDE"


def num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def main(path: str) -> int:
    rows = []
    unreadable = collections.Counter()
    for line in open(path, encoding="utf-8"):
        if not line.startswith(TAG + " "):
            continue
        r = json.loads(line[len(TAG) + 1:])
        if not r.get("readable"):
            unreadable["unreadable:" + str(r.get("why"))] += 1
            continue
        if not r.get("depth_available"):
            unreadable["no_depth:" + str(r.get("why_depth"))] += 1
            continue
        rows.append(r)
    if not rows:
        print("no readable %s rows in %s -- an empty sweep, not a null "
              "result" % (TAG, path))
        return 1

    scored = []
    for r in rows:
        bq, aq, sp = (num(r.get("bid_touch_qty")), num(r.get("ask_touch_qty")),
                      num(r.get("spread")))
        if sp is None:
            continue
        #: a missing touch is NOT a zero touch. One side unquoted means no
        #: pair is possible at all, which is reported as its own category
        #: rather than folded in as size 0 and averaged.
        if bq is None or aq is None:
            scored.append({**r, "pair_size": None, "capacity": None})
            continue
        ps = min(bq, aq)
        scored.append({**r, "pair_size": ps, "capacity": sp * ps})

    pairable = [s for s in scored if s["pair_size"] is not None]
    print("WIDE-SPREAD SPORTS CELLS: is spread x depth money?")
    print("read %d wide markets   pairable (both touches quoted) %d"
          % (len(scored), len(pairable)))
    if unreadable:
        print("excluded, counted: %s" % dict(unreadable))
    print()

    if not pairable:
        print("no market in the sample had BOTH touches quoted: a pair is")
        print("not placeable here at any size.")
        return 0

    sp = [s["spread"] for s in pairable]
    ps = [s["pair_size"] for s in pairable]
    cap = [s["capacity"] for s in pairable]
    tr = [num(s.get("shares_traded")) or 0.0 for s in pairable]

    def q(xs, p):
        xs = sorted(xs)
        return xs[min(int(p * len(xs)), len(xs) - 1)]

    print("%-16s %9s %9s %9s %9s %9s"
          % ("", "min", "p25", "median", "p75", "max"))
    for name, xs, nd in (("spread", sp, 4), ("pair_size (sh)", ps, 2),
                         ("capacity ($)", cap, 4),
                         ("lifetime traded", tr, 1)):
        print("%-16s %9.*f %9.*f %9.*f %9.*f %9.*f"
              % (name, nd, min(xs), nd, q(xs, .25), nd,
                 statistics.median(xs), nd, q(xs, .75), nd, max(xs)))
    print()

    #: THE JOINT TEST. A cell is only worth anything if the spread is wide,
    #: a pair is placeable at a size worth placing, AND the market actually
    #: trades. Each clause removes a different illusion, so they are
    #: reported as a funnel rather than a single pass/fail.
    print("FUNNEL, each clause removing a different illusion")
    steps = [
        ("wide spread >= 5c", lambda s: s["spread"] >= 0.05),
        ("  + both touches quoted", lambda s: s["pair_size"] is not None),
        ("  + pair size >= 10 sh", lambda s: (s["pair_size"] or 0) >= 10),
        ("  + pair size >= 50 sh", lambda s: (s["pair_size"] or 0) >= 50),
        ("  + lifetime traded >= 100 sh",
         lambda s: (num(s.get("shares_traded")) or 0) >= 100),
        ("  + lifetime traded >= 1000 sh",
         lambda s: (num(s.get("shares_traded")) or 0) >= 1000),
    ]
    cur = scored
    for label, fn in steps:
        cur = [s for s in cur if fn(s)]
        print("  %-32s %5d markets" % (label, len(cur)))
    print()
    if cur:
        print("SURVIVORS: wide spread, placeable pair, and real flow")
        print("  %-52s %7s %9s %9s %11s"
              % ("slug", "spread", "pair_sh", "cap_$", "traded"))
        for s in sorted(cur, key=lambda x: -(x["capacity"] or 0))[:25]:
            print("  %-52s %7.3f %9.2f %9.4f %11.1f"
                  % (s["slug"][:52], s["spread"], s["pair_size"],
                     s["capacity"], num(s.get("shares_traded")) or 0.0))
        caps = [s["capacity"] for s in cur]
        print()
        print("  median capacity $%.4f a pair across %d survivors"
              % (statistics.median(caps), len(cur)))
    else:
        print("NO SURVIVORS. Every wide-spread market in the sample failed")
        print("on depth or on flow: the spread is visible and not takeable.")
    print()
    #: the counter-illusion, stated explicitly because it is the one the
    #: handoff's "19-26c spread" reading fell into.
    big = [s for s in pairable if s["spread"] >= 0.15]
    if big:
        thin = sum(1 for s in big if (s["pair_size"] or 0) < 10)
        quiet = sum(1 for s in big
                    if (num(s.get("shares_traded")) or 0) < 100)
        print("THE WIDEST QUOTES, specifically (spread >= 15c): %d markets"
              % len(big))
        print("  of which pair size under 10 shares: %d (%.0f%%)"
              % (thin, 100.0 * thin / len(big)))
        print("  of which lifetime volume under 100 shares: %d (%.0f%%)"
              % (quiet, 100.0 * quiet / len(big)))
        print("  median pair size %.2f sh   median lifetime traded %.1f sh"
              % (statistics.median([s["pair_size"] for s in big]),
                 statistics.median([num(s.get("shares_traded")) or 0.0
                                    for s in big])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
