"""Ingest P10 rows mirrored to the collector's logs into a SEPARATE population.

    <fetch P10_ROW lines> | python3 analysis/gemini/p10j_ingest_rows.py

The persistent recorder writes to a Railway volume no other container can read.
Its one read-only channel out is the log stream, so it mirrors each raw row as
`P10_ROW {json}` on demand. This reassembles those lines into ordinary capture
files that the frozen decoder consumes unchanged.

THE TWO POPULATIONS ARE KEPT APART, DELIBERATELY.

    analysis/gemini/p10_capture/     the session-recorder population.
                                     170 decoded, gate NOT PASSED, collection
                                     ended when its substrate was reaped.
    analysis/gemini/p10_persistent/  this one. Prospective, from the 24/7
                                     recorder, and scored on its own.

`p10h_gate.py` reads only the first directory. Merging them would let a failed
gate be rescued by data collected under a different substrate after the fact,
which is the one thing the pre-registration forbids. Scoring this population is
a separate question with its own answer, and it needs its own pre-registration
before any threshold is claimed against it.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "p10_persistent")
PREFIX = "P10_ROW "
LINE = re.compile(r"P10_ROW (\{.*\})\s*$")


def ingest(stream, out_dir: str = OUT):
    os.makedirs(out_dir, exist_ok=True)
    seen, by_cohort = set(), defaultdict(list)
    n_lines = n_rows = n_dupe = n_bad = 0
    for ln in stream:
        n_lines += 1
        m = LINE.search(ln)
        if not m:
            continue
        try:
            r = json.loads(m.group(1))
        except Exception:                                      # noqa: BLE001
            n_bad += 1
            continue
        sym = str(r.get("symbol") or "")
        core = sym.split("-")[1] if "-" in sym else sym
        cohort = ("btc15m" if core.startswith("BTC15M")
                  else "btc5m" if core.startswith("BTC05M") else "other")
        if r.get("kind") == "trade":
            key = (sym, r.get("tid"))
            if r.get("tid") is None or key in seen:
                n_dupe += 1
                continue
            seen.add(key)
        by_cohort[cohort].append(r)
        n_rows += 1
    written = {}
    for cohort, rows in by_cohort.items():
        path = os.path.join(out_dir, "p10_persistent_%s.jsonl" % cohort)
        #: append: a later pull continues the population rather than
        #: replacing it, and trade rows are deduped by (symbol, tid) above
        with open(path, "a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        written[path] = len(rows)
    return {"log_lines_read": n_lines, "rows_ingested": n_rows,
            "duplicate_or_untagged_trades_skipped": n_dupe,
            "unparseable": n_bad, "files": written}


if __name__ == "__main__":
    src = open(sys.argv[1]) if len(sys.argv) > 1 else sys.stdin
    print(json.dumps(ingest(src), indent=2))
