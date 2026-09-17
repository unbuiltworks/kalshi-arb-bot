"""Did the Gemini surface fill on BOTH sides, or only one?

THE P0 THIS ANSWERS. An earlier reading -- 17 evidenced fills, zero
complements -- is ambiguous about architecture: a one-shot controller that
buys and then hunts a complement produces the same headline as a persistent
two-sided surface whose sell side simply never traded. Those are opposite
conclusions. One says the complement logic is broken; the other says the sell
side of the book never came to us.

So every count here is SPLIT BY SIDE. A fill total that is not split cannot
distinguish them, and that is the whole question.

Six of the seven surfaces quote BUY and SELL simultaneously
(`single_level` is bid-only by construction), so the split is also a check on
the surface definitions themselves: a two-sided surface with zero SELL
placements is a defect, not a market observation.

NOTHING IS PROMOTED. `VOLUME_AHEAD_CLEARED` is the only rung that counts as a
fill, and it is still a model: queue position is unpublished, so it assumes we
joined behind everything resting. It is evidence, never venue confirmation.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List, Optional, Sequence

LABEL = "GEMINI_SURFACE_REPORT_V1"
STRICT = "VOLUME_AHEAD_CLEARED"
MARKS_S = (5, 15, 30, 60, 120, 300)


def _tape(d: str) -> Optional[str]:
    hits = glob.glob(os.path.join(d, "gemini_surface_*.jsonl"))
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


def recycling(fills: Sequence[dict]) -> Dict[str, Any]:
    """Opposite-SIDE evidenced fill within each mark of an evidenced fill.

    Both legs must be evidenced. A resting opposite quote is not recycling,
    and neither is a print we did not clear the queue for."""
    fs = sorted(fills, key=lambda f: float(f.get("t") or 0))
    out: Dict[str, Any] = {"n_fills": len(fs)}
    for m in MARKS_S:
        hit = 0
        for i, f in enumerate(fs):
            t0, side = float(f.get("t") or 0), f.get("side")
            if any(g.get("side") != side
                   and 0 < float(g.get("t") or 0) - t0 <= m
                   for g in fs[i + 1:]):
                hit += 1
        out["n_opposite_within_%ds" % m] = hit
        out["p_opposite_within_%ds" % m] = _share(hit, len(fs))
    out["labels"] = ("both legs EVIDENCED. A resting opposite quote is not an "
                     "interaction.")
    return out


def report(state_dir: str) -> Dict[str, Any]:
    path = _tape(state_dir)
    if not path:
        return {"label": LABEL, "ok": False, "why": "no gemini surface tape"}
    rows = _rows(path)
    start = next((r for r in rows if r.get("kind") == "session_start"), None)
    fills = [r for r in rows if r.get("kind") == "evidenced_fill"]
    obs = [r for r in rows if r.get("kind") == "observation"]

    by_surface: Dict[str, Dict[str, Any]] = {}
    for f in fills:
        s = by_surface.setdefault(f.get("surface"), {"BUY": 0, "SELL": 0})
        s[f.get("side", "?")] = s.get(f.get("side", "?"), 0) + 1

    # the rung ladder, as the surfaces themselves counted it
    rungs: Dict[str, int] = {}
    for o in obs:
        for k, v in (o.get("rungs") or {}).items():
            if isinstance(v, (int, float)):
                rungs[k] = rungs.get(k, 0) + int(v)

    buy = sum(1 for f in fills if f.get("side") == "BUY")
    sell = sum(1 for f in fills if f.get("side") == "SELL")
    # executable capacity and queue, both directions, from the observations
    cap: Dict[str, List[float]] = {"bid_size": [], "ask_size": []}
    for o in obs:
        t = o.get("touch") or {}
        for k, src in (("bid_size", "bid_size"), ("ask_size", "ask_size")):
            v = t.get(src)
            if isinstance(v, (int, float)):
                cap[k].append(float(v))

    def _med(xs):
        return round(sorted(xs)[len(xs) // 2], 4) if xs else None

    return {
        "label": LABEL, "ok": True, "tape": path, "rows": len(rows),
        "surfaces": (start or {}).get("surfaces"),
        "observations": len(obs),
        "evidenced_fills": len(fills),
        "fills_BUY": buy, "fills_SELL": sell,
        "two_sided": bool(buy and sell),
        "by_surface": by_surface,
        "rung_ladder": rungs,
        "recycling": recycling(fills),
        "capacity": {"median_bid_size": _med(cap["bid_size"]),
                     "median_ask_size": _med(cap["ask_size"]),
                     "n": len(cap["bid_size"])},
        "verdict": (
            "TWO_SIDED_FILLS" if (buy and sell) else
            "BUY_ONLY_FILLS" if buy else
            "SELL_ONLY_FILLS" if sell else "NO_EVIDENCED_FILLS"),
        "labels": [
            "Counts are SPLIT BY SIDE because an unsplit total cannot tell a "
            "broken complement from a sell side the market never hit.",
            "VOLUME_AHEAD_CLEARED only, and it is a model: queue position is "
            "unpublished, so it assumes we joined behind everything resting.",
            "A two-sided surface showing zero SELL placements would be a "
            "defect in the surface, not an observation about the market.",
        ],
    }
