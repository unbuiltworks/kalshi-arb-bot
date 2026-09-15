"""Were we watching? Read the supervisor's markers before believing an absence.

THE QUESTION THIS ANSWERS. An analysis that finds no fills in a window, or no
L5 action, has two possible explanations and they are not interchangeable: the
market was quiet, or we were not there. Every store in this program is
append-only, so the two look identical inside it -- a stretch with no rows.

The supervisor writes explicit markers for its own downtime, and this reads
them back into an uptime accounting plus an `observed(a, b)` predicate that any
report can call before drawing a conclusion over an interval.

A GAP IS NOT A ZERO. `observed()` returns False for an interval that overlaps
any gap, and a caller that ignores it will report our absence as the market's.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Sequence

COVERAGE = "coverage.jsonl"


def load(state_root: str) -> List[dict]:
    p = os.path.join(state_root, COVERAGE)
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def gaps(rows: Sequence[dict]) -> List[Dict[str, Any]]:
    """Every interval in which a stream was demonstrably not observing.

    Two sources, both explicit rather than inferred: a `coverage_gap` marker
    written at boot for the hole before it, and the interval between a
    `stream_exit` and the `stream_restart` that follows it.
    """
    out: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("kind") == "coverage_gap":
            out.append({"stream": r.get("stream"), "from": r.get("from_utc"),
                        "to": r.get("to_utc"), "gap_s": r.get("gap_s"),
                        "source": "boot marker"})
    open_exit: Dict[str, float] = {}
    for r in sorted(rows, key=lambda x: float(x.get("t") or 0)):
        k, st, t = r.get("kind"), r.get("stream"), float(r.get("t") or 0)
        if k == "stream_exit" and st:
            open_exit[st] = t
        elif k in ("stream_restart", "stream_start") and st in open_exit:
            t0 = open_exit.pop(st)
            out.append({"stream": st, "from": t0, "to": t,
                        "gap_s": round(t - t0, 1), "source": "exit/restart"})
    for st, t0 in open_exit.items():
        out.append({"stream": st, "from": t0, "to": None, "gap_s": None,
                    "source": "exit with no restart yet -- STILL DOWN"})
    return sorted(out, key=lambda g: (g["stream"] or "", g["from"] or 0))


def observed(rows: Sequence[dict], stream: str, a: float, b: float) -> bool:
    """Was `stream` watching for the WHOLE of [a, b]?

    False if the interval touches any gap. Deliberately strict: a partially
    observed window cannot support "nothing happened", only "nothing happened
    while we looked", and the two are different claims.
    """
    for g in gaps(rows):
        if g["stream"] != stream:
            continue
        lo = g["from"] or 0.0
        hi = g["to"] if g["to"] is not None else float("inf")
        if lo < b and a < hi:
            return False
    return True


def report(state_root: str) -> Dict[str, Any]:
    rows = load(state_root)
    if not rows:
        return {"markers": 0,
                "verdict": ("no coverage markers: uptime is UNKNOWN, and no "
                            "absence in any store may be read as a zero")}
    boots = [r for r in rows if r.get("kind") == "boot"]
    hbs = [r for r in rows if r.get("kind") == "heartbeat"]
    g = gaps(rows)
    first, last = float(rows[0]["t"]), float(rows[-1]["t"])
    per: Dict[str, Any] = {}
    for st in sorted({x.get("stream") for x in rows if x.get("stream")}):
        sg = [x for x in g if x["stream"] == st]
        down = sum(x["gap_s"] or 0.0 for x in sg)
        per[st] = {
            "gaps": len(sg),
            "downtime_s": round(down, 1),
            "longest_gap_s": max((x["gap_s"] or 0.0 for x in sg), default=0.0),
            "still_down": any(x["to"] is None for x in sg),
            "coverage_fraction": (round(1 - down / (last - first), 4)
                                  if last > first else None),
        }
    return {
        "markers": len(rows), "boots": len(boots), "heartbeats": len(hbs),
        "window_utc": [first, last], "span_s": round(last - first, 1),
        "restarts": sum(1 for r in rows if r.get("kind") == "stream_restart"),
        "exits": sum(1 for r in rows if r.get("kind") == "stream_exit"),
        "per_stream": per,
        "gaps": g[-20:],
        "labels": ("coverage_fraction is over the supervisor's own recorded "
                   "span, not over all history. An interval overlapping any "
                   "gap cannot support a claim of NO ACTION or NO FILL -- use "
                   "observed(rows, stream, a, b) before making one."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-root", default="/state/l5-research")
    a = ap.parse_args(argv)
    print(json.dumps(report(a.state_root), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
