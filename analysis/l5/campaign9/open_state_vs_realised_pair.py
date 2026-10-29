#!/usr/bin/env python3
"""Does anything observable at the OPEN predict the realised pair cost?

WHY THIS IS THE QUESTION. Per-window profit is matched contracts x
(1 - pair_cost), and matched contracts have no safe headroom (order-slot
coverage is 12% and the idle time is the ceiling refusing guaranteed
losses). So the only remaining way to raise profit PER WINDOW is a lower
pair cost.

There is enormous room in it. C7 recorded realised pair costs from 0.55 to
1.05 on the same strategy in three windows. And the OPENING gate cannot
discriminate: C7 measured `PAIR_COST_ABOVE_CAP` firing ZERO times and
showed it is structurally incapable of firing, because the planned pair is
`1 - spread - 2*depth*tick` = exactly 0.9500 at a 1-tick spread and depth 2.
Every opening is admitted on an identical 0.95 projection, and the realised
cost is then whatever the second leg turns out to cost.

So: is there a pre-trade observable -- spread, depth, queue ahead, own price
level, time into window -- that separates the pairs that came in cheap from
the ones that came in dear? If yes, a selectivity rule raises profit per
window with NO extra volume, capital, exposure or risk. If no, selectivity
is not available and the honest answer is that per-window profit cannot be
improved from this evidence.

Spearman rank correlation, not Pearson: n is small and the relationship
need not be linear for a threshold rule to work.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[3]


def pairs_from(camp: str):
    """Completed pairs with the FIRST leg's book state and the pair cost."""
    base = ROOT / "analysis" / "l5" / camp
    subs = {}
    for line in (base / "tape_submits.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            subs[r["coid"]] = r
    fills = []
    for line in (base / "tape_fills.jsonl").read_text().splitlines():
        if line.strip():
            f = json.loads(line)
            s = subs.get(f["coid"])
            if s is not None:
                fills.append((f["utc"], s, D(str(f["qty"]))))
    fills.sort()

    books = {}
    out = []
    for _, s, q in fills:
        win = s["window"]
        side = s["side"].upper()
        other = "DOWN" if side == "UP" else "UP"
        px = D(str(s["own_px"]))
        b = books.setdefault(win, {"UP": [], "DOWN": []})
        left = q
        while left > 0 and b[other]:
            oq, opx, osub = b[other][0]
            take = min(left, oq)
            out.append({
                "campaign": camp, "window": win,
                "pair_cost": str(px + opx),
                "qty": str(take),
                # the FIRST leg's state -- the only thing observable when
                # the decision to open was taken
                "open_spread": str(D(str(osub["book_ask"]))
                                   - D(str(osub["book_bid"]))),
                "open_depth": str(D(str(osub["level_depth"]))),
                "open_queue_ahead": str(D(str(osub["queue_ahead"]))),
                "open_own_px": str(D(str(osub["own_px"]))),
                "open_resting_side": osub["resting_side"],
            })
            left -= take
            if oq - take > 0:
                b[other][0] = (oq - take, opx, osub)
            else:
                b[other].pop(0)
        if left > 0:
            b[side].append((left, px, s))
    return out


def pairs_from_c8():
    """C8's joined record, under its SHORT field names.

    Same quantities, different schema: `qa`/`depth`/`spr`/`own` are the
    `queue_ahead`/`level_depth`/`spread`/`own_px` of the C6/C7 tapes. An
    earlier version of this script left C8 out to avoid mixing schemas, and
    that was the wrong call -- it cost 10 of 23 pairs on a question where n
    is the binding constraint, and the signal it appeared to find on 13
    pairs does not survive the other 10.
    """
    import json as _json
    rows = _json.loads(
        (ROOT / "analysis/l5/campaign8/c8_joined.json").read_text())
    books = {}
    out = []
    for r in sorted(rows, key=lambda r: r["tsub"]):
        if r["state"] != "FILLED":
            continue
        win, side = r["win"], r["side"].upper()
        other = "DOWN" if side == "UP" else "UP"
        px, q = D(str(r["own"])), D(str(r["qty"]))
        b = books.setdefault(win, {"UP": [], "DOWN": []})
        left = q
        while left > 0 and b[other]:
            oq, opx, osub = b[other][0]
            take = min(left, oq)
            out.append({
                "campaign": "campaign8", "window": win,
                "pair_cost": str(px + opx), "qty": str(take),
                "open_spread": str(D(str(osub["spr"]))),
                "open_depth": str(D(str(osub["depth"]))),
                "open_queue_ahead": str(D(str(osub["qa"]))),
                "open_own_px": str(D(str(osub["own"]))),
                "open_resting_side": osub["rest"]})
            left -= take
            if oq - take > 0:
                b[other][0] = (oq - take, opx, osub)
            else:
                b[other].pop(0)
        if left > 0:
            b[side].append((left, px, r))
    return out


def spearman(xs, ys):
    n = len(xs)
    if n < 4:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return round(num / (dx * dy), 4) if dx and dy else None


def main() -> int:
    rows = pairs_from("campaign6") + pairs_from("campaign7") + pairs_from_c8()
    costs = [float(r["pair_cost"]) for r in rows]
    out = {}
    for f in ("open_spread", "open_depth", "open_queue_ahead",
              "open_own_px"):
        out[f] = spearman([float(r[f]) for r in rows], costs)

    cs = sorted(costs)
    print(json.dumps({
        "pairs": rows,
        "n_pairs": len(rows),
        "pair_cost": {
            "min": cs[0] if cs else None, "max": cs[-1] if cs else None,
            "median": cs[len(cs) // 2] if cs else None,
            "mean": round(sum(cs) / len(cs), 4) if cs else None},
        "spearman_vs_realised_pair_cost": out,
        "reading": ("a |rho| that is not clearly away from 0 means the "
                    "observable does not separate cheap pairs from dear "
                    "ones, so no threshold on it can select for profit"),
        "caveat": ("23 pairs across C6/C7/C8. On C6+C7 alone (13 pairs) "
                   "open_queue_ahead showed rho -0.4959 and a 7.7c median "
                   "split; adding C8's 10 pairs takes it to -0.0935 and "
                   "reverses the split's sign once the single 0.55 outlier "
                   "is removed. The apparent signal was one observation. "
                   "That is the finding."),
    }, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
