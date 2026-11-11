"""The PMUS sports per-side funnel, read off the tape the runner writes.

Reporting only. It places nothing, changes no parameter, and never converts a
missing observation into an observed absence: a run whose trade tape was
unreadable reports TRADE_FLOW_NOT_OBSERVABLE, not zero flow.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional

LABEL = "PMUS_SURFACE_REPORT_V1"


def _tape(d: str) -> Optional[str]:
    hits = glob.glob(os.path.join(d, "pmus_surface_*.jsonl"))
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


def report(state_dir: str) -> Dict[str, Any]:
    path = _tape(state_dir)
    if not path:
        return {"label": LABEL, "ok": False, "why": "no pmus surface tape"}
    rows = _rows(path)
    start = next((r for r in rows if r.get("kind") == "session_start"), None)
    # progress and session_end rows already carry the full computed report;
    # the LAST one is the current state. Recomputing it here would be a
    # second implementation that could drift from the one that ran.
    latest = None
    for r in rows:
        if r.get("kind") in ("progress", "session_end"):
            latest = r
    unreachable = sum(1 for r in rows if r.get("kind") == "status_unreachable")
    fills = [r for r in rows if r.get("kind") == "evidenced_fill"]
    if latest is None:
        return {"label": LABEL, "ok": False, "tape": path, "rows": len(rows),
                "status_unreachable_cycles": unreachable,
                "why": "no progress row yet"}
    return {
        "label": LABEL, "ok": True, "tape": path, "rows": len(rows),
        "source": (start or {}).get("source"),
        "trade_flow_source": (start or {}).get("trade_flow_source"),
        "spec_sha256": (start or {}).get("spec_sha256"),
        "cycles": latest.get("cycles"),
        "markets": latest.get("markets"),
        "per_side": latest.get("per_side"),
        "recycling": latest.get("recycling"),
        "tape_state": latest.get("tape"),
        "verdict": latest.get("verdict"),
        "evidenced_fills": len(fills),
        "status_unreachable_cycles": unreachable,
        "labels": [
            "every per_side number is DISTINCT QUOTES that reached that stage.",
            "TRADE_FLOW 0 with an unreadable tape is NOT an observed absence; "
            "the verdict says TRADE_FLOW_NOT_OBSERVABLE.",
            "an unreachable status document scores no market that cycle.",
        ],
    }
