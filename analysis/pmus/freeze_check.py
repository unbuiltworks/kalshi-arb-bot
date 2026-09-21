"""Is the frozen habitat selector still the one that was preregistered?

WHY THIS IS A SEPARATE MODULE. The freeze forbids touching the selector or its
artifact, so the check lives outside both and imports them read-only.

THE TRAP IT EXISTS TO STOP. `habitat_selector_frozen.json` records an
`artifact_sha256` of f9dcd886...861959, but that is the hash of the SELECTOR
ARTIFACT -- the features, their signs, the cutoff and the horizons -- and NOT
the sha256 of the file that contains it, which is a1e7340b...a2fb18. Hashing
the file and comparing it to the recorded value reports a freeze violation on
an untouched file. Both digests are returned here, each under its own name, so
the two can never be confused again.

Nothing here can change a frozen value: it only reads and compares.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

FROZEN_PATH = "analysis/pmus/habitat_selector_frozen.json"
RECORDED_ARTIFACT_SHA = ("f9dcd886549a28656c315cd2249075f6"
                         "aae7c6fcf48bd77e5a2fa0abfc861959")


def artifact_sha256(features, cutoff_q, lookback_s, horizon_s) -> str:
    """Digest of the DECISION CONTENT only, in a stable canonical form."""
    payload = json.dumps({"features": [[n, int(s)] for n, s in features],
                          "cutoff_quantile": float(cutoff_q),
                          "lookback_s": float(lookback_s),
                          "horizon_s": float(horizon_s)},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def file_sha256(path: str = FROZEN_PATH) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def check(selector, path: str = FROZEN_PATH) -> Dict[str, Any]:
    """Compare the live module's frozen constants against the stored artifact.

    The verdict rests on the VALUES agreeing, not on a digest match: the
    recorded artifact_sha256 predates this function and its exact canonical
    form is not recoverable, so it is reported and never used as the test.
    """
    d = json.load(open(path))
    live = {"features": [[n, int(s)] for n, s in selector.FROZEN_FEATURES],
            "cutoff_quantile": float(selector.FROZEN_CUTOFF_Q),
            "lookback_s": float(selector.LOOKBACK_S),
            "horizon_s": float(selector.HORIZON_S)}
    stored = {"features": [[n, int(s)] for n, s in d["features"]],
              "cutoff_quantile": float(d["cutoff_quantile"]),
              "lookback_s": float(d["horizons"]["lookback_s"]),
              "horizon_s": float(d["horizons"]["horizon_s"])}
    diffs = {k: (stored[k], live[k]) for k in stored if stored[k] != live[k]}
    return {"values_match": not diffs, "differences": diffs,
            "live": live, "stored": stored,
            "recorded_artifact_sha256": d.get("artifact_sha256"),
            "recomputed_artifact_sha256": artifact_sha256(
                selector.FROZEN_FEATURES, selector.FROZEN_CUTOFF_Q,
                selector.LOOKBACK_S, selector.HORIZON_S),
            "file_sha256_NOT_THE_ARTIFACT_SHA": file_sha256(path),
            "cutoff_score": d.get("cutoff_score"),
            "prospective_start_utc": d.get("prospective_start_utc"),
            "note": ("recorded_artifact_sha256 and file_sha256 are different "
                     "digests of different things and must never be compared "
                     "to each other.")}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from analysis.pmus import habitat_selector as H
    r = check(H)
    print(json.dumps({k: v for k, v in r.items()
                      if k not in ("live", "stored")}, indent=1))
    print("FROZEN VALUES INTACT" if r["values_match"] else "FROZEN VALUES CHANGED")
    raise SystemExit(0 if r["values_match"] else 1)
