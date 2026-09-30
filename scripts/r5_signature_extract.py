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
DEFAULT_LEDGER_DIR = "/state/pmus-ws"

#: R5 submitted exactly 13 orders. This is a RECONCILIATION INVARIANT: if the
#: reconstruction finds any other number the extractor reports why and
#: produces NO comparison statistics, because a cadence computed from the
#: wrong number of actions is worse than no cadence.
R5_SUBMITTED_ORDERS = 13


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

    # The mandate is the authoritative record of what was SUBMITTED.
    # native_tick in this vintage excludes the intent list entirely, so
    # reading it reports zero actions -- which looks like the idleness
    # thesis confirmed and is really a wrong key.
    ldir = os.getenv("L5_SIG_LEDGER_DIR", DEFAULT_LEDGER_DIR)
    lpath = os.path.join(ldir, "ledger.jsonl")
    ledger = list(rows(lpath)) if os.path.exists(lpath) else []
    expect = os.getenv("L5_SIG_EXPECT_ACTIONS", "").strip()
    sig = SIG.from_mandate_records(
        recs, ledger, label="R5_LIVE_BEFORE_FIXES",
        expect_actions=(int(expect) if expect else R5_SUBMITTED_ORDERS),
        window_s=900.0)
    from_tick = SIG.from_native_journal(recs, label="R5_NATIVE_TICK_FOR_CONTRAST")
    after = digest(path)
    out = {"LABEL": "R5_PERSISTENCE_SIGNATURE", "post_capable": False,
           "journal": before, "records": len(recs), "kinds": kinds,
           "ledger": {"path": lpath, "rows": len(ledger)},
           "signature": {k: v for k, v in sig.items()
                         if k not in ("matched_series", "residual_series")},
           "series_lengths": {"matched": len(sig.get("matched_series") or []),
                              "residual": len(sig.get("residual_series") or [])},
           "native_tick_contrast": {
               "n_actions": from_tick.get("n_actions"),
               "n_ticks": from_tick.get("n_ticks"),
               "no_intent_tick_frac": from_tick.get("no_intent_tick_frac"),
               "note": ("native_tick in this vintage carries no intent list, "
                        "so these numbers are an ARTEFACT of the record "
                        "shape and must not be read as idleness")},
           "unmutated": before == after,
           "labels": ("read-only: this process opened the journal and printed. "
                      "It never writes to the file it reads.")}
    print("R5_SIGNATURE " + json.dumps(out, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
