#!/usr/bin/env python3
"""COMPLETION-HAZARD DATASET: distributions and sample size, nothing fitted.

Every residual state the replay passed through, with the features that could
plausibly say whether the complement will ever be reached. No threshold, no
model, no admission rule -- the point of this pass is to find out whether the
sample could support one at all, and to say so plainly if it cannot.
"""
from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Sequence

#: below this a split is arithmetic, not evidence
MIN_PER_CELL = 30

FEATURES = (
    "tte_s", "held_qty", "held_avg_price", "desired_complete_price",
    "complete_distance_from_touch", "queue_ahead", "spread",
    "price_velocity_per_min", "seconds_since_opposite_flow",
    "rest_duration_s",
    "opposite_prints_15s", "opposite_prints_30s", "opposite_prints_60s",
    "opposite_prints_120s",
    "opposite_contracts_15s", "opposite_contracts_30s",
    "opposite_contracts_60s", "opposite_contracts_120s",
    "aggressor_imbalance_15s", "aggressor_imbalance_30s",
    "aggressor_imbalance_60s", "aggressor_imbalance_120s",
)


def _dec(v: Any):
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _stats(vals: Sequence[D]) -> Dict[str, Any]:
    xs = sorted(vals)
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "min": str(xs[0]), "max": str(xs[-1]),
            "p25": str(xs[len(xs) // 4]), "median": str(xs[len(xs) // 2]),
            "p75": str(xs[(3 * len(xs)) // 4])}


def distributions(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for f in FEATURES:
        vals = [v for v in (_dec(r.get(f)) for r in rows) if v is not None]
        out[f] = {**_stats(vals),
                  "coverage": (round(len(vals) / len(rows), 3) if rows
                               else None)}
    return out


def _independent(rows: Sequence[Mapping[str, Any]]) -> int:
    """DISTINCT underlying states, not arm-replicated rows.

    Every arm replays the same seeds over the same cycles, so one real moment
    appears once per arm. Pooling the arms multiplies the row count by ~31
    WITHOUT adding a single new observation, and a sufficiency test run on
    that count would be counting its own pseudo-replicates. The independence
    unit is (symbol, moment, seed) and nothing finer.
    """
    return len({(r.get("symbol"), r.get("utc_ms"), r.get("seed_kind"))
                for r in rows})


def by_outcome(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The split the hazard would eventually be fitted on -- IF the sample
    ever supports it. Reported with both cell sizes on every line so an
    empty cell cannot be mistaken for a signal."""
    reach = [r for r in rows if r.get("eventual_complement_reachable")]
    miss = [r for r in rows if not r.get("eventual_complement_reachable")]
    i_reach, i_miss = _independent(reach), _independent(miss)
    both = min(i_reach, i_miss)
    out = {
        "rows_reached": len(reach), "rows_never_reached": len(miss),
        "n_reached": i_reach, "n_never_reached": i_miss,
        "smaller_cell": both,
        "pseudo_replication_factor": (round(len(reach) / i_reach, 1)
                                      if i_reach else None),
        "sufficient_for_a_split": both >= MIN_PER_CELL,
        "why": ("the smaller cell holds %d INDEPENDENT states (%d rows "
                "before de-duplicating the arms, which replay the same "
                "moments and add no observations). A split needs at least %d "
                "per cell before any difference means anything, so no "
                "threshold is proposed and none should be read into the "
                "medians below." % (both, len(reach), MIN_PER_CELL)),
        "features": {},
    }
    for f in FEATURES:
        a = [v for v in (_dec(r.get(f)) for r in reach) if v is not None]
        b = [v for v in (_dec(r.get(f)) for r in miss) if v is not None]
        out["features"][f] = {
            "reached": _stats(a), "never_reached": _stats(b),
            "median_difference": (
                str(sorted(a)[len(a) // 2] - sorted(b)[len(b) // 2])
                if a and b else None)}
    return out


def build(path: str) -> Dict[str, Any]:
    rep = json.load(open(path, encoding="utf-8"))
    # POOLED ACROSS ARMS, each row tagged with the arm it came from. Taking
    # only the native arm would make the positive cell structurally zero --
    # the native arm never reaches a complement -- and an empty cell that the
    # design guaranteed is not a finding about the venue.
    rows: List[Dict[str, Any]] = []
    for arm, traces in (rep.get("traces") or {}).items():
        for t in traces:
            for h in (t.get("hazards") or []):
                rows.append({**h, "arm": arm,
                             "seed_kind": t.get("seed_kind")})
    if not rows:
        rows = list(rep.get("hazards") or [])
    per_arm = {}
    for r in rows:
        a = per_arm.setdefault(r.get("arm"), {"states": 0, "reached": 0})
        a["states"] += 1
        a["reached"] += 1 if r.get("eventual_complement_reachable") else 0
    return {
        "per_arm": per_arm,
        "n_arms": len(per_arm),
        "label": "GEMINI_COMPLETION_HAZARD_V1",
        "source": path,
        "n_residual_states": len(rows),
        "n_symbols": len({r.get("symbol") for r in rows}),
        "distributions": distributions(rows),
        "by_outcome": by_outcome(rows),
        "rows": rows,
        "labels": ("OBSERVATIONAL. No threshold is applied, no model is "
                   "fitted and no admission decision follows from any of "
                   "this. Every fill behind these states is THEORETICAL."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="analysis/gemini/completion_model_b.json")
    ap.add_argument("--out", default="analysis/gemini/completion_hazard.json")
    a = ap.parse_args(argv)
    rep = build(a.src)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2, default=str)
    print("HAZARD states=%d symbols=%d reached=%d never=%d sufficient=%s" % (
        rep["n_residual_states"], rep["n_symbols"],
        rep["by_outcome"]["n_reached"], rep["by_outcome"]["n_never_reached"],
        rep["by_outcome"]["sufficient_for_a_split"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
