"""Cut ONE 15-minute window out of a raw WS tape, verbatim.

Input may be plain or gzipped JSONL of raw recorder rows (`{kind, t, msg}`).
Output is gzipped JSONL containing exactly the rows whose `t` falls inside
[window_start, window_start + 900s), unmodified. Nothing is rewritten,
de-duplicated or reordered: the settled scoreboard's completeness gate is what
judges the result, and it can only do that honestly if this step is a copy.

    python3 scripts/l5_slice_window.py <tape[.gz]> <slug> <start_iso> <out.gz>
"""
from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import sys

WINDOW_S = 900.0


def _open(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return io.open(path, "rt", encoding="utf-8", errors="replace")


def _ep(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return dt.datetime.fromisoformat(
            str(v).replace("Z", "+00:00")).timestamp()
    except Exception:                                        # noqa: BLE001
        return None


def row_slug(row: dict):
    """The market a raw row belongs to, read STRUCTURALLY.

    Never by substring: a raw line contains the literal key name
    `"marketSlug"`, so a substring test matches unrelated markets (and any
    slug that happens to be a substring of a field name). Structure only.
    """
    msg = row.get("msg")
    if not isinstance(msg, dict):
        return None
    for key in ("marketData", "trade"):
        d = msg.get(key)
        if isinstance(d, dict) and d.get("marketSlug"):
            return d["marketSlug"]
    return None


def main(argv) -> int:
    if len(argv) != 5:
        print(__doc__)
        return 2
    tape, slug, start_iso, out = argv[1:]
    start = _ep(start_iso)
    if start is None:
        print(f"unparsable window start: {start_iso!r}")
        return 2
    end = start + WINDOW_S
    kept = seen = bad = 0
    with _open(tape) as fh, gzip.open(out, "wt", encoding="utf-8") as w:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            seen += 1
            try:
                row = json.loads(line)
            except Exception:                                # noqa: BLE001
                bad += 1               # a truncated tail line, counted not hidden
                continue
            t = _ep(row.get("t"))
            if t is None or not (start <= t < end):
                continue
            if row_slug(row) != slug:
                continue
            w.write(line + "\n")
            kept += 1
    span = f"{start_iso} +{WINDOW_S / 60:.0f}min"
    print(f"SLICED {kept} of {seen} rows ({bad} unparsable) for {slug} {span} "
          f"-> {out}")
    if kept == 0:
        print("REFUSED: the tape contains no rows for that window")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
