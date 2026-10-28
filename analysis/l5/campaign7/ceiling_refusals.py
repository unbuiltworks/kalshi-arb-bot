#!/usr/bin/env python3
"""Opening opportunities refused by the $0.96 pair-cost ceiling.

THE ANSWER FOR CAMPAIGN 6 IS ZERO, AND NOT BY LUCK.

The planner rests each leg `depth_ticks` behind its own touch, so for a book
with bid `b` and ask `a`:

    up_own = b - depth*tick
    dn_own = (1 - a) - depth*tick
    plan pair = up_own + dn_own = 1 - (a - b) - 2*depth*tick
              = 1 - spread - 2*depth*tick

At the deployed `depth_ticks=2` and this venue's 1-tick spread that is
exactly **0.9500**, every time, at every price level -- which is why all 37
of Campaign 6's live pre-submits carry `pair_cost: 0.9500` and why
`PAIR_COST_ABOVE_CAP` fired zero times across its four windows.

The ceiling only binds if `1 - spread - 2*depth*tick > 0.96`, i.e. if
`spread + 2*depth*tick < 0.04`. At tick 0.01 that needs depth <= 1 with a
1-tick spread (depth 1 gives 0.97, refused), or a zero spread. Neither
occurred.

SO THE $0.96 CEILING IS NOT WHAT LIMITS OPENING THROUGHPUT. Campaign 6's
openings were refused by `L5_TS_MIN_FIRST_LEG_PRICE` (the 0.20 first-leg
floor) and by the venue's own `MIN_PRICE`/`MAX_PRICE` band, both in the
1545z window after UP collapsed to 0.06-0.10. Reporting a refusal count
without this algebra would invite the wrong conclusion -- that relaxing
0.96 would buy throughput. It would not; it would only widen what the
ceiling never caught.

Usage:
    python3 -I ceiling_refusals.py <submits.jsonl> [decisions.jsonl]

`submits.jsonl` is a campaign tape from extract_tape.py. `decisions.jsonl`
is optional: one JSON object per line, each the `two_sided` block of an
`L5_LIVE_DECISION` record, used to count actual refusals by reason.
"""
import json
import pathlib
import sys
from collections import Counter
from decimal import Decimal as D

TICK = D("0.01")
CEILING = D("0.96")
DEPTH_TICKS = 2


def load(path):
    p = pathlib.Path(path)
    with p.open() as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    submits = load(argv[1])
    decisions = load(argv[2]) if len(argv) > 2 else []

    spreads = Counter()
    for r in submits:
        b, a = r.get("book_bid"), r.get("book_ask")
        if b and a:
            spreads[D(str(a)) - D(str(b))] += 1

    print("=" * 72)
    print("OPENINGS REFUSED BY THE $0.96 PAIR-COST CEILING")
    print("=" * 72)
    print("\nobserved spreads over %d priced openings:" % len(submits))
    binds_any = False
    for s, n in sorted(spreads.items()):
        plan = D(1) - s - 2 * D(DEPTH_TICKS) * TICK
        binds = plan > CEILING
        binds_any = binds_any or binds
        print("  spread %-7s n=%-4d plan pair %-7s ceiling %s"
              % (s, n, plan, "BINDS" if binds else "cannot bind"))

    print("\nstructural verdict: the ceiling %s at depth_ticks=%d"
          % ("CAN bind" if binds_any else "CANNOT bind", DEPTH_TICKS))
    print("  plan pair = 1 - spread - 2*depth*tick")
    print("  binds only when spread + 2*depth*tick < %s" % (D(1) - CEILING))

    reasons = Counter(str(d.get("reason") or "") for d in decisions
                      if d.get("reason"))
    refused = reasons.get("PAIR_COST_ABOVE_CAP", 0)
    print("\nactual refusals in the decision record:")
    if not decisions:
        print("  (no decision tape supplied)")
    else:
        print("  PAIR_COST_ABOVE_CAP        %d" % refused)
        for r, n in reasons.most_common():
            if r != "PAIR_COST_ABOVE_CAP":
                print("  %-26s %d" % (r, n))

    # A refused pair between the ceiling and $1.00 is gross-positive, so it
    # is the interesting case: the ceiling turned away money. Say how much,
    # and what the measured adverse drift does to it.
    gross = [D(str(d.get("pair_cost"))) for d in decisions
             if str(d.get("reason") or "") == "PAIR_COST_ABOVE_CAP"
             and d.get("pair_cost") is not None]
    inside = [p for p in gross if p < D(1)]
    print("\n  of those, pair cost < $1.00 (gross-positive, refused): %d"
          % len(inside))
    if inside:
        print("  refused pair costs: %s"
              % ", ".join(str(p) for p in sorted(inside)))
        print("  NOTE: Campaign 6 measured opening adverse drift up to")
        print("  +0.0900, so a pair admitted at 0.97 can still settle above")
        print("  $1.00. Gross-positive at plan time is not gross-positive")
        print("  at fill time, which is what the buffer is for.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
