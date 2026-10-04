"""Reduce one raw recorder part to the BTC 15m rows the OOS replay needs.

WHAT THE RAW TAPE ACTUALLY HOLDS. `OOS_RETRIEVAL_STATUS.md` recorded that the
volume carries "the same `kind: book` / `kind: trade` rows the development tape
is made of". It does NOT. `pmus_ws_evidence_recorder.append` is called with
`{"kind": "ws", "t": <iso>, "msg": <frame>}` (recorder line 2194), plus a few
`recorder_start` / `priority_plan` / `error` rows. The `book` and `trade` rows
are DERIVED in the recorder's own process by `apply_events`, and the development
tape was built from the recorder's PRINTED log lines
(`scripts/pmus_railway_log_to_tape.py`), not from the volume. A first harvest
written against the documented claim kept 0 rows out of 46 parts, which is how
this was found.

So this step keeps the raw `ws` frames VERBATIM and the derivation happens
afterwards, in `pmus_oos_derive_rows.py`, through the recorder's own folding
code. Keeping the frames rather than derived rows also means the derivation can
be re-run and audited without reading the volume again.

SLUG MATCHING IS STRUCTURAL, NEVER A SUBSTRING. A raw line contains the literal
key name `"marketSlug"`, so a substring test matches unrelated markets and any
slug that is a substring of a field name. `l5_slice_window.row_slug` already
states this rule and implements it, so it is imported rather than restated.

No row is rewritten, reordered or deduplicated. This is a filter.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "slicer", os.path.join(_HERE, "l5_slice_window.py"))
SLICER = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SLICER)

#: the market family the frozen study is about
SERIES = "btc-updown-15m"
#: recorder bookkeeping rows: the evidence that collection was continuous
PROVENANCE_KINDS = ("recorder_start", "priority_plan", "error",
                    "provenance", "session")


def reduce_part(src: str, dst: str) -> dict:
    kept = prov = total = bad = 0
    slugs = set()
    with open(src) as f, open(dst, "w") as o:
        for line in f:
            total += 1
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                bad += 1
                continue
            kind = r.get("kind")
            if kind in PROVENANCE_KINDS:
                o.write(line if line.endswith("\n") else line + "\n")
                prov += 1
                continue
            if kind != "ws":
                continue
            slug = SLICER.row_slug(r)
            if not slug or SERIES not in slug:
                continue
            o.write(line if line.endswith("\n") else line + "\n")
            slugs.add(slug)
            kept += 1
    return {"kept_ws": kept, "provenance": prov, "total": total,
            "unparsable": bad, "slugs": len(slugs)}


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: pmus_oos_reduce_part.py <raw part> <out>",
              file=sys.stderr)
        return 2
    print(json.dumps(reduce_part(sys.argv[1], sys.argv[2])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
