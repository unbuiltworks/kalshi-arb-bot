"""L5 MODEL DRIFT CHECK (OWNER Phase 1 directive 2026-09-06, item 7).

Continuously ingested L5 public activity is compared against the FROZEN
v2 behavioral model (analysis/l5/l5_action_model_v2.json): for every
base state with enough post-freeze transitions, the relation masses
observed in the new period vs the frozen model's masses. This module
NEVER changes the live model; it reports drift so any model change can
be proposed and validated out of sample first.

Inputs: the canonical sequence CSV rebuilt from the raw cache (after a
read-only re-harvest) and the frozen model. Output: JSON report.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "analysis" / "l5" / "l5_action_model_v2.json"
OUT_PATH = ROOT / "analysis" / "l5" / "l5_model_drift_check.json"
RELATIONS = ("open", "extend", "complete", "sell")
MIN_STATE_N = 30
DRIFT_L1_FLAG = 0.20          # per-state L1 that is worth a look
DRIFT_WEIGHTED_FLAG = 0.10    # transition-weighted L1 that would warrant
                              # an OOS-validated model refresh proposal


def relation_masses(counts: Mapping[str, Any]) -> tuple[dict, int]:
    agg: dict = defaultdict(float)
    for action, c in (counts or {}).items():
        agg[str(action).split("|")[0]] += float(c)
    n = int(sum(agg.values()))
    return ({r: round(agg.get(r, 0.0) / n, 4) if n else 0.0 for r in RELATIONS}, n)


def transitions_since(transitions: Iterable[Mapping[str, Any]],
                      since_epoch: float) -> list:
    return [t for t in transitions if float(t.get("t0") or 0) >= since_epoch]


def compare(model: Mapping[str, Any], new_transitions: list,
            since_utc: str, min_state_n: int = MIN_STATE_N) -> dict:
    base = model.get("base") or {}
    new_counts: dict = defaultdict(lambda: defaultdict(int))
    for t in new_transitions:
        new_counts[t["base"]][t["action"]] += 1
    per_state, weighted, wn = [], 0.0, 0
    unseen_states = 0
    for state, counts in new_counts.items():
        new_m, n = relation_masses(counts)
        if n < min_state_n:
            continue
        if state not in base:
            unseen_states += 1
            continue
        old_m, n_old = relation_masses(base[state])
        l1 = round(sum(abs(new_m[r] - old_m[r]) for r in RELATIONS), 4)
        per_state.append({"state": state, "n_new": n, "n_frozen": n_old,
                          "l1": l1, "new": new_m, "frozen": old_m,
                          "flag": l1 >= DRIFT_L1_FLAG})
        weighted += l1 * n
        wn += n
    per_state.sort(key=lambda r: -r["l1"])
    wl1 = round(weighted / wn, 4) if wn else None
    return {
        "kind": "l5_model_drift_check",
        "t": datetime.now(timezone.utc).isoformat(),
        "frozen_model_sha": model.get("_sha256_16"),
        "frozen_generated_utc": model.get("generated_utc"),
        "new_period_since_utc": since_utc,
        "new_transitions": len(new_transitions),
        "states_scored": len(per_state),
        "states_new_unseen_by_model": unseen_states,
        "weighted_l1": wl1,
        "flagged_states": [r for r in per_state if r["flag"]][:20],
        "top_states": per_state[:10],
        "verdict": ("NO_MATERIAL_DRIFT" if wl1 is not None and wl1 < DRIFT_WEIGHTED_FLAG
                    else ("DRIFT_CANDIDATE_VALIDATE_OOS" if wl1 is not None
                          else "INSUFFICIENT_NEW_DATA")),
        "rule": "the live model is never changed here; a DRIFT_CANDIDATE "
                "verdict is a proposal to refit and validate OUT OF SAMPLE "
                "before any promotion",
    }


def build_new_transitions(since_epoch: float) -> list:
    """Rebuild v2-style transitions from the canonical sequence and keep
    windows that START at/after ``since_epoch`` (uses the v2 builder's
    own state/action keys so the comparison is like-for-like)."""
    from src.polymarket_l5 import action_model_v2 as v2
    out = []
    for rows in v2._windows():
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        w0 = v2._f(rows[0]["timestamp"])
        if w0 < since_epoch:
            continue
        for i, (prev, nxt) in enumerate(zip(rows, rows[1:])):
            resid = v2._f(prev["residual_qty_signed"])
            out.append({
                "t0": w0,
                "base": v2.state_key(v2._f(prev["price"]),
                                     v2._f(nxt["seconds_to_settlement"]), resid),
                "action": v2.action_key(
                    v2._relation(resid, nxt["outcome"], nxt["side"]),
                    v2._f(nxt["quantity"])),
            })
    return out


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    since_utc = argv[0] if argv else "2026-09-01T00:00:00+00:00"
    since_epoch = datetime.fromisoformat(since_utc).timestamp()
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    new = build_new_transitions(since_epoch)
    rep = compare(model, new, since_utc)
    OUT_PATH.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("new_transitions", "states_scored",
                                          "weighted_l1", "verdict")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
