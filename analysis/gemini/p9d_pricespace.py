"""Is a Gemini contract's trade tape ONE price series, or two interleaved?

    python3 analysis/gemini/p9d_pricespace.py <pull.json> [<pull.json> ...]

THE TEST, and it needs no venue call and no book. Take consecutive prints that
share a timestamp to the SECOND. A real price cannot move in zero time, so a
single price series must show |dp| ~ 0 for essentially all of them. Classify
each same-second pair as

    SAME     |p2 - p1| <= 0.02      the series continued
    MIRROR   |p2 - (1-p1)| <= 0.02  the second row is the FIRST's complement
    OTHER    anything else

Prices in 0.45-0.55 are excluded because there p and 1-p are indistinguishable.

If MIRROR is a large share and OTHER is near zero, the feed is reporting both
legs of a match -- each in its own side's price space -- and a row cannot be
assigned to a side without a contemporaneous book. Every downstream quantity
that needs that assignment (complement availability, pair cost, markout, any
maker-fill simulation) is then undecidable from trades alone.

The same bimodality shows up between consecutive prints generally, but only
the same-second case rules out genuine price movement as the explanation,
which is why that is the test reported.
"""
import json
import sys
from collections import defaultdict
from fractions import Fraction as F

TOL = F(2, 100)
NEUTRAL = (F(45, 100), F(55, 100))


def classify(pull, key):
    fams = defaultdict(lambda: [0, 0, 0, 0])
    for c in pull:
        pr = sorted((list(r) for r in (c.get("prints") or [])),
                    key=lambda r: (int(r[0]), str(r[1])))
        for a, b in zip(pr, pr[1:]):
            if int(a[0]) != int(b[0]):
                continue
            pa, pb = F(str(a[2])), F(str(b[2]))
            if NEUTRAL[0] <= pa <= NEUTRAL[1]:
                continue
            v = fams[key(c)]
            v[0] += 1
            if abs(pb - pa) <= TOL:
                v[1] += 1
            elif abs(pb - (F(1) - pa)) <= TOL:
                v[2] += 1
            else:
                v[3] += 1
    return fams


def main(paths):
    print("SAME-SECOND CONSECUTIVE PRINTS -- a price cannot move in zero time")
    print(f"{'source':>14} {'family':>28} {'pairs':>7} {'SAME':>8} "
          f"{'MIRROR':>8} {'OTHER':>8} {'verdict':>14}")
    for p in paths:
        pull = json.load(open(p))
        has_cat = any(c.get("category") for c in pull)
        key = ((lambda c: (c.get("category") or "?", c.get("subcategory") or "?"))
               if has_cat else (lambda c: ("BTC", c.get("duration"))))
        for k, v in sorted(classify(pull, key).items(), key=lambda kv: -kv[1][0])[:12]:
            if v[0] < 20:
                continue
            mirror = v[2] / v[0]
            verdict = ("TWO SPACES" if mirror > 0.15 else "one series")
            print(f"{p.split('/')[-1][:14]:>14} {str(k)[:28]:>28} {v[0]:>7} "
                  f"{v[1] / v[0]:>7.1%} {mirror:>7.1%} {v[3] / v[0]:>7.1%} "
                  f"{verdict:>14}")


if __name__ == "__main__":
    main(sys.argv[1:])
