"""What the prospective ADAPTED surface actually did, read from its own tape.

The shadow writes to the durable volume and nothing outside the container can
read it, so this runs in-container on the supervisor's report cadence and only
the summary travels out -- the same arrangement as the prospective scorer.

IT REPORTS STAGES, NEVER FILLS. The distribution across
DESIRED -> RESTABLE -> BOOK_REACHED -> TRADE_FLOW -> EVIDENCED_FILL is the
whole point: the gap between BOOK_REACHED and EVIDENCED_FILL is where an
optimistic backtest lives, and a summary that collapsed them would hide
exactly the thing this shadow exists to measure.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional, Sequence

from src.l5_surface import evidence as EV

LABEL = "L5_PASSIVE_SURFACE_SHADOW_REPORT_V1"


def _tape(d: str) -> Optional[str]:
    hits = glob.glob(os.path.join(d, "surface_shadow_*.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def _rows(path: str) -> List[dict]:
    out: List[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _share(n: int, d: int) -> Optional[float]:
    return None if not d else round(n / d, 4)


def report(state_dir: str) -> Dict[str, Any]:
    path = _tape(state_dir)
    if not path:
        return {"label": LABEL, "ok": False, "why": "no shadow tape yet"}
    rows = _rows(path)
    boot = next((r for r in rows if r.get("kind") == "boot"), None)
    surfaces = [r for r in rows if r.get("kind") == "desired_passive_surface"]
    refused = [r for r in rows if r.get("kind") == "surface_refused"]
    ev_rows = [r for r in rows if r.get("kind") == "rung_evidence"]

    # the LAST observation of each rung, keyed by its identity: a rung is
    # re-walked every tick and only its furthest stage is its answer
    latest: Dict[tuple, dict] = {}
    for r in ev_rows:
        for x in (r.get("rows") or []):
            k = (x.get("surface_id"), x.get("outcome"), x.get("price"),
                 x.get("rung_index"))
            prev = latest.get(k)
            if prev is None or (EV.STAGES.index(x["stage"])
                                > EV.STAGES.index(prev["stage"])):
                latest[k] = x

    by_stage: Dict[str, int] = {s: 0 for s in EV.STAGES}
    fills: List[dict] = []
    for x in latest.values():
        by_stage[x["stage"]] = by_stage.get(x["stage"], 0) + 1
        if x.get("is_fill"):
            fills.append(x)

    # restability, from the rungs that never left DESIRED_SURFACE
    rest_refusals: Dict[str, int] = {}
    for x in latest.values():
        d = (x.get("detail") or {}).get(EV.RESTABLE) or {}
        if d and d.get("satisfied") is False:
            why = str(d.get("why"))[:60]
            rest_refusals[why] = rest_refusals.get(why, 0) + 1

    n = len(latest)
    reached = sum(by_stage.get(s, 0) for s in
                  (EV.BOOK_REACHED, EV.TRADE_FLOW, EV.EVIDENCED_FILL,
                   EV.INVENTORY_UPDATE, EV.OPPOSITE_RECYCLING))
    flow = sum(by_stage.get(s, 0) for s in
               (EV.TRADE_FLOW, EV.EVIDENCED_FILL, EV.INVENTORY_UPDATE,
                EV.OPPOSITE_RECYCLING))
    restable_n = sum(by_stage.get(s, 0) for s in EV.STAGES[1:])
    return {
        "label": LABEL, "ok": True, "tape": path, "rows": len(rows),
        "spec": ((boot or {}).get("spec") or {}).get("label"),
        "spec_sha256": ((boot or {}).get("spec") or {}).get("spec_sha256"),
        "surfaces_emitted": len(surfaces),
        "surfaces_refused": len(refused),
        "refusal_fields": sorted({f for r in refused
                                  for f in (r.get("blocked_by") or [])}),
        "distinct_rungs": n,
        "by_stage": by_stage,
        "restable_share": _share(restable_n, n),
        "book_reached_share": _share(reached, n),
        "trade_flow_share": _share(flow, n),
        "evidenced_fill_share": _share(by_stage.get(EV.EVIDENCED_FILL, 0), n),
        "restability_refusals": rest_refusals,
        "evidenced_fills": len(fills),
        "labels": [
            "SHADOW. No order was placed; every stage below EVIDENCED_FILL is "
            "NOT a fill and is never counted as one.",
            "book_reached_share minus trade_flow_share is the gap an "
            "optimistic backtest would have booked as fills.",
            "Stages are the FURTHEST each rung reached, not a per-tick count.",
        ],
    }
