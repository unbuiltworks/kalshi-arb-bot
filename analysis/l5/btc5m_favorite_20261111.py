"""The owner's favorite rule on REAL recorded BTC 5-minute quotes.

Input: the public best bid / ask of the Up outcome, sampled about every
2 s by `btc5m_public_recorder.py`, with the venue's settlement.

Rule: every 20 s, if the favored outcome's best bid is 0.51-0.90, rest a
0.10-share buy `depth` ticks below that bid until the next reassessment;
whatever fills is held to settlement; if the favorite flips, the rule buys
the new favorite and the old shares stay (so both sides can be held).

Only fills are modelled, two ways: "through" (the ask trades below our
price) and "touch" (the ask reaches it). The recorder has top of book only,
so queue position and partial fills cannot be modelled: a fill is all of
0.10 or nothing. Maker fee zero; no rebate at this size. Replay, not
realized. Read the window count before the result.

usage: python3 analysis/l5/btc5m_favorite_20261111.py QUOTES.jsonl
"""
from __future__ import annotations

import collections
import json
import statistics as st
import sys

EVERY_S, CLIP, BAND = 20.0, 0.10, (0.51, 0.90)


def load(path):
    rows, settled = collections.defaultdict(list), {}
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if "settled" in r:
            p = json.loads(r["settled"])
            settled[r["slug"]] = "UP" if p[0] == "1" else "DOWN"
        elif r.get("bid") and r.get("ask") and 0 <= r.get("t", -1) <= 300:
            rows[r["slug"]].append((r["t"], float(r["bid"]), float(r["ask"])))
    return {s: sorted(v) for s, v in rows.items()
            if s in settled and len(v) >= 60
            and v[0][0] <= 30 and v[-1][0] >= 240}, settled


def favorite(bid, ask):
    return ("UP", bid, ask) if bid >= 1 - ask else ("DOWN", 1 - ask, 1 - bid)


def run(win, settled, depth, fill):
    out = []
    for slug in sorted(win):
        pos, cost, resting, nxt, flips, last = \
            {"UP": 0.0, "DOWN": 0.0}, 0.0, None, 0.0, 0, None
        for t, b, a in win[slug]:
            if resting:
                side, px = resting
                ask_now = a if side == "UP" else 1 - b
                if (ask_now < px - 1e-9) if fill == "through" else \
                        (ask_now <= px + 1e-9):
                    pos[side] += CLIP
                    cost += CLIP * px
                    resting = None
            if t >= nxt:
                nxt, resting = t + EVERY_S, None
                side, fb, _ = favorite(b, a)
                if last and side != last:
                    flips += 1
                last = side
                if BAND[0] <= fb <= BAND[1]:
                    resting = (side, round(fb - depth * 0.01, 2))
        shares = pos["UP"] + pos["DOWN"]
        out.append({"slug": slug, "shares": shares, "cost": cost,
                    "flips": flips, "both": pos["UP"] > 0 and pos["DOWN"] > 0,
                    "pnl": pos[settled[slug]] - cost})
    return out


def main(argv):
    win, settled = load(argv[1])
    n = len(win)
    print(f"complete, settled BTC 5m windows recorded: {n}")
    if n == 0:
        return 0
    cells = collections.defaultdict(lambda: [0, 0, 0.0])
    for slug, rows in win.items():
        seen = set()
        for t, b, a in rows:
            slot = int(t // 30)
            if slot in seen:
                continue
            seen.add(slot)
            side, fb, fa = favorite(b, a)
            if BAND[0] <= fb <= BAND[1]:
                c = cells[min(int((fb - 0.50) * 10), 3)]
                c[0] += 1
                c[1] += settled[slug] == side
                c[2] += fa
    print("favorite, one sample per window per 30 s:")
    for k in sorted(cells):
        m, w, sa = cells[k]
        print(f"   bid {0.5 + k / 10:.2f}-{0.6 + k / 10:.2f}  samples {m:4d}"
              f"  won {w / m:5.1%}  mean ask {sa / m:.3f}")
    for depth in (2, 3, 4):
        for fill in ("through", "touch"):
            r = run(win, settled, depth, fill)
            f = [x for x in r if x["shares"] > 0]
            if not f:
                print(f"   {depth} ticks below, {fill}: no fills")
                continue
            pnl = [x["pnl"] for x in f]
            sh = sum(x["shares"] for x in f)
            print(f"   {depth} ticks below the bid, {fill:7s} fills: windows "
                  f"with a fill {len(f)}/{n}  shares {sh:5.1f}  ahead "
                  f"{sum(1 for p in pnl if p > 0)}/{len(f)}  total "
                  f"{sum(pnl):+.3f}  per share {100 * sum(pnl) / sh:+.1f}c  "
                  f"worst window {min(pnl):+.3f}  best {max(pnl):+.3f}  "
                  f"both sides held {sum(1 for x in f if x['both'])}  "
                  f"favorite flips {sum(x['flips'] for x in r)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
