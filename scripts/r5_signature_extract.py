"""R5's persistence signature, read from its journal on the volume. GET/read only.

THE BEFORE NUMBER. R5 ran at da777fd6's predecessor, before the defect-2
resting-ownership fix and before the unconditional sweep. Its journal is the
only record of what the clone's cadence looked like BEFORE those fixes, so it
is the baseline the shadow runner's windows are measured against.

READ-ONLY BY CONSTRUCTION: it opens a file and prints. No transport, no
engine, no mandate, no authorization, and nothing that can POST. It does not
write to the journal it reads -- an earlier recovery run's lesson, where
pointing a writer at the evidence would have mutated it.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Dict, Iterator, List

from src import l5_persistence_signature as SIG

DEFAULT = "/state/pmus-ws/nativeq10_r5_journal.jsonl"


def rows(path: str) -> Iterator[Dict[str, Any]]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def digest(path: str) -> Dict[str, Any]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
            n += len(chunk)
    return {"path": path, "sha256": h.hexdigest(), "bytes": n}


def main() -> int:
    path = os.getenv("L5_SIG_JOURNAL", DEFAULT)
    if not os.path.exists(path):
        print("R5_SIGNATURE " + json.dumps(
            {"error": "NO_JOURNAL", "path": path}), flush=True)
        return 0
    before = digest(path)
    recs = list(rows(path))
    kinds: Dict[str, int] = {}
    for r in recs:
        kinds[str(r.get("kind"))] = kinds.get(str(r.get("kind")), 0) + 1

    sig = SIG.from_native_journal(recs, label="R5_LIVE_BEFORE_FIXES")
    after = digest(path)
    out = {"LABEL": "R5_PERSISTENCE_SIGNATURE", "post_capable": False,
           "journal": before, "records": len(recs), "kinds": kinds,
           "signature": {k: v for k, v in sig.items()
                         if k not in ("matched_series", "residual_series")},
           "series_lengths": {"matched": len(sig["matched_series"]),
                              "residual": len(sig["residual_series"])},
           "unmutated": before == after,
           "labels": ("read-only: this process opened the journal and printed. "
                      "It never writes to the file it reads.")}
    print("R5_SIGNATURE " + json.dumps(out, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
