"""B' baseline vs B' + ECONOMIC_COMPLETE on identical chronological tape.

Governed by analysis/pmus/CLIP_CAPACITY_PREREG.md and its ECONOMIC_COMPLETE
addendum. Thresholds, clip grid, continuation variants and the DEV/OOS split
are the preregistered ones and are NOT re-chosen here.

Read-only: replays a recorded tape. No transport, no order verb, no credential.

Every accepted contract independently satisfies the completion economics
including its own level's taker fee -- see marginal_complete(). A depth
shortfall is UNKNOWN and is counted as a missed qualifying state; liquidity is
never manufactured.

No future information reaches a decision. Settlement is read once, by the
scorer, after each walk.

STATUS: UNVERIFIED. This script has never been executed or syntax-checked.

Usage:
    python3 scripts/run_economic_complete.py '{"<slug>": "up", ...}'
"""
from __future__ import annotations

import collections
import json
import os
import sys
from decimal import Decimal as D

# Make `src` importable when invoked as `python3 scripts/run_economic_complete.py`
# from the repo root. Without this the import below raises
# "ModuleNotFoundError: No module named 'src'" -- observed directly with the
# sibling checkpoint runner, which needed PYTHONPATH=. to run at all.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_completion_shadow as S  # noqa: E402

TAPE = "data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl"

# PREREGISTERED. Not re-chosen, not tuned from the exploratory sample.
CLIPS = [D(1), D(5), D(10), D(25), D(50), D(100), D(150)]
THETAS = [D("0.96"), D("0.98"), D("1.00"), D("1.02"), D("1.05")]
VARIANTS = ("A", "B", "C")
TARGET = D(5)


def load(path=TAPE):
    by = collections.defaultdict(list)
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return by
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("slug"):
                by[r["slug"]].append(r)
    for v in by.values():
        v.sort(key=lambda x: str(x.get("t")))
    return by


def two_sided(rows):
    return sum(1 for r in rows
               if r.get("kind") == "bbo" and r.get("two_sided"))


def completeness(rows):
    """Tape completeness, so a thin result is never mistaken for a real one."""
    tot = len(rows)
    err = sum(1 for r in rows
              if str(r.get("kind") or "").endswith("error"))
    return {"rows": tot, "error_rows": err,
            "error_rate": (float(err) / tot) if tot else 0.0}


def f(v, w=9, p=4):
    if v is None:
        return " " * max(w - 3, 0) + "n/a"
    return ("%%%d.%df" % (w, p)) % float(v)


def score(rows, clip, theta, variant, winner):
    return S.run_window(rows, clip=clip, theta=theta, winner=winner,
                        variant=variant)


def capacity_line(r, target=TARGET):
    """Output 4: what $5/window would require, and whether it was reached."""
    net = r.get("net")
    matched = r.get("cumulative_matched") or D(0)
    edge = r.get("median_edge_per_contract")
    need = None
    if edge is not None and edge > 0:
        need = target / edge
    return {
        "net": net,
        "matched": matched,
        "edge": edge,
        "contracts_needed_for_5": need,
        "meets_target": (net is not None and net >= target),
        "shortfall_ratio": (float(net / target) if net is not None else None),
    }


def main(argv):
    outcomes = json.loads(argv[1]) if len(argv) > 1 else {}
    by = load()
    if not by:
        print("no tape at %s" % TAPE)
        return 1
    print("B' vs B' + ECONOMIC_COMPLETE -- preregistered grid, marginal ladder")
    print("clip size is NOT throughput: cumMatch is the recycled total.\n")

    for slug, rows in sorted(by.items()):
        ts_ = two_sided(rows)
        comp = completeness(rows)
        win = outcomes.get(slug)
        print("=== %s  two-sided=%d  rows=%d  err=%.1f%%  winner=%s ===" % (
            slug[-6:], ts_, comp["rows"], 100 * comp["error_rate"], win))
        if ts_ < 5:
            print("  too few two-sided states -- not scored\n")
            continue
        if not win:
            print("  UNSETTLED -- mechanism counters only, P&L withheld")

        hdr = ("  %4s %-5s %-3s | %9s %9s %9s %9s | %8s %8s %8s | "
               "%5s %5s %5s %5s | %9s")
        print(hdr % ("q", "th", "v", "net", "matchNet", "residPL", "grossMrg",
                     "fees", "maxResid", "cumMatch",
                     "elig", "att", "exec", "miss", "medEdge"))

        for clip in CLIPS:
            b = score(rows, clip, None, "A", win)
            print(hdr % (clip, "-", "B'",
                         f(b["net"]), f(b["matched_pnl"]), f(b["resid_pnl"]),
                         f(b["gross_margin"]), f(b["fees"], 8),
                         f(b["max_simultaneous_residual"], 8, 2),
                         f(b["cumulative_matched"], 8, 2),
                         "-", "-", "-", "-", "-"))
            for th in THETAS:
                for v in VARIANTS:
                    r = score(rows, clip, th, v, win)
                    if r["eligible_states"] == 0:
                        continue
                    print(hdr % (
                        clip, th, v,
                        f(r["net"]), f(r["matched_pnl"]), f(r["resid_pnl"]),
                        f(r["gross_margin"]), f(r["fees"], 8),
                        f(r["max_simultaneous_residual"], 8, 2),
                        f(r["cumulative_matched"], 8, 2),
                        r["eligible_states"], r["attempted_states"],
                        r["executable_states"], r["missed_qualifying"],
                        f(r["median_edge_per_contract"], 9, 5)))

        if win:
            print("\n  --- $5/window capacity, per clip (best theta/variant) ---")
            print("  %4s %10s %10s %11s %14s %8s" % (
                "q", "net", "cumMatch", "medEdge", "need for $5", "meets?"))
            for clip in CLIPS:
                best = None
                for th in THETAS:
                    for v in VARIANTS:
                        r = score(rows, clip, th, v, win)
                        if r["net"] is None:
                            continue
                        if best is None or r["net"] > best["net"]:
                            best = r
                if best is None:
                    continue
                cap = capacity_line(best)
                print("  %4s %10s %10s %11s %14s %8s" % (
                    clip, f(cap["net"], 10), f(cap["matched"], 10, 2),
                    f(cap["edge"], 11, 5),
                    f(cap["contracts_needed_for_5"], 14, 0),
                    "YES" if cap["meets_target"] else "no"))
            print("  NOTE: 'best theta/variant' here is DEBUG VISIBILITY on a"
                  " single window.")
            print("  It is NOT selection -- DEV/OOS selection is the"
                  " preregistered procedure")
            print("  and runs only at the checkpoints.")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
