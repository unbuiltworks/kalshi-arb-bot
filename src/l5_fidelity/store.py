"""Immutable prospective prediction store. APPEND ONLY -- never rewritten.

THE EXPERIMENT ONLY MEANS SOMETHING IF THE PREDICTION CANNOT MOVE. A prediction
is written and sealed BEFORE the action it predicts is observable; the outcome
arrives later as a SEPARATE record keyed to it. Nothing ever edits a prediction
in place, and the seal is a content hash so a silent edit is detectable rather
than merely forbidden.

A model change does not amend history: it gets a new model_version and scores
prospectively from that point. Records carry their version, so a scoreboard can
never mix a prediction made by one model with the credit for another.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, Iterator, Optional

SCHEMA = "l5_fidelity_prediction/v1"
PRED = "predictions.jsonl"
OUT = "outcomes.jsonl"


def seal(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def freeze(out_dir: str, *, model_version: str, cohort: str,
           state: Dict[str, Any], prediction: Dict[str, Any],
           horizon_s: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """Seal one prediction. Returns the record actually written."""
    os.makedirs(out_dir, exist_ok=True)
    t = time.time() if now is None else now
    body = {
        "schema": SCHEMA,
        "model_version": model_version,
        "cohort": cohort,
        "prediction_utc": t,
        "horizons_s": horizon_s,
        "state": state,
        "prediction": prediction,
    }
    rec = {**body, "prediction_id": seal(body),
           "labels": ("FROZEN BEFORE THE OUTCOME. This record is never "
                      "rewritten; the actual arrives as a separate row keyed "
                      "by prediction_id.")}
    with open(os.path.join(out_dir, PRED), "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return rec


def resolve(out_dir: str, *, prediction_id: str, actual: Dict[str, Any],
            disagreement: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """Append the observed outcome. NEVER touches the prediction row."""
    rec = {"schema": SCHEMA + "/outcome", "prediction_id": prediction_id,
           "resolved_utc": (time.time() if now is None else now),
           "actual": actual, "disagreement": disagreement}
    with open(os.path.join(out_dir, OUT), "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return rec


def _read(path: str) -> Iterator[Dict[str, Any]]:
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load(out_dir: str) -> Dict[str, Any]:
    """Predictions joined to outcomes, with the seal re-verified on every row."""
    preds, tampered = {}, []
    for r in _read(os.path.join(out_dir, PRED)):
        body = {k: r[k] for k in ("schema", "model_version", "cohort",
                                  "prediction_utc", "horizons_s", "state",
                                  "prediction") if k in r}
        if seal(body) != r.get("prediction_id"):
            tampered.append(r.get("prediction_id"))
            continue
        preds[r["prediction_id"]] = r
    outs = {}
    for r in _read(os.path.join(out_dir, OUT)):
        # first writer wins: a later row can never overwrite a resolution
        outs.setdefault(r["prediction_id"], r)
    return {"predictions": preds, "outcomes": outs, "tampered": tampered,
            "n_predictions": len(preds), "n_resolved": len(outs),
            "labels": ("seal re-verified per row; a prediction whose content no "
                       "longer hashes to its id is reported as tampered and "
                       "EXCLUDED rather than silently scored")}
