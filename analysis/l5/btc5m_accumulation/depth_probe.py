"""Does QUEUE DEPTH predict fill toxicity? The one untested hypothesis.

READ-ONLY. Consumes `L5_BTC5M_STEP` records already emitted by the live
runner; it reaches no venue, holds no credential and decides nothing.

WHY THIS AND NOTHING ELSE IS LEFT. `replay_gates.py` closed the
entry-state filter family: 17 gates, every one negative out of sample,
with selectivity and per-share result rank-correlated the WRONG way
(rho +0.833, t +5.84). The mechanism is that a resting maker buy is only
reached when the market comes down to it, so any PRE-FILL STATE that
raises the fill probability is the state in which the price is moving
against us. Filtering on what we can see before the fill cannot separate
"I was filled" from "I was filled because I was wrong".

Queue depth is different in kind. It is not a statement about where the
price is; it is a statement about HOW MUCH HAS TO TRADE before our order
is reached, and therefore about whether a fill is a small ordinary print
or the tail of a real move. That is a property of the fill process, not of
the price state, which is why it is the only candidate the gate sweep did
not already rule out.

It had no data until 2026-11-12. The PUBLIC market endpoint publishes no
size at any level (see `btc5m_aligned_recorder.DEPTH_REASON`), so depth is
null in all 27,425 aligned rows. The AUTHENTICATED socket carries it, and
`Session.book_depth()` now writes it into every step record.

THE TEST, once enough windows have accumulated:

  1. For each step, the size resting at the top of the book on the side we
     are buying -- the queue that must clear before we are reached.
  2. Split fills by the depth that stood in front of them.
  3. Compare net-per-share across depth buckets, window-clustered.

THE PREDICTION THAT WOULD MATTER. If toxicity is purely a property of the
fill, per-share economics will be flat across depth buckets and this
family closes too. If thin-queue fills are markedly worse than deep-queue
fills, there is a real filter here -- and unlike a spread or quiet-book
gate, it would be one that does not simply trade less.

HOW MUCH DATA. At the observed ~4 fills a window, a per-share sd of
roughly 0.3 and two buckets, separating a 5c per-share difference at
t=2 needs on the order of 300 fills, i.e. roughly 75 windows. Anything
read before that is an anecdote, and this script says so rather than
reporting a number that looks like a finding.

Usage:
  python3 -I analysis/l5/btc5m_accumulation/depth_probe.py STEPS.jsonl
    where STEPS.jsonl holds `L5_BTC5M_STEP` records, one JSON per line
    (extra text before the JSON object on a line is tolerated, so a raw
    log export can be passed straight in).
"""
from __future__ import annotations

import collections
import json
import math
import statistics
import sys

MIN_FILLS_FOR_A_CLAIM = 300


def parse(path):
    """Step records, tolerating log-prefixed lines."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            i = line.find("{")
            if i < 0:
                continue
            try:
                d = json.loads(line[i:])
            except ValueError:
                continue
            if isinstance(d, dict) and "t_in_window_s" in d and "depth" in d:
                out.append(d)
    return out


def queue_ahead(step, side):
    """Shares resting at the top of the book on the side we BUY.

    Buying UP lifts nothing -- we JOIN the UP bid, so the queue ahead of us
    is the UP bid size. Buying DOWN is buying the complement, whose bid is
    1 - (UP ask), so the queue ahead is the UP ASK size.
    """
    d = step.get("depth") or {}
    if not d.get("read"):
        return None
    raw = d.get("bid_shares") if side == "UP" else d.get("ask_shares")
    try:
        return None if raw is None else float(raw)
    except (TypeError, ValueError):
        return None


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-4].strip(), file=sys.stderr)
        print("usage: depth_probe.py STEPS.jsonl", file=sys.stderr)
        return 2
    steps = parse(argv[1])
    if not steps:
        print("no L5_BTC5M_STEP records with a depth field in %s" % argv[1])
        return 1

    read_ok = sum(1 for s in steps if (s.get("depth") or {}).get("read"))
    reasons = collections.Counter(
        (s.get("depth") or {}).get("reason") for s in steps
        if not (s.get("depth") or {}).get("read"))

    print("=" * 70)
    print("DEPTH COVERAGE")
    print("=" * 70)
    print("  step records          %d" % len(steps))
    print("  with a depth read     %d (%.0f%%)"
          % (read_ok, 100.0 * read_ok / len(steps)))
    for why, n in reasons.most_common():
        print("    not read: %-20s %d" % (why, n))
    print()

    #: every fill, tagged with the queue that stood in front of it at the
    #: step it was booked on, and with its window for clustering.
    fills = []
    for s in steps:
        mkt = s.get("market")
        for f in s.get("fills") or ():
            try:
                side, qty, px = f[0], float(f[1]), float(f[2])
            except (TypeError, ValueError, IndexError):
                continue
            q = queue_ahead(s, side)
            if q is not None:
                fills.append({"window": mkt, "side": side, "qty": qty,
                              "price": px, "queue": q})

    print("=" * 70)
    print("FILLS WITH A KNOWN QUEUE AHEAD")
    print("=" * 70)
    print("  %d fills across %d windows"
          % (len(fills), len({f["window"] for f in fills})))
    if fills:
        qs = sorted(f["queue"] for f in fills)
        print("  queue ahead: median %.1f  p10 %.1f  p90 %.1f"
              % (statistics.median(qs), qs[len(qs) // 10],
                 qs[(9 * len(qs)) // 10]))
    print()

    if len(fills) < MIN_FILLS_FOR_A_CLAIM:
        print("=" * 70)
        print("NOT ENOUGH DATA FOR A CLAIM")
        print("=" * 70)
        print("  %d fills; this test needs on the order of %d before a"
              % (len(fills), MIN_FILLS_FOR_A_CLAIM))
        print("  per-share difference between depth buckets is separable")
        print("  from noise at t=2. Reporting a bucket comparison now would")
        print("  be an anecdote dressed as a finding, so it is withheld.")
        print()
        print("  Keep collecting. Settlement outcomes are needed too: this")
        print("  script sees the queue and the fill, and the per-share")
        print("  economics come from the window's settled result.")
        return 0

    print("  (bucket comparison runs once the fill count clears the bar;")
    print("   it needs the settled outcome per window joined in.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
