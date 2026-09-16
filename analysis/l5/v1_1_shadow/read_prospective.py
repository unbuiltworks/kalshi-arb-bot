"""Cumulative V1 vs V1.1 from the shadow's own deploy logs.

The service writes rows to its volume, which we cannot reach from here; what we
CAN reach is its stdout. Every `cycle` line carries the per-cycle counts and
every 30th cycle emits a `summary` over the last 5,000 rows. This aggregates
the `cycle` lines into a cumulative picture and reports the latest `summary`
beside it, so "cumulatively and by sample maturity" is one command.

Input: a Railway get-logs JSON dump (the tool spills large results to a file),
or raw log text. Nothing here is P&L and nothing here is realized.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Iterator, List

PREFIX = "L5_V1_1_SHADOW "


def events(text: str) -> Iterator[dict]:
    """Every L5_V1_1_SHADOW payload in a log dump, whatever wraps it."""
    try:
        doc = json.loads(text)
    except ValueError:
        lines = text.splitlines()
    else:
        lines = [e.get("message", "") for stream in ("deploy", "build")
                 for e in (doc.get(stream) or [])]
    for line in lines:
        i = line.find(PREFIX)
        if i < 0:
            continue
        try:
            yield json.loads(line[i + len(PREFIX):])
        except ValueError:
            continue


def cumulative(evs: List[dict]) -> dict:
    cyc = [e for e in evs if e.get("kind") == "cycle"]
    if not cyc:
        return {"cycles": 0, "note": "no cycle events in this window"}
    obs = sum(int(e.get("rows") or 0) for e in cyc)
    prop = sum(int(e.get("proposed") or 0) for e in cyc)
    wait = sum(int(e.get("stage1_wait") or 0) for e in cyc)
    v1 = sum(int(e.get("v1_would_have") or 0) for e in cyc)
    starts = [e for e in evs if e.get("kind") == "shadow_start"]
    missing = [e for e in evs if e.get("kind") == "act_artifact_missing"]
    nostatus = [e for e in evs if e.get("kind") == "no_status"]
    return {
        "cycles_in_window": len(cyc),
        "cycle_range": [cyc[0].get("cycles"), cyc[-1].get("cycles")],
        "observations": obs,
        "universe_rows_total": cyc[-1].get("universe_rows_total"),
        "markets_per_cycle": round(obs / len(cyc), 1),
        # THE HEADLINE: the same observations, two controllers.
        "v1_proposals": v1,
        "v1_propose_rate": round(v1 / obs, 4) if obs else None,
        "v1_1_proposals": prop,
        "v1_1_propose_rate": round(prop / obs, 4) if obs else None,
        "reduction_vs_v1": round(1 - prop / v1, 4) if v1 else None,
        "stage1_waits": wait,
        "stage1_wait_rate": round(wait / obs, 4) if obs else None,
        # health, because an empty window is not a quiet market
        "restarts_in_window": len(starts),
        "act_artifact_missing": len(missing),
        "no_status_events": len(nostatus),
        "labels": ("SHADOW ONLY. Proposals are not orders, not fills, not "
                   "realized. Whether L5 would have acted is NOT OBSERVABLE "
                   "on this tape."),
    }


def main(argv: List[str]) -> int:
    from analysis.l5.v1_1_shadow import maturity as M
    text = (open(argv[1], encoding="utf-8").read() if len(argv) > 1
            else sys.stdin.read())
    evs = list(events(text))
    cyc = [e for e in evs if e.get("kind") == "cycle"
           and isinstance(e.get("t"), (int, float))]
    out: Dict[str, Any] = {"cumulative": cumulative(evs)}
    # MATURITY IS EVALUATED, NEVER ASSERTED. The criterion is preregistered in
    # MATURITY.md and a failing test is a result, not a reason to relax it.
    out["maturity"] = M.assess(
        cyc,
        restarts=sum(1 for e in evs if e.get("kind") == "shadow_start") - 1
        if any(e.get("kind") == "shadow_start" for e in evs) else 0,
        act_artifact_missing=sum(1 for e in evs
                                 if e.get("kind") == "act_artifact_missing"),
        no_status=sum(1 for e in evs if e.get("kind") == "no_status"),
        distinct_markets=(cyc[-1].get("distinct_markets") if cyc else None))
    sums = [e for e in evs if e.get("kind") == "summary"]
    out["summaries_in_window"] = len(sums)
    if sums:
        out["latest_summary"] = {k: v for k, v in sums[-1].items()
                                 if k not in ("kind", "labels")}
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
