#!/usr/bin/env python3
"""Run GEMINI_COMPLETION_MODEL_B across every initial fill. Read-only.

The 4 x 5 COMPLETE matrix, the preserve-the-resting-complement experiment,
the EXTEND veto and the late COMPLETE-only phase, all as arms of one replay
over the same seeds. Sends nothing; Model A unchanged.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.gemini_execution import completion as CM
from src.gemini_execution import large_prints as LP
from src.gemini_execution import metadata as MD
from src.gemini_execution import window_volume as WV
from src.gemini_execution.large_print_report import cycles_by_symbol, _ms
from src.gemini_execution.volume_report import _expiry_ms, captured_windows

LABEL = "GEMINI_COMPLETION_MODEL_B_V1"

#: the preserve experiment: the ONLY variable is whether the complement that
#: was already resting when the fill landed is kept or requoted.
PRESERVE_ARMS = (
    CM.Arm(price_policy=CM.NATIVE, sticky_s=0),                  # cancel/reprice
    CM.Arm(price_policy=CM.NATIVE, sticky_s=0, preserve_prior_complement_s=15),
    CM.Arm(price_policy=CM.NATIVE, sticky_s=0, preserve_prior_complement_s=30),
    CM.Arm(price_policy=CM.NATIVE, sticky_s=0, preserve_prior_complement_s=45),
    CM.Arm(price_policy=CM.NATIVE, sticky_s=0, preserve_prior_complement_s=60),
)

#: the veto is tested against the arms it would actually ride on
VETO_ARMS = tuple(
    CM.Arm(price_policy=p, sticky_s=s, extend_veto=True)
    for p, s in ((CM.NATIVE, 0), (CM.IMPROVE_1, 0), (CM.BEST_MAKER, 0),
                 (CM.BEST_MAKER, 30)))

LATE_ARMS = tuple(
    CM.Arm(price_policy=p, sticky_s=0, late_complete_phase=True)
    for p in (CM.NATIVE, CM.IMPROVE_1, CM.BEST_MAKER))


def all_arms() -> List[CM.Arm]:
    seen, out = set(), []
    for a in list(CM.matrix_arms()) + list(PRESERVE_ARMS) + list(VETO_ARMS) \
            + list(LATE_ARMS):
        if a.name not in seen:
            seen.add(a.name)
            out.append(a)
    return out


def seeds(runs: str, cyc: Dict[str, Any],
          tapes: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every initial fill we can seed from. ACTUAL venue fills first."""
    out: List[Dict[str, Any]] = []
    for p in sorted(glob.glob(runs)):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        after = d.get("after") or {}
        sym = after.get("symbol")
        for o in (after.get("orders_seen") or []):
            if str(o.get("status")) != "filled" or not sym:
                continue
            out.append({"kind": "ACTUAL", "symbol": sym,
                        "leg": "YES" if str(o.get("outcome")).lower() == "yes"
                        else "NO",
                        "qty": o.get("filledQuantity"), "price": o.get("price"),
                        "utc_ms": _ms(o.get("updatedAt")),
                        "source": d.get("authorization_id")})
    for sym, tape in tapes.items():
        for r in LP.forensic(symbol=sym, prints=tape.get("prints") or [],
                             cycles=cyc.get(sym) or [], resolution={},
                             min_size="10"):
            if not r.get("evaluable"):
                continue
            t = (r.get("quote_tests") or {}).get("NATIVE") or {}
            if not t.get("would_reach_us"):
                continue
            got = ((t.get("absorbed") or {}).get("0.01") or {}).get(
                "hypothetically_filled")
            if not got or float(got) <= 0:
                continue
            out.append({"kind": "HYPOTHETICAL", "symbol": sym,
                        "leg": r["reachable_leg"], "qty": got,
                        "price": t["price"], "utc_ms": r["print"]["utc_ms"],
                        "source": "tid %s (%s contracts)" % (
                            r["tid"], r["print"]["amount"])})
    return out


