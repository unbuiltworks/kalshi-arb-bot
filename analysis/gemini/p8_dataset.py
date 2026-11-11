"""The one way to load Gemini tape for analysis. Corrections are not optional.

Every consumer goes through `load()`. That is the point: the two P8 defects
were both cases of an analysis reading the raw pull directly and deciding for
itself what a duration was and what a print was. There is now one answer, it
lives in `src.gemini_execution.tape_normalize`, and a script that wants the
uncorrected rows has to ask for them by name (`raw=True`) instead of getting
them by default.

Raw pulls on disk are never rewritten. Normalization happens on load and keeps
its own provenance, so any figure computed here can be traced back to the rows
the venue actually returned.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Sequence, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..")))
from src.gemini_execution import tape_normalize as TN          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FORWARD = os.path.join(HERE, "forward")


def load(paths: Sequence[str], *, raw: bool = False, dedupe: bool = False
         ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Read one or more raw pulls and return corrected contracts + a report.

    `dedupe` is False since the P8 audit suspended the mirrored-twin rule
    (see `tape_normalize`). Pass True to reproduce figures published before
    2026-09-22.
    """
    rows: List[Dict[str, Any]] = []
    for p in paths:
        with open(p) as fh:
            rows += json.load(fh)
    if raw:
        return rows, {"label": "RAW -- UNCORRECTED", "contracts_in": len(rows)}
    return TN.normalize_all(rows, dedupe=dedupe)


def load_forward(*, raw: bool = False, dedupe: bool = False):
    """The sealed 09-23-onward set. Development must not read this."""
    if not os.path.isdir(FORWARD):
        return [], {"label": "no forward days collected yet"}
    days = sorted(f for f in os.listdir(FORWARD) if f.endswith(".json"))
    return load([os.path.join(FORWARD, d) for d in days], raw=raw,
                dedupe=dedupe)


if __name__ == "__main__":
    rows, rep = load(sys.argv[1:])
    rep.pop("quarantined_detail", None)
    print(json.dumps(rep, indent=2))
