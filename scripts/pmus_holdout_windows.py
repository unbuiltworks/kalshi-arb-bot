"""List the windows present in a state table, with their row counts.

Provenance aid only: it computes no economics and makes no selection. It exists
so the holdout run's log shows exactly which windows the harvested parts
reconstructed, rather than asserting a count.
"""
from __future__ import annotations

import collections
import json
import sys


def main() -> int:
    counts: collections.Counter = collections.Counter()
    with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                   # noqa: BLE001
                continue
            if r.get("slug"):
                counts[r["slug"]] += 1
    print("windows: %d" % len(counts))
    for slug in sorted(counts):
        print("  %s %d" % (slug, counts[slug]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
