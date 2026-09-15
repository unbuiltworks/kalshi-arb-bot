"""Score the frozen ADAPTED arms against the preregistered gate. Reports only.

QUALIFICATION EXCLUDES INCENTIVES. Trading economics only. Any prospective
rebate or reward we can measure on our OWN authenticated record is carried in a
separate block and is never netted into the gate.

STRICT QUEUE EVIDENCE. A fill counts at VOLUME_AHEAD_CLEARED and nowhere else.
The weaker criteria are reported for contrast so the difference stays visible,
and can never promote a quote to a fill.

It computes a verdict and stops. It does not size anything, does not enable
live trading, and does not alter any policy.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

from src.l5_shadow import adapted_policy as AP

GATE = "ADAPTED/prospective_bounded_residual_v1"
STRICT = "VOLUME_AHEAD_CLEARED"
MIN_WINDOWS = 20                 # A-1
MAX_RESIDUAL_FRACTION = 0.50     # A-3
MIN_MATCHED_RETAINED = 0.70      # A-4
MAX_DRAWDOWN_FRACTION = 0.40     # A-6


def _dd(pnls: Sequence[float]) -> float:
    peak = cum = worst = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return worst


def score_arm(windows: Sequence[dict], *, arm: str,
              control_matched: Optional[float] = None) -> Dict[str, Any]:
    """windows: settled per-window rows for ONE arm on ONE venue."""
    two = [w for w in windows if w.get("two_sided_evidenced")]
    settled = [w for w in two if w.get("settled")]
    matched_pnl = sum(float(w.get("matched_pnl") or 0.0) for w in settled)
    resid_pnl = sum(float(w.get("residual_pnl") or 0.0) for w in settled)
    total = matched_pnl + resid_pnl - sum(float(w.get("fees") or 0.0)
                                          for w in settled)
    matched_qty = sum(float(w.get("matched_qty") or 0.0) for w in two)
    dd = _dd([float(w.get("total_pnl") or 0.0) for w in settled])
    hindsight = sum(int(w.get("hindsight_fills") or 0) for w in windows)

    checks: Dict[str, Any] = {}
    checks["A-1_windows_two_sided"] = {
        "value": len(two), "threshold": MIN_WINDOWS, "ok": len(two) >= MIN_WINDOWS}
    checks["A-2_settled_trading_pnl"] = {
        "value": round(total, 4), "threshold": "> 0", "ok": total > 0}
    lim = MAX_RESIDUAL_FRACTION * matched_pnl
    checks["A-3_residual_within_half_of_matched"] = {
        "residual_pnl": round(resid_pnl, 4),
        "matched_pnl": round(matched_pnl, 4),
        "allowed_floor": round(-lim, 4) if matched_pnl > 0 else None,
        "ok": bool(matched_pnl > 0 and resid_pnl > -lim),
        "why": ("the residual may not consume more than half the matched edge; "
                "this is the proposition the experiment exists to test")}
    checks["A-4_matched_retained_vs_control"] = {
        "value": (round(matched_qty / control_matched, 4)
                  if control_matched else None),
        "threshold": MIN_MATCHED_RETAINED,
        "ok": bool(control_matched and
                   matched_qty / control_matched >= MIN_MATCHED_RETAINED)}
    checks["A-5_no_hindsight_fills"] = {
        "value": hindsight, "threshold": 0, "ok": hindsight == 0}
    checks["A-6_max_drawdown"] = {
        "value": round(dd, 4),
        "allowed_floor": (round(-MAX_DRAWDOWN_FRACTION * total, 4)
                          if total > 0 else None),
        "ok": bool(total > 0 and dd >= -MAX_DRAWDOWN_FRACTION * total)}
    checks["A-7_frozen_configuration"] = {
        "arm": arm, "caps": list(AP.CAPS), "label": AP.LABEL,
        "ok": arm in AP.ARMS}

    failed = [k for k, v in checks.items() if not v["ok"]]
    if not checks["A-1_windows_two_sided"]["ok"]:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif failed:
        verdict = "FAIL"
    else:
        verdict = "PASS"
    return {
        "gate": GATE, "arm": arm, "verdict": verdict, "failed": failed,
        "fill_criterion": STRICT,
        "windows_observed": len(windows), "windows_two_sided": len(two),
        "settled_windows": len(settled),
        "matched_pnl": round(matched_pnl, 4),
        "residual_pnl": round(resid_pnl, 4),
        "total_trading_pnl": round(total, 4),
        "matched_qty": round(matched_qty, 4),
        "max_drawdown": round(dd, 4),
        "checks": checks,
        "incentives": {
            "included_in_qualification": False,
            "note": ("prospective attributable rebates/rewards are recorded "
                     "separately where our own authenticated record makes them "
                     "measurable, and are never netted into this gate"),
        },
        "labels": ("shadow only; evidenced fills are backed by public prints "
                   "under the strictest queue criterion and are NOT venue-"
                   "confirmed fills. Nothing here is realized profit."),
    }


def report(out_dir: str) -> Dict[str, Any]:
    """Read whatever the collector has written and score every arm."""
    rows: List[dict] = []
    path = os.path.join(out_dir, "adapted_windows.jsonl")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    ctrl = [w for w in rows if w.get("arm") == "control"]
    cm = sum(float(w.get("matched_qty") or 0.0) for w in ctrl) or None
    venues = sorted({w.get("venue") for w in rows if w.get("venue")}) or ["PMUS"]
    out: Dict[str, Any] = {"gate": GATE, "rows": len(rows), "by_venue": {}}
    for v in venues:
        out["by_venue"][v] = {
            arm: score_arm([w for w in rows
                            if w.get("arm") == arm and w.get("venue") == v],
                           arm=arm, control_matched=cm)
            for arm in AP.ARMS}
    verdicts = [s["verdict"] for v in out["by_venue"].values()
                for s in v.values()]
    passing = [(v, a) for v, arms in out["by_venue"].items()
               for a, s in arms.items() if s["verdict"] == "PASS"]
    out["overall"] = (
        "PASS" if passing else
        ("INSUFFICIENT_EVIDENCE"
         if all(x == "INSUFFICIENT_EVIDENCE" for x in verdicts) or not verdicts
         else "FAIL"))
    # tightest passing cap, because the objective is bounded residual
    out["selected"] = (sorted(passing, key=lambda va: AP.arm_cap(va[1]))[0]
                       if passing else None)
    out["next_step"] = ("live-probe preflight (G-P8 plus the $1-$5 bounded "
                        "checklist)" if passing else
                        "keep collecting; change nothing")
    return out
