"""Chunk a contemporaneous volume listing into a GitHub Actions matrix.

WHY THE LISTING IS RE-READ AT ARCHIVE TIME. The 12 GiB retention sweeps while we
work -- it destroyed 2026-10-01 00:00-06:00 between two listings minutes apart.
A matrix hardcoded from an earlier listing would therefore ask for parts that no
longer exist and, worse, would silently MISS parts the recorder has written
since. So the archive run lists the directory itself and builds its own plan from
what is there at that moment.

The expected set is emitted alongside the matrix so a later gate can prove every
listed part was archived, rather than trusting that it was.
"""
from __future__ import annotations

import json
import os
import re
import sys

#: hourly raw parts only; anything else in the directory is not this tape
PART = re.compile(r"^pmus_ws_\d{8}_\d{2}\.jsonl$")


def main() -> int:
    per_chunk = int(os.environ.get("PER_CHUNK", "8"))
    names = set()
    for line in sys.stdin:
        # `railway files list` prints in columns, so split on whitespace rather
        # than treating a line as one name -- reading it line-wise is what made
        # an earlier count of 46 look like 46 files when there were 91.
        for tok in line.split():
            if PART.match(tok):
                names.add(tok)
    parts = sorted(names)
    if not parts:
        print("::error::no pmus_ws_* parts found in the listing", file=sys.stderr)
        return 1

    chunks = []
    for i in range(0, len(parts), per_chunk):
        group = parts[i:i + per_chunk]
        chunks.append({"id": "%02d" % (i // per_chunk),
                       "files": ",".join(group)})

    out = {"include": chunks}
    with open("ARCHIVE_EXPECTED.txt", "w", encoding="utf-8") as f:
        for p in parts:
            f.write(p + "\n")
    print("parts=%d chunks=%d" % (len(parts), len(chunks)), file=sys.stderr)
    print("oldest=%s newest=%s" % (parts[0], parts[-1]), file=sys.stderr)
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
