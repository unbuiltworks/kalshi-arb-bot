#!/usr/bin/env python3
"""CONTROL versus CORRECTED TIMING, read from what the lane actually wrote.

WHY THIS IS NOT IN `lanes.py`. That module's entire framing is the TTE <= 300
restriction -- it compares BTC5m and BTC15m only where the frozen policy can
act, and it is right to. The corrected arm exists precisely to act outside
that interval, so reading it through a module that truncates to 300 would
report the one thing it was built to measure as zero.

THE METRIC IS RECYCLING, NOT FIRST FILLS. A band that produces more first
fills and no complements is worse than one that produces neither, because it
manufactures residual. So the headline here is the SEQUENCE --

    OPEN fill -> inventory change -> COMPLETE fill -> matched inventory

-- counted per arm, with first fills reported beside it rather than instead
of it.

EVERYTHING HERE IS HYPOTHETICAL. No order was submitted; nothing is filled,
settled, realized or a cost basis. Gemini serves no book history, so queue
position at the moment of each print is UNOBSERVABLE and every count is an
upper bound conditional on a fill that never happened.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

CONTROL = "CONTROL"

LABELS = (
    "HYPOTHETICAL. No order existed and nothing was submitted, filled, "
    "settled or realized. Upper bound: queue position is UNOBSERVABLE on "
    "this venue, so every fill assumes our rung was reached.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _blank() -> Dict[str, Any]:
    return {"windows": 0, "rungs_rested": 0, "fills": 0, "first_fills": 0,
            "open_fills": 0, "extend_fills": 0, "complete_fills": 0,
            "windows_with_a_first_fill": 0,
            "windows_with_a_complete_fill": 0,
            "windows_with_matched_inventory": 0,
            "recycled_windows": 0, "residual_only_windows": 0,
            "matched_qty": D("0"), "bases": []}


def _band(w: Mapping[str, Any], label: str) -> Any:
    if label == CONTROL:
        return None
    return ((w.get("correction_arms") or {}).get(label) or {}
            ).get("quote_between_tte_s")


def _arm_view(w: Mapping[str, Any], label: str):
    """(hypothetical, inventory) for one arm of one window."""
    if label == CONTROL:
        return (w.get("hypothetical") or {}), (w.get("inventory") or {})
    rec = (w.get("correction_arms") or {}).get(label) or {}
    return (rec.get("hypothetical") or {}), (rec.get("inventory") or {})


def compare(windows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Per-arm totals over completed windows, never summed across arms."""
    labels: List[str] = [CONTROL]
    for w in windows:
        for lbl in (w.get("correction_arms") or {}):
            if lbl not in labels:
                labels.append(lbl)

    out: Dict[str, Dict[str, Any]] = {lbl: _blank() for lbl in labels}
    bands: Dict[str, Any] = {}
    for w in windows:
        for lbl in labels:
            if lbl != CONTROL and lbl not in (w.get("correction_arms") or {}):
                continue                       # this window predates the arm
            hyp, inv = _arm_view(w, lbl)
            a = out[lbl]
            a["windows"] += 1
            for k in ("rungs_rested", "fills", "first_fills", "open_fills",
                      "extend_fills", "complete_fills"):
                a[k] += int(hyp.get(k) or 0)
            first = int(hyp.get("first_fills") or 0)
            comp = int(hyp.get("complete_fills") or 0)
            matched = _dec(inv.get("matched_qty")) or D("0")
            a["windows_with_a_first_fill"] += bool(first)
            a["windows_with_a_complete_fill"] += bool(comp)
            a["windows_with_matched_inventory"] += bool(matched > 0)
            # THE SEQUENCE, and it needs all of it: a first fill, a COMPLETE
            # behind it, and matched inventory to show for both.
            if first and comp and matched > 0:
                a["recycled_windows"] += 1
            elif first:
                a["residual_only_windows"] += 1
            a["matched_qty"] += matched
            b = _dec(inv.get("effective_matched_pair_cost"))
            if b is not None and matched > 0 and D("0") < b <= D("2"):
                a["bases"].append(b)
            bands.setdefault(lbl, _band(w, lbl))

    rep: Dict[str, Any] = {}
    for lbl, a in out.items():
        bases = sorted(a.pop("bases"))
        n = len(bases)
        a["matched_qty"] = str(a["matched_qty"])
        a["median_pair_basis"] = (
            None if not n else str(bases[n // 2] if n % 2 else
                                   (bases[n // 2 - 1] + bases[n // 2])
                                   / D("2")))
        a["par_constrained_windows"] = sum(1 for b in bases if b <= D("1"))
        a["quote_between_tte_s"] = bands.get(lbl)
        a["recycle_rate_of_first_fill_windows"] = (
            round(a["recycled_windows"] / a["windows_with_a_first_fill"], 4)
            if a["windows_with_a_first_fill"] else None)
        rep[lbl] = a
    return {"arms": rep, "labels": LABELS,
            "metric": ("recycled_windows is the metric: a first fill, a "
                       "COMPLETE fill behind it, and matched inventory to "
                       "show for both. first_fills alone is not progress -- "
                       "a band that opens and never completes manufactures "
                       "residual.")}


def the_sequence(windows: Sequence[Mapping[str, Any]], label: str
                 ) -> List[Dict[str, Any]]:
    """Every window in which one arm completed the full sequence."""
    hits = []
    for w in windows:
        if label != CONTROL and label not in (w.get("correction_arms") or {}):
            continue
        hyp, inv = _arm_view(w, label)
        matched = _dec(inv.get("matched_qty")) or D("0")
        if not (int(hyp.get("first_fills") or 0)
                and int(hyp.get("complete_fills") or 0) and matched > 0):
            continue
        hits.append({
            "arm": label, "symbol": w.get("symbol"),
            "entry_tte_s": w.get("entry_tte_s"),
            "finished_utc": w.get("finished_utc"),
            "quote_between_tte_s": _band(w, label),
            "first_fills": hyp.get("first_fills"),
            "complete_fills": hyp.get("complete_fills"),
            "matched_qty": str(matched),
            "effective_matched_pair_cost":
                inv.get("effective_matched_pair_cost"),
            "directional_residual": inv.get("directional_residual"),
            "fills": hyp.get("rows"),
            "labels": LABELS,
        })
    return hits
