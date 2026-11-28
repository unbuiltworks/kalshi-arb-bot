#!/usr/bin/env python3
"""How long does an UNREADABLE authoritative book actually last?

EP013 ended a live window on ONE crossed tick. Turning that into a bounded
HOLD needs a bound, and a bound invented from nothing is just a different
arbitrary number. This counts the real thing: consecutive cycles whose
`inventory_known` was False, over every per-cycle recording in the tree.

`inventory_known` is the exact predicate `continuous_probe.state_from_venue`
refuses on -- not readable, OR identity-inconsistent, OR crossed -- so a run
counted here is a run the driver would have HELD for.

Runs are cut at a SYMBOL CHANGE: a contract transition is terminal in its own
right and must never be absorbed into a recovery bound.

Read-only. Prints a distribution; decides nothing.
"""
import collections
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _runs(seq):
    """seq: [(symbol, t_utc, inventory_known)] -> list of run dicts."""
    out, cur = [], None
    prev_sym = None
    for sym, t, known in seq:
        if sym != prev_sym:
            if cur:
                out.append(cur)
            cur, prev_sym = None, sym
        if known is False:
            if cur is None:
                cur = {"symbol": sym, "n": 0, "t0": t, "t1": t}
            cur["n"] += 1
            cur["t1"] = t
        else:
            if cur:
                out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return out


def _from_jsonl(path):
    seq = []
    with open(path) as fh:
        for line in fh:
            o = json.loads(line)
            if o.get("kind") != "cycle":
                continue
            b = o.get("book") or {}
            seq.append((o.get("symbol"), b.get("t_utc"),
                        b.get("inventory_known")))
    return seq


def _from_run(path):
    d = json.load(open(path))
    seq = []
    sym = (d.get("window") or {}).get("symbol")
    for dec in d.get("decisions") or []:
        p = (dec.get("book_state") or {}).get("presented") or {}
        seq.append((sym, p.get("t_utc"), p.get("inventory_known")))
    return seq


def main():
    sources = []
    for p in sorted(glob.glob(os.path.join(ROOT, "analysis/gemini/*/*.jsonl"))):
        sources.append((p, _from_jsonl(p)))
    for p in sorted(glob.glob(os.path.join(
            ROOT, "analysis/gemini/continuous_run_*.json"))):
        sources.append((p, _from_run(p)))

    all_runs, total, unknown = [], 0, 0
    for path, seq in sources:
        seq = [s for s in seq if s[2] is not None]
        if not seq:
            continue
        total += len(seq)
        unknown += sum(1 for s in seq if s[2] is False)
        rs = _runs(seq)
        for r in rs:
            r["source"] = os.path.relpath(path, ROOT)
        all_runs.extend(rs)
        print("%-58s cycles %5d  unreadable %4d  runs %3d  longest %d"
              % (os.path.relpath(path, ROOT), len(seq),
                 sum(1 for s in seq if s[2] is False), len(rs),
                 max([r["n"] for r in rs] or [0])))

    print()
    print("TOTAL cycles %d, unreadable %d (%.2f%%), runs %d"
          % (total, unknown, 100.0 * unknown / max(total, 1), len(all_runs)))
    hist = collections.Counter(r["n"] for r in all_runs)
    cum = 0
    print("\nrun length (consecutive unreadable cycles):")
    print(" len  count   cum%%")
    for n in sorted(hist):
        cum += hist[n]
        print("%4d  %5d  %6.2f%%" % (n, hist[n], 100.0 * cum / len(all_runs)))

    secs = [float(r["t1"]) - float(r["t0"]) for r in all_runs
            if r["t0"] is not None and r["t1"] is not None]
    if secs:
        print("\nwall-clock span of a run (first to last unreadable cycle):")
        print("  max %.1fs   n=%d" % (max(secs), len(secs)))
    longest = sorted(all_runs, key=lambda r: -r["n"])[:5]
    print("\nlongest runs:")
    for r in longest:
        print("  n=%d  %s  %s" % (r["n"], r["symbol"], r["source"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
