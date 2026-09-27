"""Opportunity lifetime on the 2s-sampled full-ladder tape.

Reports, for each qualifying completion opportunity, the number of CONSECUTIVE
observations it remained executable. A run of one observation means the
opportunity was seen once at 2s sampling: its true lifetime is somewhere in
(0, 4s] and is reported as UNKNOWN_SUB_2S rather than estimated.

This is what says whether faster collection/execution would be material.
"""
from __future__ import annotations
import json, collections, sys
from decimal import Decimal as D
from src import pmus_completion_shadow as S

TAPE = "data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl"
CLIPS = [D(1), D(5), D(10), D(25), D(50), D(100), D(150)]


def load():
    by = collections.defaultdict(list)
    for line in open(TAPE, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("slug"):
            by[r["slug"]].append(r)
    for v in by.values():
        v.sort(key=lambda x: str(x.get("t")))
    return by


def main():
    by = load()
    print("OPPORTUNITY LIFETIME -- 2s-sampled full-ladder tape")
    print("a run of 1 observation => true lifetime in (0,4s], reported UNKNOWN_SUB_2S\n")
    for slug, rows in sorted(by.items()):
        book = None
        states = []
        for r in rows:
            if r.get("kind") == "book":
                book = r
            elif r.get("kind") == "bbo" and r.get("two_sided") and book:
                states.append((r, book))
        if len(states) < 3:
            print("%s: %d two-sided states -- too few" % (slug[-6:], len(states)))
            continue
        print("=== %s : %d two-sided states ===" % (slug[-6:], len(states)))
        print("  %5s %8s %7s %9s %9s %9s" % (
            "clip", "gross>=", "n runs", "1-obs", "median", "max obs"))
        for clip in CLIPS:
            for c in (2, 3, 5):
                thr = D(c) / D(100)
                runs = []
                cur = 0
                for (bbo, bk) in states:
                    bid = S._d(bbo.get("bid")); ask = S._d(bbo.get("ask"))
                    if bid is None or ask is None:
                        cur = 0
                        continue
                    # acquire UP at the touch; complete DOWN through the ladder
                    acq = bid
                    lv = S.buy_levels(bk, "down")
                    filled, cost, short = S.walk_ladder(lv, clip)
                    if filled < clip or filled == 0:
                        cur = 0
                        continue
                    comp = cost / filled
                    gross = D(1) - (acq + comp)
                    if gross >= thr:
                        cur += 1
                    else:
                        if cur:
                            runs.append(cur)
                        cur = 0
                if cur:
                    runs.append(cur)
                if not runs:
                    continue
                runs.sort()
                ones = sum(1 for x in runs if x == 1)
                print("  %5s %8s %7d %9s %9d %9d" % (
                    clip, ">=%dc" % c, len(runs),
                    "%d (%.0f%%)" % (ones, 100.0 * ones / len(runs)),
                    runs[len(runs) // 2], runs[-1]))
        print()


if __name__ == "__main__":
    main()
