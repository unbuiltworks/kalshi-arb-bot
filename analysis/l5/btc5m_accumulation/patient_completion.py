"""After one leg fills at 0.08, rest the OTHER side at 0.88. READ-ONLY.

THE IDEA, owner 2026-11-13. The straddle's winning branch needs a full
round trip: UP must fall to 0.08 AND DOWN must fall to 0.08, i.e. UP
travels 0.08 -> 0.92. That is the ~10% case, and it is the only branch
that pays.

But a pair need not cost 0.16. If UP fills at 0.08 and we then buy DOWN at
0.88, the pair costs 0.96 and still redeems 1.00: +4c a share, LOCKED.
And DOWN's bid reaching 0.88 only requires UP to recover to about 0.12,
not to 0.92. Measured recovery to 0.10-0.12 ran 33-43%.

WHY THIS IS NOT THE CLOSED IDEA. WHY_IT_BUYS_THE_LOSER closes TAKER
completion: at the moment UP fills, DOWN's ask is ~0.91-0.93, so crossing
costs ~0.99-1.01 for a 1.00 payout -- about nothing, as a taker, which is
disabled by policy anyway. This rests a MAKER buy and waits for the price
to come to us. It is also the operating model's own COMPLETE action
("opposite-side COMPLETE is often delayed until pricing improves", target
pair cost <= 0.96).

WHY ADVERSE SELECTION DOES NOT APPLY TO THE COMPLETING LEG. Every other
leg in this program suffers it: we fill only when the price is moving
against us, so the thing we hold is worth less than we paid. A COMPLETED
PAIR pays exactly 1.00 whichever way the market settles. So being filled
"badly" on the completing leg costs nothing -- the only question is whether
the two prices sum below 1.00.

CAUSAL BY CONSTRUCTION. The completion order is placed only AFTER a fill
we have already observed, and fills by the same adverse rule as every
other leg (a resting buy fills when that side's bid falls through it).
Nothing here reads the future; contrast EXIT_RULE_RETRACTED.md, where
branching on the window's outcome produced a +5.88 c/w phantom.

Usage: python3 -I analysis/l5/btc5m_accumulation/patient_completion.py
"""
from __future__ import annotations

import gzip
import json
import math
import os
from collections import defaultdict

TAPE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "btc5m_aligned_20261112.jsonl.gz")
CANCEL_LEAD_S = 30.0
ENTRY = 0.08


def load():
    """market -> (outcome, [(t, up_bid, down_bid), ...] time-ordered)"""
    w = defaultdict(lambda: {"outcome": None, "rows": []})
    with gzip.open(TAPE, "rt") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            mkt = d.get("market")
            rem = d.get("seconds_remaining")
            if not mkt or rem is None or float(rem) < CANCEL_LEAD_S:
                continue
            rec = w[mkt]
            if d.get("settled_outcome"):
                rec["outcome"] = str(d["settled_outcome"])
            ub = (d.get("up") or {}).get("bid")
            db = (d.get("down") or {}).get("bid")
            t = d.get("t_in_window_s")
            if ub is None or db is None or t is None:
                continue
            rec["rows"].append((float(t), float(ub), float(db)))
    out = {}
    for m, r in w.items():
        if r["outcome"] in ("UP", "DOWN") and r["rows"]:
            r["rows"].sort(key=lambda x: x[0])
            out[m] = r
    return out


def simulate(rec, complete_at, not_before=0.0):
    """Both legs rest at 0.08. On the FIRST fill, also rest the opposite
    side at `complete_at`, keeping the 0.08 order up. Returns per-share
    P&L and which branch happened.

    `complete_at` None reproduces the plain straddle.
    """
    outcome = rec["outcome"]
    held = {"UP": 0, "DOWN": 0}
    cost = 0.0
    first_fill_t = None
    completed = False
    for (t, ub, db) in rec["rows"]:
        bid = {"UP": ub, "DOWN": db}
        for side in ("UP", "DOWN"):
            # the cheap 0.08 leg, one per side
            if held[side] == 0 and bid[side] <= ENTRY + 1e-12:
                held[side] = 1
                cost += ENTRY
                if first_fill_t is None:
                    first_fill_t = t
        if (complete_at is not None and first_fill_t is not None
                and t > first_fill_t and t >= not_before):
            # the patient completing leg, on whichever side is still empty
            for side in ("UP", "DOWN"):
                other = "DOWN" if side == "UP" else "UP"
                if held[side] == 1 and held[other] == 0 and not completed:
                    if bid[other] <= complete_at + 1e-12:
                        held[other] = 1
                        cost += complete_at
                        completed = True
    payout = float(held[outcome])
    pnl = payout - cost
    if held["UP"] and held["DOWN"]:
        case = "PAIR_CHEAP" if not completed else "PAIR_COMPLETED"
    elif held["UP"] or held["DOWN"]:
        case = "ONE"
    else:
        case = "NONE"
    return pnl, case


def stats(series):
    n = len(series)
    mean = sum(series) / n if n else 0.0
    if n > 1:
        var = sum((x - mean) ** 2 for x in series) / (n - 1)
        sd = math.sqrt(var)
        t = mean / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    else:
        t = float("nan")
    return 100.0 * mean, t


def main() -> int:
    w = load()
    print("windows: %d" % len(w))
    print()
    base = [simulate(r, None) for r in w.values()]
    bm, bt = stats([p for p, _ in base])
    from collections import Counter
    bc = Counter(c for _, c in base)
    print("BASELINE plain straddle at 0.08/0.08")
    print("  net %+0.2f c/w   t %+0.2f   %s" % (bm, bt, dict(bc)))
    print()
    print("WITH a patient completing leg:")
    print("%12s %8s %8s %8s %9s %7s %8s"
          % ("complete at", "paircost", "locks", "completed", "cheap pairs",
             "net c/w", "t"))
    for c in (0.92, 0.90, 0.88, 0.84, 0.80):
        res = [simulate(r, c) for r in w.values()]
        m, t = stats([p for p, _ in res])
        cc = Counter(cs for _, cs in res)
        print("%12.2f %8.2f %+8.2f %9d %11d %+7.2f %+7.2f"
              % (c, ENTRY + c, 1.0 - (ENTRY + c), cc.get("PAIR_COMPLETED", 0),
                 cc.get("PAIR_CHEAP", 0), m, t))
    print()
    print("TIME-CONDITIONAL: wait for the cheap pair, complete only after t")
    print("%6s %8s %9s %11s %9s %7s"
          % ("after", "at", "completed", "cheap pairs", "net c/w", "t"))
    for nb in (60, 120, 180, 210, 240):
        for c in (0.90, 0.88, 0.84):
            res = [simulate(r, c, nb) for r in w.values()]
            m, t = stats([p for p, _ in res])
            cc = Counter(cs for _, cs in res)
            print("%5ds %8.2f %9d %11d %+7.2f %+7.2f"
                  % (nb, c, cc.get("PAIR_COMPLETED", 0),
                     cc.get("PAIR_CHEAP", 0), m, t))
    print()
    print("'locks' is the guaranteed gain per completed pair, per share.")
    print("A completing leg can only ADD legs, never remove the 0.08 ones,")
    print("so a negative column would mean the completions themselves lose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
