"""WHY the wide spread and the flow never co-occur: they are the same clock.

The census found 4,030 sports markets quoting >= 5c and almost none of them
trading -- 95.7% have never traded a single share -- while the books that do
trade quote one cent. Read as two independent facts that looks like bad
luck, as though a wide market might one day attract flow and pay us the
spread.

It is not bad luck. It is one variable. Sorting `aec-` moneyline markets by
days until their game shows the spread collapsing monotonically as the event
approaches:

    5+ days out   median spread 0.2850
    game day      median spread 0.0100      -> 28.5x tighter

So the spread is wide BECAUSE nothing is trading yet, and it closes as flow
arrives. A maker cannot rest in the wide book and wait for the flow: by the
time the flow is there, the spread is one cent. And resting through that
transition is the worst available position -- you are the stale quote that
the arriving, better-informed pre-game flow picks off, which is precisely
the 6.7 c/share adverse selection the tape measures.

`spread > adverse selection` therefore has no reachable cell on this venue.
The inequality is not failed by a little in one place and nearly met in
another; the two sides of it are never measurable at the same time.

Input: the enumeration dump written by the census (one JSON row a line with
`slug` and `spread`). Read-only, offline, no network.

  python3 -I .../spread_vs_event_clock.py SPORTS_ENUM.jsonl
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import re
import statistics
import sys

#: `aec-<league>-<away>-<home>-YYYY-MM-DD`. Only the moneyline family is
#: used: one market a game, so the panel is not weighted toward whichever
#: game happens to carry the most prop markets.
SLUG = re.compile(r"^aec-([a-z0-9]+)-.*-(\d{4})-(\d{2})-(\d{2})$")
MIN_CELL = 5          # a day with fewer markets than this is not reported


def main(path: str, today: "dt.date | None" = None) -> int:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    by_day: dict = collections.defaultdict(list)
    skipped = collections.Counter()
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if r.get("spread") is None:
            skipped["no_spread_one_sided"] += 1
            continue
        m = SLUG.match(r.get("slug") or "")
        if not m:
            skipped["not_aec_moneyline"] += 1
            continue
        try:
            game = dt.date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        except ValueError:
            skipped["bad_date"] += 1
            continue
        d = (game - today).days
        if d < -1 or d > 21:
            skipped["outside_window"] += 1
            continue
        by_day[d].append(r["spread"])

    if not by_day:
        print("no aec- moneyline markets with a parseable game date in %s"
              % path)
        return 1
    print("SPREAD vs THE EVENT CLOCK -- aec- moneyline, as of %s" % today)
    print("excluded, counted: %s" % dict(skipped))
    print()
    print("%6s %9s %10s %10s %10s" % ("days", "markets", "median", "p25",
                                      "p75"))
    for d in sorted(by_day):
        v = sorted(by_day[d])
        if len(v) < MIN_CELL:
            continue
        print("%6d %9d %10.4f %10.4f %10.4f"
              % (d, len(v), statistics.median(v), v[int(.25 * len(v))],
                 v[int(.75 * len(v))]))
    near = [s for d, v in by_day.items() if d <= 0 for s in v]
    far = [s for d, v in by_day.items() if d >= 5 for s in v]
    print()
    if near and far:
        mn, mf = statistics.median(near), statistics.median(far)
        print("game day or in progress (d<=0): %4d markets, median %.4f"
              % (len(near), mn))
        print("game 5 or more days out (d>=5): %4d markets, median %.4f"
              % (len(far), mf))
        print()
        print("  %.1fx wider when nobody is trading it yet." % (mf / max(mn, 1e-9)))
        print()
        print("  The wide spread and the absent flow are ONE variable, not")
        print("  two. A maker cannot wait in the wide book for the flow to")
        print("  arrive: when it arrives the spread is a cent, and resting")
        print("  through that transition means being the stale quote that")
        print("  the arriving informed flow picks off.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
