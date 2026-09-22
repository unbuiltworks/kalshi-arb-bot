"""Materialize a corrected copy of a raw bulk pull.

The corrections themselves live in `src.gemini_execution.tape_normalize` and
this script only calls them, so there is exactly one implementation to get
wrong. It previously carried its own copy of both fixes; that copy is gone.

    python3 analysis/gemini/p8_make_clean.py <raw.json> <clean.json>
"""
import json
import sys

import p8_dataset as DS

if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    rows, rep = DS.load([src])
    for r in rows:
        r.pop("prints_raw", None)          # the raw pull on disk is the record
    json.dump(rows, open(dst, "w"))
    print(f"{rep['contracts_in']} -> {rep['contracts_out']} contracts "
          f"({rep['quarantined']} quarantined); prints {rep['prints_in']} -> "
          f"{rep['prints_out']} ({rep['twins_dropped']} mirror echoes dropped)")
