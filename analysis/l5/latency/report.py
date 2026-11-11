"""Turn raw latency observations into the per-surface answer.

Quantiles, never means: arrival is batched and right-tailed, so a mean
describes a distribution that does not exist.
"""
from __future__ import annotations

import collections
import json
import sys
from typing import Any, Dict, List

IDENTITY = {"activity_user", "trades_user", "trades_global"}


def q(v: List[float], p: float) -> float:
    return round(v[min(len(v) - 1, int(p * len(v)))], 3) if v else float("nan")


def main(path: str) -> int:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    obs = [r for r in rows if r.get("kind") == "obs"]
    errs = [r for r in rows if r.get("kind") == "err"]
    start = next((r["at"] for r in rows if r.get("kind") == "run_start"), None)
    end = next((r["at"] for r in rows if r.get("kind") == "run_end"), None)

    print("=" * 78)
    print("L5 PUBLIC-SURFACE OBSERVATION LATENCY")
    print("=" * 78)
    if start:
        dur = (end or max(r.get("observed_at", start) for r in obs)) - start
        print(f"window {dur/60:.1f} min   observations {len(obs)}   errors {len(errs)}")

    by = collections.defaultdict(list)
    for r in obs:
        by[r["surface"]].append(r)

    print("\n%-16s %6s %8s %8s %8s %8s %8s  %s"
          % ("surface", "n", "p05", "p50", "p95", "max", "poll", "identity"))
    print("-" * 78)
    for s in ("activity_user", "trades_user", "trades_global",
              "clob_book_rest", "clob_ws"):
        v = sorted(float(x["lag_s"]) for x in by.get(s, []))
        if not v:
            print("%-16s %6d %8s %8s %8s %8s %8s  %s"
                  % (s, 0, "-", "-", "-", "-", "-",
                     "YES" if s in IDENTITY else "no"))
            continue
        pi = by[s][0].get("poll_interval_s")
        print("%-16s %6d %8.2f %8.2f %8.2f %8.2f %8s  %s"
              % (s, len(v), q(v, .05), q(v, .5), q(v, .95), v[-1],
                 f"{pi:g}s" if pi is not None else "-",
                 "YES" if s in IDENTITY else "no"))

    # clock skew, so a skewed container cannot masquerade as venue latency
    sk = sorted(float(r["clock_skew_s"]) for r in obs
                if r.get("clock_skew_s") is not None)
    if sk:
        print(f"\nclock skew vs venue Date header  n={len(sk)}  "
              f"p05={q(sk,.05)}  p50={q(sk,.5)}  p95={q(sk,.95)}")
        print("  (Date is whole-second, so |skew| under ~1s is not resolvable)")

    # batching / sawtooth: do discoveries cluster, and how far apart?
    print("\nBATCHING / SAWTOOTH")
    for s in ("activity_user", "trades_user", "clob_ws"):
        v = sorted(float(x["observed_at"]) for x in by.get(s, []))
        if len(v) < 3:
            print(f"  {s:<16} n={len(v)} too few to characterise")
            continue
        gaps = sorted(round(b - a, 3) for a, b in zip(v, v[1:]))
        same = sum(1 for g in gaps if g < 0.05)
        print(f"  {s:<16} n={len(v)}  inter-arrival p50={q(gaps,.5)}s "
              f"p95={q(gaps,.95)}s  simultaneous(<50ms)={same}/{len(gaps)}"
              f" = {100*same/len(gaps):.0f}%")

    # the join: same transactionHash seen on more than one identity surface
    print("\nCROSS-SURFACE, JOINED ON transactionHash (exact key)")
    tx = collections.defaultdict(dict)
    for r in obs:
        if r["surface"] in IDENTITY and r.get("tx"):
            cur = tx[r["tx"]].get(r["surface"])
            if cur is None or r["observed_at"] < cur:
                tx[r["tx"]][r["surface"]] = r["observed_at"]
    multi = {k: v for k, v in tx.items() if len(v) > 1}
    print(f"  L5 transactions seen at all: {len(tx)};  on >1 surface: {len(multi)}")
    pair = collections.defaultdict(list)
    for v in multi.values():
        for a in IDENTITY:
            for b in IDENTITY:
                if a < b and a in v and b in v:
                    pair[(a, b)].append(round(v[b] - v[a], 3))
    for (a, b), d in sorted(pair.items()):
        d.sort()
        print(f"  {b} minus {a}: n={len(d)} p05={q(d,.05)} p50={q(d,.5)} "
              f"p95={q(d,.95)}  (positive => {a} is faster)")
    if not pair:
        print("  no transaction appeared on two identity surfaces in this window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1
                          else "analysis/l5/latency/observations.jsonl"))
