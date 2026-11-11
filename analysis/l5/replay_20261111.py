"""Replay of the two live windows of 2026-11-11 (0400z, 0415z) under the
proposed execution changes.

WHAT THIS IS. A policy model on the venue's own recorded book path (the
authenticated feed as the service logged it, a sample every 5 to 7 seconds),
not the engine. It exists to answer one question with tonight's data: would
a shorter quote lifetime, a lower first-leg floor, a later entry cutoff, or
a hedge left resting at the completion ceiling have changed completed
pairs, stranded inventory and settled P&L?

THE POLICY, as the engine runs it:
  * a round is an UP bid and a DOWN bid, each `depth` ticks behind its
    touch, opened only when their sum is at or under the opening ceiling
    and each leg is at or above the first-leg floor;
  * up to `concurrent` rounds rest together, each a tick further behind;
  * while any filled quantity is unmatched no new round opens;
  * an unfilled order is cancelled after `life` seconds. A leg of a round
    with nothing filled is re-placed at the current touch; the missing leg
    of a half-filled round is re-placed only where it completes the pair
    at or under the completion ceiling;
  * `rest_hedge`: when the touch is too dear, leave the missing leg resting
    AT the completion ceiling instead of placing nothing;
  * nothing new opens after `entry_cutoff`; nothing rests after `terminal`.

FILLS ARE CONSERVATIVE AND NEVER INFERRED FROM A TOUCH. A bid fills only at
a sample where the market's OFFER on that side is at or below it -- the
book traded through us. Between samples nothing is assumed, so fast spikes
are missed; the baseline row shows how close that gets to what happened.

Fees are $0 (venue-confirmed on every fill) and rebates $0 (none credited).
Both windows settled DOWN. Two windows are two observations: this shows
what each change would have DONE tonight, never what it is WORTH.

Run: PYTHONPATH=. python3 analysis/l5/replay_20261111.py
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "..", "data", "l5", "recent")
WINDOWS = {"0400z": ("pmus_book_20261111_0400z.json", "DOWN"),
           "0415z": ("pmus_book_20261111_0415z.json", "DOWN")}
TICK = D("0.01")
#: what the venue confirmed tonight, for the baseline to be judged against
ACTUAL = {"0400z": {"fills": 3, "pairs": D(0), "pnl": D("0.6292"),
                    "note": "DOWN 1.18 @ 0.467 unmatched, won"},
          "0415z": {"fills": 1, "pairs": D(0), "pnl": D("-0.1408"),
                    "note": "UP 0.44 @ 0.32 unmatched, lost"}}
BASE = dict(life=120.0, floor=D("0.30"), entry_cutoff=480.0, terminal=600.0,
            concurrent=4, cash=D("7.00"), allocations=12, depth=2,
            open_ceiling=D("0.96"), complete_ceiling=D("1.00"),
            deficit=D("1.00"), rest_hedge=False, attempts=16,
            #: the engine's first orders went out 9 s (0400z) and 17 s (0415z)
            #: after the open; nothing is quoted before this
            open_after=9.0)


def load(name: str) -> List[Any]:
    with open(os.path.join(DATA, WINDOWS[name][0])) as fh:
        return [(float(t), D(b), D(a)) for t, b, a in json.load(fh)["samples"]]


def touch(side: str, bid: D, ask: D) -> D:
    """Best bid in own terms: UP's bid, or one minus UP's ask."""
    return bid if side == "UP" else D(1) - ask


def offer(side: str, bid: D, ask: D) -> D:
    """Best offer in own terms: what a taker on this side would pay."""
    return ask if side == "UP" else D(1) - bid


def run(name: str, **over: Any) -> Dict[str, Any]:
    p = dict(BASE, **over)
    path, winner = load(name), WINDOWS[name][1]
    pos = {"UP": D(0), "DOWN": D(0)}
    cost = {"UP": D(0), "DOWN": D(0)}
    rounds: List[Dict[str, Any]] = []        # each: legs {side: order|None}
    fills: List[Any] = []
    sent = cancelled = 0

    def unmatched() -> D:
        return pos["UP"] - pos["DOWN"]

    def one_sided_worst(extra: List[Any]) -> D:
        """Worst case if only one side of everything resting fills."""
        out = D(0)
        for s in ("UP", "DOWN"):
            c = cost["UP"] + cost["DOWN"]
            q = {"UP": pos["UP"], "DOWN": pos["DOWN"]}
            for r in rounds:
                o = r["legs"].get(s)
                if o:
                    c += o["qty"] * o["px"]
                    q[s] += o["qty"]
            for es, eq, ep in extra:
                if es == s:
                    c += eq * ep
                    q[s] += eq
            out = max(out, c - min(q.values()))
        return out

    for t, bid, ask in path:
        if not (D(0) < bid < ask < D(1)) and not (D(0) < bid <= ask < D(1)):
            continue
        # 1. fills: the book's offer is at or through our bid
        for r in rounds:
            for s, o in list(r["legs"].items()):
                if o and offer(s, bid, ask) <= o["px"]:
                    pos[s] += o["qty"]
                    cost[s] += o["qty"] * o["px"]
                    fills.append((round(t, 1), s, str(o["px"]), str(o["qty"])))
                    r["filled"][s] = o
                    r["legs"][s] = None
        # 2. lifetimes and the terminal
        for r in rounds:
            for s, o in list(r["legs"].items()):
                if o and (t - o["t"] >= p["life"] or t >= p["terminal"]):
                    r["legs"][s] = None
                    cancelled += 1
        # 3. re-place what a round is still owed
        if t < p["terminal"]:
            for r in rounds:
                for s in ("UP", "DOWN"):
                    if r["legs"].get(s) or s in r["filled"]:
                        continue
                    if r["attempts"][s] >= p["attempts"]:
                        continue
                    other = "DOWN" if s == "UP" else "UP"
                    px = touch(s, bid, ask) - TICK * (p["depth"] + r["level"])
                    if other in r["filled"]:                   # the hedge
                        cap = p["complete_ceiling"] - r["filled"][other]["px"]
                        if px > cap:
                            if not p["rest_hedge"]:
                                continue
                            px = cap
                    elif not r["filled"]:                      # still opening
                        if t > p["entry_cutoff"] or unmatched() != 0:
                            continue
                        if px < p["floor"]:
                            continue
                    if px < TICK:
                        continue
                    r["legs"][s] = {"px": px, "qty": r["qty"], "t": t}
                    r["attempts"][s] += 1
                    sent += 1
        # 4. open a new round
        # (the engine opens a round every second or two; a book sample is
        #  five to seven seconds, so every free slot is filled at one sample)
        while True:
            resting = sum(1 for r in rounds if any(r["legs"].values()))
            if not (p["open_after"] <= t <= p["entry_cutoff"]
                    and unmatched() == 0 and resting < p["concurrent"]):
                break
            level = resting
            up = bid - TICK * (p["depth"] + level)
            dn = (D(1) - ask) - TICK * (p["depth"] + level)
            if not (min(up, dn) >= p["floor"] and up + dn <= p["open_ceiling"]
                    and min(up, dn) >= TICK):
                break
            qty = ((p["cash"] / p["allocations"]) / (D(1) + up)
                   ).quantize(TICK, rounding="ROUND_FLOOR")
            if qty <= 0 or one_sided_worst(
                    [("UP", qty, up), ("DOWN", qty, dn)]) > p["deficit"]:
                break
            rounds.append({"qty": qty, "level": level, "filled": {},
                           "attempts": {"UP": 1, "DOWN": 1},
                           "legs": {"UP": {"px": up, "qty": qty, "t": t},
                                    "DOWN": {"px": dn, "qty": qty, "t": t}}})
            sent += 2
    matched = min(pos.values())
    spent = cost["UP"] + cost["DOWN"]
    pair_cost = ((cost["UP"] / pos["UP"]) + (cost["DOWN"] / pos["DOWN"])
                 if matched > 0 else None)
    return {"window": name, "orders": sent, "fills": len(fills),
            "cancelled": cancelled, "pairs_contracts": matched,
            "pair_cost": pair_cost, "unmatched": abs(unmatched()),
            "unmatched_side": ("UP" if unmatched() > 0 else
                               "DOWN" if unmatched() < 0 else None),
            "spent": spent, "pnl": pos[winner] - spent, "fill_log": fills}


VARIANTS = [
    ("as run tonight (120 s, floor 0.30, cutoff 480 s)", {}),
    ("quote lifetime 15 s", dict(life=15.0)),
    ("first-leg floor 0.05", dict(floor=D("0.05"))),
    ("entry until minute 13", dict(entry_cutoff=780.0, terminal=840.0)),
    ("hedge left resting at the $1.00 ceiling", dict(rest_hedge=True)),
    ("resting hedge, completion open to minute 14", dict(
        rest_hedge=True, terminal=840.0)),
    ("resting hedge at a 0.96 pair, open to minute 14", dict(
        rest_hedge=True, terminal=840.0, complete_ceiling=D("0.96"))),
    ("15 s + resting hedge", dict(life=15.0, rest_hedge=True)),
    ("all four", dict(life=15.0, floor=D("0.05"), entry_cutoff=780.0,
                      terminal=840.0, rest_hedge=True)),
    ("all four, one round at a time", dict(
        life=15.0, floor=D("0.05"), entry_cutoff=780.0, terminal=840.0,
        rest_hedge=True, concurrent=1)),
]


def main() -> Dict[str, Any]:
    out: Dict[str, Any] = {"actual": {k: {a: str(b) for a, b in v.items()}
                                      for k, v in ACTUAL.items()},
                           "variants": []}
    print("%-48s %-6s %6s %5s %7s %9s %9s %9s" % (
        "variant", "window", "orders", "fills", "pairs", "pair cost",
        "unmatched", "settled"))
    for label, over in VARIANTS:
        total = D(0)
        rows = []
        for name in WINDOWS:
            r = run(name, **over)
            total += r["pnl"]
            rows.append(r)
            print("%-48s %-6s %6d %5d %7s %9s %9s %+9.4f" % (
                label, name, r["orders"], r["fills"], r["pairs_contracts"],
                "-" if r["pair_cost"] is None
                else "%.4f" % r["pair_cost"],
                "%s %s" % (r["unmatched"], r["unmatched_side"] or ""),
                r["pnl"]))
        print("%-48s %-6s %57s %+9.4f" % ("", "both", "", total))
        out["variants"].append({"label": label, "total_pnl": str(total),
                                "windows": [{k: (str(v) if isinstance(v, D)
                                                 else v) for k, v in r.items()}
                                            for r in rows]})
    print("venue-confirmed tonight: 0400z %s (%s); 0415z %s (%s); both %+.4f"
          % (ACTUAL["0400z"]["pnl"], ACTUAL["0400z"]["note"],
             ACTUAL["0415z"]["pnl"], ACTUAL["0415z"]["note"],
             ACTUAL["0400z"]["pnl"] + ACTUAL["0415z"]["pnl"]))
    with open(os.path.join(HERE, "replay_20261111.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    return out


if __name__ == "__main__":
    main()
