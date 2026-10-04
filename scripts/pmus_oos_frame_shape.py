"""Print what a raw recorder part ACTUALLY contains.

This exists because the retrieval status document asserted the volume holds
`kind: book` / `kind: trade` rows and it does not. An assertion about a data
format should be checkable from a log, so this prints the kind histogram and a
couple of whole frames rather than describing them.
"""
from __future__ import annotations

import collections
import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: pmus_oos_frame_shape.py <raw part> [n samples]",
              file=sys.stderr)
        return 2
    path = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    kinds: collections.Counter = collections.Counter()
    samples = []
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                kinds["<unparsable>"] += 1
                continue
            kinds[r.get("kind")] += 1
            if r.get("kind") == "ws" and len(samples) < n:
                samples.append(line.rstrip("\n")[:1500])
    print("kinds: %s" % json.dumps(dict(kinds)))
    for s in samples:
        print("SAMPLE %s" % s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