def build(*, jsonl: str, runs: str, soaks: str) -> Dict[str, Any]:
    meta = captured_windows(soaks, runs)
    cyc = cycles_by_symbol(jsonl, runs)
    tapes: Dict[str, Any] = {}
    resolutions: Dict[str, Any] = {}
    for sym in sorted(meta):
        t = WV.fetch_tape(sym)
        if not t.get("readable"):
            continue
        exp = _expiry_ms(sym)
        for p in t["prints"]:
            p["tte_s"] = (round((exp - p["utc_ms"]) / 1000.0, 3)
                          if exp else None)
        tapes[sym] = t
        try:
            resolutions[sym] = MD.resolution_for(sym, reads=1)
        except Exception as exc:                                # noqa: BLE001
            resolutions[sym] = {"verdict": "UNREADABLE",
                                "why": "%s: %s" % (type(exc).__name__, exc)}

    sd = [s for s in seeds(runs, cyc, tapes) if s["symbol"] in tapes]
    arms = all_arms()
    by_arm: Dict[str, List[Dict[str, Any]]] = {}
    for arm in arms:
        rows = []
        for s in sd:
            tr = CM.trace(symbol=s["symbol"], arm=arm, seed_leg=s["leg"],
                          seed_qty=s["qty"], seed_price=s["price"],
                          seed_ms=s["utc_ms"],
                          cycles=cyc.get(s["symbol"]) or [],
                          prints=tapes[s["symbol"]]["prints"],
                          resolution=resolutions.get(s["symbol"]) or {},
                          expiry_ms=_expiry_ms(s["symbol"]))
            tr["seed_kind"] = s["kind"]
            tr["seed_source"] = s["source"]
            rows.append(tr)
        by_arm[arm.name] = rows

    summaries = {n: CM.arm_summary(v) for n, v in by_arm.items()}
    veto = {a.name: summaries[a.name] for a in VETO_ARMS if a.name in summaries}
    return {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "n_seeds": len(sd), "n_arms": len(arms),
        "seeds": [{k: s[k] for k in
                   ("kind", "symbol", "leg", "qty", "price", "source")}
                  for s in sd],
        "matrix": {a.name: summaries[a.name] for a in CM.matrix_arms()
                   if a.name in summaries},
        "preserve": {a.name: summaries[a.name] for a in PRESERVE_ARMS
                     if a.name in summaries},
        "veto": veto,
        "late": {a.name: summaries[a.name] for a in LATE_ARMS
                 if a.name in summaries},
        "ranked": CM.rank(list(summaries.values())),
        "pareto": CM.pareto(veto, summaries),
        "hazards": [h for rows in [by_arm[CM.Arm().name]] for t in rows
                    for h in (t.get("hazards") or [])],
        "traces": by_arm,
        "labels": ("SHADOW. Every action comes from the FROZEN policy; an arm "
                   "changes only a COMPLETE price, a quote's persistence, "
                   "whether an EXTEND is acted on, or whether a COMPLETE-only "
                   "quote holds through TTE 60-30. Nothing was sent and every "
                   "fill is THEORETICAL."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl",
                    default="analysis/gemini/continuous_v1/continuous_v1.jsonl")
    ap.add_argument("--runs", default="analysis/gemini/continuous_run_*.json")
    ap.add_argument("--soaks",
                    default="analysis/gemini/continuous_shadow_soak*.json")
    ap.add_argument("--out", default="analysis/gemini/completion_model_b.json")
    a = ap.parse_args(argv)
    rep = build(jsonl=a.jsonl, runs=a.runs, soaks=a.soaks)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    top = rep["ranked"][:3]
    print("COMPLETION seeds=%d arms=%d top=%s" % (
        rep["n_seeds"], rep["n_arms"],
        json.dumps([{k: t.get(k) for k in
                     ("arm", "matched_over_gross_usd",
                      "residual_over_gross_usd")} for t in top],
                   default=str)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
